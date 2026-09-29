"""
stock_analyzer.py — Gemini free tier, auto model discovery + 503 fallback
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

PREFERRED_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
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


def get_available_models(api_key: str) -> list:
    url  = f"{GEMINI_BASE}/models?key={api_key}"
    resp = requests.get(url, timeout=15)
    if resp.status_code == 403:
        log.error(
            "403 Forbidden — API key has no Gemini access.\n"
            "Get a free key from: https://aistudio.google.com/app/apikey\n"
            "Then update GitHub Secret: GEMINI_API_KEY"
        )
        raise SystemExit(1)
    resp.raise_for_status()
    return [m["name"].replace("models/", "") for m in resp.json().get("models", [])]


def pick_model(available: list) -> list:
    """Return ordered list of models to try, best first."""
    chosen = []
    for p in PREFERRED_MODELS:
        if p in available:
            chosen.append(p)
    # Add any remaining available flash models not in our list
    for m in available:
        if "flash" in m and m not in chosen:
            chosen.append(m)
    if not chosen:
        chosen = [m for m in available if "gemini" in m]
    log.info("Models to try (in order): %s", chosen[:5])
    return chosen


def build_prompt(news_items: list, prices: dict, generated_at: str, mode: str) -> str:
    lines = [SYSTEM_PROMPT, "", f"Date: {generated_at} | Mode: {mode}", "", "=== NEWS ITEMS ==="]
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


def call_gemini(prompt: str, model: str, api_key: str) -> str:
    """Call one model. Raises on 400/404. Returns text on success. Raises on 503 after retries."""
    url = f"{GEMINI_BASE}/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature":      0.1,
            "maxOutputTokens":  8192,
            "responseMimeType": "application/json",
        },
    }

    for attempt in range(1, 4):
        try:
            resp = requests.post(url, json=payload, timeout=120)

            # 503 = overloaded, retry with backoff
            if resp.status_code == 503:
                wait = 15 * attempt
                log.warning("503 Service Unavailable for %s — waiting %ds (attempt %d/3)", model, wait, attempt)
                time.sleep(wait)
                continue

            # 429 = rate limited, retry with backoff
            if resp.status_code == 429:
                wait = 20 * attempt
                log.warning("429 Rate limited for %s — waiting %ds (attempt %d/3)", model, wait, attempt)
                time.sleep(wait)
                continue

            # 400 / 404 = model doesn't support this request → caller should try next model
            if resp.status_code in (400, 404):
                log.warning("HTTP %d for model %s — will try next model", resp.status_code, model)
                raise ValueError(f"MODEL_UNSUPPORTED:{resp.status_code}")

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

        except ValueError:
            raise   # propagate to caller
        except requests.exceptions.RequestException as e:
            log.warning("Request error attempt %d/3 for %s: %s", attempt, model, e)
            if attempt == 3:
                raise
            time.sleep(10)

    raise RuntimeError(f"All retries exhausted for model {model}")


def clean_json(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw[raw.index("\n") + 1:]
    if raw.endswith("```"):
        raw = raw[:raw.rfind("```")]
    return raw.strip()


def call_with_fallback(prompt: str, models: list, api_key: str) -> str:
    """Try each model in order until one works."""
    last_err = None
    for model in models:
        try:
            log.info("Trying model: %s", model)
            text = call_gemini(prompt, model, api_key)
            log.info("Success with model: %s", model)
            return text, model
        except ValueError as e:
            if "MODEL_UNSUPPORTED" in str(e):
                log.warning("Model %s unsupported, trying next...", model)
                last_err = e
                continue
            raise   # MAX_TOKENS or other ValueError
        except Exception as e:
            log.warning("Model %s failed (%s), trying next...", model, e)
            last_err = e
            continue

    raise RuntimeError(f"All models failed. Last error: {last_err}")


def analyze_batch(items: list, prices: dict, generated_at: str, mode: str, models: list, api_key: str):
    prompt = build_prompt(items, prices, generated_at, mode)
    log.info("Sending %d chars for %d items...", len(prompt), len(items))
    raw, used_model = call_with_fallback(prompt, models, api_key)
    result = json.loads(clean_json(raw))
    return result.get("analyses", []), result, used_model


def run_analysis(news_data: dict) -> dict:
    api_key      = os.environ["GEMINI_API_KEY"]
    available    = get_available_models(api_key)
    log.info("Available models: %s", available)
    models       = pick_model(available)

    if not models:
        raise RuntimeError("No usable Gemini models found for this API key")

    news_items   = news_data.get("news", [])
    prices       = news_data.get("prices", {})
    generated_at = news_data.get("generated_at", "")
    mode         = news_data.get("mode", "")

    all_analyses = []
    last_result  = {}
    used_model   = models[0]

    for i in range(0, len(news_items), BATCH_SIZE):
        batch = news_items[i: i + BATCH_SIZE]
        log.info("Batch %d–%d of %d...", i + 1, i + len(batch), len(news_items))
        try:
            analyses, result, used_model = analyze_batch(batch, prices, generated_at, mode, models, api_key)
            all_analyses.extend(analyses)
            last_result = result

        except ValueError as e:
            if "MAX_TOKENS" in str(e):
                log.warning("MAX_TOKENS — retrying in sub-batches of 5...")
                for j in range(0, len(batch), 5):
                    sub = batch[j: j + 5]
                    try:
                        analyses, result, used_model = analyze_batch(sub, prices, generated_at, mode, models, api_key)
                        all_analyses.extend(analyses)
                        last_result = result
                    except Exception as sub_e:
                        log.error("Sub-batch failed, skipping: %s", sub_e)
            else:
                raise

        except json.JSONDecodeError as e:
            log.error("JSON parse error for batch %d — skipping: %s", i, e)
            continue

    return {
        "analyses":        all_analyses,
        "market_summary":  last_result.get("market_summary", "Market data processed."),
        "top_pick":        last_result.get("top_pick"),
        "top_pick_reason": last_result.get("top_pick_reason", ""),
        "prices":          prices,
        "generated_at":    generated_at,
        "ai_model":        used_model,
    }


def main():
    if not INPUT_FILE.exists():
        log.error("No input at %s. Run news_fetcher.py first.", INPUT_FILE)
        return
    news_data = json.loads(INPUT_FILE.read_text())
    log.info("Analyzing %d news items...", news_data.get("news_count", 0))
    result = run_analysis(news_data)
    OUTPUT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    log.info("Done — %d items. Model used: %s. Top pick: %s",
             len(result.get("analyses", [])), result.get("ai_model"), result.get("top_pick", "none"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
