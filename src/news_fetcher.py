"""
news_fetcher.py  (v2)

Fetches stock news / events from:
  - NSE corporate announcements (results, board outcomes, orders, M&A ...)
  - NSE bulk + block deals
  - Economic Times, Business Standard, MoneyControl, Yahoo Finance (RSS)
  - Inc42 (RSS)
  - MarketScreener and extra Economic Times queries (via Google News RSS)

Also fetches current price data from Yahoo Finance.

Run by GitHub Actions at 07:30 IST every weekday.

WATCHLIST behaviour:
    - Empty dict     -> BROAD mode   (all NSE stocks, high-impact news only)
    - Non-empty dict -> FOCUSED mode (only the configured stocks + keywords)

What changed vs v1
------------------
1. NSE parsing: uses `desc` + `attchmntText` + `sm_name` (v1 read a `subject`
   field that NSE does not send, so every item was typed "general" and many
   results announcements were filtered out).
2. Keyword matching uses word boundaries (no more "eps" matching "steps").
3. Company-name -> symbol resolution for RSS items in BROAD mode, using the
   NSE equity list, so news items carry a symbol.
4. New sources: NSE bulk/block deals, Inc42, MarketScreener, extra ET feed.
5. RSS fetched with requests (UA + timeout) and old items are dropped.
6. Items are ranked (results > deals > orders > ...) BEFORE any truncation.
7. Per-source stats written to the output JSON so you can see exactly which
   source returned what (and which one failed) without digging in logs.
"""

import calendar
import csv
import hashlib
import io
import json
import logging
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup


# =============================================================================
# LOGGING / TIMEZONE
# =============================================================================

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


# =============================================================================
# CONFIG
# =============================================================================

# Leave empty for BROAD mode.
WATCHLIST: dict[str, str] = {
    # "RELIANCE": "Reliance Industries",
    # "TCS": "Tata Consultancy Services",
}

WATCHLIST_MODE = bool(WATCHLIST)

OUTPUT_FILE = Path("data/news_raw.json")

MAX_AGE_HOURS = 36           # drop RSS items older than this
MAX_AGE_HOURS_MONDAY = 84    # Monday run must cover the weekend
MIN_DEAL_VALUE_INR = 1_00_00_000   # ignore bulk/block deals below Rs 1 crore
MAX_DEALS = 40               # keep the biggest N deals
MAX_PRICE_SYMBOLS = 60       # Yahoo lookups in BROAD mode
MAX_ITEMS_OUT = 150          # hard cap AFTER ranking
REQUIRE_SYMBOL_FOR_RSS = False  # True = drop every RSS item with no resolvable company
UNRESOLVED_RSS_MAX = 30         # BROAD mode: max symbol-less RSS items kept (event-type only)


# =============================================================================
# SOURCES
# =============================================================================

def _gnews(query: str) -> str:
    """Google News RSS search, last 24h, India edition."""
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(f"{query} when:1d")
        + "&hl=en-IN&gl=IN&ceid=IN:en"
    )


# Failing feeds are only logged; they never stop the run.
RSS_FEEDS = [
    {"name": "Economic Times",
     "url": "https://economictimes.indiatimes.com/markets/stocks/rss.cms"},
    {"name": "Economic Times (results/orders)",
     "url": _gnews('site:economictimes.indiatimes.com '
                   '(results OR order OR stake OR "bulk deal" OR dividend)')},
    {"name": "Business Standard",
     "url": "https://www.business-standard.com/rss/markets-106.rss"},
    # Old feeds.moneycontrol.com URL returned 404 and the Yahoo India feed
    # redirects to a search page (HTTP 500) - both removed.
    # The MoneyControl URLs below are the classic /rss/ ones; not verified live.
    {"name": "MoneyControl Market Reports",
     "url": "https://www.moneycontrol.com/rss/marketreports.xml"},
    {"name": "MoneyControl Results",
     "url": "https://www.moneycontrol.com/rss/results.xml"},
    {"name": "MoneyControl Buzzing Stocks",
     "url": "https://www.moneycontrol.com/rss/buzzingstocks.xml"},
    {"name": "Google News (India stocks)",
     "url": _gnews('NSE stock (results OR "order win" OR "bulk deal" OR upgrade OR downgrade)')},
    {"name": "Inc42",
     "url": "https://inc42.com/feed/"},
    {"name": "MarketScreener",
     "url": _gnews("site:marketscreener.com India")},
]

RSS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml,application/xml,text/xml,*/*",
}

NSE_HOME_URL = "https://www.nseindia.com/"
NSE_ANNOUNCEMENTS_URL = (
    "https://www.nseindia.com/api/corporate-announcements"
    "?index=equities&from_date={from_dt}&to_date={to_dt}"
)
NSE_LARGE_DEALS_URL = "https://www.nseindia.com/api/snapshot-capital-market-largedeal"
NSE_BULK_CSV = "https://nsearchives.nseindia.com/content/equities/bulk.csv"
NSE_BLOCK_CSV = "https://nsearchives.nseindia.com/content/equities/block.csv"
NSE_EQUITY_LIST_CSV = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

NSE_HEADERS = {
    "User-Agent": RSS_HEADERS["User-Agent"],
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

# NSE announcement categories (`desc`) we always keep, even if the
# category text itself has no keyword (e.g. "Outcome of Board Meeting").
ALWAYS_KEEP_CATEGORIES = (
    "financial result",
    "integrated filing",
    "outcome of board meeting",
    "acquisition",
    "bagging",
    "receiving of orders",
    "buyback",
    "dividend",
    "bonus",
    "fund raising",
    "preferential",
    "amalgamation",
    "scheme of arrangement",
)

# Known brand names that are not in the NSE legal-name list.
# Verify the symbols once; a wrong symbol only causes a failed Yahoo lookup.
EXTRA_ALIASES = {
    "zomato": "ETERNAL",
    "eternal": "ETERNAL",
    "paytm": "PAYTM",
    "nykaa": "NYKAA",
    "policybazaar": "POLICYBZR",
    "swiggy": "SWIGGY",
    "delhivery": "DELHIVERY",
    "ola electric": "OLAELEC",
    "wipro": "WIPRO",
    "tcs": "TCS",
}


# =============================================================================
# KEYWORDS / CLASSIFICATION
# =============================================================================

HIGH_IMPACT_KEYWORDS = [
    # Results / earnings
    "quarterly result", "annual result", "financial result", "result",
    "q1", "q2", "q3", "q4", "profit", "net profit", "revenue", "ebitda",
    "earnings", "eps", "board meeting", "integrated filing",
    # Orders / business
    "new order", "order win", "order book", "large order", "order",
    "contract", "government contract", "tender", "award", "bags", "bagged",
    "secures", "wins",
    # Deals
    "bulk deal", "block deal", "insider buying", "insider selling",
    "promoter buying", "promoter selling", "stake sale", "open offer",
    # Corporate actions
    "dividend", "bonus", "split", "buyback", "rights issue",
    # M&A
    "merger", "acquisition", "acquires", "stake", "joint venture",
    # Guidance / analysts
    "guidance", "outlook", "forecast", "upgrade", "downgrade",
    "target price", "price target", "fii", "dii",
    # Fund raising / debt
    "fund raising", "fundraise", "qip", "preferential issue",
    "debenture", "bond", "debt",
]

_KW_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(k) for k in sorted(set(HIGH_IMPACT_KEYWORDS), key=len, reverse=True))
    + r")s?\b",
    re.IGNORECASE,
)

# Order matters: first match wins.
_CLASS_RULES = [
    ("analyst_call", r"\b(upgrade[sd]?|downgrade[sd]?|target price|price target|initiates coverage)\b"),
    ("block_deal", r"\bblock deals?\b"),
    ("bulk_deal", r"\b(bulk deals?|insider|promoter (?:buying|selling))\b"),
    ("quarterly_result", r"\b(results?|financials?|earnings|net profit|profit|revenue|q[1-4])\b"),
    ("new_order", r"\b(orders?|contracts?|tenders?|awards?|bagg\w+|secures?|wins?)\b"),
    ("corporate_action", r"\b(dividend|bonus|split|buyback|rights issue)\b"),
    ("ma_event", r"\b(merger|acqui\w+|stake|joint venture|open offer|amalgamation)\b"),
    ("fund_raise", r"\b(qip|fund ?rais\w+|preferential|debentures?|ncds?)\b"),
]
_CLASS_RULES = [(label, re.compile(rx, re.IGNORECASE)) for label, rx in _CLASS_RULES]

PRIORITY = {
    "quarterly_result": 100,
    "bulk_deal": 90,
    "block_deal": 90,
    "new_order": 80,
    "ma_event": 80,
    "corporate_action": 70,
    "analyst_call": 65,
    "fund_raise": 60,
    "news": 40,
    "general": 30,
}


def classify_announcement(text: str) -> str:
    """Classify text (category + detail / headline) into an event type."""
    s = _clean_text(text)
    for label, rx in _CLASS_RULES:
        if rx.search(s):
            return label
    return "general"


# =============================================================================
# HELPERS
# =============================================================================

SOURCE_STATS: dict[str, dict] = {}


def _stat(name: str, **kwargs) -> dict:
    SOURCE_STATS.setdefault(name, {}).update(kwargs)
    return SOURCE_STATS[name]


def _slug(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


def _clean_text(value) -> str:
    return "" if value is None else str(value).strip()


def _to_float(value) -> float:
    try:
        return float(str(value).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _norm(text: str) -> str:
    t = _clean_text(text).lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


_WATCH_PATTERNS = {
    sym: re.compile(
        r"\b(?:%s|%s)\b" % (re.escape(sym), re.escape(name)), re.IGNORECASE
    )
    for sym, name in WATCHLIST.items()
}


def _mentions_watchlist(text: str) -> list[str]:
    if not WATCHLIST_MODE:
        return []
    t = _clean_text(text)
    return [sym for sym, rx in _WATCH_PATTERNS.items() if rx.search(t)]


def _is_relevant(text: str) -> bool:
    """BROAD: keyword needed. FOCUSED: watchlist mention OR keyword."""
    t = _clean_text(text)
    has_keyword = bool(_KW_RE.search(t))
    if not WATCHLIST_MODE:
        return has_keyword
    return has_keyword or bool(_mentions_watchlist(t))


def _extract_nse_symbol(text: str) -> str:
    """'[RELIANCE] Board Meeting' -> 'RELIANCE'."""
    text = _clean_text(text)
    if text.startswith("[") and "]" in text:
        return text[1:text.index("]")].strip().upper()
    return ""


def _pick(row: dict, *keys: str) -> str:
    """Case/space-insensitive lookup of the first present key."""
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for key in keys:
        val = lowered.get(key.strip().lower())
        if val not in (None, ""):
            return _clean_text(val)
    return ""


# ----- company name -> symbol (BROAD mode RSS) -------------------------------

_NSE_SYMBOL_RE = re.compile(r"\b(?:NSE|BSE)\s*[:\-]\s*([A-Z0-9&\-]{2,15})\b")

# Filled by load_company_names(); lets short names (ITC, CIPLA ...) match as ticker tokens.
_SYMBOLS: set[str] = set()
_SYMBOL_STOPWORDS = {
    "NSE", "BSE", "FII", "DII", "IPO", "GDP", "CEO", "CFO", "EPS", "QIP", "SEBI", "RBI",
    "NIFTY", "SENSEX", "USD", "INR", "YOY", "QOQ", "ETF", "THE", "AND", "FOR", "NEW",
    "ALL", "BUY", "SELL", "TOP", "CAN", "ONE", "NOW", "MAY", "WIN", "GET", "BIG", "LOW",
    "HIGH", "INDIA", "LTD", "LIMITED", "PSU", "AGM", "EGM", "MD", "NCD",
}


def _match_companies(text: str, name_map: dict[str, str]) -> list[str]:
    padded = f" {_norm(text)} "
    found: list[str] = []
    for name, sym in name_map.items():
        if f" {name} " in padded and sym not in found:
            found.append(sym)
        if len(found) >= 5:
            break
    raw = _clean_text(text)
    for m in _NSE_SYMBOL_RE.finditer(raw):
        sym = m.group(1).upper()
        if sym not in found:
            found.append(sym)
    if _SYMBOLS and not raw.isupper():          # skip ALL-CAPS headlines (too many false hits)
        for tok in re.findall(r"\b[A-Z][A-Z&\-]{2,14}\b", raw):
            if tok in _SYMBOLS and tok not in _SYMBOL_STOPWORDS and tok not in found:
                found.append(tok)
    return found[:5]


def _symbols_for_text(text: str, name_map: dict[str, str]) -> list[str]:
    if WATCHLIST_MODE:
        return _mentions_watchlist(text)
    return _match_companies(text, name_map)


# =============================================================================
# NSE SESSION
# =============================================================================

def _nse_session() -> requests.Session:
    """Session with cookies from the NSE home page (required by NSE APIs)."""
    s = requests.Session()
    s.headers.update(NSE_HEADERS)
    try:
        r = s.get(NSE_HOME_URL, timeout=15)
        log.info("NSE homepage status: %s", r.status_code)
    except requests.RequestException as exc:
        log.warning("NSE homepage failed: %s", exc)
    return s


def _nse_get_json(session: requests.Session, url: str, retries: int = 3):
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=20)
            log.info("NSE GET %s -> %s", url.split("?")[0], resp.status_code)
            if resp.status_code in (401, 403):
                last_exc = requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                session.get(NSE_HOME_URL, timeout=15)   # refresh cookies
                time.sleep(2 * attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            time.sleep(2 * attempt)
    raise last_exc  # type: ignore[misc]


def load_company_names(session: requests.Session) -> dict[str, str]:
    """normalised company name -> NSE symbol (from NSE's equity list)."""
    names: dict[str, str] = dict(EXTRA_ALIASES)
    try:
        resp = session.get(NSE_EQUITY_LIST_CSV, timeout=20)
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            sym = _pick(row, "SYMBOL").upper()
            name = _norm(_pick(row, "NAME OF COMPANY"))
            for suffix in (" limited", " ltd"):
                if name.endswith(suffix):
                    name = name[: -len(suffix)].strip()
            if sym:
                _SYMBOLS.add(sym)
            if sym and len(name) >= 6:       # skip very short/generic names
                names.setdefault(name, sym)
            if sym and len(sym) >= 5 and sym not in _SYMBOL_STOPWORDS:
                names.setdefault(sym.lower(), sym)   # "Cipla", "Titan", "Wipro" ...
        _stat("NSE equity list", companies=len(names))
        log.info("Loaded %d company names", len(names))
    except Exception as exc:
        _stat("NSE equity list", error=str(exc)[:200])
        log.warning("Could not load NSE equity list (%s). "
                    "RSS items will not be symbol-matched.", exc)
    return names


# =============================================================================
# NSE ANNOUNCEMENTS
# =============================================================================

def fetch_nse_announcements(session: requests.Session) -> list[dict]:
    """
    NSE fields used: symbol, sm_name, desc (category), attchmntText (detail),
    exchdisstime / an_dt (time).
    """
    today = datetime.now(IST).date()
    from_date = today - timedelta(days=1)
    url = NSE_ANNOUNCEMENTS_URL.format(
        from_dt=from_date.strftime("%d-%m-%Y"),
        to_dt=today.strftime("%d-%m-%Y"),
    )
    items: list[dict] = []
    stat = _stat("NSE announcements", raw=0, kept=0, error=None)

    try:
        data = _nse_get_json(session, url)
        announcements = data.get("data", []) if isinstance(data, dict) else data
        if not isinstance(announcements, list):
            raise ValueError(f"Unexpected NSE payload: {type(announcements).__name__}")

        stat["raw"] = len(announcements)
        log.info("NSE returned %d raw announcements", len(announcements))
        if announcements and isinstance(announcements[0], dict):
            log.info("NSE record keys: %s", sorted(announcements[0].keys()))

        for ann in announcements:
            if not isinstance(ann, dict):
                continue

            sym = _clean_text(ann.get("symbol")).upper()
            company = _clean_text(ann.get("sm_name"))
            category = _clean_text(ann.get("desc"))
            detail = _clean_text(ann.get("attchmntText")) or _clean_text(ann.get("subject"))
            combined = f"{sym} {company} {category} {detail}"

            keep_category = any(c in category.lower() for c in ALWAYS_KEEP_CATEGORIES)
            if WATCHLIST_MODE:
                keep = sym in WATCHLIST or keep_category or _is_relevant(combined)
            else:
                keep = keep_category or _is_relevant(combined)
            if not keep:
                continue

            etype = classify_announcement(f"{category} {detail}")
            published = (
                _clean_text(ann.get("exchdisstime"))
                or _clean_text(ann.get("an_dt"))
                or datetime.now(IST).isoformat()
            )

            items.append({
                "id": _slug(f"nse|{sym}|{category}|{detail[:200]}|{published}"),
                "source": "NSE",
                "title": f"[{sym}] {category}".strip(),
                "company": company,
                "summary": (detail or category)[:500],
                "url": _clean_text(ann.get("attchmntFile"))
                       or "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
                "published": published,
                "symbols": [sym] if sym else [],
                "type": etype,
            })

    except Exception as exc:
        stat["error"] = str(exc)[:200]
        log.warning("NSE announcements failed: %s", exc)

    stat["kept"] = len(items)
    log.info("NSE: collected %d relevant announcements", len(items))
    return items


# =============================================================================
# NSE BULK / BLOCK DEALS
# =============================================================================

def _rows_from_csv(session: requests.Session, url: str) -> list[dict]:
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    return list(csv.DictReader(io.StringIO(resp.text)))


def fetch_nse_deals(session: requests.Session) -> list[dict]:
    """
    Latest bulk + block deals (data is for the last trading session).
    Primary: NSE JSON snapshot. Fallback: NSE archive CSVs.
    """
    stat = _stat("NSE bulk/block deals", raw=0, kept=0, error=None, via="json")
    raw_rows: list[tuple[str, dict]] = []

    try:
        data = _nse_get_json(session, NSE_LARGE_DEALS_URL)
        if isinstance(data, dict):
            for key, kind in (("BULK_DEALS_DATA", "bulk"), ("BLOCK_DEALS_DATA", "block")):
                for row in data.get(key) or []:
                    if isinstance(row, dict):
                        raw_rows.append((kind, row))
    except Exception as exc:
        stat["error"] = f"json: {str(exc)[:150]}"
        log.warning("NSE large-deal JSON failed: %s", exc)

    if not raw_rows:
        stat["via"] = "csv"
        for url, kind in ((NSE_BULK_CSV, "bulk"), (NSE_BLOCK_CSV, "block")):
            try:
                for row in _rows_from_csv(session, url):
                    raw_rows.append((kind, row))
            except Exception as exc:
                prev = stat.get("error") or ""
                stat["error"] = f"{prev} | csv {kind}: {str(exc)[:100]}".strip(" |")
                log.warning("NSE %s deal CSV failed: %s", kind, exc)

    stat["raw"] = len(raw_rows)
    if raw_rows:
        log.info("Deal record keys: %s", sorted(raw_rows[0][1].keys()))

    deals: list[dict] = []
    for kind, row in raw_rows:
        sym = _pick(row, "symbol").upper()
        if not sym:
            continue
        if WATCHLIST_MODE and sym not in WATCHLIST:
            continue

        qty = _to_float(_pick(row, "qty", "Quantity Traded"))
        price = _to_float(_pick(row, "watp", "Trade Price / Wght. Avg. Price"))
        value = qty * price
        if value < MIN_DEAL_VALUE_INR:
            continue

        side = _pick(row, "buySell", "Buy/Sell").upper() or "?"
        client = _pick(row, "clientName", "Client Name")
        name = _pick(row, "name", "Security Name")
        date = _pick(row, "date", "Date") or datetime.now(IST).date().isoformat()
        remarks = _pick(row, "remarks", "Remarks")

        deals.append({
            "value": value,
            "item": {
                "id": _slug(f"{kind}|{date}|{sym}|{client}|{side}|{qty}|{price}"),
                "source": "NSE Bulk/Block Deals",
                "title": f"[{sym}] {kind.title()} deal: {client} {side} {int(qty):,} shares @ Rs {price:,.2f}",
                "company": name,
                "summary": (
                    f"{client} {side} {int(qty):,} shares of {name or sym} at Rs {price:,.2f} "
                    f"(about Rs {value / 1e7:,.1f} crore) on {date}."
                    + (f" Remarks: {remarks}" if remarks and remarks != "-" else "")
                )[:500],
                "url": "https://www.nseindia.com/report-detail/display-bulk-and-block-deals",
                "published": date,
                "symbols": [sym],
                "type": f"{kind}_deal",
                "deal": {"side": side, "qty": qty, "price": price,
                         "value_inr": round(value), "client": client},
            },
        })

    deals.sort(key=lambda d: d["value"], reverse=True)
    items = [d["item"] for d in deals[:MAX_DEALS]]
    stat["kept"] = len(items)
    log.info("NSE deals: collected %d deals above Rs %.1f crore",
             len(items), MIN_DEAL_VALUE_INR / 1e7)
    return items


# =============================================================================
# RSS FETCHER
# =============================================================================

def _entry_time(entry) -> datetime | None:
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if st:
        return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
    return None


def fetch_rss_news(name_map: dict[str, str]) -> list[dict]:
    items: list[dict] = []
    now = datetime.now(timezone.utc)
    max_age = MAX_AGE_HOURS_MONDAY if datetime.now(IST).weekday() == 0 else MAX_AGE_HOURS
    cutoff = now - timedelta(hours=max_age)
    require_symbol = (not WATCHLIST_MODE) and REQUIRE_SYMBOL_FOR_RSS and bool(name_map)
    unresolved = 0   # symbol-less items kept so far (shared across feeds)

    for cfg in RSS_FEEDS:
        name, url = cfg["name"], cfg["url"]
        stat = _stat(name, entries=0, stale=0, irrelevant=0, no_symbol=0, kept=0, error=None)
        try:
            log.info("Fetching RSS: %s", name)
            resp = requests.get(url, headers=RSS_HEADERS, timeout=20)
            stat["http_status"] = resp.status_code
            resp.raise_for_status()

            feed = feedparser.parse(resp.content)
            if getattr(feed, "bozo", False):
                log.warning("RSS parse warning for %s: %s", name,
                            getattr(feed, "bozo_exception", "unknown"))

            entries = feed.entries[:60]
            stat["entries"] = len(entries)

            for entry in entries:
                published_dt = _entry_time(entry)
                if published_dt and published_dt < cutoff:
                    stat["stale"] += 1
                    continue

                title = _clean_text(entry.get("title"))
                raw_summary = _clean_text(entry.get("summary"))
                summary = BeautifulSoup(raw_summary, "html.parser").get_text(
                    separator=" ", strip=True
                )
                combined = f"{title} {summary}"

                if not _is_relevant(combined):
                    stat["irrelevant"] += 1
                    continue

                symbols = _symbols_for_text(combined, name_map)
                etype = classify_announcement(combined)
                if not symbols and require_symbol:
                    stat["no_symbol"] += 1
                    continue
                if not symbols and not WATCHLIST_MODE:
                    # Keep unresolved items only if they look like a real event,
                    # up to a cap; the AI step can still read the company name.
                    if etype == "general" or unresolved >= UNRESOLVED_RSS_MAX:
                        stat["no_symbol"] += 1
                        continue
                    unresolved += 1
                    stat["unresolved_kept"] = stat.get("unresolved_kept", 0) + 1
                published = (
                    published_dt.astimezone(IST).isoformat()
                    if published_dt
                    else _clean_text(entry.get("published")) or datetime.now(IST).isoformat()
                )

                items.append({
                    "id": _slug(_norm(title)),
                    "source": name,
                    "title": title,
                    "summary": summary[:500],
                    "url": _clean_text(entry.get("link")),
                    "published": published,
                    "symbols": symbols,
                    "type": "news" if etype == "general" else etype,
                })
                stat["kept"] += 1

        except Exception as exc:
            stat["error"] = str(exc)[:200]
            log.warning("RSS fetch failed for %s: %s", name, exc)

        log.info(
            "RSS %-34s http=%s entries=%s stale=%s irrelevant=%s no_symbol=%s kept=%s%s",
            name, stat.get("http_status"), stat["entries"], stat["stale"],
            stat["irrelevant"], stat["no_symbol"], stat["kept"],
            f" ERROR={stat['error']}" if stat["error"] else "",
        )

    log.info("RSS: collected %d relevant items", len(items))
    return items


# =============================================================================
# YAHOO FINANCE
# =============================================================================

def fetch_yahoo_finance(symbol: str) -> dict | None:
    """Price info for an NSE stock (TCS -> TCS.NS)."""
    symbol = _clean_text(symbol).upper()
    if not symbol:
        return None

    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quote(symbol + '.NS')}?interval=1d&range=5d"   # quote(): M&M.NS etc.
    )

    try:
        response = requests.get(url, headers={"User-Agent": RSS_HEADERS["User-Agent"]}, timeout=15)
        response.raise_for_status()
        results = (response.json().get("chart") or {}).get("result")
        if not results:
            log.warning("Yahoo returned no result for %s", symbol)
            return None

        chart_data = results[0]
        meta = chart_data.get("meta", {})
        quote_list = (chart_data.get("indicators") or {}).get("quote", [])
        if not quote_list:
            log.warning("Yahoo returned no quote data for %s", symbol)
            return None

        closes = [v for v in quote_list[0].get("close", []) if v is not None]
        prev_close = closes[-2] if len(closes) >= 2 else None
        curr_close = closes[-1] if closes else None

        change_pct = None
        if prev_close and curr_close is not None:
            change_pct = round((curr_close - prev_close) / prev_close * 100, 2)

        return {
            "symbol": symbol,
            "price": curr_close,
            "prev_close": prev_close,
            "change_pct": change_pct,
            "52w_high": meta.get("fiftyTwoWeekHigh"),
            "52w_low": meta.get("fiftyTwoWeekLow"),
            "currency": meta.get("currency", "INR"),
        }

    except requests.exceptions.RequestException as exc:
        log.warning("Yahoo request failed for %s: %s", symbol, exc)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        log.warning("Yahoo data parsing failed for %s: %s", symbol, exc)
    except Exception as exc:
        log.warning("Yahoo fetch failed for %s: %s", symbol, exc)
    return None


# =============================================================================
# MAIN
# =============================================================================

def main():
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    mode_label = "FOCUSED (watchlist)" if WATCHLIST_MODE else "BROAD (all NSE stocks)"
    log.info("Running in %s mode", mode_label)

    session = _nse_session()
    name_map = {} if WATCHLIST_MODE else load_company_names(session)

    nse_items = fetch_nse_announcements(session)
    deal_items = fetch_nse_deals(session)
    rss_items = fetch_rss_news(name_map)

    log.info("Fetched %d NSE + %d deals + %d RSS items",
             len(nse_items), len(deal_items), len(rss_items))

    # ---- deduplicate --------------------------------------------------------
    seen: set[str] = set()
    all_items: list[dict] = []
    for item in nse_items + deal_items + rss_items:
        item_id = item.get("id")
        if item_id and item_id not in seen:
            seen.add(item_id)
            all_items.append(item)

    # ---- rank BEFORE truncating --------------------------------------------
    for item in all_items:
        item["priority"] = PRIORITY.get(item.get("type", "general"), 30)
    all_items.sort(key=lambda i: (i["priority"], bool(i.get("symbols"))), reverse=True)

    if len(all_items) > MAX_ITEMS_OUT:
        log.info("Truncating %d -> %d items (ranked by priority)", len(all_items), MAX_ITEMS_OUT)
        all_items = all_items[:MAX_ITEMS_OUT]

    log.info("Total unique news items: %d", len(all_items))

    # ---- symbols for price lookup ------------------------------------------
    if WATCHLIST_MODE:
        price_symbols = list(WATCHLIST.keys())
    else:
        price_symbols = []
        for item in all_items:                      # already priority-ordered
            candidates = list(item.get("symbols") or [])
            nse_sym = _extract_nse_symbol(item.get("title", ""))
            if nse_sym:
                candidates.append(nse_sym)
            for s in candidates:
                s = _clean_text(s).upper()
                if s and s not in price_symbols:
                    price_symbols.append(s)
            if len(price_symbols) >= MAX_PRICE_SYMBOLS:
                break
        price_symbols = price_symbols[:MAX_PRICE_SYMBOLS]
    log.info("Fetching prices for %d symbols", len(price_symbols))

    price_data: dict[str, dict] = {}
    for symbol in sorted(price_symbols):
        data = fetch_yahoo_finance(symbol)
        if data:
            price_data[symbol] = data
        time.sleep(0.2)

    # ---- output -------------------------------------------------------------
    output = {
        "generated_at": datetime.now(IST).isoformat(),
        "mode": mode_label,
        "news_count": len(all_items),
        "by_type": dict(Counter(i["type"] for i in all_items)),
        "source_stats": SOURCE_STATS,       # which source returned what / failed
        "news": all_items,
        "prices": price_data,
    }

    OUTPUT_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Saved %d news items + %d price records", len(all_items), len(price_data))
    log.info("By type: %s", output["by_type"])
    for src, st in SOURCE_STATS.items():
        log.info("SOURCE %-34s %s", src, st)


if __name__ == "__main__":
    main()
