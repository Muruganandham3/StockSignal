"""
stock_analyzer.py — Gemini API
Model: gemini-3.8-flash

Indian stock-market news analysis.
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

GEMINI_MODEL = "gemini-3.8-flash"

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

BATCH_SIZE = 10

# Retry configuration
RETRIES = 5


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """You are a senior equity research analyst for Indian stock markets (NSE/BSE).

Analyze the news items and return ONLY a valid JSON object.

No markdown.
No explanation.
No ```json fences.
Start with { and end with }.

Keep every text field to ONE sentence.

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

Rules:
- Return exactly one analysis object for every news item.
- Use the supplied news_id.
- Do not invent information.
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
            f"{item.get('summary', '')[:200]}"
        )

    if prices:

        lines.append("\n=== PRICES ===")

        for sym, p in list(prices.items())[:20]:

            change = p.get("change_pct")

            if change is not None:
                chg = f"{change:+.2f}%"
            else:
                chg = "N/A"

            lines.append(
                f"{sym}: ₹{p.get('price', 'N/A')} ({chg})"
            )

    lines.append(
        "\nReturn ONLY the JSON object."
    )

    return "\n".join(lines)


# ============================================================
# GEMINI API CALL
# ============================================================

def call_gemini(
    prompt: str,
    retries: int = RETRIES,
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
            "responseMimeType": "application/json",
            "maxOutputTokens": 8192
        }
    }

    for attempt in range(1, retries + 1):

        try:

            log.info(
                "Calling Gemini model: %s (attempt %d/%d)",
                GEMINI_MODEL,
                attempt,
                retries,
            )

            response = requests.post(
                url,
                json=payload,
                timeout=120,
            )

            # ====================================================
            # SUCCESS
            # ====================================================

            if response.status_code == 200:

                data = response.json()

                candidates = data.get(
                    "candidates",
                    []
                )

                if not candidates:

                    raise ValueError(
                        "No candidates in Gemini response."
                    )

                candidate = candidates[0]

                finish_reason = candidate.get(
                    "finishReason",
                    ""
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

            # ====================================================
            # 503 OVERLOADED
            # ====================================================

            if response.status_code == 503:

                if attempt < retries:

                    # Exponential backoff:
                    #
                    # attempt 1 -> 15 sec
                    # attempt 2 -> 30 sec
                    # attempt 3 -> 60 sec
                    # attempt 4 -> 90 sec
                    #
                    wait = min(
                        15 * (2 ** (attempt - 1)),
                        90
                    )

                    log.warning(
                        "Gemini 503 overloaded — "
                        "waiting %ds before retry %d/%d",
                        wait,
                        wait,
                        attempt + 1,
                        retries,
                    )

                    time.sleep(wait)

                    continue

                log.error(
                    "Gemini model remained overloaded "
                    "after %d attempts.",
                    retries,
                )

                raise RuntimeError(
                    "Gemini 503 overloaded."
                )

            # ====================================================
            # 429 RATE LIMIT
            # ====================================================

            if response.status_code == 429:

                if attempt < retries:

                    wait = min(
                        30 * attempt,
                        120
                    )

                    log.warning(
                        "Gemini 429 rate limited — "
                        "waiting %ds before retry %d/%d",
                        wait,
                        attempt + 1,
                        retries,
                    )

                    time.sleep(wait)

                    continue

                raise RuntimeError(
                    "Gemini 429 rate limit."
                )

            # ====================================================
            # 404 MODEL NOT FOUND
            # ====================================================

            if response.status_code == 404:

                log.error(
                    "Gemini model not available: %s",
                    GEMINI_MODEL,
                )

                log.error(
                    "Response: %s",
                    response.text,
                )

                raise RuntimeError(
                    f"Gemini model {GEMINI_MODEL} "
                    "is not available."
                )

            # ====================================================
            # OTHER HTTP ERROR
            # ====================================================

            if not response.ok:

                log.error(
                    "Gemini API error: HTTP %d",
                    response.status_code,
                )

                log.error(
                    "Response: %s",
                    response.text,
                )

                response.raise_for_status()

        except requests.exceptions.Timeout as e:

            if attempt == retries:

                raise RuntimeError(
                    "Gemini request timed out."
                ) from e

            wait = 10 * attempt

            log.warning(
                "Gemini timeout — "
                "waiting %ds before retry.",
                wait,
            )

            time.sleep(wait)

        except requests.exceptions.RequestException as e:

            if attempt == retries:

                raise RuntimeError(
                    f"Gemini request failed: {e}"
                ) from e

            wait = 10 * attempt

            log.warning(
                "Request error — "
                "waiting %ds before retry.",
                wait,
            )

            time.sleep(wait)

    raise RuntimeError(
        f"All {retries} Gemini retries exhausted."
    )


# ============================================================
# CLEAN JSON
# ============================================================

def clean_json(raw: str) -> str:

    raw = raw.strip()

    if raw.startswith("```"):

        newline = raw.find("\n")

        if newline != -1:
            raw = raw[newline + 1:]

    if raw.endswith("```"):

        raw = raw[
            :raw.rfind("```")
        ]

    return raw.strip()


# ============================================================
# ANALYZE ONE BATCH
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

    cleaned = clean_json(raw)

    result = json.loads(cleaned)

    analyses = result.get(
        "analyses",
        []
    )

    return analyses, result


# ============================================================
# RUN ANALYSIS
# ============================================================

def run_analysis(
    news_data: dict,
) -> dict:

    news_items = news_data.get(
        "news",
        []
    )

    prices = news_data.get(
        "prices",
        {}
    )

    generated_at = news_data.get(
        "generated_at",
        ""
    )

    mode = news_data.get(
        "mode",
        ""
    )

    all_analyses = []

    last_result = {}

    total = len(news_items)

    for i in range(
        0,
        total,
        BATCH_SIZE
    ):

        batch = news_items[
            i:i + BATCH_SIZE
        ]

        log.info(
            "Batch %d-%d of %d...",
            i + 1,
            i + len(batch),
            total,
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
                    5
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
                            "Sub-batch failed: %s",
                            sub_e,
                        )

            else:

                log.error(
                    "Invalid Gemini response: %s",
                    e,
                )

                continue

        # =====================================================
        # JSON ERROR
        # =====================================================

        except json.JSONDecodeError as e:

            log.error(
                "JSON parse error in batch %d: %s",
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

            continue

    # =========================================================
    # FINAL OUTPUT
    # =========================================================

    return {
        "analyses": all_analyses,

        "market_summary": last_result.get(
            "market_summary",
            "Market data processed."
        ),

        "top_pick": last_result.get(
            "top_pick"
        ),

        "top_pick_reason": last_result.get(
            "top_pick_reason",
            ""
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
            0
        ),
    )

    result = run_analysis(
        news_data
    )

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
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
                []
            )
        ),
        result.get(
            "top_pick",
            "none"
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

