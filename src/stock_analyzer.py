"""
stock_analyzer.py
Sends fetched news + price data to Google Gemini API (FREE tier).
Auto-discovers the correct model name from your API key at runtime.
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

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Preferred model order — first one that exists on your key wins
PREFERRED_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-2.0-flash-exp",
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash",
    "gemini-pro-latest",
    "gemini-pro",
]

BATCH_SIZE = 10

SYSTEM_PROMPT = """You are a senior equity research analyst for Indian stock markets (NSE/BSE).
Analyze the news items and return ONLY a valid JSON object.
No markdown, no explanation, no ```json fences. Start with { and end with }.
Keep every text field to ONE sentence to avoid truncation.

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
        "commentary": "one sentence"
      },
      "price_impact": {
        "short_term_pct_low": 0,
        "short_term_pct_high": 0,
        "medium_term_pct_low": 0,
        "medium_term_pct_high": 0,
        "rationale": "one sentence"
      },
      "signal": "BUY|HOLD|WATCH|AVOID",
      "conviction": 5,
      "key_risks": ["short phrase"],
      "key_catalysts": ["short phrase"],
      "action_items": ["short phrase"]
    }
  ],
  "market_summary": "one sentence",
  "top_pick": "SYMBOL or null",
  "top_pick_reason": "one sentence"
}"""


def discover_model(api_key: str) -> str:
    """
    Ask Gemini which models are available for this key and pick the best one.
    Falls back to gemini-pro if listing fails.
    """
    url = f"{GEMINI_BASE}/models?key={api_key}"
    try:
        resp = requests.get(url, timeout=15)

        if resp.status_code == 403:
            log.error(
                "403 Forbidden — your GEMINI_API_KEY does NOT have Gemini API access.\n"
                "This key is likely from Google Cloud Console (console.cloud.google.com).\n"
                "Get a FREE key from Google AI Studio instead:\n"
                "  👉 https://aistudio.google.com/app/apikey\n"
                "Then update your GitHub Secret GEMINI_API_KEY with the new key."
            )
            raise SystemExit(1)

        resp.raise_for_status()
        available = {
            m["name"].replace("models/", "")
            for m in resp.json().get("models", [])
        }
        log.info("Available Gemini models: %s", sorted(available))

        for preferred in PREFERRED_MODELS:
            if preferred in available:
                log.info("Selected model: %s", preferred)
                return preferred

        # Last resort: use whatever is available
        if available:
            chosen = sorted(available)[0]
            log.warning("None of preferred models found, using: %s", chosen)
            return chosen

    except SystemExit:
        raise
    except Exception as e:
        log.warning("Model discovery failed (%s), defaulting to gemini-1.5-flash", e)

    return "gemini-1.5-flash"


def build_prompt(news_items: list, prices: dict, generated_at: str, mode: str) -> str:
    lines = [
        SYSTEM_PROMPT, "",
        f"Date: {generated_at} | Mode: {mode}", "",
        "=== NEWS ITEMS ===",
    ]
    for item in news_items:
        lines.append(
            f"\n[{item['id']}] ({item['type'].upper()}) {item['title']}\n"
            f"Symbols: {', '.join(item.get('symbols', [])) or 'N/A'}\n"
            f"Summary: {item['summary'][:200]}"
        )
    if prices:
        lines.append("\n=== PRICES ===")
        for sym, p in list(prices.items())[:20]:
            chg = f"{p['change_pct']:+.2f}%" if p.get("change_pct") is not None else "N/A"
            lines.append(f"{sym}: ₹{p.get('price','N/A')} ({chg})")
    lines.append("\nReturn ONLY the JSON object.")
    return "\n".join(lines)


def call_gemini(prompt: str, model: str, api_key: str, retries: int = 3) -> str:
    url = f"{GEMINI_BASE}/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature":      0.1,
            "maxOutputTokens":  8192,
            "responseMimeType": "application/json",
        },
    }

    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=120)
            resp.raise_for_status()
            data = resp.json()

            candidates = data.get("candidates", [])
            if not candidates:
                raise ValueError("No candidates in response")

            finish_reason = candidates[0].get("finishReason", "")
            if finish_reason == "MAX_TOKENS":
                raise ValueError("MAX_TOKENS")

            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                raise ValueError("Empty parts in response")

            return parts[0]["text"].strip()

        except ValueError as e:
            if "MAX_TOKENS" in str(e):
                raise
            log.warning("Attempt %d/%d — %s", attempt, retries, e)
            if attempt == retries:
                raise

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            if status in (429, 503):
                wait = 10 * attempt
                log.warning("HTTP %d — retrying in %ds (%d/%d)", status, wait, attempt, retries)
                time.sleep(wait)
            else:
                log.error("HTTP %d error: %s", status, e.response.text if e.response else "")
                raise

        except Exception as e:
            log.warning("Attempt %d/%d — %s", attempt, retries, e)
            if attempt == retries:
                raise
            time.sleep(5)

    raise RuntimeError("All retries exhausted")


def clean_json(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw[raw.index("\n") + 1:]
    if raw.endswith("```"):
        raw = raw[:raw.rfind("```")]
    return raw.strip()


def analyze_batch(items: list, prices: dict, generated_at: str, mode: str, model: str, api_key: str):
    prompt = build_prompt(items, prices, generated_at, mode)
    log.info("Sending %d chars to Gemini (%s) for %d items...", len(prompt), model, len(items))
    raw    = call_gemini(prompt, model, api_key)
    result = json.loads(clean_json(raw))
    return result.get("analyses", []), result


def run_analysis(news_data: dict) -> dict:
    api_key      = os.environ["GEMINI_API_KEY"]
    model        = discover_model(api_key)          # ← auto-picks correct model

    news_items   = news_data.get("news", [])
    prices       = news_data.get("prices", {})
    generated_at = news_data.get("generated_at", "")
    mode         = news_data.get("mode", "")

    all_analyses = []
    last_result  = {}

    for i in range(0, len(news_items), BATCH_SIZE):
        batch = news_items[i: i + BATCH_SIZE]
        log.info("Batch %d–%d of %d...", i + 1, i + len(batch), len(news_items))
        try:
            analyses, result = analyze_batch(batch, prices, generated_at, mode, model, api_key)
            all_analyses.extend(analyses)
            last_result = result

        except ValueError as e:
            if "MAX_TOKENS" in str(e):
                log.warning("MAX_TOKENS — retrying in sub-batches of 5...")
                for j in range(0, len(batch), 5):
                    sub = batch[j: j + 5]
                    try:
                        analyses, result = analyze_batch(sub, prices, generated_at, mode, model, api_key)
                        all_analyses.extend(analyses)
                        last_result = result
                    except Exception as sub_e:
                        log.error("Sub-batch failed, skipping: %s", sub_e)
            else:
                raise

        except json.JSONDecodeError as e:
            log.error("JSON parse failed for batch %d — skipping: %s", i, e)
            continue

    return {
        "analyses":        all_analyses,
        "market_summary":  last_result.get("market_summary", "Market data processed."),
        "top_pick":        last_result.get("top_pick"),
        "top_pick_reason": last_result.get("top_pick_reason", ""),
        "prices":          prices,
        "generated_at":    generated_at,
        "ai_model":        model,
    }


def main():
    if not INPUT_FILE.exists():
        log.error("No input at %s. Run news_fetcher.py first.", INPUT_FILE)
        return

    news_data = json.loads(INPUT_FILE.read_text())
    log.info("Analyzing %d news items...", news_data.get("news_count", 0))

    result = run_analysis(news_data)
    OUTPUT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    log.info("Done — %d items. Top pick: %s",
             len(result.get("analyses", [])), result.get("top_pick", "none"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
