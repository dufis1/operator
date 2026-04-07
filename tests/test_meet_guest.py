"""
Test: Can a headless browser join a Google Meet as a guest (no auth)?
Uses Playwright in headed mode so you can see the browser.
"""
import asyncio
from playwright.async_api import async_playwright

MEET_URL = "https://meet.google.com/iwe-kdeo-zsq"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--use-fake-ui-for-media-stream",   # auto-allow mic/camera prompts
                "--use-fake-device-for-media-stream",
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = await browser.new_context(
            permissions=["camera", "microphone"],
        )

        page = await context.new_page()

        print(f"Navigating to {MEET_URL} ...")
        await page.goto(MEET_URL, wait_until="domcontentloaded")

        # Give the page time to render the join UI
        await page.wait_for_timeout(3000)

        # Look for a "Join as guest" or "Ask to join" button
        guest_selectors = [
            "text=Join as a guest",
            "text=Ask to join",
            "text=Join now",
            "[data-promo-anchor-id='join-button']",
            "button:has-text('Join')",
        ]

        for sel in guest_selectors:
            btn = page.locator(sel).first
            try:
                visible = await btn.is_visible(timeout=2000)
            except Exception:
                visible = False
            if visible:
                print(f"Found button: {sel}")
                break

        print("Page title:", await page.title())
        print("\nBrowser is open — inspect the window. Press Ctrl+C to exit.")

        # Keep browser open until user kills the script
        await asyncio.Event().wait()

    await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
