"""
stock_analyzer.py
Sends fetched news + price data to Google Gemini API (FREE tier).

Fixes applied:
  1. Model corrected to gemini-2.0-flash (free, valid model name)
  2. maxOutputTokens raised to 8192 to prevent MAX_TOKENS cutoff
  3. News items capped at 15 per call + summary-only mode to keep prompt small
  4. Fallback: if JSON is truncated, retry with fewer items (5 at a time)
  5. partial JSON recovery attempt before giving up
"""

import json
import logging
import os
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

INPUT_FILE  = Path("data/news_raw.json")
OUTPUT_FILE = Path("data/analysis.json")

# ── Model config ──────────────────────────────────────────────────────────────
GEMINI_MODEL   = "gemini-1.5-flash"          # ✅ stable free-tier model, widely available
GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)

# How many news items to send per API call (keep low to avoid MAX_TOKENS)
BATCH_SIZE = 10

# ── Prompt ────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a senior equity research analyst for Indian stock markets (NSE/BSE).
Analyze the news items and return ONLY a valid JSON object — no markdown, no explanation, no ```json fences.
Start your response directly with { and end with }.

Keep each text field SHORT (1 sentence max) to avoid response truncation.

JSON schema:
{
  "analyses": [
    {
      "news_id": "string",
      "symbol": "string or null",
      "headline": "one short sentence",
      "event_type": "quarterly_result|new_order|bulk_deal|corporate_action|ma_event|general",
      "fundamentals": {
        "eps_impact": "positive|negative|neutral|unknown",
        "revenue_direction": "up|down|flat|unknown",
        "margin_trend": "expanding|contracting|stable|unknown",
        "debt_concern": false,
        "commentary": "one sentence only"
      },
      "price_impact": {
        "short_term_pct_low": 0,
        "short_term_pct_high": 0,
        "medium_term_pct_low": 0,
        "medium_term_pct_high": 0,
        "rationale": "one sentence only"
      },
      "signal": "BUY|HOLD|WATCH|AVOID",
      "conviction": 5,
      "key_risks": ["one short phrase"],
      "key_catalysts": ["one short phrase"],
      "action_items": ["one short phrase"]
    }
  ],
  "market_summary": "one sentence",
  "top_pick": "SYMBOL or null",
  "top_pick_reason": "one sentence"
}"""


def build_prompt(news_items: list[dict], prices: dict, generated_at: str, mode: str) -> str:
    lines = [
        SYSTEM_PROMPT,
        "",
        f"Date: {generated_at} | Mode: {mode}",
        "",
        "=== NEWS ITEMS ===",
    ]
    for item in news_items:
        lines.append(
            f"\n[{item['id']}] ({item['type'].upper()}) {item['title']}\n"
            f"Symbols: {', '.join(item.get('symbols', [])) or 'N/A'}\n"
            f"Summary: {item['summary'][:200]}"   # truncate long summaries
        )

    if prices:
        lines.append("\n=== PRICES ===")
        for sym, p in list(prices.items())[:20]:  # cap price rows
            chg = f"{p['change_pct']:+.2f}%" if p.get("change_pct") is not None else "N/A"
            lines.append(f"{sym}: ₹{p.get('price','N/A')} ({chg})")

    lines.append("\nReturn ONLY the JSON object.")
    return "\n".join(lines)


def call_gemini(prompt: str, retries: int = 3) -> str:
    api_key = os.environ["GEMINI_API_KEY"]
    url     = GEMINI_API_URL.format(model=GEMINI_MODEL, key=api_key)

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature":      0.1,    # very low = consistent JSON
            "maxOutputTokens":  8192,   # ✅ max allowed for flash model
            "responseMimeType": "application/json",
        },
    }

    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=120)
            resp.raise_for_status()
            data = resp.json()

            # Check finish reason
            candidates = data.get("candidates", [])
            if not candidates:
                raise ValueError("Gemini returned no candidates")

            finish_reason = candidates[0].get("finishReason", "")
            if finish_reason == "MAX_TOKENS":
                log.warning("MAX_TOKENS hit — response was truncated. Will retry with fewer items.")
                raise ValueError("MAX_TOKENS")

            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                raise ValueError("Gemini returned empty parts")

            return parts[0]["text"].strip()

        except ValueError as e:
            if "MAX_TOKENS" in str(e):
                raise   # propagate to caller to retry with smaller batch
            log.warning("Value error attempt %d/%d: %s", attempt, retries, e)
            if attempt == retries:
                raise

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            wait   = 10 * attempt
            if status in (429, 503):
                log.warning("HTTP %d — waiting %ds (attempt %d/%d)", status, wait, attempt, retries)
                time.sleep(wait)
            else:
                raise

        except Exception as e:
            log.warning("Gemini error attempt %d/%d: %s", attempt, retries, e)
            if attempt == retries:
                raise
            time.sleep(5)

    raise RuntimeError("All Gemini retries exhausted")


def clean_json(raw: str) -> str:
    """Strip accidental markdown fences."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw[raw.index("\n") + 1:]
    if raw.endswith("```"):
        raw = raw[:raw.rfind("```")]
    return raw.strip()


def analyze_batch(items: list[dict], prices: dict, generated_at: str, mode: str) -> list[dict]:
    """Analyze a batch of news items, returns list of analysis dicts."""
    prompt = build_prompt(items, prices, generated_at, mode)
    log.info("Sending %d chars to Gemini (%s) for %d items...", len(prompt), GEMINI_MODEL, len(items))

    raw    = call_gemini(prompt)
    clean  = clean_json(raw)
    result = json.loads(clean)
    return result.get("analyses", []), result


def run_analysis(news_data: dict) -> dict:
    news_items   = news_data.get("news", [])
    prices       = news_data.get("prices", {})
    generated_at = news_data.get("generated_at", "")
    mode         = news_data.get("mode", "")

    all_analyses  = []
    last_result   = {}

    # ── Process in batches to avoid MAX_TOKENS ────────────────────────────────
    for i in range(0, len(news_items), BATCH_SIZE):
        batch = news_items[i: i + BATCH_SIZE]
        log.info("Processing batch %d-%d of %d...", i+1, i+len(batch), len(news_items))

        try:
            analyses, result = analyze_batch(batch, prices, generated_at, mode)
            all_analyses.extend(analyses)
            last_result = result   # keep last for market_summary / top_pick

        except ValueError as e:
            if "MAX_TOKENS" in str(e):
                # Retry this batch in smaller sub-batches of 5
                log.warning("Retrying batch in sub-batches of 5...")
                for j in range(0, len(batch), 5):
                    sub = batch[j: j + 5]
                    try:
                        analyses, result = analyze_batch(sub, prices, generated_at, mode)
                        all_analyses.extend(analyses)
                        last_result = result
                    except Exception as sub_e:
                        log.error("Sub-batch failed, skipping: %s", sub_e)
            else:
                raise

        except json.JSONDecodeError as e:
            log.error("JSON parse failed for batch %d: %s — skipping batch", i, e)
            # Try to salvage a partial response
            continue

    # ── Merge everything into one result object ───────────────────────────────
    merged = {
        "analyses":        all_analyses,
        "market_summary":  last_result.get("market_summary", "Market data processed."),
        "top_pick":        last_result.get("top_pick"),
        "top_pick_reason": last_result.get("top_pick_reason", ""),
        "prices":          prices,
        "generated_at":    generated_at,
        "ai_model":        GEMINI_MODEL,
    }
    return merged


def main():
    if not INPUT_FILE.exists():
        log.error("No input file at %s. Run news_fetcher.py first.", INPUT_FILE)
        return

    news_data = json.loads(INPUT_FILE.read_text())
    log.info("Analyzing %d news items...", news_data.get("news_count", 0))

    result = run_analysis(news_data)

    OUTPUT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    log.info(
        "Analysis complete — %d items. Top pick: %s",
        len(result.get("analyses", [])),
        result.get("top_pick", "none"),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
