"""
Spike: Dump the inner HTML structure of a chat message div.

Usage:
    source venv/bin/activate
    python scripts/spike_message_dom.py https://meet.google.com/xxx-yyyy-zzz

Send at least one chat message before running, or send one while the bot is in.
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTH_STATE = os.path.join(BASE, "auth_state.json")


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/spike_message_dom.py <meeting-url>")
        sys.exit(1)

    meeting_url = sys.argv[1]

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--use-fake-ui-for-media-stream", "--headless=new"],
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        )
        context = browser.new_context(
            storage_state=AUTH_STATE,
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        page.goto(meeting_url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_selector(
                'button:has-text("Join now"), button:has-text("Ask to join")',
                timeout=15000,
            )
        except Exception:
            pass

        # Turn off camera
        try:
            page.get_by_role("button", name="Turn off camera").click()
        except Exception:
            pass

        # Join
        for label in ["Join now", "Ask to join", "Switch here"]:
            try:
                btn = page.get_by_role("button", name=label)
                btn.wait_for(timeout=5000)
                btn.click()
                print(f"Clicked '{label}'")
                break
            except Exception:
                continue

        # Wait for in-meeting
        try:
            page.wait_for_selector('button[aria-label*="Leave call"]', timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(3000)

        # Open chat
        try:
            chat_btn = page.get_by_role("button", name="Chat with everyone")
            chat_btn.wait_for(timeout=3000)
            chat_btn.click()
            page.wait_for_timeout(1000)
        except Exception as e:
            print(f"Could not open chat: {e}")

        # Wait for messages — poll for 15 seconds
        print("Waiting for chat messages (15s)... send one now if chat is empty.")
        for i in range(15):
            msgs = page.locator("div[data-message-id]")
            if msgs.count() > 0:
                break
            time.sleep(1)

        count = msgs.count()
        print(f"\nFound {count} message div(s).\n")

        for i in range(min(count, 3)):
            el = msgs.nth(i)
            msg_id = el.get_attribute("data-message-id")
            inner = el.evaluate("el => el.innerHTML")
            print(f"=== Message {i} (id={msg_id}) ===")
            print(inner)
            print()

        # Leave
        try:
            page.get_by_role("button", name="Leave call").click()
            page.wait_for_timeout(1000)
        except Exception:
            pass
        browser.close()
        print("Done.")


if __name__ == "__main__":
    main()
