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

SIGNAL_COLORS = {
    "BUY":   {"bg": "#e6f4ea", "text": "#1e7e34", "border": "#28a745"},
    "HOLD":  {"bg": "#fff3cd", "text": "#856404", "border": "#ffc107"},
    "WATCH": {"bg": "#cce5ff", "text": "#004085", "border": "#0066cc"},
    "AVOID": {"bg": "#f8d7da", "text": "#721c24", "border": "#dc3545"},
}
EVENT_LABELS = {
    "quarterly_result": "📊 Quarterly Result",
    "new_order":        "📦 New Order",
    "bulk_deal":        "💼 Bulk Deal",
    "corporate_action": "🔔 Corporate Action",
    "ma_event":         "🤝 M&A",
    "general":          "📰 News",
}


def conviction_bar(score: int) -> str:
    filled = "█" * score
    empty  = "░" * (10 - score)
    color  = "#28a745" if score >= 7 else "#ffc107" if score >= 4 else "#dc3545"
    return (
        f'<span style="font-family:monospace;color:{color};font-size:13px;">'
        f"{filled}{empty}</span> <span style='color:#555;font-size:12px;'>{score}/10</span>"
    )


def impact_badge(low: float, high: float, direction: str = "short") -> str:
    avg = (low + high) / 2
    color = "#28a745" if avg > 0 else "#dc3545" if avg < 0 else "#888"
    sign  = "+" if low >= 0 else ""
    return (
        f'<span style="background:{color}15;color:{color};padding:2px 8px;'
        f'border-radius:4px;font-size:12px;font-weight:600;">'
        f"{sign}{low:.1f}% to {sign}{high:.1f}%"
        f"</span>"
    )


def render_analysis_card(item: dict) -> str:
    sig    = item.get("signal", "WATCH")
    colors = SIGNAL_COLORS.get(sig, SIGNAL_COLORS["WATCH"])
    ev     = EVENT_LABELS.get(item.get("event_type", "general"), "📰 News")
    sym    = item.get("symbol") or "—"
    fund   = item.get("fundamentals", {})
    price  = item.get("price_impact", {})
    risks  = item.get("key_risks", [])
    cats   = item.get("key_catalysts", [])
    actions = item.get("action_items", [])

    risk_html = "".join(f"<li>{r}</li>" for r in risks[:3])
    cat_html  = "".join(f"<li>{c}</li>" for c in cats[:3])
    act_html  = "".join(
        f"<li style='margin-bottom:4px;'>→ {a}</li>" for a in actions[:3]
    )

    st_badge = impact_badge(
        price.get("short_term_pct_low", 0), price.get("short_term_pct_high", 0)
    )
    mt_badge = impact_badge(
        price.get("medium_term_pct_low", 0), price.get("medium_term_pct_high", 0)
    )

    return f"""
<div style="background:#ffffff;border-radius:10px;margin-bottom:16px;
            border:1px solid #e0e0e0;overflow:hidden;">
  <!-- Card header -->
  <div style="background:{colors['bg']};border-left:4px solid {colors['border']};
              padding:12px 16px;display:flex;justify-content:space-between;align-items:center;">
    <div>
      <span style="background:{colors['border']};color:#fff;padding:2px 10px;
                   border-radius:12px;font-size:11px;font-weight:700;">{sig}</span>
      &nbsp;
      <span style="font-size:12px;color:{colors['text']}">{ev}</span>
    </div>
    <span style="font-size:18px;font-weight:700;color:#1a1a1a;">{sym}</span>
  </div>

  <!-- Headline -->
  <div style="padding:12px 16px 8px;">
    <p style="margin:0;font-size:14px;font-weight:600;color:#1a1a1a;line-height:1.5;">
      {item.get('headline','')}
    </p>
  </div>

  <!-- Fundamentals -->
  <div style="padding:4px 16px 8px;">
    <table style="width:100%;border-collapse:collapse;font-size:12px;">
      <tr>
        <td style="color:#555;padding:3px 0;width:35%;">EPS impact</td>
        <td style="font-weight:600;text-transform:capitalize;">{fund.get('eps_impact','—')}</td>
        <td style="color:#555;padding:3px 0 3px 12px;width:35%;">Revenue</td>
        <td style="font-weight:600;text-transform:capitalize;">{fund.get('revenue_direction','—')}</td>
      </tr>
      <tr>
        <td style="color:#555;padding:3px 0;">Margins</td>
        <td style="font-weight:600;text-transform:capitalize;">{fund.get('margin_trend','—')}</td>
        <td style="color:#555;padding:3px 0 3px 12px;">Debt concern</td>
        <td style="font-weight:600;">{'⚠️ Yes' if fund.get('debt_concern') else 'No'}</td>
      </tr>
    </table>
    <p style="margin:6px 0 0;font-size:12px;color:#444;line-height:1.5;">
      {fund.get('commentary','')}
    </p>
  </div>

  <!-- Price impact -->
  <div style="padding:6px 16px;background:#f9f9f9;border-top:1px solid #eee;border-bottom:1px solid #eee;">
    <span style="font-size:11px;color:#777;text-transform:uppercase;letter-spacing:.5px;">Price impact estimate</span><br>
    <span style="font-size:12px;color:#333;">Short-term (1-5d): </span>{st_badge}
    &nbsp;&nbsp;
    <span style="font-size:12px;color:#333;">Medium-term (1-3M): </span>{mt_badge}
    <p style="margin:4px 0 0;font-size:12px;color:#555;">{price.get('rationale','')}</p>
  </div>

  <!-- Conviction -->
  <div style="padding:8px 16px;">
    <span style="font-size:11px;color:#777;text-transform:uppercase;letter-spacing:.5px;">Conviction</span>
    &nbsp;&nbsp;{conviction_bar(item.get('conviction', 5))}
  </div>

  <!-- Risks / Catalysts / Actions -->
  <div style="padding:0 16px 12px;display:grid;grid-template-columns:1fr 1fr;gap:12px;font-size:12px;">
    <div>
      <p style="margin:0 0 4px;font-weight:600;color:#dc3545;">⚠ Key Risks</p>
      <ul style="margin:0;padding-left:16px;color:#444;">{risk_html}</ul>
    </div>
    <div>
      <p style="margin:0 0 4px;font-weight:600;color:#28a745;">✦ Catalysts</p>
      <ul style="margin:0;padding-left:16px;color:#444;">{cat_html}</ul>
    </div>
  </div>
  {'<div style="padding:0 16px 12px;"><p style="margin:0 0 4px;font-size:12px;font-weight:600;color:#0066cc;">Action items</p><ul style="margin:0;padding-left:4px;list-style:none;font-size:12px;color:#333;">' + act_html + '</ul></div>' if act_html else ''}
</div>"""


def render_price_table(prices: dict) -> str:
    rows = ""
    for sym, p in prices.items():
        chg = p.get("change_pct")
        price_val = f"₹{p.get('price', 'N/A'):,.2f}" if isinstance(p.get("price"), (int, float)) else "N/A"
        if chg is None:
            chg_html = '<span style="color:#888;">—</span>'
        elif chg >= 0:
            chg_html = f'<span style="color:#28a745;font-weight:600;">+{chg:.2f}%</span>'
        else:
            chg_html = f'<span style="color:#dc3545;font-weight:600;">{chg:.2f}%</span>'
        rows += f"""<tr style="border-bottom:1px solid #f0f0f0;">
          <td style="padding:6px 4px;font-weight:600;">{sym}</td>
          <td style="padding:6px 4px;">{price_val}</td>
          <td style="padding:6px 4px;">{chg_html}</td>
          <td style="padding:6px 4px;font-size:11px;color:#777;">
            ₹{p.get('52w_low','N/A')} – ₹{p.get('52w_high','N/A')}
          </td>
        </tr>"""
    return f"""
<table style="width:100%;border-collapse:collapse;font-size:13px;">
  <thead>
    <tr style="background:#f5f5f5;">
      <th style="padding:8px 4px;text-align:left;font-size:11px;color:#777;text-transform:uppercase;">Symbol</th>
      <th style="padding:8px 4px;text-align:left;font-size:11px;color:#777;text-transform:uppercase;">Price</th>
      <th style="padding:8px 4px;text-align:left;font-size:11px;color:#777;text-transform:uppercase;">Change</th>
      <th style="padding:8px 4px;text-align:left;font-size:11px;color:#777;text-transform:uppercase;">52W Range</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>"""


def build_email(analysis: dict) -> tuple[str, str]:
    """Returns (subject, html_body)."""
    now = datetime.now(IST)
    date_str = now.strftime("%A, %d %b %Y")

    top_pick = analysis.get("top_pick")
    mkt_summary = analysis.get("market_summary", "")
    top_reason  = analysis.get("top_pick_reason", "")
    analyses    = analysis.get("analyses", [])
    prices      = analysis.get("prices", {})

    # Sort: BUY first, AVOID last
    order = {"BUY": 0, "WATCH": 1, "HOLD": 2, "AVOID": 3}
    analyses.sort(key=lambda x: order.get(x.get("signal", "WATCH"), 1))

    subject = f"📈 Stock Digest — {date_str}"
    if top_pick:
        subject += f" | Top pick: {top_pick}"

    cards_html = "".join(render_analysis_card(a) for a in analyses[:10])
    price_table = render_price_table(prices)

    top_pick_section = ""
    if top_pick:
        top_pick_section = f"""
<div style="background:#e8f5e9;border-radius:8px;padding:14px 16px;margin-bottom:20px;
            border-left:4px solid #28a745;">
  <p style="margin:0;font-size:12px;color:#1e7e34;font-weight:700;text-transform:uppercase;letter-spacing:.5px;">
    ✦ Today's highest conviction idea
  </p>
  <p style="margin:6px 0 0;font-size:20px;font-weight:800;color:#1a1a1a;">{top_pick}</p>
  <p style="margin:4px 0 0;font-size:13px;color:#2d6a4f;">{top_reason}</p>
</div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{subject}</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<div style="max-width:680px;margin:0 auto;background:#f4f4f4;padding:20px 12px;">

  <!-- Header -->
  <div style="background:#1a1a2e;border-radius:10px 10px 0 0;padding:20px 24px;">
    <p style="margin:0;font-size:11px;color:#8888aa;text-transform:uppercase;letter-spacing:1px;">Morning Stock Digest</p>
    <h1 style="margin:4px 0 0;font-size:22px;font-weight:800;color:#ffffff;">{date_str}</h1>
    <p style="margin:6px 0 0;font-size:13px;color:#aaaacc;">{len(analyses)} events analyzed across your watchlist</p>
  </div>

  <!-- Market summary -->
  <div style="background:#fff;padding:16px 20px;border-left:1px solid #e0e0e0;
              border-right:1px solid #e0e0e0;">
    <p style="margin:0;font-size:12px;color:#777;text-transform:uppercase;font-weight:600;letter-spacing:.5px;">
      Market Tone
    </p>
    <p style="margin:6px 0 0;font-size:14px;color:#222;line-height:1.6;">{mkt_summary}</p>
  </div>

  <!-- Top pick -->
  <div style="background:#fff;padding:0 20px 16px;border-left:1px solid #e0e0e0;
              border-right:1px solid #e0e0e0;">
    {top_pick_section}
  </div>

  <!-- Price snapshot -->
  <div style="background:#fff;padding:12px 20px 16px;border-left:1px solid #e0e0e0;
              border-right:1px solid #e0e0e0;margin-bottom:4px;">
    <p style="margin:0 0 10px;font-size:12px;color:#777;text-transform:uppercase;
              font-weight:600;letter-spacing:.5px;">Watchlist price snapshot</p>
    {price_table}
  </div>

  <!-- Divider -->
  <div style="background:#f0f0f0;height:4px;margin:0;"></div>

  <!-- Analysis cards -->
  <div style="background:#f4f4f4;padding:16px 0;">
    <p style="margin:0 0 12px;font-size:12px;color:#777;text-transform:uppercase;
              font-weight:600;letter-spacing:.5px;padding:0 4px;">Analysis</p>
    {cards_html}
  </div>

  <!-- Footer -->
  <div style="text-align:center;padding:16px;color:#aaa;font-size:11px;">
    <p style="margin:0;">Generated by your Stock Digest bot at
      {now.strftime('%I:%M %p IST')}</p>
    <p style="margin:4px 0 0;">Not financial advice. Do your own research.</p>
  </div>
</div>
</body>
</html>"""
    return subject, html


def main() -> tuple[str, str]:
    if not INPUT_FILE.exists():
        raise FileNotFoundError("data/analysis.json not found. Run stock_analyzer.py first.")
    analysis = json.loads(INPUT_FILE.read_text())
    return build_email(analysis)


if __name__ == "__main__":
    subj, html = main()
    Path("data/email_preview.html").write_text(html)
    print("Subject:", subj)
    print("Preview saved to data/email_preview.html")
