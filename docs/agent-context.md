# Operator — Agent Context

*Working memory for coding agents. Human-readable roadmap: `docs/roadmap.md`.*
*Living document — update at the end of each session.*

---

## Working Style (follow for every step)

- One step at a time. Test and commit before moving on. Never batch steps.
- Before making any change: explain what you're about to do and why, in plain language.
- Smallest possible change. If a step can be broken down, do so.
- Don't touch anything out of scope. Note it, don't change it.
- Audience: technical product manager, not a senior engineer. Define terms when used.

---

## Current Status

**Phase:** Chat MVP feature-complete (Phase 8 + 11 in roadmap), shutdown hardened.
**What just happened (session 55, April 7, 2026):**

Session 55: Hardened Ctrl+C shutdown so it works cleanly at any point during startup — not just in-meeting. Previously, pressing Ctrl+C during the waiting room (or any pre-join phase) left Chrome running for ~60s because the browser cleanup `finally` only covered the in-meeting loop. Changes:
- **Lifted cleanup `finally` to cover all exit paths** — `try:` now starts right after `self._page = page`, so every early return (auth failure, can't join, no join button, admission cancel/timeout) goes through the same cleanup: click Leave call, navigate to about:blank as fallback, `browser.close()`, drain chat queue, set `_browser_closed`.
- **`_wait_for_admission` returns reason strings** — `"admitted"`, `"cancelled"`, or `"timeout"` instead of bool. Caller passes the correct reason to `signal_failure` (e.g., `admission_cancelled` vs `admission_timeout`).
- **`leave()` is idempotent** — guards on `_leave_event.is_set()` so the second call (from ChatRunner after `_shutdown()` already called it) produces no duplicate log output.
- **Admission poll chunk 5s→1s** — Ctrl+C during waiting room now responds within ~1s instead of ~5s.
- **Removed duplicate log line** in ChatRunner that re-logged "join failed" for unmatched failure reasons.

**MVP scope:** Google Meet only, Mac + Linux. The OS axis is nearly free (Playwright is cross-platform for chat). The costly axis is meeting platforms (DOM selectors, join flow, auth) — Zoom/Teams deferred to Phase 12 unless a real user needs it.

**Next action:** Step 8.3 (ship to friend). Chat-first MVP with MCP tool use is working end-to-end with clean startup (~4s), clean shutdown at any phase, and polished UX (auto-respond in 1-on-1, first-name greeting).

**Setup wizard note (session 52):** Step 10.5 added to roadmap — the setup wizard must include an MCP OAuth step that walks the user through authenticating each configured MCP server (Linear, GitHub, etc.) before their first meeting. `mcp-remote` caches tokens locally after initial browser-based auth, so this is a one-time step. Without it, the first meeting launch would trigger an OAuth popup mid-join.

**Top open issue (voice, deferred):** Premature finalization at 0.7s silence threshold cuts off mid-sentence prompts. See `docs/latency.md` for pipeline measurements and six reduction ideas. Will be addressed in Phase 9.

**Architecture note (session 47):** CaptionsAdapter and MacOSAdapter have duplicated browser session logic (~150 lines each). User considered refactoring into a shared base but decided against it — chat is shipping first, so keeping them separate avoids unnecessary abstraction. Revisit when both paths need parallel maintenance.

**Architecture note (session 51):** MCP integration uses a dedicated asyncio event loop thread to bridge the async MCP SDK into our sync codebase. Each MCP server runs as a long-lived `_ServerHandle` async task — required because `stdio_client` uses anyio task groups that must stay in one task. Tool names are namespaced as `server__tool` (e.g., `linear__create_issue`) to avoid collisions across servers. Confirmation flow: LLM returns tool_call → Operator asks user in chat → user confirms → tool executes → result fed back to LLM → summary sent. `LLMClient.ask(tools=None)` returns a plain string (voice path unchanged); `ask(tools=[...])` returns a structured dict.

**Architecture note (session 54):** Chat message detection now uses a two-layer approach: MutationObserver in the browser for instant detection, with 0.5s Python polling as a fallback drain cadence. The observer seeds `__operatorSeenIds` with existing messages on install to avoid replaying history. Participant count uses `[data-requested-participant-id]` which reliably tracks actual in-call participants (not invited-but-absent). The count is 2× `rosterCount` due to UI duplicate elements, so we use `requestedParticipants` instead.

---

## Hard-Won Knowledge (read before touching relevant code)

- **Whisper drops first word** without 0.5s silence pad prepended to audio. Never remove.
- **Backchannel echo:** clips play through BlackHole → back into capture. Drain audio buffer after playback.
- **Wake phrase is "operator" only.** "hey operator" rejected (Whisper drops "hey"); "operate" rejected (false positives).
- **ElevenLabs requires paid plan** — free tier gets flagged for abuse.
- **Real Chrome required on macOS** (not Playwright's bundled "Chrome for Testing") — only real Chrome gets mic permission.
- **20s conversation mode timeout** — after response, stays in listening mode 20s before idle.
- **ScreenCaptureKit requires `.app` bundle** on macOS — silently fails from plain Python script.
- **ScreenCaptureKit TCC entries are tied to codesign identity** — if the `audio_capture` binary is recompiled without a stable `--identifier`, macOS generates a hash-based identity. After a TCC reset or macOS update, the old identity's permission entry becomes stale — `startCapture` silently hangs forever. Fix: always sign with `codesign --force --sign - --identifier com.operator.audio-capture audio_capture` after compiling.
- **ScreenCaptureKit phantom hang (March 2026):** `startCapture` hung despite permission being on. Root cause: macOS `tccd` cached a stale permission denial. Recovery: (1) `tccutil reset ScreenCapture`, (2) full system restart.
- **Chrome SingletonLock stale after unclean shutdown** — MacOSAdapter pre-launch cleanup removes it automatically. SIGTERM handlers and try/finally blocks prevent it in most cases.
- **Do NOT auto-re-sign `audio_capture` at runtime** — changes code identity, invalidates TCC grant, causes cascading failures.
- **PyObjC packages are fragile** — never install new `pyobjc-framework-*` without checking prior issues.
- **`WHISPER_HALLUCINATIONS` filter** — catches common false positives on silence. Add patterns as found.
- **Audio output device is BlackHole only on macOS** — do NOT change to Multi-Output Device.
- **Ghost session in Meet:** Closing browser without clicking Leave leaves bot registered. Fix: `leave()` must click Leave before `browser.close()`. Handle "Switch here" as fallback.
- **Headless Chrome suppresses audio rendering:** Never use `headless=True`. Use `headless=False` + `--headless=new` on macOS, `headless=False` against Xvfb on Linux.
- **`requestAnimationFrame` is suppressed in `--headless=new`:** Use `setTimeout(fn, 0)` instead. This broke the entire caption MutationObserver pipeline.
- **Google Meet guest join — residential vs. data center IPs:** Residential IPs get guest join. Data center IPs get blocked. Fix for cloud: use exported session via `scripts/auth_export.py`.
- **PulseAudio must be started before Python.** `pulse_setup.sh` creates virtual sinks first.
- **PulseAudio default routing:** Must set default sink/source after creating virtual devices.
- **Chrome does not enumerate PulseAudio monitor sources as microphones.** Use `module-virtual-source` wrapper named `VirtualMic`.
- **PulseAudio user mode (not --system) on droplets.** Add `--exit-idle-time=-1`.
- **Chrome `--no-sandbox` required when running as root.**
- **Playwright `env=` replaces full environment** — never pass it; set env vars in caller.
- **Chrome 130+ uses PipeWire by default** — add `--disable-features=WebRTCPipeWireCapturer` to force PulseAudio.
- **Kokoro 0.9.4 requires spaCy `en_core_web_sm`** — install directly if pip not on PATH.
- **Shift+C is a caption TOGGLE** — check state before pressing. Blind retries toggle on/off/on/off.
- **Join button sequential timeout wastes 5s** — race all three buttons with `.or_()`.
- **HF Hub warning comes from child logger** — suppress both parent and `huggingface_hub.utils._http`.
- **Playwright teardown can hang 20s+** — use `_browser_closed` event, don't join the thread.
- **`_shutdown()` double-call on Ctrl+C** — guard with `_shutdown_called` flag.
- **Browser cleanup must run on ALL exit paths** — wrap in try/finally inside `with sync_playwright()`.
- **Camera toggle needs wait even in headless** — use `wait_for(timeout=2000)`.
- **`in_meeting_wait` required before caption enable** — Meet needs transition time after join.
- **Kokoro voice `am_cloud` does not exist** — use `am_michael` or `am_puck` for American Male.
- **Ctrl+C → Chrome stays in meeting 60s (three issues):** (1) rumps swallows SIGINT, (2) SIGINT kills Chrome via process group — fix with `start_new_session=True`, (3) `browser.close()` ≠ leaving — navigate to `about:blank` first.
- **Google Meet and Calendar use different session scopes** — `auth_export.py` must visit both `accounts.google.com` and `calendar.google.com`.
- **Playwright `headless=True` vs `--headless=new` — different cookie stores.** Calendar poller must use `headless=False` + `--headless=new`.
- **LLM round-trip is 0.9–3s** — mask with backchannels, don't try to eliminate.
- **Filler echo after abort causes speaker-change hang.** Fix: `filler_done.wait()` before resuming captions + 3s timeout on `spec.ready.wait()`.
- **`capture_start` timing crack drops late captions.** Fix: check live `_current_text` before entering new capture cycles.
- **Google ASR rewrites captions between speculative and finalization.** Fix: `_normalize_for_match()` canonicalizes both sides.
- **Abort text-grew false positive from wake phrase prefix.** Fix: use `endswith` comparison.
- **Splitting speculative events exposes TTS regression.** Fix: separate `llm_done`/`ready` checks with in-flight TTS detection.
- **Filler echo infinite loop via Meet speaker misattribution.** Fix: dynamic grace period + `allow_abort=False` on retries.
- **Abort path reads stale `_current_text` during `is_speaking`.** Fix: update text/speaker even during speaking + 0.5s settle delay.
- **Caption punctuation triggers false INCOMPLETE.** Fix: `_strip_mid_punctuation()` before classifier.
- **ASR cosmetic rewrites during `is_speaking` trigger false aborts.** Fix: normalize at abort trigger point.
- **Log string truncation mimics data bugs.** Fix: metadata at INFO, full payload at DEBUG.
- **Abort re-fire duplicates prompt.** Fix: use `_current_text` directly, not concatenation.
- **Bot TTS misattributed to human speaker.** Fix: continuity guard + echo fingerprinting.
- **Stored `_wake_position` invalidated by ASR corrections.** Fix: removed — send full caption text.
- **Daemon classifier threads accumulate as zombies.** Fix: `playback_done` event with 50ms polling.
- **Playback interrupt classifier reads stale text.** Fix: `_abort_text` field set at abort time.
- **Caption hallucinations trigger false playback aborts.** Fix: gate through stream classification.
- **INCOMPLETE race condition loses speech during classifier call.** Fix: streaming first-token classification.
- **MacOSAdapter browser.close() outside `with sync_playwright()` silently fails.** The Playwright connection is torn down when the `with` block exits. If `browser.close()` runs in a `finally` outside that scope, it throws and the browser may not close cleanly. Fix: move the try/finally inside the `with` block, matching CaptionsAdapter's pattern.
- **`time.sleep()` in Playwright hold loop blocks the event loop.** JS callbacks (expose_function, MutationObserver) won't fire. Use `page.wait_for_timeout()` instead — it yields to Playwright's event loop while waiting.
- **Playwright is single-threaded (greenlet).** `send_chat()`/`read_chat()` called from the main thread crash with "Cannot switch to a different thread". Fix: queue commands from main thread, execute them in the browser thread's idle loop. Both adapters use `_chat_queue` + `_process_chat_queue()`.
- **Google Meet chat button is a toggle.** "Chat with everyone" opens AND closes the panel. Clicking it when already open closes it, hiding the textarea. Fix: `_ensure_chat_open()` checks textarea visibility before clicking.
- **Google Meet chat selectors (verified April 2026):** Chat button: `get_by_role("button", name="Chat with everyone")`. Input: `textarea[aria-label="Send a message"]`. Messages: `div[data-message-id]`. Message text: `div[jsname="dTKtvb"]` inside message div. Send button: `aria-label="Send a message"` (disabled until text entered; use `fill()` + `Enter` instead).
- **Google Meet chat sender name is in a group header, not per-message.** Sender name lives in `div.HNucUd` which is a sibling of the message's grandparent (depth=1 from message div). Format: `"SenderName\nTimestamp"` for other participants, just `"Timestamp"` for the browser's own messages. Consecutive messages from the same sender share one header. Use `el.evaluate()` with a JS walk-up loop — Playwright's xpath locator (`../div[contains(@class,'HNucUd')]`) silently fails for this.
- **Playwright xpath locator silently returns 0 results for sibling selectors.** `el.locator("xpath=../div[...]")` didn't work for finding sibling elements in the Meet chat DOM. Fix: use `el.evaluate()` with native JS `parentElement.querySelector()` instead.
- **`\b` regex word boundary doesn't match `/` prefix.** If the wake phrase is `/operator`, `\boperator\b` won't match because `/` is not a word character. Fix: use `re.escape()` without `\b` anchors.
- **MCP SDK's `stdio_client` uses anyio task groups — cannot split across coroutines.** Manually calling `__aenter__` on `stdio_client` from a separate coroutine (via `run_coroutine_threadsafe`) fails with "Attempted to exit cancel scope in a different task." Fix: each server must run as a single long-lived async task (`_ServerHandle._run()`) that enters the `stdio_client` context and stays alive until shutdown. Tool calls are dispatched to the same event loop via `run_coroutine_threadsafe` to the `_execute_tool` coroutine (which shares the task's session but runs as a separate coroutine — that's fine, the constraint is on the context manager, not the session).
- **`@linear/mcp-server` npm package does not exist.** Linear's official MCP server is remote at `https://mcp.linear.app/mcp`. Use `npx -y mcp-remote https://mcp.linear.app/mcp` to bridge it as a stdio subprocess. Auth is OAuth (browser popup on first run), token cached locally by `mcp-remote`. No `LINEAR_API_KEY` needed.
- **MacOSAdapter/LinuxAdapter "joined" before admission.** Clicking "Ask to join" set `joined = True` immediately without waiting for the host to admit. Operator proceeded to open chat panel while still in the waiting room. Fix: track `clicked_label`, and if "Ask to join" was clicked, call `_wait_for_admission()` (two-phase event-driven: wait for lobby image to appear, then watch for it to disappear). Already existed in CaptionsAdapter.
- **Google Meet creates 2 DOM elements per chat message (different IDs, same text).** When Operator sends a message, the MutationObserver catches both elements. If the echo filter (`_own_messages`) discards the text on the first match, the second element slips through and triggers an echo loop. Fix: batch the discard — collect matched texts during the full message batch, then remove from `_own_messages` after the loop.
- **Google Meet re-enables camera after join.** Camera toggled off on the pre-join screen can reappear as on after clicking Join. Fix: add a second camera check after the in-meeting indicator is detected.
- **`[data-participant-id]` counts UI elements, not participants.** Returns ~2× actual count due to duplicate DOM entries per participant. Use `[data-requested-participant-id]` instead — reliably matches actual in-call participants (tested: 1-on-1, multi-participant, participant leave, invited-but-absent).
- **Shutdown blocks 14s after browser closes.** `read_chat()` and `get_participant_count()` queue commands for the browser thread. After browser closes, the browser thread stops processing, so callers block until their `result_q.get(timeout=...)` expires (10s + 5s). Fix: drain pending queue commands in the browser thread's finally block, responding with empty results so callers unblock immediately.
- **Ctrl+C during waiting room leaves Chrome running ~60s.** The browser cleanup `finally` only wrapped the in-meeting hold loop. Early returns from auth failure, no join button, or admission cancel/timeout skipped `browser.close()` entirely. With `start_new_session=True` (needed so terminal SIGINT doesn't kill Chrome directly), Chrome outlives Python and stays in the meeting until Meet's heartbeat times out. Fix: lift the `try/finally` to start right after `self._page = page` so all exit paths go through cleanup. Also make `leave()` idempotent (guard on `_leave_event.is_set()`) since both `_shutdown()` and ChatRunner/AgentRunner call it.

---

## Open Questions

1. **Wake phrase customization (voice)** — allow users to set their own wake phrase? Test Whisper reliability on custom phrases first. Chat wake phrase is already configurable via `chat_wake_phrase` in config.yaml.
2. **Linux distro coverage** — Ubuntu/Debian tier-1; PipeWire (Fedora, Ubuntu 22.04+) needs validation.
