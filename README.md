# 📈 Stock Morning Digest

A fully automated, **zero-cost** stock news analyzer that emails you a rich digest every weekday morning (8:00–8:30 IST) — powered by Claude AI, GitHub Actions, and Gmail.

---

## What it does

Every weekday at **07:30 IST**, GitHub Actions:

1. **Fetches** news from NSE announcements, MoneyControl, Business Standard, Yahoo Finance
2. **Filters** for high-impact events: quarterly results, new orders, bulk deals, dividends, M&A
3. **Analyzes** each item with Claude — fundamentals impact, price impact estimate, BUY/HOLD/WATCH/AVOID signal, conviction score
4. **Emails** a beautiful HTML digest to your inbox by 8:00–8:30 IST

---

## Setup (10 minutes)

### 1. Fork / clone this repo
```bash
git clone https://github.com/YOUR_USERNAME/stock-digest.git
cd stock-digest
```

### 2. Edit your watchlist
Open `src/news_fetcher.py` and update the `WATCHLIST` dict with your NSE symbols:
```python
WATCHLIST = {
    "RELIANCE": "Reliance Industries",
    "TCS": "Tata Consultancy Services",
    # ... add your stocks
}
```

### 3. Get a Gmail App Password
1. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
2. Select **Mail** + **Other (Custom name)** → "Stock Digest Bot"
3. Copy the 16-character password

### 4. Get an Anthropic API key
1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Create an API key
3. Cost estimate: ~$0.03–0.08 per day (claude-sonnet-4-6, ~20 news items)

### 5. Add GitHub Secrets
In your repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `GMAIL_USER` | yourname@gmail.com |
| `GMAIL_APP_PASS` | 16-char app password from step 3 |
| `RECIPIENT_EMAIL` | Who receives the email (can be same) |

### 6. Enable GitHub Actions
- Go to the **Actions** tab in your repo
- Click **Enable Actions**
- The workflow runs automatically Mon–Fri at 07:30 IST

### 7. Test it manually
- Go to **Actions → Stock Morning Digest → Run workflow**
- Check the artifacts tab for `data/email_preview.html`

---

## Local development
```bash
pip install -r requirements.txt

# Set env vars
export ANTHROPIC_API_KEY=...
export GMAIL_USER=...
export GMAIL_APP_PASS=...
export RECIPIENT_EMAIL=...
export DRY_RUN=true   # skips email, writes data/email_preview.html

mkdir -p data
python src/news_fetcher.py
python src/stock_analyzer.py
python src/email_sender.py
# Open data/email_preview.html in browser
```

---

## Cost breakdown

| Component | Cost |
|---|---|
| GitHub Actions (private repo) | 500 min/month free — job uses ~4 min/day × 22 days = 88 min/month ✅ |
| GitHub Actions (public repo)  | 2,000 min/month free ✅ |
| Claude API (claude-sonnet-4-6) | ~$0.03–0.10/day depending on news volume |
| Gmail SMTP | Free ✅ |
| NSE/BSE/Yahoo Finance data | Free ✅ |

**Total: effectively free, except ~₹75–200/month Claude API**

---

## Customization

### Add more news sources
Edit `RSS_FEEDS` in `news_fetcher.py`:
```python
RSS_FEEDS = [
    "https://your-rss-feed.com/rss.xml",
    ...
]
```

### Change delivery time
Edit the cron in `.github/workflows/morning_digest.yml`:
```yaml
- cron: "0 2 * * 1-5"   # 02:00 UTC = 07:30 IST
# For 08:00 IST: "30 2 * * 1-5"
# For 07:00 IST: "30 1 * * 1-5"
```

### Change the watchlist at runtime
You can also define `WATCHLIST` as a GitHub variable (not secret) and read it via `os.environ["WATCHLIST_JSON"]`.

### Add more signals
Edit the `SYSTEM_PROMPT` in `stock_analyzer.py` to ask Claude for additional signals like:
- Sector rotation alerts
- Comparison with peers
- FII/DII flow context
- Technical level alerts (support/resistance)
