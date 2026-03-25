"""
PROBE A.1 — Headless Chrome + Google Meet (no stealth config)

Purpose: Find out whether Google Meet blocks or allows a headless (invisible)
Chrome browser from joining a meeting. This is the simplest possible headless
test — no anti-detection measures. If this passes, the Docker approach is
already viable. If this fails, we run Probe A.2 with stealth config.

Prerequisites:
- You must have already run test_playwright.py at least once and signed into
  the Operator Google account. The login session is saved in browser_profile/
  and will be reused here. If you haven't done this yet, run test_playwright.py
  first, sign in, then come back here.

How to run:
    python tests/probe_a1_headless_meet.py

Where screenshots go:
    /tmp/probe_a1_step1_prejoin.png   — what the browser sees at the pre-join screen
    /tmp/probe_a1_step2_joined.png    — what the browser sees after clicking Join
    /tmp/probe_a1_step3_final.png     — final state 15 seconds after joining

Reading the result:
    PASS — "Operator" appears as a participant in the meeting
    FAIL — browser got blocked, stuck, or redirected before joining

    The script prints a clear PASS or FAIL at the end with a description of
    what happened and where to look in the screenshots.
"""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

# ── Configuration ─────────────────────────────────────────────────────────────

MEET_URL = "https://meet.google.com/ayj-awtp-fti"

# Re-use the same browser profile as test_playwright.py so the login session
# carries over. The path goes up one level from tests/ to the project root.
BROWSER_PROFILE = os.path.join(os.path.dirname(__file__), "..", "browser_profile")

# Use the full Chromium binary, not chrome-headless-shell. The headless shell has
# an incompatible profile format and can't read the browser_profile/ created by
# test_playwright.py. Both are installed by `playwright install chromium`.
CHROMIUM_EXECUTABLE = os.path.expanduser(
    "~/Library/Caches/ms-playwright/chromium-1208/chrome-mac-arm64/"
    "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
)

SCREENSHOT_PREJOIN = "/tmp/probe_a1_step1_prejoin.png"
SCREENSHOT_JOINED = "/tmp/probe_a1_step2_joined.png"
SCREENSHOT_FINAL = "/tmp/probe_a1_step3_final.png"


# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str):
    """Print a timestamped step so the output is easy to follow."""
    print(f"  → {msg}")


def result(passed: bool, detail: str):
    """Print a clear final result and exit with the appropriate code."""
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
    print("PROBE A.1 — Headless Chrome + Google Meet")
    print("(No stealth config — baseline test)")
    print()

    # Confirm browser profile exists (i.e., the user has logged in before)
    if not os.path.exists(BROWSER_PROFILE):
        result(
            False,
            "browser_profile/ folder not found. You need to run test_playwright.py\n"
            "first, sign into the Operator Google account in the browser window,\n"
            "then re-run this probe.",
        )

    log("Browser profile found — login session will be reused.")
    log(f"Target meeting: {MEET_URL}")
    log("Launching headless Chrome (no window will appear)...")

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=BROWSER_PROFILE,
            executable_path=CHROMIUM_EXECUTABLE,
            headless=True,  # ← the only change from test_playwright.py
            args=[
                "--use-fake-ui-for-media-stream",  # auto-allow mic/camera
                "--no-sandbox",                    # required in many Linux/container environments
            ],
        )

        page = browser.pages[0] if browser.pages else await browser.new_page()

        # ── Step 1: Verify login is still active ──────────────────────────────
        log("Checking whether the Google session is still active...")
        await page.goto("https://myaccount.google.com", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        if "signin" in page.url or "accounts.google.com" in page.url:
            await browser.close()
            result(
                False,
                "The Google session has expired or was never set up.\n"
                "Run test_playwright.py first (headless=False) to sign in,\n"
                "then re-run this probe.",
            )

        log(f"Session active. Current URL: {page.url}")

        # ── Step 2: Navigate to the Meet link ─────────────────────────────────
        log(f"Navigating to Meet...")
        try:
            await page.goto(MEET_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            await browser.close()
            result(False, f"Navigation to Meet URL failed: {e}")

        log(f"Page URL after navigation: {page.url}")
        log(f"Page title: {await page.title()}")
        await page.wait_for_timeout(8000)
        await page.screenshot(path=SCREENSHOT_PREJOIN)
        log(f"Screenshot saved: {SCREENSHOT_PREJOIN}")

        # Check for redirect to sign-in — a signal of session detection
        if "accounts.google.com" in page.url or "signin" in page.url:
            await browser.close()
            result(
                False,
                "Google redirected to a sign-in page after navigating to Meet.\n"
                "This typically means the session cookie was rejected in headless mode.\n"
                f"Check the screenshot: {SCREENSHOT_PREJOIN}\n"
                "Next step: run Probe A.2 with stealth config.",
            )

        # ── Step 3: Dismiss notifications popup if present ────────────────────
        try:
            not_now = page.get_by_role("button", name="Not now")
            await not_now.wait_for(timeout=3000)
            log("Dismissing notifications popup...")
            await not_now.click()
            await page.wait_for_timeout(500)
        except Exception:
            log("No notifications popup found (expected).")

        # ── Step 4: Look for the Join button ──────────────────────────────────
        log("Looking for 'Join now' or 'Ask to join' button...")
        joined = False
        join_label_used = None

        for label in ["Join now", "Ask to join"]:
            try:
                btn = page.get_by_role("button", name=label)
                await btn.wait_for(timeout=5000)
                log(f"Found button: '{label}' — clicking...")
                await btn.click()
                joined = True
                join_label_used = label
                break
            except Exception:
                log(f"Button '{label}' not found.")

        if not joined:
            await page.screenshot(path=SCREENSHOT_JOINED)
            await browser.close()
            result(
                False,
                "Could not find a Join button on the Meet pre-join screen.\n"
                "This may mean:\n"
                "  • Google is showing a CAPTCHA or access-denied screen\n"
                "  • The pre-join UI changed and the button labels are different\n"
                "  • Meet detected headless Chrome and refused to render the UI\n"
                f"Check the screenshot for visual confirmation: {SCREENSHOT_JOINED}",
            )

        log(f"Clicked '{join_label_used}'. Waiting to confirm join...")
        await page.wait_for_timeout(6000)
        await page.screenshot(path=SCREENSHOT_JOINED)
        log(f"Screenshot saved: {SCREENSHOT_JOINED}")

        # ── Step 5: Stay in the meeting and take a final screenshot ───────────
        log("Staying in meeting for 15 seconds to confirm stable connection...")
        await page.wait_for_timeout(15000)
        await page.screenshot(path=SCREENSHOT_FINAL)
        log(f"Final screenshot saved: {SCREENSHOT_FINAL}")

        final_url = page.url
        final_title = await page.title()
        log(f"Final URL: {final_url}")
        log(f"Final title: {final_title}")

        await browser.close()

    # ── Determine outcome ─────────────────────────────────────────────────────
    # A successful join lands on meet.google.com/<room-code> and stays there.
    # A kicked/blocked browser typically redirects elsewhere.
    if "meet.google.com" in final_url:
        result(
            True,
            f"Browser stayed on meet.google.com after joining.\n"
            f"Final title: {final_title}\n"
            f"Final URL: {final_url}\n\n"
            "Confirm visually: open the screenshot and verify 'Operator' appears\n"
            f"in the participant list: {SCREENSHOT_FINAL}\n\n"
            "If confirmed, Probe A passes. Proceed to Probe A.2 to test the\n"
            "stealth config that will be used in production.",
        )
    else:
        result(
            False,
            f"Browser was redirected away from meet.google.com after attempting to join.\n"
            f"Final URL: {final_url}\n"
            f"Final title: {final_title}\n\n"
            f"Check the screenshots:\n"
            f"  {SCREENSHOT_JOINED}\n"
            f"  {SCREENSHOT_FINAL}\n\n"
            "Next step: run Probe A.2 with stealth config.",
        )


asyncio.run(main())
