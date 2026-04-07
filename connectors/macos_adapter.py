"""
macOS connector for Operator.

Wraps ScreenCaptureKit audio capture (Swift helper) and Playwright/Chrome
meeting join into the MeetingConnector interface.

macOS-only: imports Playwright, subprocess for audio_capture binary.
"""
import os
import logging
import queue
import subprocess
import threading
import time

from playwright.sync_api import sync_playwright
import config

from .base import MeetingConnector
from .session import JoinStatus, detect_page_state, validate_auth_state, inject_cookies, save_debug, _chrome_lock_is_live, _chrome_kill_and_clear, _write_operator_pid

log = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

AUDIO_CAPTURE_HELPER = os.path.join(_BASE, "audio_capture")
BLACKHOLE_DEVICE = "coreaudio/BlackHole2ch_UID"
BROWSER_PROFILE = os.path.join(_BASE, config.BROWSER_PROFILE_DIR)


class MacOSAdapter(MeetingConnector):
    """MeetingConnector for macOS using ScreenCaptureKit + real Chrome."""

    def __init__(self, auth_state_file=None, force=False):
        super().__init__()
        if auth_state_file is None:
            auth_state_file = config.AUTH_STATE_FILE
        self._auth_state_file = auth_state_file
        self._force = force
        self._leave_event = threading.Event()
        self._browser_closed = threading.Event()
        self._browser_thread = None
        self._capture_proc = None
        self._blackhole_rec_proc = None
        self._page = None
        self._seen_message_ids = set()
        self._chat_queue = queue.Queue()  # (command, args, result_queue)

    # ------------------------------------------------------------------
    # MeetingConnector interface
    # ------------------------------------------------------------------

    def join(self, meeting_url):
        """Start a browser session and join the meeting. Returns immediately;
        browser runs in a background thread until leave() is called."""
        self._leave_event.clear()
        self._browser_closed.clear()
        self.join_status = JoinStatus()
        if config.DEBUG_AUDIO:
            self._start_blackhole_recording()
        self._browser_thread = threading.Thread(
            target=self._browser_session,
            args=(meeting_url,),
            daemon=True,
            name="MacOSAdapter-browser",
        )
        self._browser_thread.start()
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
        """Post a message to the Google Meet chat panel.
        Queues the request for the browser thread (Playwright is single-threaded)."""
        result_q = queue.Queue()
        self._chat_queue.put(("send", message, result_q))
        result_q.get(timeout=10)  # wait for browser thread to finish

    def read_chat(self):
        """Return new chat messages since last call.
        Queues the request for the browser thread."""
        result_q = queue.Queue()
        self._chat_queue.put(("read", None, result_q))
        try:
            return result_q.get(timeout=10)
        except queue.Empty:
            return []

    # --- Browser-thread chat implementations (called from _process_chat_queue) ---

    def _ensure_chat_open(self, page):
        """Open the chat panel if it isn't already open. Must run on browser thread."""
        try:
            textarea = page.locator('textarea[aria-label="Send a message"]')
            if textarea.count() > 0 and textarea.is_visible():
                return  # already open
        except Exception:
            pass
        try:
            chat_btn = page.get_by_role("button", name="Chat with everyone")
            chat_btn.wait_for(timeout=3000)
            chat_btn.click()
            page.wait_for_timeout(500)
        except Exception as e:
            log.warning(f"MacOSAdapter: could not open chat panel: {e}")

    def _do_send_chat(self, page, message):
        """Actual send_chat logic — must run on browser thread."""
        self._ensure_chat_open(page)
        try:
            input_box = page.locator('textarea[aria-label="Send a message"]')
            input_box.wait_for(timeout=5000)
            input_box.fill(message)
            input_box.press("Enter")
            log.info(f"MacOSAdapter: chat sent: {message!r}")
        except Exception as e:
            log.warning(f"MacOSAdapter: send_chat failed: {e}")

    def _do_read_chat(self, page):
        """Actual read_chat logic — must run on browser thread."""
        self._ensure_chat_open(page)

        new_messages = []
        try:
            msg_els = page.locator("div[data-message-id]")
            count = msg_els.count()
            for i in range(count):
                el = msg_els.nth(i)
                msg_id = el.get_attribute("data-message-id")
                if msg_id in self._seen_message_ids:
                    continue
                self._seen_message_ids.add(msg_id)
                text_el = el.locator('div[jsname="dTKtvb"]')
                text = text_el.inner_text().strip() if text_el.count() > 0 else el.inner_text().strip()
                new_messages.append({"id": msg_id, "sender": "", "text": text})
        except Exception as e:
            log.warning(f"MacOSAdapter: read_chat failed: {e}")
        return new_messages

    def _process_chat_queue(self, page):
        """Drain the chat command queue. Called from browser thread's idle loop."""
        while not self._chat_queue.empty():
            try:
                cmd, args, result_q = self._chat_queue.get_nowait()
            except queue.Empty:
                break
            if cmd == "send":
                self._do_send_chat(page, args)
                result_q.put(None)
            elif cmd == "read":
                messages = self._do_read_chat(page)
                result_q.put(messages)

    def leave(self):
        """Signal the browser session to close and stop audio capture."""
        self._leave_event.set()
        # Wait for browser.close() to finish (same pattern as CaptionsAdapter)
        if self._browser_thread and self._browser_thread.is_alive():
            log.info("MacOSAdapter: waiting for browser to close...")
            if not self._browser_closed.wait(timeout=10):
                log.warning("MacOSAdapter: browser close timed out (10s)")
        if self._blackhole_rec_proc:
            self._blackhole_rec_proc.terminate()
            try:
                self._blackhole_rec_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._blackhole_rec_proc.kill()
            self._blackhole_rec_proc = None
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

    def _start_blackhole_recording(self):
        import datetime
        os.makedirs(os.path.join(_BASE, "debug"), exist_ok=True)
        ts = datetime.datetime.now().strftime("%H%M%S")
        out_path = os.path.join(_BASE, f"debug/blackhole_{ts}.wav")
        try:
            self._blackhole_rec_proc = subprocess.Popen(
                ["sox", "-t", "coreaudio", "BlackHole 2ch", out_path],
                stderr=subprocess.DEVNULL,
            )
            log.info(f"MacOSAdapter: BlackHole recording → {out_path}")
        except FileNotFoundError:
            log.warning("MacOSAdapter: sox not found — BlackHole recording skipped (brew install sox)")
            self._blackhole_rec_proc = None

    def _browser_session(self, meeting_url):
        """Run Playwright browser session. Blocks until leave() is called."""
        singleton_lock = os.path.join(BROWSER_PROFILE, "SingletonLock")
        if os.path.islink(singleton_lock) or os.path.exists(singleton_lock):
            if _chrome_lock_is_live(singleton_lock):
                if self._force:
                    log.info("MacOSAdapter: --force: killing existing session")
                    _chrome_kill_and_clear(singleton_lock)
                else:
                    log.error(
                        "MacOSAdapter: another Operator session is already running — "
                        "stop that session before starting a new one"
                    )
                    self.join_status.signal_failure("already_running")
                    return
            else:
                os.remove(singleton_lock)
                log.info("MacOSAdapter: removed stale SingletonLock")

        _write_operator_pid(singleton_lock)
        js = self.join_status
        browser = None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch_persistent_context(
                    user_data_dir=BROWSER_PROFILE,
                    headless=False,
                    executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    args=["--use-fake-ui-for-media-stream", "--headless=new", "--mute-audio"],
                )
                page = browser.pages[0] if browser.pages else browser.new_page()
                self._page = page

                page.goto(meeting_url, wait_until="domcontentloaded", timeout=30000)
                # Event-driven: wait for a pre-join or in-meeting element instead of sleeping 8s
                try:
                    page.wait_for_selector(
                        'button:has-text("Join now"), '
                        'button:has-text("Ask to join"), '
                        'button[aria-label*="Turn off camera"], '
                        'button[aria-label*="Turn on camera"], '
                        'button[aria-label*="Sign in"]',
                        timeout=15000,
                    )
                except Exception:
                    log.warning("MacOSAdapter: no pre-join element detected — proceeding anyway")

                if config.DEBUG_AUDIO:
                    save_debug(page, "initial_load")

                # --- Session recovery ladder ---
                state = detect_page_state(page)
                recovered = False

                if state == "logged_out":
                    log.warning("MacOSAdapter: session expired — attempting cookie recovery")
                    auth = validate_auth_state(self._auth_state_file)
                    if auth and inject_cookies(browser, auth):
                        page.reload(wait_until="domcontentloaded", timeout=30000)
                        try:
                            page.wait_for_selector(
                                'button:has-text("Join now"), '
                                'button:has-text("Ask to join"), '
                                'button[aria-label*="Turn off camera"]',
                                timeout=15000,
                            )
                        except Exception:
                            pass
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

                # Race both camera states — resolves instantly when one already exists
                cam_off = page.get_by_role("button", name="Turn off camera")
                cam_on = page.get_by_role("button", name="Turn on camera")
                try:
                    cam_off.or_(cam_on).wait_for(timeout=3000)
                    if cam_off.is_visible():
                        cam_off.click()
                        log.debug("MacOSAdapter: camera turned off")
                    else:
                        log.debug("MacOSAdapter: camera already off")
                except Exception:
                    log.debug("MacOSAdapter: camera button not found")

                if config.DEBUG_AUDIO:
                    save_debug(page, "pre_join")

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

                try:
                    # Event-driven: wait for in-meeting UI instead of sleeping 3s
                    try:
                        page.wait_for_selector(
                            'button[aria-label*="Leave call"]',
                            timeout=15000,
                        )
                    except Exception:
                        log.warning("MacOSAdapter: in-meeting indicator not detected — proceeding anyway")

                    # Race both mic states — resolves instantly when mic is already on
                    mic_on_btn = page.get_by_role("button", name="Turn on microphone")
                    mic_off_btn = page.get_by_role("button", name="Turn off microphone")
                    try:
                        mic_on_btn.or_(mic_off_btn).wait_for(timeout=3000)
                        if mic_on_btn.is_visible():
                            mic_on_btn.click()
                            log.debug("MacOSAdapter: microphone unmuted")
                        else:
                            log.debug("MacOSAdapter: mic already on")
                    except Exception:
                        log.debug("MacOSAdapter: mic button not found")

                    log.info("MacOSAdapter: in meeting — holding browser open")

                    # Hold until leave() signals or 4-hour hard cap.
                    # Loop every 1s to service chat queue promptly.
                    deadline = time.time() + 4 * 3600
                    last_health = time.time()
                    while not self._leave_event.is_set() and time.time() < deadline:
                        self._process_chat_queue(page)
                        page.wait_for_timeout(1000)
                        # In-meeting health check every 5 minutes
                        if time.time() - last_health >= 300:
                            last_health = time.time()
                            try:
                                current_url = page.url
                                if "meet.google.com" not in current_url:
                                    log.warning(f"MacOSAdapter: health check — unexpected URL: {current_url}")
                            except Exception:
                                log.warning("MacOSAdapter: health check — page not accessible")

                finally:
                    # ── Clean leave — runs on ALL exit paths ──────────
                    # Navigate away so Meet fires the leave signal, then
                    # close the browser before the `with` block exits.
                    self._page = None
                    try:
                        page.goto("about:blank", timeout=5000)
                        log.info("MacOSAdapter: navigated away — left meeting cleanly")
                    except Exception:
                        pass
                    def _close_browser():
                        try:
                            browser.close()
                        except Exception:
                            pass
                    close_t = threading.Thread(target=_close_browser, daemon=True)
                    close_t.start()
                    close_t.join(timeout=5)
                    if close_t.is_alive():
                        log.warning("MacOSAdapter: browser.close() timed out (5s) — forcing exit")
                    else:
                        log.info("MacOSAdapter: browser closed")
                    self._browser_closed.set()

        except Exception as e:
            log.error(f"MacOSAdapter: browser session error: {e}")
            if not js.ready.is_set():
                js.signal_failure(f"exception: {e}")
        finally:
            pid_file = os.path.join(BROWSER_PROFILE, ".operator.pid")
            try:
                os.remove(pid_file)
            except OSError:
                pass
            if not self._browser_closed.is_set():
                self._browser_closed.set()
