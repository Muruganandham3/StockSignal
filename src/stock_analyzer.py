"""
stock_analyzer.py
Sends fetched news + price data to Google Gemini API.
"""

import json
import logging
import os
import random
import time
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

INPUT_FILE = Path("data/news_raw.json")
OUTPUT_FILE = Path("data/analysis.json")

# ── Gemini model config ───────────────────────────────────────────────────────

# Primary model
GEMINI_MODEL = "gemini-3.6-flash"

# Fallback models.
# If the primary model is temporarily unavailable, these will be tried.
GEMINI_FALLBACK_MODELS = [
    "gemini-3.5-flash",
    "gemini-2.5-flash",
]

GEMINI_BASE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models"
)

# Number of retries for each model
MAX_RETRIES = 5

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
        "debt_concern": true,
        "commentary": "2-3 sentence analysis"
      },
      "price_impact": {
        "short_term_pct_low": 0,
        "short_term_pct_high": 0,
        "medium_term_pct_low": 0,
        "medium_term_pct_high": 0,
        "rationale": "1-2 sentences"
      },
      "signal": "BUY | HOLD | WATCH | AVOID",
      "conviction": 1,
      "key_risks": ["string"],
      "key_catalysts": ["string"],
      "action_items": ["string"]
    }
  ],
  "market_summary": "2-3 sentence overall market tone for the day",
  "top_pick": "symbol or null",
  "top_pick_reason": "1 sentence"
}

Return ONLY valid JSON.
"""


def build_prompt(news_data: dict) -> str:
    news_items = news_data.get("news", [])
    prices = news_data.get("prices", {})
    generated_at = news_data.get("generated_at", "")
    mode = news_data.get("mode", "")

    lines = [
        SYSTEM_PROMPT,
        "",
        f"Date/time: {generated_at}  |  Mode: {mode}",
        "",
        "=== NEWS ITEMS ===",
    ]

    for item in news_items[:30]:
        symbols = item.get("symbols", [])

        if not isinstance(symbols, list):
            symbols = [str(symbols)]

        lines.append(
            f"\n[{item.get('id', 'N/A')}] "
            f"({str(item.get('type', '')).upper()}) "
            f"{item.get('title', '')}\n"
            f"Source: {item.get('source', '')}\n"
            f"Symbols: {', '.join(symbols) or 'unspecified'}\n"
            f"Summary: {item.get('summary', '')}"
        )

    lines.append("\n\n=== CURRENT PRICES ===")

    for sym, p in prices.items():
        chg = (
            f"{p['change_pct']:+.2f}%"
            if p.get("change_pct") is not None
            else "N/A"
        )

        lines.append(
            f"{sym}: ₹{p.get('price', 'N/A')} ({chg}) | "
            f"52W H: ₹{p.get('52w_high', 'N/A')} | "
            f"L: ₹{p.get('52w_low', 'N/A')}"
        )

    lines.append(
        "\n\nAnalyze every news item above. "
        "Prioritize items with clear fundamental or price impact. "
        "Return ONLY valid JSON."
    )

    return "\n".join(lines)


def get_gemini_url(model: str, api_key: str) -> str:
    return f"{GEMINI_BASE_URL}/{model}:generateContent?key={api_key}"


def calculate_backoff(attempt: int) -> float:
    """
    Exponential backoff with random jitter.

    attempt 1 -> roughly 5 seconds
    attempt 2 -> roughly 10 seconds
    attempt 3 -> roughly 20 seconds
    attempt 4 -> roughly 40 seconds
    attempt 5 -> roughly 60+ seconds
    """

    base = min(60, 5 * (2 ** (attempt - 1)))

    # Add 0-3 seconds random jitter
    jitter = random.uniform(0, 3)

    return base + jitter


def call_gemini_model(
    prompt: str,
    model: str,
    api_key: str,
    retries: int = MAX_RETRIES,
) -> str:

    url = get_gemini_url(model, api_key)

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": prompt
                    }
                ]
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
            log.info(
                "Calling Gemini model %s (attempt %d/%d)...",
                model,
                attempt,
                retries
            )

            response = requests.post(
                url,
                json=payload,
                timeout=120
            )

            status = response.status_code

            # ── Success ──────────────────────────────────────────────────────
            if status == 200:

                data = response.json()

                candidates = data.get("candidates", [])

                if not candidates:
                    raise ValueError(
                        "Gemini returned no candidates: "
                        + json.dumps(data)[:1000]
                    )

                candidate = candidates[0]

                finish_reason = candidate.get("finishReason")

                if finish_reason not in ("STOP", None):
                    log.warning(
                        "Gemini generation finished with reason: %s",
                        finish_reason
                    )

                parts = candidate.get(
                    "content",
                    {}
                ).get(
                    "parts",
                    []
                )

                if parts and "text" in parts[0]:

                    text = parts[0]["text"].strip()

                    if not text:
                        raise ValueError(
                            "Gemini returned an empty response."
                        )

                    log.info(
                        "Gemini response received successfully using %s",
                        model
                    )

                    return text

                raise ValueError(
                    "Gemini response contained no text: "
                    + json.dumps(data)[:1000]
                )

            # ── Temporary errors ────────────────────────────────────────────
            if status in (429, 500, 502, 503, 504):

                try:
                    error_data = response.json()
                    error_message = (
                        error_data
                        .get("error", {})
                        .get("message", response.text)
                    )
                except Exception:
                    error_message = response.text

                log.warning(
                    "Gemini temporary error %d on %s: %s",
                    status,
                    model,
                    error_message
                )

                if attempt < retries:

                    wait = calculate_backoff(attempt)

                    log.warning(
                        "Retrying %s in %.1f seconds...",
                        model,
                        wait
                    )

                    time.sleep(wait)
                    continue

                # Retries exhausted for this model
                raise RuntimeError(
                    f"Gemini model {model} unavailable after "
                    f"{retries} attempts. HTTP {status}: "
                    f"{error_message}"
                )

            # ── Permanent HTTP error ────────────────────────────────────────
            try:
                error_data = response.json()
                error_message = (
                    error_data
                    .get("error", {})
                    .get("message", response.text)
                )
            except Exception:
                error_message = response.text

            raise RuntimeError(
                f"Gemini API error HTTP {status}: {error_message}"
            )

        except requests.exceptions.Timeout:

            log.warning(
                "Gemini request timed out for %s "
                "(attempt %d/%d)",
                model,
                attempt,
                retries
            )

            if attempt < retries:

                wait = calculate_backoff(attempt)

                log.warning(
                    "Retrying after %.1f seconds...",
                    wait
                )

                time.sleep(wait)
                continue

            raise

        except requests.exceptions.ConnectionError as e:

            log.warning(
                "Gemini connection error for %s: %s",
                model,
                e
            )

            if attempt < retries:

                wait = calculate_backoff(attempt)

                time.sleep(wait)
                continue

            raise

    raise RuntimeError(
        f"All retries exhausted for Gemini model {model}."
    )


def call_gemini(prompt: str) -> str:

    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY environment variable is missing."
        )

    # Try primary model first
    models_to_try = [
        GEMINI_MODEL,
        *GEMINI_FALLBACK_MODELS
    ]

    # Remove duplicates while preserving order
    models_to_try = list(dict.fromkeys(models_to_try))

    last_error = None

    for model in models_to_try:

        log.info(
            "Trying Gemini model: %s",
            model
        )

        try:

            result = call_gemini_model(
                prompt=prompt,
                model=model,
                api_key=api_key,
                retries=MAX_RETRIES,
            )

            # Store the successful model so main() can use it
            global ACTIVE_GEMINI_MODEL
            ACTIVE_GEMINI_MODEL = model

            return result

        except Exception as e:

            last_error = e

            log.error(
                "Gemini model %s failed: %s",
                model,
                e
            )

            # Try next model
            log.info(
                "Trying next Gemini fallback model..."
            )

    raise RuntimeError(
        "All Gemini models failed. "
        f"Last error: {last_error}"
    )


def run_analysis(news_data: dict) -> dict:

    prompt = build_prompt(news_data)

    log.info(
        "Sending %d chars to Gemini...",
        len(prompt)
    )

    raw = call_gemini(prompt)

    # Sometimes an API/model can wrap JSON in markdown.
    # Remove common markdown wrappers safely.
    raw = raw.strip()

    if raw.startswith("```json"):
        raw = raw[7:]

    elif raw.startswith("```"):
        raw = raw[3:]

    if raw.endswith("```"):
        raw = raw[:-3]

    raw = raw.strip()

    try:

        result = json.loads(raw)

    except json.JSONDecodeError as e:

        log.error(
            "Gemini returned invalid JSON: %s",
            e
        )

        log.error(
            "Raw response: %s",
            raw[:3000]
        )

        raise

    return result


def main():

    if not INPUT_FILE.exists():

        log.error(
            "Input file not found at %s. "
            "Run news fetcher first.",
            INPUT_FILE
        )

        return

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    news_data = json.loads(
        INPUT_FILE.read_text(
            encoding="utf-8"
        )
    )

    log.info(
        "Analyzing %d news items...",
        len(news_data.get("news", []))
    )

    result = run_analysis(news_data)

    result["prices"] = news_data.get(
        "prices",
        {}
    )

    result["generated_at"] = news_data.get(
        "generated_at"
    )

    result["ai_model"] = globals().get(
        "ACTIVE_GEMINI_MODEL",
        GEMINI_MODEL
    )

    OUTPUT_FILE.write_text(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    log.info(
        "Analysis saved to %s — %d items analyzed. "
        "Top pick: %s | Model: %s",
        OUTPUT_FILE,
        len(result.get("analyses", [])),
        result.get("top_pick", "None"),
        result["ai_model"]
    )


if __name__ == "__main__":
    main()
