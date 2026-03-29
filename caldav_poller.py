"""
CalDAVPoller — CalDAV-based meeting detection for Operator.

Polls the bot's Google Calendar every 60 seconds via CalDAV.
When an event with a Google Meet link is starting within JOIN_WINDOW_MINUTES,
calls connector.join(meet_url).

Prerequisites:
  - Bot's Gmail address set in config.yaml under caldav.bot_gmail
  - Bot's Gmail app password stored in the system keychain:
      python -c "import keyring; keyring.set_password('operator', 'BOT@gmail.com', 'APP_PASSWORD')"
    Generate the app password at: https://myaccount.google.com/apppasswords
    (requires 2-Step Verification enabled on the bot account)

Notes:
  - Only events the bot has *accepted* appear via CalDAV. Have the user accept
    meeting invites on the bot's behalf — Operator cannot auto-accept.
  - Do not set POLL_INTERVAL below 60. Google enforces a CalDAV rate floor.
"""
import datetime
import logging
import re
import threading
import time

import caldav
import keyring

import config

log = logging.getLogger(__name__)

POLL_INTERVAL = 60          # seconds — do not poll faster (Google CalDAV rate floor)
JOIN_WINDOW_MINUTES = 2     # join this many minutes before event start
LOOK_AHEAD_MINUTES = 15     # fetch events starting within this window

_MEET_URL_RE = re.compile(r"https://meet\.google\.com/[a-z0-9\-]+")


def _extract_meet_url(vevent):
    """Return the first Google Meet URL found in the event, or None."""
    for field in ("X-GOOGLE-CONFERENCE", "LOCATION", "DESCRIPTION"):
        value = str(vevent.get(field, ""))
        m = _MEET_URL_RE.search(value)
        if m:
            return m.group()
    return None


class CalDAVPoller:
    def __init__(self, connector):
        self._connector = connector
        self._joined_event_ids = set()
        self._running = False
        self._thread = None

    def start(self):
        bot_gmail = getattr(config, "CALDAV_BOT_GMAIL", None)
        if not bot_gmail:
            log.warning("CalDAVPoller: caldav.bot_gmail not set in config.yaml — auto-join disabled")
            return

        password = keyring.get_password("operator", bot_gmail)
        if not password:
            log.warning(
                f"CalDAVPoller: no keychain credential found for '{bot_gmail}'. "
                "Store it with: "
                f"python -c \"import keyring; keyring.set_password('operator', '{bot_gmail}', 'APP_PASSWORD')\""
            )
            print(f"\n⚠️  No keychain credential for '{bot_gmail}' — store it with:\n")
            print(f"   python -c \"import keyring; keyring.set_password('operator', '{bot_gmail}', 'APP_PASSWORD')\"\n")
            return

        self._bot_gmail = bot_gmail
        self._password = password
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="CalDAVPoller")
        self._thread.start()
        log.info(f"CalDAVPoller: started (polling every {POLL_INTERVAL}s for {bot_gmail})")

    def stop(self):
        self._running = False
        if self._connector:
            self._connector.leave()
        log.info("CalDAVPoller: stopped")

    def _poll_loop(self):
        while self._running:
            try:
                self._check_calendar()
            except Exception as e:
                log.error(f"CalDAVPoller: poll error: {e}")
            # Sleep in short increments so stop() is responsive
            for _ in range(POLL_INTERVAL * 2):
                if not self._running:
                    return
                time.sleep(0.5)

    def _check_calendar(self):
        client = caldav.DAVClient(
            url=f"https://www.google.com/calendar/dav/{self._bot_gmail}/events/",
            username=self._bot_gmail,
            password=self._password,
        )
        principal = client.principal()
        calendar = principal.calendar(cal_id=self._bot_gmail)

        now = datetime.datetime.now(datetime.timezone.utc)
        look_ahead = now + datetime.timedelta(minutes=LOOK_AHEAD_MINUTES)

        events = calendar.date_search(start=now, end=look_ahead, expand=True)
        for event in events:
            vevent = event.icalendar_component
            uid = str(vevent.get("UID", ""))
            if uid in self._joined_event_ids:
                continue

            meet_url = _extract_meet_url(vevent)
            if not meet_url:
                continue

            dtstart = vevent.get("DTSTART")
            if dtstart is None:
                continue

            start_dt = dtstart.dt
            # Skip all-day events (date object, not datetime)
            if not isinstance(start_dt, datetime.datetime):
                continue
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=datetime.timezone.utc)

            minutes_until = (start_dt - now).total_seconds() / 60
            summary = str(vevent.get("SUMMARY", "(no title)"))
            log.debug(f"CalDAVPoller: '{summary}' starts in {minutes_until:.1f}m — {meet_url}")

            if minutes_until <= JOIN_WINDOW_MINUTES:
                log.info(f"CalDAVPoller: joining '{summary}' ({minutes_until:.1f}m until start)")
                self._joined_event_ids.add(uid)
                threading.Thread(
                    target=self._connector.join,
                    args=(meet_url,),
                    daemon=True,
                    name=f"AutoJoin-{uid[:8]}",
                ).start()
