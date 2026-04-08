"""
email/sender.py
───────────────
Sends the weekly NOzempic email to each participant via Gmail SMTP.

Emails go out from a dedicated NOzempic Gmail account — not from any
participant's personal email. Each person gets their own individual send
(no CC/BCC) to protect privacy.

Setup:
    1. Create nozempic.coach@gmail.com (or similar)
    2. Enable 2-Step Verification on that account
    3. Generate an App Password (Google Account → Security → App Passwords)
    4. Add to GitHub Secrets:
         GMAIL_ADDRESS      = nozempic.coach@gmail.com
         GMAIL_APP_PASSWORD = xxxx xxxx xxxx xxxx  (16 chars, spaces OK)
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date
from dotenv import load_dotenv

load_dotenv()

GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
GROUP_NAME         = os.getenv("GROUP_NAME", "NOzempic")
SMTP_HOST          = "smtp.gmail.com"
SMTP_PORT          = 587


def _build_message(from_addr: str, to_addr: str, to_name: str,
                   subject: str, html: str) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{GROUP_NAME} Coach <{from_addr}>"
    msg["To"]      = f"{to_name} <{to_addr}>"
    msg.attach(MIMEText(html, "html"))
    return msg


def send_weekly_email(recipient_email: str, recipient_name: str,
                      subject: str, html_content: str, week_num: int) -> bool:
    """
    Send the weekly dispatch to a single participant via Gmail SMTP.

    Returns True if sent successfully, False otherwise.
    """
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("  ❌  GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set in environment.")
        return False

    msg = _build_message(GMAIL_ADDRESS, recipient_email,
                         recipient_name, subject, html_content)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, recipient_email, msg.as_string())
        print(f"  ✅  Sent to {recipient_name} ({recipient_email})")
        return True
    except smtplib.SMTPAuthenticationError:
        print("  ❌  Gmail authentication failed — check GMAIL_ADDRESS and GMAIL_APP_PASSWORD.")
        return False
    except Exception as e:
        print(f"  ❌  Failed to send to {recipient_name}: {e}")
        return False


def send_test_email(recipient_email: str, recipient_name: str,
                    html_content: str) -> bool:
    """Send a one-off test email to a single address."""
    subject = f"🏋️ NOzempic — Email Test ({date.today().strftime('%b %d')})"
    return send_weekly_email(recipient_email, recipient_name, subject, html_content, 0)


def send_all(participants: list[dict], emails: dict[str, str], week_num: int) -> dict:
    """
    Send weekly emails to all participants individually.

    Args:
        participants: List of participant configs from config.json.
        emails:       Dict mapping participant_id → rendered HTML.
        week_num:     Current week number.

    Returns:
        {"sent": [...ids], "failed": [...ids]}
    """
    subject = f"🏆 NOzempic Week {week_num} — {date.today().strftime('%b %d, %Y')}"
    results = {"sent": [], "failed": []}

    print(f"\n📧  Sending Week {week_num} emails from {GMAIL_ADDRESS}...")

    for p in participants:
        pid, name, email = p["id"], p["name"], p.get("email", "")

        if not email:
            print(f"  ⚠️   No email set for {name} — skipping")
            results["failed"].append(pid)
            continue

        html = emails.get(pid)
        if not html:
            print(f"  ⚠️   No HTML generated for {name} — skipping")
            results["failed"].append(pid)
            continue

        ok = send_weekly_email(email, name, subject, html, week_num)
        (results["sent"] if ok else results["failed"]).append(pid)

    print(f"\n  ✅  Sent: {len(results['sent'])}  |  ❌  Failed: {len(results['failed'])}")
    return results
