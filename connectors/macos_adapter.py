"""
macOS connector for Operator.

Wraps ScreenCaptureKit audio capture (Swift helper) and Playwright/Chrome
meeting join into the MeetingConnector interface.

macOS-only: imports Playwright, subprocess for audio_capture binary.
"""
import os
import logging
import subprocess
import threading
import time

from playwright.sync_api import sync_playwright
import config

from .base import MeetingConnector
from .session import JoinStatus, detect_page_state, validate_auth_state, inject_cookies, save_debug

log = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

AUDIO_CAPTURE_HELPER = os.path.join(_BASE, "audio_capture")
BLACKHOLE_DEVICE = "coreaudio/BlackHole2ch_UID"
BROWSER_PROFILE = os.path.join(_BASE, config.BROWSER_PROFILE_DIR)


class MacOSAdapter(MeetingConnector):
    """MeetingConnector for macOS using ScreenCaptureKit + real Chrome."""

    def __init__(self, auth_state_file=None):
        super().__init__()
        if auth_state_file is None:
            auth_state_file = config.AUTH_STATE_FILE
        self._auth_state_file = auth_state_file
        self._leave_event = threading.Event()
        self._capture_proc = None

    # ------------------------------------------------------------------
    # MeetingConnector interface
    # ------------------------------------------------------------------

    def join(self, meeting_url):
        """Start a browser session and join the meeting. Returns immediately;
        browser runs in a background thread until leave() is called."""
        self._leave_event.clear()
        self.join_status = JoinStatus()
        threading.Thread(
            target=self._browser_session,
            args=(meeting_url,),
            daemon=True,
            name="MacOSAdapter-browser",
        ).start()
        log.info(f"MacOSAdapter: joining {meeting_url}")

    def get_audio_stream(self):
        """Launch the Swift ScreenCaptureKit helper and return the subprocess.
        Caller reads PCM float32 audio from proc.stdout and logs proc.stderr."""
        if not os.path.exists(AUDIO_CAPTURE_HELPER):
            raise FileNotFoundError(f"Audio capture helper not found: {AUDIO_CAPTURE_HELPER}")
        self._capture_proc = subprocess.Popen(
            [AUDIO_CAPTURE_HELPER],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
        )
        log.info("MacOSAdapter: Swift helper launched")
        return self._capture_proc

    def send_audio(self, audio_data):
        """Play raw audio bytes through BlackHole via mpv."""
        proc = subprocess.Popen(
            ["mpv", "--no-terminal", f"--audio-device={BLACKHOLE_DEVICE}", "--", "-"],
            stdin=subprocess.PIPE,
        )
        proc.stdin.write(audio_data)
        proc.stdin.close()
        proc.wait()

    def send_chat(self, message):
        log.info(f"MacOSAdapter: chat not yet implemented (message: {message!r})")

    def leave(self):
        """Signal the browser session to close and stop audio capture."""
        self._leave_event.set()
        if self._capture_proc:
            try:
                self._capture_proc.stdin.close()
            except Exception:
                pass
            try:
                self._capture_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._capture_proc.terminate()
            self._capture_proc = None
        log.info("MacOSAdapter: left meeting")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _browser_session(self, meeting_url):
        """Run Playwright browser session. Blocks until leave() is called."""
        singleton_lock = os.path.join(BROWSER_PROFILE, "SingletonLock")
        if os.path.exists(singleton_lock):
            os.remove(singleton_lock)
            log.info("MacOSAdapter: removed stale SingletonLock")

        js = self.join_status
        browser = None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch_persistent_context(
                    user_data_dir=BROWSER_PROFILE,
                    headless=False,
                    executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    args=["--use-fake-ui-for-media-stream", "--headless=new"],
                )
                page = browser.pages[0] if browser.pages else browser.new_page()

                page.goto(meeting_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(8000)

                # --- Session recovery ladder ---
                state = detect_page_state(page)
                recovered = False

                if state == "logged_out":
                    log.warning("MacOSAdapter: session expired — attempting cookie recovery")
                    auth = validate_auth_state(self._auth_state_file)
                    if auth and inject_cookies(browser, auth):
                        page.reload(wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(8000)
                        state = detect_page_state(page)
                        if state == "pre_join":
                            log.info("MacOSAdapter: session recovered via cookie injection")
                            recovered = True
                        else:
                            log.error(f"MacOSAdapter: recovery failed — page state: {state}")
                            save_debug(page, "recovery_fail")
                            js.signal_failure("session_expired")
                            return
                    else:
                        log.error("MacOSAdapter: no valid auth_state for recovery")
                        save_debug(page, "no_auth_state")
                        js.signal_failure("session_expired")
                        return

                if state == "cant_join":
                    log.error("MacOSAdapter: 'can't join this video call'")
                    save_debug(page, "cant_join")
                    js.signal_failure("cant_join")
                    return

                # --- Pre-join screen actions ---

                # Dismiss notifications popup if present
                try:
                    not_now = page.get_by_role("button", name="Not now")
                    not_now.wait_for(timeout=3000)
                    not_now.click()
                    page.wait_for_timeout(500)
                    log.debug("MacOSAdapter: dismissed notifications popup")
                except Exception:
                    pass

                # Turn off camera
                try:
                    cam_btn = page.get_by_role("button", name="Turn off camera")
                    cam_btn.wait_for(timeout=3000)
                    cam_btn.click()
                    page.wait_for_timeout(300)
                    log.debug("MacOSAdapter: camera turned off")
                except Exception:
                    log.debug("MacOSAdapter: camera button not found or already off")

                # Click join button
                joined = False
                for label in ["Join now", "Ask to join", "Switch here"]:
                    try:
                        btn = page.get_by_role("button", name=label)
                        btn.wait_for(timeout=5000)
                        btn.click()
                        joined = True
                        log.debug(f"MacOSAdapter: clicked {label!r}")
                        break
                    except Exception:
                        continue

                if not joined:
                    save_debug(page, "join_fail")
                    log.warning("MacOSAdapter: could not find join button")
                    js.signal_failure("no_join_button")
                    return

                log.info("MacOSAdapter: joined meeting successfully")
                js.signal_success(recovered=recovered)

                # Ensure mic is unmuted after join
                page.wait_for_timeout(3000)
                try:
                    mic_btn = page.get_by_role("button", name="Turn on microphone")
                    mic_btn.wait_for(timeout=3000)
                    mic_btn.click()
                    log.debug("MacOSAdapter: microphone unmuted")
                except Exception:
                    log.debug("MacOSAdapter: mic already on or button not found")

                log.info("MacOSAdapter: in meeting — holding browser open")

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
                                log.warning(f"MacOSAdapter: health check — unexpected URL: {current_url}")
                        except Exception:
                            log.warning("MacOSAdapter: health check — page not accessible")

        except Exception as e:
            log.error(f"MacOSAdapter: browser session error: {e}")
            if not js.ready.is_set():
                js.signal_failure(f"exception: {e}")
        finally:
            if browser:
                try:
                    browser.close()
                    log.info("MacOSAdapter: browser closed")
                except Exception:
                    log.debug("MacOSAdapter: browser already closed")
