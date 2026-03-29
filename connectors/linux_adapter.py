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
import config

from .base import MeetingConnector
from .session import JoinStatus, detect_page_state, validate_auth_state, inject_cookies, save_debug

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

    def __init__(self, user_data_dir=None, auth_state_file=None):
        super().__init__()
        if user_data_dir is None:
            user_data_dir = config.BROWSER_PROFILE_DIR
        if auth_state_file is None:
            auth_state_file = config.AUTH_STATE_FILE
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
        self.join_status = JoinStatus()
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
        js = self.join_status
        try:
            with sync_playwright() as p:
                # Re-add --no-sandbox here if running as root (e.g. in a container).
                launch_args = [
                    "--use-fake-ui-for-media-stream",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--no-sandbox",  # required when running as root (droplet/server)
                    "--disable-features=WebRTCPipeWireCapturer",  # force PulseAudio for WebRTC; PipeWire not installed
                ]
                _use_auth = self._auth_state_file and os.path.isfile(self._auth_state_file)
                if not _use_auth and self._auth_state_file:
                    log.info(f"LinuxAdapter: {self._auth_state_file} not found — using guest mode")
                if _use_auth:
                    # Authenticated path: launch + new_context with saved session.
                    # headless=False + DISPLAY (Xvfb) enables audio rendering —
                    # headless Chrome suppresses audio output entirely.
                    # Do NOT pass env= — Playwright replaces the full environment if
                    # you do, stripping XDG_RUNTIME_DIR and breaking PulseAudio discovery.
                    # DISPLAY is already set in os.environ by the caller (run_linux.py).
                    log.info(f"LinuxAdapter: loading auth state from {self._auth_state_file}")
                    raw_browser = p.chromium.launch(
                        headless=False,
                        args=launch_args,
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

                # --- Session recovery ladder (authenticated path only) ---
                recovered = False
                if _use_auth:
                    state = detect_page_state(page)

                    if state == "logged_out":
                        log.warning("LinuxAdapter: session expired — attempting cookie recovery")
                        auth = validate_auth_state(self._auth_state_file)
                        if auth and inject_cookies(browser, auth):
                            page.reload(wait_until="domcontentloaded", timeout=30000)
                            page.wait_for_timeout(8000)
                            state = detect_page_state(page)
                            if state == "pre_join":
                                log.info("LinuxAdapter: session recovered via cookie injection")
                                recovered = True
                            else:
                                log.error(f"LinuxAdapter: recovery failed — page state: {state}")
                                save_debug(page, "recovery_fail")
                                js.signal_failure("session_expired")
                                self._page = None
                                browser.close()
                                if hasattr(browser, "_raw_browser"):
                                    browser._raw_browser.close()
                                return
                        else:
                            log.error("LinuxAdapter: no valid auth_state for recovery")
                            save_debug(page, "no_auth_state")
                            js.signal_failure("session_expired")
                            self._page = None
                            browser.close()
                            if hasattr(browser, "_raw_browser"):
                                browser._raw_browser.close()
                            return

                    if state == "cant_join":
                        log.error("LinuxAdapter: 'can't join this video call'")
                        save_debug(page, "cant_join")
                        js.signal_failure("cant_join")
                        self._page = None
                        browser.close()
                        if hasattr(browser, "_raw_browser"):
                            browser._raw_browser.close()
                        return

                # --- Pre-join screen actions ---

                # Dismiss notifications popup if present
                try:
                    not_now = page.get_by_role("button", name="Not now")
                    not_now.wait_for(timeout=3000)
                    not_now.click()
                    page.wait_for_timeout(500)
                    log.debug("LinuxAdapter: dismissed notifications popup")
                except Exception:
                    pass

                # Turn off camera
                try:
                    cam_btn = page.get_by_role("button", name="Turn off camera")
                    cam_btn.wait_for(timeout=3000)
                    cam_btn.click()
                    page.wait_for_timeout(300)
                    log.debug("LinuxAdapter: camera turned off")
                except Exception:
                    log.debug("LinuxAdapter: camera button not found or already off")

                # Ensure microphone is on before joining.
                # On the pre-join screen, a muted mic means Chrome won't call
                # getUserMedia and will never appear as a PulseAudio source-output —
                # meeting participants won't hear Operator at all.
                try:
                    mic_btn = page.get_by_role("button", name="Turn on microphone")
                    mic_btn.wait_for(timeout=3000)
                    mic_btn.click()
                    page.wait_for_timeout(300)
                    log.debug("LinuxAdapter: microphone enabled on pre-join screen")
                except Exception:
                    log.debug("LinuxAdapter: mic already on (pre-join) or button not found")

                # Fill in guest name if present (unauthenticated join shows a name field)
                try:
                    name_input = page.get_by_placeholder("Your name")
                    name_input.wait_for(timeout=3000)
                    name_input.fill("Operator")
                    page.wait_for_timeout(500)
                    log.debug("LinuxAdapter: filled guest name")
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
                        log.debug(f"LinuxAdapter: clicked {label!r}")
                        break
                    except Exception:
                        continue

                if not joined:
                    save_debug(page, "join_fail")
                    log.warning("LinuxAdapter: could not find join button")
                    js.signal_failure("no_join_button")
                    self._page = None
                    browser.close()
                    if hasattr(browser, "_raw_browser"):
                        browser._raw_browser.close()
                    return

                log.info("LinuxAdapter: joined meeting successfully")
                js.signal_success(recovered=recovered)

                # Unmute mic if needed after joining (fallback — primary unmute is pre-join above)
                page.wait_for_timeout(5000)
                try:
                    mic_btn = page.get_by_role("button", name="Turn on microphone")
                    mic_btn.wait_for(timeout=5000)
                    mic_btn.click()
                    log.debug("LinuxAdapter: microphone unmuted (post-join)")
                except Exception:
                    log.debug("LinuxAdapter: mic already on or button not found (post-join)")

                # Diagnostic screenshot
                try:
                    page.screenshot(path="/tmp/meet_after_join.png")
                    log.debug("LinuxAdapter: screenshot saved to /tmp/meet_after_join.png")
                except Exception as e:
                    log.warning(f"LinuxAdapter: screenshot failed: {e}")

                log.info("LinuxAdapter: in meeting — holding browser open")

                # Hold until leave() signals or 4-hour hard cap
                deadline = time.time() + 4 * 3600
                last_health = time.time()
                while not self._leave_event.is_set() and time.time() < deadline:
                    time.sleep(5)
                    # In-meeting health check every 5 minutes
                    if time.time() - last_health >= 300:
                        last_health = time.time()
                        try:
                            current_url = page.url
                            if "meet.google.com" not in current_url:
                                log.warning(f"LinuxAdapter: health check — unexpected URL: {current_url}")
                        except Exception:
                            log.warning("LinuxAdapter: health check — page not accessible")

                # Click Leave call before closing to avoid ghost session
                try:
                    leave_btn = page.get_by_role("button", name="Leave call")
                    leave_btn.wait_for(timeout=3000)
                    leave_btn.click()
                    page.wait_for_timeout(1000)
                    log.debug("LinuxAdapter: clicked Leave call")
                except Exception:
                    log.debug("LinuxAdapter: Leave call button not found — closing directly")

                self._page = None
                browser.close()
                if hasattr(browser, "_raw_browser"):
                    browser._raw_browser.close()
                log.info("LinuxAdapter: browser closed")

        except Exception as e:
            log.error(f"LinuxAdapter: browser session error: {e}")
            if not js.ready.is_set():
                js.signal_failure(f"exception: {e}")
            self._page = None
