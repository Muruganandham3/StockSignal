"""
email_formatter.py  (news-only version)

Reads data/news_raw.json (written by news_fetcher.py) and builds an HTML
email digest. No AI analysis.

Layout: ONE table. Each stock gets
    row 1 -> SYMBOL | PRICE | CHANGE | 52W RANGE
    row 2 -> its news: type badge + headline + short description + source link

Stocks with news come first (results > deals > orders > ...), then a compact
list of watchlist stocks with no news, then general market news that could
not be matched to a stock.

build_email(data) -> (subject, html)   and   main() -> (subject, html)
keep the same signatures as before, so your sender code does not change.
"""

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path


INPUT_FILE = Path("data/news_raw.json")
IST = timezone(timedelta(hours=5, minutes=30))

MAX_NEWS_PER_STOCK = 3     # extra items are shown as "+N more"
MAX_DESC_CHARS = 180       # length of the short description
MAX_HEADLINE_CHARS = 140
MAX_OTHER_NEWS = 15        # items without a stock symbol
MAX_DEALS_PER_STOCK = 8    # bulk/block deal lines shown per stock (compact)
MAX_EMAIL_BYTES = 95_000   # Gmail clips messages around 102 KB; shrink below this

# adjusted automatically by build_email() when the email gets too big
_LIMITS = {"news": MAX_NEWS_PER_STOCK, "deals": MAX_DEALS_PER_STOCK, "stocks": None}


# ============================================================
# EVENT STYLES: label, text colour, background colour
# ============================================================

EVENT_STYLES = {
    "quarterly_result": ("📊 Result", "#1565c0", "#e3f2fd"),
    "bulk_deal": ("💼 Bulk deal", "#6a1b9a", "#f3e5f5"),
    "block_deal": ("💼 Block deal", "#6a1b9a", "#f3e5f5"),
    "new_order": ("📦 Order", "#2e7d32", "#e8f5e9"),
    "ma_event": ("🤝 M&amp;A", "#ef6c00", "#fff3e0"),
    "corporate_action": ("🔔 Corp. action", "#00838f", "#e0f7fa"),
    "analyst_call": ("🎯 Analyst", "#4527a0", "#ede7f6"),
    "fund_raise": ("💰 Fund raise", "#5d4037", "#efebe9"),
    "news": ("📰 News", "#455a64", "#eceff1"),
    "general": ("📰 News", "#455a64", "#eceff1"),
}

PRIORITY = {
    "quarterly_result": 100, "bulk_deal": 90, "block_deal": 90,
    "new_order": 80, "ma_event": 80, "corporate_action": 70,
    "analyst_call": 65, "fund_raise": 60, "news": 40, "general": 30,
}


# ============================================================
# SMALL HELPERS
# ============================================================

def _esc(value) -> str:
    return escape("" if value is None else str(value), quote=True)


def _truncate(text, limit: int) -> str:
    text = re.sub(r"\s+", " ", "" if text is None else str(text)).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut + "…"


def _num(value) -> str:
    try:
        text = f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"
    return text[:-3] if text.endswith(".00") else text


def _item_priority(item: dict) -> int:
    p = item.get("priority")
    if isinstance(p, (int, float)):
        return int(p)
    return PRIORITY.get(item.get("type", "general"), 30)


def _headline(item: dict) -> str:
    """Title without the '[SYMBOL] ' prefix the fetcher adds."""
    title = str(item.get("title") or "")
    title = re.sub(r"^\[[^\]]+\]\s*", "", title).strip()
    return _truncate(title or item.get("summary") or "(no title)", MAX_HEADLINE_CHARS)


def _safe_url(url) -> str:
    url = str(url or "").strip()
    return url if url.startswith(("http://", "https://")) else ""


# ============================================================
# BADGE
# ============================================================

def type_badge(event_type: str) -> str:
    label, fg, bg = EVENT_STYLES.get(event_type, EVENT_STYLES["general"])
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 7px;'
        f'border-radius:10px;font-size:11px;font-weight:700;'
        f'white-space:nowrap;">{label}</span>'
    )


# ============================================================
# ONE NEWS ITEM (headline + short description + source link)
# ============================================================

def _inr_short(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if v >= 1e7:
        return f"₹{v / 1e7:,.1f} cr"
    if v >= 1e5:
        return f"₹{v / 1e5:,.1f} L"
    return f"₹{v:,.0f}"


def render_deal_item(item: dict, deal: dict) -> str:
    """One compact line per bulk/block deal, coloured by BUY / SELL."""
    side = str(deal.get("side") or "").upper()
    kind = "Block" if item.get("type") == "block_deal" else "Bulk"
    if side.startswith("B"):
        word, fg, bg = "BUY", "#2e7d32", "#e8f5e9"
    elif side.startswith("S"):
        word, fg, bg = "SELL", "#c62828", "#ffebee"
    else:
        word, fg, bg = (side or "DEAL"), "#6a1b9a", "#f3e5f5"

    try:
        qty_txt = f"{int(float(deal.get('qty') or 0)):,}"
    except (TypeError, ValueError):
        qty_txt = "?"
    client = deal.get("client") or "Unknown client"
    line = (
        f"{_esc(client)} — {qty_txt} sh @ ₹{_num(deal.get('price'))} "
        f"({_inr_short(deal.get('value_inr'))})"
    )
    return (
        '<div style="margin:0 0 6px;line-height:1.5;word-break:break-word;overflow-wrap:anywhere;">'
        f'<span style="background:{bg};color:{fg};padding:2px 7px;border-radius:10px;'
        f'font-size:11px;font-weight:700;white-space:nowrap;">💼 {kind} {word}</span> '
        f'<span style="font-size:12px;color:#222;">{line}</span></div>'
    )


def render_news_item(item: dict) -> str:
    deal = item.get("deal")
    if isinstance(deal, dict):
        return render_deal_item(item, deal)
    headline = _headline(item)
    desc = _truncate(item.get("summary"), MAX_DESC_CHARS)
    if desc.lower() == headline.lower() or headline.lower().startswith(desc.lower()):
        desc = ""

    source = _esc(item.get("source") or "source")
    url = _safe_url(item.get("url"))
    link = (
        f'<a href="{_esc(url)}" style="color:#0066cc;text-decoration:none;'
        f'font-size:11px;white-space:nowrap;">{source} ↗</a>'
        if url else
        f'<span style="color:#888;font-size:11px;">{source}</span>'
    )

    desc_html = (
        f'<div style="font-size:12px;color:#555;line-height:1.5;'
        f'margin-top:2px;">{_esc(desc)}</div>'
        if desc else ""
    )

    return f"""
<div style="margin:0 0 9px;word-break:break-word;overflow-wrap:anywhere;">
  <div style="line-height:1.5;">
    {type_badge(item.get("type", "general"))}
    <span style="font-size:13px;font-weight:600;color:#1a1a1a;">{_esc(headline)}</span>
  </div>
  {desc_html}
  <div style="margin-top:2px;">{link}</div>
</div>"""


# ============================================================
# PRICE CELLS
# ============================================================

def _change_html(chg) -> str:
    try:
        chg = float(chg)
    except (TypeError, ValueError):
        return '<span style="color:#888;">—</span>'
    color = "#28a745" if chg >= 0 else "#dc3545"
    sign = "+" if chg >= 0 else ""
    return (
        f'<span style="color:{color};font-weight:700;white-space:nowrap;">'
        f"{sign}{chg:.2f}%</span>"
    )


def _price_cells(p: dict) -> tuple[str, str, str]:
    price = p.get("price")
    price_txt = f"₹{_num(price)}" if isinstance(price, (int, float)) else "N/A"
    low, high = p.get("52w_low"), p.get("52w_high")
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        range_txt = f"₹{_num(low)} – ₹{_num(high)}"
    else:
        range_txt = "—"
    return price_txt, _change_html(p.get("change_pct")), range_txt


# column widths shared by every row so the table lines up
COL_W = (30, 22, 18, 30)

_TABLE_OPEN = (
    '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
    'width="100%" style="width:100%;border-collapse:collapse;'
    'table-layout:fixed;font-size:12px;">'
)

_TABLE_HEAD = f"""
<thead>
  <tr style="background:#f5f5f5;">
    <th style="padding:8px 6px;width:{COL_W[0]}%;text-align:left;font-size:10px;color:#777;text-transform:uppercase;">Symbol</th>
    <th style="padding:8px 6px;width:{COL_W[1]}%;text-align:left;font-size:10px;color:#777;text-transform:uppercase;">Price</th>
    <th style="padding:8px 6px;width:{COL_W[2]}%;text-align:left;font-size:10px;color:#777;text-transform:uppercase;">Change</th>
    <th style="padding:8px 6px;width:{COL_W[3]}%;text-align:left;font-size:10px;color:#777;text-transform:uppercase;">52W range</th>
  </tr>
</thead>"""


# ============================================================
# STOCK BLOCK = price row + news row
# ============================================================

def render_stock_block(sym: str, price: dict, items: list[dict]) -> str:
    price_txt, chg_html, range_txt = _price_cells(price or {})

    deals = [i for i in items if isinstance(i.get("deal"), dict)]
    others = [i for i in items if not isinstance(i.get("deal"), dict)]
    shown_o = others[:_LIMITS["news"]]
    shown_d = deals[:_LIMITS["deals"]]
    news_html = "".join(render_news_item(i) for i in shown_o + shown_d)
    extra = (len(others) - len(shown_o)) + (len(deals) - len(shown_d))
    if extra > 0:
        news_html += (
            f'<div style="font-size:11px;color:#888;">+{extra} more '
            f'item{"s" if extra > 1 else ""} for {_esc(sym)}</div>'
        )

    return f"""
<tr style="background:#fafafa;border-top:2px solid #e0e0e0;">
  <td style="padding:9px 6px 5px;font-size:13px;font-weight:800;color:#1a1a1a;word-break:break-word;">{_esc(sym)}</td>
  <td style="padding:9px 6px 5px;font-size:13px;white-space:nowrap;">{price_txt}</td>
  <td style="padding:9px 6px 5px;font-size:13px;white-space:nowrap;">{chg_html}</td>
  <td style="padding:9px 6px 5px;font-size:10px;color:#777;line-height:1.4;">{range_txt}</td>
</tr>
<tr>
  <td colspan="4" style="padding:4px 8px 6px 12px;border-left:3px solid #d0d7de;">{news_html}</td>
</tr>"""


def render_no_news_rows(rows: list[tuple[str, dict]]) -> str:
    out = ""
    for sym, p in rows:
        price_txt, chg_html, range_txt = _price_cells(p)
        out += f"""
<tr style="border-top:1px solid #f0f0f0;">
  <td style="padding:6px;font-weight:600;word-break:break-word;">{_esc(sym)}</td>
  <td style="padding:6px;white-space:nowrap;">{price_txt}</td>
  <td style="padding:6px;white-space:nowrap;">{chg_html}</td>
  <td style="padding:6px;font-size:10px;color:#777;line-height:1.4;">{range_txt}</td>
</tr>"""
    return out


def render_other_news(items: list[dict]) -> str:
    if not items:
        return ""
    body = "".join(render_news_item(i) for i in items[:MAX_OTHER_NEWS])
    return f"""
<tr>
  <td style="background:#ffffff;padding:14px;border-left:1px solid #e0e0e0;border-right:1px solid #e0e0e0;border-top:4px solid #f0f0f0;">
    <p style="margin:0 0 10px;font-size:12px;color:#777;text-transform:uppercase;font-weight:600;letter-spacing:.5px;">
      Other market news (no stock matched)
    </p>
    {body}
  </td>
</tr>"""


# ============================================================
# BUILD EMAIL
# ============================================================

def _build_email(data: dict) -> tuple[str, str]:
    now = datetime.now(IST)
    date_str = now.strftime("%A, %d %b %Y")

    news = data.get("news") or []
    prices = data.get("prices") or {}
    if not isinstance(news, list):
        news = []
    if not isinstance(prices, dict):
        prices = {}

    # ---- group news by symbol ---------------------------------------------
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    other: list[dict] = []
    for item in news:
        if not isinstance(item, dict):
            continue
        symbols = [str(s).strip().upper() for s in (item.get("symbols") or []) if s]
        if symbols:
            for s in symbols:
                by_symbol[s].append(item)
        else:
            other.append(item)

    # best news first inside each stock; drop duplicate headlines
    for sym, items in by_symbol.items():
        items.sort(key=_item_priority, reverse=True)
        seen, unique = set(), []
        for it in items:
            key = re.sub(r"\W+", "", _headline(it).lower())[:60]
            if key not in seen:
                seen.add(key)
                unique.append(it)
        by_symbol[sym] = unique
    other.sort(key=_item_priority, reverse=True)

    # ---- order stocks: strongest event first, then biggest mover ----------
    def sort_key(sym: str):
        items = by_symbol[sym]
        chg = (prices.get(sym) or {}).get("change_pct")
        try:
            move = abs(float(chg))
        except (TypeError, ValueError):
            move = 0.0
        return (-_item_priority(items[0]), -move)

    news_symbols = sorted(by_symbol, key=sort_key)
    hidden_stocks = 0
    if _LIMITS.get("stocks") and len(news_symbols) > _LIMITS["stocks"]:
        hidden_stocks = len(news_symbols) - _LIMITS["stocks"]
        news_symbols = news_symbols[: _LIMITS["stocks"]]
    no_news = [
        (sym, p) for sym, p in sorted(prices.items())
        if sym not in by_symbol and isinstance(p, dict)
    ]

    # ---- summary numbers ---------------------------------------------------
    type_counts = Counter(i.get("type", "general") for i in news if isinstance(i, dict))
    chips = ""
    for etype in ("quarterly_result", "bulk_deal", "block_deal", "new_order",
                  "ma_event", "corporate_action", "analyst_call", "fund_raise"):
        if type_counts.get(etype):
            label = EVENT_STYLES[etype][0]
            chips += (
                f'<span style="display:inline-block;margin:0 10px 4px 0;'
                f'color:#ccd;font-size:12px;">{label} '
                f'<b style="color:#fff;">{type_counts[etype]}</b></span>'
            )

    as_of_counts = Counter(p.get("as_of") for p in prices.values()
                           if isinstance(p, dict) and p.get("as_of"))
    as_of = as_of_counts.most_common(1)[0][0] if as_of_counts else None
    price_note = f"Prices: last close ({as_of})" if as_of else "Prices: last close"

    sources = sorted({str(i.get("source")) for i in news
                      if isinstance(i, dict) and i.get("source")})

    # ---- subject -----------------------------------------------------------
    subject = f"📰 Stock News Digest — {date_str} | {len(news_symbols)} stocks with news"

    # ---- stock table -------------------------------------------------------
    blocks = "".join(
        render_stock_block(sym, prices.get(sym) or {}, by_symbol[sym])
        for sym in news_symbols
    )
    if hidden_stocks:
        blocks += (
            '<tr><td colspan="4" style="padding:10px 6px;color:#888;font-size:12px;'
            'border-top:2px solid #e0e0e0;">'
            f"+{hidden_stocks} more stocks with news not shown (email size limit). "
            "Lower-priority items are cut first; full list is in data/news_raw.json."
            "</td></tr>"
        )
    if not blocks:
        blocks = (
            '<tr><td colspan="4" style="padding:14px 6px;color:#777;">'
            "No stock-specific news collected today.</td></tr>"
        )

    no_news_section = ""
    if no_news:
        no_news_section = f"""
<tr>
  <td style="background:#ffffff;padding:12px 14px 16px;border-left:1px solid #e0e0e0;border-right:1px solid #e0e0e0;border-top:4px solid #f0f0f0;">
    <p style="margin:0 0 8px;font-size:12px;color:#777;text-transform:uppercase;font-weight:600;letter-spacing:.5px;">
      Watchlist — no news today
    </p>
    {_TABLE_OPEN}{_TABLE_HEAD}<tbody>{render_no_news_rows(no_news)}</tbody></table>
  </td>
</tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>{_esc(subject)}</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;width:100%;">

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="width:100%;border-collapse:collapse;background:#f4f4f4;">
<tr><td align="center" style="padding:12px;">

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="680" style="width:100%;max-width:680px;border-collapse:collapse;background:#f4f4f4;">

<!-- HEADER -->
<tr>
<td style="background:#1a1a2e;border-radius:10px 10px 0 0;padding:20px 18px;">
  <p style="margin:0;font-size:11px;color:#8888aa;text-transform:uppercase;letter-spacing:1px;">Morning Stock News</p>
  <h1 style="margin:4px 0 0;font-size:22px;font-weight:800;color:#ffffff;line-height:1.3;">{_esc(date_str)}</h1>
  <p style="margin:6px 0 8px;font-size:13px;color:#aaaacc;line-height:1.4;">
    {len(news)} news items · {len(news_symbols)} stocks · {_esc(price_note)}
  </p>
  <div>{chips}</div>
</td>
</tr>

<!-- STOCKS WITH NEWS -->
<tr>
<td style="background:#ffffff;padding:12px 14px 16px;border-left:1px solid #e0e0e0;border-right:1px solid #e0e0e0;">
  <p style="margin:0 0 8px;font-size:12px;color:#777;text-transform:uppercase;font-weight:600;letter-spacing:.5px;">
    Stocks with news
  </p>
  {_TABLE_OPEN}{_TABLE_HEAD}<tbody>{blocks}</tbody></table>
</td>
</tr>

{no_news_section}
{render_other_news(other)}

<!-- FOOTER -->
<tr>
<td style="text-align:center;padding:16px 10px;color:#aaa;font-size:11px;line-height:1.5;">
  <p style="margin:0;">Sources: {_esc(", ".join(sources)) or "—"}</p>
  <p style="margin:4px 0 0;">Generated at {now.strftime('%I:%M %p IST')} · Prices are the last close, not live · No analysis or ratings, news only.</p>
</td>
</tr>

</table>
</td></tr>
</table>

</body>
</html>
"""
    return subject, html


def build_email(data: dict) -> tuple[str, str]:
    """Build the email; shrink per-stock detail if it would exceed Gmail's clip limit."""
    levels = [
        (MAX_NEWS_PER_STOCK, MAX_DEALS_PER_STOCK),
        (2, 4),
        (1, 2),
        (1, 1),
    ]
    attempts = [(n, d, None) for n, d in levels]
    attempts += [(1, 1, k) for k in (120, 90, 70, 50, 35, 25, 15)]   # then cut whole stocks
    for news_n, deals_n, stocks_n in attempts:
        _LIMITS.update(news=news_n, deals=deals_n, stocks=stocks_n)
        subject, html = _build_email(data)
        if len(html.encode("utf-8")) <= MAX_EMAIL_BYTES:
            break
    _LIMITS.update(news=MAX_NEWS_PER_STOCK, deals=MAX_DEALS_PER_STOCK, stocks=None)
    return subject, html


# ============================================================
# MAIN
# ============================================================

def main() -> tuple[str, str]:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            "data/news_raw.json not found. Run news_fetcher.py first."
        )
    data = json.loads(INPUT_FILE.read_text(encoding="utf-8"))
    return build_email(data)


if __name__ == "__main__":
    subj, html = main()
    Path("data/email_preview.html").write_text(html, encoding="utf-8")
    print("Subject:", subj)
    print("Preview saved to data/email_preview.html")
