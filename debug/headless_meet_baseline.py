"""Headless Meet baseline — session 163.

Self-contained probe: launches Playwright's bundled Chromium in headless
mode (no profile, no auth, no flags beyond defaults), navigates to a
Meet URL, waits for client-side JS to settle, and dumps:

  - final URL (catches sign-in / accounts.google.com redirects)
  - page title (catches "Sign in - Google Accounts" walls)
  - first 600 chars of visible body text (catches "browser not supported"
    / "you appear to be a robot" walls without needing OCR)
  - full-page screenshot

Goal: see exactly what Google Meet renders for an unauthed, fingerprint-
default headless browser. This is the BASELINE before we layer guest
sign-in, UA spoof, or stealth-plugin equivalents.

Usage:
    source venv/bin/activate
    python debug/headless_meet_baseline.py <meet-url>
"""

import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT_DIR = Path(__file__).parent
SCREENSHOT = OUT_DIR / "headless_baseline.png"


def main(url: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()

        print(f"navigating to {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(8000)  # let client-side JS settle

        title = page.title()
        final_url = page.url
        body_text = page.evaluate("() => document.body.innerText || ''")[:600]

        print(f"  title:      {title!r}")
        print(f"  final_url:  {final_url}")
        print(f"  body[:600]: {body_text!r}")

        page.screenshot(path=str(SCREENSHOT), full_page=True)
        print(f"  screenshot: {SCREENSHOT}")

        browser.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python debug/headless_meet_baseline.py <meet-url>")
        sys.exit(1)
    main(sys.argv[1])
