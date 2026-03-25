# Operator — AI Meeting Participant

A macOS menu bar app that joins meetings as an AI participant. Anyone in the meeting can say **"operator"** to prompt it; Operator listens to all meeting audio, thinks, and responds aloud through the meeting so every participant hears it. No one else needs to install anything — they just invite Operator to the call.

The goal: a helpful AI thought partner for remote brainstorming sessions.

---

## Resuming Development (Start Here)

If you're picking this up in a new session:

```bash
cd ~/Desktop/projects/operator
source venv/bin/activate
tail -f /tmp/operator.log          # watch logs in one terminal
python setup.py py2app -A && open dist/Operator.app   # build and run in another
```

**Currently iterating on:**
- Nothing active — all core features complete and tested.

**Still to implement:**
- LLM→TTS streaming overlap — start TTS before LLM finishes generating (sentence-level batching). Estimated ~0.5-1s latency reduction.

**Open issues (lower priority):**
- Headphone routing issue — when the user's AirPods are connected to their phone (in-call), Operator responses route through BlackHole → Google Meet → phone correctly, but only when the meeting audio is playing through that path. If headphones switch to the Mac as the audio source, audio is lost. Needs investigation.

**Key commands:**
```bash
source venv/bin/activate                          # activate venv
python setup.py py2app -A && open dist/Operator.app  # build + run
tail -f /tmp/operator.log                         # live logs
grep "TIMING" /tmp/operator.log                   # timing analysis
grep "wake_\|prompt_finalized\|Pipeline" /tmp/operator.log  # key events only
```

---

## Table of Contents

1. [How It Works](#how-it-works)
2. [Architecture](#architecture)
3. [Tech Stack](#tech-stack)
4. [Project Structure](#project-structure)
5. [Environment Setup](#environment-setup)
6. [Building & Running](#building--running)
7. [Current Development Status](#current-development-status)
8. [Build Plan](#build-plan)
9. [Known Gotchas](#known-gotchas)
10. [Incident Log](#incident-log)
11. [Key Decisions Made](#key-decisions-made)

---

## How It Works

1. Operator runs on a dedicated machine (or the host's machine) and joins the meeting as a named participant via a browser logged into the Operator Google account
2. Operator's virtual mic in the meeting is routed through **BlackHole** — a virtual audio device — so TTS responses are heard by all participants
3. **ScreenCaptureKit** captures all system audio (everything in the meeting) and feeds it into a continuous utterance-based listening loop
4. When anyone says **"operator"**, Whisper detects the wake phrase in the transcription:
   - **Inline:** "operator, what's the plan?" → trailing text sent to GPT-4.1-mini immediately
   - **Wake-only:** "operator" alone → plays a short acknowledgment ("yeah?", "yes?", or "mm-hm?"), then captures the next utterance as the prompt
5. The prompt (plus rolling meeting transcript for context) is sent to GPT-4.1-mini
6. The response is streamed via ElevenLabs TTS → BlackHole → Operator's meeting mic → heard by all participants
7. After Operator responds, it stays in **conversation mode** for 20s — replies go directly to the LLM without needing to say "operator" again. After 20s of silence, it returns to idle.
8. Menu bar icon shows current state: ⚪ idle / 🔴 listening / 🟡 thinking / 🟢 speaking

---

## Architecture

```
Meeting audio (all participants)
        │
        ▼
Swift helper (audio_capture) — ScreenCaptureKit
        │  raw Float32 PCM via stdout pipe
        ▼
Python (app.py) — reads audio from subprocess into buffer
        │
        ▼
Utterance detector — RMS-based silence detection
(waits for speech, then silence, then finalizes)
        │
        ▼
faster-whisper (base model) — transcribes every utterance
        │
        ▼
Wake phrase detector — scans for "operator" in transcription
        │  inline case: extract trailing text as prompt
        │  wake-only case: capture next utterance as prompt
        ▼
Completeness check + backchannel (utterances ≥3.5s only)
        │  GPT-4.1-mini checks if thought is complete (YES/NO)
        │  if YES: finalize immediately (no backchannel)
        │  if NO: plays "mm-hmm?" or "go on" via mpv → BlackHole, keep listening
        ▼
GPT-4.1-mini (OpenAI) + rolling transcript context
        │
        ▼
ElevenLabs TTS (eleven_flash_v2_5, streaming)
        │
        ▼
BlackHole virtual audio device
        │
        ▼
Operator's mic input in Google Meet → heard by all participants
        │
        ▼
Conversation mode — next reply goes to LLM without wake phrase
(exits after 20s of silence)
```

### Same-machine testing

You can host the meeting and run Operator on the same machine:
- **Your browser:** join the meeting as yourself (normal account, headphones on)
- **Operator's browser:** a separate Chrome profile logged into the Operator Google account; its mic input is set to BlackHole
- Operator hears everyone via system audio; its TTS goes through BlackHole into the meeting
- Echo prevention pauses system audio ingestion while Operator is speaking

---

## Tech Stack

| Component | Library | Notes |
|-----------|---------|-------|
| Menu bar UI | `rumps` 0.4.0 | macOS status bar app framework |
| System audio capture | Swift helper (`audio_capture`) via ScreenCaptureKit | Compiled Swift CLI captures system audio, streams raw PCM to Python via stdout pipe. PyObjC cannot bridge ScreenCaptureKit's `CMSampleBufferRef` callbacks — see Incident 2 |
| Wake phrase detection | `faster-whisper` (`base` model) | Whisper transcribes every utterance; wake phrase scanned in text. Inline ("operator, [prompt]") and wake-only ("operator" alone) cases both supported. 0.5s silence pad prepended to audio to prevent Whisper dropping the first word |
| Virtual audio device | **BlackHole** (system, not Python) | Routes TTS output → Operator's meeting mic; install: `brew install blackhole-2ch` |
| LLM | `openai` (gpt-4.1-mini) | Conversational history + rolling transcript maintained per session |
| TTS | `elevenlabs` (`eleven_flash_v2_5`) | Streaming endpoint — audio starts playing ~0.4s after request. **Requires paid plan** — free tier gets flagged for abuse |
| Audio playback | `mpv` | Used for TTS responses and backchannel clips; must be installed: `brew install mpv` |
| Backchannel clips | `backchannel_mmhmm.mp3`, `backchannel_goon.mp3` | Pre-generated via ElevenLabs in George's voice. Play through BlackHole only when GPT-4.1-mini determines the thought is incomplete |
| Acknowledgment clips | `ack_yeah.mp3`, `ack_yes.mp3`, `ack_mmhm.mp3` | Pre-generated via ElevenLabs in George's voice. One plays randomly on wake-only trigger ("operator" alone) before listening for the prompt |
| App bundle | `py2app` | Alias mode (`-A`) for development; required for ScreenCaptureKit |
| Meeting platform | **Google Meet** | Browser-based, no install; Operator joins as a named participant |

**Python version:** 3.11 (Homebrew: `/opt/homebrew/bin/python3.11`)
**macOS:** Tahoe 26.2+

---

## Project Structure

```
operator/
├── app.py                  # Main app — entry point
├── audio_capture.swift     # Swift CLI — captures system audio via ScreenCaptureKit
├── audio_capture           # Compiled Swift binary (generated, not committed)
├── backchannel_mmhmm.mp3   # Pre-generated backchannel clip — "mm-hmm?" (George voice)
├── backchannel_goon.mp3    # Pre-generated backchannel clip — "go on" (George voice)
├── ack_yeah.mp3            # Pre-generated acknowledgment clip — "yeah?" (George voice)
├── ack_yes.mp3             # Pre-generated acknowledgment clip — "yes?" (George voice)
├── ack_mmhm.mp3            # Pre-generated acknowledgment clip — "mm-hm?" (George voice)
├── generate_backchannel.py # One-time script to regenerate backchannel + acknowledgment clips via ElevenLabs
├── setup.py                # py2app build config
├── capture_clips.py        # STT benchmark — standalone clip capture using same Swift helper + utterance detection
├── benchmark_stt.py        # STT benchmark — tests clips against all providers, prints comparison table
├── benchmark_clips/        # STT benchmark — captured WAV files + ground_truth.json (generated)
├── test_api_keys.py        # Validates OpenAI + Deepgram + AssemblyAI + Speechmatics API keys
├── .env                    # API keys (never commit this)
├── .gitignore
├── spec.md                 # Original product spec
├── venv/                   # Python virtual environment
├── dist/Operator.app       # Built app bundle (generated, not committed)
├── build/                  # py2app build artifacts (generated, not committed)
├── credentials.json        # Google OAuth client credentials (never commit)
├── token.json              # Google OAuth access token (never commit)
├── browser_profile/        # Playwright Chromium profile — logged into Operator Google account, BlackHole set as mic/speaker (never commit)
└── test_*.py               # Component test scripts
    ├── test_apis.py         # Verifies OpenAI + ElevenLabs keys work
    ├── test_whisper.py      # Whisper transcription (records 5s from mic)
    ├── test_menubar.py      # Menu bar state cycling UI stub
    ├── test_swift_capture.py # Python ↔ Swift helper integration test
    ├── test_calendar.py     # Verifies Google Calendar API connection + lists upcoming events
    ├── test_playwright_basic.py # Basic Playwright smoke test (example.com)
    └── test_playwright.py   # Auto-join flow — navigates to Meet URL, dismisses popup, clicks Join
```

---

## Environment Setup

### First-time setup

```bash
# Install Homebrew (if not already installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python 3.11
brew install python@3.11

# Install mpv (required for TTS audio streaming and backchannel clips)
brew install mpv

# Install BlackHole virtual audio device (routes TTS into meeting mic)
brew install blackhole-2ch

# Create and activate virtual environment
cd ~/Desktop/projects/operator
/opt/homebrew/bin/python3.11 -m venv venv
source venv/bin/activate

# Install all dependencies
pip install openai elevenlabs python-dotenv sounddevice soundfile numpy faster-whisper rumps py2app

# Install STT benchmark dependencies (optional — only needed for benchmark)
pip install deepgram-sdk assemblyai speechmatics-batch mlx-whisper

# Compile the Swift audio capture helper
swiftc -O -o audio_capture audio_capture.swift -framework ScreenCaptureKit -framework CoreMedia -framework AVFoundation
```

### API Keys

Create a `.env` file in the project root:

```
OPENAI_API_KEY=your_key_here
ELEVENLABS_API_KEY=your_key_here

# STT Benchmark (optional — leave blank to skip that provider)
DEEPGRAM_API_KEY=
ASSEMBLYAI_API_KEY=
SPEECHMATICS_API_KEY=
```

- **OpenAI:** platform.openai.com → API Keys
- **ElevenLabs:** elevenlabs.io → Profile → API Key (ensure **all permissions** are enabled). **Must be a paid plan** — free tier gets blocked for "unusual activity" during development
- **Deepgram:** deepgram.com → Dashboard → API Keys (STT benchmark only)
- **AssemblyAI:** assemblyai.com → Dashboard → API Key (STT benchmark only)
- **Speechmatics:** speechmatics.com → Manage → API Keys (STT benchmark only)

### Operator's Google Account

Operator joins meetings as a named participant. You need a Google account for it — any account works. Set up a separate Chrome profile logged into that account. In that profile, set the microphone to **BlackHole 2ch**.

### Required macOS Permissions

Grant these in **System Settings → Privacy & Security**:

| Permission | Required For | Where to Grant |
|------------|-------------|----------------|
| Screen & System Audio Recording | Capturing meeting audio via ScreenCaptureKit | Privacy → Screen Recording |

---

## Building & Running

```bash
# Activate venv
source venv/bin/activate

# Compile Swift audio capture helper (only needed after changing audio_capture.swift)
swiftc -O -o audio_capture audio_capture.swift -framework ScreenCaptureKit -framework CoreMedia -framework AVFoundation

# Build the .app bundle (alias/dev mode — fast, references venv directly)
python setup.py py2app -A

# Launch
open "dist/Operator.app"

# Watch logs
tail -f /tmp/operator.log
```

### If the icon doesn't appear in the menu bar

This is almost always a macOS Launch Services cache issue. Run:

```bash
pkill -9 -f Operator
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -r -domain local -domain system -domain user
open "dist/Operator.app"
```

### If the icon still doesn't appear after that

Change the bundle identifier in `setup.py` to force macOS to treat it as a new app:

```python
"CFBundleIdentifier": "com.operator.meeting-participant.v3",  # increment version
```

Then rebuild and relaunch.

---

## Current Development Status

### Foundation — verified ✅

All core components tested and working:

- **API connectivity** — OpenAI and ElevenLabs keys validated at startup with clear error messages
- **Whisper transcription** — faster-whisper `base` model loads and transcribes correctly
- **LLM integration** — GPT-4.1-mini via OpenAI SDK. Conversational history maintained per session; max 60 tokens; system prompt enforces spoken-word style (no markdown, 1-2 sentences, under 30 words) and instructs the LLM to infer intended words from speech-to-text errors using surrounding context
- **ElevenLabs TTS streaming** — `eleven_flash_v2_5` model; first audio chunk in ~0.4s
- **Full pipeline** — text → LLM → TTS → audio plays; verified end-to-end
- **App bundle** — Launches via py2app alias mode; `LSUIElement = True` (no Dock icon); reaches idle state cleanly

### System audio capture — verified ✅

- **Swift audio capture helper** — `audio_capture.swift` compiled and tested. Captures system audio (16kHz mono Float32) via ScreenCaptureKit and streams raw PCM to stdout
- **Python integration** — `app.py` launches the Swift helper as a subprocess, reads PCM from its stdout, converts to numpy array

### Continuous listening + wake phrase + full pipeline — verified ✅

**Completed:** Session 3 (March 15, 2026)

- **Utterance-based audio capture** — RMS-based silence detection (`UTTERANCE_SILENCE_RMS = 0.02`). Accumulates audio while speech is detected, finalizes after `UTTERANCE_SILENCE_THRESHOLD` (2 × 0.5s = ~1s) of silence or `UTTERANCE_MAX_DURATION` (10s) hard cap
- **Rolling transcript** — Up to 100 lines of ambient meeting conversation sent as context with each LLM request (last 20 lines used)
- **Transcript isolation** — Utterances consumed as prompts are NOT added to the rolling transcript. Only ambient meeting conversation accumulates in context

### Echo prevention + hallucination filtering — verified ✅

**Completed:** Session 4 (March 15, 2026)

- **Echo prevention** — `_speaking` flag pauses audio ingestion for the entire think+speak cycle. Audio buffer drained before pausing and after resuming
- **Whisper hallucination filtering** — `WHISPER_HALLUCINATIONS` set filters known false transcriptions on silence

### Production test (Google Meet) — verified ✅

**Completed:** Session 5 (March 15, 2026)

- BlackHole routing verified; TTS heard by all meeting participants
- Pipeline timing: ~0.9-2s LLM, ~0.4s TTS first chunk, ~1-9s playback depending on response length

### Auto-join from Google Meet invites — complete ✅

**Session 8–9 (March 18, 2026)**

- Google Cloud project created (`operator-meet`), Google Calendar API enabled
- OAuth credentials set up (`credentials.json`), token saved (`token.json`) — one-time browser auth done
- `calendar_join.py` — calendar poller + auto-join module:
  - Polls every 2 minutes, looks 15 minutes ahead for events with a `hangoutLink`
  - Triggers auto-join 2 minutes before start; tracks joined event IDs to avoid re-joining
  - Pre-join flow: dismiss notifications popup → turn off camera → click "Join now" / "Ask to join" → unmute mic
  - Removes stale `SingletonLock` on launch to recover from crashed sessions
  - Uses real Chrome (`/Applications/Google Chrome.app`) instead of Playwright's "Chrome for Testing" — required for BlackHole mic routing (Chrome for Testing cannot be granted macOS microphone permission)
- `CalendarPoller` integrated into `app.py` as a background thread; starts on app launch, stops on quit
- Google Calendar "Automatically add invitations → Yes" required on Operator's account so external invites appear without manual accept
- End-to-end verified: invite sent from personal account → Operator auto-joins → audio heard by all participants

### Latency reduction + backchannel + conversation flow — complete ✅

**Sessions 10–11 (March 19–20, 2026)**

- **TIMING instrumentation** ✅ — `TIMING`-prefixed log lines at every pipeline stage. Use `grep "TIMING" /tmp/operator.log` for a clean timeline.
- **Validated latency breakdown** ✅ — End-of-speech → first audio: silence detection (1.0s) + Whisper (0.5s) + LLM (0.9–3s) + TTS first chunk (0.4s). The LLM API round-trip dominates and is not addressable in code.
- **Porcupine removed** ✅ — Replaced with Whisper-based inline wake phrase detection. No separate real-time detection loop; no chime. Wake phrase detected after each utterance transcription. Inline case ("operator, [prompt]") extracts trailing text directly; wake-only case captures next utterance.
- **Whisper first-word dropout fix** ✅ — Whisper reliably drops the first word when audio starts immediately with speech. Fixed by prepending 0.5s of silence to all audio before transcription.
- **Backchannel + completeness check** ✅ — For prompt utterances ≥3.5s: transcribes and checks completeness first via GPT-4.1-mini (with recent conversation context for follow-up recognition). If complete → finalize immediately without backchannel. If incomplete → plays "mm-hmm?" or "go on" (George voice, pre-cached) via mpv → BlackHole, then keeps listening. Short utterances skip this entirely. Echo fix: backchannel thread is joined before resuming capture, then buffer drained to discard clip echo.
- **Verbal acknowledgment on wake** ✅ — When "operator" is said alone (wake-only case), Operator immediately plays one of "yeah?", "yes?", or "mm-hm?" (randomly chosen, George voice, pre-cached) through BlackHole before listening for the follow-up prompt. Echo prevention mirrors the backchannel pattern: `_speaking` flag set, buffer drained before and after playback, 0.2s flush sleep.
- **Conversation mode** ✅ — After Operator responds, stays in 🔴 listening mode for 20s. Follow-up replies go directly to the LLM without needing to say "operator" again. Exits to idle after 20s of no speech.
- **Post-backchannel hang fix** ✅ — `BACKCHANNEL_CONTINUATION_TIMEOUT = 10s`. If no speech arrives after a backchannel NO, the capture loop exits instead of hanging indefinitely.

### LLM swap to GPT-4.1-mini + STT benchmark setup — complete ✅

**Session 12 (March 21, 2026)**

- **LLM swapped from Claude to GPT-4.1-mini** ✅ — All Anthropic SDK usage replaced with OpenAI SDK. Both main responses (`_ask_llm`) and completeness checks (`_check_completeness`) now use `gpt-4.1-mini`. API key check updated to `OPENAI_API_KEY`. All imports, comments, and log strings updated — zero references to Claude/Anthropic remain in `app.py`.
- **STT provider benchmark scaffolding** ✅ — Two standalone scripts created for benchmarking transcription accuracy against real meeting audio:
  - `capture_clips.py` — uses the same Swift `audio_capture` helper and identical RMS-based utterance detection (same thresholds as `app.py`) to save utterances as 16kHz mono WAVs to `benchmark_clips/`
  - `benchmark_stt.py` — runs clips through 6 providers (faster-whisper base, faster-whisper turbo, mlx-whisper large-v3-turbo, Deepgram Nova-3, AssemblyAI, Speechmatics), prints per-clip transcripts + WER + latency + wake phrase detection, saves results to `benchmark_results.json`. Providers with missing API keys are auto-skipped.
  - Pre-defined ground truth for all 5 test phrases ships as `ground_truth.json` — no manual entry step needed.
- **All 4 new API keys validated** ✅ — `test_api_keys.py` confirms OpenAI (gpt-4.1-mini), Deepgram (Nova-3), AssemblyAI (universal-3-pro), and Speechmatics all respond correctly.
- **Benchmark dependencies installed** ✅ — `deepgram-sdk`, `assemblyai`, `speechmatics-batch`, `mlx-whisper`, `openai` added to venv.
- **SDK API gotchas resolved** ✅ — Deepgram v6 uses `client.listen.v1.media.transcribe_file(request=...)` (not `PrerecordedOptions`). AssemblyAI requires `speech_models=["universal-3-pro"]` (`speech_model` is deprecated). Speechmatics uses async `speechmatics.batch.AsyncClient` (not `speechmatics.batch_client.BatchClient`).

### STT provider benchmark — complete ✅

**Session 13 (March 21, 2026)**

- **Benchmark executed** ✅ — 5 clips of real ScreenCaptureKit-captured meeting audio tested against 6 providers. Speechmatics integration bug fixed (SDK returns model objects, not dicts).
- **Results (avg across 5 clips):**

  | Provider | Avg Latency | Avg WER | Wake Detection |
  |---|---|---|---|
  | Deepgram Nova-3 | 0.90s | 0.12% | 3/3 |
  | **faster-whisper base (current)** | **1.19s** | **0.10%** | **3/3** |
  | mlx-whisper large-v3-turbo | 2.36s | 0.14% | 3/3 |
  | AssemblyAI | 6.21s | 0.08% | 3/3 |
  | Speechmatics | 7.49s | 0.15% | 3/3 |
  | faster-whisper turbo | 12.30s | 0.13% | 3/3 |

- **Decision: keep faster-whisper base** ✅ — Deepgram is ~0.3s faster but requires a cloud API key and per-minute costs. Since Operator is intended to be a downloadable open-source product, local inference is the right choice: zero marginal cost, no API key onboarding, model downloads automatically via pip. faster-whisper base offers the best balance of latency (1.2s), accuracy (0.10% WER), and zero cost.

### Backchannel timing fix + STT error tolerance — complete ✅

**Session 14 (March 24, 2026)**

- **Backchannel timing fix** ✅ — Previously, backchannel clips ("mm-hmm", "go on") played immediately on every utterance ≥3.5s, before knowing if the thought was complete. Now: transcribe → completeness check → only play backchannel if incomplete. Complete thoughts go straight to the LLM with no filler. Tradeoff: ~0.5s of natural silence before backchannel (more human-like).
- **Completeness check now context-aware** ✅ — GPT-4.1-mini completeness check now receives the last 5 lines of conversation transcript, not just the isolated utterance. Follow-up questions (e.g. "What temperature is it typically?" after discussing SF weather) are now correctly recognized as complete.
- **STT error tolerance in system prompt** ✅ — Added instruction to the LLM system prompt that input comes from speech-to-text and may contain transcription errors (e.g. "shop advice" → "Shopify's"). The LLM uses surrounding context to infer intended words. Zero latency cost.
- **Backchannel/ack logging** ✅ — `_play_backchannel` and `_play_acknowledgment` now log human-readable messages (e.g. `Operator says: "mmhmm" (backchannel)`).

### Deferred (post-MVP)
- LLM→TTS streaming overlap — sentence-level batching, estimated ~0.5-1s saving
- Visual wake-word feedback (Siri-style animation in the meeting tile)
- Rolling transcript view in menu bar dropdown
- Config file (`~/.operator/config.json`) for voice ID, model, system prompt
- API key storage in macOS Keychain instead of `.env`
- Headphone routing fix — audio goes to wrong device when AirPods switch source

---

## Known Gotchas

**Gotcha #1 — ScreenCaptureKit requires `.app` bundle**
ScreenCaptureKit silently fails when called from a plain Python script. Always build and run via `python setup.py py2app -A && open dist/Operator.app`.

**Gotcha #2 — Menu bar icon requires `.app` bundle on macOS Tahoe**
Running `python app.py` directly from a terminal does not reliably show the menu bar icon on macOS Tahoe.

**Gotcha #3 — ElevenLabs API key permissions + paid plan required**
When creating an ElevenLabs API key, all permissions must be enabled. Also: the free tier gets flagged for "unusual activity" during heavy development (repeated API calls). A paid plan ($5/mo Starter) is required.

**Gotcha #4 — py2app alias mode and macOS Tahoe**
`python setup.py py2app -A` (alias mode) is used for development. Full builds hit a `RecursionError`. Alias mode is sufficient for development.

**Gotcha #5 — ElevenLabs streaming requires mpv**
Must be installed separately: `brew install mpv`.

**Gotcha #6 — pyobjc-framework-* packages are dangerous to touch**
See Incident Log. Never install a new `pyobjc-framework-*` package without reading that section first.

**Gotcha #7 — Screen Recording permission applies per-binary, not per-app**
When Operator launches the `audio_capture` Swift helper, macOS may require a separate Screen Recording permission grant for the helper binary. If audio capture fails silently, go to System Settings → Privacy & Security → Screen & System Audio Recording, remove Operator, and re-add it. Simply toggling the switch off/on is not sufficient — you must remove and re-add.

**Gotcha #8 — Swift helper must be recompiled after changes**
After any change to `audio_capture.swift`, recompile before rebuilding the app:
```bash
swiftc -O -o audio_capture audio_capture.swift -framework ScreenCaptureKit -framework CoreMedia -framework AVFoundation
python setup.py py2app -A
```

**Gotcha #9 — Whisper `base` model hallucinates on silence**
Filters in `WHISPER_HALLUCINATIONS` catch the most common ones. Add new patterns as they appear.

**Gotcha #10 — Wake phrase is "operator" only, not "hey operator"**
The current wake phrase is just "operator". Whisper often drops "hey" anyway, so this is intentional.

**Gotcha #11 — Menu bar UI updates must use callAfter**
`rumps` UI updates from background threads silently fail. All UI updates from background threads must go through `_set_state()`, which uses `PyObjCTools.AppHelper.callAfter()`.

**Gotcha #12 — TTS output device is hardcoded to BlackHole**
`_speak()` always routes through `coreaudio/BlackHole2ch_UID`. For dev testing without a meeting, temporarily change `BLACKHOLE_DEVICE` to `"auto"` in `app.py` (but don't commit that change).

**Gotcha #13 — System audio only, not microphone**
The Swift helper captures system audio via ScreenCaptureKit, not microphone input. In a real meeting, other participants' voices come through system audio naturally.

**Gotcha #14 — "operate" is a common meeting word — do not use as wake phrase variant**
Earlier versions considered matching "operate" as a fuzzy variant of "operator". Rejected: "operate" appears too frequently in normal meeting conversation and would cause constant false triggers.

**Gotcha #15 — Use real Chrome, not Playwright's "Chrome for Testing", for the meeting browser**
Playwright's bundled "Chrome for Testing" cannot be granted macOS microphone permission — the system permission prompt never appears for it, so BlackHole audio is never transmitted into the meeting. Fix: pass `executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"` to `launch_persistent_context`. Real Chrome already has mic permission and works correctly.

**Gotcha #16 — Google Calendar "Automatically add invitations" must be enabled on Operator's account**
By default, Google Calendar does not add events from unknown senders. Operator will never see external invites unless this is turned on: calendar.google.com → Settings → Event settings → Automatically add invitations → Yes.

**Gotcha #17 — Playwright browser profile may have a stale SingletonLock**
If a previous browser session crashed or was force-killed, it leaves a `SingletonLock` file in `browser_profile/`. The next launch fails with "Failed to create a ProcessSingleton". `calendar_join.py` removes this file automatically before each launch.

**Gotcha #18 — `playwright install` must be run via the venv, not the system Python**
Running `playwright install` from the system shell installs browsers to `~/Library/Caches/ms-playwright` but the venv's Playwright may look elsewhere. Always run: `python -m playwright install chromium` with the venv active. Also set `PLAYWRIGHT_BROWSERS_PATH` to `~/Library/Caches/ms-playwright` in code to ensure the right path is used at runtime.

**Gotcha #19 — Whisper drops the first word without a silence pad**
Whisper has a known tendency to drop the first word of an utterance when audio starts immediately with speech. All audio is prepended with 0.5s of silence before being passed to `whisper.transcribe()`. Do not remove this pad.

**Gotcha #20 — Backchannel echo must be drained before resuming capture**
Backchannel clips play through BlackHole → meeting → system audio → get re-captured. After a backchannel NO (incomplete thought), the code joins the backchannel thread, sleeps 0.2s, then drains the audio buffer before resuming. Do not remove this drain or the clip words will appear in the next Whisper transcription.

---

## Incident Log

### Incident 1: Menu bar icon disappeared after pyobjc-framework-AVFoundation install/uninstall

**Date:** Session 1 (March 2026)

**What happened:**
Installing `pyobjc-framework-AVFoundation` caused an immediate SIGTRAP crash. Uninstalling it left pyobjc in a damaged state, and macOS Launch Services silently suppressed the menu bar icon.

**Resolution:**
1. Rebuilt the venv from scratch
2. Changed bundle identifier to `com.operator.meeting-participant.v2`
3. Reset the Launch Services database:
   ```bash
   /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -r -domain local -domain system -domain user
   ```

**Rules going forward:**
- Never install a new `pyobjc-framework-*` package without verifying compatibility
- If the menu bar icon disappears with no crash log, run the Launch Services reset immediately
- The current bundle ID is `com.operator.meeting-participant.v2` — do not revert it

### Incident 2: PyObjC SIGABRT crash on ScreenCaptureKit delegate callback

**Date:** Session 2 (March 15, 2026)

**Root cause:**
PyObjC cannot bridge `CMSampleBufferRef` (an opaque CoreFoundation struct pointer). The crash was fatal and reproducible regardless of type encoding used.

**Resolution:**
Replaced PyObjC delegate with a Swift CLI helper (`audio_capture.swift`) that captures system audio natively and pipes raw PCM to Python via stdout.

**Rules going forward:**
- Do not use PyObjC for ScreenCaptureKit delegate callbacks
- Use the Swift helper for all system audio capture

### Incident 3: ElevenLabs free tier flagged for abuse

**Date:** Session 6 (March 17, 2026)

**What happened:**
ElevenLabs returned a 401 "detected_unusual_activity" error mid-session. The free tier was suspended. Likely triggered by repeated API calls during development (startup caching attempts + normal usage).

**Resolution:**
Upgrade to ElevenLabs paid plan.

---

## Key Decisions Made

| Decision | Rationale |
|----------|-----------|
| Python + Swift helper | Python for orchestration (APIs, Whisper, TTS, menu bar). Swift helper for system audio capture only — PyObjC can't bridge ScreenCaptureKit's CMSampleBufferRef callbacks |
| Google Meet as target platform | Browser-based, no install required, easiest to get a named participant bot running quickly |
| BlackHole for virtual mic routing | Free, open source, no Python dependency — routes TTS audio into the meeting at the OS level |
| Wake phrase over hotkey | Multiple remote participants need to trigger Operator; a hotkey only works for the person running the app |
| Anyone can trigger wake phrase | Operator is a shared resource for the whole meeting, not just the host |
| Wake phrase is "operator" only | "hey operator" was the original; "hey" is reliably dropped by Whisper so it was removed |
| Porcupine removed | Real-time wake word detection was fast (<400ms) but required a chime that interrupted fast speech and caused echo artifacts. Whisper-based inline detection is simpler and doesn't need a chime |
| Inline wake phrase ("operator, [prompt]") | User says both wake phrase and prompt in one breath; trailing text extracted directly. No second capture phase, no chime to wait for |
| Conversation mode after response | After Operator speaks, stays in 🔴 mode for 20s so follow-up replies go directly to the LLM. Natural back-and-forth without re-triggering wake phrase |
| 0.5s silence pad before Whisper | Whisper reliably drops the first word of an utterance when audio starts cold. Prepending silence gives the model a run-up |
| Utterance-based detection | Replaced rolling 3-second chunk approach. RMS-based silence detection captures complete utterances before transcribing, eliminating mid-word cuts |
| Prompts excluded from rolling transcript | When an utterance is consumed as a prompt, it is not also added to the meeting transcript. Keeps the LLM's context clean (meeting conversation only, not Q&A with Operator) |
| "operate" rejected as wake variant | Common meeting word; would cause constant false triggers |
| `eleven_flash_v2_5` TTS model | Fastest ElevenLabs model; first audio chunk arrives in ~0.4s |
| Whisper `base` model | `tiny` was too inaccurate; `small` was ~5x slower with marginal gain; `base` is the current balance |
| Max 60 tokens per LLM response | System prompt enforces "1-2 sentences, under 30 words". Longer responses produce 13-17s TTS playback |
| ElevenLabs paid plan required | Free tier gets suspended during heavy development. Not negotiable for this use pattern |
| `callAfter` for UI updates | `rumps` title updates from background threads silently fail |
| Rolling transcript as LLM context | Last 20 transcript lines sent with each prompt so the LLM has meeting context |
| BlackHole always on (no toggle) | TTS always routes through BlackHole. For dev testing without a meeting, temporarily change `BLACKHOLE_DEVICE` to `"auto"` locally |
| Backchannel for active listening | When a thought is incomplete, Operator says "mm-hmm?" or "go on" to signal it's listening. Pre-cached ElevenLabs clips in George's voice for zero-latency playback. Backchannel only plays after completeness check confirms the thought is incomplete — never on complete thoughts |
| GPT-4.1-mini for completeness check | Checks if the speaker's thought is complete before deciding whether to play backchannel. Receives last 5 lines of conversation context so follow-up questions are recognized as complete. Sufficient for YES/NO |
| SHORT_UTTERANCE_THRESHOLD = 3.5s | Quick questions skip the backchannel and finalize immediately. 3.5s accounts for the ~1s of silence detection overhead added to actual speech duration |
| Backchannel echo drain | After backchannel NO, join the clip thread + sleep 0.2s + drain buffer. Prevents the clip's echo from appearing in the next Whisper transcription |
| Verbal acknowledgment on wake | When "operator" is said alone, Operator plays "yeah?", "yes?", or "mm-hm?" before listening for the prompt. Gives the speaker immediate audio confirmation without needing a chime or UI update |
| Ack clips are blocking, not threaded | `_play_acknowledgment()` runs synchronously so the echo drain + flag clear complete before `_capture_next_utterance` starts. Threading would let the capture loop start while the clip is still playing, causing the clip to be re-captured as the prompt |
| TIMING log instrumentation | All pipeline stages emit `TIMING`-prefixed log lines for latency analysis. Use `grep "TIMING" /tmp/operator.log` to get a clean timeline |
| GPT-4.1-mini over Claude | Switched from Anthropic Claude (Sonnet + Haiku) to OpenAI GPT-4.1-mini for both main responses and completeness checks. Faster for this use case |
| STT benchmark before swapping provider | Benchmark real meeting audio against multiple providers before changing the transcription pipeline. Clean mic recordings give misleading results — must use ScreenCaptureKit-captured audio |
| Pre-defined ground truth for benchmark | All 5 test phrases have exact scripts written in advance, shipped as `ground_truth.json`. Eliminates a manual data-entry step from the benchmark workflow |
| faster-whisper base over cloud STT | Benchmarked 6 providers. Deepgram was 0.3s faster but requires API key + per-minute cost. For an open-source downloadable product, local inference (zero cost, no API onboarding, auto-downloads via pip) is the right tradeoff at 1.2s latency |
| Backchannel after completeness check, not before | Previously backchannel played in parallel with transcription — caused "mm-hmm" before every response even when the thought was complete. Now: transcribe → check → only backchannel if incomplete. ~0.5s of natural silence before backchannel is more human-like |
| STT error tolerance in LLM prompt over initial_prompt/dictionary | Whisper `initial_prompt` would bias toward specific domain terms but requires user configuration. Instead, the LLM prompt tells GPT to infer intended words from context — works for any domain, zero config, zero latency cost |
| Completeness check with conversation context | Isolated utterances like "What temperature is it typically?" look incomplete without context. Passing last 5 transcript lines lets GPT recognize follow-up questions as complete |
