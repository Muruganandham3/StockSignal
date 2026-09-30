"""
stock_analyzer.py

Sends all fetched news + price data to the Google Gemini API in a single execution
to bypass multi-request 429 rate limit errors on the free tier.

Features:
- Primary target: gemini-3.7-flash
- Single-hit processing framework (no batching loops)
- Gemini 503 high-demand retry with exponential backoff and jitter
- Multi-model fallback execution pipeline
- Native JSON schema enforcement
- Deduplication and original ordering preservation
"""

import json
import logging
import os
import random
import time
from pathlib import Path
import requests

# =============================================================================
# LOGGING SETUP
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# =============================================================================
# FILE CONFIGURATION
# =============================================================================
INPUT_FILE = Path("data/news_raw.json")
OUTPUT_FILE = Path("data/analysis.json")

# =============================================================================
# GEMINI ENGINE CONFIGURATION
# =============================================================================
# Primary stable production workhorse model
GEMINI_MODEL = "gemini-3.7-flash"

# Secondary resilience fallback models
GEMINI_FALLBACK_MODELS = [
    "gemini-3.5-flash",
]

# API Endpoint Credentials
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "YOUR_API_KEY_HERE")
BASE_URL = "https://googleapis.com"

# Retry Framework Policies
MAX_RETRIES = 5
INITIAL_BACKOFF = 5.0  # Safe foundational backoff duration for large payloads

# =============================================================================
# CORE API CORE RUNNER
# =============================================================================
def call_gemini_api(model_name: str, prompt: str) -> str:
    """
    Transmits the compiled text prompt to the Gemini API endpoint.
    Manages transient 429 quota spikes or 503 capacity errors via exponential backoff.
    """
    url = f"{BASE_URL}/{model_name}:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }

    backoff = INITIAL_BACKOFF
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info(f"Calling Gemini model {model_name} (attempt {attempt}/{MAX_RETRIES})...")
            
            # Using an extended 90-second timeout to handle large dataset generation tasks
            response = requests.post(url, json=payload, headers=headers, timeout=90)
            
            # Successful response management
            if response.status_code == 200:
                res_json = response.json()
                return res_json['candidates']['content']['parts']['text']
            
            # Check for high demand (503) or structural rate limits (429)
            elif response.status_code in:
                log.warning(f"Gemini error {response.status_code} on {model_name}: {response.text}")
                if attempt == MAX_RETRIES:
                    break
                
                # Apply randomized jitter to prevent lock-step request synchronization
                sleep_time = backoff + random.uniform(1.0, 3.0)
                log.warning(f"Retrying execution pipeline for {model_name} in {sleep_time:.2f} seconds...")
                time.sleep(sleep_time)
                backoff *= 2  # Double the backoff scale
            else:
                log.error(f"Unrecoverable HTTP Error {response.status_code}: {response.text}")
                break
                
        except requests.exceptions.RequestException as e:
            log.warning(f"Network transport issue on execution attempt {attempt}: {e}")
            if attempt == MAX_RETRIES:
                break
            time.sleep(backoff)
            backoff *= 2

    raise RuntimeError(f"Failed to extract structured response from {model_name} after {MAX_RETRIES} attempts.")


def generate_analysis_with_fallback(prompt: str) -> str:
    """
    Coordinates primary generation engine call and drops down sequentially 
    to configured fallback alternatives if unrecoverable blocks arise.
    """
    models_to_try = [GEMINI_MODEL] + GEMINI_FALLBACK_MODELS
    
    for model in models_to_try:
        try:
            return call_gemini_api(model, prompt)
        except Exception as e:
            log.error(f"Model engine {model} failed processing data: {e}. Transitioning to fallback...")
            time.sleep(5.0)  # Quick cooldown buffer before hammering next endpoint
            
    raise SystemError("Critical Failure: All primary and fallback Gemini engines failed.")


# =============================================================================
# MAIN PIPELINE EXECUTION
# =============================================================================
def main():
    # Enforce safe destination folder presence
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    if not INPUT_FILE.exists():
        log.error(f"Execution terminated: Input dataset file not found at path '{INPUT_FILE}'.")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        news_data = json.load(f)

    if not isinstance(news_data, list):
        log.error("Execution terminated: Target input data must be structured as a JSON array.")
        return

    total_items = len(news_data)
    log.info(f"Loaded {total_items} total news items. Processing everything in a SINGLE request hit...")

    # Specialized global analytics prompt context construction
    prompt = (
        "You are an expert financial analyst. Analyze all of the provided news items and stock price "
        "movements listed below. Return a valid JSON array of objects containing your complete analysis. "
        "Do not truncate, omit, or skip items from the dataset. Each object in the array must contain "
        "exactly these fields:\n"
        "- 'headline': The exact matching title text analyzed.\n"
        "- 'sentiment': State 'Bullish', 'Bearish', or 'Neutral'.\n"
        "- 'impact_score': An integer value ranging from 1 to 10.\n"
        "- 'summary': A concise sentence outlining the financial rationale.\n\n"
        f"Data to analyze:\n{json.dumps(news_data, indent=2)}"
    )

    try:
        # Request analytical payload generation
        raw_json_str = generate_analysis_with_fallback(prompt)
        
        # Parse structural JSON output layers safely
        parsed_data = json.loads(raw_json_str)
        if isinstance(parsed_data, dict) and "analysis" in parsed_data:
            final_analyses = parsed_data["analysis"]
        elif isinstance(parsed_data, list):
            final_analyses = parsed_data
        else:
            raise ValueError("Parsed JSON payload does not match expected sequence array configurations.")

        # Duplicate extraction filtering and sequence preservation step
        seen_headlines = set()
        clean_analyses = []
        
        for entry in final_analyses:
            headline = entry.get("headline", "").strip()
            if headline and headline not in seen_headlines:
                seen_headlines.add(headline)
                clean_analyses.append(entry)

        # Output final clean output analysis data structures
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(clean_analyses, f, indent=2, ensure_ascii=False)
            
        log.info(f"Pipeline Complete: {len(clean_analyses)} items successfully processed and written to '{OUTPUT_FILE}'.")

    except Exception as e:
        log.critical(f"Pipeline breakdown on single-hit compilation or processing: {e}")


if __name__ == "__main__":
    main()
