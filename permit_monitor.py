#!/usr/bin/env python3
"""
Recreation.gov Permit Monitor
Checks permit #234624 (4 River Lottery) for availability
and sends an email alert when permits are found.

Usage:
  python permit_monitor.py                  # run once
  python permit_monitor.py --loop           # run on repeat (uses INTERVAL_HOURS)
  python permit_monitor.py --test-email     # send a test alert email
  python permit_monitor.py --digest         # send daily digest email and exit

Environment variables (required for email alerts):
  SMTP_HOST       e.g. smtp.gmail.com
  SMTP_PORT       e.g. 587
  SMTP_USER       your sending email address
  SMTP_PASS       your email password or app password
  ALERT_EMAIL     address to receive alerts (can be same as SMTP_USER)

Optional environment variables:
  SEASON_START    e.g. 2026-07-01  (only alert within this date window)
  SEASON_END      e.g. 2026-08-15
  KEYWORDS        comma-separated, e.g. "Rogue,Selway,October"
  INTERVAL_HOURS  how often to check when running in --loop mode (default: 4)
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

PERMIT_URL  = "https://www.recreation.gov/permits/234624"
PERMIT_NAME = "4 River Lottery (permit #234624)"
LOG_FILE    = "check_log.txt"

# Email settings — pulled from env, or set directly below as fallback
SMTP_HOST   = os.environ.get("SMTP_HOST",   "smtp.gmail.com")
SMTP_PORT   = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER   = os.environ.get("SMTP_USER",   "")
SMTP_PASS   = os.environ.get("SMTP_PASS",   "")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", SMTP_USER)

# Season window — only alert if today falls within this range
SEASON_START = os.environ.get("SEASON_START", "")   # "YYYY-MM-DD"
SEASON_END   = os.environ.get("SEASON_END",   "")   # "YYYY-MM-DD"

# Optional keyword filter
KEYWORDS_RAW = os.environ.get("KEYWORDS", "")
KEYWORDS = [k.strip().lower() for k in KEYWORDS_RAW.split(",") if k.strip()]

# Check interval for --loop mode (hours)
INTERVAL_HOURS = float(os.environ.get("INTERVAL_HOURS", "4"))

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

# Strings that mean nothing is available
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
    """Parse the page HTML and return an availability result dict."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ").lower()

    matched_unavailable = [s for s in UNAVAILABLE_SIGNALS if s in text]
    matched_available   = [s for s in AVAILABILITY_SIGNALS if s in text]
    matched_keywords    = [k for k in KEYWORDS if k in text] if KEYWORDS else []

    available = False
    if matched_available and not matched_unavailable:
        if not KEYWORDS or matched_keywords:
            available = True

    excerpt = " ".join(text.split()[:120])

    return {
        "available": available,
        "matched_signals": matched_available,
        "matched_keywords": matched_keywords,
        "unavailable_signals": matched_unavailable,
        "excerpt": excerpt,
    }


def log_check_result(available: bool, result: dict, fetch_failed: bool = False):
    """Append a single check result to the daily log file."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    if fetch_failed:
        entry = f"{timestamp} | FETCH FAILED | could not reach recreation.gov\n"
    elif available:
        entry = f"{timestamp} | PERMIT FOUND | signals: {result.get('matched_signals', [])}\n"
    else:
        signals = result.get("unavailable_signals", [])
        if signals:
            entry = f"{timestamp} | all clear | page confirmed no availability ({signals})\n"
        else:
            entry = f"{timestamp} | all clear | no availability signals detected\n"

    with open(LOG_FILE, "a") as f:
        f.write(entry)


def send_email(subject: str, body: str) -> bool:
    """Send an email. Returns True on success."""
    if not SMTP_USER or not SMTP_PASS:
        print(f"\n{subject}\n{'-'*40}\n{body}\n")
        return True

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
        log.info("Email sent to %s | subject: %s", ALERT_EMAIL, subject)
        return True
    except smtplib.SMTPAuthenticationError:
        log.error("SMTP authentication failed — check SMTP_USER and SMTP_PASS.")
    except smtplib.SMTPException as e:
        log.error("SMTP error: %s", e)
    return False


def send_alert(result: dict) -> bool:
    """Send an immediate permit availability alert."""
    subject = f"PERMIT AVAILABLE — {PERMIT_NAME}"
    body_lines = [
        f"A permit may be available for: {PERMIT_NAME}",
        f"",
        f"Book it now: {PERMIT_URL}",
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
        f"--- Sent by permit_monitor.py at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')} ---",
    ]
    return send_email(subject, "\n".join(body_lines))


def send_digest() -> bool:
    """Read todays log and email a daily summary report."""
    today = datetime.utcnow().strftime("%Y-%m-%d")

    if not os.path.exists(LOG_FILE):
        lines = []
    else:
        with open(LOG_FILE, "r") as f:
            all_lines = f.readlines()
        lines = [l.strip() for l in all_lines if today in l]

    total        = len(lines)
    found        = sum(1 for l in lines if "PERMIT FOUND" in l)
    clear        = sum(1 for l in lines if "all clear" in l)
    fetch_failed = sum(1 for l in lines if "FETCH FAILED" in l)

    subject = f"Permit Monitor Daily Report — {today}"
    body_lines = [
        f"Daily check summary for: {PERMIT_NAME}",
        f"Date: {today} (UTC)",
        f"",
        f"-----------------------------",
        f"  Total checks run:    {total}",
        f"  All clear:           {clear}",
        f"  Permits found:       {found}",
        f"  Fetch errors:        {fetch_failed}",
        f"-----------------------------",
        f"",
    ]

    if found > 0:
        body_lines.append("*** PERMITS WERE DETECTED TODAY — check your inbox for the alert email! ***")
        body_lines.append(f"    {PERMIT_URL}")
        body_lines.append("")

    if total == 0:
        body_lines.append("No checks ran today. This may mean the season window has not started yet,")
        body_lines.append("or there was an issue with the scheduled workflow.")
    else:
        body_lines.append("--- Full check log for today ---")
        body_lines += lines if lines else ["(no entries)"]

    body_lines += [
        "",
        f"Permit page: {PERMIT_URL}",
        f"Season window: {SEASON_START or 'any'} to {SEASON_END or 'any'}",
        f"Check interval: every {INTERVAL_HOURS} hour(s)",
        f"",
        f"--- Sent by permit_monitor.py ---",
    ]

    return send_email(subject, "\n".join(body_lines))


def run_check() -> bool:
    """Run a single permit check. Returns True if permits were found."""
    log.info("Checking %s ...", PERMIT_URL)

    if not in_season_window():
        return False

    html = fetch_page(PERMIT_URL)
    if html is None:
        log.warning("Could not fetch page — will retry next cycle.")
        log_check_result(False, {}, fetch_failed=True)
        return False

    result = parse_availability(html)

    if result["available"]:
        log.info("PERMIT AVAILABLE — signals: %s", result["matched_signals"])
        if result["matched_keywords"]:
            log.info("    Keyword matches: %s", result["matched_keywords"])
        log_check_result(True, result)
        send_alert(result)
        return True
    else:
        if result["unavailable_signals"]:
            log.info("No permits — page shows: %s", result["unavailable_signals"])
        else:
            log.info("No availability signals found on page.")
        log_check_result(False, result)
        return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Recreation.gov permit availability monitor")
    parser.add_argument("--loop",       action="store_true", help="Run continuously on a schedule")
    parser.add_argument("--test-email", action="store_true", help="Send a test alert email and exit")
    parser.add_argument("--digest",     action="store_true", help="Send daily digest email and exit")
    args = parser.parse_args()

    if args.test_email:
        log.info("Sending test alert email to %s ...", ALERT_EMAIL)
        send_alert({
            "matched_signals": ["test signal"],
            "matched_keywords": [],
            "excerpt": "This is a test alert from permit_monitor.py.",
        })
        sys.exit(0)

    if args.digest:
        log.info("Sending daily digest to %s ...", ALERT_EMAIL)
        send_digest()
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
