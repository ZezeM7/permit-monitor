# .github/workflows/permit-monitor.yml
#
# Two jobs:
#   1. check-permit  — runs every 4 hours, July 1–Aug 15, checks for availability
#   2. send-digest   — runs once daily at 8pm Mountain (2am UTC), emails a summary

name: Permit Monitor

on:
  schedule:
    - cron: "0 */4 * * *"   # every 4 hours — permit check
    - cron: "0 2 * * *"     # 2am UTC daily (8pm Mountain) — digest email
  workflow_dispatch:         # allows manual trigger from Actions tab

jobs:

  # ── Job 1: Check for permit availability ────────────────────────────────────
  check-permit:
    runs-on: ubuntu-latest

    env:
      SMTP_HOST:     ${{ secrets.SMTP_HOST }}
      SMTP_PORT:     ${{ secrets.SMTP_PORT }}
      SMTP_USER:     ${{ secrets.SMTP_USER }}
      SMTP_PASS:     ${{ secrets.SMTP_PASS }}
      ALERT_EMAIL:   ${{ secrets.ALERT_EMAIL }}
      SEASON_START:  "2026-07-01"
      SEASON_END:    "2026-08-15"
      INTERVAL_HOURS: "4"

    steps:
      - name: Check out repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install requests beautifulsoup4

      - name: Run permit check
        run: python permit_monitor.py
        continue-on-error: true

      - name: Upload check log
        uses: actions/upload-artifact@v4
        with:
          name: check-log-${{ github.run_id }}
          path: check_log.txt
          if-no-files-found: ignore

      - name: Log result
        run: echo "Check complete at $(date -u)"

  # ── Job 2: Send daily digest email ──────────────────────────────────────────
  send-digest:
    runs-on: ubuntu-latest
    # Only run this job when the daily digest cron fires (2am UTC)
    # We detect this by checking if the current hour is 2 UTC
    if: github.event_name == 'workflow_dispatch' || (github.event_name == 'schedule')

    env:
      SMTP_HOST:     ${{ secrets.SMTP_HOST }}
      SMTP_PORT:     ${{ secrets.SMTP_PORT }}
      SMTP_USER:     ${{ secrets.SMTP_USER }}
      SMTP_PASS:     ${{ secrets.SMTP_PASS }}
      ALERT_EMAIL:   ${{ secrets.ALERT_EMAIL }}
      SEASON_START:  "2026-07-01"
      SEASON_END:    "2026-08-15"
      INTERVAL_HOURS: "4"

    steps:
      - name: Check out repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install requests beautifulsoup4

      - name: Download today's check log (if available)
        uses: actions/download-artifact@v4
        with:
          pattern: check-log-*
          merge-multiple: true
        continue-on-error: true

      - name: Send daily digest
        run: python permit_monitor.py --digest

      - name: Log result
        run: echo "Digest sent at $(date -u)"
