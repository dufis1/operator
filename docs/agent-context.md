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

**Phase:** Phase 9 hardening in progress. Chat MVP + MCP integration feature-complete.
**What just happened (session 68, April 9, 2026):**

Session 68: Planning session for step 9.11 (chat message size management). No code written. Full implementation plan designed. Key decisions: (1) Response length target ~600 chars / ~150 tokens — meeting chat is a side panel, not a dedicated chat UI. (2) Two separate problems identified — LLM verbosity (fix via system prompt + max_tokens) and tool result bloat (fix via size guard + error handling). (3) Truncation sequence: archive all old tool results in one sweep, then clear history entirely as last resort — no intermediate "drop one turn" complexity. (4) "Archive-with-metadata" pattern established: always replace removed content with a placeholder naming what happened and what the model can do about it. (5) Steps 9.8/9.9/9.10 moved to Phase 12 as 12.14/12.15/12.16. (6) New step 12.17 added: MCP-specific format and context hints for all supported servers after 12.1 hints infrastructure is built.

**Previous sessions:** Session 67: step 9.7 (calendar polling startup latency). Session 66: step 9.6 (simultaneous meeting handling). Session 65: step 9.5 (security audit). Session 64: step 9.4 (race condition audit). Session 63: step 9.1 (UI dependency audit + selector hardening).

**MVP scope:** Google Meet only, Mac + Linux. Platform cost is in meeting service (DOM selectors, auth), not OS — Playwright is cross-platform. Zoom/Teams deferred to Phase 14, demand-driven.

**Next action:** Step 9.11 (chat message size management) — investigate Google Meet chat character limits, truncate/summarize long tool results, fix overly verbose Operator responses. Steps 9.8/9.9/9.10 deferred to Phase 12 (post-MVP polish).

**Setup wizard note (session 52):** Step 10.5 added to roadmap — the setup wizard must include an MCP OAuth step that walks the user through authenticating each configured MCP server (Linear, GitHub, etc.) before their first meeting. `mcp-remote` caches tokens locally after initial browser-based auth, so this is a one-time step. Without it, the first meeting launch would trigger an OAuth popup mid-join.

**Top open issue (voice, deferred):** Premature finalization at 0.7s silence threshold cuts off mid-sentence prompts. See `docs/latency.md` for pipeline measurements and six reduction ideas. Will be addressed in Phase 13 (Voice).

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
- **Camera toggle needs wait even in headless** — use `wait_for(timeout=5000)`, then confirm via `data-is-muted="true"`.
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
- **Google Meet chat selectors (verified April 2026, hardened session 62):** Chat button: `get_by_role("button", name="Chat with everyone")`. Input: `textarea[aria-label="Send a message"]`. Messages: `div[data-message-id]`. Message text: `div[jsname]` (any jsname value, with fallback to first child text node) — previously hardcoded to `jsname="dTKtvb"`. Chat panel: dynamically discovered via `textarea.closest('[data-panel-id]')` — previously hardcoded to `data-panel-id="2"`. Send button: `aria-label="Send a message"` (disabled until text entered; use `fill()` + `Enter` instead).
- **Google Meet chat sender name is in a group header, not per-message.** Sender div found by walking up to 4 parents and matching sibling divs whose text contains a time pattern (`/\d{1,2}:\d{2}\s*(AM|PM)/i`). Format: `"SenderName\nTimestamp"` for other participants, just `"Timestamp"` for the browser's own messages. Previously used obfuscated class `div.HNucUd` — replaced with structural time-pattern approach in session 62. Important: use a `foundSender` flag to stop walking on first match, because empty sender (self-message) is falsy and would cause the loop to keep walking into other message groups.
- **Playwright xpath locator silently returns 0 results for sibling selectors.** `el.locator("xpath=../div[...]")` didn't work for finding sibling elements in the Meet chat DOM. Fix: use `el.evaluate()` with native JS `parentElement.querySelector()` instead.
- **`\b` regex word boundary doesn't match `/` prefix.** If the wake phrase is `/operator`, `\boperator\b` won't match because `/` is not a word character. Fix: use `re.escape()` without `\b` anchors.
- **MCP SDK's `stdio_client` uses anyio task groups — cannot split across coroutines.** Manually calling `__aenter__` on `stdio_client` from a separate coroutine (via `run_coroutine_threadsafe`) fails with "Attempted to exit cancel scope in a different task." Fix: each server must run as a single long-lived async task (`_ServerHandle._run()`) that enters the `stdio_client` context and stays alive until shutdown. Tool calls are dispatched to the same event loop via `run_coroutine_threadsafe` to the `_execute_tool` coroutine (which shares the task's session but runs as a separate coroutine — that's fine, the constraint is on the context manager, not the session).
- **`@linear/mcp-server` npm package does not exist.** Linear's official MCP server is remote at `https://mcp.linear.app/mcp`. Use `npx -y mcp-remote https://mcp.linear.app/mcp` to bridge it as a stdio subprocess. Auth is OAuth (browser popup on first run), token cached locally by `mcp-remote`. No `LINEAR_API_KEY` needed.
- **MacOSAdapter/LinuxAdapter "joined" before admission.** Clicking "Ask to join" set `joined = True` immediately without waiting for the host to admit. Operator proceeded to open chat panel while still in the waiting room. Fix: track `clicked_label`, and if "Ask to join" was clicked, call `_wait_for_admission()` (two-phase event-driven: wait for lobby image to appear, then watch for it to disappear). Already existed in CaptionsAdapter.
- **Google Meet creates 2 DOM elements per chat message (different IDs, same text).** When Operator sends a message, the MutationObserver catches both elements. If the echo filter (`_own_messages`) discards the text on the first match, the second element slips through and triggers an echo loop. Fix: batch the discard — collect matched texts during the full message batch, then remove from `_own_messages` after the loop.
- **Camera toggle must be confirmed before join.** Meet defaults to camera on at the pre-join screen. The old race-both-states approach with a 2s timeout silently skipped the toggle on slow renders. Fix (session 62): click "Turn off camera", then wait for `data-is-muted="true"` on the camera button as confirmation. Log WARNING + screenshot if confirmation fails. No post-join re-check — gate it at pre-join.
- **Meet camera/mic toggles are `<div role="button">`, not `<button>`.** CSS selectors like `button[data-is-muted="true"]` silently match nothing. Use `[role="button"][data-is-muted="true"]` instead. This applies to all Meet toolbar controls.
- **`[data-participant-id]` counts UI elements, not participants.** Returns ~2× actual count due to duplicate DOM entries per participant. Use `[data-requested-participant-id]` instead — reliably matches actual in-call participants (tested: 1-on-1, multi-participant, participant leave, invited-but-absent).
- **Calendar poller joins meetings that ended hours ago.** `minutes_until <= JOIN_WINDOW_MINUTES` has no lower bound — a meeting that started 103 minutes ago has `minutes_until = -103.3`, which is `<= 2`. Fix: extract event end time and skip meetings where `now > end_dt`.
- **`run_polling()` re-join on Ctrl+C.** After `run()` returns, the loop called `_stop_event.clear()` then checked the queue — but the second meeting URL was already queued, so it tried to join mid-shutdown. Fix: check `_stop_event.is_set()` before clearing and looping back.
- **In-meeting participant detection: `innerText` vs `aria-label`.** `[data-participant-id]` elements carry participant names in `innerText` in normal view but NOT in portrait mode. `aria-label` attributes (e.g., "More options for Jojo Shapiro") survive all layouts. Fix: check aria-label first, innerText as fallback.
- **Shutdown blocks 14s after browser closes.** `read_chat()` and `get_participant_count()` queue commands for the browser thread. After browser closes, the browser thread stops processing, so callers block until their `result_q.get(timeout=...)` expires (10s + 5s). Fix: drain pending queue commands in the browser thread's finally block, responding with empty results so callers unblock immediately.
- **Ctrl+C during waiting room leaves Chrome running ~60s.** The browser cleanup `finally` only wrapped the in-meeting hold loop. Early returns from auth failure, no join button, or admission cancel/timeout skipped `browser.close()` entirely. With `start_new_session=True` (needed so terminal SIGINT doesn't kill Chrome directly), Chrome outlives Python and stays in the meeting until Meet's heartbeat times out. Fix: lift the `try/finally` to start right after `self._page = page` so all exit paths go through cleanup. Also make `leave()` idempotent (guard on `_leave_event.is_set()`) since both `_shutdown()` and ChatRunner/AgentRunner call it.
- **Deprecated npm `@modelcontextprotocol/server-github` (v0.6.2) fails `search_code` with "Authentication Failed".** The npm package doesn't pass the auth token for the code search endpoint. Other tools like `get_file_contents` work fine. Fix: replaced with official Go binary from `github/github-mcp-server` releases (v0.32.0). The Go binary expects `GITHUB_PERSONAL_ACCESS_TOKEN` (not `GITHUB_TOKEN`).
- **OpenAI parallel tool_calls crash ChatRunner.** When the model returns 2+ tool_calls in one assistant message, `ask()` only returns the first one. The assistant message (with all tool_call_ids) is appended to history, but only one tool result is sent back. OpenAI rejects this with 400: "tool_call_ids did not have response messages." Fix: `parallel_tool_calls=False` in the LLM request. Full parallel support would require ChatRunner to handle multiple confirmations per response.
- **`send_tool_result` must pass tools for follow-up tool calls.** Without `tools` in the kwargs, the LLM can't request another tool call after the first one completes. It emits a text summary instead ("now retrieving auth.py") but can't actually do it. Fix: pass `tools` and `parallel_tool_calls=False` to the completion call in `send_tool_result`, and handle both text and tool_call responses.
- **GitHub MCP `get_file_contents` returns file content as `EmbeddedResource`, not text.** The MCP result has two content parts: a text part ("successfully downloaded text file (SHA: ...)") and an `EmbeddedResource` part containing the actual file content in `c.resource.text`. If you only extract `c.text`, you get the 81-char confirmation and the LLM hallucinates the file contents. Fix: check `hasattr(c, "resource")` and extract `c.resource.text`.
- **GitHub Code Search doesn't index small/new repos.** `search_code` with `repo:dufis1/demo-api` returns 0 results even for code that definitely exists. GitHub's code search indexing is unreliable for repos with few stars/activity. Fix: steer the LLM away from `search_code` toward browsing with `get_file_contents` on directories. System prompt hint: "Avoid search_code — it often returns no results for small repos."
- **LLM uses chat display name as GitHub owner.** When the chat sender is "Jojo Shapiro", gpt-4.1-mini guesses `owner='Jojo'` for GitHub API calls, causing 404s. The `get_me` tool returns the correct login but the model doesn't reliably call it. Fix: resolve `get_me` once at startup in `MCPClient.resolve_github_user()`, inject the login into the LLM system prompt via `inject_github_user()`.
- **Python `\n` in JS comments inside `page.evaluate()` breaks the script.** A comment like `// matches "Name\nTimestamp"` in a triple-quoted Python string sends a literal newline to JS, splitting the comment mid-line and causing a SyntaxError. Fix: avoid `\n` in any JS string/comment inside `evaluate()` — use `"Name + Timestamp"` or similar. Same applies to any Python escape sequence that isn't explicitly doubled.
- **Falsy empty-string check skips self-message sender match.** When extracting sender name via parent-walk, self-messages return `sender = ''`. If the outer loop uses `if (sender) break`, empty string is falsy — the loop continues walking up and matches a different message group's sender div, returning a wrong name. Fix: use a `foundSender` boolean flag to stop on first time-pattern match regardless of whether sender is empty.
- **Queue-depth warning race at dequeue time.** First attempt at the "overlapping meetings" warning in `run_polling` checked `meeting_queue.qsize() > 0` right before calling `self.run(url)`. This missed the common case: CalendarPoller runs on its own thread and may queue meeting 2 *after* meeting 1 has already been dequeued (observed 5ms gap in a live test, and the typical case is seconds/minutes later while meeting 1 is running). The dequeue-time check is blind to meetings queued after that moment. Fix: moved the warning into `CalendarPoller._check_calendar` where it runs at *enqueue* time on the poller's own thread, with an `is_busy` callback from the runner so it can detect "meeting 1 is running" even when the queue is empty.
- **CalendarPoller "already ended" skip logs re-fire every poll cycle.** Old or multi-day events that have ended still appear in Google Calendar's day view and get re-evaluated on every 30s poll. Without deduplication, they produce DEBUG log lines every cycle, drowning real events. Fix: track `_ended_event_ids` in CalendarPoller and only log the skip the first time each event_id is seen. Note: accessibility labels for multi-day events start with a date (e.g., "April 7, 2026 at 7pm to...") not "All day", so the `startswith("all day")` filter doesn't catch them — they rely on the `end_dt` skip instead. That's fine, just be aware if debugging calendar noise.
- **Google Calendar event accessibility label looks like a timestamp but isn't.** `ev.inner_text()` on an event cell returns a comma-separated blob like `'April 7, 2026 at 7pm to April 8, 2026 at 7:52pm, test 2, Operator, Needs RSVP, No location,'`. When this blob appears in a log line next to a local-time Python timestamp like `23:38:02`, it can look like Operator thinks the current time is 7pm. It doesn't — that "7pm" is the scheduled start of the event. Just a reading-the-logs gotcha.
- **`wait_for_selector` on an absent selector silently regresses startup.** The original `CalendarPoller` had a blind `time.sleep(8)` after `page.goto()`. Replacing it with `wait_for_selector("[data-eventid]", timeout=15000)` looked obviously better — but on empty-calendar days the selector never appears and the wait runs to the full 15s, making startup *worse* (16.3s vs the original 9.4s). Tighten the timeout to ~3s and accept the no-events path waits 3s of nothing — the next 30s poll picks up any late events anyway. Always test the empty case when replacing a sleep with a selector wait.
- **Browser profile mtime check via Chromium `Default/Cookies`.** `shutil.copytree` uses `copy2` under the hood, which preserves mtimes. So after a copy, the source and destination `Default/Cookies` have *equal* mtimes — and on the next start, comparing those mtimes is a one-stat shortcut for "has auth state moved on since the last copy?" Used in `CalendarPoller._cal_profile_is_stale` to skip the rmtree+copytree on warm restarts. Don't compare directory mtimes — they only change on entry add/remove, not on file edits.

---

## LLM Interaction Principles

Design patterns for working with LLMs in this product. Read before making architectural decisions about context management or model behavior.

- **Archive-with-metadata pattern:** When removing content from the model's context (tool results, history turns), always replace it with a placeholder that names (1) what happened and (2) what the model can do about it. Example: `[tool result archived — call the tool again with a narrower scope to retrieve more]` is better than silent deletion or `[result omitted]` (which implies error). The model reasons better when it understands its own state and has a recovery path. Applies anywhere context is managed: history trimming, tool result truncation, overflow recovery.

---

## To-Do (non-urgent)

- **Sender time-pattern regex assumes AM/PM format.** The structural sender extraction (session 62 hardening) uses `/\d{1,2}:\d{2}\s*(AM|PM)/i` to find the sender div. Breaks in 24h locales where timestamps render as `"22:35"` without AM/PM. Add 24h pattern `\d{2}:\d{2}` as alternative match when locale support matters.
- **Caption speaker badge still uses fragile class selectors.** `.NWpY1d, .xoMHSc` in `captions_adapter.py` are obfuscated CSS classes. Structural fix identified: use `firstElementChild` positional extraction (speaker is always first child in its wrapper). Not v1-critical — captions are post-v1 voice path (Phase 13).
- **MCP-specific format and context hints (step 12.17).** After finalizing supported MCP servers, add per-server hints covering response format and context window hygiene (prefer targeted calls, avoid whole-file retrieval). Depends on 12.1 hints infrastructure. GitHub format guidance added in step 9.11 as the first instance of this pattern.
- **Revisit LLM history compaction if needed.** Context overflow handling in step 9.11 clears history as a last resort. If long meetings with heavy tool use make this a frequent problem, consider summarizing old turns instead of discarding them (one extra LLM call per compaction). Deferred — meetings are short and 128k context is generous.

---

## Open Questions

1. **Wake phrase customization (voice)** — allow users to set their own wake phrase? Test Whisper reliability on custom phrases first. Chat wake phrase is already configurable via `chat_wake_phrase` in config.yaml.
2. **Linux distro coverage** — Ubuntu/Debian tier-1; PipeWire (Fedora, Ubuntu 22.04+) needs validation.
