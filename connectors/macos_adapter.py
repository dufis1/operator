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

log = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

AUDIO_CAPTURE_HELPER = os.path.join(_BASE, "audio_capture")
BLACKHOLE_DEVICE = "coreaudio/BlackHole2ch_UID"
BROWSER_PROFILE = os.path.join(_BASE, config.BROWSER_PROFILE_DIR)


class MacOSAdapter(MeetingConnector):
    """MeetingConnector for macOS using ScreenCaptureKit + real Chrome."""

    def __init__(self):
        self._leave_event = threading.Event()
        self._capture_proc = None

    # ------------------------------------------------------------------
    # MeetingConnector interface
    # ------------------------------------------------------------------

    def join(self, meeting_url):
        """Start a browser session and join the meeting. Returns immediately;
        browser runs in a background thread until leave() is called."""
        self._leave_event.clear()
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
                    log.warning("MacOSAdapter: could not find join button")
                    browser.close()
                    return

                log.info("MacOSAdapter: joined meeting successfully")

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
                while not self._leave_event.is_set() and time.time() < deadline:
                    time.sleep(5)

                browser.close()
                log.info("MacOSAdapter: browser closed")

        except Exception as e:
            log.error(f"MacOSAdapter: browser session error: {e}")
