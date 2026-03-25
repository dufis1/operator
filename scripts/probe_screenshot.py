import os
from playwright.sync_api import sync_playwright

MEETING_URL = os.environ.get("MEETING_URL", "https://meet.google.com/ztq-gphp-nax")
AUTH_STATE_FILE = os.environ.get("AUTH_STATE_FILE")

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--no-sandbox", "--use-fake-ui-for-media-stream"])
    if AUTH_STATE_FILE:
        print(f"Loading auth state from {AUTH_STATE_FILE}")
        ctx = b.new_context(storage_state=AUTH_STATE_FILE, viewport={"width": 1920, "height": 1080})
    else:
        ctx = b.new_context(viewport={"width": 1920, "height": 1080})
    page = ctx.new_page()
    page.goto(MEETING_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(8000)
    page.screenshot(path="/tmp/meet_prejoin.png")
    print("screenshot saved to /tmp/meet_prejoin.png")
    b.close()
