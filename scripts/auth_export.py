"""
One-time auth helper — runs on macOS with the local venv.

Opens Chrome using the same persistent browser profile that Operator uses
for meetings, navigates to accounts.google.com, and waits for you to log in.
Once logged in, cookies are stored in the browser profile (so the macOS
adapter is authenticated on next launch) AND exported to auth_state.json
(so Linux/Docker can reuse the session).

Usage:
    source venv/bin/activate
    python3 scripts/auth_export.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from playwright.sync_api import sync_playwright

BROWSER_PROFILE = os.path.join(os.path.dirname(__file__), "..", config.BROWSER_PROFILE_DIR)
OUTPUT = "auth_state.json"

print("Opening browser — log in as the Operator Google account.")
print("Press Enter here once you are fully logged in.")

with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context(
        user_data_dir=BROWSER_PROFILE,
        headless=False,
        executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        args=["--use-fake-ui-for-media-stream"],
    )
    page = browser.pages[0] if browser.pages else browser.new_page()
    page.goto("https://accounts.google.com")

    input("\n>>> Press Enter once you have finished logging in... ")

    # Visit Calendar to establish its service-specific session cookies.
    # Without this, the calendar poller gets redirected to login even
    # though Meet works fine (different Google service scopes).
    print("Establishing Calendar session...")
    page.goto("https://calendar.google.com/calendar/u/0/r/day",
              wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)

    browser.storage_state(path=OUTPUT)
    browser.close()

print(f"\nAuth state saved to {OUTPUT}")
print("Browser profile updated — Operator will use these cookies on next launch.")
print("Docker/Linux backup also saved to auth_state.json.")
