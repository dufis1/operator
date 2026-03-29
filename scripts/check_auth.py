"""
Opens the Operator browser profile to myaccount.google.com and takes a screenshot.
If authenticated, you'll see the Google account page. If not, you'll see a sign-in page.

Usage:
    source venv/bin/activate
    python scripts/check_auth.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config

BROWSER_PROFILE = os.path.join(os.path.dirname(__file__), "..", config.BROWSER_PROFILE_DIR)
OUTPUT = os.path.join(os.path.dirname(__file__), "..", "debug", "auth_check.png")

from playwright.sync_api import sync_playwright

os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context(
        user_data_dir=BROWSER_PROFILE,
        headless=False,
        executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        args=["--headless=new"],
    )
    page = browser.pages[0] if browser.pages else browser.new_page()
    page.goto("https://mail.google.com", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)
    page.screenshot(path=OUTPUT, full_page=True)
    browser.close()

print(f"Screenshot saved to {OUTPUT}")
