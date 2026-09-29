"""
stock_analyzer.py
Gemini-powered Indian stock news analyzer.

Uses ONE Gemini model.
No fallback models.
No model retry chain.
"""

import json
import logging
import os
from pathlib import Path

import requests


# ============================================================
# CONFIG
# ============================================================

INPUT_FILE = Path("data/news_raw.json")
OUTPUT_FILE = Path("data/analysis.json")

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Use one model only.
# Change this ONLY if your API key does not support it.
GEMINI_MODEL = "gemini-2.5-flash"

BATCH_SIZE = 10


# ============================================================
# PROMPT
# ============================================================

SYSTEM_PROMPT = """
You are a senior equity research analyst for Indian stock markets
(NSE/BSE).

Analyze the supplied news items.

Return ONLY one valid JSON object.
Do NOT return markdown.
Do NOT return ```json.
Do NOT add explanations outside JSON.

Keep every text field to ONE short sentence.

JSON schema:

{
  "analyses": [
    {
      "news_id": "string",
      "symbol": "string or null",
      "headline": "one short sentence",

      "event_type":
        "quarterly_result|new_order|bulk_deal|corporate_action|ma_event|general",

      "fundamentals": {
        "eps_impact": "positive|negative|neutral|unknown",
        "revenue_direction": "up|down|flat|unknown",
        "margin_trend": "expanding|contracting|stable|unknown",
        "debt_concern": false,
        "commentary": "one sentence"
      },

      "price_impact": {
        "direction": "positive|negative|neutral|unknown",
        "strength": "high|medium|low|unknown",
        "time_horizon": "intraday|short_term|medium_term|unknown",
        "rationale": "one sentence"
      },

      "signal": "BUY|HOLD|WATCH|AVOID",
      "conviction": 5,

      "key_risks": [
        "short phrase"
      ],

      "key_catalysts": [
        "short phrase"
      ],

      "action_items": [
        "short phrase"
      ]
    }
  ],

  "market_summary": "one sentence",

  "top_pick": "SYMBOL or null",

  "top_pick_reason": "one sentence"
}
"""


# ============================================================
# GEMINI API
# ============================================================

def call_gemini(prompt: str, api_key: str) -> str:
    """
    Call exactly one Gemini model.

    No fallback.
    No retry.
    """

    url = (
        f"{GEMINI_BASE}/models/"
        f"{GEMINI_MODEL}:generateContent"
        f"?key={api_key}"
    )

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
            "responseMimeType": "application/json"
        }
    }

    log.info("Calling Gemini model: %s", GEMINI_MODEL)

    response = requests.post(
        url,
        json=payload,
        timeout=120
    )

    # --------------------------------------------------------
    # Error handling
    # --------------------------------------------------------

    if response.status_code != 200:

        log.error(
            "Gemini API error: HTTP %s",
            response.status_code
        )

        log.error(
            "Gemini response: %s",
            response.text[:2000]
        )

        response.raise_for_status()

    data = response.json()

    candidates = data.get("candidates", [])

    if not candidates:
        raise RuntimeError(
            "Gemini returned no candidates"
        )

    candidate = candidates[0]

    finish_reason = candidate.get(
        "finishReason",
        ""
    )

    if finish_reason == "MAX_TOKENS":
        raise RuntimeError(
            "Gemini response exceeded MAX_OUTPUT_TOKENS"
        )

    content = candidate.get(
        "content",
        {}
    )

    parts = content.get(
        "parts",
        []
    )

    if not parts:
        raise RuntimeError(
            "Gemini returned empty content"
        )

    text = parts[0].get(
        "text",
        ""
    )

    if not text:
        raise RuntimeError(
            "Gemini returned empty text"
        )

    return text.strip()


# ============================================================
# JSON CLEANING
# ============================================================

def clean_json(raw: str) -> str:
    """
    Remove accidental markdown fences if Gemini returns them.
    """

    raw = raw.strip()

    if raw.startswith("```"):
        first_newline = raw.find("\n")

        if first_newline != -1:
            raw = raw[first_newline + 1:]

    if raw.endswith("```"):
        raw = raw[:-3]

    return raw.strip()


# ============================================================
# PROMPT BUILDER
# ============================================================

def build_prompt(
    news_items: list,
    prices: dict,
    generated_at: str,
    mode: str
) -> str:

    lines = [
        SYSTEM_PROMPT,
        "",
        f"Date: {generated_at}",
        f"Mode: {mode}",
        "",
        "=== NEWS ITEMS ==="
    ]

    for item in news_items:

        symbols = ", ".join(
            item.get("symbols", [])
        )

        if not symbols:
            symbols = "N/A"

        summary = item.get(
            "summary",
            ""
        )[:250]

        lines.append(
            f"""
[{item.get('id', '')}]
Type: {item.get('type', '').upper()}
Title: {item.get('title', '')}
Symbols: {symbols}
Summary: {summary}
"""
        )

    if prices:

        lines.append(
            "\n=== CURRENT PRICES ==="
        )

        for symbol, price_data in list(
            prices.items()
        )[:30]:

            price = price_data.get(
                "price",
                "N/A"
            )

            change_pct = price_data.get(
                "change_pct"
            )

            if change_pct is None:
                change = "N/A"
            else:
                change = f"{change_pct:+.2f}%"

            lines.append(
                f"{symbol}: ₹{price} ({change})"
            )

    lines.append(
        "\nReturn ONLY the JSON object."
    )

    return "\n".join(lines)


# ============================================================
# ANALYZE BATCH
# ============================================================

def analyze_batch(
    items: list,
    prices: dict,
    generated_at: str,
    mode: str,
    api_key: str
):

    prompt = build_prompt(
        items,
        prices,
        generated_at,
        mode
    )

    log.info(
        "Sending %d characters for %d news items",
        len(prompt),
        len(items)
    )

    raw = call_gemini(
        prompt,
        api_key
    )

    cleaned = clean_json(raw)

    try:

        result = json.loads(
            cleaned
        )

    except json.JSONDecodeError as exc:

        log.error(
            "Gemini returned invalid JSON"
        )

        log.error(
            "Raw response: %s",
            raw[:5000]
        )

        raise exc

    analyses = result.get(
        "analyses",
        []
    )

    return analyses, result


# ============================================================
# MAIN ANALYSIS
# ============================================================

def run_analysis(news_data: dict) -> dict:

    api_key = os.environ.get(
        "GEMINI_API_KEY"
    )

    if not api_key:

        raise RuntimeError(
            "GEMINI_API_KEY environment variable is missing"
        )

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
            "Batch %d-%d of %d",
            i + 1,
            i + len(batch),
            total
        )

        analyses, result = analyze_batch(
            batch,
            prices,
            generated_at,
            mode,
            api_key
        )

        all_analyses.extend(
            analyses
        )

        last_result = result

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

        "ai_model": GEMINI_MODEL
    }


# ============================================================
# MAIN
# ============================================================

def main():

    if not INPUT_FILE.exists():

        log.error(
            "Input file not found: %s",
            INPUT_FILE
        )

        return

    log.info(
        "Reading news from %s",
        INPUT_FILE
    )

    news_data = json.loads(
        INPUT_FILE.read_text(
            encoding="utf-8"
        )
    )

    news_count = news_data.get(
        "news_count",
        len(news_data.get("news", []))
    )

    log.info(
        "Analyzing %d news items...",
        news_count
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
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    log.info(
        "Done — %d items. Model used: %s. Top pick: %s",
        len(result.get("analyses", [])),
        result.get("ai_model"),
        result.get("top_pick", "none")
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
        )
    )

    main()
