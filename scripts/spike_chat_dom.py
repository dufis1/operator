"""
Spike: Inspect Google Meet chat DOM structure.

Joins a meeting, opens the chat panel, and dumps the DOM elements
we need for building chat read/write — button labels, ARIA roles,
message container structure, and input box attributes.

Usage:
    source venv/bin/activate
    python scripts/spike_chat_dom.py https://meet.google.com/xxx-yyyy-zzz

Requires: valid auth_state.json (run scripts/auth_export.py if expired).
"""
import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTH_STATE = os.path.join(BASE, "auth_state.json")
DEBUG_DIR = os.path.join(BASE, "debug")


def dump_chat_dom(page):
    """Open the chat panel and dump its DOM structure."""

    print("\n=== STEP 1: Find the chat button ===")
    # Try known candidates for the chat toggle button
    chat_button_candidates = [
        ("ARIA: Open chat", 'button', 'Open chat'),
        ("ARIA: Chat with everyone", 'button', 'Chat with everyone'),
        ("ARIA: chat", 'button', 'chat'),
    ]
    chat_opened = False
    for desc, role, name in chat_button_candidates:
        try:
            btn = page.get_by_role(role, name=name)
            if btn.count() > 0:
                print(f"  FOUND: {desc}")
                print(f"    visible={btn.first.is_visible()}")
                # Dump the outer HTML for inspection
                html = btn.first.evaluate("el => el.outerHTML")
                print(f"    HTML: {html[:300]}")
                btn.first.click()
                page.wait_for_timeout(1000)
                chat_opened = True
                break
        except Exception as e:
            print(f"  not found: {desc} ({e})")

    if not chat_opened:
        # Fallback: find ALL buttons and list them so we can identify the right one
        print("\n  Chat button not found by name. Listing all buttons:")
        buttons = page.query_selector_all("button")
        for i, b in enumerate(buttons):
            try:
                label = b.get_attribute("aria-label") or ""
                text = b.inner_text()[:60] if b.is_visible() else "(hidden)"
                if any(kw in (label + text).lower() for kw in ["chat", "message", "everyone"]):
                    html = b.evaluate("el => el.outerHTML")
                    print(f"    button[{i}] aria-label={label!r} text={text!r}")
                    print(f"      HTML: {html[:300]}")
            except Exception:
                pass

    print("\n=== STEP 2: Inspect the chat panel ===")
    page.wait_for_timeout(1000)

    # Look for the chat message container
    print("\n  -- Looking for chat message container --")
    container_selectors = [
        ("role=log", page.get_by_role("log")),
        ("role=list (chat)", page.get_by_role("list")),
        ("[aria-label*='chat' i]", page.locator("[aria-label*='chat' i]")),
        ("[aria-label*='message' i]", page.locator("[aria-label*='message' i]")),
    ]
    for desc, loc in container_selectors:
        try:
            count = loc.count()
            if count > 0:
                print(f"  FOUND: {desc} (count={count})")
                for i in range(min(count, 3)):
                    html = loc.nth(i).evaluate("el => el.outerHTML.substring(0, 500)")
                    print(f"    [{i}]: {html}")
        except Exception as e:
            print(f"  not found: {desc} ({e})")

    print("\n  -- Looking for chat input box --")
    input_candidates = [
        ("textbox: Send a message to everyone", page.get_by_role("textbox", name="Send a message to everyone")),
        ("textbox: Send a message", page.get_by_role("textbox", name="Send a message")),
        ("textbox (any)", page.get_by_role("textbox")),
        ("[aria-label*='Send a message' i]", page.locator("[aria-label*='Send a message' i]")),
        ("div[contenteditable]", page.locator("div[contenteditable='true']")),
    ]
    for desc, loc in input_candidates:
        try:
            count = loc.count()
            if count > 0:
                print(f"  FOUND: {desc} (count={count})")
                for i in range(min(count, 3)):
                    html = loc.nth(i).evaluate("el => el.outerHTML.substring(0, 500)")
                    visible = loc.nth(i).is_visible()
                    print(f"    [{i}] visible={visible}: {html}")
        except Exception as e:
            print(f"  not found: {desc} ({e})")

    print("\n  -- Looking for send button --")
    send_candidates = [
        ("button: Send", page.get_by_role("button", name="Send")),
        ("button: Send a message", page.get_by_role("button", name="Send a message")),
        ("[aria-label*='Send' i]", page.locator("button[aria-label*='Send' i]")),
    ]
    for desc, loc in send_candidates:
        try:
            count = loc.count()
            if count > 0:
                print(f"  FOUND: {desc} (count={count})")
                for i in range(min(count, 3)):
                    html = loc.nth(i).evaluate("el => el.outerHTML.substring(0, 500)")
                    print(f"    [{i}]: {html}")
        except Exception as e:
            print(f"  not found: {desc} ({e})")

    print("\n=== STEP 3: Inspect existing chat messages (if any) ===")
    # Dump all visible text in the chat panel area
    # This helps us understand the message structure
    try:
        # Get the chat panel content via a broad approach
        chat_elements = page.evaluate("""() => {
            // Find elements that look like chat messages
            const results = [];
            // Look for elements with data-message-id or similar
            document.querySelectorAll('[data-message-id], [data-sender-id]').forEach(el => {
                results.push({
                    tag: el.tagName,
                    id: el.id,
                    attrs: Object.fromEntries([...el.attributes].map(a => [a.name, a.value]).slice(0, 10)),
                    text: el.innerText?.substring(0, 200),
                    html: el.outerHTML?.substring(0, 300)
                });
            });
            return results;
        }""")
        if chat_elements:
            print(f"  Found {len(chat_elements)} message-like elements:")
            for el in chat_elements[:5]:
                print(f"    {json.dumps(el, indent=6)}")
        else:
            print("  No data-message-id/data-sender-id elements found.")
    except Exception as e:
        print(f"  Error scanning messages: {e}")

    # Screenshot for visual reference
    os.makedirs(DEBUG_DIR, exist_ok=True)
    screenshot_path = os.path.join(DEBUG_DIR, "chat_spike.png")
    try:
        page.screenshot(path=screenshot_path, full_page=True)
        print(f"\n  Screenshot saved: {screenshot_path}")
    except Exception as e:
        print(f"\n  Screenshot failed: {e}")

    # Also dump full HTML for offline inspection
    html_path = os.path.join(DEBUG_DIR, "chat_spike.html")
    try:
        with open(html_path, "w") as f:
            f.write(page.content())
        print(f"  Full HTML saved: {html_path}")
    except Exception as e:
        print(f"  HTML dump failed: {e}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/spike_chat_dom.py <meeting-url>")
        sys.exit(1)

    meeting_url = sys.argv[1]
    print(f"Meeting URL: {meeting_url}")
    print(f"Auth state: {AUTH_STATE}")

    if not os.path.isfile(AUTH_STATE):
        print("ERROR: auth_state.json not found. Run scripts/auth_export.py first.")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--use-fake-ui-for-media-stream",
                "--headless=new",
            ],
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        )
        context = browser.new_context(
            storage_state=AUTH_STATE,
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        print("\nNavigating to meeting...")
        page.goto(meeting_url, wait_until="domcontentloaded", timeout=30000)

        # Wait for pre-join screen
        try:
            page.wait_for_selector(
                'button:has-text("Join now"), '
                'button:has-text("Ask to join")',
                timeout=15000,
            )
        except Exception:
            print("WARNING: no join button detected after 15s")

        # Turn off camera
        try:
            cam_btn = page.get_by_role("button", name="Turn off camera")
            cam_btn.wait_for(timeout=3000)
            cam_btn.click()
        except Exception:
            pass

        # Join
        joined = False
        for label in ["Join now", "Ask to join", "Switch here"]:
            try:
                btn = page.get_by_role("button", name=label)
                btn.wait_for(timeout=5000)
                btn.click()
                joined = True
                print(f"Clicked '{label}'")
                break
            except Exception:
                continue

        if not joined:
            print("ERROR: Could not find join button")
            os.makedirs(DEBUG_DIR, exist_ok=True)
            page.screenshot(path=os.path.join(DEBUG_DIR, "chat_spike_join_fail.png"))
            browser.close()
            sys.exit(1)

        # Wait for in-meeting state
        print("Waiting for in-meeting state...")
        try:
            page.wait_for_selector(
                'button[aria-label*="Leave call"]',
                timeout=15000,
            )
        except Exception:
            print("WARNING: Leave call button not found — proceeding anyway")

        page.wait_for_timeout(3000)
        print("In meeting. Inspecting chat DOM...\n")

        dump_chat_dom(page)

        # Leave cleanly
        print("\nLeaving meeting...")
        try:
            leave_btn = page.get_by_role("button", name="Leave call")
            leave_btn.click()
            page.wait_for_timeout(1000)
        except Exception:
            pass

        browser.close()
        print("Done.")


if __name__ == "__main__":
    main()
