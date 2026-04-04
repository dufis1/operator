"""
CalendarPoller — browser-based meeting detection for Operator.

Polls Google Calendar every 30 seconds using a headless Playwright browser.
When an event with a Google Meet link is starting within JOIN_WINDOW_MINUTES,
puts the meet_url onto a shared queue for AgentRunner to consume.

Auth: uses a copy of the main browser_profile so it shares the same Google
session — no app passwords, no keychain, no extra setup beyond the initial
browser login via scripts/auth_export.py.
"""
import datetime
import logging
import os
import queue
import re
import shutil
import threading
import time

from playwright.sync_api import sync_playwright

import config

log = logging.getLogger(__name__)

POLL_INTERVAL = 30          # seconds between calendar refreshes
JOIN_WINDOW_MINUTES = 2     # join this many minutes before event start
LOOK_AHEAD_MINUTES = 15     # ignore events further out than this

_MEET_URL_RE = re.compile(r"https://meet\.google\.com/[a-z0-9\-]+")
_CALENDAR_DAY_URL = "https://calendar.google.com/calendar/u/0/r/day"
_BASE = os.path.dirname(os.path.abspath(__file__))
_CAL_PROFILE = os.path.join(_BASE, "browser_profile_calendar")


class CalendarPoller:
    def __init__(self, meeting_queue: queue.Queue):
        self._meeting_queue = meeting_queue
        self._joined_event_ids = set()
        self._running = False
        self._thread = None

    def start(self):
        src_profile = config.BROWSER_PROFILE_DIR
        if not os.path.exists(src_profile):
            log.warning(
                "CalendarPoller: browser_profile not found — calendar polling disabled. "
                "Run: python scripts/auth_export.py"
            )
            print("\n⚠️  No browser_profile — calendar polling disabled.")
            print("   Run: python scripts/auth_export.py\n")
            return

        # Copy the browser profile so calendar and meeting browsers don't conflict
        try:
            if os.path.exists(_CAL_PROFILE):
                shutil.rmtree(_CAL_PROFILE)
            shutil.copytree(
                src_profile, _CAL_PROFILE,
                ignore=shutil.ignore_patterns(
                    "SingletonLock", "SingletonSocket", "SingletonCookie",
                    ".operator.pid",
                ),
            )
        except Exception as e:
            log.error(f"CalendarPoller: failed to copy browser profile: {e}")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="CalendarPoller",
        )
        self._thread.start()
        log.info(f"CalendarPoller: started (polling every {POLL_INTERVAL}s)")

    def stop(self):
        self._running = False
        self._meeting_queue.put(None)  # sentinel to unblock runner
        log.info("CalendarPoller: stopped")

    def _poll_loop(self):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch_persistent_context(
                    user_data_dir=_CAL_PROFILE,
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                page = browser.pages[0] if browser.pages else browser.new_page()

                page.goto(_CALENDAR_DAY_URL, wait_until="domcontentloaded", timeout=30000)
                time.sleep(8)  # let calendar JS render

                if "accounts.google.com" in page.url:
                    log.error(
                        "CalendarPoller: redirected to login — session may be expired. "
                        "Re-run: python scripts/auth_export.py"
                    )
                    print("\n⚠️  Calendar session expired — re-run: python scripts/auth_export.py\n")
                    browser.close()
                    return

                log.info(f"CalendarPoller: calendar loaded — {page.title()}")

                while self._running:
                    try:
                        self._check_calendar(page)
                    except Exception as e:
                        log.error(f"CalendarPoller: poll error: {e}")

                    for _ in range(POLL_INTERVAL * 2):
                        if not self._running:
                            break
                        time.sleep(0.5)

                    if self._running:
                        try:
                            page.reload(wait_until="domcontentloaded", timeout=30000)
                            time.sleep(5)  # let calendar JS re-render
                        except Exception as e:
                            log.error(f"CalendarPoller: reload error: {e}")

                browser.close()
        except Exception as e:
            log.error(f"CalendarPoller: browser launch failed: {e}")

    def _check_calendar(self, page):
        now = datetime.datetime.now(datetime.timezone.utc)
        full_html = page.content()
        events = page.query_selector_all("[data-eventid]")

        for ev in events:
            text = ev.inner_text().strip()

            # Skip all-day events
            if text.lower().startswith("all day"):
                continue

            # Extract raw event ID from jslog attribute
            jslog = ev.get_attribute("jslog") or ""
            id_match = re.search(r'"([a-z0-9]+)"', jslog)
            if not id_match:
                continue
            event_id = id_match.group(1)

            if event_id in self._joined_event_ids:
                continue

            # Find Meet URL near this event ID in page source
            meet_url = self._find_meet_url(full_html, event_id)
            if not meet_url:
                continue

            # Extract start time from page source
            start_dt = self._find_start_time(full_html, event_id)
            if not start_dt:
                continue

            # Parse title from display text (second line)
            lines = text.split("\n")
            title = lines[1].strip() if len(lines) > 1 else "(no title)"

            minutes_until = (start_dt - now).total_seconds() / 60
            log.debug(
                f"CalendarPoller: '{title}' starts in {minutes_until:.1f}m — {meet_url}"
            )

            if minutes_until <= JOIN_WINDOW_MINUTES:
                log.info(
                    f"CalendarPoller: joining '{title}' ({minutes_until:.1f}m until start)"
                )
                self._joined_event_ids.add(event_id)
                self._meeting_queue.put(meet_url)

    @staticmethod
    def _find_meet_url(html, event_id):
        """Search page source for a Meet URL near the given event ID."""
        idx = 0
        while True:
            idx = html.find(event_id, idx)
            if idx == -1:
                return None
            nearby = html[idx : idx + 2000]
            match = _MEET_URL_RE.search(nearby)
            if match:
                url = match.group()
                # Filter out settings/non-meeting URLs
                if "calendarsettings" not in url:
                    return url
            idx += 1

    @staticmethod
    def _find_start_time(html, event_id):
        """Extract the event start time (UTC) from page source data."""
        idx = 0
        while True:
            idx = html.find(event_id, idx)
            if idx == -1:
                return None
            nearby = html[idx : idx + 2000]
            # Pattern: [null,[epoch_ms],"timezone"]
            matches = re.findall(r'\[null,\[(\d{13})\],"([^"]+)"\]', nearby)
            if matches:
                # First match is start time, second is end time
                epoch_ms = int(matches[0][0])
                return datetime.datetime.fromtimestamp(
                    epoch_ms / 1000, tz=datetime.timezone.utc
                )
            idx += 1
