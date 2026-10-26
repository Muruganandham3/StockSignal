"""
stock_analyzer.py

Sends fetched news + price data to Google Gemini API
and creates structured stock analysis.

Features:
- Gemini 503 retry with exponential backoff
- Gemini model fallback
- Batch processing to prevent MAX_TOKENS
- Automatic smaller-batch retry
- JSON validation
- Duplicate protection
- Original news ordering
"""

import json
import logging
import os
import random
import time
from pathlib import Path

import requests


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
# GEMINI CONFIG (UPDATED FOR LATEST STABLE ENDPOINTS)
# =============================================================================

GEMINI_MODEL = "gemini-3.7-flash"

# Fallback models (Kept as backup strings in case 3.7 hits a temporary peak)
GEMINI_FALLBACK_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
]

GEMINI_BASE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models"
)

# Number of retries for temporary errors
MAX_RETRIES = 5

# Number of news items per Gemini request
#
# 43 news items -> 6 requests:
#
# 8
# 8
# 8
# 8
# 8
# 3
#
BATCH_SIZE = 8

# Maximum Gemini output tokens per request
MAX_OUTPUT_TOKENS = 8192

# This variable will be updated after a successful Gemini request
ACTIVE_GEMINI_MODEL = GEMINI_MODEL


# =============================================================================
# SYSTEM PROMPT
# =============================================================================

SYSTEM_PROMPT = """
You are a senior equity research analyst specializing in Indian stock markets
(NSE/BSE).

You receive raw news items and price data and return structured JSON analysis.

For EACH news item, analyze:

1. Fundamentals impact:
   - EPS impact
   - Revenue direction
   - Margin trend
   - Debt concern

2. Price impact:
   - Short-term 1-5 days
   - Medium-term 1-3 months

3. Signal:
   - BUY
   - HOLD
   - WATCH
   - AVOID

4. Conviction:
   - 1 to 10

5. Key risks

6. Key catalysts

7. Action items

IMPORTANT RULES:

- Analyze every news item provided.
- Return exactly ONE analysis object for every news item.
- Do not skip news items.
- Do not invent facts that are not supported by the supplied news.
- Keep commentary concise.
- Keep price estimates realistic and clearly uncertain.
- key_risks: maximum 3 items.
- key_catalysts: maximum 3 items.
- action_items: maximum 3 items.
- Do not repeat the entire news article.
- Return ONLY valid JSON.
- Do NOT use Markdown.
- Do NOT use ```json.
- Do NOT include explanations outside JSON.

JSON SCHEMA:

{
  "analyses": [
    {
      "news_id": "string",
      "symbol": "string or null",
      "headline": "string",
      "event_type": "quarterly_result | new_order | bulk_deal | corporate_action | ma_event | general",

      "fundamentals": {
        "eps_impact": "positive | negative | neutral | unknown",
        "revenue_direction": "up | down | flat | unknown",
        "margin_trend": "expanding | contracting | stable | unknown",
        "debt_concern": true,
        "commentary": "short concise analysis"
      },

      "price_impact": {
        "short_term_pct_low": 0,
        "short_term_pct_high": 0,
        "medium_term_pct_low": 0,
        "medium_term_pct_high": 0,
        "rationale": "short concise rationale"
      },

      "signal": "BUY | HOLD | WATCH | AVOID",

      "conviction": 1,

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
  ]
}
"""


# =============================================================================
# BUILD BATCH PROMPT
# =============================================================================

def build_batch_prompt(news_data: dict, news_items: list) -> str:
    """
    Build a prompt for a small batch of news items.

    Keeping batches small prevents Gemini from hitting MAX_TOKENS.
    """

    prices = news_data.get("prices", {})
    generated_at = news_data.get("generated_at", "")
    mode = news_data.get("mode", "")

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

    for item in news_items:

        symbols = item.get("symbols", [])

        if not isinstance(symbols, list):
            symbols = [str(symbols)]

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

    lines.append(
        """
==================================================
CURRENT PRICES
==================================================
"""
    )

    for sym, price_data in prices.items():

        change_pct = price_data.get("change_pct")

        if change_pct is not None:

            try:
                change_text = f"{float(change_pct):+.2f}%"
            except (TypeError, ValueError):
                change_text = str(change_pct)

        else:
            change_text = "N/A"

        lines.append(
            f"{sym}: "
            f"₹{price_data.get('price', 'N/A')} "
            f"({change_text}) | "
            f"52W H: ₹{price_data.get('52w_high', 'N/A')} | "
            f"L: ₹{price_data.get('52w_low', 'N/A')}"
        )

    lines.append(
        """
==================================================
INSTRUCTIONS
==================================================

Analyze EVERY news item above.

Return exactly one analysis object for each news item.

Keep the response concise enough to fit within the output token limit.

Maximum:
- key_risks: 3
- key_catalysts: 3
- action_items: 3

Return ONLY valid JSON.
"""
    )

    return "\n".join(lines)


# =============================================================================
# BACKOFF
# =============================================================================

def calculate_backoff(attempt: int) -> float:
    """
    Exponential backoff with jitter.

    Approximate waits:

    Attempt 1 -> 5-8 sec
    Attempt 2 -> 10-13 sec
    Attempt 3 -> 20-23 sec
    Attempt 4 -> 40-43 sec
    Attempt 5 -> 60-63 sec
    """

    base = min(
        60,
        5 * (2 ** (attempt - 1))
    )

    jitter = random.uniform(0, 3)

    return base + jitter


# =============================================================================
# GEMINI URL
# =============================================================================

def get_gemini_url(
    model: str,
    api_key: str,
) -> str:

    return (
        f"{GEMINI_BASE_URL}/"
        f"{model}:generateContent"
        f"?key={api_key}"
    )


# =============================================================================
# CLEAN JSON RESPONSE
# =============================================================================

def clean_json_response(raw: str) -> str:
    """
    Remove common Markdown code fences if Gemini returns them.
    """

    raw = raw.strip()

    if raw.startswith("```json"):
        raw = raw[len("```json"):]

    elif raw.startswith("```"):
        raw = raw[len("```"):]

    if raw.endswith("```"):
        raw = raw[:-3]

    return raw.strip()


# =============================================================================
# CALL ONE GEMINI MODEL
# =============================================================================

def call_gemini_model(
    prompt: str,
    model: str,
    api_key: str,
    retries: int = MAX_RETRIES,
) -> str:

    url = get_gemini_url(
        model,
        api_key,
    )

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
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
            "responseMimeType": "application/json",
        },
    }

    for attempt in range(1, retries + 1):

        try:

            log.info(
                "Calling Gemini model %s (attempt %d/%d)...",
                model,
                attempt,
                retries,
            )

            response = requests.post(
                url,
                json=payload,
                timeout=120,
            )

            status = response.status_code

            # =================================================================
            # SUCCESS
            # =================================================================

            if status == 200:

                data = response.json()

                candidates = data.get(
                    "candidates",
                    [],
                )

                if not candidates:

                    raise ValueError(
                        "Gemini returned no candidates: "
                        + json.dumps(data)[:2000]
                    )

                candidate = candidates[0]

                finish_reason = candidate.get(
                    "finishReason"
                )

                if finish_reason not in (
                    "STOP",
                    None,
                ):

                    log.warning(
                        "Gemini generation finished with reason: %s",
                        finish_reason,
                    )

                # =============================================================
                # IMPORTANT:
                # MAX_TOKENS means response may be incomplete.
                # Do NOT pass it directly to json.loads().
                # Raise an error so the batch can be retried smaller.
                # =============================================================

                if finish_reason == "MAX_TOKENS":

                    raise RuntimeError(
                        "Gemini response reached MAX_TOKENS "
                        "and may contain incomplete JSON."
                    )

                parts = (
                    candidate
                    .get("content", {})
                    .get("parts", [])
                )

                if not parts:

                    raise ValueError(
                        "Gemini returned no content parts."
                    )

                text_parts = []

                for part in parts:

                    if "text" in part:

                        text_parts.append(
                            part["text"]
                        )

                if not text_parts:

                    raise ValueError(
                        "Gemini returned no text content."
                    )

                result = "".join(
                    text_parts
                ).strip()

                if not result:

                    raise ValueError(
                        "Gemini returned empty text."
                    )

                log.info(
                    "Gemini response received successfully using %s",
                    model,
                )

                return result

            # =================================================================
            # TEMPORARY HTTP ERRORS
            # =================================================================

            if status in (
                429,
                500,
                502,
                503,
                504,
            ):

                try:

                    error_data = response.json()

                    error_message = (
                        error_data
                        .get("error", {})
                        .get(
                            "message",
                            response.text,
                        )
                    )

                except Exception:

                    error_message = response.text

                log.warning(
                    "Gemini temporary error %d on %s: %s",
                    status,
                    model,
                    error_message,
                )

                if attempt < retries:

                    wait = calculate_backoff(
                        attempt
                    )

                    log.warning(
                        "Retrying %s in %.1f seconds...",
                        model,
                        wait,
                    )

                    time.sleep(wait)

                    continue

                raise RuntimeError(
                    f"Gemini model {model} unavailable "
                    f"after {retries} attempts. "
                    f"HTTP {status}: {error_message}"
                )

            # =================================================================
            # PERMANENT HTTP ERROR
            # =================================================================

            try:

                error_data = response.json()

                error_message = (
                    error_data
                    .get("error", {})
                    .get(
                        "message",
                        response.text,
                    )
                )

            except Exception:

                error_message = response.text

            raise RuntimeError(
                f"Gemini API error HTTP {status}: "
                f"{error_message}"
            )

        # =====================================================================
        # TIMEOUT
        # =====================================================================

        except requests.exceptions.Timeout:

            log.warning(
                "Gemini request timed out for %s "
                "(attempt %d/%d)",
                model,
                attempt,
                retries,
            )

            if attempt < retries:

                wait = calculate_backoff(
                    attempt
                )

                log.warning(
                    "Retrying after %.1f seconds...",
                    wait,
                )

                time.sleep(wait)

                continue

            raise

        # =====================================================================
        # CONNECTION ERROR
        # =====================================================================

        except requests.exceptions.ConnectionError as e:

            log.warning(
                "Gemini connection error for %s: %s",
                model,
                e,
            )

            if attempt < retries:

                wait = calculate_backoff(
                    attempt
                )

                log.warning(
                    "Retrying after %.1f seconds...",
                    wait,
                )

                time.sleep(wait)

                continue

            raise

        # =====================================================================
        # MAX TOKENS / OTHER RETRYABLE GENERATION ERROR
        # =====================================================================

        except RuntimeError as e:

            error_text = str(e)

            if "MAX_TOKENS" in error_text:

                log.warning(
                    "Gemini %s reached MAX_TOKENS.",
                    model,
                )

                # Do NOT repeatedly retry the exact same oversized
                # request. The caller will reduce the batch size.
                raise

            raise

    raise RuntimeError(
        f"All retries exhausted for Gemini model {model}."
    )


# =============================================================================
# GEMINI CALL WITH FALLBACK MODELS
# =============================================================================

def call_gemini(prompt: str) -> str:

    global ACTIVE_GEMINI_MODEL

    api_key = os.environ.get(
        "GEMINI_API_KEY"
    )

    if not api_key:

        raise ValueError(
            "GEMINI_API_KEY environment variable is missing."
        )

    models_to_try = [
        GEMINI_MODEL,
        *GEMINI_FALLBACK_MODELS,
    ]

    # Remove duplicates
    models_to_try = list(
        dict.fromkeys(models_to_try)
    )

    last_error = None

    for model in models_to_try:

        log.info(
            "Trying Gemini model: %s",
            model,
        )

        try:

            result = call_gemini_model(
                prompt=prompt,
                model=model,
                api_key=api_key,
                retries=MAX_RETRIES,
            )

            ACTIVE_GEMINI_MODEL = model

            return result

        except RuntimeError as e:

            last_error = e

            # MAX_TOKENS should be handled by batch-size reduction,
            # NOT by switching models immediately.
            if "MAX_TOKENS" in str(e):

                raise

            log.error(
                "Gemini model %s failed: %s",
                model,
                e,
            )

            log.info(
                "Trying next Gemini fallback model..."
            )

        except Exception as e:

            last_error = e

            log.error(
                "Gemini model %s failed: %s",
                model,
                e,
            )

            log.info(
                "Trying next Gemini fallback model..."
            )

    raise RuntimeError(
        "All Gemini models failed. "
        f"Last error: {last_error}"
    )


# =============================================================================
# ANALYZE ONE BATCH
# =============================================================================

def analyze_batch(
    news_data: dict,
    news_items: list,
) -> dict:

    prompt = build_batch_prompt(
        news_data,
        news_items,
    )

    log.info(
        "Analyzing batch of %d news items (%d chars)...",
        len(news_items),
        len(prompt),
    )

    raw = call_gemini(
        prompt
    )

    raw = clean_json_response(
        raw
    )

    try:

        result = json.loads(
            raw
        )

    except json.JSONDecodeError as e:

        log.error(
            "Invalid JSON from Gemini batch."
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

    if not isinstance(result, dict):

        raise ValueError(
            "Gemini response is not a JSON object."
        )

    analyses = result.get(
        "analyses"
    )

    if not isinstance(
        analyses,
        list,
    ):

        raise ValueError(
            "Gemini response does not contain "
            "an 'analyses' array."
        )

    return result


# =============================================================================
# ANALYZE WITH AUTOMATIC BATCH REDUCTION
# =============================================================================

def analyze_batch_with_retry(
    news_data: dict,
    news_items: list,
) -> list:

    """
    Analyze a batch.

    If Gemini reaches MAX_TOKENS or returns invalid JSON,
    split the batch into smaller pieces.

    Example:

    8 items
       ↓
    MAX_TOKENS
       ↓
    4 + 4
       ↓
    success
    """

    if not news_items:

        return []

    try:

        result = analyze_batch(
            news_data,
            news_items,
        )

        analyses = result.get(
            "analyses",
            [],
        )

        log.info(
            "Batch completed successfully: "
            "%d/%d analyses returned.",
            len(analyses),
            len(news_items),
        )

        return analyses

    except Exception as e:

        error_text = str(e)

        log.warning(
            "Batch of %d items failed: %s",
            len(news_items),
            error_text,
        )

        # If only one news item remains, we cannot split further.
        if len(news_items) == 1:

            raise

        # Split batch in half
        middle = len(news_items) // 2

        first_half = news_items[
            :middle
        ]

        second_half = news_items[
            middle:
        ]

        log.warning(
            "Splitting failed batch: %d items -> %d + %d",
            len(news_items),
            len(first_half),
            len(second_half),
        )

        first_results = analyze_batch_with_retry(
            news_data,
            first_half,
        )

        second_results = analyze_batch_with_retry(
            news_data,
            second_half,
        )

        return (
            first_results
            + second_results
        )


# =============================================================================
# MARKET SUMMARY
# =============================================================================

def build_market_summary(
    analyses: list,
) -> dict:

    if not analyses:

        return {
            "market_summary": "No analysis available.",
            "top_pick": None,
            "top_pick_reason": "",
        }

    # -------------------------------------------------------------------------
    # Count signals
    # -------------------------------------------------------------------------

    signal_counts = {
        "BUY": 0,
        "HOLD": 0,
        "WATCH": 0,
        "AVOID": 0,
    }

    for item in analyses:

        signal = item.get(
            "signal"
        )

        if signal in signal_counts:

            signal_counts[
                signal
            ] += 1

    # -------------------------------------------------------------------------
    # Find highest conviction
    # -------------------------------------------------------------------------

    ranked = sorted(
        analyses,
        key=lambda x: (
            x.get(
                "conviction",
                0,
            )
            if isinstance(
                x.get(
                    "conviction",
                    0,
                ),
                (int, float),
            )
            else 0
        ),
        reverse=True,
    )

    top = ranked[0]

    top_symbol = top.get(
        "symbol"
    )

    top_headline = top.get(
        "headline"
    )

    top_reason = (
        top_headline
        if top_headline
        else "Highest conviction analysis."
    )

    market_summary = (
        f"Analyzed {len(analyses)} news events. "
        f"Signals: "
        f"BUY {signal_counts['BUY']}, "
        f"HOLD {signal_counts['HOLD']}, "
        f"WATCH {signal_counts['WATCH']}, "
        f"AVOID {signal_counts['AVOID']}."
    )

    return {
        "market_summary": market_summary,
        "top_pick": top_symbol,
        "top_pick_reason": top_reason,
    }


# =============================================================================
# RUN COMPLETE ANALYSIS
# =============================================================================

def run_analysis(
    news_data: dict,
) -> dict:

    news_items = news_data.get(
        "news",
        [],
    )

    if not news_items:

        log.warning(
            "No news items found."
        )

        return {
            "analyses": [],
            "market_summary": "No news available.",
            "top_pick": None,
            "top_pick_reason": "",
        }

    # -------------------------------------------------------------------------
    # Split into initial batches
    # -------------------------------------------------------------------------

    batches = [
        news_items[i:i + BATCH_SIZE]
        for i in range(
            0,
            len(news_items),
            BATCH_SIZE,
        )
    ]

    log.info(
        "Total news: %d | "
        "Batch size: %d | "
        "Total batches: %d",
        len(news_items),
        BATCH_SIZE,
        len(batches),
    )

    all_analyses = []

    # -------------------------------------------------------------------------
    # Process every batch
    # -------------------------------------------------------------------------

    for index, batch in enumerate(
        batches,
        start=1,
    ):

        log.info(
            "=================================================="
        )

        log.info(
            "Processing batch %d/%d (%d items)...",
            index,
            len(batches),
            len(batch),
        )

        batch_results = analyze_batch_with_retry(
            news_data,
            batch,
        )

        all_analyses.extend(
            batch_results
        )

        log.info(
            "Batch %d/%d complete. "
            "Total analyses so far: %d",
            index,
            len(batches),
            len(all_analyses),
        )

    # -------------------------------------------------------------------------
    # Remove duplicate news IDs
    # -------------------------------------------------------------------------

    seen_ids = set()

    unique_analyses = []

    for analysis in all_analyses:

        news_id = analysis.get(
            "news_id"
        )

        # If no ID exists, keep it
        if news_id is None:

            unique_analyses.append(
                analysis
            )

            continue

        if news_id not in seen_ids:

            seen_ids.add(
                news_id
            )

            unique_analyses.append(
                analysis
            )

    # -------------------------------------------------------------------------
    # Preserve original news order
    # -------------------------------------------------------------------------

    original_order = {
        str(
            item.get(
                "id"
            )
        ): index
        for index, item in enumerate(
            news_items
        )
    }

    unique_analyses.sort(
        key=lambda x: original_order.get(
            str(
                x.get(
                    "news_id"
                )
            ),
            999999,
        )
    )

    # -------------------------------------------------------------------------
    # Log result count
    # -------------------------------------------------------------------------

    log.info(
        "=================================================="
    )

    log.info(
        "News items received: %d",
        len(news_items),
    )

    log.info(
        "Unique analyses generated: %d",
        len(unique_analyses),
    )

    # -------------------------------------------------------------------------
    # Market summary
    # -------------------------------------------------------------------------

    summary = build_market_summary(
        unique_analyses
    )

    return {
        "analyses": unique_analyses,
        "market_summary": summary[
            "market_summary"
        ],
        "top_pick": summary[
            "top_pick"
        ],
        "top_pick_reason": summary[
            "top_pick_reason"
        ],
    }


# =============================================================================
# MAIN
# =============================================================================

def main():

    # -------------------------------------------------------------------------
    # Check input
    # -------------------------------------------------------------------------

    if not INPUT_FILE.exists():

        log.error(
            "Input file not found at %s. "
            "Run news fetcher first.",
            INPUT_FILE,
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
    # Read news data
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
    # Log news count
    # -------------------------------------------------------------------------

    news_count = len(
        news_data.get(
            "news",
            [],
        )
    )

    log.info(
        "Analyzing %d news items...",
        news_count,
    )

    # -------------------------------------------------------------------------
    # Run analysis
    # -------------------------------------------------------------------------

    result = run_analysis(
        news_data
    )

    # -------------------------------------------------------------------------
    # Add original price data
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
    # Add AI model
    # -------------------------------------------------------------------------

    result["ai_model"] = ACTIVE_GEMINI_MODEL

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
    # Final log
    # -------------------------------------------------------------------------

    log.info(
        "=================================================="
    )

    log.info(
        "Analysis saved to %s",
        OUTPUT_FILE,
    )

    log.info(
        "%d items analyzed.",
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
        "Gemini model used: %s",
        ACTIVE_GEMINI_MODEL,
    )

    log.info(
        "=================================================="
    )


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    main()
