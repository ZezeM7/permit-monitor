#!/usr/bin/env python3
"""
Recreation.gov Permit Monitor
Checks permit #234624 (4 River Lottery) for availability
and sends an email alert when permits are found.

Usage:
  python permit_monitor.py                  # run once
  python permit_monitor.py --loop           # run on repeat (uses INTERVAL_HOURS)
  python permit_monitor.py --test-email     # send a test alert email

Environment variables (required for email alerts):
  SMTP_HOST       e.g. smtp.gmail.com
  SMTP_PORT       e.g. 587
  SMTP_USER       your sending email address
  SMTP_PASS       your email password or app password
  ALERT_EMAIL     address to receive alerts (can be same as SMTP_USER)

Optional environment variables:
  SEASON_START    e.g. 2025-03-01  (only alert within this date window)
  SEASON_END      e.g. 2025-10-31
  KEYWORDS        comma-separated, e.g. "Rogue,Selway,October"
  INTERVAL_HOURS  how often to check when running in --loop mode (default: 3)
"""

import os
import sys
import time
import smtplib
import logging
import argparse
from datetime import datetime, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration — override via environment variables or edit directly here
# ---------------------------------------------------------------------------

PERMIT_URL = "https://www.recreation.gov/permits/234624"
PERMIT_NAME = "4 River Lottery (permit #234624)"

# Email settings — pulled from env, or set directly below as fallback
SMTP_HOST   = os.environ.get("SMTP_HOST",   "smtp.gmail.com")
SMTP_PORT   = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER   = os.environ.get("SMTP_USER",   "")   # your Gmail or SMTP address
SMTP_PASS   = os.environ.get("SMTP_PASS",   "")   # app password recommended
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", SMTP_USER)

# Season window — only alert if today falls within this range (leave blank to always alert)
SEASON_START = os.environ.get("SEASON_START", "")   # "YYYY-MM-DD"
SEASON_END   = os.environ.get("SEASON_END",   "")   # "YYYY-MM-DD"

# Optional keyword filter — only alert if ANY keyword appears on the page
# Leave empty to alert on any availability signal
KEYWORDS_RAW = os.environ.get("KEYWORDS", "")
KEYWORDS = [k.strip().lower() for k in KEYWORDS_RAW.split(",") if k.strip()]

# Check interval for --loop mode (hours)
INTERVAL_HOURS = float(os.environ.get("INTERVAL_HOURS", "3"))

# Strings that suggest permits ARE available
AVAILABILITY_SIGNALS = [
    "available",
    "book now",
    "add to cart",
    "reserve",
    "select dates",
    "lottery open",
    "apply now",
]

# Strings that mean the page is loaded but nothing is available
UNAVAILABLE_SIGNALS = [
    "sold out",
    "no permits available",
    "unavailable",
    "not available",
    "lottery closed",
    "no availability",
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("permit_monitor")

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def in_season_window() -> bool:
    """Return True if today falls within the configured season window."""
    if not SEASON_START and not SEASON_END:
        return True
    today = date.today()
    if SEASON_START:
        start = date.fromisoformat(SEASON_START)
        if today < start:
            log.info("Outside season window — before %s. Skipping check.", SEASON_START)
            return False
    if SEASON_END:
        end = date.fromisoformat(SEASON_END)
        if today > end:
            log.info("Outside season window — after %s. Skipping check.", SEASON_END)
            return False
    return True


def fetch_page(url: str) -> str | None:
    """Fetch the permit page and return its text content."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.HTTPError as e:
        log.error("HTTP error fetching page: %s", e)
    except requests.exceptions.ConnectionError:
        log.error("Connection failed — check your internet connection.")
    except requests.exceptions.Timeout:
        log.error("Request timed out.")
    except requests.exceptions.RequestException as e:
        log.error("Request error: %s", e)
    return None


def parse_availability(html: str) -> dict:
    """
    Parse the page HTML and return a result dict:
      available (bool), matched_signals (list), matched_keywords (list), excerpt (str)
    """
    soup = BeautifulSoup(html, "html.parser")
    # Remove script/style noise
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ").lower()

    matched_unavailable = [s for s in UNAVAILABLE_SIGNALS if s in text]
    matched_available   = [s for s in AVAILABILITY_SIGNALS if s in text]
    matched_keywords    = [k for k in KEYWORDS if k in text] if KEYWORDS else []

    # Availability logic:
    #   1. If unavailable signals present → not available
    #   2. If available signals present AND (no keyword filter OR keyword matched) → available
    available = False
    if matched_available and not matched_unavailable:
        if not KEYWORDS or matched_keywords:
            available = True

    # Grab a short snippet of visible text for the email body
    excerpt = " ".join(text.split()[:120])

    return {
        "available": available,
        "matched_signals": matched_available,
        "matched_keywords": matched_keywords,
        "unavailable_signals": matched_unavailable,
        "excerpt": excerpt,
    }


def send_alert(result: dict) -> bool:
    """Send an email alert. Returns True on success."""
    if not SMTP_USER or not SMTP_PASS:
        log.warning("Email not configured — printing alert to console instead.")
        print("\n" + "=" * 60)
        print("🎉  PERMIT AVAILABLE!")
        print(f"    {PERMIT_NAME}")
        print(f"    URL: {PERMIT_URL}")
        print(f"    Signals: {result['matched_signals']}")
        if result["matched_keywords"]:
            print(f"    Keywords matched: {result['matched_keywords']}")
        print("=" * 60 + "\n")
        return True

    subject = f"🎉 Permit available — {PERMIT_NAME}"
    body_lines = [
        f"A permit may be available for: {PERMIT_NAME}",
        f"",
        f"Check it now: {PERMIT_URL}",
        f"",
        f"Detected signals: {', '.join(result['matched_signals'])}",
    ]
    if result["matched_keywords"]:
        body_lines.append(f"Keyword matches: {', '.join(result['matched_keywords'])}")
    body_lines += [
        f"",
        f"Page excerpt:",
        result["excerpt"],
        f"",
        f"--- Sent by permit_monitor.py at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---",
    ]
    body = "\n".join(body_lines)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = ALERT_EMAIL
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, ALERT_EMAIL, msg.as_string())
        log.info("Alert email sent to %s", ALERT_EMAIL)
        return True
    except smtplib.SMTPAuthenticationError:
        log.error("SMTP authentication failed — check SMTP_USER and SMTP_PASS.")
    except smtplib.SMTPException as e:
        log.error("SMTP error: %s", e)
    return False


def run_check() -> bool:
    """
    Run a single permit check. Returns True if permits were found.
    """
    log.info("Checking %s ...", PERMIT_URL)

    if not in_season_window():
        return False

    html = fetch_page(PERMIT_URL)
    if html is None:
        log.warning("Could not fetch page — will retry next cycle.")
        return False

    result = parse_availability(html)

    if True:
        log.info("✅  PERMIT AVAILABLE — signals: %s", result["matched_signals"])
        if result["matched_keywords"]:
            log.info("    Keyword matches: %s", result["matched_keywords"])
        send_alert(result)
        return True
    else:
        if result["unavailable_signals"]:
            log.info("❌  No permits — page shows: %s", result["unavailable_signals"])
        else:
            log.info("⏳  No availability signals found on page.")
        return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Recreation.gov permit availability monitor")
    parser.add_argument("--loop",       action="store_true", help="Run continuously on a schedule")
    parser.add_argument("--test-email", action="store_true", help="Send a test alert email and exit")
    args = parser.parse_args()

    if args.test_email:
        log.info("Sending test email to %s ...", ALERT_EMAIL)
        send_alert({
            "matched_signals": ["test signal"],
            "matched_keywords": [],
            "excerpt": "This is a test alert from permit_monitor.py.",
        })
        sys.exit(0)

    if args.loop:
        log.info(
            "Starting permit monitor — checking every %.1f hour(s). Press Ctrl+C to stop.",
            INTERVAL_HOURS,
        )
        while True:
            try:
                run_check()
            except KeyboardInterrupt:
                log.info("Stopped by user.")
                sys.exit(0)
            except Exception as e:
                log.error("Unexpected error: %s — will retry next cycle.", e)
            sleep_seconds = INTERVAL_HOURS * 3600
            next_run = datetime.fromtimestamp(time.time() + sleep_seconds)
            log.info("Next check at %s", next_run.strftime("%Y-%m-%d %H:%M:%S"))
            time.sleep(sleep_seconds)
    else:
        found = run_check()
        sys.exit(0 if found else 1)


if __name__ == "__main__":
    main()
