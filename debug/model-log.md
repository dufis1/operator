# Model Log Reference

Last updated: 2026-04-07 (session 56)
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
STARTUP warming LLM connection (background)... # warmup thread fires 1-token dummy request
STARTUP joining meeting <url>                  # only if meeting_url provided
MacOSAdapter: joining <url>                    # connector-level join
LLM warmup complete                            # TCP/TLS connection pool established (~1.3s after startup)
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
STARTUP warming LLM connection (background)... # warmup thread fires 1-token dummy request
STARTUP joining meeting <url>
CaptionsAdapter: joining <url>                  # caption connector join
TIMING browser_launch=0.9s                     # Chromium cold start
LLM warmup complete                            # TCP/TLS pool established (fires ~1.3s after startup, during browser launch)
TIMING navigation=0.8s                         # page.goto to Meet URL
TIMING pre_join_ready=0.5s                     # wait for pre-join DOM elements
TIMING detect_page_state=0.0s (state=pre_join) # auth/session state check
TIMING tts_kokoro_import=3.0s                  # background: from kokoro import KPipeline
session: screenshot saved to debug/pre_camera_toggle.png  # pre-toggle snapshot
session: HTML saved to debug/pre_camera_toggle.html
CaptionsAdapter: clicked 'Turn off camera'       # click camera toggle
CaptionsAdapter: camera confirmed off (data-is-muted=true)  # DOM confirmation
TIMING camera_toggle=0.5s                      # wait + click + confirm camera button
CaptionsAdapter: waiting for 'Jojo Shapiro' on pre-join screen...   # only if user_display_name configured
CaptionsAdapter: 'Jojo Shapiro' detected in call — joining          # user found on pre-join screen
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
STARTUP join failed: admission_cancelled               # Ctrl+C or leave() called while waiting in lobby
STARTUP join failed: user_not_present                  # user_display_name not found on pre-join screen (leave called while waiting)

# Multiple instances / SingletonLock
CaptionsAdapter: removed stale SingletonLock           # previous session crashed; lock was dead — removed automatically
CaptionsAdapter: another Operator session is already running — stop that session before starting a new one  # live lock detected; second instance exits
⚠️  Another Operator session is already running.        # printed to stdout for visibility
   Stop that session before starting a new one.

# Waiting room (when 'Ask to join' is clicked and host approval is required)
# All three adapters (CaptionsAdapter, MacOSAdapter, LinuxAdapter) use the same pattern:
{Adapter}: waiting for lobby screen to appear...                                         # phase 1: confirming lobby loaded
{Adapter}: lobby confirmed — watching for host to admit us (timeout=600s)                # lobby detected; event-driven watch active
{Adapter}: still in waiting room (Ns elapsed)                                            # heartbeat every 30s
{Adapter}: admitted — lobby screen gone (event-driven, waited N.Ns total)                # host clicked 'Let in'; N.N = wait time
{Adapter}: admission timeout after Ns                                                    # gave up; triggers admission_timeout failure
{Adapter}: lobby screen not detected after N.Ns — assuming already admitted              # lobby never appeared; proceeding optimistically
{Adapter}: admission wait cancelled (leave called after Ns)                              # leave() called while waiting
{Adapter}: browser closed during admission wait — aborting                               # browser torn down while in lobby

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

After a prompt is finalized, the pipeline streams the LLM response and synthesizes speech.

**Streaming path** (caption mode with `stream_classify=True` — both wake and conversation mode):

The first token from the streaming LLM decides the action: PASS (suppress), EXIT (respond then exit conversation), or anything else (it's the response).

```
Echo prevention: paused caption processing      # caption mode: ignore captions while bot speaks
TIMING llm_stream_start                          # streaming LLM call begins
LLM ask_stream model=gpt-4.1-mini history_msgs=N prompt_chars=N  # logged before stream
TIMING llm_first_token elapsed=N.NNNs token="..."  # first non-whitespace token (~300ms typical)
```

**If PASS** (not for operator — suppress everything, ~300ms total):
```
TIMING llm_classified=PASS — not for operator    # first token was "PASS"
Echo prevention: resumed caption processing
```

**If EXIT** (wrap-up — respond then exit conversation):
```
TIMING llm_classified=EXIT — wrap-up response    # first token was "EXIT", prefix stripped from response
TIMING filler_play_start clip=filler_NN.mp3 bucket=<neutral|...>  # filler starts after classification
...                                              # same as normal response below
```

**If response** (normal — first token is NOT PASS/EXIT):
```
TIMING filler_play_start clip=filler_NN.mp3 bucket=<neutral|...>  # filler starts after first token confirms response
TIMING llm_stream_done elapsed=N.NNNs reply="..."   # all tokens collected
TIMING llm_resolved elapsed_from_finalized=N.NNNs   # wall-clock from finalization to LLM done
State → speaking (Speaking...)
TIMING tts_synthesis_start
TIMING tts_synth_done (N.NNs)                    # typical Kokoro: 0.5-1.5s
TIMING tts_synthesis_done elapsed=N.NNNs
TIMING tts_resolved elapsed_from_finalized=N.NNNs
TIMING filler_play_done                          # filler finishes (concurrent with LLM + TTS)
TIMING filler_wait_done elapsed=N.NNNs
TIMING response_play_start gap_since_filler_done=N.NNNs
TTS play_audio: N bytes → device=coreaudio/BlackHole2ch_UID
TIMING mpv_spawned elapsed=N.NNNs
TIMING mpv_audio_piped elapsed=N.NNNs
TTS play_audio: done
TIMING response_play_done elapsed=N.NNNs
TIMING end_to_end — llm_wait: N.NNNs | synthesis: N.NNNs | filler_wait: N.NNNs | speak: N.NNNs | total_from_finalized: N.NNNs
Echo prevention: resumed caption processing
State → idle (Listening for 'operator'...)
```

**Non-streaming path** (audio/transcription mode — filler first, then blocking LLM):
```
Echo prevention: paused audio ingestion
TIMING filler_play_start clip=filler_NN.mp3 bucket=<neutral|...>
LLM ask model=gpt-4.1-mini history_msgs=N prompt_chars=N
TIMING llm_request_sent
TIMING llm_response_received elapsed=N.NNNs reply="..."
...                                              # same synthesis + playback as above
Echo prevention: resumed audio ingestion
```

**Echo diagnostics** (every caption during `is_speaking`, added session 37):
```
DIAG echo_caption speaker="<name>" you=<True|False> text="..." tts="..."           # all captions during is_speaking
DIAG echo_caption speaker="<name>" you=<True|False> [ECHO-MATCH] text="..." tts="..." # caption matches TTS output
DIAG echo_false_abort_suppressed — caption matches TTS output                       # non-You echo correctly suppressed
caption: rejected echo-suspect during abort — prev="..." new="..."                  # continuity guard rejected discontinuous text
```
`[ECHO-MATCH]` fires when caption text overlaps with `_tts_text` (substring match or 60%+ word overlap). `echo_false_abort_suppressed` means a non-"You" caption was recognized as bot echo and did not trigger abort.

**Playback interruption** (caption detected during operator's response):
```
TIMING abort_caption_detected speaker=<name> text="..."                     # non-"You" caption during is_speaking
TIMING playback_interrupt_classifying speaker=<name> text="..."             # classifier invoked on the caption
TIMING playback_interrupt_classify_start                                    # LLM stream begins
TIMING playback_interrupt_classify_done token="PASS"                        # noise/hallucination → continue playback
TIMING playback_interrupt_dismissed — continuing playback                   # playback NOT interrupted

TIMING playback_interrupt_classify_done token="INTERRUPT"                   # real interruption → stop playback
TIMING playback_interrupt_confirmed — stopping playback                     # confirmed_interrupt event set
TTS play_audio: interrupted by user speech                                  # mpv terminated mid-playback
TIMING response_interrupted — user talked over playback
```
Note: `playback_interrupt_empty` fires if abort has no associated text (fallback: confirms interrupt).

**Processing-phase interruption** (speaker kept talking after finalization, before playback):
```
TIMING abort_caption_detected speaker=<name> text="..."                    # caption detected during processing
TIMING interruption_detected — speaker=<name> original="..." updated="..." # text grew beyond original prompt
TIMING interruption_classified=PASS — playing original response            # updated text not for operator → play as planned
TIMING interruption_classified=RESPOND — re-processing                     # updated text IS for operator → re-process
TIMING interruption_filler clip=filler_NN.mp3                              # "heard you" / "one sec" clip
```

**What to check:**
- `LLM ask_stream` present but no `llm_first_token` → streaming hung; check for `LLM API stream failed` error
- `LLM ask` present but no `LLM reply` → API call hung or raised; check for `LLM API call failed` error
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
caption: [Alice] Hey operator what is the plan  [bridge_lag=Nms batch_delay=Nms]   # raw caption + bridge lag + setTimeout batch delay
TIMING caption_wake_detected speaker=Alice prompt_so_far="what is the plan"  # wake found mid-speech
TIMING caption_wake_confirmed — entering silence detection
TIMING caption_finalized reason=silence gap=0.7Ns speaker=Alice prompt="what is the plan"
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

**DOM timing instrumentation** — raw mutation timestamps logged before setTimeout batching.
Fires for addedNodes, characterData, and text node mutations. Use these to distinguish
Meet ASR delays from our batching overhead:
```
CaptionsAdapter: JS diagnostic — dom_raw mutation_gap=0.0ms text=Hey.           # first mutation (gap=0)
CaptionsAdapter: JS diagnostic — dom_raw mutation_gap=334.0ms text=Hey Operator. # ~333ms = Meet's render cadence
CaptionsAdapter: JS diagnostic — dom_raw mutation_gap=505.7ms text=Hey Operator. What's  # longer gap = natural speech pause
```

**What to check:**
- No `caption_wake_detected` when someone says "hey operator" → check caption observer injection, check captions are enabled
- `caption_wake_retracted` frequently → ASR is unstable for "hey operator", may need wake phrase tuning
- No `caption:` lines at all → caption callback not wired, check adapter/processor connection
- Caption finalization hangs (no `caption_finalized` after user stops talking) → check if all captions showing `[You]` speaker label; Meet occasionally relabels user speech — is_speaking gate and wake anchoring handle this correctly

---

## Section 5b: Caption Mode — Conversation Mode

Follow-up utterances don't require "hey operator". The streaming first-token
classification decides: PASS (exit conversation), EXIT (respond then exit), or
anything else (it's the response — play filler, stream, synthesize, speak).

```
Entering conversation mode
State → listening (Listening...)
TIMING caption_capture_start (timeout=60 require_wake=False)     # 60s backstop timeout (PASS/EXIT handle normal exits)
TIMING caption_followup_started — entering silence detection
TIMING caption_prompt_finalized speaker=Alice prompt="now triple it"
```

Then enters Section 4 streaming path. First token decides:

**Follow-up response** (first token is not PASS/EXIT):
```
TIMING llm_first_token elapsed=0.300s token="That"               # response token → filler + stream + speak
...                                                               # responds, loops back to conversation mode
```

**PASS — exit conversation mode** (first token is PASS):
```
TIMING llm_classified=PASS — not for operator
Conversation mode: PASS — exiting conversation mode
State → idle (Listening for 'operator'...)
```

**EXIT — respond and exit** (first token is EXIT):
```
TIMING llm_classified=EXIT — wrap-up response
...                                                               # speaks sign-off ("You're welcome!")
Conversation mode: EXIT — responded and exiting
State → idle (Listening for 'operator'...)
```

**Wake phrase reset** ("hey operator" during conversation → reset to wake mode):
```
Conversation mode: wake phrase detected — resetting to wake mode
```

**Backstop timeout** — no captions within 60 seconds:
```
TIMING caption_timeout (no captions in 60s)
Conversation mode: no follow-up — returning to idle
State → idle (Listening for 'operator'...)
```

**Wake mode PASS** (ambient speech contained "hey operator" but wasn't directed at bot):
```
TIMING wake_caption speaker=Alice prompt="..."
TIMING llm_classified=PASS — not for operator
TIMING wake PASS — ignoring ambient speech
```

---

## Section 5b: Perceived Latency Probe (caption mode only, DEBUG level)

**These log at DEBUG level** — not visible in standard INFO logs. Enable DEBUG logging to see them.
Interspersed with caption and pipeline events. Measure the gap between acoustic speech end and pipeline events.

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

## Section 5c: Chat Mode (`--chat`)

Chat mode uses `ChatRunner` + `MacOSAdapter`. No audio pipeline, no wake detection.
The bot joins the meeting, uses a MutationObserver for instant chat message detection,
and checks participant count to decide whether wake phrase is required.

**Startup + join:**
```
TIMING chat_setup=2.5s                           # total chat mode initialization
Starting Operator — joining <url>
ChatRunner: joining <url>
MacOSAdapter: joining <url>
TIMING browser_launch=0.9s
TIMING navigation=0.8s
TIMING pre_join_ready=0.5s
TIMING detect_page_state=0.0s (state=pre_join)
session: screenshot saved to debug/pre_camera_toggle.png  # pre-toggle snapshot
session: HTML saved to debug/pre_camera_toggle.html
MacOSAdapter: clicked 'Turn off camera'           # click camera toggle
MacOSAdapter: camera confirmed off (data-is-muted=true)  # DOM confirmation
TIMING camera_toggle=0.7s
MacOSAdapter: clicked 'Join now'                 # or 'Ask to join' or 'Switch here'
TIMING join_click=0.1s (Join now)
MacOSAdapter: joined meeting successfully
ChatRunner: joined — starting chat loop
TIMING in_meeting_wait=1.4s
MacOSAdapter: mic already on                     # or "microphone unmuted"
TIMING mic_check=0.0s
MacOSAdapter: in meeting — holding browser open
TIMING total_join=4.1s
MacOSAdapter: chat MutationObserver installed     # observer injected on chat panel
ChatRunner: participant count changed 0 → 2       # initial count detection
```

**MCP parallel startup (if mcp_servers configured):**
```
TIMING mcp_connect=2.1s (32 tools)               # runs in parallel with browser join
MCP server 'linear': 32 tools discovered
MCP server 'linear' connected — 32 tools
```

**1-on-1 mode (≤2 participants — no wake phrase required):**
```
MacOSAdapter: observer drained 1 new messages                     # MutationObserver caught message instantly
ChatRunner: new message sender='Jojo Shapiro' id='spaces/.../messages/...' text='whats up' one_on_one=True
LLM ask model=gpt-4.1-mini mode=chat max_tokens=300 history_msgs=0 prompt_chars=43 tools=32
LLM utterance: Jojo: whats up (First time talking to Jojo)        # first-name only, first-time greeting marker
LLM reply="Hello Jojo! How can I assist you today?"
MacOSAdapter: chat sent: 'Hello Jojo! How can I assist you today?'
```

**Echo suppression (bot's own messages — Meet creates 2 DOM elements per message):**
```
MacOSAdapter: observer drained 2 new messages     # Meet adds 2 divs with different IDs, same text
ChatRunner: skipping own message (text match)      # first element filtered
ChatRunner: skipping own message (text match)      # second element filtered (batch discard)
```

**Multi-participant mode (>2 participants — wake phrase required):**
```
ChatRunner: participant count changed 2 → 3        # someone joined
ChatRunner: new message sender='Jojo Shapiro' id='...' text="What's two plus two?" one_on_one=False
ChatRunner: stored as context (no wake phrase)      # no @operator → stored as LLM context only

ChatRunner: new message sender='Jojo Shapiro' id='...' text="@operator what's 2+2?" one_on_one=False
LLM ask model=gpt-4.1-mini mode=chat max_tokens=300 history_msgs=9 prompt_chars=17 tools=32
LLM utterance: Jojo: what's 2+2?                   # wake phrase stripped, first-name prefix
```

**Participant transitions (dynamic wake phrase gating):**
```
ChatRunner: participant count changed 3 → 2        # someone left → back to 1-on-1
ChatRunner: new message sender='Jojo Shapiro' id='...' text="What's the capital of Singapore?" one_on_one=True
```

**Bot's own messages filtered by sender name:**
```
ChatRunner: skipping own message (sender='Operator')   # sender matched config.AGENT_NAME
```

**Shutdown (Ctrl+C):**
```
Received signal 2 — shutting down
MCP client shutdown complete                       # if MCP was configured
MacOSAdapter: waiting for browser to close...
MacOSAdapter: clicked Leave call                   # clean leave (or "navigated away" as fallback)
MacOSAdapter: browser closed
MacOSAdapter: left meeting
ChatRunner: participant count changed 2 → 0        # final count after browser closes
```

**Join failure lines** are identical to Section 1 (same MacOSAdapter join flow).

**What to check:**
- No `ChatRunner: new message` lines when messages are sent → chat panel not opening; check `_ensure_chat_open()` selectors
- No `MutationObserver installed` → observer injection failed; check `[data-panel-id="2"]` selector
- `observer drained 0` repeatedly despite messages → observer not firing; check if panel container changed
- Echo loop (bot responding to own messages) → check `skipping own message (text match)` appears twice per send; if only once, batch discard may be broken
- `participant count changed` not appearing → `[data-requested-participant-id]` selector may have changed
- `{Adapter}: could not open chat panel: ...` → chat button selector failed; debug screenshot saved to `debug/chat_btn_not_found.png`
- `MacOSAdapter: send_chat failed` → textarea selector changed or chat panel closed unexpectedly

---

## Section 6: Shutdown

Clean shutdown (Ctrl+C, SIGTERM, or leave()):

**Audio mode:**
```
Interrupted — leaving meeting                     # or "Received signal 15 — shutting down"
AgentRunner: audio read loop ended
AgentRunner: transcription loop ended
MacOSAdapter: waiting for browser to close...
MacOSAdapter: navigated away — left meeting cleanly   # about:blank triggers Meet's leave signal
MacOSAdapter: browser closed
MacOSAdapter: left meeting
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

**Caption mode — auto-leave (past end time + user departed):**
```
CaptionsAdapter: user detected via aria-label               # presence check every 30s after end time (or "via innerText")
CaptionsAdapter: past end time but user still present        # end time passed, user still in meeting — stay
CaptionsAdapter: past end time and user has left — auto-leaving  # both conditions met — leave
CaptionsAdapter: clicked Leave call
CaptionsAdapter: browser closed
AgentRunner: caption loop ended
CaptionsAdapter: left meeting
State → idle (Waiting for meeting...)
POLLING meeting ended — waiting for next
```

**Calendar polling mode (no meeting URL argument):**
```
CalendarPoller: reusing cached profile copy (cookies unchanged)   # appears on warm restart only — session 67 mtime gate skipped the rmtree+copytree
CalendarPoller: started (polling every 30s)
STARTUP polling mode — waiting for meetings
State → idle (Waiting for meeting...)
CalendarPoller: calendar loaded — Google Calendar - Tuesday, April 7, 2026, today    # session 67: gap from `started` is now ~1.5–4.5s (was ~9–10s)
CalendarPoller: 'test' already ended — skipping https://meet.google.com/xxx-yyyy-zzz  # logged once per event_id, not every poll (session 66)
CalendarPoller: 'standup' starts in -5.3m — https://meet.google.com/xxx-yyyy-zzz       # within join window
CalendarPoller: joining 'standup' (-5.3m until start)
POLLING received meeting URL: https://meet.google.com/xxx-yyyy-zzz
```

**Overlapping meetings (single-meeting design, session 66):**
```
CalendarPoller: joining 'meeting 1' (0.5m until start)
POLLING received meeting URL: https://meet.google.com/xxx-yyyy-zzz
# ... meeting 1 is running ...
CalendarPoller: queuing 'meeting 2' while another meeting is active (busy=True, pending=0) — Operator handles one meeting at a time; will join after current ends
CalendarPoller: joining 'meeting 2' (1.0m until start)
# ... meeting 1 ends / user leaves ...
POLLING meeting ended — waiting for next
POLLING received meeting URL: https://meet.google.com/xxx-yyyy-zzz   # meeting 2 dequeued
```

**Stale meeting skipped on dequeue (session 66):**
```
POLLING skipping https://meet.google.com/xxx-yyyy-zzz — meeting ended while queued
```

**Pre-join gate failure (user never appeared):**
```
CaptionsAdapter: waiting for 'Jojo Shapiro' on pre-join screen...
CaptionsAdapter: user never appeared — not joining           # leave() called or user_not_present
STARTUP join failed: user_not_present
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
| Kokoro synthesis | 0.5-1.5s | >3s |
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
