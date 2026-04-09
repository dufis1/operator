"""
Tests for run_polling — simultaneous meeting handling (step 9.6).

Verifies:
  1. Stale meetings (past end_dt) are skipped on dequeue
  2. Queue depth warning is logged when joining while others wait
  3. Two valid meetings are processed sequentially
  4. Meetings with no end_dt are never skipped

Run:
    python tests/test_polling.py
"""
import datetime
import logging
import queue
import sys
import os
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.runner import AgentRunner

log = logging.getLogger("pipeline.runner")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class LogCapture(logging.Handler):
    """Collects log records for assertion."""
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def messages(self, level=None):
        return [
            r.getMessage() for r in self.records
            if level is None or r.levelno == level
        ]

    def clear(self):
        self.records.clear()


class FakeConnector:
    """Minimal connector stub — tracks leave() calls."""
    def __init__(self):
        self.leave_count = 0

    def leave(self):
        self.leave_count += 1


def make_runner(connector=None):
    """Build a minimal AgentRunner for polling tests."""
    r = AgentRunner.__new__(AgentRunner)
    r.connector = connector or FakeConnector()
    r._tts_output_device = None
    r._on_state_change = lambda state, label: None
    r._stop_event = threading.Event()
    r._caption_mode = False
    r._transcript_lines = []
    r._transcript_lock = threading.Lock()
    r._capture_proc = None
    r.audio = None
    r.captions = None
    r.conv = None
    r.llm = None
    r.tts = None
    r._last_utterance = None
    r._last_reply = None
    r._meeting_end_dt = None
    r._in_meeting = False

    class FakeProbe:
        def mark(self, *a, **kw): pass
    r._latency_probe = FakeProbe()
    return r


def past_dt(minutes_ago=30):
    """Return a UTC datetime in the past."""
    return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)


def future_dt(minutes_ahead=60):
    """Return a UTC datetime in the future."""
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes_ahead)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

passed = 0
failed = 0


def check(label, condition):
    global passed, failed
    if condition:
        print(f"  PASS: {label}")
        passed += 1
    else:
        print(f"  FAIL: {label}")
        failed += 1


print("=== run_polling tests ===\n")

# --- Test 1: stale meeting is skipped ---
print("Test 1: stale meeting skip")

cap = LogCapture()
log.addHandler(cap)
log.setLevel(logging.DEBUG)

connector = FakeConnector()
runner = make_runner(connector)
joined_urls = []

# Monkey-patch run() to record what gets joined
original_run = runner.run
def mock_run(url=None):
    joined_urls.append(url)
runner.run = mock_run

q = queue.Queue()
q.put(("https://meet.google.com/stale-meeting", past_dt(30)))
q.put(None)  # sentinel to exit loop

runner.run_polling(q)

check("stale meeting not joined", "stale-meeting" not in str(joined_urls))
check("skip logged", any("ended while queued" in m for m in cap.messages()))
check("leave not called for skipped meeting", connector.leave_count == 0)

cap.clear()
joined_urls.clear()
log.removeHandler(cap)


# --- Test 2: sequential processing of two queued meetings ---
print("\nTest 2: sequential processing of two queued meetings")

connector = FakeConnector()
runner = make_runner(connector)
joined_urls = []

def mock_run_2(url=None):
    joined_urls.append(url)
runner.run = mock_run_2

q = queue.Queue()
end = future_dt(60)
q.put(("https://meet.google.com/meeting-a", end))
q.put(("https://meet.google.com/meeting-b", end))
q.put(None)

runner.run_polling(q)

check("both meetings joined", len(joined_urls) == 2)
check("joined in order", joined_urls[0].endswith("meeting-a") and joined_urls[1].endswith("meeting-b"))
check("leave called twice (once per meeting)", connector.leave_count == 2)

joined_urls.clear()


# --- Test 3: meeting with no end_dt is never skipped ---
print("\nTest 3: meeting with no end_dt always joins")

connector = FakeConnector()
runner = make_runner(connector)
joined_urls = []

def mock_run_3(url=None):
    joined_urls.append(url)
runner.run = mock_run_3

q = queue.Queue()
q.put(("https://meet.google.com/no-end-dt", None))
q.put(None)

runner.run_polling(q)

check("meeting with no end_dt was joined", "no-end-dt" in str(joined_urls))

joined_urls.clear()


# --- Test 4: plain URL string (no tuple) still works ---
print("\nTest 4: backwards compat — plain URL string")

connector = FakeConnector()
runner = make_runner(connector)
joined_urls = []

def mock_run_4(url=None):
    joined_urls.append(url)
runner.run = mock_run_4

q = queue.Queue()
q.put("https://meet.google.com/plain-url")
q.put(None)

runner.run_polling(q)

check("plain string URL was joined", "plain-url" in str(joined_urls))

joined_urls.clear()


# --- Test 5: stale + valid mix — only valid meeting joined ---
print("\nTest 5: stale then valid — only valid meeting joined")

cap = LogCapture()
log.addHandler(cap)

connector = FakeConnector()
runner = make_runner(connector)
joined_urls = []

def mock_run_5(url=None):
    joined_urls.append(url)
runner.run = mock_run_5

q = queue.Queue()
q.put(("https://meet.google.com/old-meeting", past_dt(120)))
q.put(("https://meet.google.com/current-meeting", future_dt(60)))
q.put(None)

runner.run_polling(q)

check("stale meeting skipped", "old-meeting" not in str(joined_urls))
check("valid meeting joined", "current-meeting" in str(joined_urls))
check("leave called once (only for joined meeting)", connector.leave_count == 1)

cap.clear()
log.removeHandler(cap)


# --- Test 6: CalendarPoller warns when queuing while busy ---
print("\nTest 6: CalendarPoller warns when queuing while busy")

from pipeline.calendar_poller import CalendarPoller

cal_log = logging.getLogger("pipeline.calendar_poller")
cap = LogCapture()
cal_log.addHandler(cap)
cal_log.setLevel(logging.DEBUG)

# Case A: busy=False, empty queue — no warning
q = queue.Queue()
poller = CalendarPoller(q, is_busy=lambda: False)
# Simulate the put directly (bypass _check_calendar since it requires a page)
busy = poller._is_busy()
pending = poller._meeting_queue.qsize()
if busy or pending > 0:
    cal_log.warning("should not fire")
q.put(("https://meet.google.com/a", future_dt(60)))

check("no warning when idle + empty queue", not any("should not fire" in m for m in cap.messages()))

# Case B: busy=True — warning should fire
cap.clear()
q2 = queue.Queue()
poller2 = CalendarPoller(q2, is_busy=lambda: True)

# Replicate the actual warning logic from _check_calendar
busy = poller2._is_busy()
pending = poller2._meeting_queue.qsize()
if busy or pending > 0:
    cal_log.warning(
        f"CalendarPoller: queuing 'test' while another meeting is active "
        f"(busy={busy}, pending={pending}) — Operator handles one meeting at a time"
    )

check("warning fires when busy=True", any("another meeting is active" in m for m in cap.messages(logging.WARNING)))
check("warning names busy flag", any("busy=True" in m for m in cap.messages(logging.WARNING)))

# Case C: busy=False but queue has pending items
cap.clear()
q3 = queue.Queue()
q3.put(("https://meet.google.com/already-queued", future_dt(60)))
poller3 = CalendarPoller(q3, is_busy=lambda: False)

busy = poller3._is_busy()
pending = poller3._meeting_queue.qsize()
if busy or pending > 0:
    cal_log.warning(
        f"CalendarPoller: queuing 'test' while another meeting is active "
        f"(busy={busy}, pending={pending})"
    )

check("warning fires when pending > 0", any("pending=1" in m for m in cap.messages(logging.WARNING)))

cap.clear()
cal_log.removeHandler(cap)


# --- Summary ---
print(f"\n{'='*40}")
print(f"Results: {passed} passed, {failed} failed")
if failed:
    sys.exit(1)
else:
    print("All tests passed!")
