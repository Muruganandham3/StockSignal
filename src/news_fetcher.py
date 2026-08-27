"""
news_fetcher.py

Fetches stock news from multiple sources:
- NSE/BSE announcements
- MoneyControl RSS
- Yahoo Finance RSS
- Economic Times RSS
- Business Standard RSS

Also fetches current price data from Yahoo Finance.

Run by GitHub Actions at 07:30 IST every weekday.

WATCHLIST behaviour:
    - Empty dict -> BROAD mode
        Fetch high-impact news for all NSE stocks.
    - Non-empty dict -> FOCUSED mode
        Fetch news related to only the configured stocks.
"""

import os
import json
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup


# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
)

log = logging.getLogger(__name__)


# =============================================================================
# TIMEZONE
# =============================================================================

IST = timezone(timedelta(hours=5, minutes=30))


# =============================================================================
# WATCHLIST
# =============================================================================

# Leave empty for BROAD mode.
#
# Example:
#
# WATCHLIST = {
#     "RELIANCE": "Reliance Industries",
#     "TCS": "Tata Consultancy Services",
#     "INFY": "Infosys",
#     "HDFCBANK": "HDFC Bank",
# }

WATCHLIST: dict[str, str] = {
    # "RELIANCE": "Reliance Industries",
    # "TCS": "Tata Consultancy Services",
    # "INFY": "Infosys",
    # "HDFCBANK": "HDFC Bank",
    # "WIPRO": "Wipro",
    # "BAJFINANCE": "Bajaj Finance",
    # "TATAMOTORS": "Tata Motors",
    # "ADANIENT": "Adani Enterprises",
}

WATCHLIST_MODE = bool(WATCHLIST)


# =============================================================================
# OUTPUT
# =============================================================================

OUTPUT_FILE = Path("data/news_raw.json")


# =============================================================================
# RSS FEEDS
# =============================================================================

RSS_FEEDS = [
    "https://economictimes.indiatimes.com/markets/stocks/rss.cms",
    "https://www.business-standard.com/rss/markets-106.rss",
    "https://feeds.moneycontrol.com/mc/stockmarket/marketnews",
    "https://in.finance.yahoo.com/rss/topstories",
]


# =============================================================================
# NSE API
# =============================================================================

NSE_ANNOUNCEMENTS_URL = (
    "https://www.nseindia.com/api/corporate-announcements"
    "?index=equities&from_date={from_dt}&to_date={to_dt}"
)

NSE_HOME_URL = "https://www.nseindia.com/"

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}


# =============================================================================
# KEYWORDS
# =============================================================================

HIGH_IMPACT_KEYWORDS = [
    # Results / earnings
    "quarterly result",
    "q1",
    "q2",
    "q3",
    "q4",
    "annual result",
    "profit",
    "revenue",
    "ebitda",
    "earnings",
    "eps",
    "net profit",
    "financial results",

    # Orders / business
    "new order",
    "order win",
    "order book",
    "large order",
    "contract",
    "government contract",
    "tender",
    "award",

    # Deals
    "bulk deal",
    "block deal",
    "insider buying",
    "insider selling",
    "promoter buying",
    "promoter selling",

    # Corporate actions
    "dividend",
    "bonus",
    "split",
    "buyback",
    "rights issue",

    # M&A
    "merger",
    "acquisition",
    "stake",
    "joint venture",

    # Guidance
    "guidance",
    "outlook",
    "forecast",

    # Fund raising / debt
    "fund raising",
    "fundraise",
    "fund raising",
    "qip",
    "preferential issue",
    "rights issue",
    "debenture",
    "bond",
    "debt",
]


# =============================================================================
# HELPERS
# =============================================================================

def _slug(text: str) -> str:
    """
    Stable hash for deduplication.
    """
    return hashlib.md5(
        text.encode("utf-8")
    ).hexdigest()[:12]


def _clean_text(value) -> str:
    """
    Safely convert any value to clean text.
    """
    if value is None:
        return ""

    return str(value).strip()


def _is_relevant(text: str) -> bool:
    """
    Determine whether a news item is high-impact.

    BROAD mode:
        Any high-impact keyword is enough.

    FOCUSED mode:
        A watchlist symbol/company name OR high-impact keyword
        is enough.
    """

    t = _clean_text(text).lower()

    has_keyword = any(
        keyword.lower() in t
        for keyword in HIGH_IMPACT_KEYWORDS
    )

    if not WATCHLIST_MODE:
        # BROAD MODE
        return has_keyword

    # FOCUSED MODE
    mentions_symbol = any(
        sym.lower() in t or name.lower() in t
        for sym, name in WATCHLIST.items()
    )

    return mentions_symbol or has_keyword


def _mentions_watchlist(text: str) -> list[str]:
    """
    Return matched watchlist symbols.

    In BROAD mode this returns an empty list because there is
    no watchlist filter.
    """

    if not WATCHLIST_MODE:
        return []

    t = _clean_text(text).lower()

    return [
        sym
        for sym, name in WATCHLIST.items()
        if sym.lower() in t or name.lower() in t
    ]


def _extract_nse_symbol(text: str) -> str:
    """
    Extract an NSE symbol from a title such as:

        [RELIANCE] Board Meeting

    Returns:
        RELIANCE

    or:
        ""
    """

    text = _clean_text(text)

    if text.startswith("[") and "]" in text:
        return text[
            1:text.index("]")
        ].strip().upper()

    return ""


# =============================================================================
# RSS FETCHER
# =============================================================================

def fetch_rss_news() -> list[dict]:
    """
    Fetch relevant news from RSS feeds.
    """

    items = []

    for url in RSS_FEEDS:

        try:
            log.info("Fetching RSS: %s", url)

            feed = feedparser.parse(url)

            # feedparser doesn't always throw exceptions for bad feeds.
            if getattr(feed, "bozo", False):
                log.warning(
                    "RSS feed warning for %s: %s",
                    url,
                    getattr(feed, "bozo_exception", "unknown error"),
                )

            feed_title = getattr(
                feed.feed,
                "title",
                url,
            )

            for entry in feed.entries[:50]:

                title = _clean_text(
                    entry.get("title", "")
                )

                summary = _clean_text(
                    entry.get("summary", "")
                )

                combined = f"{title} {summary}"

                if not _is_relevant(combined):
                    continue

                clean_summary = BeautifulSoup(
                    summary,
                    "html.parser",
                ).get_text(
                    separator=" ",
                    strip=True,
                )

                # Try to identify watchlist symbols.
                symbols = _mentions_watchlist(
                    combined
                )

                items.append(
                    {
                        "id": _slug(title),
                        "source": feed_title,
                        "title": title,
                        "summary": clean_summary[:500],
                        "url": entry.get("link", ""),
                        "published": entry.get(
                            "published",
                            datetime.now(IST).isoformat(),
                        ),
                        "symbols": symbols,
                        "type": "news",
                    }
                )

        except Exception as exc:

            log.warning(
                "RSS fetch failed for %s: %s",
                url,
                exc,
            )

    log.info(
        "RSS: collected %d relevant items",
        len(items),
    )

    return items


# =============================================================================
# NSE ANNOUNCEMENTS
# =============================================================================

def fetch_nse_announcements() -> list[dict]:
    """
    Fetch NSE corporate announcements.

    IMPORTANT:
    NSE may return either:

        {
            "data": [...]
        }

    or directly:

        [...]

    This function handles both formats.
    """

    today = datetime.now(IST).date()

    yesterday = today - timedelta(days=1)

    url = NSE_ANNOUNCEMENTS_URL.format(
        from_dt=yesterday.strftime("%d-%m-%Y"),
        to_dt=today.strftime("%d-%m-%Y"),
    )

    items = []

    session = requests.Session()

    try:

        log.info(
            "Fetching NSE announcements: %s",
            url,
        )

        # ---------------------------------------------------------------------
        # STEP 1
        # Get NSE homepage first to establish cookies/session.
        # ---------------------------------------------------------------------

        home_response = session.get(
            NSE_HOME_URL,
            headers=NSE_HEADERS,
            timeout=15,
        )

        log.info(
            "NSE homepage status: %s",
            home_response.status_code,
        )

        # ---------------------------------------------------------------------
        # STEP 2
        # Fetch announcements.
        # ---------------------------------------------------------------------

        response = session.get(
            url,
            headers=NSE_HEADERS,
            timeout=20,
        )

        log.info(
            "NSE announcements status: %s",
            response.status_code,
        )

        response.raise_for_status()

        # ---------------------------------------------------------------------
        # STEP 3
        # Parse JSON.
        # ---------------------------------------------------------------------

        try:
            data = response.json()

        except ValueError as exc:
            log.error(
                "NSE returned invalid JSON. Response starts with: %s",
                response.text[:500],
            )
            raise ValueError(
                "NSE response was not valid JSON"
            ) from exc

        # ---------------------------------------------------------------------
        # STEP 4
        # IMPORTANT FIX:
        #
        # NSE can return either:
        #
        #   {"data": [...]}
        #
        # OR:
        #
        #   [...]
        #
        # Your previous code assumed only the first format.
        # ---------------------------------------------------------------------

        if isinstance(data, dict):

            announcements = data.get(
                "data",
                [],
            )

        elif isinstance(data, list):

            announcements = data

        else:

            raise ValueError(
                "Unexpected NSE response type: "
                f"{type(data).__name__}"
            )

        if not isinstance(announcements, list):

            raise ValueError(
                "NSE announcements is not a list: "
                f"{type(announcements).__name__}"
            )

        log.info(
            "NSE returned %d raw announcements",
            len(announcements),
        )

        # ---------------------------------------------------------------------
        # STEP 5
        # Process announcements.
        # ---------------------------------------------------------------------

        for ann in announcements:

            # Defensive check.
            if not isinstance(ann, dict):

                log.warning(
                    "Skipping unexpected NSE announcement: %r",
                    ann,
                )

                continue

            sym = _clean_text(
                ann.get("symbol", "")
            ).upper()

            desc = _clean_text(
                ann.get("desc", "")
            )

            subject = _clean_text(
                ann.get("subject", "")
            )

            combined = (
                f"{sym} "
                f"{subject} "
                f"{desc}"
            )

            # ---------------------------------------------------------------
            # Relevance filtering
            # ---------------------------------------------------------------

            if WATCHLIST_MODE:

                # Focused mode:
                # keep if symbol is in watchlist OR item is otherwise relevant.
                if (
                    sym not in WATCHLIST
                    and not _is_relevant(combined)
                ):
                    continue

            else:

                # Broad mode:
                # keep high-impact announcements only.
                if not _is_relevant(combined):
                    continue

            # ---------------------------------------------------------------
            # Symbol
            # ---------------------------------------------------------------

            symbols = []

            if sym:
                symbols = [sym]

            # ---------------------------------------------------------------
            # Publication time
            # ---------------------------------------------------------------

            published = ann.get(
                "exchdisstime"
            )

            if not published:

                published = datetime.now(
                    IST
                ).isoformat()

            # ---------------------------------------------------------------
            # Create item
            # ---------------------------------------------------------------

            items.append(
                {
                    "id": _slug(combined),
                    "source": "NSE",
                    "title": f"[{sym}] {subject}",
                    "summary": desc[:500],
                    "url": (
                        "https://www.nseindia.com/"
                        "companies-listing/"
                        "corporate-filings-announcements"
                    ),
                    "published": published,
                    "symbols": symbols,
                    "type": classify_announcement(
                        subject
                    ),
                }
            )

    except requests.exceptions.HTTPError as exc:

        status = (
            exc.response.status_code
            if exc.response is not None
            else 0
        )

        log.warning(
            "NSE HTTP error (%s): %s",
            status,
            exc,
        )

        if exc.response is not None:
            log.warning(
                "NSE response: %s",
                exc.response.text[:500],
            )

    except requests.exceptions.RequestException as exc:

        log.warning(
            "NSE request failed: %s",
            exc,
        )

    except Exception as exc:

        log.warning(
            "NSE fetch failed: %s",
            exc,
        )

    log.info(
        "NSE: collected %d relevant announcements",
        len(items),
    )

    return items


# =============================================================================
# YAHOO FINANCE
# =============================================================================

def fetch_yahoo_finance(symbol: str) -> dict | None:
    """
    Fetch price information for an NSE stock using Yahoo Finance.

    NSE symbols are represented as:

        TCS.NS
        RELIANCE.NS
        INFY.NS
    """

    symbol = _clean_text(symbol).upper()

    if not symbol:
        return None

    ticker = f"{symbol}.NS"

    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{ticker}"
        "?interval=1d&range=5d"
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
        )
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            timeout=15,
        )

        response.raise_for_status()

        payload = response.json()

        chart = payload.get(
            "chart",
            {},
        )

        results = chart.get(
            "result"
        )

        if not results:

            log.warning(
                "Yahoo returned no result for %s",
                symbol,
            )

            return None

        chart_data = results[0]

        meta = chart_data.get(
            "meta",
            {},
        )

        indicators = chart_data.get(
            "indicators",
            {},
        )

        quote_list = indicators.get(
            "quote",
            [],
        )

        if not quote_list:

            log.warning(
                "Yahoo returned no quote data for %s",
                symbol,
            )

            return None

        closes = quote_list[0].get(
            "close",
            [],
        )

        closes = [
            value
            for value in closes
            if value is not None
        ]

        prev_close = (
            closes[-2]
            if len(closes) >= 2
            else None
        )

        curr_close = (
            closes[-1]
            if closes
            else None
        )

        change_pct = None

        if (
            prev_close is not None
            and curr_close is not None
            and prev_close != 0
        ):

            change_pct = round(
                (
                    (curr_close - prev_close)
                    / prev_close
                ) * 100,
                2,
            )

        return {
            "symbol": symbol,
            "price": curr_close,
            "prev_close": prev_close,
            "change_pct": change_pct,
            "52w_high": meta.get(
                "fiftyTwoWeekHigh"
            ),
            "52w_low": meta.get(
                "fiftyTwoWeekLow"
            ),
            "currency": meta.get(
                "currency",
                "INR",
            ),
        }

    except requests.exceptions.RequestException as exc:

        log.warning(
            "Yahoo request failed for %s: %s",
            symbol,
            exc,
        )

    except (KeyError, IndexError, TypeError, ValueError) as exc:

        log.warning(
            "Yahoo data parsing failed for %s: %s",
            symbol,
            exc,
        )

    except Exception as exc:

        log.warning(
            "Yahoo fetch failed for %s: %s",
            symbol,
            exc,
        )

    return None


# =============================================================================
# ANNOUNCEMENT CLASSIFICATION
# =============================================================================

def classify_announcement(subject: str) -> str:
    """
    Classify NSE announcement into a category.
    """

    s = _clean_text(subject).lower()

    if any(
        keyword in s
        for keyword in [
            "result",
            "financial",
            "earnings",
            "profit",
            "revenue",
        ]
    ):
        return "quarterly_result"

    if any(
        keyword in s
        for keyword in [
            "order",
            "contract",
            "tender",
            "award",
        ]
    ):
        return "new_order"

    if any(
        keyword in s
        for keyword in [
            "bulk",
            "block",
            "insider",
        ]
    ):
        return "bulk_deal"

    if any(
        keyword in s
        for keyword in [
            "dividend",
            "bonus",
            "split",
            "buyback",
        ]
    ):
        return "corporate_action"

    if any(
        keyword in s
        for keyword in [
            "merger",
            "acqui",
            "stake",
        ]
    ):
        return "ma_event"

    return "general"


# =============================================================================
# MAIN
# =============================================================================

def main():

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    seen_ids: set[str] = set()

    all_items: list[dict] = []

    # -------------------------------------------------------------------------
    # Mode
    # -------------------------------------------------------------------------

    mode_label = (
        "FOCUSED (watchlist)"
        if WATCHLIST_MODE
        else "BROAD (all NSE stocks)"
    )

    log.info(
        "Running in %s mode",
        mode_label,
    )

    # -------------------------------------------------------------------------
    # Fetch NSE + RSS
    # -------------------------------------------------------------------------

    nse_items = fetch_nse_announcements()

    rss_items = fetch_rss_news()

    log.info(
        "Fetched %d NSE items + %d RSS items",
        len(nse_items),
        len(rss_items),
    )

    # -------------------------------------------------------------------------
    # Deduplicate
    # -------------------------------------------------------------------------

    for item in nse_items + rss_items:

        item_id = item.get(
            "id"
        )

        if not item_id:
            continue

        if item_id in seen_ids:
            continue

        seen_ids.add(item_id)

        all_items.append(item)

    log.info(
        "Total unique news items: %d",
        len(all_items),
    )

    # -------------------------------------------------------------------------
    # Decide symbols for price fetching
    # -------------------------------------------------------------------------

    if WATCHLIST_MODE:

        # FOCUSED MODE:
        # Always fetch every watchlist symbol.

        price_symbols = set(
            WATCHLIST.keys()
        )

        log.info(
            "Focused mode: fetching prices for %d watchlist symbols",
            len(price_symbols),
        )

    else:

        # BROAD MODE:
        #
        # Fetch prices for symbols appearing in today's news.
        #
        # Maximum 50 symbols to avoid hammering Yahoo Finance.

        price_symbols = set()

        for item in all_items:

            # ---------------------------------------------------------------
            # First: symbols explicitly provided by the news item.
            # ---------------------------------------------------------------

            symbols = item.get(
                "symbols",
                []
            )

            if isinstance(symbols, list):

                for symbol in symbols:

                    if symbol:
                        price_symbols.add(
                            str(symbol).strip().upper()
                        )

            # ---------------------------------------------------------------
            # Second: extract [SYM] from NSE title.
            # ---------------------------------------------------------------

            title = _clean_text(
                item.get("title", "")
            )

            nse_symbol = _extract_nse_symbol(
                title
            )

            if nse_symbol:

                price_symbols.add(
                    nse_symbol
                )

        # ---------------------------------------------------------------------
        # Cap at 50 symbols.
        # ---------------------------------------------------------------------

        price_symbols = set(
            list(price_symbols)[:50]
        )

        log.info(
            "Broad mode: fetching prices for %d symbols from news",
            len(price_symbols),
        )

    # -------------------------------------------------------------------------
    # Fetch Yahoo prices
    # -------------------------------------------------------------------------

    price_data: dict[str, dict] = {}

    for symbol in sorted(price_symbols):

        data = fetch_yahoo_finance(
            symbol
        )

        if data:

            price_data[symbol] = data

    # -------------------------------------------------------------------------
    # Output
    # -------------------------------------------------------------------------

    output = {
        "generated_at": datetime.now(
            IST
        ).isoformat(),

        "mode": mode_label,

        "news_count": len(
            all_items
        ),

        "news": all_items,

        "prices": price_data,
    }

    OUTPUT_FILE.write_text(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    log.info(
        "Saved %d news items + %d price records",
        len(all_items),
        len(price_data),
    )


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    main()