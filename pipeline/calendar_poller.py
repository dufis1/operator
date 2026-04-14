"""
CalendarPoller — browser-based meeting detection for Operator.

Polls Google Calendar every 30 seconds using a headless Playwright browser.
When an event with a Google Meet link is starting within JOIN_WINDOW_MINUTES,
puts the meet_url onto a shared queue for the runner to consume.

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
    def __init__(self, meeting_queue: queue.Queue, is_busy=None):
        """
        Args:
            meeting_queue: shared queue where detected meetings are put
            is_busy:       optional callable returning True if a meeting is
                           currently running. Used to log a warning when a
                           new meeting is queued while one is active
                           (Operator handles one meeting at a time).
        """
        self._meeting_queue = meeting_queue
        self._is_busy = is_busy or (lambda: False)
        self._joined_event_ids = set()
        self._ended_event_ids = set()   # log ended events once, not every poll
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

        # Copy the browser profile so calendar and meeting browsers don't
        # conflict. Skip the copy if our existing copy is already up to date
        # with the source's cookie store — copytree preserves mtimes, so a
        # stale cookie file is the right signal that auth state has moved on.
        if self._cal_profile_is_stale(src_profile, _CAL_PROFILE):
            try:
                if os.path.exists(_CAL_PROFILE):
                    shutil.rmtree(_CAL_PROFILE)
                shutil.copytree(
                    src_profile, _CAL_PROFILE,
                    ignore=shutil.ignore_patterns(
                        "SingletonLock", "SingletonSocket", "SingletonCookie",
                        "RunningChromeVersion",
                        ".operator.pid",
                    ),
                )
            except shutil.Error as e:
                # shutil.Error collects per-file failures — the copy mostly succeeded
                log.warning(f"CalendarPoller: partial copy errors (non-fatal): {e}")
            except Exception as e:
                log.error(f"CalendarPoller: failed to copy browser profile: {e}")
                return
        else:
            log.info("CalendarPoller: reusing cached profile copy (cookies unchanged)")

        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="CalendarPoller",
        )
        self._thread.start()
        log.info(f"CalendarPoller: started (polling every {POLL_INTERVAL}s)")

    @staticmethod
    def _cal_profile_is_stale(src_profile, cal_profile):
        """Return True if the calendar profile needs to be (re)copied.

        Compares mtimes of the Chromium Cookies file in src vs the cached
        copy. shutil.copytree preserves mtimes, so equal mtimes mean the
        cached copy is fresh enough to reuse.
        """
        if not os.path.exists(cal_profile):
            return True
        src_cookies = os.path.join(src_profile, "Default", "Cookies")
        dst_cookies = os.path.join(cal_profile, "Default", "Cookies")
        if not os.path.exists(src_cookies) or not os.path.exists(dst_cookies):
            return True
        return os.path.getmtime(src_cookies) > os.path.getmtime(dst_cookies)

    def stop(self):
        self._running = False
        self._meeting_queue.put(None)  # sentinel to unblock runner
        log.info("CalendarPoller: stopped")

    def _poll_loop(self):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch_persistent_context(
                    user_data_dir=_CAL_PROFILE,
                    headless=False,
                    executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    args=["--headless=new", "--disable-blink-features=AutomationControlled"],
                )
                page = browser.pages[0] if browser.pages else browser.new_page()

                page.goto(_CALENDAR_DAY_URL, wait_until="domcontentloaded", timeout=30000)
                # Wait briefly for calendar JS to render event nodes. On
                # empty-calendar days the selector never appears, so we cap
                # this at 3s — if events show up later the next poll catches
                # them, and the login redirect check below still runs.
                try:
                    page.wait_for_selector("[data-eventid]", timeout=3000)
                except Exception:
                    pass

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
                            # Same fast-wait pattern as the initial goto:
                            # cap at 3s and fall through if the day has no
                            # events.
                            try:
                                page.wait_for_selector("[data-eventid]", timeout=3000)
                            except Exception:
                                pass
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

            # Extract start and end time from page source
            start_dt, end_dt = self._find_event_times(full_html, event_id)
            if not start_dt:
                continue

            # Parse title from display text (second line)
            lines = text.split("\n")
            title = lines[1].strip() if len(lines) > 1 else "(no title)"

            minutes_until = (start_dt - now).total_seconds() / 60

            # Skip meetings that have already ended — log once per event_id
            if end_dt and now > end_dt:
                if event_id not in self._ended_event_ids:
                    log.debug(
                        f"CalendarPoller: '{title}' already ended — skipping {meet_url}"
                    )
                    self._ended_event_ids.add(event_id)
                continue

            log.debug(
                f"CalendarPoller: '{title}' starts in {minutes_until:.1f}m — {meet_url}"
            )

            if minutes_until <= JOIN_WINDOW_MINUTES:
                busy = self._is_busy()
                pending = self._meeting_queue.qsize()
                if busy or pending > 0:
                    log.warning(
                        f"CalendarPoller: queuing '{title}' while another meeting "
                        f"is active (busy={busy}, pending={pending}) — Operator "
                        f"handles one meeting at a time; will join after current ends"
                    )
                log.info(
                    f"CalendarPoller: joining '{title}' ({minutes_until:.1f}m until start)"
                )
                self._joined_event_ids.add(event_id)
                self._meeting_queue.put((meet_url, end_dt))

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
    def _find_event_times(html, event_id):
        """Extract event start and end times (UTC) from page source data.

        Returns (start_dt, end_dt) — end_dt may be None if only start is found.
        """
        idx = 0
        while True:
            idx = html.find(event_id, idx)
            if idx == -1:
                return None, None
            nearby = html[idx : idx + 2000]
            # Pattern: [null,[epoch_ms],"timezone"]
            matches = re.findall(r'\[null,\[(\d{13})\],"([^"]+)"\]', nearby)
            if matches:
                start_dt = datetime.datetime.fromtimestamp(
                    int(matches[0][0]) / 1000, tz=datetime.timezone.utc
                )
                end_dt = None
                if len(matches) >= 2:
                    end_dt = datetime.datetime.fromtimestamp(
                        int(matches[1][0]) / 1000, tz=datetime.timezone.utc
                    )
                return start_dt, end_dt
            idx += 1
