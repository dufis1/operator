"""
PROBE A.2 — Headless Chrome + Google Meet (with stealth configuration)

Purpose: Test whether anti-detection measures allow headless Chrome to join
Google Meet without being blocked. Run this regardless of whether Probe A.1
passed or failed:
  - If A.1 PASSED: this confirms the production configuration works.
  - If A.1 FAILED: this tests whether stealth config fixes the problem.

What "stealth" means here:
  By default, headless Chrome leaves fingerprints that websites can detect:
    1. The User-Agent string contains "HeadlessChrome" — a dead giveaway.
    2. A JavaScript property called `navigator.webdriver` is set to `true`,
       which is the standard signal that a browser is being automated.
    3. The window size defaults to something tiny and unusual.
  This script overrides all three, making the browser look like a normal
  Chrome installation on a regular desktop.

How to run:
    python tests/probe_a2_stealth_meet.py

Where screenshots go:
    /tmp/probe_a2_step1_prejoin.png
    /tmp/probe_a2_step2_joined.png
    /tmp/probe_a2_step3_final.png

Reading the result:
    PASS — "Operator" appears as a participant in the meeting
    FAIL — browser got blocked, stuck, or redirected before joining
"""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

# ── Configuration ─────────────────────────────────────────────────────────────

MEET_URL = "https://meet.google.com/qga-gwge-gfi"

BROWSER_PROFILE = os.path.join(os.path.dirname(__file__), "..", "browser_profile")

# Use full Chromium binary — headless shell has incompatible profile format.
CHROMIUM_EXECUTABLE = os.path.expanduser(
    "~/Library/Caches/ms-playwright/chromium-1208/chrome-mac-arm64/"
    "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
)

SCREENSHOT_PREJOIN = "/tmp/probe_a2_step1_prejoin.png"
SCREENSHOT_JOINED = "/tmp/probe_a2_step2_joined.png"
SCREENSHOT_FINAL = "/tmp/probe_a2_step3_final.png"

# Realistic User-Agent string for Chrome 124 on macOS — no "HeadlessChrome" in it.
# A User-Agent is the string a browser sends to every website identifying itself.
# Headless Chrome's default includes "HeadlessChrome" which is an easy detection signal.
STEALTH_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# JavaScript that runs before any page script.
# It deletes the navigator.webdriver property, which is the main JS-level signal
# that a browser is being automated. Without this, websites can detect automation
# with a single line of JavaScript.
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
});
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str):
    print(f"  → {msg}")


def result(passed: bool, detail: str):
    print()
    print("=" * 60)
    if passed:
        print("RESULT: PASS ✓")
    else:
        print("RESULT: FAIL ✗")
    print(detail)
    print("=" * 60)
    sys.exit(0 if passed else 1)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print()
    print("PROBE A.2 — Headless Chrome + Google Meet (stealth config)")
    print()

    if not os.path.exists(BROWSER_PROFILE):
        result(
            False,
            "browser_profile/ folder not found. Run test_playwright.py first\n"
            "to sign into the Operator Google account, then re-run this probe.",
        )

    log("Browser profile found — login session will be reused.")
    log("Stealth config: custom User-Agent + navigator.webdriver removed + 1920x1080 window")
    log(f"Target meeting: {MEET_URL}")
    log("Launching headless Chrome...")

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=BROWSER_PROFILE,
            executable_path=CHROMIUM_EXECUTABLE,
            headless=True,
            user_agent=STEALTH_USER_AGENT,
            viewport={"width": 1920, "height": 1080},  # realistic desktop resolution
            args=[
                "--use-fake-ui-for-media-stream",
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",  # removes Chrome's own automation flag
                "--disable-infobars",
            ],
        )

        page = browser.pages[0] if browser.pages else await browser.new_page()

        # Inject the stealth JS before any page load
        await page.add_init_script(STEALTH_JS)

        # ── Step 1: Verify login ───────────────────────────────────────────────
        log("Verifying Google session...")
        await page.goto("https://myaccount.google.com", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        if "signin" in page.url or "accounts.google.com" in page.url:
            await browser.close()
            result(
                False,
                "Google session has expired. Run test_playwright.py to sign in again.",
            )

        log(f"Session active. URL: {page.url}")

        # ── Step 2: Navigate to Meet ───────────────────────────────────────────
        log("Navigating to Meet...")
        try:
            await page.goto(MEET_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            await browser.close()
            result(False, f"Navigation failed: {e}")

        log(f"URL: {page.url}")
        log(f"Title: {await page.title()}")
        await page.wait_for_timeout(8000)
        await page.screenshot(path=SCREENSHOT_PREJOIN)
        log(f"Screenshot: {SCREENSHOT_PREJOIN}")

        if "accounts.google.com" in page.url or "signin" in page.url:
            await browser.close()
            result(
                False,
                "Redirected to sign-in page — session rejected even with stealth config.\n"
                "This suggests Google is detecting the headless session at the account level,\n"
                "not just via browser fingerprinting.\n\n"
                "Recommendation: consider Recall.ai as the meeting-join layer.\n"
                f"Screenshot: {SCREENSHOT_PREJOIN}",
            )

        # ── Step 3: Dismiss notifications ─────────────────────────────────────
        try:
            not_now = page.get_by_role("button", name="Not now")
            await not_now.wait_for(timeout=3000)
            log("Dismissing notifications popup...")
            await not_now.click()
            await page.wait_for_timeout(500)
        except Exception:
            log("No notifications popup.")

        # ── Step 4: Join ───────────────────────────────────────────────────────
        log("Looking for Join button...")
        joined = False
        join_label_used = None

        for label in ["Join now", "Ask to join"]:
            try:
                btn = page.get_by_role("button", name=label)
                await btn.wait_for(timeout=5000)
                log(f"Found '{label}' — clicking...")
                await btn.click()
                joined = True
                join_label_used = label
                break
            except Exception:
                log(f"'{label}' not found.")

        if not joined:
            await page.screenshot(path=SCREENSHOT_JOINED)
            await browser.close()
            result(
                False,
                "No Join button found on the pre-join screen.\n"
                f"Screenshot for inspection: {SCREENSHOT_JOINED}\n\n"
                "This means Meet is rendering a different UI for headless Chrome\n"
                "even with stealth config — stronger mitigation needed, or use Recall.ai.",
            )

        log(f"Clicked '{join_label_used}'. Waiting...")
        await page.wait_for_timeout(6000)
        await page.screenshot(path=SCREENSHOT_JOINED)
        log(f"Screenshot: {SCREENSHOT_JOINED}")

        # ── Step 5: Hold in meeting ────────────────────────────────────────────
        log("Holding in meeting for 15 seconds...")
        await page.wait_for_timeout(15000)
        await page.screenshot(path=SCREENSHOT_FINAL)

        final_url = page.url
        final_title = await page.title()
        log(f"Final URL: {final_url}")
        log(f"Final title: {final_title}")

        await browser.close()

    # ── Outcome ───────────────────────────────────────────────────────────────
    if "meet.google.com" in final_url:
        result(
            True,
            f"Browser stayed on meet.google.com — join succeeded with stealth config.\n"
            f"Final title: {final_title}\n"
            f"Final URL: {final_url}\n\n"
            "Confirm visually: open the screenshot and verify 'Operator' appears\n"
            f"in the participant list: {SCREENSHOT_FINAL}\n\n"
            "PROBE A PASSES. Document the stealth configuration — it goes into\n"
            "the Docker adapter in Phase 3.",
        )
    else:
        result(
            False,
            f"Browser redirected away from meet.google.com.\n"
            f"Final URL: {final_url}\n\n"
            f"Screenshots:\n"
            f"  {SCREENSHOT_JOINED}\n"
            f"  {SCREENSHOT_FINAL}\n\n"
            "PROBE A FAILS. Contingency: use Recall.ai as the meeting-join layer.\n"
            "Stop here and discuss before proceeding with the refactor.",
        )


asyncio.run(main())
