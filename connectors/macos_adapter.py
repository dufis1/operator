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
        self._observer_installed = False

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

    def get_participant_count(self):
        """Return participant count via browser thread."""
        result_q = queue.Queue()
        self._chat_queue.put(("participant_count", None, result_q))
        try:
            return result_q.get(timeout=5)
        except queue.Empty:
            return 0

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
            try:
                page.screenshot(path="debug/chat_btn_not_found.png")
                log.info("MacOSAdapter: saved debug screenshot to debug/chat_btn_not_found.png")
            except Exception:
                pass

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

    def _install_chat_observer(self, page):
        """Inject a MutationObserver that queues new chat messages in JS.

        The observer watches for new div[data-message-id] elements and
        stores them in window.__operatorChatQueue. _do_read_chat drains
        this queue instead of scanning the full DOM each time.
        """
        if self._observer_installed:
            return
        try:
            page.evaluate("""() => {
                if (window.__operatorChatObserver) return;
                window.__operatorChatQueue = [];
                window.__operatorSeenIds = new Set();

                // Seed seen IDs with all existing messages so we don't re-process history
                document.querySelectorAll('div[data-message-id]').forEach(el => {
                    window.__operatorSeenIds.add(el.getAttribute('data-message-id'));
                });

                function extractMessage(el) {
                    const msgId = el.getAttribute('data-message-id');
                    if (!msgId || window.__operatorSeenIds.has(msgId)) return null;
                    window.__operatorSeenIds.add(msgId);
                    // Extract text
                    const textEl = el.querySelector('div[jsname="dTKtvb"]');
                    const text = textEl ? textEl.innerText.trim() : el.innerText.trim();
                    // Extract sender
                    let sender = '';
                    let node = el;
                    for (let d = 0; d < 4; d++) {
                        node = node.parentElement;
                        if (!node) break;
                        const h = node.querySelector(':scope > div.HNucUd');
                        if (h) {
                            const lines = h.innerText.trim().split('\\n');
                            sender = lines.length >= 2 ? lines[0] : '';
                            break;
                        }
                    }
                    return {id: msgId, sender: sender, text: text};
                }

                const container = document.querySelector('[data-panel-id="2"]');
                if (!container) return;

                window.__operatorChatObserver = new MutationObserver(mutations => {
                    for (const mut of mutations) {
                        for (const node of mut.addedNodes) {
                            if (node.nodeType !== 1) continue;
                            // Check if the added node itself is a message
                            if (node.matches && node.matches('div[data-message-id]')) {
                                const msg = extractMessage(node);
                                if (msg) window.__operatorChatQueue.push(msg);
                            }
                            // Check descendants
                            if (node.querySelectorAll) {
                                node.querySelectorAll('div[data-message-id]').forEach(el => {
                                    const msg = extractMessage(el);
                                    if (msg) window.__operatorChatQueue.push(msg);
                                });
                            }
                        }
                    }
                });
                window.__operatorChatObserver.observe(container, {childList: true, subtree: true});
            }""")
            self._observer_installed = True
            log.info("MacOSAdapter: chat MutationObserver installed")
        except Exception as e:
            log.warning(f"MacOSAdapter: failed to install chat observer: {e}")

    def _do_read_chat(self, page):
        """Drain the JS-side chat queue populated by the MutationObserver."""
        self._ensure_chat_open(page)
        self._install_chat_observer(page)

        try:
            messages = page.evaluate("""() => {
                const q = window.__operatorChatQueue || [];
                window.__operatorChatQueue = [];
                return q;
            }""")
            if messages:
                log.debug(f"MacOSAdapter: observer drained {len(messages)} new messages")
            return messages
        except Exception as e:
            log.warning(f"MacOSAdapter: read_chat failed: {e}")
            return []

    def _do_get_participant_count(self, page):
        """Count participants via data-requested-participant-id elements."""
        try:
            return page.locator('[data-requested-participant-id]').count()
        except Exception as e:
            log.warning(f"MacOSAdapter: get_participant_count failed: {e}")
            return 0

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
            elif cmd == "participant_count":
                count = self._do_get_participant_count(page)
                result_q.put(count)

    # ── Waiting room ─────────────────────────────────────────────────

    def _wait_for_admission(self, page):
        """Wait for the host to admit us, with event-driven detection.

        Phase 1: wait up to 10s for the waiting room image to appear — confirms
        the page has settled into the lobby state.
        Phase 2: watch for that image to disappear — fires immediately when the
        host clicks 'Let in', with no polling lag.

        Returns "admitted", "cancelled", or "timeout".
        """
        timeout_seconds = config.IDLE_TIMEOUT_SECONDS
        deadline = time.time() + timeout_seconds
        wait_start = time.time()
        last_status_log = wait_start
        chunk_ms = 1000  # how often to re-check _leave_event

        WAITING_ROOM_SEL = 'img[alt*="Please wait until a meeting host"]'

        # Phase 1: confirm the page has settled into the waiting room
        log.info("MacOSAdapter: waiting for lobby screen to appear...")
        try:
            page.wait_for_selector(WAITING_ROOM_SEL, state="visible", timeout=10_000)
            log.info("MacOSAdapter: lobby confirmed — watching for host to admit us "
                     f"(timeout={timeout_seconds}s)")
        except Exception:
            elapsed = time.time() - wait_start
            log.info(
                f"MacOSAdapter: lobby screen not detected after {elapsed:.1f}s "
                f"— assuming already admitted or different join flow"
            )
            return "admitted"

        # Phase 2: event-driven watch for the lobby to go away = admitted
        while not self._leave_event.is_set() and time.time() < deadline:
            remaining_ms = int((deadline - time.time()) * 1000)
            chunk = min(chunk_ms, max(remaining_ms, 0))
            if chunk <= 0:
                break

            try:
                page.wait_for_selector(WAITING_ROOM_SEL, state="detached", timeout=chunk)
                elapsed = time.time() - wait_start
                log.info(
                    f"MacOSAdapter: admitted — lobby screen gone "
                    f"(event-driven, waited {elapsed:.1f}s total)"
                )
                return "admitted"
            except Exception:
                if page.is_closed():
                    log.info("MacOSAdapter: browser closed during admission wait — aborting")
                    return "cancelled"

            if time.time() - last_status_log >= 30:
                elapsed = time.time() - wait_start
                log.info(f"MacOSAdapter: still in waiting room ({elapsed:.0f}s elapsed)")
                last_status_log = time.time()

        elapsed = time.time() - wait_start
        if self._leave_event.is_set():
            log.info(f"MacOSAdapter: admission wait cancelled (leave called after {elapsed:.0f}s)")
            return "cancelled"
        else:
            log.warning(f"MacOSAdapter: admission timeout after {elapsed:.0f}s")
            return "timeout"

    def leave(self):
        """Signal the browser session to close and stop audio capture.
        Safe to call multiple times — only the first call does work."""
        if self._leave_event.is_set():
            return
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
        t_start = time.monotonic()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch_persistent_context(
                    user_data_dir=BROWSER_PROFILE,
                    headless=False,
                    executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    args=["--use-fake-ui-for-media-stream", "--headless=new", "--mute-audio"],
                )
                t_browser = time.monotonic()
                log.info(f"TIMING browser_launch={t_browser - t_start:.1f}s")
                page = browser.pages[0] if browser.pages else browser.new_page()
                self._page = page

                try:
                    page.goto(meeting_url, wait_until="domcontentloaded", timeout=30000)
                    t_nav = time.monotonic()
                    log.info(f"TIMING navigation={t_nav - t_browser:.1f}s")
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
                    log.info(f"TIMING pre_join_ready={time.monotonic() - t_nav:.1f}s")

                    if config.DEBUG_AUDIO:
                        save_debug(page, "initial_load")

                    # --- Session recovery ladder ---
                    t_state = time.monotonic()
                    state = detect_page_state(page)
                    log.info(f"TIMING detect_page_state={time.monotonic() - t_state:.1f}s (state={state})")
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

                    # Turn off camera and confirm before joining
                    t_prejoin = time.monotonic()
                    cam_off = page.get_by_role("button", name="Turn off camera")
                    try:
                        cam_off.wait_for(timeout=5000)
                        cam_off.click()
                        log.info("MacOSAdapter: clicked 'Turn off camera'")
                        # Confirm camera is actually off via data-is-muted attribute
                        try:
                            page.wait_for_selector(
                                'button[data-is-muted="true"][aria-label*="camera"]',
                                timeout=3000,
                            )
                            log.info("MacOSAdapter: camera confirmed off (data-is-muted=true)")
                        except Exception:
                            log.warning("MacOSAdapter: camera toggle clicked but could not confirm off state")
                            save_debug(page, "camera_not_confirmed")
                    except Exception:
                        log.warning("MacOSAdapter: 'Turn off camera' button not found — camera may be on")
                        save_debug(page, "camera_btn_missing")
                    log.info(f"TIMING camera_toggle={time.monotonic() - t_prejoin:.1f}s")

                    if config.DEBUG_AUDIO:
                        save_debug(page, "pre_join")

                    # Race all join buttons — avoids 5s timeout per missing button
                    t_join = time.monotonic()
                    join_now = page.get_by_role("button", name="Join now")
                    ask_join = page.get_by_role("button", name="Ask to join")
                    switch_here = page.get_by_role("button", name="Switch here")
                    clicked_label = None
                    try:
                        join_now.or_(ask_join).or_(switch_here).wait_for(timeout=10000)
                        for label, btn in [("Join now", join_now), ("Ask to join", ask_join), ("Switch here", switch_here)]:
                            if btn.is_visible():
                                btn.click()
                                clicked_label = label
                                log.debug(f"MacOSAdapter: clicked {label!r}")
                                break
                    except Exception:
                        pass

                    if clicked_label is None:
                        save_debug(page, "join_fail")
                        log.warning("MacOSAdapter: could not find join button")
                        js.signal_failure("no_join_button")
                        return

                    log.info(f"TIMING join_click={time.monotonic() - t_join:.1f}s ({clicked_label})")

                    if clicked_label == "Ask to join":
                        admission = self._wait_for_admission(page)
                        if admission != "admitted":
                            save_debug(page, "admission_fail")
                            js.signal_failure(f"admission_{admission}")
                            return

                    log.info("MacOSAdapter: joined meeting successfully")
                    js.signal_success(recovered=recovered)

                    # Event-driven: wait for in-meeting UI instead of sleeping 3s
                    t_in_meeting = time.monotonic()
                    try:
                        page.wait_for_selector(
                            'button[aria-label*="Leave call"]',
                            timeout=5000,
                        )
                    except Exception:
                        log.warning("MacOSAdapter: in-meeting indicator not detected — proceeding anyway")
                    log.info(f"TIMING in_meeting_wait={time.monotonic() - t_in_meeting:.1f}s")

                    # Race both mic states — resolves instantly when mic is already on
                    t_mic = time.monotonic()
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
                    log.info(f"TIMING mic_check={time.monotonic() - t_mic:.1f}s")

                    log.info("MacOSAdapter: in meeting — holding browser open")
                    log.info(f"TIMING total_join={time.monotonic() - t_start:.1f}s")

                    # Hold until leave() signals or 4-hour hard cap.
                    # Loop every 1s to service chat queue promptly.
                    deadline = time.time() + 4 * 3600
                    last_health = time.time()
                    while not self._leave_event.is_set() and time.time() < deadline:
                        self._process_chat_queue(page)
                        page.wait_for_timeout(500)
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
                    # Click Leave call so Meet's server registers the
                    # disconnect immediately (avoids ~60s ghost session).
                    self._page = None
                    try:
                        leave_btn = page.get_by_role("button", name="Leave call")
                        leave_btn.wait_for(timeout=2000)
                        leave_btn.click()
                        page.wait_for_timeout(500)
                        log.info("MacOSAdapter: clicked Leave call")
                    except Exception:
                        # Fall back to navigating away if Leave button not found
                        try:
                            page.goto("about:blank", timeout=3000)
                            log.info("MacOSAdapter: navigated away (Leave button not found)")
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
                    # Drain any pending chat queue commands so callers unblock
                    # immediately instead of waiting for their full timeout.
                    while not self._chat_queue.empty():
                        try:
                            cmd, args, result_q = self._chat_queue.get_nowait()
                            if cmd == "read":
                                result_q.put([])
                            elif cmd == "participant_count":
                                result_q.put(0)
                            else:
                                result_q.put(None)
                        except queue.Empty:
                            break
                    self._browser_closed.set()
                    # Suppress Playwright teardown noise (greenlet/asyncio)
                    import asyncio, io, sys
                    try:
                        loop = asyncio.get_event_loop()
                        loop.set_exception_handler(lambda _loop, _ctx: None)
                    except Exception:
                        pass
                    self._orig_stderr = sys.stderr
                    sys.stderr = io.StringIO()

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
