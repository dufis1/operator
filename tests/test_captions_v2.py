"""
Caption validation experiments — closes all 7 gaps from caption-timing-findings.md.

Usage:
    source venv/bin/activate

    # Run a specific experiment phase:
    python tests/test_captions_v2.py <meet-url> --phase multi-speaker
    python tests/test_captions_v2.py <meet-url> --phase endurance
    python tests/test_captions_v2.py <meet-url> --phase availability

    # Run with late caption enable (for Gap 5 — captions-on timing):
    python tests/test_captions_v2.py <meet-url> --phase availability --late-enable 15

Experiments:
    multi-speaker  — Gaps 1 & 6: multi-speaker nodes, overlapping speech (~5 min)
    endurance      — Gaps 2, 3, 7: text length cap, ASR corrections, technical terms (~15 min)
    availability   — Gaps 4 & 5: free Gmail captions, late caption enable (~3 min)
"""

import argparse
import os
import subprocess
import sys
import time
import logging

from playwright.sync_api import sync_playwright

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE)

import config
from connectors.session import detect_page_state, validate_auth_state, inject_cookies, save_debug

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("test_captions_v2")

BROWSER_PROFILE = os.path.join(_BASE, config.BROWSER_PROFILE_DIR)

# ── Spoken prompt helper ────────────────────────────────────────────

def announce(msg):
    """Print to terminal AND speak aloud via macOS say (non-blocking)."""
    log.info(f">>> {msg}")
    # Remove commas for say command
    clean = msg.replace(",", "")
    subprocess.Popen(["say", clean], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ── Enhanced Caption Observer JS ────────────────────────────────────

BADGE_SEL = ".NWpY1d, .xoMHSc"

CAPTION_OBSERVER_JS = """
(() => {
    const badgeSel = ".NWpY1d, .xoMHSc";
    let lastSpeaker = "Unknown";
    let nextNodeId = 1;
    const nodeState = new WeakMap();

    const getSpeaker = (node) => {
        const badge = node.querySelector(badgeSel);
        return badge?.textContent?.trim() || lastSpeaker;
    };

    const getText = (node) => {
        const clone = node.cloneNode(true);
        clone.querySelectorAll(badgeSel).forEach(el => el.remove());
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

        const prevText = state.lastText || "";
        const isCorrection = prevText.length > 0 && !txt.startsWith(prevText);
        const textLen = txt.length;

        state.lastText = txt;
        lastSpeaker = spk;

        window.__onCaption(spk, txt, state.id, performance.now(), textLen, isCorrection, prevText);
    };

    let pending = new Set();
    let rafScheduled = false;

    const processPending = () => {
        for (const node of pending) send(node);
        pending.clear();
        rafScheduled = false;
    };

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
    }).observe(document.body, {
        childList: true,
        characterData: true,
        subtree: true,
    });

    console.log("[operator] enhanced caption observer injected");
})();
"""


# ── Shared helpers ──────────────────────────────────────────────────

def click_if_visible(page, selector, timeout=3000):
    try:
        elem = page.locator(selector)
        elem.wait_for(state="visible", timeout=timeout)
        elem.click()
        return True
    except Exception:
        return False


def join_meeting(page):
    click_if_visible(page, 'button:has-text("Not now")', timeout=3000)
    click_if_visible(page, 'button:has-text("Got it")', timeout=2000)
    click_if_visible(page, 'button[aria-label*="Turn off microphone"]', timeout=3000)
    click_if_visible(page, 'button[aria-label*="Turn off camera"]', timeout=3000)
    click_if_visible(page, 'button:has-text("Continue without microphone and camera")', timeout=3000)

    for label in ["Join now", "Ask to join", "Switch here", "Join meeting", "Join call", "Join"]:
        try:
            btn = page.get_by_role("button", name=label)
            btn.wait_for(timeout=3000)
            btn.click()
            log.info(f"Clicked '{label}'")
            return True
        except Exception:
            continue

    log.error("Could not find any join button")
    return False


def wait_until_joined(page, timeout=60000):
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        try:
            if page.locator('button[aria-label*="Leave call"]').is_visible(timeout=500):
                return True
        except Exception:
            pass
        try:
            if page.locator('text="You\'ve been admitted"').is_visible(timeout=500):
                return True
        except Exception:
            pass
        try:
            if page.locator('text="You\'re the only one here"').is_visible(timeout=500):
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def enable_captions(page):
    page.wait_for_timeout(3000)
    for _ in range(5):
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)

    for i in range(10):
        log.info(f"Caption enable attempt {i+1}: pressing Shift+C")
        page.keyboard.down("Shift")
        page.keyboard.press("c")
        page.keyboard.up("Shift")

        try:
            page.locator('[role="region"][aria-label*="Captions"]').wait_for(timeout=1500)
            log.info("Captions enabled via Shift+C")
            return True
        except Exception:
            pass

        try:
            off_btn = page.locator('button[aria-label*="Turn off captions"]')
            if off_btn.is_visible(timeout=500):
                log.info("Captions already enabled")
                return True
        except Exception:
            pass

        page.wait_for_timeout(500)

    log.info("Shift+C failed — trying CC button fallback")
    page.mouse.move(500, 700)
    page.wait_for_timeout(300)

    if click_if_visible(page, 'button[aria-label*="Turn on captions"]', timeout=4000):
        try:
            page.locator('[role="region"][aria-label*="Captions"]').wait_for(timeout=5000)
            log.info("Captions enabled via button fallback")
            return True
        except Exception:
            pass

    save_debug(page, "captions_v2_failed")
    log.error("Could not enable captions")
    return False


def wait_listening(page, seconds):
    """Wait while keeping Playwright event loop alive for caption callbacks."""
    start = time.time()
    while time.time() - start < seconds:
        page.wait_for_timeout(1000)


# ── Data collection ─────────────────────────────────────────────────

class CaptionCollector:
    """Collects and analyzes caption events."""

    def __init__(self):
        self.events = []
        self.nodes = {}
        self.last_node_id = None
        self.corrections = []  # (py_time, node_id, old_text, new_text, chars_back)
        self.speaker_transitions = []  # (py_time, from_speaker, to_speaker)
        self.max_text_lengths = {}  # node_id -> max char count seen

    def on_caption(self, speaker, text, node_id, js_time, text_len, is_correction, prev_text):
        py_time = time.time()
        node_id = int(node_id)
        text_len = int(text_len)
        is_correction = bool(is_correction)

        # Track max text length per node
        if node_id not in self.max_text_lengths or text_len > self.max_text_lengths[node_id]:
            self.max_text_lengths[node_id] = text_len

        # Initialize node
        if node_id not in self.nodes:
            self.nodes[node_id] = {"speaker": speaker, "updates": []}

            # Detect speaker transition
            if self.last_node_id is not None and self.last_node_id in self.nodes:
                prev_speaker = self.nodes[self.last_node_id]["speaker"]
                prev_last_time = self.nodes[self.last_node_id]["updates"][-1][0]
                gap = py_time - prev_last_time
                prev_final_text = self.nodes[self.last_node_id]["updates"][-1][2]

                if prev_speaker != speaker:
                    self.speaker_transitions.append((py_time, prev_speaker, speaker))

                log.info(f"")
                log.info(f"  == NEW NODE #{node_id} (speaker: {speaker}) ==")
                log.info(f"     gap from previous node: {gap:.2f}s")
                log.info(f"     previous node #{self.last_node_id} final text ({len(prev_final_text)} chars): \"{prev_final_text[-80:]}\"")
            else:
                log.info(f"")
                log.info(f"  == FIRST NODE #{node_id} (speaker: {speaker}) ==")

        # Track ASR corrections
        if is_correction:
            # Find how far back the correction reached
            old = prev_text
            new = text
            # Find common prefix length
            common = 0
            for i in range(min(len(old), len(new))):
                if old[i] == new[i]:
                    common = i + 1
                else:
                    break
            chars_back = len(old) - common
            self.corrections.append((py_time, node_id, old, new, chars_back))
            log.info(f"  ** CORRECTION node#{node_id}: rewrote {chars_back} chars back")
            log.info(f"     old: \"{old[-60:]}\"")
            log.info(f"     new: \"{new[-60:]}\"")

        # Compute delta and log
        prev_updates = self.nodes[node_id]["updates"]
        if prev_updates:
            prev_t = prev_updates[-1][2]
            dt = py_time - prev_updates[-1][0]
            if text.startswith(prev_t):
                delta = text[len(prev_t):].strip()
            else:
                delta = f"[corrected] {text[-40:]}"
            log.info(f"  node#{node_id} +{dt:.2f}s  len={text_len}  +\"{delta}\"")
        else:
            log.info(f"  node#{node_id}         len={text_len}  \"{text}\"")

        self.nodes[node_id]["updates"].append((py_time, js_time, text))
        self.events.append((py_time, js_time, node_id, speaker, text, text_len, is_correction))
        self.last_node_id = node_id

    def print_report(self, phase_name):
        log.info("")
        log.info("=" * 70)
        log.info(f"REPORT: {phase_name}")
        log.info("=" * 70)

        # Per-node summary
        for nid in sorted(self.nodes.keys()):
            n = self.nodes[nid]
            updates = n["updates"]
            final_text = updates[-1][2]
            duration = updates[-1][0] - updates[0][0] if len(updates) > 1 else 0
            max_len = self.max_text_lengths.get(nid, 0)

            log.info(f"")
            log.info(f"  Node #{nid} -- speaker: {n['speaker']}")
            log.info(f"    updates: {len(updates)}")
            log.info(f"    duration: {duration:.1f}s")
            log.info(f"    max text length: {max_len} chars")
            log.info(f"    final text ({len(final_text)} chars): \"{final_text[-100:]}\"")

            if len(updates) > 1:
                intervals = [updates[i][0] - updates[i-1][0] for i in range(1, len(updates))]
                log.info(f"    avg interval: {sum(intervals)/len(intervals):.2f}s")

        # Speaker transitions
        log.info(f"")
        log.info(f"  -- Speaker transitions: {len(self.speaker_transitions)} --")
        for t, frm, to in self.speaker_transitions:
            log.info(f"    {frm} -> {to}")

        # Unique speakers
        speakers = set(n["speaker"] for n in self.nodes.values())
        log.info(f"  Unique speakers: {speakers}")

        # Corrections
        log.info(f"")
        log.info(f"  -- ASR corrections: {len(self.corrections)} --")
        if self.corrections:
            chars_back_values = [c[4] for c in self.corrections]
            log.info(f"    avg chars back: {sum(chars_back_values)/len(chars_back_values):.0f}")
            log.info(f"    max chars back: {max(chars_back_values)}")
            log.info(f"    min chars back: {min(chars_back_values)}")

        # Text length tracking
        log.info(f"")
        log.info(f"  -- Max text lengths per node --")
        for nid in sorted(self.max_text_lengths.keys()):
            log.info(f"    node #{nid}: {self.max_text_lengths[nid]} chars")

        log.info("=" * 70)


# ── Experiment phases ───────────────────────────────────────────────

def phase_multi_speaker(page, collector):
    """Experiment 1: Multi-speaker nodes + overlapping speech (Gaps 1, 6)."""

    announce("Experiment 1. Multi-speaker and overlapping speech.")
    time.sleep(2)

    announce("Step 1. Please speak alone for 10 seconds. Starting now.")
    wait_listening(page, 12)

    announce("Step 2. Now stay silent. Have the second laptop speak using the say command for 10 seconds.")
    wait_listening(page, 15)

    announce("Step 3. Silence for 5 seconds.")
    wait_listening(page, 7)

    announce("Step 4. Please speak again for 10 seconds. Testing returning speaker.")
    wait_listening(page, 12)

    announce("Step 5. Alternating turns. You say one sentence then the second laptop says one. Repeat 3 times with short pauses.")
    wait_listening(page, 30)

    announce("Step 6. Simultaneous speech. Speak at the same time as the second laptop for 10 seconds. Repeat twice.")
    wait_listening(page, 30)

    announce("Experiment 1 complete.")
    collector.print_report("Multi-Speaker + Overlapping Speech (Gaps 1, 6)")


def phase_endurance(page, collector):
    """Experiment 2: Endurance + ASR corrections + technical terms (Gaps 2, 3, 7)."""

    announce("Experiment 2. Endurance and ASR corrections and technical terms.")
    time.sleep(2)

    # Phase A: ASR corrections
    announce("Phase A. ASR correction window. Say a phrase then pause 1 second then continue. Repeat 5 times.")
    wait_listening(page, 40)

    announce("Now say the word operator then pause 2 seconds then ask a question.")
    wait_listening(page, 15)

    # Phase B: Technical terms
    announce("Phase B. Technical terms. Please read aloud the following terms.")
    time.sleep(1)
    terms = [
        "API endpoint",
        "kubectl apply",
        "PostgreSQL",
        "OAuth2",
        "localhost 3000",
        "pip install numpy",
        "YAML config file",
        "JSON web token",
        "SSH into the server",
        "git rebase dash dash interactive",
    ]
    for term in terms:
        announce(f"Say: {term}")
        wait_listening(page, 5)

    # Phase C: Endurance
    announce("Phase C. Endurance test. Start the say loop on the second laptop now. Will listen for 10 minutes.")
    log.info("")
    log.info("  Paste this on the second laptop:")
    log.info("  for i in $(seq 1 120); do say -v Samantha \"Sentence $i of the endurance test for caption validation.\"; sleep 1; done")
    log.info("")

    # Listen for 10 minutes, logging text length every minute
    start = time.time()
    endurance_duration = 600  # 10 minutes
    last_report = start
    while time.time() - start < endurance_duration:
        page.wait_for_timeout(5000)
        elapsed = time.time() - start
        if time.time() - last_report >= 60:
            last_report = time.time()
            # Log current max text lengths
            for nid, max_len in sorted(collector.max_text_lengths.items()):
                log.info(f"  [{elapsed:.0f}s] node #{nid} max text length: {max_len} chars")

    announce("Experiment 2 complete.")
    collector.print_report("Endurance + ASR Corrections + Technical Terms (Gaps 2, 3, 7)")


def phase_availability(page, collector, late_enable_seconds=0):
    """Experiment 3: Caption availability + late enable (Gaps 4, 5)."""

    announce("Experiment 3. Caption availability and late enable.")
    time.sleep(2)

    if late_enable_seconds > 0:
        # Late enable mode: captions are NOT enabled yet
        announce(f"Late enable mode. Please start speaking now. Captions will be enabled in {late_enable_seconds} seconds.")
        wait_listening(page, late_enable_seconds)

        announce("Enabling captions now.")
        if not enable_captions(page):
            log.error("Failed to enable captions for late-enable test")
            return

        # Inject observer after enabling
        page.evaluate(CAPTION_OBSERVER_JS)
        log.info("Observer injected after late caption enable")

        announce("Continue speaking for 15 more seconds.")
        wait_listening(page, 17)

        announce("Check the log. Any text from before captions were enabled means retroactive capture works.")
    else:
        # Normal mode: captions already enabled before this point
        announce("Phase A. Testing caption availability. Please speak for 30 seconds.")
        wait_listening(page, 35)

        announce("Phase B. Late enable test. We will now disable and re-enable captions.")
        log.info("Toggling captions off...")
        page.keyboard.down("Shift")
        page.keyboard.press("c")
        page.keyboard.up("Shift")
        page.wait_for_timeout(2000)

        announce("Captions are off. Please speak for 15 seconds.")
        wait_listening(page, 17)

        announce("Re-enabling captions now.")
        page.keyboard.down("Shift")
        page.keyboard.press("c")
        page.keyboard.up("Shift")
        page.wait_for_timeout(2000)

        announce("Continue speaking for 15 seconds.")
        wait_listening(page, 17)

    announce("Experiment 3 complete.")
    collector.print_report("Caption Availability + Late Enable (Gaps 4, 5)")


# ── Main ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Caption validation experiments")
    parser.add_argument("meeting_url", help="Google Meet URL")
    parser.add_argument("--phase", required=True,
                        choices=["multi-speaker", "endurance", "availability"],
                        help="Which experiment to run")
    parser.add_argument("--late-enable", type=int, default=0,
                        help="For availability phase: seconds to wait before enabling captions (0 = normal mode)")
    args = parser.parse_args()

    collector = CaptionCollector()

    # Remove stale browser lock
    singleton_lock = os.path.join(BROWSER_PROFILE, "SingletonLock")
    if os.path.exists(singleton_lock):
        os.remove(singleton_lock)

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=BROWSER_PROFILE,
            headless=False,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=["--use-fake-ui-for-media-stream", "--headless=new"],
        )

        page = browser.pages[0] if browser.pages else browser.new_page()

        # Expose enhanced caption callback
        page.expose_function("__onCaption", collector.on_caption)

        # Navigate and join
        log.info(f"Navigating to {args.meeting_url}")
        page.goto(args.meeting_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(8000)

        state = detect_page_state(page)
        log.info(f"Page state: {state}")

        if state == "logged_out":
            auth = validate_auth_state(config.AUTH_STATE_FILE)
            if auth and inject_cookies(browser, auth):
                page.reload(wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(8000)
                state = detect_page_state(page)
            if state != "pre_join":
                log.error(f"Cannot recover session -- state: {state}")
                save_debug(page, "captions_v2_auth_fail")
                browser.close()
                sys.exit(1)

        if state == "cant_join":
            log.error("Can't join this meeting")
            save_debug(page, "captions_v2_cant_join")
            browser.close()
            sys.exit(1)

        if not join_meeting(page):
            save_debug(page, "captions_v2_join_fail")
            browser.close()
            sys.exit(1)

        if not wait_until_joined(page):
            log.error("Not admitted to meeting within timeout")
            save_debug(page, "captions_v2_not_admitted")
            browser.close()
            sys.exit(1)

        log.info("In meeting")

        # For late-enable test, skip caption enable here
        if args.phase == "availability" and args.late_enable > 0:
            log.info("Late-enable mode: skipping initial caption enable")
            # Still inject observer — it will see nothing until captions are turned on
            page.evaluate(CAPTION_OBSERVER_JS)
        else:
            log.info("Enabling captions")
            if not enable_captions(page):
                browser.close()
                sys.exit(1)

            try:
                page.locator("[aria-live]").first.wait_for(timeout=10000)
                log.info("Caption aria-live region found")
            except Exception:
                log.warning("No aria-live region detected")

            page.evaluate(CAPTION_OBSERVER_JS)

        log.info("Observer injected")
        log.info("=" * 70)

        # Run the selected phase
        try:
            if args.phase == "multi-speaker":
                phase_multi_speaker(page, collector)
            elif args.phase == "endurance":
                phase_endurance(page, collector)
            elif args.phase == "availability":
                phase_availability(page, collector, args.late_enable)
        except KeyboardInterrupt:
            log.info("Interrupted by user")
            collector.print_report(f"{args.phase} (interrupted)")

        # Leave meeting
        click_if_visible(page, 'button[aria-label*="Leave call"]', timeout=3000)
        page.wait_for_timeout(2000)
        browser.close()

    sys.exit(0 if len(collector.events) > 0 else 1)


if __name__ == "__main__":
    main()
