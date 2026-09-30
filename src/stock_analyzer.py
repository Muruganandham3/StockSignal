"""
stock_analyzer.py
Sends fetched news + price data to Google Gemini API.
"""

import json
import logging
import os
import time
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

INPUT_FILE = Path("data/news_raw.json")
OUTPUT_FILE = Path("data/analysis.json")

# ── Model config ──────────────────────────────────────────────────────────────
GEMINI_MODEL = " gemini-3.5-flash-lite"
GEMINI_API_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)

# ── Prompt ────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a senior equity research analyst specializing in Indian stock markets (NSE/BSE).
You receive raw news items and price data, and return a structured JSON analysis.

For each news item, analyze:
1. Fundamentals impact: How does this affect PE, EPS, revenue, EBITDA, debt/equity?
2. Price impact: What % move is likely? Short-term (1-5 days) and medium-term (1-3 months)?
3. Signal: BUY / HOLD / WATCH / AVOID
4. Conviction: 1-10 score
5. Key risks
6. Key catalysts

IMPORTANT: Return ONLY valid JSON matching this schema:
{
  "analyses": [
    {
      "news_id": "string",
      "symbol": "string or null",
      "headline": "string (your 1-line summary)",
      "event_type": "quarterly_result | new_order | bulk_deal | corporate_action | ma_event | general",
      "fundamentals": {
        "eps_impact": "positive | negative | neutral | unknown",
        "revenue_direction": "up | down | flat | unknown",
        "margin_trend": "expanding | contracting | stable | unknown",
        "debt_concern": true or false,
        "commentary": "2-3 sentence analysis"
      },
      "price_impact": {
        "short_term_pct_low": number,
        "short_term_pct_high": number,
        "medium_term_pct_low": number,
        "medium_term_pct_high": number,
        "rationale": "1-2 sentences"
      },
      "signal": "BUY | HOLD | WATCH | AVOID",
      "conviction": number between 1 and 10,
      "key_risks": ["string"],
      "key_catalysts": ["string"],
      "action_items": ["string"]
    }
  ],
  "market_summary": "2-3 sentence overall market tone for the day",
  "top_pick": "symbol or null — your single highest conviction idea today",
  "top_pick_reason": "1 sentence"
}"""


def build_prompt(news_data: dict) -> str:
    news_items   = news_data.get("news", [])
    prices       = news_data.get("prices", {})
    generated_at = news_data.get("generated_at", "")
    mode         = news_data.get("mode", "")

    lines = [
        SYSTEM_PROMPT,
        "",
        f"Date/time: {generated_at}  |  Mode: {mode}",
        "",
        "=== NEWS ITEMS ===",
    ]

    for item in news_items[:30]:
        lines.append(
            f"\n[{item.get('id', 'N/A')}] ({str(item.get('type', '')).upper()}) {item.get('title', '')}\n"
            f"Source: {item.get('source', '')}\n"
            f"Symbols: {', '.join(item.get('symbols', [])) or 'unspecified'}\n"
            f"Summary: {item.get('summary', '')}"
        )

    lines.append("\n\n=== CURRENT PRICES ===")
    for sym, p in prices.items():
        chg = f"{p['change_pct']:+.2f}%" if p.get("change_pct") is not None else "N/A"
        lines.append(
            f"{sym}: ₹{p.get('price', 'N/A')} ({chg}) | "
            f"52W H: ₹{p.get('52w_high', 'N/A')} | L: ₹{p.get('52w_low', 'N/A')}"
        )

    lines.append(
        "\n\nAnalyze every news item above. "
        "Prioritize items with clear fundamental or price impact. "
        "Return ONLY valid JSON."
    )
    return "\n".join(lines)


def call_gemini(prompt: str, retries: int = 3) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is missing.")

    url = f"{GEMINI_API_URL}?key={api_key}"
    
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json"
        }
    }

    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=90)
            
            if resp.status_code != 200:
                log.error("Gemini API Error (%s): %s", resp.status_code, resp.text)
                resp.raise_for_status()

            data = resp.json()
            candidates = data.get("candidates", [])
            if candidates:
                candidate = candidates[0]
                # Check for output block / finish reason issues
                finish_reason = candidate.get("finishReason")
                if finish_reason not in ("STOP", None):
                    log.warning("Generation finished with reason: %s", finish_reason)

                parts = candidate.get("content", {}).get("parts", [])
                if parts and "text" in parts[0]:
                    return parts[0]["text"].strip()

            raise ValueError(f"No valid text candidate returned: {json.dumps(data)[:200]}")

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            if status in (429, 503):
                wait = 10 * attempt
                log.warning("Rate limit / Service unavailable (%d). Retrying in %ds...", status, wait)
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            log.warning("Gemini request failed (attempt %d/%d): %s", attempt, retries, e)
            if attempt == retries:
                raise
            time.sleep(5)

    raise RuntimeError("All Gemini API retries exhausted.")


def run_analysis(news_data: dict) -> dict:
    prompt = build_prompt(news_data)
    log.info("Sending %d chars to Gemini (%s)...", len(prompt), GEMINI_MODEL)

    raw = call_gemini(prompt)
    result = json.loads(raw)
    return result


def main():
    if not INPUT_FILE.exists():
        log.error("Input file not found at %s. Run news fetcher first.", INPUT_FILE)
        return

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    news_data = json.loads(INPUT_FILE.read_text(encoding="utf-8"))
    log.info("Analyzing %d news items...", len(news_data.get("news", [])))

    result = run_analysis(news_data)
    result["prices"] = news_data.get("prices", {})
    result["generated_at"] = news_data.get("generated_at")
    result["ai_model"] = GEMINI_MODEL

    OUTPUT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(
        "Analysis saved to %s — %d items analyzed. Top pick: %s",
        OUTPUT_FILE,
        len(result.get("analyses", [])),
        result.get("top_pick", "None"),
    )


if __name__ == "__main__":
    main()
