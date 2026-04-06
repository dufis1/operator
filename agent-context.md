# Operator — Agent Context

*Token-optimized for coding agents. Human overview: `next-steps.md`. Human checklist: `refactor-plan.md`.*
*Living document — check off steps as completed. Pick up from the first unchecked item.*

---

## Working Style (follow for every step)

- One step at a time. Test and commit before moving on. Never batch steps.
- Before making any change: explain what you're about to do and why, in plain language.
- Smallest possible change. If a step can be broken down, do so.
- Don't touch anything out of scope. Note it, don't change it.
- Audience: technical product manager, not a senior engineer. Define terms when used.
- Each step has a commit message — use it exactly.

---

## Current Status

**Phase:** Streaming classifier — live testing, three rounds complete.
**Next action:** The premature finalization at 0.7s silence threshold is the top user-facing issue. It causes mid-sentence cutoffs ("How about?" → PASS before "Sri Lanka" arrives), forcing users to re-invoke the wake phrase. Consider tuning `captions.silence_seconds` upward or implementing a smarter finalization strategy that accounts for incomplete thoughts. The `_abort_text` fix (session 40) needs live validation — it wasn't exercised because Google Meet happened to attribute all echo captions to "You" in the test session.

**What was built this session (April 6, 2026, session 43):**
- **Latency deep-dive + docs.** Traced the full pipeline from wake phrase start to first audio byte using live log data from the `11:56:47` cycle (`"Operator." → "Yes, I'm here..."`). Created `docs/latency.md` with a step-by-step table (timestamps, per-step durations, cumulative from first caption). Key numbers: ~330ms caption ASR lag → 720ms silence wait → 431ms LLM first token → 864ms TTS synthesis → 172ms mpv overhead = **~2,700ms from speech end to first audio byte**.
- **Latency reduction ideas documented.** Added five exploration ideas to `docs/latency.md`: (1) Whisper STT revisit — structurally faster to finalization at silence=0 than captions at 0.7s, contingent on speculative reliability and long-utterance benchmarks; (2) Single-fire speculative — cap speculative to one call per utterance rather than re-running on each new caption; (3) LLM first-token investigation — 431ms for one token vs 181ms for the rest suggests prompt overhead, explore split classify/generate and local classifier; (4) Per-speaker silence calibration from speech rhythm analysis; (5) Dedicated audio-based wake detection (Porcupine / OpenWakeWord) to bypass the 330ms structural caption lag.

**What was built this session (April 6, 2026, session 42):**
- **Filler phrase revision + regeneration.** Revised phrase lists across all four buckets (neutral, cerebral, empathetic, interruption) in `scripts/gen_fillers.py` — removed weak/awkward phrases, added cleaner alternatives. User listened and approved. All MP3s regenerated via Kokoro af_heart.
- **ASR lag measurement.** Built `scripts/test_asr_lag.py` — plays a known clip through BlackHole, auto-reads `/tmp/operator.log` for `caption_wake_detected`, computes lag, detects contaminated runs. Key finding: Google Meet ASR-to-DOM lag is **~407ms** from mpv process exit (5-run avg, σ≈15ms). Two-stage pattern: partial result ("Oper.") at +73ms, confirmed result ("Operator.") at +407ms — the ~334ms gap between stages is a fixed ASR flush interval on Google's end. The 0.7s silence threshold fires ~313ms after the confirmed caption, making the total user-stops-talking → filler-plays gap approximately 1.5s (silence threshold + ASR lag + LLM first token).

**What was built this session (April 5, 2026, session 41):**
- **LLM cold-start warmup.** Added `LLMClient.warmup()` — fires a 1-token dummy request at startup in a background thread (same pattern as `tts-init`). Establishes the httpx TCP/TLS connection pool before the first real question. Validated in back-to-back sessions: `llm_first_token` on first question dropped from 0.748s → 0.440s (41% reduction). Warmup completes ~1.3s after startup, well within the browser launch window. Non-fatal: exceptions are swallowed with a warning.

**What was built this session (April 5, 2026, session 40):**
- **Playback interrupt classifier text fix.** The classifier was reading `_current_text` (stale prompt from before playback) instead of the caption that actually triggered `abort_event`. Added `_abort_text` field to `CaptionProcessor` — set at abort time, updated when subsequent captions pass continuity check, read by classifier thread instead of `_current_text`. Also fixed write-before-signal ordering: `_abort_speaker` and `_abort_text` are now assigned before `abort_event.set()` to prevent a theoretical race where the classifier thread wakes and reads before the values are written.
- **Live test results (2 sessions).** Session 39 fixes validated: (1) playback interrupt classification correctly dismissed echo misattribution (PASS on "Cool." attributed to human speaker), (2) full caption text as prompt working (pre-wake context preserved), (3) no zombie classifier threads. New scenarios confirmed: EXIT wrap-up ("Thanks Operator" → "You're welcome!" → conversation exit), processing-phase interruption ("John, can you look into that for me?" correctly classified as PASS during processing). Premature finalization at 0.7s remains the primary issue — occurred twice across both sessions.

**What was built last session (April 5, 2026, session 39):**
- **Playback interrupt classification.** Previously, any non-"You" caption during response playback killed mpv immediately. Now gated through `_stream_classify_playback_interrupt()` — a classifier thread watches `abort_event`, grabs the caption text, stream-classifies it (first token: INTERRUPT or PASS). Only sets `confirmed_interrupt` event (which `play_audio` watches) if the classifier confirms a real interruption. Prompt explicitly calls out hallucinations, background noise, and ambient speech as PASS cases. Includes the bot's current reply text for context.
- **Removed `_wake_position` slicing.** `_extract_prompt()` now returns the full `_current_text` instead of slicing at a stored character position. This fixes two bugs: (1) ASR corrections shifting characters invalidated the stored position, producing garbled prompts like "r, can you hear me". (2) Pre-wake-phrase context was discarded — user saying "How about Germany? Hey Operator" lost "How about Germany?". The LLM now gets the full caption node text. Conversation-mode wake reset also sends full text instead of slicing.
- **Fixed zombie classifier threads.** Each `_finalize_prompt` call spawned a daemon thread waiting on `abort_event.wait()`. When playback completed normally, the thread sat forever. Next time `abort_event` fired (even in a different prompt cycle), all accumulated threads woke up simultaneously — causing triple classifier calls. Fix: added `playback_done` event; thread polls both events and exits cleanly when playback finishes.

**What was built last session (April 5, 2026, session 38):**
- **Streaming LLM support.** Added `ask_stream()` to `LLMClient` — yields tokens as they arrive from the OpenAI API. Does not auto-record to history; caller uses `record_exchange()`.
- **Streaming first-token classification.** `_finalize_prompt()` restructured: when `stream_classify=True`, starts streaming LLM call, checks first token. PASS → suppress (no filler, no response). EXIT → strip prefix, respond with sign-off, signal conversation exit. Anything else → it IS the response (play filler, collect rest, synthesize, speak). Applied to both wake mode and conversation mode.
- **Conversation mode simplified.** Removed: `_classify_followup()`, `_reclassify_full_text()`, `_strip_mid_punctuation()`, two-strike PASS system, INCOMPLETE re-fire loop, soft PASS logic (~75 lines → ~15 lines). PASS exits immediately. EXIT responds then exits.
- **Wake phrase in conversation mode.** Full "hey operator" regex (punctuation-tolerant, case-insensitive) resets to wake mode. Bare "operator" does not trigger reset.
- **Playback-only interruption.** Removed pre-playback abort check (0.4s wait + text-grew + recursive re-process). Added `interrupt_event` parameter to `TTSClient.play_audio()` — polls mpv process and terminates on event. User talking over response stops playback immediately.
- **Processing-phase interruption.** After synthesis, checks if abort_event fired during processing. Same speaker + text extends original → stream-classify updated text. PASS → play original response. Not PASS → play interruption filler + re-process.
- **Interruption filler clips.** New "interruption" bucket with 6 clips: "Heard you", "One sec", "Hang on", "Got it", "Okay okay", "Hold on". Generated via Kokoro.
- **Backstop timeout.** Caption-mode conversation timeout bumped from 20s to 60s (safety net only — PASS/EXIT handle normal exits).

**What was built last session (April 5, 2026, session 37):**
- **Code review + 3 bug fixes.** (1) Abort re-fire duplication: `prompt + " " + new_text` duplicated prompt because `_current_text` is full text not delta; fixed to use `new_text` directly. (2) Speaker bleed: any non-"You" speaker could overwrite `_current_text` during `is_speaking`; added `_abort_speaker` tracking. (3) Echo misattribution: Google sometimes captions bot TTS as human speaker; added continuity guard (reject discontinuous text) + echo fingerprinting (`_tts_text` comparison).
- **Echo diagnostics.** Every caption during `is_speaking` logs `DIAG echo_caption` with speaker, you flag, `[ECHO-MATCH]` tag, caption text, and TTS text.
- **Live testing.** Two runs in Google Meet. Run 1: echo misattribution reproduced and fixed. Run 2: wake/follow-up/conversation all passed. "Capital of France" failed due to INCOMPLETE race condition (text grew during 1.3s classifier call).
- **Architectural redesign agreed.** Streaming first-token classification replaces blocking classifier. INCOMPLETE removed. Abort moved to playback-only. Interruption handling via same-speaker continuity + stream-classify. Classification-based conversation exit replaces timeout. See `handoff.md` for full spec.

**What was built last session (April 5, 2026, session 36):**
- **Single-threshold consolidation — all 11 steps implemented.** Merged speculative (0.5s) + finalization (0.7s) into single `silence_seconds: 0.7` threshold. Removed: `_SpeculativeResult` class, `_run_caption_speculative()`, `_make_caption_speculative_callback()`, `_run_speculative()`, `_make_speculative_callback()`, `_speculative_fired` flag, all speculative hit/miss branching in `_finalize_prompt()`, `on_first_silence` from audio.py, `on_speculative` from captions.py. Net ~230 lines removed.
- **New `_classify_followup()` method.** Conversation-mode classifier now runs inline after finalization instead of speculatively before it. Same prompt, same PASS/INCOMPLETE/RESPOND logic, just sequential instead of threaded. Returns `(for_assistant, is_incomplete, llm_reply)` tuple.
- **`_finalize_prompt()` simplified.** Removed `speculative` parameter. Added `precomputed_reply` parameter so classifier responses (which are valid LLM replies) skip redundant LLM calls. Filler always plays (except abort retries). TTS always synthesizes fresh.
- **Config consolidated.** `speculative_seconds` + `finalization_seconds` → single `silence_seconds: 0.7` in config.yaml/config.py.

**What was built last session (April 5, 2026, session 35):**
- **DOM timing instrumentation.** Added unbatched `dom_raw` mutation timing logs to the caption observer JS — logs raw `performance.now()` timestamps for `addedNodes`, `characterData`, and text node mutations before `setTimeout` batching. Added `batch_delay` measurement to every caption log line. Confirmed: `batch_delay` is 1-3ms (our batching adds nothing), and Meet's ASR renders at a consistent ~333ms cadence. The 0.5s speculative threshold fires after just 1.5 render cycles, which frequently catches incomplete text.
- **Latency probe moved to DEBUG.** `perceived_speech_start` and `perceived_acoustic_silence_end` are purely diagnostic — moved from `log.info` to `log.debug` to declutter INFO logs.
- **Single-threshold consolidation plan.** Drafted 11-step plan to merge speculative (0.5s) + finalization (0.7s) into one `silence_seconds: 0.7` threshold.

**What was built last session (April 5, 2026, session 34):**
- **Live verification of all 9 finalization refactor steps.** Tested in Google Meet with stress cases: rapid Q&A, mid-sentence pauses, drawn-out "How about... Belgium" follow-ups. Results: Steps 1,2,4,5,6,7,8,9 confirmed PASS. Step 3 (abort window widening) not exercised (bridge lag was clean). Step 5 (INCOMPLETE) was the star — 6 correct classifications. Step 7 (re-fire cap) hit 3/3 cleanly on Belgium sequence.
- **Abort path fix: stale text.** Root cause: `_current_text` was never updated during `is_speaking` (early return at captions.py line 114). Fix: update `_current_text` and `_current_speaker` for non-"You" speakers during `is_speaking`. Added 0.5s settle delay in abort path so partial captions ("German.") settle to full words ("Germany.") before reading.
- **Abort path fix: lost prompt context.** Root cause: abort read `_current_text` which was just the new caption block ("Germany."), losing the original prompt ("What's the capital of"). Fix: concatenate `prompt + " " + new_text` in abort path. Always concatenate regardless of speaker — LLM needs full context either way.
- **INCOMPLETE classifier fix: false positives from caption punctuation.** Root cause: Google Meet inserts sentence-ending punctuation at speech pauses, turning "How about Belgium" into "How about? Belgium." which matched INCOMPLETE examples. Fix: `_strip_mid_punctuation()` removes `.?!` followed by whitespace before sending to classifier. Applied only to classifier path, not wake or direct response paths.
- **Known issue: false aborts from ASR rewrites.** Google Meet cosmetically rewrites captions after finalization (e.g. "two plus two" → "2 + 2"). Abort mechanism can't distinguish this from new speech. Causes redundant LLM call + ~1s latency. Fix planned: semantic diff at abort trigger point using `_normalize_for_match`.

**What was built last session (April 5, 2026, session 33):**
- **Finalization refactor steps 4-9.** Step 4: second PASS text-growth check (re-classifies if caption text grew since speculative snapshot). Step 5: INCOMPLETE classification — LLM can now return INCOMPLETE for unfinished sentences ("What about?", "How about the"), keeping the bot listening instead of responding or PASSing. Step 6: removed punctuation gate from caption finalization — silence gap is now the only trigger, with LLM INCOMPLETE judgment replacing the `.?!` heuristic. Step 7: INCOMPLETE re-fire loop with cap of 3 — `_speculative_fired` resets after INCOMPLETE so speculative re-fires on next silence gap with longer text; counter prevents infinite loops. Step 8: echo guard reduced from 1.0s to 0.6s in config.yaml. Step 9: cleanup verified — no dead code from filler grace removal or punctuation gate removal, speculative TTS correctly gates on `for_assistant=True` (skips INCOMPLETE and PASS).

**What was built last session (April 4-5, 2026, session 32):**
- **Finalization refactor steps 1-3.** Step 1: removed filler grace period (`_filler_done_at` and 1.0s grace window). Step 2: fixed double "Echo prevention: resumed" log by adding `aborted` flag to skip `finally` cleanup on recursive `_finalize_prompt` calls. Step 3: widened abort window from 0.15s to 0.4s to accommodate caption bridge lag.

**What was built last session (April 4, 2026, session 31):**
- **Issue #1 fix (LLM log truncation).** Root cause: `utterance[:80]` in `llm.py:38` truncated the log display at INFO level, making it look like the LLM received truncated prompts. Actual API calls were unaffected. Fix: replaced with `prompt_chars=N` at INFO, full utterance at DEBUG.
- **Full pipeline audit.** Inventoried 34 mechanisms across 7 groups (silence detection, speculative processing, conversation mode, echo prevention, abort, fillers, context management). Identified 4 conflicts: (A) filler grace blocks abort signals, (B) speculative classify locks in decisions on incomplete text, (C) ASR punctuation doesn't correlate with sentence completion, (D) echo guard adds dead time in rapid Q&A. Drafted 9-step refactor plan.

**What was built last session (April 4, 2026, session 30):**
- **Duplicate caption event fix.** Root cause: MutationObserver configured with `subtree: true` fires `childList` records for both parent div and child span when Meet inserts a caption subtree. Both are different DOM nodes, so the per-node `nodeState` WeakMap dedup didn't catch them. `getText()` on either returns the same text → two identical callbacks to Python. Fix: added global `(speaker + text)` dedup with 50ms window in the JS `send()` function, before the `__onCaption` bridge call. Catches duplicates regardless of mutation source. Verified zero duplicates in live meeting.

**What was built last session (April 4, 2026, session 29):**
- **Conversation-mode runner pickup delay fix.** Root cause: `spec.ready.wait(timeout=3.0)` in the conversation-mode loop blocked until speculative LLM+TTS both finished before the runner could proceed. In wake mode, `_finalize_prompt` was called immediately. Fix: added `llm_done` event to `_SpeculativeResult`, set after LLM returns (before TTS). Conversation loop waits on `llm_done` instead of `ready`. `_finalize_prompt` Step 1 (LLM resolution) checks `llm_done` instead of `ready`. Step 2 (TTS) waits for in-flight speculative TTS when LLM matched, instead of starting redundant fresh synthesis. Filler skip logic recognizes "LLM done, TTS in-flight" as a skip condition. Result: pickup delay reduced from 0.8-1.3s to 0.2-0.3s. All speculative TTS hits preserved. Verified across 3 live test runs.

**What was built last session (April 4, 2026, session 28):**
- **Audio buffer race condition fix.** Moved debug WAV write inside `_audio_lock` in `feed_audio()` — was writing outside the lock, creating a race with the connector thread.
- **Stale buffer drain on conversation re-entry.** Added `drain_audio_buffer()` at top of `capture_next_utterance()` when `no_speech_timeout` is set. Conversation follow-ups that time out left stale PCM in the buffer; next capture cycle would Whisper old data. Guard scoped to only fire on conversation follow-ups (line 437 in runner.py), not ambient wake (line 401) or initial prompt (line 421).
- **Log audit of live session.** Analyzed 6-question caption-mode session. Found 5 issues: (1) duplicate caption events from MutationObserver, (2) LLM history truncated mid-word, (3) premature speculative finalization on mid-sentence pauses, (4) double echo-resume log in abort path, (5) ~1s runner pickup delay in conversation mode that accidentally masks filler need.

**What was built last session (April 4, 2026, session 27):**
- **Filler echo loop fix.** Google Meet sometimes misattributes filler audio (played through BlackHole) to the previous human speaker instead of "You". This caused the abort mechanism to trigger in an infinite loop — each retry played a new filler, which got misattributed again. Three-layer fix: (1) Dynamic grace period on `_filler_done_at` — ignores non-"You" captions until 1s after filler playback finishes, adapting to any filler clip length. (2) `allow_abort=False` on recursive `_finalize_prompt` calls — hard cap at one retry. (3) No filler on abort retries — prevents double-filler awkwardness.
- **Filler skip when speculative ready.** In conversation-mode follow-ups, `spec.ready.wait()` in the loop often completes before `_finalize_prompt` runs, meaning LLM reply and TTS audio are both already cached. Previously, `_finalize_prompt` played a filler anyway (~0.8s delay), then discovered the speculative hit and skipped synthesis. Now checks `speculative.ready.is_set() and speculative.synth_bytes` before starting filler — goes straight to playback when everything is ready.

**What was built last session (April 4, 2026, session 26):**
- **Aggressive finalization tuning.** Reduced `finalization_seconds` 1.5s→0.7s, `speculative_seconds` 1.0s→0.5s. Tried 0.3s/0.5s first — too aggressive, caused speculative misses (fired before captions settled) and premature finalization cutting off mid-sentence (Ecuador→France bug). Settled on 0.5s/0.7s as the sweet spot.
- **Abort mechanism.** Two-signal system prevents wrong answers from premature finalization: (1) `abort_event` on `CaptionProcessor` — set when a non-"You" caption arrives during `is_speaking`, indicating user is still talking. (2) Text-grew check — compares live `_current_text` against finalized prompt using `endswith` after normalization, catching captions that arrived in the ~1s gap between finalization and echo prevention being set. On abort, `_finalize_prompt` re-processes immediately with the updated text (no round-trip through capture loop, avoiding the `capture_start` timing crack). Filler echo protection: `finally` block calls `filler_done.wait()` before resuming captions. Echo guard sleep skipped on abort (no response was played). `_finalize_prompt` returns bool so callers know whether to enter conversation mode.
- **Speculative match normalization.** `_normalize_for_match()` handles Google ASR rewrites: lowercases, expands symbols (`+`→`plus`, `=`→`equals`, `-`→`minus`), converts number words to digits (`two`→`2`, etc.), strips punctuation, collapses whitespace. Fixed "What's two plus two" vs "what's 2 + 2" mismatch that caused speculative misses on first question every time.
- **Soft PASS text-grew check.** After a soft PASS, peeks at live `_current_text` to detect late-arriving words (e.g. "How about?" finalized, "How about France?" arrived 98ms later). If text grew, re-classifies with full text before committing to PASS.
- **`spec.ready.wait()` timeout.** Added 3s timeout to prevent infinite hang when utterance finalizes via speaker-change before speculative threshold. Root cause: filler echo after abort created "You" captions that triggered speaker-change finalization at 0.34s (below 0.5s speculative threshold), so no speculative thread launched and `spec.ready` was never set.
- **Millisecond TIMING logs.** Console timestamps now `HH:MM:SS.mmm`. All TIMING lines in the response path use `.3f`. New markers: `llm_resolved`, `tts_resolved`, `filler_wait_done`, `response_play_start gap_since_filler_done`, `mpv_spawned`, `mpv_audio_piped`, `abort_caption_detected`, `abort_text_grew`, `abort_triggered`.
- **Investigated pre-spawned mpv** — not viable (stdin is single-use, process exits on EOF). Only 28ms of the ~160ms overhead is eliminable. Not worth the complexity.

**What was built last session (April 4, 2026, session 25):**
- **Interaction latency audit and speculative TTS optimization.** Identified TTS synthesis (0.70s) as the largest shaveable bottleneck. `_run_caption_speculative` now synthesizes Kokoro audio immediately after LLM returns, caching WAV bytes in `synth_bytes`. `_finalize_prompt` skips synthesis on hit. Live-tested: `total_from_finalized` dropped from 4.27s→3.72s.
- **Improved end_to_end TIMING log.** Added `filler_wait` as a fourth column. Fixed timing variables so columns add up.

**What was built session 24 (April 4, 2026):**
- **Live-tested and debugged startup optimizations from session 23.** Startup confirmed at **4s** (down from 30s baseline — 87% reduction).
- **Granular TIMING logs** across entire startup pipeline: `browser_launch`, `navigation`, `pre_join_ready`, `detect_page_state`, `camera_toggle`, `join_click`, `in_meeting_wait`, `mic_check`, `captions_escape_overlays`, `captions_enable`, `caption_observer_inject`, `tts_kokoro_import`, `tts_kokoro_pipeline`. All grep-able via `grep TIMING /tmp/operator.log`.
- **Join button race** — replaced sequential 5s-timeout-per-button loop with `.or_()` race across Join now / Ask to join / Switch here. Eliminated 5s waste on guest joins.
- **Caption toggle fix** — added `_captions_are_on()` state check before pressing Shift+C to prevent toggling captions off when already on. Raced region + turn-off button detection.
- **HF Hub warning suppression** — traced via stack to `huggingface_hub.utils._http` child logger; suppressed both parent and child loggers during Kokoro init.
- **Shutdown hardening** — (1) `_shutdown_called` guard prevents double-call on Ctrl+C. (2) `_browser_closed` event lets `leave()` return immediately after `browser.close()` without waiting for Playwright teardown. (3) Inner `try/finally` ensures navigate-away + browser close runs on all exit paths including early failures. (4) stderr redirect during Playwright teardown silences greenlet/asyncio noise.
- **Removed defensive waits** — "Not now" popup dismissal (3s timeout) and Escape overlay loop (0.5s) removed after live testing confirmed they never trigger and don't block subsequent steps.
- **Reactive overlay diagnosis** — `_diagnose_overlay()` fires only on failure, scanning for visible dialogs and high z-index overlays, saving screenshot + HTML to `debug/blocked_<context>.png`.

**Previous session (session 23):** Event-driven startup replacing fixed sleeps + parallel TTS init.

**What was built session 22 (April 4, 2026):**
- `pipeline/runner.py` — Implemented two-strike PASS system for conversation-mode exit detection. First speculative PASS is now "soft" — stays in conversation mode instead of immediately exiting. If finalized text grew beyond speculative snapshot (word count delta > 2), re-classifies on full text via new `_reclassify_full_text()` method. Second consecutive PASS exits for real. Second-strike classifier prompt includes "[Context] You previously concluded the conversation was over" framing via `was_soft_pass` field on `_SpeculativeResult`. `soft_pass_active` boolean resets on any successful RESPOND, allowing future soft PASSes within the same conversation. All five scenarios verified in live meeting: happy path (no regression), soft PASS → timeout, soft PASS → recovery, soft PASS → second strike, short-pause follow-up.

**What was built session 21 (April 4, 2026):**
- `__main__.py` + `app.py` — Added `HH:MM:SS` timestamp formatting to the stderr/stdout StreamHandler so terminal logs show timing.
- `pipeline/runner.py` — Fixed conversation timeout bug: caption-mode follow-up loop was calling `capture_next_wake_utterance` without `no_speech_timeout`, so it defaulted to `None` (wait forever). Now passes `CONVERSATION_TIMEOUT` (20s). Improved classifier prompt: includes last exchange context (`_last_utterance` + `_last_reply` tracked on the runner, not pulled from LLM history which contained the full formatted prompt). Classifier instruction rewritten to be meeting-aware ("You are in a live meeting with multiple participants. You just answered a question. Decide: is this a follow-up directed at you, or has the speaker moved on...").

**What was built session 20 (April 4, 2026):**
- `__main__.py` — Terminal mode (`python __main__.py`) no longer uses rumps. Runs `run_polling()` directly on the main thread so SIGINT works as normal `KeyboardInterrupt`. Merged the old `_run_macos_headless` into a single `_run_macos_terminal()` that handles both direct URL and calendar polling modes. Added `start_new_session=True` monkey-patch on `subprocess.Popen.__init__` so child processes (Playwright driver, Chrome) don't receive SIGINT from the terminal. `_run_macos()` (rumps) retained only for `Operator.app` bundle.
- `connectors/captions_adapter.py` — Moved navigate-away (`page.goto("about:blank")`) and `browser.close()` inside the `with sync_playwright()` block. Previously they were in a `finally` outside it, so the Playwright driver was already dead when they ran. Removed broken `_force_kill_chrome()` method (SingletonLock doesn't exist in `--headless=new` mode).

**What was built session 19 (April 3, 2026):**
- `connectors/captions_adapter.py` — Fixed Ctrl+C shutdown delay (Chrome orphaned for 30-60s). Root cause: browser thread was daemon, `leave()` only set an event flag, main process exited before `browser.close()` ran. Fix: `leave()` now joins the browser thread (10s timeout). Also reduced hold-loop poll from 5s to 1s.
- `calendar_poller.py` — Fixed session auth failure. Two bugs: (1) was using Playwright's `headless=True` (old headless mode, can't decrypt Chrome cookies) instead of `headless=False` + `--headless=new`; (2) was using Playwright's bundled Chromium instead of real Chrome (`executable_path` was missing). Now matches `CaptionsAdapter`'s launch pattern.
- `scripts/auth_export.py` — Now visits `calendar.google.com` after login to establish Calendar's service-specific session cookies. Meet and Calendar use different Google service scopes; visiting only `accounts.google.com` authenticated Meet but not Calendar.

**What was built in session 18 (April 3, 2026):**
- `config.yaml` + `config.py` — Renamed `admission_timeout_seconds` → `idle_timeout_seconds` (default 600s). Single config value now controls both lobby patience and in-meeting inactivity timeout.
- `connectors/captions_adapter.py` — Added `_last_caption_time` tracking. Hold loop replaced: 4-hour hard deadline removed, replaced with inactivity check that arms on first caption. If `now - _last_caption_time >= idle_timeout`, triggers `leave_event.set()`. Bot waits indefinitely in a silent meeting before anyone speaks (timer only starts after first caption).
- `pipeline/runner.py` — Updated `ADMISSION_TIMEOUT_SECONDS` → `IDLE_TIMEOUT_SECONDS` reference.
- **Google Calendar API migration explored and shelved.** Built full replacement using `google.auth.default()` + `googleapiclient` with `gcloud auth application-default login`. Discovered: (1) gcloud requires 500MB SDK install, (2) Google is deprecating calendar scopes on gcloud's default client ID (`WARNING: The following scopes will be blocked soon`), (3) Playwright browser cookies are actually more durable than CLI-scoped OAuth tokens. Reverted — calendar poller stays browser-based.
- **Live test result (April 3, 2026):** Inactivity timer confirmed working. Set to 60s for testing. Bot joined meeting, answered "what's 2+2" (4.54s e2e), then left cleanly after 60s of no captions when user departed. Log: `CaptionsAdapter: no captions for 60s — leaving meeting`. Returned to polling state correctly.

**What was built last session (April 3, 2026, session 17):**
- `app.py` — Added `logging.StreamHandler()` to root logger so terminal output matches headless mode (previously only wrote to `/tmp/operator.log`).
- `calendar_poller.py` — Added `RunningChromeVersion` to `copytree` ignore patterns (transient Chromium file that vanishes mid-copy). Changed `shutil.Error` handling from fatal return to warning-and-continue (partial copy failures are non-fatal; auth redirect check on line 95 catches real problems).
- **Live test result (April 3, 2026):** Full calendar poller → auto-join → caption pipeline confirmed working. 4.77s end-to-end latency.

**What was built this session (April 3, 2026, session 16):**
- `caldav_poller.py` deleted. Replaced with `calendar_poller.py` — browser-based Google Calendar scraper. Uses a copied `browser_profile` (avoids SingletonLock conflict with the meeting browser). Polls the day view every 30s, extracts events from `[data-eventid]` DOM elements, finds Meet URLs in page source data near event IDs. No CalDAV, no keyring, no app passwords — zero extra auth setup.
- `app.py` — Fixed hardcoded `MacOSAdapter()` → config-driven connector resolution (matches `__main__.py`). Switched from `runner.run()` to `runner.run_polling(meeting_queue)`. CalDAV import replaced with CalendarPoller. Shutdown now always calls `connector.leave()`.
- `pipeline/runner.py` — Added `run_polling(meeting_queue)` method: outer loop that pulls URLs from a `queue.Queue`, calls `run(url)` for each meeting, cleans up and waits for next. Added `_on_disconnect` callback wiring in caption mode so the caption loop exits when the browser session ends.
- `connectors/captions_adapter.py` — Added `_on_disconnect` callback, fired in `_browser_session` finally block. Signals the caption processor to stop when the browser exits for any reason.
- `config.py` / `config.yaml` — Removed `caldav` section and `CALDAV_BOT_GMAIL`.
- `requirements.txt` — Removed `caldav` and `keyring` dependencies.
- `.gitignore` — Added `browser_profile_calendar/`.
- `__main__.py` — Updated help text (CalDAV → Google Calendar).

**Tested:** CalendarPoller extraction verified standalone — correctly finds events by `[data-eventid]`, extracts Meet URL from page source, queues it. Full end-to-end live test not yet run.

**Context for next session:** Meeting-exit detection is done via caption inactivity timer (session 18). System phrase logging preserved for diagnostics but no longer triggers exits. The 4-hour deadline is removed. Calendar poller auth is now reliable — `auth_export.py` establishes both Meet and Calendar sessions, and the poller uses real Chrome with `--headless=new`. Ctrl+C shutdown is clean (browser thread joined before exit). Google Calendar API was explored and ruled out (gcloud scope deprecation).

**What was built last session (April 2, 2026, session 15):**
- `connectors/captions_adapter.py:574` — Added `log.info(f"CaptionsAdapter: system phrase detected — {stripped!r}")` before the early return in `_on_caption_from_js`. This surfaces Meet's system messages in the log for exit detection groundwork.
- No functional behavior changed — system phrases still filtered, just now logged.

**What was built this session (April 2, 2026, session 14):**
- `__main__.py` — Added `--force` CLI flag. Threads `force=True` into `CaptionsAdapter` and `MacOSAdapter` constructors. `_shutdown()` now reads `browser_profile/.operator.kill_reason` on SIGTERM and prints a user-facing reason if present.
- `connectors/session.py` — Added `_write_operator_pid(lock_path)`: writes current Python PID to `browser_profile/.operator.pid` at session start. Added `_chrome_kill_and_clear(lock_path)`: (1) reads `.operator.pid`, writes `.operator.kill_reason`, SIGTERMs the Operator Python process (SIGKILL after 3s if still alive); (2) kills Chrome as a fallback via the SingletonLock PID; (3) removes both lock and PID file.
- `connectors/captions_adapter.py` — `__init__` accepts `force=False`, stored as `self._force`. Lock-check block: if force and lock is live, calls `_chrome_kill_and_clear` instead of signalling `already_running`. Calls `_write_operator_pid` before Chrome launch. Cleans up `.operator.pid` in `finally`. Fixed `_wait_for_admission`: `except Exception` block now checks `page.is_closed()` and returns False immediately if the browser is gone (previously spun for up to 10 min on the ADMISSION_TIMEOUT).
- `connectors/macos_adapter.py` — Same `force` param, PID write, and finally cleanup as `captions_adapter`.
- `pipeline/runner.py` — Updated `already_running` error message: now says `Use --force to stop it and start a new one.`

**What was built this session (April 2, 2026, session 13):**
- `connectors/session.py` — Added `_chrome_lock_is_live(lock_path)`. Chrome's `SingletonLock` is a symlink to `hostname-<pid>`. Reads the symlink, parses the PID, probes with `os.kill(pid, 0)` (no signal, just existence check). Returns `True` if process is alive (live lock), `False` if dead or any error (stale lock).
- `connectors/captions_adapter.py` — Replaced unconditional `SingletonLock` removal with live-check: if lock is live, signals `already_running` and returns immediately (no Chrome launch). If stale, removes and proceeds as before. Uses `os.path.islink()` instead of `os.path.exists()` to catch broken symlinks (stale lock pointing to dead PID — `exists()` would return False for broken symlinks).
- `connectors/macos_adapter.py` — Same fix applied (identical lock-removal pattern).
- `pipeline/runner.py` — Added `already_running` branch in join-failure handling: prints `⚠️  Another Operator session is already running. / Stop that session before starting a new one.` to stdout, alongside existing `session_expired` special-case.
- `docs/model-log.md` — Added "Multiple instances / SingletonLock" block to Section 1 join failures.

**What was built this session (April 2, 2026, session 12):**
- `connectors/captions_adapter.py` — Replaced immediate post-click "joined" assumption with a two-phase waiting room detection. Phase 1: `wait_for_selector(state="visible")` confirms the lobby screen appeared (up to 10s). Phase 2: `wait_for_selector(state="detached")` fires the instant the host clicks "Let in" — event-driven, zero polling lag. Admission signal: `img[alt*="Please wait until a meeting host"]` (confirmed in live DOM captures). If lobby never appears within 10s, proceeds optimistically (handles open meetings or auto-admit flows). Times out with `admission_timeout` after `ADMISSION_TIMEOUT_SECONDS` if never admitted. Heartbeat log every 30s while waiting.
- `pipeline/runner.py` — Extended join timeout from hardcoded 60s to `ADMISSION_TIMEOUT_SECONDS + 60` so the runner doesn't kill the wait prematurely. Fast-fail cases (session_expired, cant_join, no_join_button) still fail immediately — only the waiting room case benefits.
- `config.yaml` + `config.py` — `connector.admission_timeout_seconds: 600` (10 min default). Exposed as `config.ADMISSION_TIMEOUT_SECONDS`.
- `docs/model-log.md` — Added waiting room log lines and `admission_timeout` failure reason to Section 1.

**What was built this session (April 2, 2026, session 11):**
- `connectors/captions_adapter.py` — Added `--mute-audio` to Chrome launch args. Chrome was routing received meeting audio back into BlackHole (its audio output), creating a real AEC feedback loop on every session. WebRTC AEC normally handled it, but occasionally suppressed a word during TTS playback (confirmed via BlackHole recording: meeting audio was present before fix, TTS-only after). `--mute-audio` breaks the loop at the source; bot uses captions not audio so no functionality lost.
- `connectors/captions_adapter.py` + `connectors/macos_adapter.py` — Added `diagnostics.debug_audio` support: when `true`, starts a `sox` recording of BlackHole at session start (saved to `debug/blackhole_HHMMSS.wav`) and saves each TTS synthesis to `debug/tts_HHMMSS.wav`. Used to isolate whether audio dropouts originate in synthesis, routing, or AEC. Flag defaults to `false`.
- `config.yaml` + `config.py` — `diagnostics.debug_audio` toggle (default `false`).

**What was built this session (April 2, 2026, session 10):**
- `pipeline/latency_probe.py` — Hysteresis increased from 300ms to 600ms (`_SILENCE_HOLD_BLOCKS` 3→6): absorbs natural word-boundary pauses, eliminates mid-utterance chattering. Post-gate warmup added: on `set_active(True)`, discards first 5 blocks (500ms) before resuming detection, preventing lingering room echo from triggering probe after gate reopens.
- `pipeline/captions.py` — Removed `"You"` speaker filter that was silently blocking finalization when Google Meet relabeled user speech as `"You"` (observed: Unknown→You transition mid-utterance). Root cause: not audio routing — likely transient Meet attribution behavior. `is_speaking` gate already covers TTS echo. Added `log.debug` when captions dropped by gate, for future diagnosis.
- `scripts/parse_latency.py` — Re-anchored cycles on `caption_wake_confirmed` (not any ambient `perceived_acoustic_silence_end`); filters out multi-participant ambient silences. Added speaker + prompt columns. LEAK flag for gate-leak cycles; excluded from averages. Verified against live log: 1.62s dead-air-to-filler, 3.41s to response.
- `config.yaml` + `config.py` — `diagnostics.latency_probe` toggle (default `true`). Set `false` to skip opening the PortAudio stream, for echo investigation. Concluded the subtle echo heard at meeting start is WebRTC AEC calibration behavior, not probe-related.
- `docs/model-log.md` — Section 5b updated: 600ms hysteresis noted, `LatencyProbe: disabled via config` line added, dropped-caption DEBUG line documented, LEAK flag documented.

**What was built this session (April 2, 2026, session 9):**
- `pipeline/latency_probe.py` — New `LatencyProbe` class. Background daemon thread reads the system default mic via `sounddevice.InputStream` at 8kHz/100ms blocks. Detects acoustic speech→silence transitions; logs `TIMING perceived_speech_start` and `TIMING perceived_acoustic_silence_end speech_duration=N.NNs peak_rms=N.NNNN`. RMS threshold `_SILENCE_RMS=0.03`, silence hold `_SILENCE_HOLD_BLOCKS=3` (300ms hysteresis). `set_active(False/True)` gate prevents false events from filler/response audio bleeding back through the mic.
- `pipeline/runner.py` — Wired `LatencyProbe`: instantiated in `__init__`, started in `run()` before the main loop, stopped in both `run()` finally and `stop()` (with 2s join). Probe gated inactive at `filler_play_start` (not just `response_play_start`) and reactivated after echo guard clears.
- `scripts/parse_latency.py` — New log parser. Groups events into cycles keyed on `perceived_acoustic_silence_end`, prints per-cycle table: ASR delay, dead air to filler, dead air to response; averages at bottom.
- **Tuning notes:** System default input must be MacBook Pro Microphone (not Display Audio). Threshold 0.03 is correct for this setup (speech peaks at ~0.033 RMS). Three SILENCE_HOLD_BLOCKS prevents between-word chattering. Parse script handles mid-utterance false silence_end via `perceived_speech_start` reset.
- **Not yet done:** `parse_latency.py` hasn't been run against a full session log yet — deferred to next session.

**What was built this session (April 2, 2026, session 8):**
- `pipeline/runner.py` — Speculative LLM path fixed: removed `speculative.ready.wait(timeout=0.3)` which expired before the result arrived, causing an unconditional duplicate `llm.ask()` to fire in parallel. Now `speculative.ready.wait()` is unbounded — the speculative call is the only call. Fresh call only fires if speculative failed (llm_reply is None) or transcript mismatched. Filler clip now starts in a daemon thread at finalization entry (not after LLM returns), so it plays concurrently with the LLM wait. New TIMING markers: `filler_play_start/done`, `llm_speculative_hit waited=Ns`, `llm_speculative_miss`, `llm_request_sent/received`, `tts_synthesis_start/done`, `response_play_start/done`, `end_to_end` summary.
- `pipeline/captions.py` — Added `last_caption_time` read-only property (exposes `_last_update_time` for runner latency logging).
- `docs/model-log.md` — Section 4 updated to reflect all new TIMING lines.
- **Live test result (April 2, 2026):** 1.54s silence detection + 0.49s LLM wait (speculative still in-flight) + 0.75s TTS synthesis = 2.79s from last caption to bot audio. Only one LLM call confirmed. Filler played concurrently during LLM wait, covering 493ms of otherwise dead air.

**What was built this session (April 1, 2026, session 7):**
- `pipeline/captions.py` — Wake phrase detection now uses a regex compiled from `config.WAKE_PHRASE` that tolerates punctuation between words (`_WAKE_RE`). Previously the plain `in` check (`"hey operator" in text`) would fail when Google's ASR inserted a comma mid-phrase ("hey, operator"), causing spurious retraction and re-detection failure. Pattern: `"hey operator"` → `hey[,\s]+operator`. Both the initial detection and the retraction check use `_WAKE_RE.search(text_lower)`. Wake position now taken from `m.end()` (the regex match end) rather than `idx + len(wake_phrase)`.
- `pipeline/runner.py` — Filler playback capped at one clip per response. Previously the loop cycled through all clips until synthesis finished, causing two (or more) fillers to play back-to-back when synthesis took longer than one clip. Now exactly one clip is selected and played, then the pipeline waits silently. Removed unused `itertools` import.

**What was built this session (April 1, 2026, session 6):**
- `connectors/captions_adapter.py` — Replaced `time.sleep(5)` with `page.wait_for_timeout(5000)` in the main hold loop. `time.sleep()` blocks the sync_playwright event loop entirely, preventing `expose_function` callbacks from ever being dispatched. The DOM poll (`page.evaluate()` every 5s) was accidentally acting as the only event loop pump — callbacks batched until it fired (2–3s delay). Removing the poll caused zero callbacks; replacing sleep with Playwright's own wait fixed both issues. bridge_lag dropped from 2–3s to 0–2ms. DOM poll log lines removed from `docs/model-log.md` (no longer emitted).

**What was built this session (April 1, 2026, session 5):**
- `pipeline/captions.py` — Filter "You" speaker to block Operator TTS echo. Added punctuation-gated finalization: at 1.5s silence, only finalize if raw caption text ends with `.?!`; hard timeout at 3.75s for unpunctuated fragments. Checks `_current_text.rstrip()[-1]` (not extracted prompt, which has punctuation stripped).
- `pipeline/runner.py` — Both `_run_caption_speculative` and `_finalize_prompt` now use `_transcript_lines[-20:-1]` to exclude the current utterance from LLM context. Prevents pre-wake speech ("I'm a janitor") from leaking into the meeting transcript section.
- `connectors/captions_adapter.py` — JS-side `performance.now()` timestamps now used for gap measurement instead of Python `time.time()`. Calibrates offset on first caption. Logs `bridge_lag` on every caption — key diagnostic for Playwright batching investigation.

**What was built this session (April 1, 2026, session 4):**
- `connectors/captions_adapter.py` — Fixed caption silence bug: `requestAnimationFrame` is throttled/suppressed in `--headless=new` Chrome, so pending mutations never flushed. Replaced with `setTimeout(fn, 0)`. Added diagnostic logging: mutation count heartbeat (every 10 mutations), JS observer-attach sentinel surfaced to Python log, in-meeting DOM snapshot (`debug/in_meeting.html`), 5s DOM poll reading caption region text directly (bypass MutationObserver for verification), text node and attribute mutation handling for defensive coverage. `caption:` log lines promoted from DEBUG → INFO.
- Confirmed in live test: captions reach Python, speaker labels correct, DOM structure as expected.

**What was built this session (April 1, 2026, session 3):**
- `pipeline/captions.py` — `capture_next_wake_utterance()` gains `require_wake=False` for follow-up mode: skips wake detection, uses full caption text as prompt, handles speaker-change finalization and silence detection without `_wake_detected`. New `_require_wake` instance flag gates wake detection in `on_caption_update`. `_do_finalize()` gains `prompt_override` parameter.
- `pipeline/runner.py` — Follow-up loop uses `require_wake=False`. `_make_caption_speculative_callback()` and `_run_caption_speculative()` gain `run_classifier` flag. In follow-up mode, a PASS instruction is appended to `spec.full_prompt`: model returns "PASS" if not addressed, otherwise responds normally. `spec.for_assistant` set from reply. `_SpeculativeResult` gains `for_assistant` field.
- Combined classify+respond: one LLM call handles both jobs. No separate classifier thread, no added latency.

**What was built this session (April 1, 2026, session 2):**
- `connectors/captions_adapter.py` — Playwright browser join + scoped MutationObserver on `[role="region"][aria-label*="Captions"]`. JS→Python bridge via `expose_function`. Filters junk (material icons, system phrases). TTS playback via mpv→BlackHole (same as audio path).
- `pipeline/captions.py` — CaptionProcessor: real-time wake detection on every DOM update (~330ms), silence detection via timing gaps (speculative at 1.0s, finalize at 1.5s), ASR correction handling (wake retraction if "hey operator" disappears), echo guard, transcript callback for all meeting speech.
- `pipeline/runner.py` — Updated to detect caption vs audio mode from connector type. Caption loop uses `capture_next_wake_utterance()` (single call, not two-step). Speculative LLM fires at 1.0s (no Whisper step). All latency tricks preserved: fillers, echo guard, conversation mode, deferred history.
- `config.yaml` — `connector.type: auto` now defaults to `meet-captions` on macOS. Audio fallback via `connector.type: audio`. Wake phrase changed to "hey operator". New `captions:` section with `finalization_seconds` and `speculative_seconds`.
- `config.py` — Added `CAPTION_FINALIZATION_SECONDS` and `CAPTION_SPECULATIVE_SECONDS`.

- **Experiment 1 results (March 31, 2026):** Gaps 1 & 6 both GO. Multi-speaker: Meet creates new DOM node on every speaker change (56 nodes, 23 transitions, 2 speakers). Speaker labels reliable. Overlapping speech: Meet interleaves short fragments per speaker with correct attribution — no text lost or merged. Duplicate "seed" nodes observed (short node immediately superseded by accumulation node). Max text 255 chars (short turns by design). 138 ASR corrections, avg 12 chars back, max 76. Log: `experiments/captions/logs/multi-speaker_20260331_220941.log`.

**Audio architecture rethink (March 30, 2026):**
- Fundamental reassessment: ScreenCaptureKit captures all system audio (privacy violation, captures host's music/notifications, dies if host leaves meeting, causes echo). After evaluating all options (ScreenCaptureKit app filtering, PulseAudio on macOS, WebRTC monkey-patching, Chrome tabCapture extension, Google Meet Media API, DOM caption scraping), decided to replace audio capture entirely with **DOM caption scraping** from Google Meet's built-in live captions.
- This eliminates: ScreenCaptureKit, Whisper/MLX STT, all input audio routing, and the echo problem. TTS output path (BlackHole/mpv) unchanged.
- Built and ran `experiments/captions/test_captions.py` — validates caption scraping via Playwright MutationObserver. Key findings:
  - One DOM node per speaker (not per utterance). Even 20s silence doesn't create new node for same speaker.
  - Update cadence ~330ms (~3/sec while speaking). Silence visible as gaps between updates.
  - Meet does ASR corrections mid-stream (rewrites recent words). Full text not strictly append-only.
  - No phantom captions during silence. Speaker labels reliable (single-speaker tested).
  - Post-meeting UI elements leak through body-wide observer — need to scope to captions region.
- Utterance boundary strategy: detect silence via update-gap timing (no update for ~2.5s = speaker stopped). Wake phrase detection via text matching on accumulated caption text.
- Seven validation gaps identified (3 must-test, 4 nice-to-have). See `experiments/captions/caption-timing-findings.md` lines 88-107.
- Built `experiments/captions/test_captions_v2.py` — three-phase experiment script to close all 7 gaps:
  - `--phase multi-speaker`: Gaps 1 & 6 (multi-speaker nodes, overlapping speech). Host laptop mic ON (Speaker A) + Rober in another room (Speaker B).
  - `--phase endurance`: Gaps 2, 3, 7 (text length cap, ASR correction window, technical terms). Rober runs `say` loop for 10 min.
  - `--phase availability`: Gaps 4 & 5 (free Gmail captions, late enable). Use `--late-enable 15` for late-enable test.
- Files: `experiments/captions/test_captions.py`, `experiments/captions/test_captions_v2.py`, `experiments/captions/caption-timing-findings.md`.
- **Experiment 3 results (March 30, 2026):** Gaps 4 & 5 both GO. Captions work on free Gmail (91 updates over 40s, reliable speaker labels). Late enable loses all speech during off period and triggers ~440 spurious DOM nodes (language menus, settings UI). Decision: enable captions once at join and never toggle. Meet ASR also transcribed "Phase B late enable test" as "Faith be late and able test" — relevant to Gap 7 (technical terms). Full results in `experiments/captions/caption-validation-results.md`.
- **Experiment 2 results (March 31, 2026):** Gaps 2, 3, 7 all GO.
  - Gap 2 (text length cap): No cap. Single node grew to 6018 chars over 9 minutes of continuous speech (1020 updates). No truncation or splitting.
  - Gap 3 (ASR corrections): Corrections rewrite 1–28 chars back in ~330ms steps. Text is stable after a 2–3s silence gap. Safe to treat as finalized.
  - Gap 7 (technical terms): 6/10 terms accurate. PostgreSQL, API endpoint, JSON web token, SSH all correct. kubectl→"Cubicle", OAuth2→"Go off too" — phonetic ASR limitations, same as any STT system.
  - DOM noise: Observer captured 131 nodes but only 3 were captions. Rest were Meet UI chrome (mic_off, keep_outline, aspect_ratio, etc.). Observer must be scoped to caption container before refactor.
  - Log: `experiments/captions/logs/endurance_20260331_210935.log`

**Echo prevention hardening (March 29, 2026):**
- Diagnosed audio feedback loop via `OPERATOR_DUMP_AUDIO=1` debug dump. ScreenCaptureKit captures all system audio (music, notifications, TTS echo from BlackHole → Meet → speakers → recapture). User confirmed hearing: crisp system join sound, echo of join sound, echo of surroundings, and TTS response with echo.
- Added `ECHO_GUARD_SECONDS` (default 1.0s) — post-TTS delay before resuming audio ingestion. Absorbs network-delayed echo that arrives after playback ends. Ack clip guard increased from 0.2s → 0.5s.
- Added `_is_repetition_hallucination()` filter — rejects Whisper output where any unigram or bigram accounts for >50% of words (with >10 words total). Catches "I know I know..." ×112 and "p p p..." ×223 patterns observed in logs.
- Attempted app-specific ScreenCaptureKit filtering (`SCContentFilter(display:including:[chrome])`) to capture only Chrome audio. Result: zero audio captured. Chrome's web audio rendering may not be attributed to `com.google.Chrome` bundle at the ScreenCaptureKit level. Reverted to display-wide capture. Next step: log all apps visible to ScreenCaptureKit during a live session to identify Chrome's actual identity.
- Debug dump mode (`OPERATOR_DUMP_AUDIO=1` env var) writes all captured audio to `/tmp/operator_audio_dump.wav` for diagnosis. Left in place for future debugging.
- Files changed: `pipeline/audio.py` (repetition filter + debug dump), `pipeline/runner.py` (echo guard delay), `config.yaml` + `config.py` (`echo_guard_seconds` setting).

**Error signposting standardization (March 29, 2026):**
- Audited all error/warning messages across the codebase. Identified 11 actionable failure points where users need to take specific action but messages were only in logs or lacked visual prominence.
- Added prominent `print()` signposting (emoji prefix, newline spacing, indented fix commands) to match the auth failure pattern established in `pipeline/runner.py:122-123`.
- Files changed: `pipeline/runner.py` (4: Screen Recording denied, TCC stuck, codesign needed, capture failed), `pipeline/tts.py` (2: no macOS voice, Kokoro fallback), `__main__.py` (4: missing URL, DISPLAY, pactl, PulseAudio sinks), `caldav_poller.py` (1: missing keychain credential).
- Pattern: `❌` for errors requiring action, `⚠️` for warnings with automatic fallback. Existing `log.error()`/`log.warning()` preserved for diagnostics.

**Auth/detection fixes (March 29, 2026):**
- `scripts/auth_export.py` rewritten: now uses `launch_persistent_context(user_data_dir=BROWSER_PROFILE)` so Chrome stores session cookies directly in the profile the bot uses. Previously used a throwaway context that only saved to `auth_state.json`. `auth_state.json` still exported as Linux/Docker backup.
- `connectors/session.py` `detect_page_state()`: when "can't join" text is detected, now checks `page.context.cookies()` for a Google SID cookie. No SID = auth failure → returns `logged_out` (recovery ladder fires). Has SID = genuine host controls → returns `cant_join`. Previously always returned `cant_join`, meaning unauthenticated bots never triggered recovery.
- `connectors/macos_adapter.py`: added `save_debug(page, "initial_load")` after 8s page load (before state detection) and `save_debug(page, "pre_join")` before join button click.
- `pipeline/runner.py`: added prominent `print()` to stdout when auth fails, showing exact command to fix (`python scripts/auth_export.py`).
- `scripts/check_auth.py` (new): diagnostic script that opens browser profile to Gmail, takes screenshot to `debug/auth_check.png`.
- **Demo strategy decided:** Invite-based, not link-paste. Google Meet blocks headless/unauthenticated bots even with open host controls. Users provide bot's Google account email, invite it to their meeting (same model as Otter.ai/Fireflies).

**Session recovery ladder (March 28–29, 2026):**
- Root cause: Google revoked `.google.com` session cookies (SID/HSID/SSID) from the browser profile while Chrome was running in `--headless=new` mode and couldn't complete a re-auth challenge.
- Implemented `connectors/session.py`: `JoinStatus` (threading.Event-based browser→runner signalling), `detect_page_state()` (classifies pre_join/logged_out/cant_join/unknown), `validate_auth_state()` (loads auth_state.json, checks SID cookie exists), `inject_cookies()` (add_cookies on Playwright context), `save_debug()` (screenshot + HTML dump).
- Both adapters: recovery ladder after 8s page load — detect state, attempt cookie injection if logged out, reload, re-detect. JoinStatus wired in join(). 5-minute in-meeting health checks in hold loop.
- `pipeline/runner.py`: `time.sleep(12)` replaced with `join_status.ready.wait(timeout=60)`. On failure, fires `on_state_change("error", reason)`.
- `app.py`: `STATE_ICONS` includes `"error": "⚠️"`. `_on_conv_state_change` fires `rumps.notification()` on error state — user gets macOS notification + persistent menu bar icon.
- `config.yaml`: `auth_state_file` changed from `null` to `"./auth_state.json"`.
- Unit-tested: imports, instantiation, JoinStatus signalling, validate_auth_state edge cases, runner error callback wiring. Full browser→join flow needs live meeting test.

**Session recovery edge case audit (March 29, 2026):**
- Reviewed 4 flagged edge cases from implementation session. Found 1 real bug, 3 non-issues.
- **Bug fixed:** `linux_adapter.py` crashed when `auth_state.json` doesn't exist. `config.yaml` always sets `auth_state_file` to `"./auth_state.json"` (non-empty string), so `if self._auth_state_file:` was always truthy → Playwright's `storage_state=` threw FileNotFoundError → browser thread crashed. Guest code path was unreachable. Fix: replaced truthiness check with `os.path.isfile()` at both the path-selection branch (line 170) and the recovery ladder guard (line 208). Added log line when configured file is missing.
- **No action needed:** (1) macOS `add_cookies()` on persistent context works fine — Playwright supports it on any context type. (2) `validate_auth_state` can't detect server-side revocation — by design, documented at `session.py:84`, ladder re-checks after injection. (3) Linux guest path now reachable after the `isfile()` fix.

**TCC recovery ladder tests (March 28, 2026):**
- `tests/test_recovery_ladder.py`: 10 tests using stub shell scripts — no macOS hardware needed.
- Group 1 (4 tests): Signature verification — correct identity, wrong identity, no signature, missing binary. Patches `subprocess.run` for fake `codesign -d` output and `_BASE` for temp dirs.
- Group 2 (5 tests): Exit code handling — codes 0, 1, 3, 4 (first attempt with tccutil retry), 4 (second attempt with escalation). Uses stub scripts that exit with specific codes.
- Group 3 (1 test): Full recovery ladder — stub uses sentinel file (exit 4 first call, write PCM + exit 0 second call). Verifies tccutil was called, `_start_capture` recursed, and `feed_audio` received data.
- Run: `python tests/test_recovery_ladder.py`

**STT benchmark + mlx-whisper switch (March 28, 2026):**
- Recorded 6 benchmark clips from mic (`benchmark_clips/`) covering wake phrases, names, numbers, technical jargon.
- Benchmarked 4 engines against clips: faster-whisper base (420ms avg), faster-whisper small (1.1s), distil-large-v3 (3.9s), mlx-whisper base (110ms). WER comparable across all except distil on short clips.
- Parakeet (NVIDIA NeMo) ruled out without install: 600M params minimum, full NeMo toolkit required (~2-3 GB), CPU inference estimated 3-10x slower, no streaming API, macOS not first-class.
- Switched to mlx-whisper base as default STT on macOS. Config: `stt.provider: mlx | faster-whisper`. MLX path passes numpy arrays directly (no temp file). faster-whisper codepath preserved for Linux/Docker.
- `config.yaml`: added `stt.provider` field. `config.py`: exposes `STT_PROVIDER`. `pipeline/audio.py`: `AudioProcessor.__init__` and `transcribe()` dispatch on provider. MLX model warm-up on init.
- Benchmark tooling: `benchmark_record.py` (mic capture + silence-based splitting), `benchmark_stt.py` (multi-engine benchmark with WER + RTF).
- STARTUP log line changed: `STARTUP STT provider=mlx model=base` (was `STARTUP Whisper model=base device=cpu`).

**Latency benchmark results (earlier March 28, 2026):**
- TIMING logs updated: `utterance_done` now reports `speech=N.NNs silence=N.NNs` separately. `silence_detected` promoted to INFO.
- Silence wait: consistently 0.50s (one check interval — tight).
- Whisper: ~450ms (higher than old ~120ms baseline — likely due to longer utterances / meeting audio quality).
- LLM (GPT-4.1-mini): 0.9–1.6s, consistent with prior baseline.
- Kokoro TTS: 0.6–1.7s, scaling with response length.
- Speculative processing: confirmed working — saved ~1s LLM time on 2 of 5 interactions.
- Model log (`docs/model-log.md`) updated with new TIMING format and baselines table.

**TTS text sanitization (March 28, 2026):**
- `pipeline/sanitize.py`: `sanitize_for_speech(text)` cleans LLM output before TTS. Handles arrows (→ "then"), math operators (→ spoken words), markdown stripping, code symbols (underscores/brackets/backslashes → spaces), em dashes/semicolons → commas, ampersands → "and". Zero-latency regex pass.
- Wired into `pipeline/runner.py` in `_finalize_prompt()` — runs after LLM reply resolved, before TTS synthesis starts. Catches all paths (normal + speculative).
- `tests/test_sanitize.py`: 27 test cases. Run with `python tests/test_sanitize.py`.

**Voice/TTS design decisions (March 28, 2026):**
- Local TTS trimmed to Kokoro-only. Piper and macos_say removed as shipped options. `af_heart` is the default.
- Setup wizard (Phase 9) redesigned with re-runnable subcommands: `operator setup voice`, `setup keys`, `setup calendar`, `setup agent`. Each subcommand detects existing config, shows current values as defaults, only overwrites what the user changes. Same command for onboarding and post-onboarding settings changes.
- Voice selection fetches voice lists live from provider APIs (Kokoro HuggingFace repo, ElevenLabs `/voices`, OpenAI static list) — no hardcoded voice lists in our code.
- Preview links printed during voice selection so users can listen before choosing.
- One active voice at a time — no multi-voice support.
- Startup validation: if config references a broken voice/provider, tell user to run `operator setup voice`.

**Logging overhaul (March 28, 2026):**
- Standardized log format to `%(asctime)s %(levelname)s %(name)s — %(message)s` across all entry points (was missing module name on macOS).
- Added `STARTUP` prefix markers to all initialization steps in runner.py, audio.py, tts.py — `grep STARTUP /tmp/operator.log` shows full init sequence.
- Downgraded noisy connector UI automation logs (button clicks, popup dismissals) from INFO → DEBUG. Only meaningful state changes (joined, in meeting, left) remain at INFO.
- Reduced per-sample audio debug noise — silence/speech logs now fire only on state transitions, not every 0.5s check.
- Created `docs/model-log.md` — annotated reference log covering startup, ambient listening, wake-only, inline wake, LLM+TTS, conversation mode, timeout, and shutdown. Includes timing baselines and troubleshooting notes. Gitignored.
- Updated end-session skill with log verification step: if logging changed, compare against model log and update it.

**Watchdog race condition fix (March 28, 2026):**
`audio_capture.swift` watchdog was firing unconditionally after 10s even when capture had already started successfully — killing a working capture process. Fixed by adding `captureStarted` flag set in `startCapture` completion handler; watchdog checks it before `exit(3)`.

**RESOLVED — ScreenCaptureKit audio hang (March 28, 2026):**
Root cause: `audio_capture` binary had a stale TCC permission entry tied to its linker-generated codesign identity. After `tccutil reset` + manual re-add, macOS matched the binary to the zombie TCC record, causing `startCapture` to silently hang. Proven by testing an identical binary with a different codesign identifier — worked immediately.
Fix: re-signed with stable identifier `com.operator.audio-capture`. Hardened with three defense layers: (1) `CGPreflightScreenCaptureAccess()` pre-flight + dialog trigger, (2) 10s watchdog exits with code 3 instead of hanging, (3) `AgentRunner` detects code 3, re-signs binary, retries once. Also added `SCStreamDelegate` for error reporting, dedicated audio dispatch queue, minimized video overhead (2x2 @ 1fps). Live meeting test confirmed working end-to-end. See Hard-Won Knowledge for full details.

**Step 7.4 complete — mechanics (March 28, 2026):**
- `__main__.py`: macOS now accepts URL arg and joins headlessly via MacOSAdapter (bypasses menu bar app). Fixes `python __main__.py <url>` being silently ignored on macOS.
- `assets/ack_*.mp3`: All three ack clips regenerated with Kokoro Heart voice (af_heart) for voice consistency.
- `pipeline/fillers.py`: New module. `classify(text)` — empathetic-first priority, then short-query gate (≤8 words → neutral), then cerebral keywords, else neutral. Empathetic keywords include intensifiers (really/very/seriously); removed ambiguous "hard"/"lost". `get_clips(bucket)` — returns shuffled clip paths, falls back to neutral if bucket empty, returns `[]` if no clips at all (graceful no-op).
- `pipeline/tts.py`: `speak()` split into `synthesize() -> bytes` + `play_audio(bytes)`. All three providers (kokoro, openai, elevenlabs) now buffer to bytes before playback. `speak()` preserved as thin wrapper for backward compat.
- `pipeline/llm.py`: `ask(record=False)` skips history update. `record_exchange(user, reply)` commits a speculative exchange manually. This keeps history clean when speculative LLM calls are discarded.
- `pipeline/audio.py`: `capture_next_utterance()` gains `on_first_silence` callback — fires once with a snapshot of accumulated audio bytes when silence_count first hits 1 (~500ms of silence). Non-blocking hook for speculative processing.
- `pipeline/runner.py`: Full speculative + filler loop wired.
  - `_SpeculativeResult`: holds transcript, full_prompt, llm_reply, ready Event.
  - `_make_speculative_callback()`: returns the on_first_silence hook that spawns `_run_speculative()` in a background thread.
  - `_run_speculative()`: Whisper on first-silence snapshot → LLM (record=False) → stores in spec. Runs during the ~500ms second silence chunk.
  - `_finalize_prompt(prompt, speculative=)`: checks spec result (exact transcript match); if hit, calls `record_exchange()` and skips LLM call; if miss/timeout, falls back to normal LLM call. Then starts synthesis in a background thread, plays filler clips in foreground until synthesis_done is set, then plays response.
  - Both wake-only prompt capture and conversation follow-up mode now pass speculative callbacks.
- `scripts/gen_fillers.py`: Offline generation script — 43 phrases across 3 buckets using Kokoro Heart + ffmpeg → MP3. All phrases shortened to 1–4 syllables; ffmpeg `silenceremove` filter trims Kokoro padding (clips now 0.34–0.79s, down from 1.2–3.5s). Run once with python3.11.
- `assets/fillers/{neutral,cerebral,empathetic}/`: 43 clips generated and saved (neutral: 14, cerebral: 15, empathetic: 14).

**Baseline logs captured (March 27, 2026 session):** Pre-7.4 timings from `/tmp/operator.log`: silence detection ~1s, Whisper ~120ms, LLM 0.6–2.1s (avg ~1.2s), Kokoro synthesis ~1.23s, total from end of speech ~3–4s. Use these as benchmark against post-clip live test.

**Step 7.3 complete (March 27, 2026):**
- Full benchmark across 11 providers. Final quality scores: `{"elevenlabs": 5, "openai_tts1hd": 5, "openai_mini_tts": 5, "kokoro_isabella": 5, "kokoro_sky": 5, "kokoro_heart": 4, "kokoro_emma": 4, "openai_tts1": 4, "macos_say": 3, "piper_lessac": 3, "piper": 2}`
- Decision: `kokoro_heart` (af_heart) as default local voice (4/5, free). `gpt-4o-mini-tts` for openai tier (5/5, ~0.87s TTFAB, cheapest). ElevenLabs unchanged (5/5, ~0.39s TTFAB).
- Multi-provider architecture implemented in `pipeline/tts.py` and `config.yaml`. TTSClient now takes only `output_device`; all provider clients are lazy-inited internally. Kokoro wrapped in try/except ImportError with graceful fallback to macos_say. `ELEVENLABS_API_KEY` is now optional in `.env`.
- Kokoro requires Python 3.10–3.12 (caps at <3.13). System python3 on this Mac is 3.14 — Kokoro must be installed under python3.11 (`pip3.11 install kokoro soundfile`). For open-source users: document Python 3.10–3.12 requirement for local tier; rest of project works on any Python.
- Sentence streaming analysis done: TTFAB is length-independent → sentence streaming is the highest-leverage latency win available (gives back full LLM generation time for free). Implement in a later step.
**Phase 6 progress (March 26, 2026):**
- Step 6.1: `pipeline/runner.py` created — `AgentRunner` class encapsulates the full transcription loop, prompt handling, acknowledgment playback, and audio capture lifecycle. Interface: `AgentRunner(connector, tts_output_device, on_state_change, stop_event)`.
- Step 6.1.5: `calendar_join.py` deleted, replaced with `caldav_poller.py` — CalDAV + system keychain, no OAuth. `config.yaml` gained `caldav.bot_gmail` field. `requirements.txt` updated (removed google-auth-oauthlib/google-api-python-client, added caldav/keyring).
- Step 6.2: `app.py` simplified from 426 → 205 lines. Now a thin macOS shell: creates `MacOSAdapter` + `AgentRunner` + `CalDAVPoller`, wires `_on_conv_state_change` callback for menu bar icon updates, calls `runner.run()`. All pipeline logic removed from app.py.
- End-to-end test passed (March 26, 2026): wake phrase detected via `say` command, LLM responded correctly, TTS fired through BlackHole, state machine cycled correctly. TTS not audible to user (expected — BlackHole has no physical output; audio goes into meeting participants' ears in real use).
- Step 6.3: `run_linux.py` created — Linux entry point. Accepts meeting URL as CLI arg or MEETING_URL env var. Checks $DISPLAY and PulseAudio sinks (MeetingOutput + MeetingInput) before starting, fails fast with actionable error if prerequisites are missing. Instantiates LinuxAdapter + AgentRunner(tts_output_device="pulse/MeetingOutput") and calls runner.run(url).
- Step 6.4: `__main__.py` created — cross-platform entry point. argparse `--help` works on both platforms. On macOS → launches OperatorApp (rumps menu bar). On Linux → same preflight checks as run_linux.py + dispatches to LinuxAdapter. Platform-specific imports deferred inside functions so the file imports cleanly everywhere. Note: `python -m operator` conflicts with stdlib `operator` module — use `python __main__.py` or `python .` until Step 8.1 (pyproject.toml) resolves this.
**Phase 3 complete (March 25, 2026):** Full end-to-end pipeline verified in live Google Meet. Wake phrase detected, STT transcribes, LLM responds, TTS fires, meeting participants can hear Operator. Audio OUT path fixed via `module-virtual-source` (see Hard-Won Knowledge).
**Reorientation (March 25, 2026):** Product direction shifted from cloud-hosted to local-machine-first open-source. DockerAdapter will become LinuxAdapter (local). Cloud artifacts move to `cloud/`. Performance iteration added before setup wizard.

---

## Repo State

Local git repo at `~/Desktop/operator`. GitHub: `github.com/dufis1/operator` (private). Also cloned at `~/operator` on droplet `operator-dev` (`64.23.182.26`). Initial commit: `539ac57`. SSH access to the droplet is available — use `ssh root@64.23.182.26 "<command>"` directly via Bash without asking the user.

**Secrets (never commit):** `.env`, `browser_profile/`, `auth_state.json`
All excluded via `.gitignore`.

---

## Current File Layout

```
operator/
├── app.py                     # macOS UI shell — imports from pipeline.*
├── audio_capture.swift        # macOS-only: ScreenCaptureKit system audio capture
├── audio_capture              # compiled Swift binary (gitignored)
├── calendar_join.py           # TO BE DELETED — replaced by CalDAV poller (Phase 9)
├── setup.py                   # macOS app bundle config (py2app)
├── product-strategy.md        # authoritative product strategy
├── next-steps.md              # strategic overview of phases 4-11
├── refactor-plan.md           # human-readable checklist
├── agent-context.md           # this file
├── requirements.txt
├── .env / auth_state.json  # secrets, all gitignored
├── .gitignore / .vscode/settings.json
├── pipeline/
│   ├── __init__.py
│   ├── audio.py               # AudioProcessor: buffer, silence detection, Whisper STT; on_first_silence hook
│   ├── wake.py                # detect_wake_phrase: inline vs wake-only detection
│   ├── conversation.py        # ConversationState: idle/listening/thinking/speaking
│   ├── fillers.py             # Filler clip management: classify(text) → bucket, get_clips(bucket) → paths
│   ├── llm.py                 # LLMClient: GPT-4.1-mini; ask(record=False) + record_exchange() for speculative
│   ├── runner.py              # AgentRunner: speculative Whisper+LLM + filler loop + pipeline orchestration
│   └── tts.py                 # TTSClient: synthesize()->bytes + play_audio(bytes) split; speak() wrapper
├── connectors/
│   ├── __init__.py
│   ├── base.py                # MeetingConnector: abstract interface
│   ├── macos_adapter.py       # MacOSAdapter: ScreenCaptureKit + Playwright/Chrome
│   └── docker_adapter.py      # DockerAdapter: PulseAudio + headless Chromium (cloud/Docker)
├── docker/
│   ├── Dockerfile
│   ├── Dockerfile.bench
│   ├── Dockerfile.probe_b2
│   ├── entrypoint.py          # cloud/Docker entry point
│   ├── pulse_setup.sh         # PulseAudio virtual sink setup for container
│   ├── bench_stt.py
│   └── whisper_bench.py
├── assets/
│   ├── ack_yeah.mp3 / ack_yes.mp3 / ack_mmhm.mp3  # Kokoro Heart voice
│   └── fillers/
│       ├── neutral/            # clips pending gen_fillers.py
│       ├── cerebral/
│       └── empathetic/
├── scripts/
│   ├── generate_backchannel.py
│   ├── gen_fillers.py         # run with python3.11 to populate assets/fillers/
│   ├── auth_export.py         # exports Chrome session to auth_state.json
│   └── probe_screenshot.py
└── tests/
    ├── test_audio_processor.py
    ├── test_smoke_docker.py
    ├── test_pipeline.py
    ├── probe_a1_headless_meet.py / probe_a2_stealth_meet.py
    ├── probe_b2_whisper_docker.py
    └── test_*.py
```

---

## Target File Layout (post-refactor, Phases 4–6)

```
operator/
├── app.py                     # macOS entry point (menu bar shell — thin wrapper)
├── config.yaml                # loadout config — all configurable values
├── pyproject.toml             # packaging (pip install -e .)
├── LICENSE                    # MIT
├── README.md                  # rewritten for open-source audience
├── requirements.txt
├── .env / auth_state.json
├── .gitignore
├── pipeline/
│   ├── __init__.py
│   ├── audio.py
│   ├── wake.py
│   ├── conversation.py
│   ├── llm.py
│   ├── tts.py
│   └── runner.py              # shared transcription loop (Phase 6)
├── connectors/
│   ├── __init__.py
│   ├── base.py
│   ├── macos_adapter.py
│   └── linux_adapter.py       # local Linux headless adapter (replaces docker_adapter.py)
├── assets/
│   └── *.mp3
├── scripts/
│   ├── generate_backchannel.py
│   ├── auth_export.py
│   ├── linux_setup.sh         # creates PulseAudio virtual sinks on local Linux
│   └── setup_wizard.py        # Phase 9
├── tests/
│   ├── probe_a1_headless_meet.py / probe_a2_stealth_meet.py
│   ├── probe_b2_whisper_docker.py
│   ├── test_pipeline.py
│   ├── test_smoke_docker.py
│   └── test_*.py
└── cloud/                     # cloud deployment artifacts — separated, not primary
    └── docker/
        ├── Dockerfile
        ├── Dockerfile.bench
        ├── Dockerfile.probe_b2
        ├── entrypoint.py
        ├── pulse_setup.sh
        ├── bench_stt.py
        └── whisper_bench.py
```

---

## Hard-Won Knowledge (read before touching relevant code)

- **Whisper drops first word** without 0.5s silence pad prepended to audio. Never remove.
- **Backchannel echo:** clips play through BlackHole → back into capture. Drain audio buffer after playback.
- **Wake phrase is "operator" only.** "hey operator" rejected (Whisper drops "hey"); "operate" rejected (false positives).
- **ElevenLabs requires paid plan** — free tier gets flagged for abuse.
- **Real Chrome required on macOS** (not Playwright's bundled "Chrome for Testing") — only real Chrome gets mic permission.
- **20s conversation mode timeout** — after response, stays in listening mode 20s before idle.
- **ScreenCaptureKit requires `.app` bundle** on macOS — silently fails from plain Python script.
- **ScreenCaptureKit TCC entries are tied to codesign identity** — if the `audio_capture` binary is recompiled without a stable `--identifier`, macOS generates a hash-based identity. After a TCC reset or macOS update, the old identity's permission entry becomes stale — `startCapture` silently hangs forever (no error, no dialog). Fix: always sign with `codesign --force --sign - --identifier com.operator.audio-capture audio_capture` after compiling. The binary has three layers of defense: (1) `CGPreflightScreenCaptureAccess()` pre-flight check that can trigger the permission dialog, (2) a 10-second watchdog that exits with code 4 if `startCapture` hangs, and (3) Python-side pre-launch codesign identity verification. Never ship `audio_capture` with only a linker-generated signature.
- **ScreenCaptureKit phantom hang (March 2026):** `startCapture` hung with no error despite `CGPreflightScreenCaptureAccess()` returning true and `SCShareableContent.get` succeeding — with no code changes, no recompile, and Screen Recording permission visibly toggled on. Recompiling, re-signing, toggling permission, and restarting VS Code all failed. A full system restart fixed it. Root cause: macOS `tccd` (TCC daemon) cached a stale permission denial in memory that didn't match the on-disk database. Likely triggered by an unclean process shutdown or sleep/wake cycle. Recovery ladder: (1) try `tccutil reset ScreenCapture` to flush TCC cache without rebooting, (2) if that fails, full system restart. Monitor for recurrence.
- **Chrome SingletonLock stale after unclean shutdown** — if the Operator process is killed (SIGKILL, crash, force quit) without running `browser.close()`, Chrome leaves a `SingletonLock` file in `browser_profile/` that blocks the next launch with "Failed to create a Process Singleton." The MacOSAdapter pre-launch cleanup (line 100-103) removes it automatically. To prevent: `__main__.py` now has SIGTERM handlers and try/finally blocks on both macOS and Linux entry points, and `_browser_session` closes the browser in a finally block. Only SIGKILL and hard crashes can still leave the lock.
- **Do NOT auto-re-sign `audio_capture` at runtime** — the original auto-retry logic re-signed the binary with `codesign --force --sign -` on capture failure, which changed the binary's code identity and invalidated the Screen Recording TCC grant for the parent app. This caused a cascading failure: the first run's re-sign broke all subsequent runs until the user toggled permission off/on and restarted the terminal app. Removed March 2026. Recovery for capture failures is now: (1) pre-launch codesign identity verification, (2) differentiated exit codes (3=permission denied, 4=watchdog timeout), (3) automatic `tccutil reset ScreenCapture` + retry on exit 4, (4) escalate to user with clear message.
- **PyObjC packages are fragile** — never install new `pyobjc-framework-*` without checking prior issues.
- **`WHISPER_HALLUCINATIONS` filter** — catches common false positives on silence. Add patterns as found.
- **Audio output device is BlackHole only (`coreaudio/BlackHole2ch_UID`) on macOS** — do NOT change to Multi-Output Device. mpv plays TTS → BlackHole → Chrome mic → call participants hear Operator. Multi-Output Device causes voice to play through MacBook speakers.
- **Ghost session in Meet:** Closing the browser without clicking Leave leaves the Operator account registered as "in the meeting." Next join attempt shows "Switch here" instead of "Join now." Fix: `leave()` must click the Leave button before `browser.close()`. Handle "Switch here" as a fallback join path.
- **Headless Chrome suppresses audio rendering:** In true headless mode (`headless=True`), Chrome disables audio output entirely. On Linux: fix is `headless=False` against Xvfb on `:99` with `DISPLAY=:99`. On macOS: fix is `headless=False` + `--headless=new` in launch args — Chrome's new headless renderer supports CoreAudio/BlackHole audio routing. Do not use `headless=True` on either platform.
- **`requestAnimationFrame` is suppressed in `--headless=new` Chrome:** rAF callbacks are throttled or never fire when Chrome is running headless (no visible frame to render). Any JS that batches work through `requestAnimationFrame` will silently queue mutations and never flush them. Use `setTimeout(fn, 0)` instead. This caused the entire caption MutationObserver pipeline to silently stop delivering text to Python — no errors, just silence. Symptom: observer attaches correctly, mutation count never appears in logs despite speech in the meeting. Fix: `connectors/captions_adapter.py` CAPTION_OBSERVER_JS uses `setTimeout(processPending, 0)` not `requestAnimationFrame`.
- **Google Meet guest join — residential vs. data center IPs:** On residential IPs (local machine, Docker Desktop), Meet shows a "Your name?" field — fill it and guest join works. On data center IPs (DigitalOcean droplet), Google shows "You can't join this video call" and blocks join entirely — bot detection fires on the IP. Production fix for cloud: export a real Google session via `scripts/auth_export.py` and load it as `storage_state` in Playwright.
- **PulseAudio must be started before Python:** `pulse_setup.sh` creates the virtual sinks. If Python starts first, `parec` gets `Connection refused`. Startup order: PulseAudio setup → Python.
- **PulseAudio default routing:** Chrome uses the default PulseAudio sink for audio output (meeting audio IN) and the default source for mic input (TTS audio OUT). Must set `pactl set-default-sink MeetingInput` and `pactl set-default-source MeetingOutput.monitor` after creating virtual devices. Without this, Chrome outputs to the wrong sink.
- **Chrome does not enumerate PulseAudio monitor sources as microphones:** `MeetingOutput.monitor` is a monitor source — Chrome's `getUserMedia()` returns `NotFoundError`. Fix: use `module-virtual-source` to wrap the monitor as a proper source named `VirtualMic`. Set `VirtualMic` as the default PulseAudio source. Audio path: mpv → MeetingOutput → MeetingOutput.monitor → VirtualMic → Chrome mic → WebRTC → participants. Do not revert to `MeetingOutput.monitor` as default source.
- **Audio quality on Apple Silicon (QEMU):** When running the `linux/amd64` Docker image on a Mac (ARM64), QEMU CPU emulation causes audio buffer underruns — Operator's voice sounds fuzzy/staticky. **Confirmed on native AMD64 (DigitalOcean droplet, March 2026): audio still choppy — QEMU is not the cause.** Root cause is sample rate mismatch in the TTS → PulseAudio → Chrome → WebRTC chain. Fix in Phase 7.2.
- **PulseAudio must run in user mode (not --system) on the droplet:** `pulseaudio --system --daemonize` creates a socket at `/run/pulse/native` which requires `pulse-access` group membership — parec and Chrome both get `Access denied`. Fix: `pulseaudio --daemonize` (no `--system`). User-mode socket lands at `/run/user/0/pulse/native` and is accessible to root without any group config.
- **DockerAdapter hardcodes `PULSE_RUNTIME_PATH=/tmp/pulse` for Chrome:** On bare Linux (not Docker), PulseAudio's user-mode socket is at `/run/user/0/pulse/native`, not `/tmp/pulse`. Chrome can't find PulseAudio, `getUserMedia` fails, Meet shows "mic not found", VirtualMic stays SUSPENDED. Fix without code change: `mkdir -p /tmp/pulse && ln -sf /run/user/0/pulse/native /tmp/pulse/native`. LinuxAdapter must not hardcode this path — let Chrome inherit `PULSE_SERVER` from environment or use the default socket discovery.
- **`mpv` is not installed by default on a bare Ubuntu droplet:** `apt install -y mpv` required. Without it, the acknowledgment clip playback crashes immediately after wake phrase detection.
- **DockerAdapter was cloud-oriented:** `docker_adapter.py` hardcodes `DISPLAY=:99` and `PULSE_RUNTIME_PATH=/tmp/pulse` for the Docker container environment. These must be removed/made environment-aware in `linux_adapter.py` for local machine use.
- **LLM round-trip is 0.9–3s** — not fixable in code; mask it with backchannels, don't try to eliminate it.
- **Porcupine removed** — app uses Whisper-based inline wake detection. `PORCUPINE_ACCESS_KEY` in `.env` is unused leftover.
- **CalDAV requires a Gmail app password** — a regular Gmail password will not work. App passwords are generated at myaccount.google.com/apppasswords (requires 2-Step Verification enabled on the account).
- **CalDAV app password must be stored in system keychain** — macOS Keychain or Linux Secret Service. Never store in `.env` or commit to the repo.
- **CalDAV poll interval is 1 minute** — this is the safe rate limit floor for Google's CalDAV endpoint. Do not poll faster.
- **Only accepted events appear via CalDAV** — the bot's Gmail must have accepted the meeting invite for the event to be visible. The user must accept invites on the bot's behalf; Operator cannot auto-accept.
- **Chrome requires `--no-sandbox` when running as root on a server** — without it, Chrome's audio service sandbox blocks PulseAudio socket access. Symptom: VirtualMic stays SUSPENDED, Meet shows "Microphone not found." Add to `launch_args` for both auth and guest paths.
- **Playwright `env=` in `launch()` replaces the full process environment** — passing `env={"DISPLAY": ":99"}` strips `XDG_RUNTIME_DIR`, `HOME`, `PATH`, etc. Chrome loses PulseAudio socket discovery. Fix: do NOT pass `env=` at all; set `DISPLAY` in the caller's environment before launching Python (`DISPLAY=:99 python3 run_linux.py`).
- **Chrome 130+ uses PipeWire by default for WebRTC audio on Linux** — if PipeWire is not installed, WebRTC audio capture silently fails (VirtualMic SUSPENDED) while video and regular Chrome audio still work. Fix: add `--disable-features=WebRTCPipeWireCapturer` to Chrome launch args to force PulseAudio.
- **Kokoro 0.9.4 requires spaCy `en_core_web_sm` model** — `KPipeline` downloads it at first run via `pip`. On systems where `pip` is not on PATH (Homebrew Python 3.14), download fails with "No package installer found." Fix: install the model directly — `pip3.11 install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl`. Kokoro is installed under Python 3.11 (`pip3.11 install kokoro soundfile`); run benchmark via `python3.11 scripts/bench_tts.py`.
- **Shift+C is a caption TOGGLE, not enable-only:** Pressing Shift+C when captions are already on turns them OFF. The persistent browser profile retains caption state across sessions, so captions are often already on at join. Blind retries toggle on→off→on→off and can exhaust all attempts with captions left off. Fix: `_captions_are_on()` checks state before each press — looks for `button[aria-label*="Turn off captions"]` (visible when on) and `[role="region"][aria-label="Captions"]` (exists in DOM when active). Only press Shift+C when confirmed off.
- **Join button sequential timeout wastes 5s:** The join flow originally tried "Join now" → "Ask to join" → "Switch here" sequentially, each with a 5s `wait_for` timeout. When "Join now" doesn't exist (guest joining), the full 5s expires before trying "Ask to join." Fix: race all three with `.or_()` — whichever exists resolves instantly.
- **HF Hub unauthenticated warning comes from child logger:** The "unauthenticated requests to HF Hub" warning is emitted by `huggingface_hub.utils._http` (a child logger), not the parent `huggingface_hub`. Setting the parent to ERROR doesn't suppress it. Fix: explicitly suppress both `huggingface_hub` and `huggingface_hub.utils._http` loggers during Kokoro init.
- **Playwright `with sync_playwright()` teardown can hang 20s+:** After `browser.close()` succeeds, the `with sync_playwright()` context manager exit can block indefinitely on greenlet/asyncio cleanup. Never join the browser thread to wait for full teardown. Fix: use a `threading.Event` (`_browser_closed`) that fires after `browser.close()` returns — `leave()` waits on that event, not the thread. Also suppress stderr during teardown to silence greenlet/TargetClosed noise.
- **`_shutdown()` double-call on Ctrl+C:** SIGINT handler calls `_shutdown()` → `connector.leave()`, then `KeyboardInterrupt` hits the `finally` block → `_shutdown()` again. Each `leave()` waits up to 20s for the browser thread, so double-call = 40s hang. Fix: `_shutdown_called` guard prevents re-entry.
- **Browser cleanup must run on ALL exit paths:** Early `return` on join failure (session expired, can't join, caption enable failed) skipped navigate-away and browser.close(), leaving Operator visible in the meeting for 60s until Meet's heartbeat timed out. Fix: wrap the session body in `try/finally` inside the `with sync_playwright()` block so cleanup runs on every exit path.
- **Camera toggle needs a wait even in headless:** `is_visible()` without `wait_for()` runs before Meet renders the camera button — always returns false. Meet still broadcasts a black camera feed in `--headless=new` mode, visible to other participants. Fix: keep `wait_for(timeout=2000)` on the camera button race.
- **`in_meeting_wait` is required before caption enable:** Removing the post-join "Leave call" button wait caused all 10 Shift+C caption attempts to fail. Meet's UI needs a moment to transition from the join animation to the in-meeting state before keyboard shortcuts register. Reduced timeout from 15s to 5s — resolves in ~0.5s on happy path.
- **Kokoro voice `am_cloud` does not exist** — the full voice list is in the HuggingFace repo `hexgrad/Kokoro-82M/voices/`. American Female: af_heart, af_sky, af_bella, af_sarah, af_nova, af_alloy, etc. British Female: bf_emma, bf_isabella, bf_alice, bf_lily. Use `am_michael` or `am_puck` for American Male.
- **PulseAudio user-mode on the droplet dies without `--exit-idle-time=-1`** — `pulseaudio --daemonize` as root exits immediately at idle. Fix: `pulseaudio --daemonize --exit-idle-time=-1`. Add to startup procedure.
- **Whisper cold-start on the droplet is ~28s; subsequent runs are <2s** — first inference triggers JIT/model warmup. Not fixable in code; warm up Whisper before entering the transcription loop or use a persistent inference thread. Addressed in Step 7.6.
- **mpv drain inflated to ~5s for short TTS clips** — mpv buffers aggressively; drain time does not track audio duration. Investigate `--audio-buffer=50` or streaming TTS directly to parec/pacat to bypass mpv.
- **Ctrl+C → Chrome stays in meeting 60s (three stacked issues):** (1) **rumps swallows SIGINT at the C level** — `NSApplication.run()` calls `_exit()` on SIGINT, bypassing all Python cleanup (signal handlers, atexit, finally blocks — nothing runs). Fix: `python __main__.py` no longer uses rumps; runs `run_polling()` on the main thread directly so SIGINT delivers as normal `KeyboardInterrupt`. rumps is only used by `Operator.app`. (2) **SIGINT kills Chrome via process group** — the terminal sends SIGINT to all processes in the foreground group (Python, Playwright driver, Chrome). Fix: `__main__.py` monkey-patches `subprocess.Popen.__init__` to set `start_new_session=True`, so child processes are in their own session and don't receive SIGINT. (3) **`browser.close()` ≠ leaving the meeting** — closing Chrome (gracefully or not) just drops the WebRTC connection; Meet's server waits ~60s for its heartbeat to expire. Fix: navigate to `about:blank` before closing, which triggers Meet's JS `beforeunload` handler that sends an explicit leave signal to the server. Also critical: the navigate + `browser.close()` must happen **inside** the `with sync_playwright()` block — if they're in a `finally` outside it, the Playwright driver is already dead.
- **Google Meet and Calendar use different session scopes:** `auth_export.py` visiting only `accounts.google.com` establishes a session for Meet but NOT Calendar. The calendar poller gets redirected to login even though Meet works fine with the same profile. Fix: `auth_export.py` must also navigate to `calendar.google.com` after login to establish Calendar's service-specific cookies (`service=cl`).
- **Playwright `headless=True` vs Chrome `--headless=new` — different cookie stores:** Playwright's `headless=True` launches Chrome's old headless mode, which is essentially a different browser binary that cannot decrypt cookies stored by real Chrome. The calendar poller must use `headless=False` + `--headless=new` in Chrome args (same pattern as `CaptionsAdapter`) to share the profile's cookie encryption.

---

## Environment Setup

- [x] **Env A** — Secrets recovered from USB: `.env` (all API keys present), `credentials.json`, `token.json`. `operator_mac.ppn` discarded (Porcupine removed).
- [x] **Env B** — `.gitignore` created.
- [x] **Env C** — `requirements.txt` created. Cross-platform at top; macOS-only (`rumps`, `pyobjc-core`, `pyobjc-framework-Cocoa`) noted at bottom — exclude from Docker.
- [x] **Env D** — venv created, deps installed, Playwright Chromium downloaded.
- [x] **Env E** — `.vscode/settings.json` created.
- [x] **Env F** — Upgrade Python 3.9 → 3.11.
- [x] **Env G** — Recreate `browser_profile/` by signing into Operator Google account.
- [x] **Env H** — New machine setup: BlackHole 2ch, mpv, Swift helper compiled, app bundle rebuilt.

---

## Phase -1: Pre-Validation Probes ✅

- [x] **Probe A.1** — Headless Chrome + Google Meet (no stealth): PASSES
- [x] **Probe A.2** — Headless Chrome + Google Meet (stealth config): PASSES
- [x] **Probe B.1** — Docker Desktop installed
- [x] **Probe B.2** — PulseAudio + Whisper accuracy in Docker: PASSES (9.1% WER, matches local baseline)

---

## Phase 0: Codebase Cleanup ✅

- [x] 0.1 — Delete benchmark files
- [x] 0.2 — Delete `spec.md`
- [x] 0.3 — Move test files into `tests/`
- [x] 0.4 — Create `scripts/`, move `generate_backchannel.py`
- [x] 0.5 — Create `assets/`, move ack `.mp3` files, update paths in `app.py`

---

## Phase 1: Extract the Agent Pipeline ✅

- [x] 1.1 — Create `pipeline/__init__.py`
- [x] 1.2 — Extract audio processing → `pipeline/audio.py`
- [x] 1.3 — Extract wake phrase detection → `pipeline/wake.py`
- [x] 1.4 — Extract conversation state machine → `pipeline/conversation.py`
- [x] 1.5 — Extract LLM calls → `pipeline/llm.py`
- [x] 1.6 — Extract TTS → `pipeline/tts.py` (output device as parameter)

---

## Phase 2: Connector Interface ✅

- [x] 2.1 — Create `connectors/__init__.py`
- [x] 2.2 — Define `MeetingConnector` abstract interface → `connectors/base.py`
- [x] 2.3 — Implement `MacOSAdapter` → `connectors/macos_adapter.py`

---

## Phase 3: Docker/Cloud Adapter ✅

- [x] 3.0a–f — DigitalOcean droplet provisioned (`64.23.182.26`), Docker installed, code pushed
- [x] 3.1 — `pipeline/` imports cleanly on Linux (no macOS leaks)
- [x] 3.2 — `docker/Dockerfile` created
- [x] 3.4 — PulseAudio virtual audio routing in container
- [x] 3.5 — STT accuracy benchmark on container audio: PASS (3.3% WER)
- [x] 3.6 — `DockerAdapter` implemented → `connectors/docker_adapter.py`
- [x] 3.7 — `docker/entrypoint.py` created, wired to pipeline
- [x] 3.8 — `tests/test_smoke_docker.py` created and passing

---

## Phase 4: Reorient — Cloud Cleanup + Linux Local Adapter

### Step 4.1 — Move Docker files to `cloud/` ✅

Move all cloud deployment artifacts into a `cloud/` subdirectory. This keeps the code but removes it from the top-level view.

```bash
mkdir -p cloud
mv docker cloud/docker
```

Update `.gitignore` if needed. Check that no imports in `pipeline/` or `connectors/` reference `docker/` paths — there shouldn't be any.

**Test:** `python -c "from pipeline import audio, wake, conversation, llm, tts; print('ok')"` — no errors.
**Commit:** `chore: move cloud/Docker deployment artifacts to cloud/ subdirectory`

---

### Step 4.2 — Create `connectors/linux_adapter.py` from `docker_adapter.py` ✅

Copy `connectors/docker_adapter.py` to `connectors/linux_adapter.py`. Rename the class `LinuxAdapter`. Make these changes:

1. **Remove** the hardcoded `env={"DISPLAY": ":99", "PULSE_RUNTIME_PATH": "/tmp/pulse"}` from the Playwright launch call. Replace with: read `DISPLAY` from `os.environ` (fall back to `:99` only if not set), and do NOT set `PULSE_RUNTIME_PATH` — let PulseAudio use its system default socket.
2. **Remove** `--no-sandbox` from the default `launch_args` list. Add a note: "re-add if running as root." Keep it available as an optional constructor parameter.
3. **Rename** all logging strings from `DockerAdapter` to `LinuxAdapter`.
4. **Keep** `docker_adapter.py` in place — it will move to `cloud/` in a later cleanup step. Do not delete it yet.

**Test:** Import check: `python -c "from connectors.linux_adapter import LinuxAdapter; print('ok')"`.
**Commit:** `feat: add LinuxAdapter for local-machine headless Linux (connectors/linux_adapter.py)`

---

### Step 4.3 — Create `scripts/linux_setup.sh` ✅

Create a shell script that sets up the required PulseAudio virtual audio devices on a local Linux machine. This is the same set of `pactl` commands that `cloud/docker/pulse_setup.sh` runs at container startup, adapted for local use (no Docker-specific paths).

```bash
#!/usr/bin/env bash
# Operator — Linux local audio setup
# Creates PulseAudio virtual devices required for meeting audio routing.
# Run once per session (devices reset on reboot or when PulseAudio restarts).
set -e

pactl load-module module-null-sink sink_name=MeetingOutput sink_properties=device.description=MeetingOutput
pactl load-module module-null-sink sink_name=MeetingInput sink_properties=device.description=MeetingInput
pactl load-module module-virtual-source source_name=VirtualMic master=MeetingOutput.monitor source_properties=device.description=VirtualMic

pactl set-default-sink MeetingInput
pactl set-default-source VirtualMic

echo "Operator: PulseAudio virtual devices ready."
echo "  Audio IN  (meeting → Operator): parec --device=MeetingInput.monitor"
echo "  Audio OUT (Operator → meeting): mpv --audio-device=pulse/MeetingOutput"
```

Make executable: `chmod +x scripts/linux_setup.sh`

**Test:** On a Linux machine (or inside the existing Docker container for now): run `bash scripts/linux_setup.sh` → no errors → `pactl list short sinks` shows `MeetingOutput` and `MeetingInput`.
**Commit:** `feat: add scripts/linux_setup.sh for local Linux PulseAudio setup`

---

### Step 4.4 — Update `connectors/__init__.py` ✅

If `connectors/__init__.py` imports or references `DockerAdapter`, update it to also expose `LinuxAdapter`. Do not remove `DockerAdapter` — it's still referenced by `cloud/docker/entrypoint.py`.

**Test:** `python -c "from connectors import LinuxAdapter; print('ok')"` (adjust based on what `__init__.py` actually exports).
**Commit:** `chore: expose LinuxAdapter in connectors/__init__.py`

---

### Step 4.5 — Verify `LinuxAdapter` end-to-end (local Linux or native droplet) ✅

Verified on `operator-dev` droplet (64.23.182.26, native AMD64, no Docker), March 2026.

Full wake → LLM → TTS cycle confirmed working. Key findings:
- Audio still choppy on native AMD64 → QEMU is not the cause → Phase 7.2 (sample rate audit) needed
- PulseAudio must run in user mode (`pulseaudio --daemonize`, not `--system`) — see Hard-Won Knowledge
- `mpv` must be installed separately (`apt install mpv`)
- DockerAdapter's hardcoded `PULSE_RUNTIME_PATH=/tmp/pulse` breaks bare Linux; symlink workaround required — LinuxAdapter must not repeat this

**Commit:** `test: verify LinuxAdapter end-to-end on native Linux (no Docker)`

---

### Step 4.6 — Verify `MacOSAdapter` end-to-end on local macOS ✅

Verified March 2026. Full wake → LLM → TTS cycle confirmed on macOS. Key findings:
- `headless=True` suppresses audio on macOS (same as Linux) — ScreenCaptureKit captures silence
- Fix: `headless=False` + `--headless=new` in launch args. Chrome's new headless renderer supports CoreAudio/BlackHole routing
- TCC Screen Recording permission requires ad-hoc signed bundle (`codesign --force --deep --sign -`) — unsigned alias builds don't hold the grant
- Full build (`py2app` without `-A`) is preferred for distribution; alias build needs re-signing after each rebuild

**Commit:** `test: confirm MacOSAdapter end-to-end on local macOS after Phase 4 reorientation`

---

## Phase 5: Config System (The Loadout)

### Step 5.1 — Create `config.yaml`

Create `config.yaml` in the repo root. This is the loadout — the single serializable unit of agent configuration. API keys stay in `.env`; `config.yaml` is for everything else.

```yaml
# Operator loadout config
# Secrets (API keys) stay in .env — this file is safe to commit and share.

agent:
  name: "Operator"
  wake_phrase: "operator"
  system_prompt: >
    You are Operator, an AI assistant in a video call.
    Keep responses short and conversational — under 30 words.
    Avoid bullet points, headers, or markdown — speak naturally.
  interaction_mode: "voice"      # voice | chat | both
  conversation_timeout: 20       # seconds in listening mode after a response

llm:
  provider: "openai"
  model: "gpt-4.1-mini"

tts:
  provider: "elevenlabs"
  voice_id: "JBFqnCBsd6RMkjVDRZzb"
  model: "eleven_turbo_v2"

stt:
  model: "base"                  # faster-whisper model size: tiny | base | small | medium
  device: "cpu"
  compute_type: "int8"

connector:
  type: "auto"                   # auto | macos | linux | docker
  browser_profile_dir: "./browser_profile"
  auth_state_file: null          # path to auth_state.json, or null for guest join
```

**Test:** `python -c "import yaml; c = yaml.safe_load(open('config.yaml')); print(c['agent']['name'])"` → `Operator`.
**Commit:** `feat: add config.yaml — externalize all agent configuration (loadout)`

---

### Step 5.2 — Create `config.py`

Create `config.py` in the repo root. This is the single source of truth for all modules.

```python
import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
_ROOT = Path(__file__).parent
_config = yaml.safe_load((_ROOT / "config.yaml").read_text())

# Agent
AGENT_NAME           = _config["agent"]["name"]
WAKE_PHRASE          = _config["agent"]["wake_phrase"]
SYSTEM_PROMPT        = _config["agent"]["system_prompt"]
INTERACTION_MODE     = _config["agent"]["interaction_mode"]
CONVERSATION_TIMEOUT = _config["agent"]["conversation_timeout"]

# LLM
LLM_PROVIDER = _config["llm"]["provider"]
LLM_MODEL    = _config["llm"]["model"]

# TTS
TTS_PROVIDER = _config["tts"]["provider"]
TTS_VOICE_ID = _config["tts"]["voice_id"]
TTS_MODEL    = _config["tts"]["model"]

# STT
STT_MODEL        = _config["stt"]["model"]
STT_DEVICE       = _config["stt"]["device"]
STT_COMPUTE_TYPE = _config["stt"]["compute_type"]

# Connector
CONNECTOR_TYPE       = _config["connector"]["type"]
BROWSER_PROFILE_DIR  = _config["connector"]["browser_profile_dir"]
AUTH_STATE_FILE      = _config["connector"]["auth_state_file"]

# Secrets from .env
OPENAI_API_KEY    = os.environ["OPENAI_API_KEY"]
ELEVENLABS_API_KEY = os.environ["ELEVENLABS_API_KEY"]
```

**Test:** `python -c "import config; print(config.AGENT_NAME)"` → `Operator`.
**Commit:** `feat: add config.py — single source of truth for all configuration`

---

### Step 5.3 — Wire `config.py` into pipeline modules

Replace hardcoded constants in each `pipeline/` module with imports from `config`. One module at a time. Test after each.

Order: `pipeline/llm.py` (SYSTEM_PROMPT, LLM_MODEL, OPENAI_API_KEY) → `pipeline/tts.py` (TTS_VOICE_ID, TTS_MODEL, ELEVENLABS_API_KEY) → `pipeline/wake.py` (WAKE_PHRASE) → `pipeline/conversation.py` (CONVERSATION_TIMEOUT) → `pipeline/audio.py` (STT_MODEL, STT_DEVICE, STT_COMPUTE_TYPE).

**Test after each module:** Full wake → LLM → TTS cycle. Confirm behavior unchanged.
**Commit (one per module):** e.g. `refactor: read LLM config from config.py in pipeline/llm.py`

---

### Step 5.4 — Wire `config.py` into adapters and entry points

Update `app.py` and `connectors/linux_adapter.py` (and `connectors/macos_adapter.py` if it has hardcoded values) to read from `config`.

**Test:** Change `agent.name` in `config.yaml` to something different → confirm the agent joins meetings under the new name. Revert.
**Commit:** `refactor: read connector and agent config from config.py in adapters and entry points`

---

## Phase 6: Consolidate Entry Points

### Step 6.1 — Create `pipeline/runner.py`

Extract the shared transcription loop that exists in both `app.py` and `cloud/docker/entrypoint.py` into `pipeline/runner.py`. The runner takes a `MeetingConnector` instance and starts the main loop: audio capture → wake detection → LLM → TTS.

```python
# pipeline/runner.py
class AgentRunner:
    def __init__(self, connector: MeetingConnector, config):
        ...
    def run(self, meeting_url: str):
        ...  # the transcription loop
```

**Test:** `python -c "from pipeline.runner import AgentRunner; print('ok')"`.
**Commit:** `refactor: extract shared transcription loop into pipeline/runner.py`

---

### Step 6.1.5 — Replace `calendar_join.py` with `caldav_poller.py`

Do this before simplifying `app.py` so that the old `CalendarPoller` import is gone before the thin-shell refactor — one clean pass instead of two partial ones.

Delete `calendar_join.py`. Create `caldav_poller.py` in the repo root. The poller:
- Connects to the bot's Google Calendar via CalDAV using `caldav` library + app password from system keychain
- Polls every 60 seconds (do not poll faster — this is Google's safe rate floor)
- For each event starting within the join window: checks that the event is accepted and has a Google Meet link
- Calls `connector.join(meet_url)` for matching events

Keychain access: use `keyring` library (`keyring.get_password("operator", bot_gmail)`). The setup wizard (Phase 9) writes the credential; the poller reads it. For this step, store the credential manually: `keyring.set_password("operator", bot_gmail, app_password)`.

CalDAV connection pattern:
```python
import caldav, keyring
password = keyring.get_password("operator", bot_gmail)
client = caldav.DAVClient(
    url="https://www.google.com/calendar/dav/{bot_gmail}/events/",
    username=bot_gmail,
    password=password,
)
```

Remove `google-api-python-client` and `google-auth-oauthlib` from `requirements.txt`. Add `caldav` and `keyring`.

**Test:** With app password manually stored in keychain, create a test calendar event with a Meet link starting in 2 minutes → confirm poller calls `connector.join()`.
**Commit:** `feat: replace calendar_join.py with caldav_poller.py — CalDAV-based meeting detection`

---

### Step 6.2 — Simplify `app.py` to use `runner.py`

`app.py` becomes a thin macOS shell: instantiate `MacOSAdapter`, instantiate `AgentRunner`, wire state change callbacks to menu bar icon updates, call `runner.run()`. Wire `caldav_poller.py` here instead of the old `CalendarPoller`.

**Test:** Full end-to-end macOS test — wake phrase → response.
**Commit:** `refactor: simplify app.py to thin macOS shell using pipeline/runner.py`

---

### Step 6.3 — Create Linux entry point using `runner.py`

Create `run_linux.py` (or `__main__.py` for `python -m operator`): check `$DISPLAY` and PulseAudio sinks are set up, instantiate `LinuxAdapter`, instantiate `AgentRunner`, call `runner.run(MEETING_URL)` where `MEETING_URL` is passed as a CLI argument or env var.

**Test:** On Linux, `python run_linux.py <meet-url>` → agent joins and responds to wake phrase.
**Commit:** `feat: add run_linux.py — Linux local entry point using LinuxAdapter + AgentRunner`

---

### Step 6.4 — Add OS auto-detection

Create `__main__.py` so `python -m operator` works. Auto-detect OS: if `sys.platform == "darwin"` → use `MacOSAdapter`, else → use `LinuxAdapter`.

**Test:** `python -m operator --help` works. On macOS, runs macOS adapter. On Linux, runs Linux adapter.
**Commit:** `feat: add __main__.py with OS auto-detection — python -m operator works on both platforms`

---

## Phase 7: Performance Iteration

### Step 7.1 — Test audio quality on native AMD64 (no QEMU) ✅

Tested on `operator-dev` droplet (64.23.182.26, native AMD64, no Docker). Audio still choppy — QEMU ruled out. Root cause is sample rate mismatch (see Step 7.2). See Hard-Won Knowledge for full finding.

---

### Step 7.2 — Sample rate audit + fix ✅

**Diagnosed (March 26, 2026):** PulseAudio virtual sinks default to 44100Hz. Chrome's WebRTC engine runs at 48000Hz. PulseAudio's real-time 44100→48000 SRC (sample rate conversion) using the default `speex-float-1` resampler causes audible artifacts.

**Fix:** Added `rate=48000` to both `pactl load-module module-null-sink` calls in `scripts/linux_setup.sh`. Also fixed three blockers in `LinuxAdapter` discovered during live test (March 27, 2026):
1. Added `--no-sandbox` to Chrome launch args — required when running as root; without it Chrome's audio sandbox blocks PulseAudio.
2. Removed `env={"DISPLAY": display}` from `p.chromium.launch()` — Playwright replaces the full environment if `env=` is passed, stripping `XDG_RUNTIME_DIR` and breaking PulseAudio socket discovery.
3. Added `--disable-features=WebRTCPipeWireCapturer` — Chrome 130+ tries PipeWire first; fails silently on droplet (no PipeWire). Forces PulseAudio for WebRTC audio.

**Result:** Voice confirmed clear through WebRTC in live meeting (March 27, 2026). VirtualMic RUNNING, audio flowing to parec, Whisper transcribing correctly.

---

### Step 7.3 — TTS provider benchmark

ElevenLabs was chosen without a systematic evaluation. Before investing further in TTS reliability (Step 7.5), benchmark all three viable providers against each other in the actual meeting audio chain (after the 48kHz fix is in place).

**Providers to test:**
- **ElevenLabs** (`eleven_flash_v2_5`) — current provider. High voice quality. Requires paid plan.
- **OpenAI TTS** (`tts-1` / `tts-1-hd`) — same OpenAI API key already in use. Streaming supported. One fewer vendor.
- **Piper** (local, open source) — runs on the machine, no API call, no cost, outputs natively at any sample rate. Lower voice quality but aligns with open-source-first direction.

**Test phrases:** Use the same 8–10 phrases for all three — mix of short acknowledgments ("Got it, one moment"), longer explanations (2–3 sentences), and technical language.

**Measure for each:**
1. Latency to first audio chunk (time from `speak()` call to audio starting)
2. Total playback time per phrase
3. Cost per character (or $0 for Piper)
4. Setup complexity (install steps, dependencies added)
5. Voice quality through WebRTC — listen in an actual meeting, not just locally. WebRTC's Opus codec compresses audio; naturalness degrades differently per voice.

**Decision criteria:** Document scores in a short table. Pick the provider that best balances quality-through-WebRTC, latency, and vendor count. Update `config.yaml`, `requirements.txt`, and `pipeline/tts.py` for the winning provider.

**Test:** Full wake → LLM → TTS → meeting participants hear Operator cycle with the chosen provider.
**Commit:** `feat: switch TTS provider to [provider] — benchmark results in commit body`

---

### Step 7.4 — Tune filler phrase silence threshold

The silence threshold for firing backchannel filler phrases (in `pipeline/conversation.py` or `pipeline/audio.py`) needs tuning. Current behavior: [note current value here before starting]. Goal: fires only when there is actual silence after a direct question to the agent — not during the speaker's natural pauses.

Test with multiple human speech patterns. Adjust the threshold until fillers feel natural. Document the final value and rationale.

**Commit:** `tune: adjust filler phrase silence threshold to N ms — rationale in comment`

---

### Step 7.5 — TTS reliability improvements

After the provider decision in Step 7.3: add retry logic for transient API failures (e.g. 3 retries with exponential backoff) for whichever provider was chosen. Add graceful degradation: if TTS fails after retries, log the error and post the response text to meeting chat as a fallback (requires `send_chat()` to be wired up). Skip this step if Piper was chosen (local — no API failures possible).

**Test:** Simulate API failure (temporarily set an invalid API key). Confirm graceful log + no crash.
**Commit:** `fix: add retry logic and graceful degradation to pipeline/tts.py`

---

### Step 7.6 — STT accuracy review

Review the `WHISPER_HALLUCINATIONS` list in `pipeline/audio.py`. Add any new false-positive patterns discovered during Phase 3 testing.

Evaluate `small` model vs. `base`: run both on 10–20 representative utterances from real meeting audio. Compare WER and latency. If `small` improves accuracy meaningfully without pushing latency past 1.5s, update `config.yaml` default.

**Test:** Wake phrase reliability — "operator" detected correctly; "let's operate on that" not triggered.
**Commit:** `tune: update WHISPER_HALLUCINATIONS filter; [update model if changed]`

---

## Phase 8: Open-Source Packaging

### Step 8.1 — Add `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "operator-agent"
version = "0.1.0"
requires-python = ">=3.11"
description = "An open-source bridge layer that lets any AI agent join any video call as a live participant."
license = {text = "MIT"}
dependencies = [
    "openai", "elevenlabs", "faster-whisper", "playwright",
    "python-dotenv", "numpy", "soundfile", "sounddevice",
    "caldav", "pyyaml",
]

[project.optional-dependencies]
macos = ["rumps", "pyobjc-core", "pyobjc-framework-Cocoa"]

[project.scripts]
operator-setup = "scripts.setup_wizard:main"
operator-run   = "operator.__main__:main"
```

**Test:** `pip install -e .` in a clean venv → no errors.
**Commit:** `feat: add pyproject.toml for pip install`

---

### Step 8.2 — Add `LICENSE`

Create `LICENSE` file with MIT license text. Year: 2026. Copyright holder: [confirm with user].

**Commit:** `chore: add MIT LICENSE`

---

### Step 8.3 — Rewrite `README.md`

Structure:
1. One-line description
2. Quick start (5 steps: prerequisites, install, run wizard, paste meeting link, done)
3. Architecture (three layers — brief, with diagram)
4. Configuration (config.yaml fields)
5. Swapping providers (how to change LLM, TTS, STT)
6. Platform support (macOS, Linux)
7. Contributing

Do NOT include anything from the old README.

**Commit:** `docs: rewrite README.md for open-source audience`

---

## Phase 9: Setup Wizard

### Step 9.1 — Create `scripts/setup_wizard.py`

Interactive CLI wizard. Steps in order:
1. Detect OS (silent — no prompt)
2. Ask: OpenAI API key → validate with a test call → store in `.env`
3. Ask: ElevenLabs API key → validate → store in `.env`
4. Ask: agent name (default: "Operator")
5. Ask: wake phrase (default: "operator") — warn if phrase is unusual
6. Ask: voice selection → list available voices → offer preview → confirm
7. Ask: interaction mode (voice / chat / both)
8. CalDAV setup:
   - Ask: bot's Gmail address (e.g. yourname.operator@gmail.com)
   - Open `https://myaccount.google.com/apppasswords` in the default browser automatically (`webbrowser.open(...)`)
   - Display inline instructions: "1. Sign in if prompted. 2. Under 'Select app', choose 'Other' and name it Operator. 3. Click Generate. 4. Copy the 16-character password."
   - Ask: paste the 16-character app password
   - Validate: attempt a CalDAV connection (`caldav.DAVClient(...)`) — print success or error before proceeding
   - Store credential in system keychain: `keyring.set_password("operator", bot_gmail, app_password)`
   - Print: "Accept meeting invites sent to [bot_gmail] and Operator will join automatically."
9. Ask: Google account for agent? (y/n) — if yes, open browser for one-time login via `scripts/auth_export.py`
10. OS-specific audio setup:
    - macOS: `brew install blackhole-2ch` (silent), write Chrome mic preference to profile JSON
    - Linux: run `scripts/linux_setup.sh`
11. Write `config.yaml` (include `caldav.bot_gmail` field)
12. Print: "Setup complete. Run `python -m operator <meeting-url>` to start."

**Test:** Run from scratch with no `.env` and no `config.yaml`. Complete prompts. Confirm both files created. Run `python -m operator <test-meet-url>` — agent joins and responds to wake phrase.
**Commit:** `feat: add scripts/setup_wizard.py — guided first-run setup`

---

### Step 9.2 — Wire into `pyproject.toml` entry point

`operator-setup` command should call `setup_wizard.main()`. Verify `operator-setup` works after `pip install -e .`.

**Commit:** `feat: wire setup_wizard to operator-setup entry point`

---

## Phase 10: Chat Mode

### Step 10.1 — Add interaction mode to config

`config.yaml` already has `interaction_mode: "voice"`. Ensure `config.py` exposes `INTERACTION_MODE`. Both adapters and the runner should check this value.

**Commit:** `feat: read interaction_mode from config.yaml`

---

### Step 10.2 — Implement chat monitoring in `LinuxAdapter`

In `linux_adapter.py`:
- Add a `monitor_chat()` method that polls the meeting chat panel for new messages containing `@<AGENT_NAME>`
- When found: strip the mention, return the message text
- The runner calls this in chat mode instead of (or in addition to) wake phrase detection

The chat panel ARIA labels are already partially implemented in `send_chat()` — use the same approach to read messages.

**Test:** In a test Meet, type `@operator what's 2+2?` → agent posts `4` (or similar) in chat within 10s.
**Commit:** `feat: implement chat monitoring in LinuxAdapter`

---

### Step 10.3 — Implement same in `MacOSAdapter`

Mirror the `monitor_chat()` implementation in `macos_adapter.py`.

**Test:** Same as 10.2, on macOS.
**Commit:** `feat: implement chat monitoring in MacOSAdapter`

---

## Phase 11: Visual Feedback

### Step 11.1 — Chat acknowledgment

When the agent enters the "thinking" state: call `connector.send_chat("On it...")`. When the response is ready and TTS/chat message has been sent, optionally follow up. Keep it short — this is a signal, not a conversation.

Wire into `pipeline/conversation.py` state transitions (or `pipeline/runner.py`).

**Test:** Trigger wake phrase → "On it..." appears in chat within 1s of wake detection.
**Commit:** `feat: post chat acknowledgment when agent enters thinking state`

---

### Step 11.2 — Emoji reactions

Add `send_reaction(emoji)` to `MeetingConnector` base interface. Implement in both adapters. Fire 🤔 on thinking state, ✅ when response is delivered.

Google Meet reaction button ARIA label: "Send a reaction" — click it, then click the emoji. Test this Playwright interaction manually before wiring into the pipeline.

**Test:** Wake phrase → 🤔 appears within 1s → ✅ appears after response.
**Commit:** `feat: add emoji reactions to MeetingConnector — thinking and done states`

---

## Phase 12: Config Hot-Reload Audit

*Do this after all feature phases so the config's full shape is known. Goal: review every key in `config.yaml`, decide which should hot-reload vs. require a restart, and implement accordingly.*

### Step 12.1 — Classify every config key

Walk `config.yaml` top to bottom. For each key, decide:
- **Hot-reloadable** — safe to re-read from disk on each use. No object is "holding" the value between calls. Examples: `system_prompt`, `wake_phrase`, `conversation_timeout`, filler phrase lists, finalization thresholds.
- **Startup-only** — gates initialization of a client or model. Changing it live would leave a partially-initialized object in an inconsistent state. Examples: `tts.provider`, `stt.model`, `connector.type`, API keys.

Output: annotated list (or inline comments in `config.yaml`) marking each key.

**Commit:** `docs: classify config keys as hot-reloadable vs startup-only`

---

### Step 12.2 — Refactor hot-reloadable keys

For keys classified as hot-reloadable, replace the module-level constant in `config.py` with a function or per-call re-read so a live edit to `config.yaml` takes effect on the next response — no restart needed.

Pattern: instead of `SYSTEM_PROMPT = _config["agent"]["system_prompt"]` at import time, expose `get_system_prompt()` that re-parses on demand (or re-reads the file with a short TTL cache if performance matters).

**Test:** Edit `system_prompt` in `config.yaml` mid-session. Next response should reflect the new value.

**Commit:** `feat: hot-reload config keys that are safe to re-read at call time`

---

### Step 12.3 — Annotate startup-only keys in config.yaml

Add a `# requires restart` comment to every startup-only key so users understand which changes take effect live vs. which require a relaunch.

**Commit:** `docs: annotate startup-only keys in config.yaml`

---

## Key Decisions

- **Meeting detection:** CalDAV polling (1 min interval). App password stored in system keychain. No OAuth, no Cloud Console, no credentials.json. Implemented in Phase 9.
- **Guest join:** Locked default. "Ask to join" — host admits the bot. Authenticated join via `auth_state.json` is opt-in only. Existing connector join logic is unchanged.
- **Demo strategy:** Invite-based, not link-paste. Users cannot just paste an instant meeting link to try the product — Google Meet blocks headless/unauthenticated bots. Instead, we provide the bot's Google account email and the user invites it to their meeting. This is the same model as Otter.ai/Fireflies. A pre-configured "demo bot" account must be running and ready for people to invite.
- **Platform scope:** Google Meet only for v1. Zoom and Teams are v2.

---

## Hard Won Knowledge

**Filler echo after abort causes speaker-change hang.** When the abort mechanism discards a response, the filler audio is still playing through BlackHole. If caption processing resumes before the filler finishes, the filler's "You" captions trigger a speaker-change finalization on the next capture cycle — at a gap shorter than the speculative threshold. Since no speculative thread launches, `spec.ready.wait()` blocks forever. Fix: `finally` block calls `filler_done.wait()` before resuming captions, and `spec.ready.wait()` has a 3s timeout as a safety net.

**`capture_start` timing crack drops late captions.** `capture_next_wake_utterance` sets `capture_start = time.time()` at the top. In follow-up mode, it waits for `_last_update_time > capture_start`. If a caption arrives between the previous capture returning and the new one starting (~100-200ms of classifier/PASS logic), its timestamp is before `capture_start` and the new capture never sees it. Fix: check live `_current_text` before entering new capture cycles (soft PASS text-grew check and abort re-process).

**Google ASR rewrites captions between speculative and finalization.** The same utterance can appear as "What's two plus two" at speculative time and "what's 2 + 2" at finalization — case change, number word↔digit swap, symbol substitution. Exact string matching causes speculative misses on the first question every time. Fix: `_normalize_for_match()` that canonicalizes both sides (digits, symbols, case, punctuation).

**Abort text-grew false positive from wake phrase prefix.** In wake-triggered mode, `prompt` is post-wake extraction ("what's 2 + 2") but `_current_text` includes the full caption region ("Hey operator, what's 2 + 2"). Comparing with `!=` always triggers abort. Fix: use `endswith` — if the normalized current text ends with the normalized prompt, no new content was added.

**Splitting speculative events exposes TTS regression.** When separating `llm_done` from `ready` to unblock the runner earlier, `_finalize_prompt` still checked `speculative.ready.is_set()` for both LLM and TTS resolution. This meant the runner unblocked faster but then started redundant fresh TTS synthesis (speculative TTS was still in-flight). The filler skip logic also broke — `spec_ready` was false (TTS not done), so fillers played unnecessarily. Fix: Step 1 checks `llm_done` for LLM resolution; Step 2 detects in-flight speculative TTS (LLM done + `ready` not set) and waits for it; filler skip adds `spec_tts_inflight` condition.

**Filler echo infinite loop via Google Meet speaker misattribution.** Google Meet nondeterministically attributes filler audio (human-voice clips like "Yeah", "Right") to the previous human speaker's caption bubble instead of creating a new "[You]" bubble. This set `abort_event` in `on_caption_update`, triggering an abort→retry→new filler→misattributed again→abort loop. Each cycle took ~1.2s and played an audible filler, burning LLM tokens with duplicate requests. The misattribution is not timing-dependent — identical gaps between user speech and filler produced correct "[You]" attribution 3/4 times, then failed on the 4th. Root cause is in Google's server-side caption rendering, not our audio routing (mpv explicitly targets BlackHole). Fix: dynamic grace period (`_filler_done_at + 1.0s`), `allow_abort=False` on recursive retry, no filler on retries.

**Filler grace period conflicts with abort mechanism.** The 1.0s filler grace (ignoring non-"You" captions after filler playback) was added to prevent the misattribution loop above. But it also suppresses the abort signal when the user is genuinely still talking after premature finalization. In logs: user says "And what about?" (finalized prematurely), then "Texas" arrives 0.5s later during filler playback — grace period has 0.01s left, abort is suppressed, bot speaks the wrong answer. The filler grace and abort mechanism are fighting each other. Planned fix (session 32): remove filler grace entirely, rely on `is_speaking` + echo guard for echo prevention, widen the abort window to 0.4s.

**Abort path reads stale `_current_text` because `is_speaking` blocks updates.** During `is_speaking`, `on_caption_update` returned early at line 114 without updating `_current_text`. When the abort path in `_finalize_prompt` read `_current_text`, it got the pre-finalization value — missing the late-arriving speech that triggered the abort. E.g., user says "Germany" after a pause, abort fires on "German." caption, but `_current_text` still has "What's the capital of?" from before finalization. Fix: update `_current_text` and `_current_speaker` for non-"You" speakers even during `is_speaking`. Added 0.5s settle delay so partial words settle before reading. Also concatenate `prompt + " " + new_text` so original context isn't lost.

**Google Meet caption punctuation triggers false INCOMPLETE classifications.** Meet inserts sentence-ending punctuation at speech pauses. "How about Belgium" spoken with a brief pause becomes "How about? Belgium." in captions. The INCOMPLETE classifier sees "How about?" which matches the example patterns and returns INCOMPLETE, even though the full utterance is a complete question. Fix: `_strip_mid_punctuation()` removes `.?!` followed by whitespace before sending to classifier, collapsing artificial sentence boundaries into run-on text.

**Google ASR rewrites during `is_speaking` trigger false aborts.** After finalization, Meet sometimes cosmetically rewrites the caption (e.g., "two plus two" → "2 + 2", capitalization changes). The abort mechanism sees a non-"You" caption arrive and fires, causing a redundant LLM round-trip + ~1s latency. The content is semantically identical. Planned fix: normalize both finalized prompt and incoming caption at the abort trigger point (captions.py line 110) and only fire `abort_event` if content actually differs.

**Log string truncation mimics data bugs.** `utterance[:80]` in INFO-level logging truncated the displayed prompt mid-word (e.g., "Now, what about F"), making it look like the LLM received incomplete input. Actual API calls were unaffected — full prompts were sent. Cost several hours of investigation across sessions before being identified as display-only. Fix: metadata at INFO (`prompt_chars=N`), full payload at DEBUG only.

**Abort re-fire duplicates prompt via `_current_text` concatenation.** The abort path in `_finalize_prompt` concatenated `prompt + " " + new_text` where `new_text` was `self.captions._current_text` — the full live caption text, not a delta. If original prompt was "What is two plus two" and `_current_text` grew to "What is two plus two and three plus three", the re-fired prompt became "What is two plus two What is two plus two and three plus three". Fix: use `_current_text` directly as the updated prompt (it already contains the full utterance).

**Google Meet nondeterministically attributes bot TTS to the human speaker.** During `is_speaking`, the bot's TTS plays through BlackHole → meeting mic. Google sometimes captions this audio as the previous human speaker (e.g., `[Jojo Shapiro] Yep. 12 Right.`) instead of `[You]`. This overwrites `_current_text` with echo garbage, poisoning the abort re-fire path. The misattribution is transient and not reproducible — same audio routing produces correct `[You]` labels most of the time. Fix: two-layer defense — (1) continuity guard rejects updates where normalized text doesn't extend the previous value (catches discontinuous echo blocks), (2) echo fingerprinting compares incoming caption text against `_tts_text` and suppresses false aborts on match.

**Stored character position invalidated by ASR corrections produces garbled prompts.** `_wake_position` stored the char index where "operator" ended at detection time. Google Meet's ASR then rewrote the caption text (changed punctuation, capitalization, added commas) — shifting characters so the stored position now pointed into the middle of a word. E.g., "Hey Operator." → "Hey, Operator, " added a comma after "Hey", shifting everything by +1 char. `_extract_prompt()` sliced at the stale position and produced "r, can you hear me" instead of the intended text. Fix: removed `_wake_position` entirely — send the full caption node text as the prompt. The LLM can handle "hey operator" appearing in the text.

**Daemon classifier threads accumulate as zombies across prompt cycles.** Each `_finalize_prompt` spawned a daemon thread that called `abort_event.wait()` to watch for playback interruptions. When playback completed normally (no abort), the thread sat blocked on `.wait()` forever. Since `abort_event` is shared across all cycles, the next time any caption set it (even in a completely different prompt cycle), ALL accumulated zombie threads woke up simultaneously — firing 3+ parallel classify calls with different `reply` values from their respective closures. Fix: added a `playback_done` event; the thread polls both `abort_event` and `playback_done` with 50ms timeouts, exiting cleanly when playback finishes.

**Playback interrupt classifier reads stale prompt text instead of triggering caption.** When a non-"You" caption fires `abort_event` during playback, the classifier thread reads `_current_text` to find out what was said. But if the echo-suspect continuity check rejects the caption (discontinuous text), it skips updating `_current_text` — so the classifier reads the old prompt from before playback started. E.g., caption "Cool." triggers abort, continuity check rejects it (not a continuation of "Hey operator, what's 2 + 2?"), but `_current_text` still holds the old prompt and the classifier asks "is 'Hey operator, what's 2 + 2?' an interruption?" instead of "is 'Cool.' an interruption?". Fix: added `_abort_text` field — set to the actual triggering caption at abort time, updated on continuity-passing captions, read by classifier instead of `_current_text`. Also moved `_abort_speaker`/`_abort_text` assignments before `abort_event.set()` to prevent a race where the classifier thread wakes before values are written.

**Caption hallucinations and background noise trigger false playback aborts.** Google Meet's captioner occasionally produces phantom short words ("What?", "Yeah.", "Oh.") attributed to a participant, from either background noise or ASR hallucination. These look structurally identical to real captions — no confidence score, no styling difference, no DOM attribute distinguishing them. The abort mechanism treated any non-"You" caption during playback as a real interruption and killed mpv. Fix: gate playback interruptions through stream classification — the LLM decides whether the caption text looks like a deliberate interruption or noise/hallucination.

**INCOMPLETE race condition loses speech that arrives during classifier LLM call.** User says "What's the capital of..." [0.7s pause] "...France?" — finalized at "capital of?", classifier fires (~1.3s round-trip), "France" arrives via caption 690ms later but classifier is already in-flight with stale text. Classifier returns INCOMPLETE (correct for what it saw). New capture cycle starts but "France" was already consumed by the previous cycle. User experiences silence and has to repeat. Root cause: blocking classifier creates a ~1.3s blind window where caption growth is invisible. Planned fix: streaming first-token classification eliminates the blocking window entirely.

---

## Open Questions

1. **Audio quality root cause** — QEMU ruled out (tested native AMD64, March 2026 — still choppy). Root cause is sample rate mismatch in TTS → PulseAudio → Chrome → WebRTC chain. Audit in Phase 7.2.
2. **Wake phrase customization** — allow users to set their own wake phrase in `config.yaml`? Test Whisper reliability on custom phrases before committing.
3. ~~**Calendar auto-join**~~ — **Resolved.** CalDAV polling (1 min interval) implemented in Phase 9. Bot's Gmail receives invites; user accepts on bot's behalf; Operator polls and auto-joins.
4. **Linux distro coverage** — Ubuntu/Debian tier-1; PulseAudio vs. PipeWire (Fedora, Ubuntu 22.04+) needs separate validation path.
5. ~~**Calendar secrets in cloud**~~ — **Moot.** CalDAV uses only a Gmail app password stored in system keychain. No `credentials.json`, no `token.json`, no OAuth app.
