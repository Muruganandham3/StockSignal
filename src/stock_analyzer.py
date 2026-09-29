"""
stock_analyzer.py

Sends all fetched news + price data to Google Gemini API.

Configuration:
- Gemini only
- Single API request
- No fallback
- No batching
- JSON response
- Free-tier compatible
"""

import json
import logging
import os
import time
from pathlib import Path

from google import genai
from google.genai import types


# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

log = logging.getLogger(__name__)


# =============================================================================
# FILE CONFIG
# =============================================================================

INPUT_FILE = Path("data/news_raw.json")
OUTPUT_FILE = Path("data/analysis.json")


# =============================================================================
# GEMINI CONFIG
# =============================================================================

GEMINI_MODEL = "gemini-3.5-flash"

MAX_RETRIES = 3

MAX_OUTPUT_TOKENS = 16384


# =============================================================================
# SYSTEM PROMPT
# =============================================================================

SYSTEM_PROMPT = """
You are a senior equity research analyst specializing in Indian stock
markets (NSE/BSE).

You receive raw news items and current price data.

Analyze EVERY news item provided.

For each news item analyze:

1. Fundamentals impact:
   - EPS impact
   - Revenue direction
   - Margin trend
   - Debt concern

2. Price impact:
   - Short-term expected impact (1-5 days)
   - Medium-term expected impact (1-3 months)

3. Signal:
   - BUY
   - HOLD
   - WATCH
   - AVOID

4. Conviction:
   - Integer from 1 to 10

5. Key risks

6. Key catalysts

7. Action items

IMPORTANT RULES:

- Analyze every news item.
- Do not skip news items.
- Do not invent facts.
- Base analysis on supplied news and price data.
- Keep each analysis concise.
- key_risks: maximum 3 items.
- key_catalysts: maximum 3 items.
- action_items: maximum 3 items.
- Do not repeat the complete news article.
- Return ONLY valid JSON.

JSON STRUCTURE:

{
  "analyses": [
    {
      "news_id": "string",
      "symbol": "string or null",
      "headline": "string",

      "event_type":
        "quarterly_result | new_order | bulk_deal | corporate_action | ma_event | general",

      "fundamentals": {
        "eps_impact":
          "positive | negative | neutral | unknown",

        "revenue_direction":
          "up | down | flat | unknown",

        "margin_trend":
          "expanding | contracting | stable | unknown",

        "debt_concern":
          true,

        "commentary":
          "short concise analysis"
      },

      "price_impact": {
        "short_term_pct_low": 0,
        "short_term_pct_high": 0,

        "medium_term_pct_low": 0,
        "medium_term_pct_high": 0,

        "rationale":
          "short concise rationale"
      },

      "signal":
        "BUY | HOLD | WATCH | AVOID",

      "conviction":
        1,

      "key_risks": [
        "risk"
      ],

      "key_catalysts": [
        "catalyst"
      ],

      "action_items": [
        "action"
      ]
    }
  ],

  "market_summary":
    "2-3 sentence overall market tone for the day",

  "top_pick":
    "symbol or null",

  "top_pick_reason":
    "1 sentence"
}

Return ONLY valid JSON.
"""


# =============================================================================
# BUILD PROMPT
# =============================================================================

def build_prompt(news_data: dict) -> str:

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

    lines = [
        SYSTEM_PROMPT,
        "",
        f"Date/time: {generated_at}",
        f"Mode: {mode}",
        "",
        "==================================================",
        "NEWS ITEMS",
        "==================================================",
    ]

    # -------------------------------------------------------------------------
    # NEWS ITEMS
    # -------------------------------------------------------------------------

    for item in news_items:

        symbols = item.get(
            "symbols",
            [],
        )

        if not isinstance(
            symbols,
            list,
        ):
            symbols = [
                str(symbols)
            ]

        lines.append(
            f"""
[{item.get("id", "N/A")}] ({str(item.get("type", "")).upper()})

Title:
{item.get("title", "")}

Source:
{item.get("source", "")}

Symbols:
{", ".join(symbols) if symbols else "unspecified"}

Summary:
{item.get("summary", "")}
"""
        )

    # -------------------------------------------------------------------------
    # CURRENT PRICES
    # -------------------------------------------------------------------------

    lines.append(
        """
==================================================
CURRENT PRICES
==================================================
"""
    )

    for symbol, price_data in prices.items():

        change_pct = price_data.get(
            "change_pct"
        )

        if change_pct is not None:

            try:

                change_text = (
                    f"{float(change_pct):+.2f}%"
                )

            except (
                TypeError,
                ValueError,
            ):

                change_text = str(
                    change_pct
                )

        else:

            change_text = "N/A"

        lines.append(
            f"{symbol}: "
            f"₹{price_data.get('price', 'N/A')} "
            f"({change_text}) | "
            f"52W H: ₹{price_data.get('52w_high', 'N/A')} | "
            f"L: ₹{price_data.get('52w_low', 'N/A')}"
        )

    # -------------------------------------------------------------------------
    # FINAL INSTRUCTIONS
    # -------------------------------------------------------------------------

    lines.append(
        """
==================================================
FINAL INSTRUCTIONS
==================================================

Analyze ALL news items.

Return exactly ONE analysis object for EVERY news item.

Keep the response concise.

Maximum:
- key_risks: 3
- key_catalysts: 3
- action_items: 3

Return ONLY valid JSON.
"""
    )

    return "\n".join(lines)


# =============================================================================
# CLEAN JSON
# =============================================================================

def clean_json_response(
    raw: str,
) -> str:

    raw = raw.strip()

    if raw.startswith(
        "```json"
    ):

        raw = raw[
            len("```json"):
        ]

    elif raw.startswith(
        "```"
    ):

        raw = raw[
            len("```"):
        ]

    if raw.endswith(
        "```"
    ):

        raw = raw[
            :-len("```")
        ]

    return raw.strip()


# =============================================================================
# CALL GEMINI
# =============================================================================

def call_gemini(
    prompt: str,
    retries: int = MAX_RETRIES,
) -> str:

    api_key = os.environ.get(
        "GEMINI_API_KEY"
    )

    if not api_key:

        raise ValueError(
            "GEMINI_API_KEY environment variable is missing."
        )

    client = genai.Client(
        api_key=api_key
    )

    for attempt in range(
        1,
        retries + 1,
    ):

        try:

            log.info(
                "Calling Gemini model %s (attempt %d/%d)...",
                GEMINI_MODEL,
                attempt,
                retries,
            )

            response = client.models.generate_content(
                model=GEMINI_MODEL,

                contents=prompt,

                config=types.GenerateContentConfig(

                    response_mime_type="application/json",

                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            )

            if not response:

                raise RuntimeError(
                    "Gemini returned an empty response."
                )

            raw = response.text

            if not raw:

                raise RuntimeError(
                    "Gemini returned empty text."
                )

            log.info(
                "Gemini response received successfully."
            )

            return raw.strip()

        except Exception as e:

            error_text = str(e)

            log.warning(
                "Gemini request failed "
                "(attempt %d/%d): %s",
                attempt,
                retries,
                error_text,
            )

            if attempt >= retries:

                raise

            # -------------------------------------------------------------
            # Retry temporary service errors.
            # -------------------------------------------------------------

            wait = 5 * attempt

            log.warning(
                "Retrying Gemini in %d seconds...",
                wait,
            )

            time.sleep(
                wait
            )

    raise RuntimeError(
        "All Gemini retries exhausted."
    )


# =============================================================================
# RUN ANALYSIS
# =============================================================================

def run_analysis(
    news_data: dict,
) -> dict:

    news_items = news_data.get(
        "news",
        [],
    )

    log.info(
        "Analyzing %d news items...",
        len(news_items),
    )

    prompt = build_prompt(
        news_data
    )

    log.info(
        "Sending %d chars to Gemini...",
        len(prompt),
    )

    raw = call_gemini(
        prompt
    )

    raw = clean_json_response(
        raw
    )

    # -------------------------------------------------------------------------
    # Parse JSON
    # -------------------------------------------------------------------------

    try:

        result = json.loads(
            raw
        )

    except json.JSONDecodeError as e:

        log.error(
            "Gemini returned invalid JSON."
        )

        log.error(
            "JSON error: %s",
            e,
        )

        log.error(
            "Raw response preview:\n%s",
            raw[:5000],
        )

        raise

    # -------------------------------------------------------------------------
    # Validate response
    # -------------------------------------------------------------------------

    if not isinstance(
        result,
        dict,
    ):

        raise ValueError(
            "Gemini response is not a JSON object."
        )

    analyses = result.get(
        "analyses",
        [],
    )

    if not isinstance(
        analyses,
        list,
    ):

        raise ValueError(
            "Gemini 'analyses' is not a list."
        )

    log.info(
        "Gemini returned %d analyses.",
        len(analyses),
    )

    return result


# =============================================================================
# MAIN
# =============================================================================

def main():

    # -------------------------------------------------------------------------
    # Check input
    # -------------------------------------------------------------------------

    if not INPUT_FILE.exists():

        log.error(
            "Input file not found at %s.",
            INPUT_FILE,
        )

        log.error(
            "Run news_fetcher.py first."
        )

        return

    # -------------------------------------------------------------------------
    # Create output directory
    # -------------------------------------------------------------------------

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # -------------------------------------------------------------------------
    # Read input
    # -------------------------------------------------------------------------

    try:

        news_data = json.loads(
            INPUT_FILE.read_text(
                encoding="utf-8"
            )
        )

    except json.JSONDecodeError as e:

        log.error(
            "Invalid JSON in %s: %s",
            INPUT_FILE,
            e,
        )

        raise

    # -------------------------------------------------------------------------
    # Run analysis
    # -------------------------------------------------------------------------

    result = run_analysis(
        news_data
    )

    # -------------------------------------------------------------------------
    # Add prices
    # -------------------------------------------------------------------------

    result["prices"] = news_data.get(
        "prices",
        {},
    )

    # -------------------------------------------------------------------------
    # Add generated timestamp
    # -------------------------------------------------------------------------

    result["generated_at"] = news_data.get(
        "generated_at"
    )

    # -------------------------------------------------------------------------
    # Add model name
    # -------------------------------------------------------------------------

    result["ai_model"] = GEMINI_MODEL

    # -------------------------------------------------------------------------
    # Save output
    # -------------------------------------------------------------------------

    OUTPUT_FILE.write_text(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # -------------------------------------------------------------------------
    # Final logs
    # -------------------------------------------------------------------------

    log.info(
        "=================================================="
    )

    log.info(
        "Analysis saved to %s",
        OUTPUT_FILE,
    )

    log.info(
        "Items analyzed: %d",
        len(
            result.get(
                "analyses",
                [],
            )
        ),
    )

    log.info(
        "Top pick: %s",
        result.get(
            "top_pick",
            "None",
        ),
    )

    log.info(
        "AI model: %s",
        GEMINI_MODEL,
    )

    log.info(
        "=================================================="
    )


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    main()
