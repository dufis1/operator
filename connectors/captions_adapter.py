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
from .session import JoinStatus, detect_page_state, validate_auth_state, inject_cookies, save_debug

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
    const attachObserver = (root) => {
        new MutationObserver((mutations) => {
            for (const m of mutations) {
                for (const n of m.addedNodes) {
                    if (n instanceof HTMLElement) pending.add(n);
                }
                if (m.type === "characterData" && m.target?.parentElement instanceof HTMLElement) {
                    pending.add(m.target.parentElement);
                }
            }
            if (!rafScheduled && pending.size > 0) {
                rafScheduled = true;
                requestAnimationFrame(processPending);
            }
        }).observe(root, {
            childList: true,
            characterData: true,
            subtree: true,
        });
        console.log("[operator] caption observer attached to scoped region");
    };

    const region = document.querySelector(REGION_SEL);
    if (region) {
        attachObserver(region);
    } else {
        // Region may not exist yet — poll briefly.
        let attempts = 0;
        const poll = setInterval(() => {
            const el = document.querySelector(REGION_SEL);
            if (el) {
                clearInterval(poll);
                attachObserver(el);
            } else if (++attempts > 50) {
                clearInterval(poll);
                console.warn("[operator] caption region not found after 5s — falling back to body");
                attachObserver(document.body);
            }
        }, 100);
    }
})();
"""


class CaptionsAdapter(MeetingConnector):
    """Caption-based connector for Google Meet on macOS.

    Joins a meeting via Playwright, enables captions, and streams
    caption text updates to a registered callback.  TTS playback
    goes through mpv → BlackHole, same as MacOSAdapter.
    """

    def __init__(self, auth_state_file=None):
        super().__init__()
        if auth_state_file is None:
            auth_state_file = config.AUTH_STATE_FILE
        self._auth_state_file = auth_state_file
        self._leave_event = threading.Event()
        self._caption_callback = None  # set via set_caption_callback()
        self._page = None              # set once in-meeting (for echo guard)

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
        threading.Thread(
            target=self._browser_session,
            args=(meeting_url,),
            daemon=True,
            name="CaptionsAdapter-browser",
        ).start()
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
        log.info("CaptionsAdapter: left meeting")

    # ── Browser session ──────────────────────────────────────────────

    def _browser_session(self, meeting_url):
        singleton_lock = os.path.join(BROWSER_PROFILE, "SingletonLock")
        if os.path.exists(singleton_lock):
            os.remove(singleton_lock)
            log.info("CaptionsAdapter: removed stale SingletonLock")

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

                # Register the Python callback BEFORE any navigation so
                # expose_function is available when the observer JS runs.
                page.expose_function("__onCaption", self._on_caption_from_js)

                page.goto(meeting_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(8000)

                save_debug(page, "initial_load")

                # --- Session recovery (same as MacOSAdapter) ---
                state = detect_page_state(page)

                if state == "logged_out":
                    log.warning("CaptionsAdapter: session expired — attempting cookie recovery")
                    auth = validate_auth_state(self._auth_state_file)
                    if auth and inject_cookies(browser, auth):
                        page.reload(wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(8000)
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
                try:
                    not_now = page.get_by_role("button", name="Not now")
                    not_now.wait_for(timeout=3000)
                    not_now.click()
                    page.wait_for_timeout(500)
                    log.debug("CaptionsAdapter: dismissed notifications popup")
                except Exception:
                    pass

                try:
                    cam_btn = page.get_by_role("button", name="Turn off camera")
                    cam_btn.wait_for(timeout=3000)
                    cam_btn.click()
                    page.wait_for_timeout(300)
                    log.debug("CaptionsAdapter: camera turned off")
                except Exception:
                    log.debug("CaptionsAdapter: camera button not found or already off")

                save_debug(page, "pre_join")

                joined = False
                for label in ["Join now", "Ask to join", "Switch here"]:
                    try:
                        btn = page.get_by_role("button", name=label)
                        btn.wait_for(timeout=5000)
                        btn.click()
                        joined = True
                        log.debug(f"CaptionsAdapter: clicked {label!r}")
                        break
                    except Exception:
                        continue

                if not joined:
                    save_debug(page, "join_fail")
                    log.warning("CaptionsAdapter: could not find join button")
                    js.signal_failure("no_join_button")
                    return

                log.info("CaptionsAdapter: joined meeting successfully")

                # Ensure mic is unmuted
                page.wait_for_timeout(3000)
                try:
                    mic_btn = page.get_by_role("button", name="Turn on microphone")
                    mic_btn.wait_for(timeout=3000)
                    mic_btn.click()
                    log.debug("CaptionsAdapter: microphone unmuted")
                except Exception:
                    log.debug("CaptionsAdapter: mic already on or button not found")

                # --- Enable captions and inject observer ---
                if not self._enable_captions(page):
                    save_debug(page, "captions_enable_fail")
                    js.signal_failure("captions_enable_failed")
                    return

                page.evaluate(CAPTION_OBSERVER_JS)
                log.info("CaptionsAdapter: caption observer injected")

                self._page = page
                js.signal_success()

                # Hold until leave() or 4-hour cap
                deadline = time.time() + 4 * 3600
                last_health = time.time()
                while not self._leave_event.is_set() and time.time() < deadline:
                    time.sleep(5)
                    if time.time() - last_health >= 300:
                        last_health = time.time()
                        try:
                            current_url = page.url
                            if "meet.google.com" not in current_url:
                                log.warning(f"CaptionsAdapter: health check — unexpected URL: {current_url}")
                        except Exception:
                            log.warning("CaptionsAdapter: health check — page not accessible")

        except Exception as e:
            log.error(f"CaptionsAdapter: browser session error: {e}")
            if not js.ready.is_set():
                js.signal_failure(f"exception: {e}")
        finally:
            self._page = None
            if browser:
                try:
                    browser.close()
                    log.info("CaptionsAdapter: browser closed")
                except Exception:
                    log.debug("CaptionsAdapter: browser already closed")

    # ── Caption enable ───────────────────────────────────────────────

    def _enable_captions(self, page):
        """Enable captions once at join. Never toggle again."""
        page.wait_for_timeout(3000)

        # Dismiss any overlays
        for _ in range(5):
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)

        # Try Shift+C shortcut (up to 10 attempts)
        for i in range(10):
            log.debug(f"CaptionsAdapter: caption enable attempt {i+1} via Shift+C")
            page.keyboard.down("Shift")
            page.keyboard.press("c")
            page.keyboard.up("Shift")

            try:
                page.locator('[role="region"][aria-label*="Captions"]').wait_for(timeout=1500)
                log.info("CaptionsAdapter: captions enabled via Shift+C")
                return True
            except Exception:
                pass

            try:
                off_btn = page.locator('button[aria-label*="Turn off captions"]')
                if off_btn.is_visible(timeout=500):
                    log.info("CaptionsAdapter: captions already enabled")
                    return True
            except Exception:
                pass

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
        # Filter junk
        stripped = text.strip()
        if not stripped:
            return
        if stripped in _ICON_NAMES:
            return
        if any(stripped.startswith(phrase) for phrase in _SYSTEM_PHRASES):
            return
        # Short fragments that are just the speaker name
        if stripped.lower() == speaker.lower():
            return

        timestamp = time.time()
        log.debug(f"caption: [{speaker}] {stripped[:80]}")

        if self._caption_callback:
            self._caption_callback(speaker, stripped, timestamp)
