"""
One-time auth helper — runs on macOS with the local venv.

Opens a headed Chromium browser, navigates to accounts.google.com,
and waits for you to log in as the Operator Google account.
Once logged in, saves the session to auth_state.json so the Docker
container can reuse it without repeating the login.

Usage:
    source venv/bin/activate
    python3 scripts/auth_export.py
"""
import sys
from playwright.sync_api import sync_playwright

OUTPUT = "auth_state.json"

print("Opening browser — log in as the Operator Google account.")
print("Press Enter here once you are fully logged in.")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto("https://accounts.google.com")

    input("\n>>> Press Enter once you have finished logging in... ")

    ctx.storage_state(path=OUTPUT)
    browser.close()

print(f"\nAuth state saved to {OUTPUT}")
print("You can now run the Docker container with:")
print(f'  docker run --rm --env-file .env -e MEETING_URL="..." -v $(pwd)/auth_state.json:/app/auth_state.json -e AUTH_STATE_FILE=/app/auth_state.json operator')
