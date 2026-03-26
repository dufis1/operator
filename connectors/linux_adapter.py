"""
Linux local connector for Operator.

Wraps PulseAudio virtual audio routing and headless Playwright/Chromium
meeting join into the MeetingConnector interface.

Linux-only: requires PulseAudio (MeetingOutput + MeetingInput sinks set up
by scripts/linux_setup.sh) and Playwright's Chromium browser installed via
`python3 -m playwright install chromium`.
"""
import logging
import os
import subprocess
import threading
import time

from playwright.sync_api import sync_playwright

from .base import MeetingConnector

log = logging.getLogger(__name__)

# PulseAudio virtual device names — must match scripts/linux_setup.sh
PULSE_OUTPUT_SINK = "MeetingOutput"
PULSE_INPUT_SOURCE = "MeetingInput.monitor"

# Stealth config — validated in tests/probe_a2_stealth_meet.py (PASSES)
# Removes the two main bot-detection signals from headless Chrome:
#   1. "HeadlessChrome" substring in the User-Agent string
#   2. navigator.webdriver = true (standard automation flag)
STEALTH_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
});
"""


class LinuxAdapter(MeetingConnector):
    """MeetingConnector for headless local Linux using PulseAudio + Playwright Chromium."""

    def __init__(self, user_data_dir="/tmp/operator_browser_profile", auth_state_file=None):
        self._user_data_dir = user_data_dir
        self._auth_state_file = auth_state_file  # path to storage_state JSON from auth_export.py
        self._leave_event = threading.Event()
        self._capture_proc = None
        self._page = None  # kept for send_chat; set/cleared by browser thread

    # ------------------------------------------------------------------
    # MeetingConnector interface
    # ------------------------------------------------------------------

    def join(self, meeting_url):
        """Start a headless browser session and join the meeting.
        Returns immediately; browser runs in a background thread until leave()."""
        self._leave_event.clear()
        threading.Thread(
            target=self._browser_session,
            args=(meeting_url,),
            daemon=True,
            name="LinuxAdapter-browser",
        ).start()
        log.info(f"LinuxAdapter: joining {meeting_url}")

    def get_audio_stream(self):
        """Start parec reading from MeetingInput.monitor and return the subprocess.
        Caller reads raw float32-le PCM (16 kHz, mono) from proc.stdout —
        same wire format as the macOS Swift helper."""
        cmd = [
            "parec",
            f"--device={PULSE_INPUT_SOURCE}",
            "--format=float32le",
            "--rate=16000",
            "--channels=1",
        ]
        self._capture_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        log.info("LinuxAdapter: parec capture started from MeetingInput.monitor")
        return self._capture_proc

    def send_audio(self, audio_data):
        """Play raw audio bytes to MeetingOutput PulseAudio sink via mpv."""
        proc = subprocess.Popen(
            [
                "mpv",
                "--no-terminal",
                f"--audio-device=pulse/{PULSE_OUTPUT_SINK}",
                "--",
                "-",
            ],
            stdin=subprocess.PIPE,
        )
        proc.stdin.write(audio_data)
        proc.stdin.close()
        proc.wait()

    def send_chat(self, message):
        """Post a message to the meeting chat panel.
        Uses ARIA labels, not CSS classes, to locate the UI elements."""
        page = self._page
        if page is None:
            log.warning("LinuxAdapter: send_chat called but no active page")
            return
        try:
            # Open the chat panel if it isn't already open
            chat_btn = page.get_by_role("button", name="Open chat")
            chat_btn.wait_for(timeout=3000)
            chat_btn.click()
            page.wait_for_timeout(500)
        except Exception:
            pass  # panel may already be open

        try:
            input_box = page.get_by_role("textbox", name="Send a message to everyone")
            input_box.wait_for(timeout=5000)
            input_box.fill(message)
            input_box.press("Enter")
            log.info(f"LinuxAdapter: chat message sent: {message!r}")
        except Exception as e:
            log.warning(f"LinuxAdapter: send_chat failed: {e}")

    def leave(self):
        """Signal the browser session to close and stop audio capture."""
        self._leave_event.set()
        if self._capture_proc:
            try:
                self._capture_proc.terminate()
            except Exception:
                pass
            try:
                self._capture_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._capture_proc.kill()
            self._capture_proc = None
        log.info("LinuxAdapter: left meeting")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _browser_session(self, meeting_url):
        """Run headless Playwright/Chromium session. Blocks until leave() is called."""
        os.makedirs(self._user_data_dir, exist_ok=True)
        try:
            with sync_playwright() as p:
                # Re-add --no-sandbox here if running as root (e.g. in a container).
                launch_args = [
                    "--use-fake-ui-for-media-stream",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                ]
                if self._auth_state_file:
                    # Authenticated path: launch + new_context with saved session.
                    # headless=False + DISPLAY (Xvfb) enables audio rendering —
                    # headless Chrome suppresses audio output entirely.
                    display = os.environ.get("DISPLAY", ":99")
                    log.info(f"LinuxAdapter: loading auth state from {self._auth_state_file}")
                    raw_browser = p.chromium.launch(
                        headless=False,
                        args=launch_args,
                        env={"DISPLAY": display},
                    )
                    browser = raw_browser.new_context(
                        storage_state=self._auth_state_file,
                        user_agent=STEALTH_USER_AGENT,
                        viewport={"width": 1920, "height": 1080},
                    )
                    page = browser.new_page()
                    # Wrap close so leave() works the same way for both paths
                    browser._raw_browser = raw_browser
                else:
                    # Unauthenticated guest path: persistent context
                    browser = p.chromium.launch_persistent_context(
                        user_data_dir=self._user_data_dir,
                        headless=True,
                        user_agent=STEALTH_USER_AGENT,
                        viewport={"width": 1920, "height": 1080},
                        args=launch_args,
                    )
                    page = browser.pages[0] if browser.pages else browser.new_page()
                page.add_init_script(STEALTH_JS)
                self._page = page

                page.goto(meeting_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(8000)

                # Dismiss notifications popup if present
                try:
                    not_now = page.get_by_role("button", name="Not now")
                    not_now.wait_for(timeout=3000)
                    not_now.click()
                    page.wait_for_timeout(500)
                    log.info("LinuxAdapter: dismissed notifications popup")
                except Exception:
                    pass

                # Turn off camera
                try:
                    cam_btn = page.get_by_role("button", name="Turn off camera")
                    cam_btn.wait_for(timeout=3000)
                    cam_btn.click()
                    page.wait_for_timeout(300)
                    log.info("LinuxAdapter: camera turned off")
                except Exception:
                    log.info("LinuxAdapter: camera button not found or already off")

                # Ensure microphone is on before joining.
                # On the pre-join screen, a muted mic means Chrome won't call
                # getUserMedia and will never appear as a PulseAudio source-output —
                # meeting participants won't hear Operator at all.
                try:
                    mic_btn = page.get_by_role("button", name="Turn on microphone")
                    mic_btn.wait_for(timeout=3000)
                    mic_btn.click()
                    page.wait_for_timeout(300)
                    log.info("LinuxAdapter: microphone enabled on pre-join screen")
                except Exception:
                    log.info("LinuxAdapter: mic already on (pre-join) or button not found")

                # Fill in guest name if present (unauthenticated join shows a name field)
                try:
                    name_input = page.get_by_placeholder("Your name")
                    name_input.wait_for(timeout=3000)
                    name_input.fill("Operator")
                    page.wait_for_timeout(500)
                    log.info("LinuxAdapter: filled guest name")
                except Exception:
                    pass  # signed-in users don't see this field

                # Click the join button — try each label in order
                joined = False
                for label in ["Join now", "Ask to join", "Switch here"]:
                    try:
                        btn = page.get_by_role("button", name=label)
                        btn.wait_for(timeout=5000)
                        btn.click()
                        joined = True
                        log.info(f"LinuxAdapter: clicked {label!r}")
                        break
                    except Exception:
                        continue

                if not joined:
                    log.warning("LinuxAdapter: could not find join button")
                    browser.close()
                    if hasattr(browser, "_raw_browser"):
                        browser._raw_browser.close()
                    return

                # Unmute mic if needed after joining (fallback — primary unmute is pre-join above)
                page.wait_for_timeout(5000)
                try:
                    mic_btn = page.get_by_role("button", name="Turn on microphone")
                    mic_btn.wait_for(timeout=5000)
                    mic_btn.click()
                    log.info("LinuxAdapter: microphone unmuted (post-join)")
                except Exception:
                    log.info("LinuxAdapter: mic already on or button not found (post-join)")

                # Diagnostic screenshot
                try:
                    page.screenshot(path="/tmp/meet_after_join.png")
                    log.info("LinuxAdapter: screenshot saved to /tmp/meet_after_join.png")
                except Exception as e:
                    log.warning(f"LinuxAdapter: screenshot failed: {e}")

                log.info("LinuxAdapter: in meeting — holding browser open")

                # Hold until leave() signals or 4-hour hard cap
                deadline = time.time() + 4 * 3600
                while not self._leave_event.is_set() and time.time() < deadline:
                    time.sleep(5)

                # Click Leave call before closing to avoid ghost session
                try:
                    leave_btn = page.get_by_role("button", name="Leave call")
                    leave_btn.wait_for(timeout=3000)
                    leave_btn.click()
                    page.wait_for_timeout(1000)
                    log.info("LinuxAdapter: clicked Leave call")
                except Exception:
                    log.info("LinuxAdapter: Leave call button not found — closing directly")

                self._page = None
                browser.close()
                if hasattr(browser, "_raw_browser"):
                    browser._raw_browser.close()
                log.info("LinuxAdapter: browser closed")

        except Exception as e:
            log.error(f"LinuxAdapter: browser session error: {e}")
            self._page = None
