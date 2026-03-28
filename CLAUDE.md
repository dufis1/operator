# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Operator is an AI meeting participant bot. It joins Google Meet, listens for the wake phrase "operator", transcribes the prompt via Whisper, queries GPT-4.1-mini, and plays the TTS response through a virtual audio device so all meeting participants hear it.

## Commands

### Run

```bash
source venv/bin/activate

# macOS menu bar app
python __main__.py

# Linux / headless with a meeting URL
python __main__.py https://meet.google.com/xxx-yyyy-zzz
# or
MEETING_URL=https://meet.google.com/xxx-yyyy-zzz python __main__.py
```

### Build macOS app bundle

```bash
# Recompile Swift audio helper (only needed after editing audio_capture.swift)
swiftc -O -o audio_capture audio_capture.swift \
  -framework ScreenCaptureKit -framework CoreMedia -framework AVFoundation
codesign --force --sign - --identifier "com.operator.audio-capture" audio_capture

# Build alias bundle (fast, references venv in-place)
python setup.py py2app -A
open dist/Operator.app
```

### Logs & Diagnostics

```bash
tail -f /tmp/operator.log
grep "TIMING" /tmp/operator.log          # latency markers
grep "wake_\|Pipeline\|prompt_finalized" /tmp/operator.log
```

### Tests

Tests are standalone scripts — no pytest runner. Run them individually:

```bash
source venv/bin/activate
python tests/test_apis.py          # validate API keys
python tests/test_whisper.py       # 5s mic recording → Whisper
python tests/test_tts.py           # TTS streaming
python tests/test_pipeline.py      # full pipeline + wake phrase
python tests/test_audio_processor.py
python tests/test_smoke_docker.py  # end-to-end Docker smoke test
```

## Architecture

### Layer Overview

```
App layer (platform UI)
  app.py               — macOS rumps menu bar shell
  __main__.py          — entry point; selects connector, runs preflight, starts AgentRunner

Connectors (platform-specific — implement MeetingConnector interface)
  connectors/base.py          — abstract: join(), get_audio_stream(), send_audio(), leave()
  connectors/macos_adapter.py — ScreenCaptureKit + Playwright + mpv → BlackHole
  connectors/linux_adapter.py — PulseAudio parec + headless Chromium + mpv
  connectors/docker_adapter.py

Pipeline (platform-agnostic — all LLM/audio logic lives here)
  pipeline/runner.py       — AgentRunner: orchestrates the full audio → STT → LLM → TTS loop
  pipeline/audio.py        — AudioProcessor: RMS silence detection, utterance capture, Whisper
  pipeline/wake.py         — detects "operator" (inline or wake-only mode)
  pipeline/conversation.py — state machine: idle → listening → thinking → speaking
  pipeline/llm.py          — LLMClient wrapping gpt-4.1-mini (≤60 tokens, spoken-word prompt)
  pipeline/tts.py          — TTSClient: 3-tier (local Kokoro / openai / elevenlabs), lazy init
```

### Key Data Flow

1. Connector captures raw PCM audio from the meeting
2. `AudioProcessor` buffers it, detects silence boundaries, produces utterance chunks
3. faster-whisper transcribes each chunk (16 kHz mono, base model)
4. `wake.py` checks for "operator" in the transcript
5. `LLMClient` sends prompt + conversation history → GPT-4.1-mini streams response
6. `TTSClient` streams audio back → connector routes to virtual audio device → meeting participants hear it

### Configuration

All runtime settings live in `config.yaml` (loaded by `config.py` into module-level constants). Key knobs:
- `tts_provider`: `local` | `openai` | `elevenlabs`
- `stt_model`: `tiny` | `base` | `small` | `medium`
- `interaction_mode`: `inline` | `wake-only`
- `connector`: `auto` | `macos` | `linux` | `docker`

API keys go in `.env` (never commit).

### Virtual Audio Routing

- **macOS**: ScreenCaptureKit → Swift binary → Python float32 PCM. TTS output via `mpv` → BlackHole virtual device → meeting mic input.
- **Linux**: `parec` from PulseAudio monitor source. TTS output via `mpv` → PulseAudio `MeetingOutput` sink.

### Assets

`assets/` holds pre-generated MP3 clips for immediate acknowledgment (played before LLM responds):
- `ack_*.mp3` — wake-only acknowledgments ("yeah?", "mm-hm?")
- `backchannel_*.mp3` — incomplete-thought signals

### Conversation Timeout

After responding, the bot stays in "conversation mode" for 20 seconds — follow-up questions don't require repeating "operator". Configurable via `CONVERSATION_TIMEOUT_SECONDS` in `config.yaml`.

## Development Notes

- `agent-context.md` tracks current dev phase, architectural decisions, and incident logs — read it before making structural changes.
- `next-steps.md` has the roadmap.
- The GitHub Actions daily smoke test (`.github/workflows/smoke-test.yml`) runs `tests/test_smoke_docker.py` against Docker.
- `browser_profile/` and `auth_state.json` hold logged-in Google session state — never commit them.
