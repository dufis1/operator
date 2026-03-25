"""
Calendar poller + auto-join for Operator.

Polls the Operator Google Calendar every 2 minutes and triggers a Playwright
auto-join 1–2 minutes before any event that has a Google Meet link.
"""

import datetime
import logging
import os

# Tell Playwright to use the user-level browser cache (~/Library/Caches/ms-playwright)
# rather than looking inside the venv package directory.
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.expanduser("~/Library/Caches/ms-playwright"))
import threading
import time

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
POLL_INTERVAL_SECONDS = 120       # how often to check the calendar
JOIN_WINDOW_MINUTES = 2           # join this many minutes before start time
LOOK_AHEAD_MINUTES = 15           # fetch events starting within this window

_BASE = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(_BASE, "credentials.json")
TOKEN_FILE = os.path.join(_BASE, "token.json")
BROWSER_PROFILE = os.path.join(_BASE, "browser_profile")


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------

def _get_calendar_service():
    if not os.path.exists(TOKEN_FILE):
        log.warning("Calendar: token.json not found — skipping")
        return None
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
            log.info("Calendar: token refreshed")
        else:
            log.error("Calendar: token invalid and cannot refresh — re-run test_calendar.py to re-auth")
            return None
    return build("calendar", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# Auto-join
# ---------------------------------------------------------------------------

def _join_meeting(meet_url, stop_event):
    """Open a Playwright browser, join the Meet call, and hold it open."""
    log.info(f"Auto-join: launching browser for {meet_url}")
    # Remove stale lock file left by a previous crashed/killed browser session
    singleton_lock = os.path.join(BROWSER_PROFILE, "SingletonLock")
    if os.path.exists(singleton_lock):
        os.remove(singleton_lock)
        log.info("Auto-join: removed stale SingletonLock")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=BROWSER_PROFILE,
                headless=False,
                executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                args=["--use-fake-ui-for-media-stream"],
            )
            page = browser.pages[0] if browser.pages else browser.new_page()

            page.goto(meet_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(8000)

            # Dismiss notifications popup if present
            try:
                not_now = page.get_by_role("button", name="Not now")
                not_now.wait_for(timeout=3000)
                not_now.click()
                page.wait_for_timeout(500)
                log.info("Auto-join: dismissed notifications popup")
            except Exception:
                pass

            # Turn off camera in the pre-join screen
            try:
                cam_btn = page.get_by_role("button", name="Turn off camera")
                cam_btn.wait_for(timeout=3000)
                cam_btn.click()
                page.wait_for_timeout(300)
                log.info("Auto-join: camera turned off")
            except Exception:
                log.info("Auto-join: camera button not found or already off")

            # Click join button
            joined = False
            for label in ["Join now", "Ask to join"]:
                try:
                    btn = page.get_by_role("button", name=label)
                    btn.wait_for(timeout=5000)
                    btn.click()
                    joined = True
                    log.info(f"Auto-join: clicked '{label}'")
                    break
                except Exception:
                    continue

            if not joined:
                log.warning("Auto-join: could not find join button")
                browser.close()
                return

            # Wait for Meet to fully load, then ensure mic is unmuted
            # Meet sometimes joins muted — Ctrl+D toggles mic
            page.wait_for_timeout(3000)
            try:
                # If mic is muted, unmute it
                mic_btn = page.get_by_role("button", name="Turn on microphone")
                mic_btn.wait_for(timeout=3000)
                mic_btn.click()
                log.info("Auto-join: microphone unmuted")
            except Exception:
                log.info("Auto-join: mic already on or button not found")

            log.info("Auto-join: in meeting — holding browser open")

            # Keep the browser alive until the app quits or 4-hour hard cap
            deadline = time.time() + 4 * 3600
            while not stop_event.is_set() and time.time() < deadline:
                time.sleep(5)

            log.info("Auto-join: closing browser")
            browser.close()

    except Exception as e:
        log.error(f"Auto-join error: {e}")


# ---------------------------------------------------------------------------
# CalendarPoller
# ---------------------------------------------------------------------------

class CalendarPoller:
    def __init__(self):
        self._joined_event_ids = set()
        self._running = False
        self._stop_event = threading.Event()
        self._poll_thread = None

    def start(self):
        if not os.path.exists(CREDENTIALS_FILE):
            log.warning("Calendar poller: credentials.json not found — auto-join disabled")
            return
        if not os.path.exists(TOKEN_FILE):
            log.warning("Calendar poller: token.json not found — auto-join disabled")
            return
        self._running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True, name="CalendarPoller")
        self._poll_thread.start()
        log.info("Calendar poller: started")

    def stop(self):
        self._running = False
        self._stop_event.set()
        log.info("Calendar poller: stopped")

    def _poll_loop(self):
        # Poll immediately on start, then every POLL_INTERVAL_SECONDS
        while self._running:
            try:
                self._check_calendar()
            except Exception as e:
                log.error(f"Calendar poller: unexpected error: {e}")
            # Sleep in small increments so stop() is responsive
            for _ in range(POLL_INTERVAL_SECONDS * 2):
                if not self._running:
                    return
                time.sleep(0.5)

    def _check_calendar(self):
        service = _get_calendar_service()
        if not service:
            return

        now_utc = datetime.datetime.utcnow()
        time_min = now_utc.isoformat() + "Z"
        time_max = (now_utc + datetime.timedelta(minutes=LOOK_AHEAD_MINUTES)).isoformat() + "Z"

        result = service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        for event in result.get("items", []):
            meet_url = event.get("hangoutLink")
            if not meet_url:
                continue

            event_id = event["id"]
            if event_id in self._joined_event_ids:
                continue

            start_str = event["start"].get("dateTime")
            if not start_str:
                continue  # all-day event — skip

            # Parse start time and convert to UTC for comparison
            start_dt = datetime.datetime.fromisoformat(start_str)
            if start_dt.tzinfo is not None:
                epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
                start_utc = (start_dt.astimezone(datetime.timezone.utc) - epoch).total_seconds()
                now_ts = (now_utc - datetime.datetime(1970, 1, 1)).total_seconds()
                minutes_until = (start_utc - now_ts) / 60
            else:
                minutes_until = (start_dt - now_utc).total_seconds() / 60

            summary = event.get("summary", "(no title)")
            log.debug(f"Calendar: '{summary}' in {minutes_until:.1f}m — {meet_url}")

            if minutes_until <= JOIN_WINDOW_MINUTES:
                log.info(f"Calendar poller: auto-joining '{summary}' ({minutes_until:.1f}m until start)")
                self._joined_event_ids.add(event_id)
                threading.Thread(
                    target=_join_meeting,
                    args=(meet_url, self._stop_event),
                    daemon=True,
                    name=f"AutoJoin-{event_id[:8]}",
                ).start()
