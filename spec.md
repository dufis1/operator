# Claude Meeting Participant — v1 Spec

## Overview
A macOS menu bar app that joins your meetings as an audible AI participant. You trigger Claude with a push-to-talk hotkey; it hears the full meeting conversation, thinks, and responds aloud through your speakers so everyone in the room can hear.

## How It Works
1. App runs in the menu bar while you're in a Google Meet / Zoom / Teams call
2. System audio (all participants) and your mic are continuously transcribed and appended to a rolling conversation context
3. You press and hold a global hotkey to speak a prompt to Claude
4. On hotkey release, your utterance is sent to Claude along with the full meeting context
5. Claude's response is synthesized via ElevenLabs TTS and played through your Mac speakers
6. All meeting participants hear Claude's response

---

## Platform
- macOS only (v1)
- Requires macOS Ventura 13.0+ for ScreenCaptureKit
- Must be distributed as a proper `.app` bundle (not a plain script) — see Gotcha #3

---

## Required Permissions
The app requires three macOS permissions, all requested at first launch with clear explanations:

1. **Screen & System Audio Recording** — for ScreenCaptureKit to capture system audio
2. **Microphone** — for capturing the user's voice via AVFoundation
3. **Accessibility** — for the global hotkey to work across all apps

Each permission triggers a native macOS prompt. The app must handle the case where any permission is denied and show a clear error state in the menu bar UI directing the user to System Settings.

---

## Dependencies
- **ScreenCaptureKit** (native macOS framework, Ventura 13+) — captures system audio with a one-time permission prompt; no virtual audio driver or manual setup required
- **whisper-mlx** (Apple Silicon optimised) or **faster-whisper** (Intel fallback) — real-time transcription
- **whisper_streaming** — wrapper that enables chunked real-time streaming on top of Whisper (see Gotcha #4)
- **Claude API** (`claude-sonnet-4-6`) — generates responses
- **ElevenLabs API** — TTS for Claude's spoken response; use streaming endpoint to reduce perceived latency

---

## Trigger Mechanism
- Push-to-talk via a **global hotkey** (configurable, default: `⌥Space`)
- Hold to speak, release to send
- Implemented via `pynput` (Python) or `CGEventTap` / `NSEvent.addGlobalMonitorForEvents` (Swift/Obj-C)
- Menu bar icon reflects current state: **idle / listening / thinking / speaking**

### Gotcha #1 — Accessibility Permission for Global Hotkey
For a global hotkey to fire when another app (e.g. Zoom) is in focus, macOS requires the app to be granted **Accessibility access** in System Settings → Privacy & Security → Accessibility. Without this, the hotkey only works when the app itself is focused, which defeats the purpose. The app must request this permission at first launch and check for it on every startup.

### Gotcha #2 — Hotkey Conflict with Meeting Apps
`⌥Space` may conflict with input method shortcuts or meeting app hotkeys (e.g. Zoom uses `⌘⇧A` for mute). The app should detect conflicts at launch and warn the user. The hotkey must be fully user-configurable.

---

## Audio Architecture

### System Audio Capture
- ScreenCaptureKit's `SCStream` with `capturesAudio = true` and `excludesCurrentProcessAudio = false` captures all system audio output (i.e., what other participants say)
- This provides a clean audio stream without needing any virtual audio driver

### Microphone Capture
- User's mic captured separately via AVFoundation (`AVCaptureSession`)
- Kept as a separate stream so Whisper can label utterances as "Me" vs "Them"

### Transcription
- Both streams are fed into **whisper_streaming** (built on faster-whisper) which processes audio in small chunks for near-real-time transcription
- Transcript is maintained as a rolling log with speaker labels: `[Me]` and `[Them]`
- On hotkey press, a marker is inserted into the transcript to identify the start of the user's query
- On hotkey release, audio since the marker is extracted as the user's utterance and sent to Claude

### Gotcha #3 — ScreenCaptureKit Requires an .app Bundle
ScreenCaptureKit will not work correctly when invoked from a plain Python script or terminal process — the permission prompt either doesn't appear or the stream silently fails. The app **must be packaged as a proper `.app` bundle** (e.g. using `py2app` for Python, or built natively in Swift). This is a hard requirement.

### Gotcha #4 — Vanilla Whisper is Not Real-Time
The base `faster-whisper` library processes audio in 30-second chunks and is not suitable for continuous live transcription. The implementation must use **whisper_streaming** (`github.com/ufal/whisper_streaming`) which implements a chunked streaming policy achieving approximately 3 second latency. Without this wrapper, the rolling transcript will lag 30+ seconds behind the conversation, making Claude's context stale.

### Gotcha #5 — Echo / Feedback Loop
When Claude's TTS response plays through the speakers, ScreenCaptureKit will capture it as system audio and feed it back into the transcript, causing Claude to "hear" its own responses and potentially respond to them. This must be mitigated by:
- Setting a boolean flag `is_speaking` when ElevenLabs audio begins playing
- Pausing ingestion of ScreenCaptureKit audio into the transcript while `is_speaking = true`
- Resuming capture after playback completes
- The mic stream should continue uninterrupted during playback so the user can speak

---

## Context Management

### Claude Request Structure
Each request to the Claude API includes:
- A **system prompt** establishing Claude's role (configurable — see Configuration)
- The **full rolling transcript** from session start as the conversation context
- The **user's current utterance** as the human turn

### Gotcha #6 — Context Window Growth
Over a long meeting, the rolling transcript will grow and may approach the Claude API context limit (~200k tokens for Sonnet). The app must:
- Track approximate token count of the transcript (rough heuristic: 1 token ≈ 4 characters)
- When approaching ~180k tokens, truncate the oldest portion of the transcript, always preserving the most recent exchanges
- Display a subtle warning in the menu bar when truncation has occurred

### Session Reset
- v1: transcript and conversation context reset on app quit
- No persistence across sessions

---

## Latency Budget
Target: under 6 seconds from hotkey release to first audio output

| Step | Expected latency |
|------|-----------------|
| Whisper transcription of user utterance | ~1–2s |
| Claude API response | ~1–2s |
| ElevenLabs first audio chunk (streaming) | ~0.5–1s |
| Audio playback start | ~0.1s |
| **Total** | **~3–5s** |

- Use ElevenLabs **streaming endpoint** — begin playing audio as first chunks arrive, do not wait for full synthesis
- Use **whisper-mlx** on Apple Silicon Macs for faster local transcription
- Whisper model default: `medium` — configurable by user (smaller = faster, less accurate)

---

## Configuration
Stored as a JSON config file at `~/.claude-meeting/config.json`. Editable via menu bar settings UI or directly.

```json
{
  "hotkey": "alt+space",
  "elevenlabs_voice_id": "...",
  "whisper_model": "medium",
  "system_prompt": "You are Claude, an AI thought partner participating in a meeting. Be concise and conversational, as your response will be spoken aloud."
}
```

### Gotcha #7 — API Key Storage
API keys must **not** be stored in plaintext in the config file (risk of accidental exposure). Store both the Anthropic and ElevenLabs API keys in the **macOS Keychain**. The menu bar settings UI should provide input fields for keys on first launch, writing them to Keychain. The config file references keys by Keychain service name only.

---

## Menu Bar UI
The app lives entirely in the menu bar with no Dock icon. The icon conveys current state:
- ⚪ **Idle** — running, building transcript, ready
- 🔴 **Listening** — hotkey held, recording user utterance
- 🟡 **Thinking** — Claude API request in flight
- 🟢 **Speaking** — ElevenLabs audio playing

Clicking the icon opens a dropdown with:
- Live transcript view (scrollable, last ~10 exchanges)
- Start / Stop session toggle
- Settings panel (hotkey, ElevenLabs voice, Whisper model size, system prompt, API keys)
- Quit

---

## Error Handling
The following failure modes must be handled gracefully and surfaced in the menu bar:

| Failure | Behaviour |
|---------|-----------|
| Permission denied (any) | Show which permission is missing; link directly to System Settings pane |
| Whisper model not downloaded | Prompt to download on first launch with progress indicator (~1.5GB for `medium`) |
| No internet / API unreachable | Show error icon in menu bar; do not crash |
| Claude API error (rate limit, timeout) | Show brief error, allow user to re-trigger |
| ElevenLabs API error | Fall back to macOS native TTS (`AVSpeechSynthesizer`) with a visible warning |
| Context window truncation | Show subtle indicator in transcript view |

---

## Installation & First-Run Flow
1. User opens `.app` bundle
2. App checks for required permissions; requests any that are missing with explanatory dialogs
3. App checks Keychain for API keys; if absent, shows a setup form
4. App checks for local Whisper model; if absent, offers to download with a progress bar
5. App enters idle state in menu bar — ready for use in a meeting

---

## Out of Scope (v1)
- Interruption handling (user speaks while Claude is mid-response)
- Persistent transcript or context across sessions
- Speaker diarization beyond simple Me / Them labeling
- Windows / Linux support
- Meeting platform SDK integration (audio capture is OS-level and platform-agnostic)
- Automatic meeting detection / auto-start

---

## Open Questions for Future Versions
- Should Claude's spoken responses also appear as text in the transcript view?
- Should transcripts optionally be saved to disk after each session?
- Should there be a way to load a pre-meeting briefing document to give Claude context before the call starts?
- Should the app auto-detect when a meeting begins (e.g. by watching for Zoom/Meet audio activity)?
- Should interruption handling (stop speaking when user presses hotkey again) be added in v2?
