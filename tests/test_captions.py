"""
Caption timing experiment — measures Google Meet's caption DOM behavior.

Usage:
    source venv/bin/activate
    python tests/test_captions.py https://meet.google.com/xxx-yyyy-zzz

What it measures:
    - Time between mutations within a single caption node (update cadence)
    - Time gap that triggers Meet to create a new node (silence threshold)
    - Whether Meet ever splits mid-sentence into a new node
    - Speaker label reliability across node boundaries
    - Caption latency from speech to first DOM appearance

Logs verbose timing data and prints a summary report at the end.
"""

import os
import sys
import time
import logging

from playwright.sync_api import sync_playwright

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE)

import config
from connectors.session import detect_page_state, validate_auth_state, inject_cookies, save_debug

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("test_captions")

BROWSER_PROFILE = os.path.join(_BASE, config.BROWSER_PROFILE_DIR)
LISTEN_SECONDS = 180

# ── Caption DOM selectors (from reference implementation) ────────────

# Speaker name badge inside a caption node
BADGE_SEL = ".NWpY1d, .xoMHSc"

# JavaScript injected into the page to observe captions with timing data
CAPTION_OBSERVER_JS = """
(() => {
    const badgeSel = ".NWpY1d, .xoMHSc";
    let lastSpeaker = "Unknown";
    let nextNodeId = 1;

    // Track per-node: last emitted text + assigned node ID
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

        // Dedup: skip if this exact node already emitted this exact text
        if (state.lastText === txt) return;
        state.lastText = txt;
        lastSpeaker = spk;

        window.__onCaption(spk, txt, state.id, performance.now());
    };

    // Batch mutations per animation frame
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

    console.log("[operator] caption observer injected");
})();
"""


# ── Helpers ──────────────────────────────────────────────────────────

def click_if_visible(page, selector, timeout=3000):
    try:
        elem = page.locator(selector)
        elem.wait_for(state="visible", timeout=timeout)
        elem.click()
        return True
    except Exception:
        return False


def join_meeting(page):
    """Click the join button. Mirrors our MacOSAdapter logic."""
    # Dismiss notifications popup
    click_if_visible(page, 'button:has-text("Not now")', timeout=3000)
    click_if_visible(page, 'button:has-text("Got it")', timeout=2000)

    # Mute mic and camera
    click_if_visible(page, 'button[aria-label*="Turn off microphone"]', timeout=3000)
    click_if_visible(page, 'button[aria-label*="Turn off camera"]', timeout=3000)

    # Try "Continue without microphone and camera" (headless mode)
    click_if_visible(page, 'button:has-text("Continue without microphone and camera")', timeout=3000)

    # Click join
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
    """Wait until we're actually in the meeting by polling for in-call indicators."""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        # "Leave call" button means we're in
        try:
            if page.locator('button[aria-label*="Leave call"]').is_visible(timeout=500):
                return True
        except Exception:
            pass
        # Admitted text
        try:
            if page.locator('text="You\'ve been admitted"').is_visible(timeout=500):
                return True
        except Exception:
            pass
        # Solo in meeting
        try:
            if page.locator('text="You\'re the only one here"').is_visible(timeout=500):
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def enable_captions(page):
    """Enable captions via Shift+C shortcut, with button fallback."""
    # Wait for UI to stabilize
    page.wait_for_timeout(3000)

    # Dismiss any overlays
    for _ in range(5):
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)

    # Try Shift+C shortcut (up to 10 attempts)
    for i in range(10):
        log.info(f"Caption enable attempt {i+1}: pressing Shift+C")
        page.keyboard.down("Shift")
        page.keyboard.press("c")
        page.keyboard.up("Shift")

        # Check if captions region appeared
        try:
            page.locator('[role="region"][aria-label*="Captions"]').wait_for(timeout=1500)
            log.info("Captions enabled via Shift+C")
            return True
        except Exception:
            pass

        # Check if "Turn off captions" button exists (meaning captions are already on)
        try:
            off_btn = page.locator('button[aria-label*="Turn off captions"]')
            if off_btn.is_visible(timeout=500):
                log.info("Captions already enabled")
                return True
        except Exception:
            pass

        page.wait_for_timeout(500)

    # Fallback: click the CC button directly
    log.info("Shift+C failed — trying CC button fallback")
    page.mouse.move(500, 700)  # hover to reveal toolbar
    page.wait_for_timeout(300)

    if click_if_visible(page, 'button[aria-label*="Turn on captions"]', timeout=4000):
        try:
            page.locator('[role="region"][aria-label*="Captions"]').wait_for(timeout=5000)
            log.info("Captions enabled via button fallback")
            return True
        except Exception:
            pass

    # Debug: dump visible regions
    regions = page.locator('[role="region"]').all()
    for r in regions:
        try:
            label = r.get_attribute("aria-label")
            log.info(f"  region: {label}")
        except Exception:
            pass

    save_debug(page, "captions_failed")
    log.error("Could not enable captions")
    return False


# ── Main ─────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python tests/test_captions.py <meet-url>")
        sys.exit(1)

    meeting_url = sys.argv[1]

    # ── Timing data structures ──
    # Each event: (python_time, js_perf_time, node_id, speaker, full_text, delta)
    events = []
    # Per-node tracking: {node_id: {speaker, texts: [(py_time, js_time, full_text)]}}
    nodes = {}
    # Track last node_id to detect new-node boundaries
    last_node_id = [None]

    def on_caption(speaker, text, node_id, js_time):
        py_time = time.time()
        node_id = int(node_id)

        # Initialize node tracking
        if node_id not in nodes:
            nodes[node_id] = {"speaker": speaker, "updates": []}
            # Log gap from previous node
            if last_node_id[0] is not None and last_node_id[0] in nodes:
                prev = nodes[last_node_id[0]]
                prev_last_time = prev["updates"][-1][0]
                gap = py_time - prev_last_time
                prev_final_text = prev["updates"][-1][2]
                log.info(f"")
                log.info(f"  ── NEW NODE #{node_id} (speaker: {speaker}) ──")
                log.info(f"     gap from previous node: {gap:.2f}s")
                log.info(f"     previous node #{last_node_id[0]} final text: \"{prev_final_text}\"")
            else:
                log.info(f"")
                log.info(f"  ── FIRST NODE #{node_id} (speaker: {speaker}) ──")

        # Compute delta
        prev_updates = nodes[node_id]["updates"]
        if prev_updates:
            prev_text = prev_updates[-1][2]
            dt = py_time - prev_updates[-1][0]
            if text.startswith(prev_text):
                delta = text[len(prev_text):].strip()
            else:
                delta = text
            log.info(f"  node#{node_id} +{dt:.2f}s  +\"{delta}\"  (full: \"{text}\")")
        else:
            delta = text
            log.info(f"  node#{node_id}         \"{text}\"")

        nodes[node_id]["updates"].append((py_time, js_time, text))
        events.append((py_time, js_time, node_id, speaker, text, delta))
        last_node_id[0] = node_id

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

        # Expose the caption callback before navigating
        page.expose_function("__onCaption", on_caption)

        # Navigate and join
        log.info(f"Navigating to {meeting_url}")
        page.goto(meeting_url, wait_until="domcontentloaded", timeout=30000)
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
                log.error(f"Cannot recover session — state: {state}")
                save_debug(page, "captions_auth_fail")
                browser.close()
                sys.exit(1)

        if state == "cant_join":
            log.error("Can't join this meeting")
            save_debug(page, "captions_cant_join")
            browser.close()
            sys.exit(1)

        if not join_meeting(page):
            save_debug(page, "captions_join_fail")
            browser.close()
            sys.exit(1)

        if not wait_until_joined(page):
            log.error("Not admitted to meeting within timeout")
            save_debug(page, "captions_not_admitted")
            browser.close()
            sys.exit(1)

        log.info("In meeting — enabling captions")

        if not enable_captions(page):
            browser.close()
            sys.exit(1)

        # Wait for aria-live region (where captions actually render)
        try:
            page.locator("[aria-live]").first.wait_for(timeout=10000)
            log.info("Caption aria-live region found")
        except Exception:
            log.warning("No aria-live region detected — observer may still work")

        # Inject the MutationObserver
        page.evaluate(CAPTION_OBSERVER_JS)
        log.info(f"Observer injected — listening for {LISTEN_SECONDS}s...")
        log.info("=" * 60)

        # Listen — use page.wait_for_timeout so Playwright keeps dispatching
        # expose_function callbacks (time.sleep blocks the event loop)
        start = time.time()
        try:
            while time.time() - start < LISTEN_SECONDS:
                page.wait_for_timeout(1000)
        except KeyboardInterrupt:
            log.info("Interrupted by user")

        log.info("=" * 60)
        log.info(f"Done. Captured {len(events)} caption events across {len(nodes)} nodes.")

        # ── Summary Report ──
        log.info("")
        log.info("=" * 60)
        log.info("TIMING REPORT")
        log.info("=" * 60)

        # Per-node summary
        for nid in sorted(nodes.keys()):
            n = nodes[nid]
            updates = n["updates"]
            final_text = updates[-1][2]
            duration = updates[-1][0] - updates[0][0] if len(updates) > 1 else 0
            log.info(f"")
            log.info(f"  Node #{nid} — speaker: {n['speaker']}")
            log.info(f"    updates: {len(updates)}")
            log.info(f"    duration: {duration:.2f}s (first update to last)")
            log.info(f"    final text: \"{final_text}\"")

            if len(updates) > 1:
                intervals = [updates[i][0] - updates[i-1][0] for i in range(1, len(updates))]
                log.info(f"    update intervals: {', '.join(f'{dt:.2f}s' for dt in intervals)}")
                log.info(f"    avg interval: {sum(intervals)/len(intervals):.2f}s")
                log.info(f"    min interval: {min(intervals):.2f}s")
                log.info(f"    max interval: {max(intervals):.2f}s")

        # Inter-node gaps (silence thresholds)
        sorted_nids = sorted(nodes.keys())
        if len(sorted_nids) > 1:
            log.info(f"")
            log.info(f"  ── Inter-node gaps (silence that triggers new node) ──")
            gaps = []
            for i in range(1, len(sorted_nids)):
                prev_nid = sorted_nids[i-1]
                curr_nid = sorted_nids[i]
                prev_last = nodes[prev_nid]["updates"][-1][0]
                curr_first = nodes[curr_nid]["updates"][0][0]
                gap = curr_first - prev_last
                prev_speaker = nodes[prev_nid]["speaker"]
                curr_speaker = nodes[curr_nid]["speaker"]
                same = "same speaker" if prev_speaker == curr_speaker else f"speaker change: {prev_speaker} → {curr_speaker}"
                log.info(f"    node #{prev_nid} → #{curr_nid}: {gap:.2f}s ({same})")
                gaps.append(gap)

            log.info(f"")
            log.info(f"    avg gap: {sum(gaps)/len(gaps):.2f}s")
            log.info(f"    min gap: {min(gaps):.2f}s")
            log.info(f"    max gap: {max(gaps):.2f}s")

        # Speaker summary
        speakers = set(n["speaker"] for n in nodes.values())
        log.info(f"")
        log.info(f"  ── Speakers observed: {len(speakers)} ──")
        for spk in speakers:
            spk_nodes = [nid for nid, n in nodes.items() if n["speaker"] == spk]
            log.info(f"    {spk}: {len(spk_nodes)} caption nodes")

        log.info("")
        log.info("=" * 60)

        # Leave meeting
        click_if_visible(page, 'button[aria-label*="Leave call"]', timeout=3000)
        page.wait_for_timeout(2000)
        browser.close()

    sys.exit(0 if len(events) > 0 else 1)


if __name__ == "__main__":
    main()
