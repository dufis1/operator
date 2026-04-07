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

**Phase:** Chat-first MVP (Phase 8 in roadmap).
**What just happened (session 48, April 6, 2026):** Wired `LLMClient` into `ChatRunner._handle_message()` — chat messages now get real GPT-4.1-mini responses with conversation history. Verified in live Google Meet: correct answers, follow-up context works (`history_turns=1`), ~1.6s round-trip, clean Ctrl+C shutdown.

**MVP scope:** Google Meet only, Mac + Linux. The OS axis is nearly free (Playwright is cross-platform for chat). The costly axis is meeting platforms (DOM selectors, join flow, auth) — Zoom/Teams deferred to Phase 11 unless a real user needs it.

**Next action (step 8.2.1):** Three chat hardening tasks, discussed and scoped in session 48:
1. **Chat history cap** — `LLMClient` sends full unlimited history today. Add configurable `chat_history_turns` (default ~20 pairs). The existing `MAX_TRANSCRIPT_LINES = 100` in `llm.py` is unused placeholder.
2. **Wake phrase gating** — currently every message triggers an LLM call. For multi-participant meetings, require "operator" in the message to trigger a response. Non-addressed messages should still be added to history as context (so "operator, summarize what was discussed" works).
3. **Sender field extraction** — `read_chat()` returns `sender: ""` for all messages. Extract sender name from DOM so we can: (a) reliably filter bot's own messages instead of brittle text-match, (b) include "who said what" in LLM context.

**Top open issue (voice, deferred):** Premature finalization at 0.7s silence threshold cuts off mid-sentence prompts. See `docs/latency.md` for pipeline measurements and six reduction ideas. Will be addressed in Phase 9.

**Architecture note (session 47):** CaptionsAdapter and MacOSAdapter have duplicated browser session logic (~150 lines each). User considered refactoring into a shared base but decided against it — chat is shipping first, so keeping them separate avoids unnecessary abstraction. Revisit when both paths need parallel maintenance.

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

---

## Open Questions

1. **Wake phrase customization** — allow users to set their own wake phrase? Test Whisper reliability on custom phrases first.
2. **Linux distro coverage** — Ubuntu/Debian tier-1; PipeWire (Fedora, Ubuntu 22.04+) needs validation.
