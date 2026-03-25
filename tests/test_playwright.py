"""
Test script — opens a Playwright Chromium browser with a persistent profile,
logs into the Operator Google account (once), then navigates to a Meet link.
"""

import asyncio
import os
from playwright.async_api import async_playwright

MEET_URL = "https://meet.google.com/hgv-ewrt-hdz"

# Dedicated browser profile stored in the project folder
BROWSER_PROFILE = os.path.join(os.path.dirname(__file__), "browser_profile")


async def main():
    os.makedirs(BROWSER_PROFILE, exist_ok=True)

    async with async_playwright() as p:
        print("Launching browser...")
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=BROWSER_PROFILE,
            headless=False,
            args=["--use-fake-ui-for-media-stream"],  # auto-allow mic/camera prompts
        )

        page = browser.pages[0] if browser.pages else await browser.new_page()

        # Check if already logged in
        print("Checking login status...")
        await page.goto("https://myaccount.google.com", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        if "myaccount.google.com" in page.url and "signin" not in page.url:
            print("Already logged in.")
        else:
            print("Not logged in — please sign into the Operator Google account in the browser window.")
            print("Sign in at: https://accounts.google.com")
            await page.goto("https://accounts.google.com", wait_until="domcontentloaded")
            print("\nPress Enter here once you have fully signed in...")
            input()

        print(f"\nNavigating to Meet: {MEET_URL}")
        await page.goto(MEET_URL, wait_until="domcontentloaded", timeout=30000)

        print("Waiting for pre-join screen...")
        await page.wait_for_timeout(8000)

        # Dismiss notifications popup if present
        try:
            not_now = page.get_by_role("button", name="Not now")
            await not_now.wait_for(timeout=3000)
            print("Dismissing notifications popup...")
            await not_now.click()
            await page.wait_for_timeout(500)
        except Exception:
            print("No notifications popup.")

        await page.screenshot(path="/tmp/meet_prejoin.png")
        print("Pre-join screenshot saved to /tmp/meet_prejoin.png")

        # Click Join button — Meet uses either "Join now" or "Ask to join"
        print("Looking for Join button...")
        joined = False
        for label in ["Join now", "Ask to join"]:
            try:
                btn = page.get_by_role("button", name=label)
                await btn.wait_for(timeout=5000)
                print(f"Found button: '{label}' — clicking...")
                await btn.click()
                joined = True
                break
            except Exception:
                continue

        if not joined:
            print("Could not find Join button — taking screenshot for inspection.")
            await page.screenshot(path="/tmp/meet_nojoin.png")
        else:
            print("Clicked join. Waiting to confirm we're in the meeting...")
            await page.wait_for_timeout(6000)
            await page.screenshot(path="/tmp/meet_joined.png")
            print("Screenshot saved to /tmp/meet_joined.png")

        print("\nPress Enter to close the browser...")
        input()
        await browser.close()


asyncio.run(main())
