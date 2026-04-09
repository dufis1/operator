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
import queue
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
        self._page = None  # kept for send_chat/read_chat; set/cleared by browser thread
        self._seen_message_ids = set()
        self._chat_queue = queue.Queue()

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
        """Post a message to the Google Meet chat panel.
        Queues the request for the browser thread (Playwright is single-threaded)."""
        result_q = queue.Queue()
        self._chat_queue.put(("send", message, result_q))
        result_q.get(timeout=10)

    def read_chat(self):
        """Return new chat messages since last call.
        Queues the request for the browser thread."""
        result_q = queue.Queue()
        self._chat_queue.put(("read", None, result_q))
        try:
            return result_q.get(timeout=10)
        except queue.Empty:
            return []

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
            log.warning(f"LinuxAdapter: could not open chat panel: {e}")
            try:
                page.screenshot(path="debug/chat_btn_not_found.png")
                log.info("LinuxAdapter: saved debug screenshot to debug/chat_btn_not_found.png")
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
            log.info(f"LinuxAdapter: chat sent: {message!r}")
        except Exception as e:
            log.warning(f"LinuxAdapter: send_chat failed: {e}")

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
                # Extract text — prefer first div[jsname] (any value), fallback chain
                jsname_el = el.locator('div[jsname]')
                if jsname_el.count() > 0:
                    text = jsname_el.first.inner_text().strip()
                else:
                    text = el.evaluate("""el => {
                        const fc = el.children[0]?.childNodes[0];
                        return (fc && fc.textContent) ? fc.textContent.trim() : el.innerText.trim();
                    }""")
                # Extract sender — walk up to 4 parents, find sibling div whose text
                # matches "Name\nTimestamp". Avoids depending on obfuscated class names.
                sender = ""
                try:
                    sender = el.evaluate("""el => {
                        const TIME_RE = /\\d{1,2}:\\d{2}\\s*(AM|PM)/i;
                        let node = el;
                        for (let d = 0; d < 4; d++) {
                            node = node.parentElement;
                            if (!node) break;
                            for (const sib of node.children) {
                                const t = sib.innerText?.trim();
                                if (t && TIME_RE.test(t)) {
                                    const lines = t.split('\\n');
                                    return lines.length >= 2 ? lines[0] : '';
                                }
                            }
                        }
                        return '';
                    }""")
                except Exception:
                    pass
                new_messages.append({"id": msg_id, "sender": sender, "text": text})
        except Exception as e:
            log.warning(f"LinuxAdapter: read_chat failed: {e}")
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

    # ── Waiting room ─────────────────────────────────────────────────

    def _wait_for_admission(self, page):
        """Wait for the host to admit us, with event-driven detection.

        Phase 1: wait up to 10s for the waiting room image to appear — confirms
        the page has settled into the lobby state.
        Phase 2: watch for that image to disappear — fires immediately when the
        host clicks 'Let in', with no polling lag.

        Returns True if admitted, False on timeout or leave().
        """
        timeout_seconds = config.IDLE_TIMEOUT_SECONDS
        deadline = time.time() + timeout_seconds
        wait_start = time.time()
        last_status_log = wait_start
        chunk_ms = 5000  # how often to re-check _leave_event

        WAITING_ROOM_SEL = 'img[alt*="Please wait until a meeting host"]'

        # Phase 1: confirm the page has settled into the waiting room
        log.info("LinuxAdapter: waiting for lobby screen to appear...")
        try:
            page.wait_for_selector(WAITING_ROOM_SEL, state="visible", timeout=10_000)
            log.info("LinuxAdapter: lobby confirmed — watching for host to admit us "
                     f"(timeout={timeout_seconds}s)")
        except Exception:
            elapsed = time.time() - wait_start
            log.info(
                f"LinuxAdapter: lobby screen not detected after {elapsed:.1f}s "
                f"— assuming already admitted or different join flow"
            )
            return True

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
                    f"LinuxAdapter: admitted — lobby screen gone "
                    f"(event-driven, waited {elapsed:.1f}s total)"
                )
                return True
            except Exception:
                if page.is_closed():
                    log.info("LinuxAdapter: browser closed during admission wait — aborting")
                    return False

            if time.time() - last_status_log >= 30:
                elapsed = time.time() - wait_start
                log.info(f"LinuxAdapter: still in waiting room ({elapsed:.0f}s elapsed)")
                last_status_log = time.time()

        elapsed = time.time() - wait_start
        if self._leave_event.is_set():
            log.info(f"LinuxAdapter: admission wait cancelled (leave called after {elapsed:.0f}s)")
        else:
            log.warning(f"LinuxAdapter: admission timeout after {elapsed:.0f}s")
        return False

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
                    # DISPLAY is already set in os.environ by the caller (scripts/run_linux.py).
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

                # Turn off camera and confirm before joining
                save_debug(page, "pre_camera_toggle")
                try:
                    cam_btn = page.get_by_role("button", name="Turn off camera")
                    cam_btn.wait_for(timeout=5000)
                    cam_btn.click()
                    log.info("LinuxAdapter: clicked 'Turn off camera'")
                    try:
                        page.wait_for_selector(
                            '[role="button"][data-is-muted="true"][aria-label*="camera"]',
                            timeout=3000,
                        )
                        log.info("LinuxAdapter: camera confirmed off (data-is-muted=true)")
                    except Exception:
                        log.warning("LinuxAdapter: camera toggle clicked but could not confirm off state")
                        save_debug(page, "camera_not_confirmed")
                except Exception:
                    log.warning("LinuxAdapter: 'Turn off camera' button not found — camera may be on")
                    save_debug(page, "camera_btn_missing")

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

                # Race all join buttons — avoids 5s timeout per missing button
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
                            log.debug(f"LinuxAdapter: clicked {label!r}")
                            break
                except Exception:
                    pass

                if clicked_label is None:
                    save_debug(page, "join_fail")
                    log.warning("LinuxAdapter: could not find join button")
                    js.signal_failure("no_join_button")
                    self._page = None
                    browser.close()
                    if hasattr(browser, "_raw_browser"):
                        browser._raw_browser.close()
                    return

                if clicked_label == "Ask to join":
                    if not self._wait_for_admission(page):
                        save_debug(page, "admission_fail")
                        js.signal_failure("admission_timeout")
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

                # Hold until leave() signals or 4-hour hard cap.
                # Loop every 1s to service chat queue promptly.
                deadline = time.time() + 4 * 3600
                last_health = time.time()
                while not self._leave_event.is_set() and time.time() < deadline:
                    self._process_chat_queue(page)
                    time.sleep(1)
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
