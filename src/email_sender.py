"""
email_sender.py
Sends the formatted HTML digest via Gmail SMTP.
Uses Gmail App Password (not your regular Gmail password).
Set these GitHub Secrets:
  GMAIL_USER     = yourname@gmail.com
  GMAIL_APP_PASS = xxxx xxxx xxxx xxxx  (16-char app password)
  RECIPIENT_EMAIL = yourname@gmail.com  (can be same as sender)
"""

import logging
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from email_formatter import main as build_email

log = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))


def send_email(subject: str, html_body: str) -> None:
    sender    = os.environ["GMAIL_USER"]
    password  = os.environ["GMAIL_APP_PASS"]
    recipient = os.environ.get("RECIPIENT_EMAIL", sender)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Stock Digest Bot <{sender}>"
    msg["To"]      = recipient

    # Plain-text fallback (stripped)
    plain = f"Stock Digest — {datetime.now(IST).strftime('%d %b %Y')}\n\n" \
            "Open this email in an HTML-capable client to view the full digest."
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())

    log.info("Email sent to %s", recipient)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    subject, html = build_email()

    # Dry-run mode: set DRY_RUN=true to skip sending (useful for local testing)
    if os.environ.get("DRY_RUN", "").lower() == "true":
        Path("data/email_preview.html").write_text(html)
        log.info("DRY_RUN mode — email not sent. Preview at data/email_preview.html")
        log.info("Subject would be: %s", subject)
        return

    send_email(subject, html)


if __name__ == "__main__":
    main()
