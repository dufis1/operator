# Race Condition Audit — Step 9.4

*Session 64, April 8, 2026*

## Threading Model Summary

The codebase has 5–8 threads active during a chat-mode meeting:

| Thread | Owner | Purpose |
|--------|-------|---------|
| Main thread | `__main__.py` | Signal handlers, `runner.run()` / `ChatRunner._loop()` |
| Browser thread | `MacOSAdapter` / `CaptionsAdapter` / `LinuxAdapter` | Playwright session, chat queue processing |
| MCP event loop | `MCPClient._loop_thread` | asyncio loop for MCP server tasks |
| TTS init | `AgentRunner` (voice mode) | Background Kokoro model load |
| LLM warmup | `AgentRunner` (voice mode) | Background TCP/TLS warmup |
| Audio read loop | `AgentRunner` (voice mode) | Reads PCM from capture subprocess |
| Stderr reader | `AgentRunner` (voice mode) | Reads capture proc stderr |
| Filler / TTS threads | `AgentRunner` (voice mode) | Short-lived playback threads |
| LatencyProbe | `AgentRunner` (voice mode) | Background mic monitor |

**Chat mode is simpler:** main thread (ChatRunner polling loop), browser thread, and optionally MCP event loop thread.

---

## Findings

### FINDING 1: `LinuxAdapter` hold loop uses `time.sleep()` instead of `page.wait_for_timeout()`

**File:** `connectors/linux_adapter.py:530`
**Severity:** Medium
**Category:** Browser thread coordination

```python
while not self._leave_event.is_set() and time.time() < deadline:
    self._process_chat_queue(page)
    time.sleep(1)  # ← blocks Playwright event loop
```

`MacOSAdapter` correctly uses `page.wait_for_timeout(500)` (line 599), which yields to Playwright's event loop so JS callbacks (MutationObserver, expose_function) can fire. `LinuxAdapter` uses `time.sleep(1)` which blocks the greenlet — any JS callbacks queued during that second are delayed.

This is already documented in agent-context as hard-won knowledge ("time.sleep() in Playwright hold loop blocks the event loop") but was never applied to LinuxAdapter.

**Impact:** Chat observer events in LinuxAdapter may be delayed up to 1s. In practice the LinuxAdapter doesn't use MutationObserver for chat (it uses DOM-scanning `_do_read_chat`), so the impact is limited to potential future features. But it's inconsistent and a latent bug.

**Fix:** Replace `time.sleep(1)` with `page.wait_for_timeout(500)` to match MacOSAdapter.

---

### FINDING 2: `LinuxAdapter` missing chat queue drain on exit

**File:** `connectors/linux_adapter.py:541-555`
**Severity:** Medium
**Category:** Shutdown path

When the hold loop exits, LinuxAdapter clicks "Leave call" and closes the browser, but does NOT drain pending chat queue commands. Compare:

- **MacOSAdapter** (lines 641–652): Drains `_chat_queue` in the finally block, putting empty results so callers unblock immediately.
- **LinuxAdapter** (lines 541–555): No queue drain. If `ChatRunner` has a `read_chat()` or `send_chat()` call in-flight when the browser closes, the caller blocks for the full `result_q.get(timeout=10)` or `result_q.get(timeout=5)` before getting a `queue.Empty` exception.

This is already documented in agent-context as hard-won knowledge ("Shutdown blocks 14s after browser closes") but the fix was only applied to MacOSAdapter.

**Impact:** On Linux, shutdown after browser close is delayed ~10–15s as queued commands time out.

**Fix:** Add the same queue drain pattern from MacOSAdapter's finally block.

---

### FINDING 3: `LinuxAdapter` missing `_browser_closed` event and `leave()` idempotency

**File:** `connectors/linux_adapter.py:46-61, 295-308`
**Severity:** Medium
**Category:** Shutdown path

LinuxAdapter lacks:
1. `_browser_closed` event — MacOSAdapter and CaptionsAdapter both have this so `leave()` can wait for the browser to actually close (with a 10s timeout) before proceeding to kill capture procs.
2. `leave()` idempotency — MacOSAdapter guards with `if self._leave_event.is_set(): return`. LinuxAdapter's `leave()` sets `_leave_event` and tries to terminate `_capture_proc` every time it's called. Since `_shutdown()` in `__main__.py` can call both `runner.stop()` (which may call `leave()`) and then `connector.leave()` directly, this can double-terminate.

**Impact:** Double-terminate on `_capture_proc` is mostly harmless (already handles exceptions), but it's a code smell and the missing `_browser_closed` wait means `leave()` returns before the browser is actually closed — capture proc cleanup may race with browser teardown.

**Fix:** Add `_browser_closed` event and idempotent `leave()` guard matching MacOSAdapter pattern.

---

### FINDING 4: `LinuxAdapter._browser_session` early returns don't go through try/finally

**File:** `connectors/linux_adapter.py:382-409, 482-500`
**Severity:** Medium
**Category:** Shutdown path

Multiple early return paths (session_expired, cant_join, no_join_button, admission_timeout) manually call `browser.close()` + `_raw_browser.close()` then `return`. These are NOT inside a try/finally block.

Compare MacOSAdapter which wraps everything in a `try/finally` after `self._page = page` (line 439/610) so all exit paths go through the same cleanup — including "Leave call" click, browser close, queue drain, and `_browser_closed.set()`.

**Impact:** If an exception occurs during one of these early-return cleanup blocks, the browser may not close. Also, the pattern is fragile — any new early return would need to remember to add the cleanup calls.

**Fix:** Restructure to use try/finally like MacOSAdapter. This is a larger refactor but important for reliability.

---

### FINDING 5: `CaptionProcessor` shared state accessed without lock

**File:** `pipeline/captions.py:116-168` (is_speaking block)
**Severity:** Low
**Category:** Threading / shared state

In `on_caption_update()`, when `is_speaking` is True, the method reads/writes `self._abort_speaker`, `self._abort_text`, and `self.abort_event` without holding `self._lock`. These fields are also read from the main thread in `runner.py:743-746`:

```python
with self.captions._lock:
    updated_text = self.captions._current_text.strip()
    abort_speaker = self.captions._abort_speaker
```

The runner does hold the lock to read them. But `on_caption_update` writes `_abort_speaker` and `_abort_text` (lines 147-148) without the lock. This is a TOCTOU window: the runner could read a stale `_abort_text` that doesn't match the `_abort_speaker` just written.

**Impact:** Very unlikely to cause a user-visible bug — the abort path already handles mismatches gracefully (checks for continuation). But it's technically a data race.

**Fix:** Wrap the `_abort_speaker`/`_abort_text`/`abort_event.set()` block in `with self._lock:`.

---

### FINDING 6: `LLMClient._history` not thread-safe

**File:** `pipeline/llm.py:25, 90-92, 160-167, 169-171`
**Severity:** Low (chat mode) / Very Low (voice mode)
**Category:** Threading / shared state

`LLMClient._history` is a plain list with no lock. In chat mode, `ChatRunner._loop()` runs on the main thread and calls `_llm.ask()` and `_llm.send_tool_result()` — these are the only callers that modify history, and they're sequential within the single polling loop. No race.

In voice mode, `AgentRunner` calls `llm.ask()`, `llm.ask_stream()`, and `llm.record_exchange()` — all from the main thread. The `llm.warmup()` call runs in a background thread but doesn't touch `_history`. No race.

`add_context()` is called from ChatRunner's main loop (same thread as `ask()`). No race.

**Impact:** None under current usage. Only becomes an issue if `ask()` were ever called from multiple threads simultaneously.

**Fix:** No fix needed. Document as single-threaded by design.

---

### FINDING 7: `MacOSAdapter.sys.stderr` replacement never restored on normal exit

**File:** `connectors/macos_adapter.py:661-662`
**Severity:** Low
**Category:** Resource cleanup

```python
self._orig_stderr = sys.stderr
sys.stderr = io.StringIO()
```

This suppresses Playwright teardown noise by replacing stderr with a StringIO. But the outer `except/finally` block (line 664-676) never restores it. Compare CaptionsAdapter which does restore it:

```python
finally:
    if hasattr(self, "_orig_stderr"):
        sys.stderr = self._orig_stderr
```

**Impact:** After MacOSAdapter's browser session ends, stderr is silenced for the rest of the process lifetime. Any subsequent warnings or errors written to stderr are swallowed. In practice the process usually exits shortly after, but in polling mode (calendar join) the process continues.

**Fix:** Add the same `sys.stderr = self._orig_stderr` restoration in MacOSAdapter's outer finally block.

---

### FINDING 8: `JoinStatus` fields written without synchronization

**File:** `connectors/session.py:17-35`
**Severity:** Very Low
**Category:** Threading / shared state

`JoinStatus.signal_success()` and `signal_failure()` set `self.success`, `self.failure_reason`, and `self.session_recovered` as plain attribute writes, then call `self.ready.set()`. The reader (`ChatRunner.run()`, `AgentRunner.run()`) calls `self.ready.wait()` then reads these attributes.

The `threading.Event.set()` call provides a happens-before relationship in CPython (it acquires the internal lock), so in practice the reader will see the written values. This is safe under CPython's GIL and the Event's internal lock, but it's not formally guaranteed by the threading module's documentation.

**Impact:** None in practice. CPython's GIL + Event internal lock makes this safe.

**Fix:** No fix needed. Adding a lock would be over-engineering.

---

### FINDING 9: `_shutdown()` in `__main__.py` Linux path missing `_shutdown_called` guard

**File:** `__main__.py:336-342`
**Severity:** Low
**Category:** Shutdown path

The macOS path has a `_shutdown_called` flag to prevent double-shutdown:

```python
_shutdown_called = False
def _shutdown(signum=None, frame=None):
    nonlocal _shutdown_called
    if _shutdown_called:
        return
    _shutdown_called = True
```

The Linux path (line 336-342) does NOT have this guard:

```python
def _shutdown(signum=None, frame=None):
    if signum:
        log.info(f"Received signal {signum} — shutting down")
    runner.stop()
    if mcp:
        mcp.shutdown()
    connector.leave()
```

Since `_shutdown()` is registered as a signal handler AND called in the `finally` block (line 352), it will run twice on Ctrl+C: once from SIGINT, once from `finally`. `runner.stop()` and `connector.leave()` are individually idempotent-ish, but `mcp.shutdown()` clears `_loop` and `_loop_thread` — calling it twice is technically a race if the first call's `loop_thread.join()` hasn't finished.

**Impact:** Usually harmless but can produce noisy log warnings during shutdown.

**Fix:** Add the same `_shutdown_called` guard.

---

## Summary by Severity

| Severity | Count | Findings |
|----------|-------|----------|
| Medium   | 4     | #1 (sleep vs wait_for_timeout), #2 (queue drain), #3 (browser_closed event), #4 (try/finally) |
| Low      | 4     | #5 (lock), #7 (stderr), #9 (shutdown guard), #6 (history docs) |
| Very Low | 1     | #8 (JoinStatus) |

## Recommended Fix Order

All four Medium findings are in **LinuxAdapter** — they're parity gaps where battle-tested patterns from MacOSAdapter/CaptionsAdapter were never carried over. I'd fix them in one pass:

1. **Finding 1** — `time.sleep` → `page.wait_for_timeout` (one-liner)
2. **Finding 2** — Add queue drain to LinuxAdapter finally block
3. **Finding 3** — Add `_browser_closed` event + idempotent `leave()`
4. **Finding 4** — Restructure LinuxAdapter `_browser_session` with try/finally
5. **Finding 9** — Add `_shutdown_called` guard to Linux path
6. **Finding 7** — Restore stderr in MacOSAdapter
7. **Finding 5** — Add lock around abort state writes

Findings 6 and 8 need no code changes.
