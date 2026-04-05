"""
Google Meet captions connector for Operator.

Uses Playwright to join Google Meet and a MutationObserver on the captions DOM
to stream transcript text back to Python — replacing the ScreenCaptureKit +
Whisper audio path with Meet's built-in speech-to-text.

Still uses Playwright for browser automation and mpv + BlackHole for TTS
playback (same as MacOSAdapter). The difference is input: DOM text instead
of raw audio.
"""
import logging
import os
import subprocess
import threading
import time

from playwright.sync_api import sync_playwright
import config

from .base import MeetingConnector
from .session import JoinStatus, detect_page_state, validate_auth_state, inject_cookies, save_debug, _chrome_lock_is_live, _chrome_kill_and_clear, _write_operator_pid

log = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BLACKHOLE_DEVICE = "coreaudio/BlackHole2ch_UID"
BROWSER_PROFILE = os.path.join(_BASE, config.BROWSER_PROFILE_DIR)

# Known junk text emitted by Meet UI elements inside the caption region.
# Material icon ligatures and system messages — not speech.
_ICON_NAMES = frozenset({
    "mic_off", "mic_none", "keep_outline", "more_vert",
    "aspect_ratio", "visual_effects", "frame_person",
    "arrow_downward", "settings", "close",
})

_SYSTEM_PHRASES = (
    "You left the meeting",
    "No one else is in this meeting",
    "Returning to home screen",
)


# ── MutationObserver JavaScript ─────────────────────────────────────
# Scoped to the captions region (not document.body) to avoid 100+ junk
# nodes from Meet's UI chrome.  Experiment data: observing body captured
# 131 nodes, only 3 were captions.

CAPTION_OBSERVER_JS = """
(() => {
    const BADGE_SEL = ".NWpY1d, .xoMHSc";
    let lastSpeaker = "Unknown";
    let nextNodeId = 1;
    const nodeState = new WeakMap();

    const getSpeaker = (node) => {
        const badge = node.querySelector(BADGE_SEL);
        return badge?.textContent?.trim() || lastSpeaker;
    };

    const getText = (node) => {
        const clone = node.cloneNode(true);
        clone.querySelectorAll(BADGE_SEL).forEach(el => el.remove());
        return clone.textContent?.trim() ?? "";
    };

    const send = (node) => {
        const txt = getText(node);
        const spk = getSpeaker(node);
        if (!txt || txt.toLowerCase() === spk.toLowerCase()) return;

        let state = nodeState.get(node);
        if (!state) {
            state = { id: nextNodeId++, lastText: null };
            nodeState.set(node, state);
        }

        if (state.lastText === txt) return;
        state.lastText = txt;
        lastSpeaker = spk;

        window.__onCaption(spk, txt, performance.now());
    };

    // Batch DOM mutations per animation frame to avoid flooding Python.
    let pending = new Set();
    let rafScheduled = false;

    const processPending = () => {
        for (const node of pending) send(node);
        pending.clear();
        rafScheduled = false;
    };

    // Wait for the captions region to appear, then scope the observer.
    const REGION_SEL = '[role="region"][aria-label*="Captions"]';
    let totalMutations = 0;
    let lastMutationReport = 0;

    const attachObserver = (root, label) => {
        new MutationObserver((mutations) => {
            totalMutations += mutations.length;

            // Emit a heartbeat every 10 mutations so Python can see the observer is alive
            if (totalMutations - lastMutationReport >= 10) {
                lastMutationReport = totalMutations;
                window.__onCaption("__operator_diag__", "mutation_count=" + totalMutations, performance.now());
            }

            for (const m of mutations) {
                // Handle added HTMLElement nodes
                for (const n of m.addedNodes) {
                    if (n instanceof HTMLElement) pending.add(n);
                }
                // Handle text node additions (Meet may wrap text in spans dynamically)
                for (const n of m.addedNodes) {
                    if (n.nodeType === Node.TEXT_NODE && n.parentElement instanceof HTMLElement) {
                        pending.add(n.parentElement);
                    }
                }
                // Handle characterData updates
                if (m.type === "characterData" && m.target?.parentElement instanceof HTMLElement) {
                    pending.add(m.target.parentElement);
                }
                // Handle subtree attribute changes (e.g. aria-label updates carrying text)
                if (m.type === "attributes" && m.target instanceof HTMLElement) {
                    pending.add(m.target);
                }
            }
            if (!rafScheduled && pending.size > 0) {
                rafScheduled = true;
                setTimeout(processPending, 0);
            }
        }).observe(root, {
            childList: true,
            characterData: true,
            attributes: true,
            subtree: true,
        });
        window.__onCaption("__operator_diag__", "observer_attached label=" + label, performance.now());
    };

    const region = document.querySelector(REGION_SEL);
    if (region) {
        attachObserver(region, "scoped_region");
    } else {
        // Region may not exist yet — poll briefly.
        let attempts = 0;
        const poll = setInterval(() => {
            const el = document.querySelector(REGION_SEL);
            if (el) {
                clearInterval(poll);
                attachObserver(el, "scoped_region_polled");
            } else if (++attempts > 50) {
                clearInterval(poll);
                attachObserver(document.body, "body_fallback");
            }
        }, 100);
    }
})();
"""


def _diagnose_overlay(page, context):
    """Fires only on failure — logs what's covering the UI and saves a snapshot.

    Checks for common Meet overlays (dialogs, popups, tooltips) that might
    be intercepting clicks or keyboard shortcuts.  Zero cost on the happy path.
    """
    try:
        overlays = page.evaluate("""() => {
            const found = [];
            // Modal dialogs
            for (const d of document.querySelectorAll('[role="dialog"], [role="alertdialog"]')) {
                if (d.offsetParent !== null)
                    found.push({type: 'dialog', text: d.innerText.slice(0, 120)});
            }
            // High z-index overlays covering the viewport
            for (const el of document.querySelectorAll('[style*="z-index"]')) {
                const z = parseInt(getComputedStyle(el).zIndex);
                if (z > 1000 && el.offsetWidth > 200 && el.offsetHeight > 100)
                    found.push({type: 'overlay', z, tag: el.tagName, text: el.innerText.slice(0, 80)});
            }
            return found;
        }""")
        if overlays:
            log.warning(f"CaptionsAdapter: UI blocked at '{context}' — overlays detected: {overlays}")
        else:
            log.warning(f"CaptionsAdapter: UI blocked at '{context}' — no overlays found (may be a timing issue)")
        save_debug(page, f"blocked_{context}")
    except Exception as e:
        log.warning(f"CaptionsAdapter: overlay diagnosis failed at '{context}': {e}")


class CaptionsAdapter(MeetingConnector):
    """Caption-based connector for Google Meet on macOS.

    Joins a meeting via Playwright, enables captions, and streams
    caption text updates to a registered callback.  TTS playback
    goes through mpv → BlackHole, same as MacOSAdapter.
    """

    def __init__(self, auth_state_file=None, force=False):
        super().__init__()
        if auth_state_file is None:
            auth_state_file = config.AUTH_STATE_FILE
        self._auth_state_file = auth_state_file
        self._force = force
        self._leave_event = threading.Event()
        self._browser_closed = threading.Event()  # signals browser.close() done
        self._caption_callback = None  # set via set_caption_callback()
        self._page = None              # set once in-meeting (for echo guard)
        self._js_time_offset = None    # maps performance.now() → wall clock time
        self._blackhole_rec_proc = None
        self._on_disconnect = None  # callback fired when browser session exits
        self._last_caption_time = None  # set on first caption; inactivity timer arms from here
        self._browser_thread = None    # saved so leave() can join it

    # ── Public API for caption consumers ─────────────────────────────

    def set_caption_callback(self, fn):
        """Register a callback: fn(speaker: str, text: str, timestamp: float).

        Called on every DOM caption update from the browser thread.
        Must be set BEFORE calling join().
        """
        self._caption_callback = fn

    @property
    def page(self):
        """Expose the Playwright page for echo-guard mic mute/unmute."""
        return self._page

    # ── MeetingConnector interface ───────────────────────────────────

    def join(self, meeting_url):
        self._leave_event.clear()
        self.join_status = JoinStatus()
        if config.DEBUG_AUDIO:
            self._start_blackhole_recording()
        self._browser_thread = threading.Thread(
            target=self._browser_session,
            args=(meeting_url,),
            daemon=True,
            name="CaptionsAdapter-browser",
        )
        self._browser_thread.start()
        log.info(f"CaptionsAdapter: joining {meeting_url}")

    def get_audio_stream(self):
        raise NotImplementedError(
            "CaptionsAdapter does not provide an audio stream — "
            "use set_caption_callback() instead"
        )

    def send_audio(self, audio_data):
        proc = subprocess.Popen(
            ["mpv", "--no-terminal", f"--audio-device={BLACKHOLE_DEVICE}", "--", "-"],
            stdin=subprocess.PIPE,
        )
        proc.stdin.write(audio_data)
        proc.stdin.close()
        proc.wait()

    def send_chat(self, message):
        log.info(f"CaptionsAdapter: chat not yet implemented (message: {message!r})")

    def leave(self):
        self._leave_event.set()
        # Wait for browser.close() to finish — not for the full browser thread,
        # which can hang in `with sync_playwright()` teardown for 20s+.
        if self._browser_thread and self._browser_thread.is_alive():
            log.info("CaptionsAdapter: waiting for browser to close...")
            if not self._browser_closed.wait(timeout=10):
                log.warning("CaptionsAdapter: browser close timed out (10s)")
        if self._blackhole_rec_proc:
            self._blackhole_rec_proc.terminate()
            try:
                self._blackhole_rec_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._blackhole_rec_proc.kill()
            self._blackhole_rec_proc = None
        log.info("CaptionsAdapter: left meeting")

    # ── Browser session ──────────────────────────────────────────────

    def _start_blackhole_recording(self):
        import datetime
        os.makedirs(os.path.join(os.path.dirname(os.path.dirname(__file__)), "debug"), exist_ok=True)
        ts = datetime.datetime.now().strftime("%H%M%S")
        out_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), f"debug/blackhole_{ts}.wav")
        try:
            self._blackhole_rec_proc = subprocess.Popen(
                ["sox", "-t", "coreaudio", "BlackHole 2ch", out_path],
                stderr=subprocess.DEVNULL,
            )
            log.info(f"CaptionsAdapter: BlackHole recording → {out_path}")
        except FileNotFoundError:
            log.warning("CaptionsAdapter: sox not found — BlackHole recording skipped (brew install sox)")
            self._blackhole_rec_proc = None

    def _browser_session(self, meeting_url):
        singleton_lock = os.path.join(BROWSER_PROFILE, "SingletonLock")
        if os.path.islink(singleton_lock) or os.path.exists(singleton_lock):
            if _chrome_lock_is_live(singleton_lock):
                if self._force:
                    log.info("CaptionsAdapter: --force: killing existing session")
                    _chrome_kill_and_clear(singleton_lock)
                else:
                    log.error(
                        "CaptionsAdapter: another Operator session is already running — "
                        "stop that session before starting a new one"
                    )
                    self.join_status.signal_failure("already_running")
                    return
            else:
                os.remove(singleton_lock)
                log.info("CaptionsAdapter: removed stale SingletonLock")

        _write_operator_pid(singleton_lock)
        js = self.join_status
        browser = None
        try:
            t_start = time.monotonic()
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

                try:
                    # Register the Python callback BEFORE any navigation so
                    # expose_function is available when the observer JS runs.
                    page.expose_function("__onCaption", self._on_caption_from_js)

                    page.goto(meeting_url, wait_until="domcontentloaded", timeout=30000)
                    t_nav = time.monotonic()
                    log.info(f"TIMING navigation={t_nav - t_browser:.1f}s")
                    # Event-driven: wait for a pre-join or in-meeting element instead of sleeping 8s.
                    # CDP fires a DOM mutation the instant any of these appear.
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
                        log.warning("CaptionsAdapter: no pre-join element detected — proceeding anyway")
                    log.info(f"TIMING pre_join_ready={time.monotonic() - t_nav:.1f}s")

                    if config.DEBUG_AUDIO:
                        save_debug(page, "initial_load")

                    # --- Session recovery (same as MacOSAdapter) ---
                    t_state = time.monotonic()
                    state = detect_page_state(page)
                    log.info(f"TIMING detect_page_state={time.monotonic() - t_state:.1f}s (state={state})")

                    if state == "logged_out":
                        log.warning("CaptionsAdapter: session expired — attempting cookie recovery")
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
                                log.info("CaptionsAdapter: session recovered via cookie injection")
                            else:
                                log.error(f"CaptionsAdapter: recovery failed — state: {state}")
                                save_debug(page, "recovery_fail")
                                js.signal_failure("session_expired")
                                return
                        else:
                            log.error("CaptionsAdapter: no valid auth_state for recovery")
                            save_debug(page, "no_auth_state")
                            js.signal_failure("session_expired")
                            return

                    if state == "cant_join":
                        log.error("CaptionsAdapter: 'can't join this video call'")
                        save_debug(page, "cant_join")
                        js.signal_failure("cant_join")
                        return

                    # --- Pre-join screen ---
                    # Camera: wait briefly for the button, then click if visible.
                    # We run headless so there's no real camera, but Meet may
                    # still show a black feed to other participants.
                    t_prejoin = time.monotonic()
                    cam_off = page.get_by_role("button", name="Turn off camera")
                    cam_on = page.get_by_role("button", name="Turn on camera")
                    try:
                        cam_off.or_(cam_on).wait_for(timeout=2000)
                        if cam_off.is_visible():
                            cam_off.click()
                            log.debug("CaptionsAdapter: camera turned off")
                        else:
                            log.debug("CaptionsAdapter: camera already off")
                    except Exception:
                        log.debug("CaptionsAdapter: camera button not found")
                    log.info(f"TIMING camera_toggle={time.monotonic() - t_prejoin:.1f}s")

                    if config.DEBUG_AUDIO:
                        save_debug(page, "pre_join")

                    t_join = time.monotonic()
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
                                log.debug(f"CaptionsAdapter: clicked {label!r}")
                                break
                    except Exception:
                        pass
                    log.info(f"TIMING join_click={time.monotonic() - t_join:.1f}s ({clicked_label})")

                    if clicked_label is None:
                        _diagnose_overlay(page, "join_button")
                        log.warning("CaptionsAdapter: could not find join button")
                        js.signal_failure("no_join_button")
                        return

                    if clicked_label == "Ask to join":
                        if not self._wait_for_admission(page):
                            save_debug(page, "admission_fail")
                            js.signal_failure("admission_timeout")
                            return

                    log.info("CaptionsAdapter: joined meeting successfully")

                    # Brief wait for in-meeting UI to settle before enabling
                    # captions — Meet needs a moment after join for Shift+C
                    # to register.
                    t_in_meeting = time.monotonic()
                    try:
                        page.wait_for_selector(
                            'button[aria-label*="Leave call"]',
                            timeout=5000,
                        )
                    except Exception:
                        log.warning("CaptionsAdapter: in-meeting indicator not detected — proceeding anyway")
                    log.info(f"TIMING in_meeting_wait={time.monotonic() - t_in_meeting:.1f}s")

                    # Mic unmute
                    t_mic = time.monotonic()
                    mic_on_btn = page.get_by_role("button", name="Turn on microphone")
                    mic_off_btn = page.get_by_role("button", name="Turn off microphone")
                    try:
                        mic_on_btn.or_(mic_off_btn).wait_for(timeout=3000)
                        if mic_on_btn.is_visible():
                            mic_on_btn.click()
                            log.debug("CaptionsAdapter: microphone unmuted")
                        else:
                            log.debug("CaptionsAdapter: mic already on")
                    except Exception:
                        log.debug("CaptionsAdapter: mic button not found")
                    log.info(f"TIMING mic_check={time.monotonic() - t_mic:.1f}s")

                    # --- Enable captions and inject observer ---
                    t_cap = time.monotonic()
                    if not self._enable_captions(page):
                        _diagnose_overlay(page, "captions_enable")
                        js.signal_failure("captions_enable_failed")
                        return
                    log.info(f"TIMING captions_enable={time.monotonic() - t_cap:.1f}s")

                    t_obs = time.monotonic()
                    page.evaluate(CAPTION_OBSERVER_JS)
                    log.info(f"TIMING caption_observer_inject={time.monotonic() - t_obs:.1f}s")
                    log.info("CaptionsAdapter: caption observer injected")
                    if config.DEBUG_AUDIO:
                        save_debug(page, "in_meeting")

                    self._page = page
                    js.signal_success()

                    # Hold until leave() or inactivity timeout (arms on first caption)
                    idle_timeout = config.IDLE_TIMEOUT_SECONDS
                    last_health = time.time()
                    while not self._leave_event.is_set():
                        # Use Playwright's own wait (pumps the CDP event loop) rather than
                        # time.sleep() which blocks it — expose_function callbacks won't fire
                        # unless the Playwright event loop is being pumped.
                        page.wait_for_timeout(1000)

                        now = time.time()

                        # Inactivity check — only armed after first caption
                        if self._last_caption_time is not None:
                            idle_secs = now - self._last_caption_time
                            if idle_secs >= idle_timeout:
                                log.info(f"CaptionsAdapter: no captions for {idle_secs:.0f}s — leaving meeting")
                                self._leave_event.set()
                                break

                        if now - last_health >= 300:
                            last_health = now
                            try:
                                current_url = page.url
                                if "meet.google.com" not in current_url:
                                    log.warning(f"CaptionsAdapter: health check — unexpected URL: {current_url}")
                            except Exception:
                                log.warning("CaptionsAdapter: health check — page not accessible")

                finally:
                    # ── Clean leave — runs on ALL exit paths ──────────
                    # Navigate away so Meet fires the leave signal, then
                    # close the browser before the `with` block exits.
                    try:
                        page.goto("about:blank", timeout=5000)
                        log.info("CaptionsAdapter: navigated away — left meeting cleanly")
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
                        log.warning("CaptionsAdapter: browser.close() timed out (5s) — forcing exit")
                    else:
                        log.info("CaptionsAdapter: browser closed")
                    # Signal leave() that the browser is closed — don't make
                    # it wait for `with sync_playwright()` teardown to finish.
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
            log.error(f"CaptionsAdapter: browser session error: {e}")
            if not js.ready.is_set():
                js.signal_failure(f"exception: {e}")
        finally:
            if hasattr(self, "_orig_stderr"):
                sys.stderr = self._orig_stderr
            self._page = None
            pid_file = os.path.join(BROWSER_PROFILE, ".operator.pid")
            try:
                os.remove(pid_file)
            except OSError:
                pass
            if self._on_disconnect:
                self._on_disconnect()

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
        log.info("CaptionsAdapter: waiting for lobby screen to appear...")
        try:
            page.wait_for_selector(WAITING_ROOM_SEL, state="visible", timeout=10_000)
            log.info("CaptionsAdapter: lobby confirmed — watching for host to admit us "
                     f"(timeout={timeout_seconds}s)")
        except Exception:
            # Waiting room image never appeared — page may have auto-admitted us
            # (e.g. open meeting) or landed in an unexpected state.
            elapsed = time.time() - wait_start
            log.info(
                f"CaptionsAdapter: lobby screen not detected after {elapsed:.1f}s "
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
                    f"CaptionsAdapter: admitted — lobby screen gone "
                    f"(event-driven, waited {elapsed:.1f}s total)"
                )
                return True
            except Exception:
                if page.is_closed():
                    log.info("CaptionsAdapter: browser closed during admission wait — aborting")
                    return False
                # chunk expired, not admitted yet — continue waiting

            if time.time() - last_status_log >= 30:
                elapsed = time.time() - wait_start
                log.info(f"CaptionsAdapter: still in waiting room ({elapsed:.0f}s elapsed)")
                last_status_log = time.time()

        elapsed = time.time() - wait_start
        if self._leave_event.is_set():
            log.info(f"CaptionsAdapter: admission wait cancelled (leave called after {elapsed:.0f}s)")
        else:
            log.warning(f"CaptionsAdapter: admission timeout after {elapsed:.0f}s")
        return False

    # ── Caption enable ───────────────────────────────────────────────

    def _captions_are_on(self, page):
        """Check if captions are currently enabled (non-blocking)."""
        try:
            # "Turn off captions" button only exists when captions are on
            off_btn = page.locator('button[aria-label*="Turn off captions"]')
            if off_btn.is_visible(timeout=300):
                return True
        except Exception:
            pass
        try:
            # Caption region with attached state (may be zero-height when empty)
            region = page.locator('[role="region"][aria-label*="Captions"]')
            if region.count() > 0:
                return True
        except Exception:
            pass
        return False

    def _enable_captions(self, page):
        """Enable captions once at join. Never toggle again."""
        # Check if captions are already on before toggling — the persistent
        # browser profile often retains caption state.  Pressing Shift+C
        # when captions are already on would turn them OFF.
        if self._captions_are_on(page):
            log.info("CaptionsAdapter: captions already enabled (pre-check)")
            return True

        # Try Shift+C shortcut — only press when captions are confirmed off
        for i in range(10):
            t_attempt = time.monotonic()
            log.debug(f"CaptionsAdapter: caption enable attempt {i+1} via Shift+C")
            page.keyboard.down("Shift")
            page.keyboard.press("c")
            page.keyboard.up("Shift")

            # Wait a moment for Meet to process the shortcut
            page.wait_for_timeout(500)

            if self._captions_are_on(page):
                log.info(f"CaptionsAdapter: captions enabled via Shift+C (attempt took {time.monotonic() - t_attempt:.1f}s)")
                return True

            # Captions are still off — wait before next attempt
            page.wait_for_timeout(500)

        # Fallback: hover to reveal bottom bar, click CC button
        log.info("CaptionsAdapter: Shift+C failed — trying button fallback")
        page.mouse.move(500, 700)
        page.wait_for_timeout(300)

        try:
            cc_btn = page.locator('button[aria-label*="Turn on captions"]')
            cc_btn.wait_for(state="visible", timeout=4000)
            cc_btn.click()
            page.locator('[role="region"][aria-label*="Captions"]').wait_for(timeout=5000)
            log.info("CaptionsAdapter: captions enabled via button fallback")
            return True
        except Exception:
            pass

        log.error("CaptionsAdapter: could not enable captions")
        return False

    # ── JS → Python bridge ───────────────────────────────────────────

    def _on_caption_from_js(self, speaker, text, js_timestamp):
        """Called by the browser's MutationObserver on every caption update."""
        # Diagnostic sentinel from observer setup
        if speaker == "__operator_diag__":
            log.info(f"CaptionsAdapter: JS diagnostic — {text}")
            return

        # Filter junk
        stripped = text.strip()
        if not stripped:
            return
        if stripped in _ICON_NAMES:
            return
        if any(stripped.startswith(phrase) for phrase in _SYSTEM_PHRASES):
            log.info(f"CaptionsAdapter: system phrase detected — {stripped!r}")
            return
        # Short fragments that are just the speaker name
        if stripped.lower() == speaker.lower():
            return

        py_now = time.time()
        # Calibrate JS→wall-clock offset on first caption (performance.now() is ms from page load)
        if self._js_time_offset is None:
            self._js_time_offset = py_now - js_timestamp / 1000.0
        timestamp = self._js_time_offset + js_timestamp / 1000.0
        bridge_lag = py_now - timestamp
        log.info(f"caption: [{speaker}] {stripped[:80]}  [bridge_lag={bridge_lag*1000:.0f}ms]")

        self._last_caption_time = py_now

        if self._caption_callback:
            self._caption_callback(speaker, stripped, timestamp)
