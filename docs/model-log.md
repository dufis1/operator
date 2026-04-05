# Model Log Reference

Last updated: 2026-04-04
Captured from: macOS headless, Kokoro TTS, Whisper base model (audio mode) / Meet captions (caption mode)

This is the gold-standard reference for what Operator's logs should look like during
a healthy session. When debugging issues, compare current logs against this file
section by section — missing or out-of-order lines indicate where things went wrong.

**How to use:** `grep "STARTUP\|TIMING\|State →\|wake_\|prompt_finalized\|Filler\|Pipeline\|Echo\|Utterance:\|Entering\|conversation" /tmp/operator.log`

---

## Section 1: Startup Sequence

Everything from launch to "listening for wake phrase". If any line is missing,
the component it names failed to initialize.

**Audio mode** (connector.type: audio):
```
STARTUP begin
STARTUP mode=audio (ScreenCaptureKit + Whisper)
STARTUP loading Whisper model...
STARTUP STT provider=mlx model=base             # provider and model from config.yaml
STARTUP Whisper model loaded                   # ~0.4s on Apple Silicon
STARTUP connecting to APIs...                  # OpenAI client init
STARTUP initializing TTS (background)...       # TTS init runs in background thread
STARTUP joining meeting <url>                  # only if meeting_url provided
MacOSAdapter: joining <url>                    # connector-level join
STARTUP TTS ready (background)                 # Kokoro load finished (overlaps with browser)
MacOSAdapter: Swift helper launched            # audio capture binary started
AgentRunner: audio capture started
State → idle (Listening for 'operator'...)
STARTUP complete — idle, listening for wake phrase
```

**Caption mode** (connector.type: auto or meet-captions):
```
STARTUP begin
STARTUP mode=captions (DOM-based, no Whisper)   # no Whisper model load
STARTUP connecting to APIs...                   # OpenAI client init
STARTUP initializing TTS (background)...       # TTS init runs in background thread
STARTUP joining meeting <url>
CaptionsAdapter: joining <url>                  # caption connector join
TIMING browser_launch=0.9s                     # Chromium cold start
TIMING navigation=0.8s                         # page.goto to Meet URL
TIMING pre_join_ready=0.5s                     # wait for pre-join DOM elements
TIMING detect_page_state=0.0s (state=pre_join) # auth/session state check
TIMING tts_kokoro_import=3.0s                  # background: from kokoro import KPipeline
CaptionsAdapter: camera turned off              # or "camera already off"
TIMING camera_toggle=0.5s                      # wait + click camera button
CaptionsAdapter: clicked 'Join now'             # or 'Ask to join' or 'Switch here'
TIMING join_click=0.1s (Join now)              # join button race
CaptionsAdapter: joined meeting successfully
TIMING tts_kokoro_pipeline=2.2s                # background: KPipeline instantiation
STARTUP Kokoro TTS ready (voice=af_heart) total=5.1s  # background TTS init done
STARTUP TTS ready (background)
TIMING in_meeting_wait=0.5s                    # wait for "Leave call" button
TIMING mic_check=0.1s                          # mic state race
CaptionsAdapter: captions enabled via Shift+C   # or "already enabled (pre-check)" or "via button fallback"
TIMING captions_enable=0.5s                    # Shift+C + state detection
TIMING caption_observer_inject=0.0s            # JS evaluate (instant)
CaptionsAdapter: caption observer injected      # MutationObserver scoped to caption region
STARTUP caption processing active
LatencyProbe: input device = 'MacBook Pro Microphone'  # system default mic; must NOT be Display Audio or BlackHole
LatencyProbe: started
State → idle (Listening for 'operator'...)
STARTUP complete — idle, listening for wake phrase
```

**Join failure / session recovery lines** (only appear when join has problems):
```
# Page state detection (after event-driven wait for pre-join elements)
session: 'can't join' but no Google session cookie — treating as logged_out  # auth failure, not host controls
session: detected 'can't join' state (authenticated — likely host controls)  # genuine host block

# Session expired — cookie recovery attempted
MacOSAdapter: session expired — attempting cookie recovery
session: auth_state.json valid (N cookies)            # auth_state loaded OK
session: injected N .google.com cookies               # cookies injected into context
MacOSAdapter: session recovered via cookie injection   # recovery succeeded
STARTUP session recovered via cookie injection — consider re-running scripts/auth_export.py

# Session expired — recovery failed
session: cannot load auth state from ./auth_state.json: ...  # file missing or invalid
MacOSAdapter: no valid auth_state for recovery
STARTUP join failed: session_expired
Re-export session: python scripts/auth_export.py       # action for user

# Other join failures
STARTUP join timed out (660s)                          # browser thread didn't signal in time (value = ADMISSION_TIMEOUT_SECONDS + 60)
STARTUP join failed: cant_join                         # "You can't join this video call" (authenticated — host controls)
❌ Not authenticated — run this to sign in:             # printed to stdout (not log) for visibility
   python scripts/auth_export.py
STARTUP join failed: no_join_button                    # pre-join screen but no button found
STARTUP join failed: admission_timeout                 # waited in lobby for ADMISSION_TIMEOUT_SECONDS, never admitted

# Multiple instances / SingletonLock
CaptionsAdapter: removed stale SingletonLock           # previous session crashed; lock was dead — removed automatically
CaptionsAdapter: another Operator session is already running — stop that session before starting a new one  # live lock detected; second instance exits
⚠️  Another Operator session is already running.        # printed to stdout for visibility
   Stop that session before starting a new one.

# Waiting room (when 'Ask to join' is clicked and host approval is required)
CaptionsAdapter: waiting for lobby screen to appear...                                   # phase 1: confirming lobby loaded
CaptionsAdapter: lobby confirmed — watching for host to admit us (timeout=600s)          # lobby detected; event-driven watch active
CaptionsAdapter: still in waiting room (Ns elapsed)                                      # heartbeat every 30s
CaptionsAdapter: admitted — lobby screen gone (event-driven, waited N.Ns total)          # host clicked 'Let in'; N.N = wait time
CaptionsAdapter: admission timeout after 600s                                            # gave up; triggers admission_timeout failure
CaptionsAdapter: lobby screen not detected after N.Ns — assuming already admitted        # lobby never appeared; proceeding optimistically
CaptionsAdapter: admission wait cancelled (leave called after Ns)                        # leave() called while waiting

# In-meeting health check (every 5 min in hold loop)
MacOSAdapter: health check — unexpected URL: ...       # navigated away from meet.google.com

# Meet system phrases (exit signals — currently logged then filtered, not yet acted on)
CaptionsAdapter: system phrase detected — 'No one else is in this meeting'  # everyone left naturally
CaptionsAdapter: system phrase detected — 'Returning to home screen'        # host ended meeting for everyone
CaptionsAdapter: system phrase detected — 'You left the meeting'            # Operator itself was kicked/left
```

**Diagnostic lines** (appear during startup in caption mode — normal, not errors):
```
CaptionsAdapter: JS diagnostic — observer_attached label=scoped_region    # observer wired to caption region
CaptionsAdapter: JS diagnostic — observer_attached label=scoped_region_polled  # found after polling (slight delay)
CaptionsAdapter: JS diagnostic — observer_attached label=body_fallback    # caption region not found — selector mismatch
CaptionsAdapter: JS diagnostic — mutation_count=10                        # heartbeat: observer is firing
```
- `body_fallback` → caption region selector needs updating; check `debug/in_meeting.html`
- No `mutation_count` lines while someone is speaking → MutationObserver not firing; check headless mode

**What to check if startup fails:**
- Missing "Whisper model loaded" → model download failed or wrong compute_type
- Missing "Kokoro TTS ready" → Kokoro not installed, will fall back to macos_say
- Missing "audio capture started" → Swift helper failed to launch (check Screen Recording permissions)
- "audio_capture has unexpected identity" → binary re-signed with wrong identifier, re-run codesign
- "audio_capture has no valid signature" → binary not signed at all, run codesign
- "Screen Recording permission denied" (exit 3) → permission genuinely not granted, grant in System Settings
- "audio capture hung" / "resetting TCC cache" (exit 4) → TCC daemon stale, auto-reset + retry in progress
- "audio capture hung after TCC reset" (exit 4 after retry) → restart Operator, or restart Mac as last resort
- "join failed: session_expired" → Google revoked session cookies, or browser profile never authenticated. Run `python scripts/auth_export.py`
- "can't join but no Google session cookie — treating as logged_out" → bot saw "can't join" but has no auth cookies, so it's an auth problem not host controls
- "join timed out" → browser thread hung or crashed. Check debug/ for screenshots

**At DEBUG level you'll also see** (not required for healthy operation, but useful for diagnosing capture issues):
```
AgentRunner: audio_capture signature OK — ...     # pre-launch codesign verification
[capture] audio_capture: starting
[capture] audio_capture: parent process: <app> (<bundle-id>, pid=N)  # or "parent pid=N (not an NSRunningApplication)"
[capture] audio_capture: Screen Recording permission OK
[capture] audio_capture: requesting shareable content...
[capture] audio_capture: displays=1, windows=N, apps=N
[capture] audio_capture: using display 1 (WxH)
[capture] audio_capture: calling startCapture...
[capture] audio_capture: capture started — streaming until stdin closes
[capture] audio_capture: callback #1 — wrote 1280 bytes
```

---

## Section 2: Ambient Listening

After startup, Operator captures ambient audio and transcribes it to build meeting
context. Each utterance follows this pattern:

```
TIMING ambient_capture_start                    # listening begins
TIMING ambient_speech_first rms=0.0XXX          # voice detected (RMS above 0.02 threshold)
TIMING ambient_silence_detected rms=0.0XXX      # first silent check after speech
TIMING ambient_utterance_done speech=N.NNs silence=N.NNs  # speech duration + silence wait
TIMING ambient_whisper_start
TIMING ambient_whisper_done "<transcribed text>" # Whisper result
Utterance: <text>                               # stored in rolling transcript
TIMING ambient_capture_start                    # next cycle begins immediately
```

**What to check:**
- Empty whisper_done ("") → VAD filtered everything out, normal for brief noises
- "Ignoring hallucination: <text>" → Whisper produced a known false positive (e.g. "thank you" on silence)
- `TIMING ambient_whisper_rejected_repetition` → Whisper produced pathological repetition (e.g. "I know" ×100), rejected as hallucination from audio feedback loop
- Very high RMS on speech_first (>0.5) → possible audio clipping or feedback
- No ambient_speech_first for a long time → audio capture may have stalled

---

## Section 3a: Wake Phrase — Inline

When someone says "operator" followed by a question in the same utterance:

```
TIMING ambient_whisper_done "Operator, what time is it?"
TIMING wake_inline prompt=" what time is it"    # trailing text extracted as prompt
State → listening (Listening for prompt...)
TIMING waiting for TTS init...                   # only if Kokoro still loading (rare — overlaps with browser)
TIMING prompt_finalized " what time is it"      # prompt sent to pipeline
State → thinking (Thinking...)
```

Then continues to Section 4 (LLM + TTS).

---

## Section 3b: Wake Phrase — Wake-Only

When someone says just "operator" with no trailing prompt:

```
TIMING ambient_whisper_done "Operator"
TIMING wake_only waiting_for_prompt             # wake phrase detected, no trailing text
State → listening (Listening for prompt...)
Operator says: "yeah" (acknowledgment)          # random ack clip played
TIMING ack_done
TIMING prompt_capture_start                     # now listening for the actual question
TIMING prompt_speech_first rms=0.0XXX
TIMING prompt_silence_detected rms=0.0XXX       # first silent check after speech
TIMING prompt_utterance_done speech=N.NNs silence=N.NNs
TIMING prompt_whisper_start
TIMING prompt_whisper_done "<question>"
TIMING prompt_finalized "<question>"
State → thinking (Thinking...)
```

Then continues to Section 4 (LLM + TTS).

**What to check:**
- "Prompt empty after wake phrase" → user said "operator" but then nothing — returns to idle
- No prompt_speech_first after ack_done → mic capture may have stalled during ack playback

---

## Section 4: LLM + TTS Response

After a prompt is finalized, the pipeline calls LLM and synthesizes speech:

```
Echo prevention: paused caption processing      # caption mode: ignore captions while bot speaks
TIMING filler_play_start clip=filler_NN.mp3 bucket=<neutral|...>  # filler starts immediately at finalization
LLM ask model=gpt-4.1-mini history_turns=N utterance="..."        # logged before API call
LLM reply="..."                                 # logged on successful response
TIMING caption_speculative_llm_done reply="..."  # speculative LLM result
TIMING caption_speculative_tts_start             # speculative TTS synthesis begins (overlaps finalization wait)
TIMING tts_synth_done (N.NNs)                   # typical Kokoro: 0.5-1.5s (logged inside tts.py)
TIMING caption_speculative_tts_done bytes=N      # speculative TTS cached WAV ready
TIMING llm_speculative_hit waited=N.NNs reply="..."  # speculative result used (waited=0.00s if already done)
State → speaking (Speaking...)
TIMING tts_speculative_hit bytes=N               # cached WAV used, synthesis skipped (0.00s)
TIMING filler_play_done                         # filler finishes (concurrent with LLM + TTS)
TIMING response_play_start
TTS play_audio: N bytes → device=coreaudio/BlackHole2ch_UID  # logged before mpv launch
TTS play_audio: done                            # logged after mpv exits cleanly
TIMING response_play_done elapsed=N.NNs
TIMING end_to_end — llm_wait: N.NNs | synthesis: N.NNs | filler_wait: N.NNs | speak: N.NNs | total_from_finalized: N.NNs
Echo prevention: resumed caption processing      # caption mode
State → idle (Listening for 'operator'...)
```

**LLM resolution variants** (one of these appears per interaction):
- `TIMING llm_speculative_hit waited=0.00s reply="..."` — speculative done before finalization, used immediately
- `TIMING llm_speculative_hit waited=N.NNs reply="..."` — speculative still in-flight at finalization, waited for it (includes speculative TTS time)
- `TIMING llm_speculative_miss reason=<transcript_mismatch|no_reply> waited=N.NNs` — speculative failed or mismatched; fresh call follows
- `TIMING llm_request_sent` + `TIMING llm_response_received elapsed=N.NNs reply="..."` — fresh call (no speculative, or after miss)

**TTS resolution variants** (one of these appears per interaction):
- `TIMING tts_speculative_hit bytes=N` — speculative TTS cached audio used, synthesis skipped (0.00s)
- `TIMING tts_synthesis_start` + `TIMING tts_synthesis_done elapsed=N.NNs` — fresh synthesis (speculative miss or no speculative)

**What to check:**
- `LLM ask` present but no `LLM reply` → API call hung or raised; check for `LLM API call failed` error below it
- Missing `TIMING response_play_start` → synthesis returned empty bytes; check for `Synthesis error` above
- `TTS play_audio: mpv exited with code N` → mpv failed; likely wrong audio device string or mpv not installed
- "Synthesis error: ..." → TTS provider failed
- Very long tts_synthesis_done (>3s) → TTS provider slow, consider switching
- `end_to_end total_from_finalized` >5s → investigate which stage is slow (llm_wait vs synthesis vs speak)
- `Filler: no clips for bucket=...` → filler asset missing for that category; check assets/fillers/

---

## Section 5: Conversation Mode

After responding, Operator stays in conversation mode for 20 seconds, accepting
follow-up questions without requiring "operator" again:

```
Entering conversation mode
State → listening (Listening...)
TIMING prompt_capture_start
TIMING prompt_speech_first rms=0.0XXX           # follow-up detected
TIMING prompt_silence_detected rms=0.0XXX       # first silent check after speech
TIMING prompt_utterance_done speech=N.NNs silence=N.NNs
TIMING prompt_whisper_start
TIMING prompt_whisper_done "<follow-up>"
TIMING prompt_finalized "<follow-up>"
State → thinking (Thinking...)
```

Then repeats Section 4 (LLM + TTS), then loops back to listen for more follow-ups.

**Conversation timeout** — if no speech within 20 seconds:
```
TIMING prompt_timeout (no speech in 20s)
Conversation mode: no follow-up — returning to idle
State → idle (Listening for 'operator'...)
TIMING ambient_capture_start                    # back to ambient listening
```

---

## Section 2b: Caption Mode — Wake Detection + Prompt Capture

In caption mode, Sections 2/3a/3b are replaced by a single flow. Wake detection
happens in real-time on every DOM update (~330ms), not after full utterance transcription.

```
TIMING caption_capture_start (timeout=None require_wake=True)   # initial wake listen
caption: [Alice] Hey operator what is the plan  [bridge_lag=Nms]   # raw caption + JS→Python bridge lag
TIMING caption_wake_detected speaker=Alice prompt_so_far="what is the plan"  # wake found mid-speech
TIMING caption_wake_confirmed — entering silence detection
TIMING caption_speculative_fire gap=1.04s prompt="what is the plan"  # speculative LLM at 1.0s of silence
TIMING caption_speculative_llm_start prompt="what is the plan"
TIMING caption_speculative_llm_done reply="<LLM reply>"
TIMING caption_finalized reason=silence gap=1.56s speaker=Alice prompt="what is the plan"
TIMING caption_prompt_finalized speaker=Alice prompt="what is the plan"
TIMING wake_caption speaker=Alice prompt="what is the plan"
State → listening (Listening for prompt...)
TIMING prompt_finalized "what is the plan"       # enters standard Section 4 (LLM + TTS)
State → thinking (Thinking...)
```

**ASR correction edge case** (wake phrase corrected away):
```
TIMING caption_wake_detected speaker=Bob prompt_so_far=""
TIMING caption_wake_retracted (ASR correction removed wake phrase)  # "hey operator" → "hey operate on"
TIMING caption_wake_lost — returning to wake detection
```

**Speaker change finalization:**
```
caption: speaker change Alice -> Bob
TIMING caption_finalized reason=speaker_change ...  # previous speaker's prompt finalized early
```

**At DEBUG level** — dropped captions during TTS playback:
```
caption: dropped while speaking [You] On it. 2 + 2 = 4.    # is_speaking=True gate active; normal during response
```

**What to check:**
- No `caption_wake_detected` when someone says "hey operator" → check caption observer injection, check captions are enabled
- `caption_wake_retracted` frequently → ASR is unstable for "hey operator", may need wake phrase tuning
- `caption_speculative_fire` gap >> 1.0s → DOM updates stalled, check Playwright event loop
- No `caption:` lines at all → caption callback not wired, check adapter/processor connection
- Caption finalization hangs (no `caption_finalized` after user stops talking) → check if all captions showing `[You]` speaker label; Meet occasionally relabels user speech — is_speaking gate and wake anchoring handle this correctly

---

## Section 5b: Caption Mode — Conversation Mode

Follow-up utterances don't require "hey operator". The speculative LLM call doubles
as a classifier: PASS instruction appended to prompt, model returns "PASS" if not
addressed, otherwise responds normally.

```
Entering conversation mode
State → listening (Listening...)
TIMING caption_capture_start (timeout=20 require_wake=False)     # follow-up capture (20s = CONVERSATION_TIMEOUT)
TIMING caption_followup_started — entering silence detection
TIMING caption_speculative_fire gap=1.03s prompt="now triple it"
TIMING caption_speculative_llm_start prompt="now triple it"
TIMING caption_speculative_llm_done reply="That would be 12."
TIMING caption_combined_classify for_assistant=True              # staying in conversation
TIMING caption_prompt_finalized speaker=Alice prompt="now triple it"
...                                                              # responds, loops
```

**Classifier prompt includes last exchange context** — the speculative LLM call in follow-up
mode includes `[Your last exchange]` (raw utterance + reply) and a meeting-aware instruction
asking the model to decide if the new utterance is a follow-up or the speaker moving on.

**Conversation ends — two-strike PASS system:**

First PASS is "soft" — stays listening. Second consecutive PASS exits for real.
If finalized text grew beyond speculative snapshot (word delta > 2), re-classifies
on full text before committing to soft PASS.

```
TIMING caption_combined_classify for_assistant=False
Conversation mode: soft PASS — staying in conversation mode    # first strike: stay listening
```

Re-classify path (finalized text grew beyond speculative snapshot):
```
Conversation mode: soft PASS re-classify (spec=N final=M delta=D)  # word count grew
TIMING reclassify_full_text result=True/False reply="..."          # classify-only LLM call
Conversation mode: re-classify flipped PASS→RESPOND                # re-classify overturned PASS
```

Second strike (two consecutive PASSes):
```
TIMING caption_combined_classify for_assistant=False
Conversation mode: second PASS — returning to idle               # hard exit
State → idle (Listening for 'operator'...)
```

Soft PASS recovery (someone follows up after soft PASS):
```
TIMING caption_combined_classify for_assistant=True              # second-strike classifier says RESPOND
...                                                              # responds normally, soft_pass resets
```

**Conversation timeout** — no captions within 20 seconds:
```
TIMING caption_timeout (no captions in 20s)
Conversation mode: capture ended — returning to idle
State → idle (Listening for 'operator'...)
```

**Capture ended** — if connector stops (e.g. meeting left):
```
Conversation mode: capture ended — returning to idle
```

---

## Section 5b: Perceived Latency Probe (caption mode only)

Interspersed with caption and pipeline events. These measure the gap between acoustic speech end and pipeline events.

```
TIMING perceived_speech_start                                   # mic RMS crosses threshold — user started talking
TIMING perceived_acoustic_silence_end speech_duration=N.NNs peak_rms=N.NNNN  # mic went quiet (sustained 600ms)
```

**Normal pattern per interaction:**
- One `perceived_speech_start` as user begins speaking
- One `perceived_acoustic_silence_end` after user finishes — appears BEFORE `caption_prompt_finalized`
- No perceived events during filler or response (probe gated off at `filler_play_start`, 500ms warmup on resume)

**Disabled probe** (diagnostics.latency_probe: false in config.yaml):
```
LatencyProbe: disabled via config
```

**Key derived metrics** (computed by `scripts/parse_latency.py`):
- ASR delay = `caption_prompt_finalized` − `perceived_acoustic_silence_end` (typically 0.5–1.5s)
- Dead air to filler = `filler_play_start` − `perceived_acoustic_silence_end`
- Dead air to response = `response_play_start` − `perceived_acoustic_silence_end`
- Parse script anchors cycles on `caption_wake_confirmed`, not ambient silences — multi-participant safe
- Gate-leak cycles (filler started before acoustic silence logged) shown as `LEAK(N.NN)`, excluded from averages

**What to check:**
- No `perceived_*` events at all → check `LatencyProbe: input device` — must be real mic, not Display Audio or BlackHole; or `latency_probe: false` in config
- Events fire during bot response → gate not closing; check `set_active(False)` at filler_play_start in runner.py
- Multiple start/end events per utterance → between-word pauses exceeding 600ms; increase `_SILENCE_HOLD_BLOCKS`
- Only `perceived_speech_start`, no `perceived_acoustic_silence_end` → ambient noise above threshold (0.03); may need tuning
- `peak_rms` near 0.03 → user's voice barely above threshold; consider lowering to 0.025

---

## Section 6: Shutdown

Clean shutdown (Ctrl+C, SIGTERM, or leave()):

**Audio mode:**
```
Interrupted — leaving meeting                     # or "Received signal 15 — shutting down"
AgentRunner: audio read loop ended
AgentRunner: transcription loop ended
MacOSAdapter: left meeting
MacOSAdapter: browser closed
```

**Caption mode (terminal — `python __main__.py`):**
```
Received signal 2 — shutting down                 # SIGINT handler fires on main thread
CalendarPoller: stopped
CaptionsAdapter: waiting for browser to close...
CaptionsAdapter: navigated away — left meeting cleanly   # page.goto("about:blank") triggers Meet's leave signal
CaptionsAdapter: browser closed
CaptionsAdapter: left meeting
AgentRunner: caption loop ended
```

**Caption mode — inactivity exit (no Ctrl+C):**
```
CaptionsAdapter: no captions for 600s — leaving meeting    # idle_timeout_seconds elapsed since last caption
CaptionsAdapter: navigated away — left meeting cleanly
CaptionsAdapter: browser closed
AgentRunner: caption loop ended
CaptionsAdapter: left meeting
```

---

## Typical Timing Baselines (macOS, March 2026)

| Stage | Typical | Concern if |
|-------|---------|------------|
| Whisper model load | ~0.4s | >2s |
| Kokoro TTS init | ~3s | >8s |
| Speech duration | varies | >10s (hard cap) |
| Silence wait (post-speech) | 1.0-1.5s | >2s (threshold or noise floor issue) |
| Whisper transcription | ~0.1-0.5s | >1s |
| LLM response | 0.8-1.7s | >3s |
| Kokoro synthesis | 0.5-1.5s (0.0s on speculative hit) | >3s |
| Full pipeline (prompt→done) | 3.5-7s | >12s |

---

## Quick Diff Commands

```bash
# Extract just the key events (INFO level, pipeline markers)
grep "STARTUP\|TIMING\|State →\|wake_\|prompt_finalized\|Filler\|Pipeline\|Echo\|Utterance:\|Entering\|conversation\|caption" /tmp/operator.log

# Startup only
grep "STARTUP" /tmp/operator.log

# Single interaction cycle
grep "TIMING\|State →\|Pipeline\|Echo" /tmp/operator.log

# Caption mode only
grep "caption\|Caption" /tmp/operator.log

# Timing numbers only
grep "TIMING\|Pipeline timing" /tmp/operator.log
```
