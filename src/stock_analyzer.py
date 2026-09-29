"""
stock_analyzer.py — Gemini API
Model: gemini-2.5-flash

Used for Indian stock-market news analysis.
"""

import json
import logging
import os
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

INPUT_FILE = Path("data/news_raw.json")
OUTPUT_FILE = Path("data/analysis.json")

# ============================================================
# GEMINI MODEL
# ============================================================

GEMINI_MODEL = "gemini-2.5-flash"

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

# Keep your existing batch size
BATCH_SIZE = 10

# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """You are a senior equity research analyst for Indian stock markets (NSE/BSE).

Analyze the news items and return ONLY a valid JSON object.

No markdown.
No explanation.
No ```json fences.
Start with { and end with }.

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
}

Important:
- Return exactly one analysis object for each news item.
- Use the supplied news_id.
- Do not invent news.
- If information is unavailable, use unknown.
- Keep all text concise.
"""


# ============================================================
# BUILD PROMPT
# ============================================================

def build_prompt(
    news_items: list,
    prices: dict,
    generated_at: str,
    mode: str,
) -> str:

    lines = [
        SYSTEM_PROMPT,
        "",
        f"Date: {generated_at} | Mode: {mode}",
        "",
        "=== NEWS ITEMS ===",
    ]

    for item in news_items:

        lines.append(
            f"\n[{item['id']}] "
            f"({item['type'].upper()}) "
            f"{item['title']}\n"
            f"Symbols: "
            f"{', '.join(item.get('symbols', [])) or 'N/A'}\n"
            f"Summary: "
            f"{item['summary'][:200]}"
        )

    if prices:

        lines.append("\n=== PRICES ===")

        for sym, p in list(prices.items())[:20]:

            chg = (
                f"{p['change_pct']:+.2f}%"
                if p.get("change_pct") is not None
                else "N/A"
            )

            lines.append(
                f"{sym}: ₹{p.get('price', 'N/A')} ({chg})"
            )

    lines.append(
        "\nReturn ONLY the JSON object."
    )

    return "\n".join(lines)


# ============================================================
# CALL GEMINI
# ============================================================

def call_gemini(
    prompt: str,
    retries: int = 3,
) -> str:

    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:

        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set."
        )

    url = f"{GEMINI_URL}?key={api_key}"

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
        },
    }

    for attempt in range(1, retries + 1):

        try:

            log.info(
                "Calling Gemini model: %s (attempt %d/%d)",
                GEMINI_MODEL,
                attempt,
                retries,
            )

            resp = requests.post(
                url,
                json=payload,
                timeout=120,
            )

            # ====================================================
            # 503 - SERVICE OVERLOADED
            # ====================================================

            if resp.status_code == 503:

                if attempt < retries:

                    wait = 20 * attempt

                    log.warning(
                        "503 overloaded — "
                        "waiting %ds before retry %d/%d",
                        wait,
                        attempt + 1,
                        retries,
                    )

                    time.sleep(wait)

                    continue

                log.error(
                    "Gemini returned 503 after %d attempts.",
                    retries,
                )

                raise RuntimeError(
                    f"Gemini 503 overloaded after "
                    f"{retries} retries."
                )

            # ====================================================
            # 429 - RATE LIMIT
            # ====================================================

            if resp.status_code == 429:

                if attempt < retries:

                    wait = 30 * attempt

                    log.warning(
                        "429 rate limited — "
                        "waiting %ds before retry %d/%d",
                        wait,
                        attempt + 1,
                        retries,
                    )

                    time.sleep(wait)

                    continue

                raise RuntimeError(
                    f"Gemini 429 rate limit after "
                    f"{retries} retries."
                )

            # ====================================================
            # OTHER HTTP ERRORS
            # ====================================================

            if not resp.ok:

                log.error(
                    "Gemini API error: HTTP %d\n%s",
                    resp.status_code,
                    resp.text,
                )

                resp.raise_for_status()

            # ====================================================
            # PARSE RESPONSE
            # ====================================================

            data = resp.json()

            candidates = data.get(
                "candidates",
                [],
            )

            if not candidates:

                raise ValueError(
                    "No candidates in Gemini response."
                )

            candidate = candidates[0]

            finish_reason = candidate.get(
                "finishReason",
                "",
            )

            if finish_reason == "MAX_TOKENS":

                raise ValueError(
                    "MAX_TOKENS"
                )

            parts = (
                candidate
                .get("content", {})
                .get("parts", [])
            )

            if not parts:

                raise ValueError(
                    "Empty parts in Gemini response."
                )

            text = parts[0].get("text")

            if not text:

                raise ValueError(
                    "Empty text in Gemini response."
                )

            return text.strip()

        # ========================================================
        # REQUEST ERROR
        # ========================================================

        except requests.exceptions.RequestException as e:

            log.warning(
                "Request error attempt %d/%d: %s",
                attempt,
                retries,
                e,
            )

            if attempt == retries:

                raise RuntimeError(
                    f"Gemini request failed after "
                    f"{retries} attempts: {e}"
                ) from e

            wait = 10 * attempt

            time.sleep(wait)

        except ValueError:

            # Do not retry JSON / MAX_TOKENS errors here.
            raise

    raise RuntimeError(
        f"All {retries} retries exhausted for "
        f"{GEMINI_MODEL}"
    )


# ============================================================
# CLEAN JSON
# ============================================================

def clean_json(raw: str) -> str:

    raw = raw.strip()

    if raw.startswith("```"):

        newline_index = raw.find("\n")

        if newline_index != -1:

            raw = raw[
                newline_index + 1:
            ]

    if raw.endswith("```"):

        raw = raw[
            :raw.rfind("```")
        ]

    return raw.strip()


# ============================================================
# ANALYZE BATCH
# ============================================================

def analyze_batch(
    items: list,
    prices: dict,
    generated_at: str,
    mode: str,
):

    prompt = build_prompt(
        items,
        prices,
        generated_at,
        mode,
    )

    log.info(
        "Sending %d characters for %d news items",
        len(prompt),
        len(items),
    )

    raw = call_gemini(prompt)

    result = json.loads(
        clean_json(raw)
    )

    return (
        result.get("analyses", []),
        result,
    )


# ============================================================
# RUN ANALYSIS
# ============================================================

def run_analysis(
    news_data: dict,
) -> dict:

    news_items = news_data.get(
        "news",
        [],
    )

    prices = news_data.get(
        "prices",
        {},
    )

    generated_at = news_data.get(
        "generated_at",
        "",
    )

    mode = news_data.get(
        "mode",
        "",
    )

    all_analyses = []

    last_result = {}

    # =========================================================
    # PROCESS BATCHES
    # =========================================================

    for i in range(
        0,
        len(news_items),
        BATCH_SIZE,
    ):

        batch = news_items[
            i:i + BATCH_SIZE
        ]

        log.info(
            "Batch %d-%d of %d...",
            i + 1,
            i + len(batch),
            len(news_items),
        )

        try:

            analyses, result = analyze_batch(
                batch,
                prices,
                generated_at,
                mode,
            )

            all_analyses.extend(
                analyses
            )

            last_result = result

        # =====================================================
        # MAX TOKENS
        # =====================================================

        except ValueError as e:

            if "MAX_TOKENS" in str(e):

                log.warning(
                    "MAX_TOKENS — "
                    "retrying in sub-batches of 5..."
                )

                for j in range(
                    0,
                    len(batch),
                    5,
                ):

                    sub = batch[
                        j:j + 5
                    ]

                    try:

                        analyses, result = analyze_batch(
                            sub,
                            prices,
                            generated_at,
                            mode,
                        )

                        all_analyses.extend(
                            analyses
                        )

                        last_result = result

                    except Exception as sub_e:

                        log.error(
                            "Sub-batch failed, "
                            "skipping: %s",
                            sub_e,
                        )

            else:

                raise

        # =====================================================
        # INVALID JSON
        # =====================================================

        except json.JSONDecodeError as e:

            log.error(
                "JSON parse error batch %d — "
                "skipping: %s",
                i,
                e,
            )

            continue

        # =====================================================
        # GEMINI / NETWORK ERROR
        # =====================================================

        except RuntimeError as e:

            log.error(
                "Gemini batch failed: %s",
                e,
            )

            # Continue with the next batch instead of
            # terminating the complete GitHub Action.
            continue

    # =========================================================
    # FINAL RESULT
    # =========================================================

    return {
        "analyses": all_analyses,

        "market_summary": last_result.get(
            "market_summary",
            "Market data processed.",
        ),

        "top_pick": last_result.get(
            "top_pick"
        ),

        "top_pick_reason": last_result.get(
            "top_pick_reason",
            "",
        ),

        "prices": prices,

        "generated_at": generated_at,

        "ai_model": GEMINI_MODEL,
    }


# ============================================================
# MAIN
# ============================================================

def main():

    if not INPUT_FILE.exists():

        log.error(
            "No input at %s — "
            "run news_fetcher.py first.",
            INPUT_FILE,
        )

        return

    log.info(
        "Reading news from %s",
        INPUT_FILE,
    )

    news_data = json.loads(
        INPUT_FILE.read_text(
            encoding="utf-8"
        )
    )

    log.info(
        "Analyzing %d news items...",
        news_data.get(
            "news_count",
            0,
        ),
    )

    result = run_analysis(
        news_data
    )

    OUTPUT_FILE.write_text(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    log.info(
        "Done — %d items analyzed. "
        "Top pick: %s",
        len(
            result.get(
                "analyses",
                [],
            )
        ),
        result.get(
            "top_pick",
            "none",
        ),
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s "
            "[%(levelname)s] "
            "%(message)s"
        ),
    )

    main()
