"""
email_formatter.py
Converts analysis JSON into a rich HTML email digest.
Designed for mobile + desktop rendering.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


INPUT_FILE = Path("data/analysis.json")

IST = timezone(timedelta(hours=5, minutes=30))


# ============================================================
# SIGNAL COLORS
# ============================================================

SIGNAL_COLORS = {
    "BUY": {
        "bg": "#e6f4ea",
        "text": "#1e7e34",
        "border": "#28a745",
    },
    "HOLD": {
        "bg": "#fff3cd",
        "text": "#856404",
        "border": "#ffc107",
    },
    "WATCH": {
        "bg": "#cce5ff",
        "text": "#004085",
        "border": "#0066cc",
    },
    "AVOID": {
        "bg": "#f8d7da",
        "text": "#721c24",
        "border": "#dc3545",
    },
}


# ============================================================
# EVENT LABELS
# ============================================================

EVENT_LABELS = {
    "quarterly_result": "📊 Quarterly Result",
    "new_order": "📦 New Order",
    "bulk_deal": "💼 Bulk Deal",
    "corporate_action": "🔔 Corporate Action",
    "ma_event": "🤝 M&A",
    "general": "📰 News",
}


# ============================================================
# CONVICTION BAR
# ============================================================

def conviction_bar(score: int) -> str:

    try:
        score = int(score)
    except (TypeError, ValueError):
        score = 5

    score = max(0, min(10, score))

    filled = "█" * score
    empty = "░" * (10 - score)

    if score >= 7:
        color = "#28a745"
    elif score >= 4:
        color = "#ffc107"
    else:
        color = "#dc3545"

    return (
        '<span style="'
        'font-family:monospace;'
        f'color:{color};'
        'font-size:13px;'
        'white-space:nowrap;'
        '">'
        f"{filled}{empty}"
        "</span> "
        '<span style="'
        'color:#555;'
        'font-size:12px;'
        'white-space:nowrap;'
        '">'
        f"{score}/10"
        "</span>"
    )


# ============================================================
# IMPACT BADGE
# ============================================================

def impact_badge(
    low: float,
    high: float,
    direction: str = "short",
) -> str:

    try:
        low = float(low)
    except (TypeError, ValueError):
        low = 0.0

    try:
        high = float(high)
    except (TypeError, ValueError):
        high = 0.0

    avg = (low + high) / 2

    if avg > 0:
        color = "#28a745"
    elif avg < 0:
        color = "#dc3545"
    else:
        color = "#888"

    sign_low = "+" if low >= 0 else ""
    sign_high = "+" if high >= 0 else ""

    return (
        '<span style="'
        f'background:{color}15;'
        f'color:{color};'
        'padding:2px 8px;'
        'border-radius:4px;'
        'font-size:12px;'
        'font-weight:600;'
        'white-space:nowrap;'
        '">'
        f"{sign_low}{low:.1f}% to "
        f"{sign_high}{high:.1f}%"
        "</span>"
    )


# ============================================================
# ANALYSIS CARD
# ============================================================

def render_analysis_card(item: dict) -> str:

    sig = item.get("signal", "WATCH")

    colors = SIGNAL_COLORS.get(
        sig,
        SIGNAL_COLORS["WATCH"],
    )

    ev = EVENT_LABELS.get(
        item.get("event_type", "general"),
        "📰 News",
    )

    sym = item.get("symbol") or "—"

    fund = item.get("fundamentals", {})
    price = item.get("price_impact", {})

    risks = item.get("key_risks", [])
    cats = item.get("key_catalysts", [])
    actions = item.get("action_items", [])

    # Make sure these are lists
    if not isinstance(risks, list):
        risks = []

    if not isinstance(cats, list):
        cats = []

    if not isinstance(actions, list):
        actions = []

    # --------------------------------------------------------
    # Risks
    # --------------------------------------------------------

    risk_html = "".join(
        f"<li style='margin-bottom:3px;'>{r}</li>"
        for r in risks[:3]
    )

    # --------------------------------------------------------
    # Catalysts
    # --------------------------------------------------------

    cat_html = "".join(
        f"<li style='margin-bottom:3px;'>{c}</li>"
        for c in cats[:3]
    )

    # --------------------------------------------------------
    # Actions
    # --------------------------------------------------------

    act_html = "".join(
        f"<li style='margin-bottom:4px;'>→ {a}</li>"
        for a in actions[:3]
    )

    # --------------------------------------------------------
    # Price Impact
    # --------------------------------------------------------

    st_badge = impact_badge(
        price.get("short_term_pct_low", 0),
        price.get("short_term_pct_high", 0),
    )

    mt_badge = impact_badge(
        price.get("medium_term_pct_low", 0),
        price.get("medium_term_pct_high", 0),
    )

    # --------------------------------------------------------
    # Action HTML
    # --------------------------------------------------------

    action_section = ""

    if act_html:

        action_section = f"""
        <div style="
            padding:0 14px 12px;
            word-break:break-word;
            overflow-wrap:anywhere;
        ">

            <p style="
                margin:0 0 4px;
                font-size:12px;
                font-weight:600;
                color:#0066cc;
            ">
                Action items
            </p>

            <ul style="
                margin:0;
                padding-left:4px;
                list-style:none;
                font-size:12px;
                color:#333;
            ">
                {act_html}
            </ul>

        </div>
        """

    # --------------------------------------------------------
    # Analysis Card
    # --------------------------------------------------------

    return f"""
<div style="
    background:#ffffff;
    border-radius:10px;
    margin-bottom:16px;
    border:1px solid #e0e0e0;
    overflow:hidden;
    width:100%;
    box-sizing:border-box;
">

    <!-- ================================================= -->
    <!-- CARD HEADER -->
    <!-- ================================================= -->

    <table
        role="presentation"
        cellpadding="0"
        cellspacing="0"
        border="0"
        width="100%"
        style="
            width:100%;
            border-collapse:collapse;
            table-layout:fixed;
        "
    >

        <tr>

            <td
                style="
                    background:{colors['bg']};
                    border-left:4px solid {colors['border']};
                    padding:12px 10px;
                    vertical-align:middle;
                    width:65%;
                "
            >

                <div style="
                    font-size:12px;
                    line-height:18px;
                    word-break:break-word;
                ">

                    <span style="
                        background:{colors['border']};
                        color:#fff;
                        padding:2px 8px;
                        border-radius:12px;
                        font-size:11px;
                        font-weight:700;
                    ">
                        {sig}
                    </span>

                    <span style="
                        color:{colors['text']};
                        margin-left:5px;
                    ">
                        {ev}
                    </span>

                </div>

            </td>


            <td
                style="
                    background:{colors['bg']};
                    padding:12px 10px;
                    text-align:right;
                    vertical-align:middle;
                    width:35%;
                    word-break:break-word;
                    overflow-wrap:anywhere;
                "
            >

                <span style="
                    font-size:18px;
                    font-weight:700;
                    color:#1a1a1a;
                ">
                    {sym}
                </span>

            </td>

        </tr>

    </table>


    <!-- ================================================= -->
    <!-- HEADLINE -->
    <!-- ================================================= -->

    <div style="
        padding:12px 14px 8px;
        word-break:break-word;
        overflow-wrap:anywhere;
    ">

        <p style="
            margin:0;
            font-size:14px;
            font-weight:600;
            color:#1a1a1a;
            line-height:1.5;
        ">
            {item.get('headline', '')}
        </p>

    </div>


    <!-- ================================================= -->
    <!-- FUNDAMENTALS -->
    <!-- ================================================= -->

    <div style="
        padding:4px 14px 8px;
        overflow:hidden;
    ">

        <table
            role="presentation"
            cellpadding="0"
            cellspacing="0"
            border="0"
            width="100%"
            style="
                width:100%;
                border-collapse:collapse;
                font-size:12px;
                table-layout:fixed;
            "
        >

            <tr>

                <td style="
                    color:#555;
                    padding:3px 4px 3px 0;
                    width:24%;
                ">
                    EPS impact
                </td>

                <td style="
                    font-weight:600;
                    text-transform:capitalize;
                    padding:3px 4px;
                    width:26%;
                    word-break:break-word;
                    overflow-wrap:anywhere;
                ">
                    {fund.get('eps_impact', '—')}
                </td>

                <td style="
                    color:#555;
                    padding:3px 4px 3px 8px;
                    width:24%;
                ">
                    Revenue
                </td>

                <td style="
                    font-weight:600;
                    text-transform:capitalize;
                    padding:3px 0 3px 4px;
                    width:26%;
                    word-break:break-word;
                    overflow-wrap:anywhere;
                ">
                    {fund.get('revenue_direction', '—')}
                </td>

            </tr>


            <tr>

                <td style="
                    color:#555;
                    padding:3px 4px 3px 0;
                ">
                    Margins
                </td>

                <td style="
                    font-weight:600;
                    text-transform:capitalize;
                    padding:3px 4px;
                    word-break:break-word;
                    overflow-wrap:anywhere;
                ">
                    {fund.get('margin_trend', '—')}
                </td>

                <td style="
                    color:#555;
                    padding:3px 4px 3px 8px;
                ">
                    Debt concern
                </td>

                <td style="
                    font-weight:600;
                    padding:3px 0 3px 4px;
                ">
                    {
                        '⚠️ Yes'
                        if fund.get('debt_concern')
                        else 'No'
                    }
                </td>

            </tr>

        </table>


        <p style="
            margin:6px 0 0;
            font-size:12px;
            color:#444;
            line-height:1.5;
            word-break:break-word;
            overflow-wrap:anywhere;
        ">
            {fund.get('commentary', '')}
        </p>

    </div>


    <!-- ================================================= -->
    <!-- PRICE IMPACT -->
    <!-- ================================================= -->

    <div style="
        padding:8px 14px;
        background:#f9f9f9;
        border-top:1px solid #eee;
        border-bottom:1px solid #eee;
        overflow:hidden;
    ">

        <span style="
            font-size:11px;
            color:#777;
            text-transform:uppercase;
            letter-spacing:.5px;
        ">
            Price impact estimate
        </span>


        <div style="
            margin-top:5px;
            font-size:12px;
            color:#333;
            line-height:2;
        ">

            <span>
                Short-term (1-5d):
            </span>

            {st_badge}

            <br>

            <span>
                Medium-term (1-3M):
            </span>

            {mt_badge}

        </div>


        <p style="
            margin:4px 0 0;
            font-size:12px;
            color:#555;
            line-height:1.5;
            word-break:break-word;
            overflow-wrap:anywhere;
        ">
            {price.get('rationale', '')}
        </p>

    </div>


    <!-- ================================================= -->
    <!-- CONVICTION -->
    <!-- ================================================= -->

    <div style="
        padding:8px 14px;
        overflow:hidden;
    ">

        <span style="
            font-size:11px;
            color:#777;
            text-transform:uppercase;
            letter-spacing:.5px;
        ">
            Conviction
        </span>

        <span style="margin-left:6px;">
            {conviction_bar(item.get('conviction', 5))}
        </span>

    </div>


    <!-- ================================================= -->
    <!-- RISKS / CATALYSTS -->
    <!-- ================================================= -->

    <table
        role="presentation"
        cellpadding="0"
        cellspacing="0"
        border="0"
        width="100%"
        style="
            width:100%;
            border-collapse:collapse;
            table-layout:fixed;
            font-size:12px;
        "
    >

        <tr>

            <td style="
                width:50%;
                vertical-align:top;
                padding:0 7px 12px 14px;
                word-break:break-word;
                overflow-wrap:anywhere;
            ">

                <p style="
                    margin:0 0 4px;
                    font-weight:600;
                    color:#dc3545;
                ">
                    ⚠ Key Risks
                </p>

                <ul style="
                    margin:0;
                    padding-left:16px;
                    color:#444;
                ">
                    {risk_html}
                </ul>

            </td>


            <td style="
                width:50%;
                vertical-align:top;
                padding:0 14px 12px 7px;
                word-break:break-word;
                overflow-wrap:anywhere;
            ">

                <p style="
                    margin:0 0 4px;
                    font-weight:600;
                    color:#28a745;
                ">
                    ✦ Catalysts
                </p>

                <ul style="
                    margin:0;
                    padding-left:16px;
                    color:#444;
                ">
                    {cat_html}
                </ul>

            </td>

        </tr>

    </table>


    <!-- ================================================= -->
    <!-- ACTION ITEMS -->
    <!-- ================================================= -->

    {action_section}

</div>
"""


# ============================================================
# PRICE TABLE
# ============================================================

def render_price_table(prices: dict) -> str:

    rows = ""

    if not isinstance(prices, dict):
        prices = {}

    for sym, p in prices.items():

        if not isinstance(p, dict):
            p = {}

        # ----------------------------------------------------
        # Price
        # ----------------------------------------------------

        price_value = p.get("price")

        if isinstance(price_value, (int, float)):

            price_val = f"₹{price_value:,.2f}"

        else:

            price_val = "N/A"


        # ----------------------------------------------------
        # Change
        # ----------------------------------------------------

        chg = p.get("change_pct")

        if chg is None:

            chg_html = (
                '<span style="color:#888;">—</span>'
            )

        else:

            try:
                chg = float(chg)
            except (TypeError, ValueError):
                chg = None

            if chg is None:

                chg_html = (
                    '<span style="color:#888;">—</span>'
                )

            elif chg >= 0:

                chg_html = (
                    '<span style="'
                    'color:#28a745;'
                    'font-weight:600;'
                    'white-space:nowrap;'
                    '">'
                    f"+{chg:.2f}%"
                    '</span>'
                )

            else:

                chg_html = (
                    '<span style="'
                    'color:#dc3545;'
                    'font-weight:600;'
                    'white-space:nowrap;'
                    '">'
                    f"{chg:.2f}%"
                    '</span>'
                )


        # ----------------------------------------------------
        # 52 Week Range
        # ----------------------------------------------------

        low = p.get(
            "52w_low",
            "N/A",
        )

        high = p.get(
            "52w_high",
            "N/A",
        )


        # ----------------------------------------------------
        # Row
        # ----------------------------------------------------

        rows += f"""
        <tr style="
            border-bottom:1px solid #f0f0f0;
        ">

            <!-- SYMBOL -->

            <td style="
                padding:7px 3px;
                width:23%;
                vertical-align:middle;
                word-break:break-word;
                overflow-wrap:anywhere;
                font-weight:600;
            ">
                {sym}
            </td>


            <!-- PRICE -->

            <td style="
                padding:7px 3px;
                width:23%;
                vertical-align:middle;
                white-space:nowrap;
                font-size:12px;
            ">
                {price_val}
            </td>


            <!-- CHANGE -->

            <td style="
                padding:7px 3px;
                width:21%;
                vertical-align:middle;
                white-space:nowrap;
                font-size:12px;
            ">
                {chg_html}
            </td>


            <!-- 52 WEEK RANGE -->

            <td style="
                padding:7px 3px;
                width:33%;
                vertical-align:middle;
                font-size:10px;
                color:#777;
                line-height:1.4;
                word-break:break-word;
                overflow-wrap:anywhere;
            ">
                ₹{low} – ₹{high}
            </td>

        </tr>
        """


    # --------------------------------------------------------
    # Complete Table
    # --------------------------------------------------------

    return f"""
<table
    role="presentation"
    cellpadding="0"
    cellspacing="0"
    border="0"
    width="100%"
    style="
        width:100%;
        max-width:100%;
        border-collapse:collapse;
        table-layout:fixed;
        font-size:12px;
    "
>

    <thead>

        <tr style="
            background:#f5f5f5;
        ">


            <!-- SYMBOL HEADER -->

            <th style="
                padding:8px 3px;
                width:23%;
                text-align:left;
                font-size:10px;
                color:#777;
                text-transform:uppercase;
                font-weight:600;
            ">
                Symbol
            </th>


            <!-- PRICE HEADER -->

            <th style="
                padding:8px 3px;
                width:23%;
                text-align:left;
                font-size:10px;
                color:#777;
                text-transform:uppercase;
                font-weight:600;
            ">
                Price
            </th>


            <!-- CHANGE HEADER -->

            <th style="
                padding:8px 3px;
                width:21%;
                text-align:left;
                font-size:10px;
                color:#777;
                text-transform:uppercase;
                font-weight:600;
            ">
                Change
            </th>


            <!-- 52 WEEK HEADER -->

            <th style="
                padding:8px 3px;
                width:33%;
                text-align:left;
                font-size:10px;
                color:#777;
                text-transform:uppercase;
                font-weight:600;
            ">
                52W Range
            </th>

        </tr>

    </thead>


    <tbody>

        {rows}

    </tbody>

</table>
"""


# ============================================================
# BUILD EMAIL
# ============================================================

def build_email(analysis: dict) -> tuple[str, str]:
    """
    Returns:
        (subject, html_body)
    """

    now = datetime.now(IST)

    date_str = now.strftime(
        "%A, %d %b %Y"
    )

    top_pick = analysis.get(
        "top_pick"
    )

    mkt_summary = analysis.get(
        "market_summary",
        "",
    )

    top_reason = analysis.get(
        "top_pick_reason",
        "",
    )

    analyses = analysis.get(
        "analyses",
        [],
    )

    prices = analysis.get(
        "prices",
        {},
    )


    # ========================================================
    # SAFETY
    # ========================================================

    if not isinstance(analyses, list):
        analyses = []

    if not isinstance(prices, dict):
        prices = {}


    # ========================================================
    # SORT ANALYSIS
    # ========================================================

    order = {
        "BUY": 0,
        "WATCH": 1,
        "HOLD": 2,
        "AVOID": 3,
    }

    analyses.sort(
        key=lambda x: order.get(
            x.get(
                "signal",
                "WATCH",
            ),
            1,
        )
    )


    # ========================================================
    # SUBJECT
    # ========================================================

    subject = (
        f"📈 Stock Digest — {date_str}"
    )

    if top_pick:

        subject += (
            f" | Top pick: {top_pick}"
        )


    # ========================================================
    # ANALYSIS CARDS
    # ========================================================

    cards_html = "".join(
        render_analysis_card(a)
        for a in analyses[:10]
    )


    # ========================================================
    # PRICE TABLE
    # ========================================================

    price_table = render_price_table(
        prices
    )


    # ========================================================
    # TOP PICK
    # ========================================================

    top_pick_section = ""

    if top_pick:

        top_pick_section = f"""
<div style="
    background:#e8f5e9;
    border-radius:8px;
    padding:14px;
    margin-bottom:20px;
    border-left:4px solid #28a745;
    word-break:break-word;
    overflow-wrap:anywhere;
">

    <p style="
        margin:0;
        font-size:12px;
        color:#1e7e34;
        font-weight:700;
        text-transform:uppercase;
        letter-spacing:.5px;
    ">
        ✦ Today's highest conviction idea
    </p>


    <p style="
        margin:6px 0 0;
        font-size:20px;
        font-weight:800;
        color:#1a1a1a;
        word-break:break-word;
        overflow-wrap:anywhere;
    ">
        {top_pick}
    </p>


    <p style="
        margin:4px 0 0;
        font-size:13px;
        color:#2d6a4f;
        line-height:1.5;
        word-break:break-word;
        overflow-wrap:anywhere;
    ">
        {top_reason}
    </p>

</div>
"""


    # ========================================================
    # FULL EMAIL HTML
    # ========================================================

    html = f"""<!DOCTYPE html>

<html lang="en">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<meta
    http-equiv="X-UA-Compatible"
    content="IE=edge"
>

<title>{subject}</title>

</head>


<body
    style="
        margin:0;
        padding:0;
        background:#f4f4f4;
        font-family:-apple-system,BlinkMacSystemFont,
                     'Segoe UI',Arial,sans-serif;
        width:100%;
    "
>


<!-- ======================================================== -->
<!-- OUTER WRAPPER -->
<!-- ======================================================== -->

<table
    role="presentation"
    cellpadding="0"
    cellspacing="0"
    border="0"
    width="100%"
    style="
        width:100%;
        border-collapse:collapse;
        background:#f4f4f4;
    "
>

<tr>

<td
    align="center"
    style="
        padding:12px;
    "
>


<!-- ======================================================== -->
<!-- MAIN CONTAINER -->
<!-- ======================================================== -->

<table
    role="presentation"
    cellpadding="0"
    cellspacing="0"
    border="0"
    width="680"
    style="
        width:100%;
        max-width:680px;
        border-collapse:collapse;
        background:#f4f4f4;
    "
>


<!-- ======================================================== -->
<!-- HEADER -->
<!-- ======================================================== -->

<tr>

<td
    style="
        background:#1a1a2e;
        border-radius:10px 10px 0 0;
        padding:20px 18px;
    "
>

<p style="
    margin:0;
    font-size:11px;
    color:#8888aa;
    text-transform:uppercase;
    letter-spacing:1px;
">
    Morning Stock Digest
</p>


<h1 style="
    margin:4px 0 0;
    font-size:22px;
    font-weight:800;
    color:#ffffff;
    line-height:1.3;
">
    {date_str}
</h1>


<p style="
    margin:6px 0 0;
    font-size:13px;
    color:#aaaacc;
    line-height:1.4;
">
    {len(analyses)} events analyzed across your watchlist
</p>

</td>

</tr>


<!-- ======================================================== -->
<!-- MARKET SUMMARY -->
<!-- ======================================================== -->

<tr>

<td
    style="
        background:#ffffff;
        padding:16px 14px;
        border-left:1px solid #e0e0e0;
        border-right:1px solid #e0e0e0;
    "
>

<p style="
    margin:0;
    font-size:12px;
    color:#777;
    text-transform:uppercase;
    font-weight:600;
    letter-spacing:.5px;
">
    Market Tone
</p>


<p style="
    margin:6px 0 0;
    font-size:14px;
    color:#222;
    line-height:1.6;
    word-break:break-word;
    overflow-wrap:anywhere;
">
    {mkt_summary}
</p>

</td>

</tr>


<!-- ======================================================== -->
<!-- TOP PICK -->
<!-- ======================================================== -->

<tr>

<td
    style="
        background:#ffffff;
        padding:0 14px 16px;
        border-left:1px solid #e0e0e0;
        border-right:1px solid #e0e0e0;
    "
>

{top_pick_section}

</td>

</tr>


<!-- ======================================================== -->
<!-- PRICE SNAPSHOT -->
<!-- ======================================================== -->

<tr>

<td
    style="
        background:#ffffff;
        padding:12px 14px 16px;
        border-left:1px solid #e0e0e0;
        border-right:1px solid #e0e0e0;
        overflow:hidden;
    "
>

<p style="
    margin:0 0 10px;
    font-size:12px;
    color:#777;
    text-transform:uppercase;
    font-weight:600;
    letter-spacing:.5px;
">
    Watchlist price snapshot
</p>


{price_table}

</td>

</tr>


<!-- ======================================================== -->
<!-- DIVIDER -->
<!-- ======================================================== -->

<tr>

<td
    style="
        background:#f0f0f0;
        height:4px;
        line-height:4px;
        font-size:0;
    "
>
    &nbsp;
</td>

</tr>


<!-- ======================================================== -->
<!-- ANALYSIS -->
<!-- ======================================================== -->

<tr>

<td
    style="
        background:#f4f4f4;
        padding:16px 0;
    "
>

<p style="
    margin:0 0 12px;
    font-size:12px;
    color:#777;
    text-transform:uppercase;
    font-weight:600;
    letter-spacing:.5px;
    padding:0 4px;
">
    Analysis
</p>


{cards_html}

</td>

</tr>


<!-- ======================================================== -->
<!-- FOOTER -->
<!-- ======================================================== -->

<tr>

<td
    style="
        text-align:center;
        padding:16px 10px;
        color:#aaa;
        font-size:11px;
        line-height:1.5;
    "
>

<p style="margin:0;">
    Generated by your Stock Digest bot at
    {now.strftime('%I:%M %p IST')}
</p>


<p style="margin:4px 0 0;">
    Not financial advice. Do your own research.
</p>

</td>

</tr>


</table>

<!-- END MAIN CONTAINER -->


</td>

</tr>

</table>

<!-- END OUTER WRAPPER -->


</body>

</html>
"""

    return subject, html


# ============================================================
# MAIN
# ============================================================

def main() -> tuple[str, str]:

    if not INPUT_FILE.exists():

        raise FileNotFoundError(
            "data/analysis.json not found. "
            "Run stock_analyzer.py first."
        )

    analysis = json.loads(
        INPUT_FILE.read_text()
    )

    return build_email(
        analysis
    )


# ============================================================
# LOCAL EXECUTION
# ============================================================

if __name__ == "__main__":

    subj, html = main()

    Path(
        "data/email_preview.html"
    ).write_text(
        html,
        encoding="utf-8",
    )

    print(
        "Subject:",
        subj,
    )

    print(
        "Preview saved to data/email_preview.html"
    )
