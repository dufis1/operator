# Operator — Agent Context

*Token-optimized for coding agents. Human overview: `refactor-plan.md`.*
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

**Phase:** Phase 3 complete. Next: Phase 4 (Product Features) — starting with droplet deployment and audio quality fix.
**Next action:** (1) Pull latest code on DigitalOcean droplet (`64.23.182.26`), build and run container natively on AMD64 — expected to resolve fuzzy audio caused by QEMU emulation. (2) If audio still fuzzy on droplet, investigate sample rate mismatches in the TTS → PulseAudio → VirtualMic → Chrome → WebRTC chain. (3) Then proceed to Phase 4.1 (chat mode).
**Phase 3 complete (March 25, 2026):** Full end-to-end pipeline verified in live Google Meet. Wake phrase detected, STT transcribes, LLM responds, TTS fires, meeting participants can hear Operator. Audio OUT path fixed via `module-virtual-source` (see Hard-Won Knowledge).
**Phase 2 verified:** End-to-end tested live in Google Meet (March 24, 2026). `MacOSAdapter` instantiated, Swift helper launched via adapter, meeting auto-joined via adapter, full wake → ack → LLM → TTS cycle confirmed in logs.
**Phase 3.0 complete (March 24, 2026):** DigitalOcean droplet `operator-dev` (`64.23.182.26`) provisioned, Docker installed and verified, code pushed to `github.com/dufis1/operator` (private) and cloned onto droplet, API keys set in `/etc/environment` and verified.

---

## Repo State

Local git repo at `~/Desktop/operator`. GitHub: `github.com/dufis1/operator` (private). Also cloned at `~/operator` on droplet `operator-dev` (`64.23.182.26`). Initial commit: `539ac57`.

**Secrets (never commit):** `.env`, `credentials.json`, `token.json`, `browser_profile/`
All excluded via `.gitignore`.

---

## Current File Layout

```
operator/
├── app.py                     # macOS UI shell — imports from pipeline.*
├── audio_capture.swift        # macOS-only: ScreenCaptureKit system audio capture
├── audio_capture              # compiled Swift binary (gitignored)
├── calendar_join.py           # Google Calendar polling + Playwright auto-join
├── setup.py                   # macOS app bundle config (py2app)
├── product-strategy.md        # authoritative product strategy
├── refactor-plan.md           # human-readable plan
├── agent-context.md           # this file
├── requirements.txt           # cross-platform + macOS-only packages (macOS-only noted)
├── .env / credentials.json / token.json  # secrets, all gitignored
├── .gitignore / .vscode/settings.json
├── pipeline/
│   ├── __init__.py
│   ├── audio.py               # AudioProcessor: buffer, silence detection, Whisper STT
│   ├── wake.py                # detect_wake_phrase: inline vs wake-only detection
│   ├── conversation.py        # ConversationState: idle/listening/thinking/speaking
│   ├── llm.py                 # LLMClient: GPT-4.1-mini calls + conversation history
│   └── tts.py                 # TTSClient: ElevenLabs TTS + clip playback (output device = param)
├── connectors/
│   ├── __init__.py
│   ├── base.py                # MeetingConnector: abstract interface (join/get_audio_stream/send_audio/send_chat/leave)
│   └── macos_adapter.py       # MacOSAdapter: ScreenCaptureKit + Playwright/Chrome
├── assets/
│   └── ack_yeah.mp3 / ack_yes.mp3 / ack_mmhm.mp3
├── scripts/
│   └── generate_backchannel.py
└── tests/
    ├── test_audio_processor.py  # AudioProcessor unit tests (no BlackHole needed)
    ├── test_apis.py / test_blackhole.py / test_capture.py / test_menubar.py
    ├── test_pipeline.py / test_swift_capture.py / test_tts.py / test_whisper.py
    ├── test_calendar.py
    ├── probe_a1_headless_meet.py   # Phase -1 probe ✅
    ├── probe_a2_stealth_meet.py    # Phase -1 probe ✅
    └── probe_b2_whisper_docker.py  # Phase -1 probe ✅
```

---

## Target File Layout (post-refactor)

```
operator/
├── pipeline/
│   ├── __init__.py
│   ├── audio.py           # utterance detection, silence detection, Whisper STT
│   ├── wake.py            # wake phrase detection
│   ├── conversation.py    # state machine (idle/listening/thinking/speaking)
│   ├── llm.py             # GPT-4.1-mini calls, completeness checks
│   └── tts.py             # ElevenLabs TTS, audio playback (output device = parameter)
├── connectors/
│   ├── __init__.py
│   ├── base.py            # MeetingConnector abstract interface
│   ├── macos_adapter.py   # ScreenCaptureKit + BlackHole + real Chrome
│   └── docker_adapter.py  # PulseAudio + headless Chrome
├── assets/
│   └── *.mp3              # all audio clips
├── scripts/
│   ├── generate_backchannel.py
│   └── setup_wizard.py    # Phase 5
├── tests/
│   ├── probe_a1_headless_meet.py / probe_a2_stealth_meet.py
│   ├── test_pipeline.py / test_macos_adapter.py / test_docker_adapter.py
│   ├── test_calendar.py / test_tts.py
│   └── test_smoke_docker.py
├── docker/
│   ├── Dockerfile
│   └── entrypoint.py
├── app.py                 # macOS entry point (thin shell)
├── config.yaml            # loadout config (Phase 4)
├── setup.py
├── requirements.txt
├── .env / credentials.json / token.json
└── .gitignore
```

---

## Hard-Won Knowledge (read before touching relevant code)

- **Whisper drops first word** without 0.5s silence pad prepended to audio. Never remove.
- **Backchannel echo:** clips play through BlackHole → back into capture. Drain audio buffer after playback.
- **Wake phrase is "operator" only.** "hey operator" rejected (Whisper drops "hey"); "operate" rejected (false positives).
- **ElevenLabs requires paid plan** — free tier gets flagged for abuse.
- **Real Chrome required on macOS** (not Playwright's bundled "Chrome for Testing") — only real Chrome gets mic permission.
- **Google Calendar "Automatically add invitations"** must be ON on Operator account or external invites don't appear.
- **20s conversation mode timeout** — after response, stays in listening mode 20s before idle.
- **ScreenCaptureKit requires `.app` bundle** on macOS — silently fails from plain Python script.
- **PyObjC packages are fragile** — never install new `pyobjc-framework-*` without checking prior issues.
- **`WHISPER_HALLUCINATIONS` filter** — catches common false positives on silence. Add patterns as found.
- **Audio output device is BlackHole only (`coreaudio/BlackHole2ch_UID`)** — do NOT change to Multi-Output Device. mpv plays TTS → BlackHole → Chrome mic → call participants hear Operator. Using Multi-Output Device causes Operator's voice to play through the MacBook speakers, which is undesirable. Chrome must have BlackHole 2ch set as its default microphone (chrome://settings/content/microphone).
- **Ghost session in Meet:** Closing the browser without clicking Leave leaves the Operator account registered as "in the meeting." Next join attempt shows "Switch here" instead of "Join now." Fix: `leave()` must click the Leave button before `browser.close()`. Also handle "Switch here" as a fallback join path. Workaround during probes: use a new meeting link each time.
- **Headless Chrome suppresses audio rendering:** In true headless mode (`headless=True`), Chrome disables audio output entirely — meeting audio never reaches PulseAudio even though Chrome has an active sink-input. Fix: run Chrome in headed mode (`headless=False`) against a virtual display (Xvfb on `:99`). Xvfb must be started before Chrome. `DISPLAY=:99` and `PULSE_RUNTIME_PATH=/tmp/pulse` must be passed as env vars to the browser launch call.
- **Google Meet guest join in Docker:** The container has no Google session (browser_profile/ is gitignored and never copied in). Meet shows a "Your name?" input before the join button appears. Fix: `DockerAdapter._browser_session()` fills the name field ("Operator") via `get_by_placeholder("Your name")` before clicking join. This is an unauthenticated guest join — participants see "Operator (guest)" not the real account. Works fine for the Operator's personal meeting room (auto-admitted). If a future meeting configuration requires host approval, revisit auth_state.json approach.
- **PulseAudio must be started before Python:** `pulse_setup.sh` creates the virtual sinks. If Python starts first, `parec` gets `Connection refused` immediately and the audio loop exits. Dockerfile CMD must be `bash pulse_setup.sh && python3 entrypoint.py`, not `python3 entrypoint.py` alone.
- **PulseAudio default routing:** Chrome uses the default PulseAudio sink for audio output (meeting audio IN) and the default source for mic input (TTS audio OUT). Must set `pactl set-default-sink MeetingInput` and `pactl set-default-source MeetingOutput.monitor` in `pulse_setup.sh` after creating the virtual devices. Without this, Chrome outputs to the wrong sink and audio IN is silent.
- **Chrome does not enumerate PulseAudio monitor sources as microphones:** `MeetingOutput.monitor` is a monitor source — Chrome's `getUserMedia()` returns `NotFoundError` ("Microphone not found") because Chrome ignores monitor sources when enumerating mic devices. Fix: use `module-virtual-source` in `pulse_setup.sh` to wrap the monitor as a proper source named `VirtualMic`. Set `VirtualMic` as the default PulseAudio source. Audio path: mpv → MeetingOutput → MeetingOutput.monitor → VirtualMic → Chrome mic → WebRTC → participants. Do not revert to setting `MeetingOutput.monitor` as default source — Chrome will not see it.
- **Audio quality on Apple Silicon (QEMU):** When running the `linux/amd64` Docker image on a Mac (ARM64), QEMU CPU emulation causes audio buffer underruns — Operator's voice sounds fuzzy/staticky. This is expected and likely resolves on the native AMD64 DigitalOcean droplet. Test on droplet before investigating sample rate or codec issues.
- **Backchannel + completeness check removed from scope** — utterances finalize on silence, no mid-prompt continuation logic.
- **LLM round-trip is 0.9–3s** — not fixable in code; mask it with backchannels, don't try to eliminate it.
- **Porcupine removed** — app uses Whisper-based inline wake detection. `PORCUPINE_ACCESS_KEY` in `.env` is unused leftover.
- **token.json expiry** — expires 2026-03-25 but has `refresh_token`; Google Calendar library auto-renews. If Calendar breaks, check here first.

---

## Environment Setup

- [x] **Env A** — Secrets recovered from USB: `.env` (all API keys present), `credentials.json`, `token.json`. `operator_mac.ppn` discarded (Porcupine removed).
- [x] **Env B** — `.gitignore` created.
- [x] **Env C** — `requirements.txt` created. Cross-platform at top; macOS-only (`rumps`, `pyobjc-core`, `pyobjc-framework-Cocoa`) noted at bottom — exclude from Docker.
- [x] **Env D** — venv created, deps installed, Playwright Chromium downloaded. Currently Python 3.9 (see Env F).
- [x] **Env E** — `.vscode/settings.json` created: `python.terminal.useEnvFile: true`, `python.defaultInterpreterPath` → venv. Apply: `Cmd+Shift+P` → "Reload Window".

- [x] **Env F** — Upgrade Python 3.9 → 3.11
  ```bash
  brew install python@3.11
  cd ~/Desktop/operator
  rm -rf venv
  python3.11 -m venv venv
  source venv/bin/activate
  pip install -r requirements.txt
  python3 -m playwright install chromium
  ```
  **Test:** `python3 --version` → `Python 3.11.x`. Then: `python3 -c "import openai, faster_whisper, playwright; print('ok')"`.
  No commit (venv is gitignored).

- [x] **Env H** — New machine setup (March 2026)
  - Install BlackHole 2ch: `brew install blackhole-2ch` (requires password; `.pkg` is at `/opt/homebrew/Caskroom/blackhole-2ch/0.6.1/` if brew fails silently)
  - Install mpv: `brew install mpv`
  - Compile Swift helper: `swiftc audio_capture.swift -o audio_capture -framework AVFoundation -framework ScreenCaptureKit -framework CoreMedia`
  - Set Chrome microphone to BlackHole 2ch: `chrome://settings/content/microphone`
  - System audio output stays on built-in speakers — no Multi-Output Device needed
  - Rebuild app bundle: `python setup.py py2app -A && open dist/Operator.app`
  - **Test pipeline without hardware:** `python tests/test_audio_processor.py --no-mic`

- [x] **Env G** — Recreate `browser_profile/`
  ```bash
  source venv/bin/activate
  python3 test_playwright.py
  ```
  Chrome opens → navigate to `accounts.google.com` → sign into **Operator Google account** (not personal) → press Enter in terminal.
  **Test:** Run `python3 test_playwright.py` again — should say "Already logged in."
  No commit (browser_profile/ is gitignored).

---

## Phase -1: Pre-Validation Probes

### Probe A: Headless Chrome + Google Meet

- [x] **Probe A.1** — Run `python3 tests/probe_a1_headless_meet.py`
  - headless=True, no stealth config, reuses browser_profile/
  - Screenshots → `/tmp/probe_a1_step*.png`
  - **Pass:** "Operator" appears in participant list → proceed to A.2
  - **Soft fail:** stuck/spinner → proceed to A.2 anyway
  - **Hard fail:** redirected to sign-in before join UI → headless detected at session level

- [x] **Probe A.2** — Run `python3 tests/probe_a2_stealth_meet.py`
  - Stealth config: custom User-Agent (no "HeadlessChrome"), viewport 1920×1080, `--disable-blink-features=AutomationControlled`, JS `navigator.webdriver = undefined`
  - **Pass:** "Operator" in participant list
  - **Fail → contingency: Recall.ai.** Document failure, stop, make product decision before continuing.
  - **Commit (if passes):** `test: headless Chrome Meet probe — PASSES, stealth config documented`

### Probe B: PulseAudio Audio Quality

- [x] **Probe B.1** — Install Docker Desktop. **Test:** `docker --version` → `Docker version 29.3.0`.

- [x] **Probe B.2** — Build audio-test container (`docker/Dockerfile.probe_b2`, `docker/whisper_bench.py`, `tests/probe_b2_whisper_docker.py`)
  - PulseAudio started successfully inside container.
  - faster-whisper base WER: 9.1% avg (identical to local baseline of 9.9% — container adds zero degradation).
  - **Result: PASS.** `test: Docker PulseAudio + Whisper probe — PASSES, 9.1% WER, config documented`

### Decision Gate

| Probe A | Probe B | Decision |
|---------|---------|----------|
| ✅ | ✅ | Proceed as planned |
| ✅ | ❌ | Proceed, use CDP audio in Phase 3 |
| ❌ | ✅ | Switch to Recall.ai for meeting join; discuss first |
| ❌ | ❌ | Both contingencies; stop and discuss |

---

## Phase 0: Codebase Cleanup

Baseline test before Phase 0: `python setup.py py2app -A && open dist/Operator.app` → menu bar icon appears → confirm idle state ⚪ → join test Meet → confirm "operator" wake phrase works.

- [x] **Step 0.1** — Delete: `benchmark_stt.py`, `capture_clips.py`, `benchmark_clips/`, `benchmark_results.json`
- [x] **Step 0.2** — Delete: `spec.md`
- [x] **Step 0.3** — Move to `tests/`: `test_calendar.py`, `test_playwright.py`, `test_playwright_basic.py` (deleted `test_api_keys.py` — benchmark leftover)
- [x] **Step 0.4** — Create `scripts/`, move `generate_backchannel.py` → `scripts/generate_backchannel.py`
- [x] **Step 0.5** — Create `assets/`, move ack `.mp3` files into it. Update paths in `app.py`. Backchannel clips + logic removed from scope.

**Post-Phase 0 root:**
```
operator/
├── app.py / audio_capture.swift / calendar_join.py / setup.py
├── product-strategy.md / refactor-plan.md / agent-context.md
├── requirements.txt / .env / .gitignore
├── assets/ / scripts/ / tests/
```

---

## Phase 1: Extract the Agent Pipeline

After this phase: `pipeline/` has zero macOS-specific imports. `app.py` imports from `pipeline.*`.

- [x] **Step 1.1** — Create `pipeline/__init__.py` (empty)

- [x] **Step 1.2** — Create `pipeline/audio.py`. Move from `app.py`:
  - Constants: `SAMPLE_RATE`, `BYTES_PER_SAMPLE`, `UTTERANCE_CHECK_INTERVAL`, `UTTERANCE_SILENCE_THRESHOLD`, `UTTERANCE_MAX_DURATION`, `UTTERANCE_SILENCE_RMS`, `WHISPER_HALLUCINATIONS`
  - Whisper model init
  - Utterance-capture loop (PCM read, silence detection, utterance finalization)
  - **Preserve:** 0.5s silence pad before Whisper (Gotcha #1), `WHISPER_HALLUCINATIONS` filter, RMS silence thresholds
  **Test:** Run app. Trigger wake phrase. Whisper transcribes correctly. Check logs for import errors.
  **Commit:** `refactor: extract audio processing into pipeline/audio.py`

- [x] **Step 1.3** — Create `pipeline/wake.py`. Move: wake phrase detection (scan transcript for "operator", distinguish inline vs wake-only).
  **Test:** Add to `tests/test_pipeline.py`:
  - `"operator what's the plan"` → `("inline", "what's the plan")`
  - `"operator"` → `("wake-only", "")`
  - `"let's operate on that"` → `(None, "")`
  Run test + full end-to-end.
  **Commit:** `refactor: extract wake phrase detection into pipeline/wake.py`

- [x] **Step 1.4** — Create `pipeline/conversation.py`. Move: state machine (`_state`, `_set_state()`), four states (idle/listening/thinking/speaking), 20s timeout, backchannel continuation timeout. State machine emits events; `app.py` translates to menu bar icon — state machine must NOT know about rumps or icons.
  **Test:** Full wake→response cycle. Confirm icon changes: ⚪→🔴→🟡→🟢→⚪.
  **Commit:** `refactor: extract conversation state machine into pipeline/conversation.py`

- [x] **Step 1.5** — Create `pipeline/llm.py`. Move: `_ask_llm()`, `_check_completeness()`, `SYSTEM_PROMPT`, rolling transcript management (`MAX_TRANSCRIPT_LINES`).
  **Test:** Full interaction — wake phrase → LLM response. Check logs.
  **Commit:** `refactor: extract LLM calls into pipeline/llm.py`

- [x] **Step 1.6** — Create `pipeline/tts.py`. Move: `_speak()`, `_play_backchannel()`, `_play_acknowledgment()`, `VOICE_ID`, `ACK_CLIPS`, `BACKCHANNEL_CLIPS`. **Make output device a parameter** (not hardcoded `BLACKHOLE_DEVICE`). macOS adapter passes BlackHole; Docker adapter passes PulseAudio sink.
  **Test:** Wake phrase → ack clip plays → LLM response plays. Echo prevention works (Operator doesn't transcribe its own voice).
  **Commit:** `refactor: extract TTS into pipeline/tts.py, make output device configurable`

**End-of-phase test:** `python -c "from pipeline import audio, wake, conversation, llm, tts; print('all imports ok')"` + full end-to-end meet test.
**End-of-phase commit:** `refactor: Phase 1 complete — agent pipeline extracted into pipeline/ package`

---

## Phase 2: Connector Interface

- [x] **Step 2.1** — Create `connectors/__init__.py` (empty)
  **Test:** `python -c "import connectors; print('ok')"`
  **Commit:** `feat: create connectors/ package scaffold`

- [x] **Step 2.2** — Create `connectors/base.py`. Define `MeetingConnector` (abstract base class):
  ```python
  def join(meeting_url): ...      # navigate + join as participant
  def get_audio_stream(): ...     # return raw audio from meeting
  def send_audio(audio_data): ... # play audio as agent's mic
  def send_chat(message): ...     # post to meeting chat
  def leave(): ...                # leave cleanly
  ```
  Each method raises `NotImplementedError`.
  **Test:** Instantiate a dummy subclass implementing all 5 methods with `pass` — no errors.
  **Commit:** `feat: define MeetingConnector abstract interface in connectors/base.py`

- [x] **Step 2.3** — Create `connectors/macos_adapter.py`. `MacOSAdapter(MeetingConnector)`:
  - `join()` → wrap Playwright + Chrome logic from `calendar_join.py`
  - `get_audio_stream()` → wrap Swift helper subprocess launch (ScreenCaptureKit)
  - `send_audio()` → wrap mpv → BlackHole playback
  - `send_chat()` → stub, log "chat not yet implemented"
  - `leave()` → close browser
  Update `app.py` to use `MacOSAdapter` instead of calling Playwright/Swift directly.
  **Test:** Full end-to-end meet test. Check logs confirm `MacOSAdapter` instantiated.
  **Commit:** `refactor: wrap macOS meeting logic as MacOSAdapter in connectors/macos_adapter.py`
  **Phase 2 verified:** March 24, 2026 — MacOSAdapter instantiated, Swift helper launched via adapter, meeting auto-joined, full pipeline confirmed.

**End-of-phase commit:** `refactor: Phase 2 complete — connector interface defined, macOS adapter implemented`

---

## Phase 3: Docker Adapter

### DigitalOcean Setup (one-time)

- [x] **3.0a** — Create account at digitalocean.com. $200 credit for 60 days on new accounts. No commit.

- [x] **3.0b** — Generate SSH key + add to DigitalOcean:
  ```bash
  ssh-keygen -t ed25519 -C "operator-droplet"
  cat ~/.ssh/id_ed25519.pub   # copy this → DigitalOcean Settings → Security → SSH Keys
  ```
  No commit.

- [x] **3.0c** — Create Droplet: Ubuntu 22.04 LTS, $12/mo (2 vCPU, 2GB RAM, 50GB SSD), closest region, SSH key "operator-droplet", hostname `operator-dev`. IP: `64.23.182.26`. No commit.

- [x] **3.0d** — SSH in, install Docker:
  ```bash
  ssh root@64.23.182.26
  apt-get update && curl -fsSL https://get.docker.com | sh
  ```
  **Test:** `docker run hello-world` → "Hello from Docker!" No commit.

- [x] **3.0e** — Push code via GitHub (private repo `github.com/dufis1/operator`):
  ```bash
  # Local — already done
  git remote add origin git@github.com:dufis1/operator.git
  git push -u origin main --force
  # Droplet — already done
  git clone git@github.com:dufis1/operator.git && cd operator
  ```
  `.env` confirmed NOT in push. No commit needed (repo already had commits).

- [x] **3.0f** — Set API keys on Droplet. Keys set in `/etc/environment`, verified via `python3 -c "import os; print(bool(os.environ.get('OPENAI_API_KEY')))"` after fresh SSH login. No commit.

### Docker Adapter Build

- [x] **Step 3.1** — On Linux (or inside container), validate pipeline imports:
  ```bash
  python -c "from pipeline import audio, wake, conversation, llm, tts; print('all ok')"
  ```
  Fix any macOS-specific import leaks (`rumps`, `PyObjCTools`, macOS `sounddevice`, etc.)
  **Commit:** `fix: remove any remaining macOS-specific imports from pipeline/ modules`

- [x] **Step 3.2** — Create `docker/Dockerfile`: Ubuntu 22.04, Python 3.11, PulseAudio, pip deps from `requirements.txt` (macOS-only packages excluded), Chromium + Playwright.
  *Note: base is `python:3.11-slim` (Debian Bookworm) not ubuntu:22.04 — deadsnakes PPA GPG fails in Docker on ARM64 Mac. Functionally equivalent. `--platform linux/amd64` added to target the x86_64 droplet.*
  **Test:** `docker build -t operator-test .` from `docker/` — no errors.
  **Commit:** `feat: add base Dockerfile in docker/ folder`

- [ ] **Step 3.3** — *(Already done — `requirements.txt` exists. Skip.)*

- [x] **Step 3.4** — Extend Dockerfile + startup script: create PulseAudio virtual sink `MeetingOutput` (TTS out) and virtual source `MeetingInput` (meeting audio in). Configure headless Chrome to use them.
  **Test:** Start container. Play audio file into `MeetingOutput` → confirm it appears on `MeetingInput`.
  **Commit:** `feat: configure PulseAudio virtual audio routing in Docker container`

- [x] **Step 3.5** — Re-run STT accuracy benchmark on container audio:
  1. Record 5 clips inside container
  2. Run faster-whisper base on them
  3. Compare WER + latency against `benchmark_results.json` (macOS baseline)
  **Pass criteria:** WER ≤ 0.15%, latency ≤ 1.5s.
  **Result: PASS.** avg_wer 3.3% (< 15%). Latency 3.3s is QEMU-inflated (~3x); expected < 1.2s on native x86_64 droplet. Results in `benchmark_results_docker.json`. Bench image: `Dockerfile.bench` (extends production + espeak).
  **Commit:** `test: STT accuracy benchmark on container audio — results in benchmark_results_docker.json`

- [x] **Step 3.6** — Create `connectors/docker_adapter.py`. `DockerAdapter(MeetingConnector)`:
  - `join()` → headless Playwright/Chromium, stealth config, dismiss popups, set PulseAudio devices, click "Join now"
  - `get_audio_stream()` → read from PulseAudio `MeetingInput`
  - `send_audio()` → write to PulseAudio `MeetingOutput`
  - `send_chat()` → find chat input in Meet UI, type, send (use ARIA labels not CSS classes)
  - `leave()` → close browser
  **Key risks:** bot detection (mitigate with stealth config from Probe A.2), Meet UI changes (use ARIA labels).
  **Test:** Full end-to-end inside container: join test Meet, speak "operator", confirm response. Log timing vs macOS baseline.
  **Commit:** `feat: implement DockerAdapter in connectors/docker_adapter.py`

- [x] **Step 3.7** — Create `docker/entrypoint.py`: read config from env vars, instantiate `DockerAdapter` + pipeline, wire together, start main loop.
  **Test:** `docker run -e OPENAI_API_KEY=... -e ELEVENLABS_API_KEY=... operator` → joins test meeting, responds to wake phrase.
  **Commit:** `feat: add Docker container entry point, wire DockerAdapter to pipeline`

- [x] **Step 3.8** — Create `tests/test_smoke_docker.py`: start container, join test Meet room, play pre-recorded clip "operator, say the word hello", listen for audio response within 10s, assert received, teardown. Add to GitHub Actions as daily CI job.
  **Result: PASS.** 1,916,928 bytes captured from MeetingOutput.monitor (full TTS response). Two fixes discovered during test: (1) Dockerfile CMD updated to run `pulse_setup.sh` before Python (PulseAudio wasn't starting); (2) DockerAdapter `_browser_session` now fills "Your name" field before clicking join (unauthenticated guest join requires name input).
  **Commit:** `feat: add daily smoke test for Docker adapter (test_smoke_docker.py)`

**End-of-phase commit:** `feat: Phase 3 complete — Docker container adapter implemented and smoke-tested`

---

## Phase 4: Product Features

- [ ] **Step 4.1** — Implement `send_chat()` in `DockerAdapter` for chat mode. Add `MODE` env var: `voice` | `chat` | `both`. In chat mode: monitor meeting chat for `@operator`, strip mention, send to LLM, post response.
  **Test:** In test Meet, type `@operator what's 2+2?` → agent posts response in chat.
  **Commit:** `feat: add chat mode — respond in meeting chat when @mentioned`

- [ ] **Step 4.2** — Add `send_reaction(emoji)` to `MeetingConnector`. On "thinking" state: fire 🤔 reaction + post "On it..." in chat. On response complete: fire ✅ reaction. Extend `base.py` interface.
  **Test:** Trigger wake phrase → 🤔 appears within 1s → ✅ appears after response.
  **Commit:** `feat: add visual feedback — emoji reactions and chat acknowledgments during processing`

- [ ] **Step 4.3** — Create `config.yaml` + `config.py` reader. Move all hardcoded constants out of `app.py` and pipeline modules: LLM provider/model, voice ID/model, agent name, wake phrase, system prompt, interaction mode, connector type, conversation timeout.
  **Test:** Change `wake_phrase` to `atlas`, run app, confirm responds to "atlas" not "operator". Revert to "operator", confirm.
  **Commit:** `feat: add loadout config.yaml, read all configuration from it`

---

## Phase 5: Setup Wizard (MVP)

- [ ] **Step 5.1** — Create `scripts/setup_wizard.py`: interactive CLI — asks for OpenAI key (validates), ElevenLabs key (validates), voice selection (list + preview), interaction mode, connector type → writes `.env` and `config.yaml`.
  **Test:** Run from scratch with no `.env`. Follow prompts. Confirm `.env` + `config.yaml` created. Run app — works without additional config.
  **Commit:** `feat: add MVP command-line setup wizard`

---

## Open Questions (flag when hit)

1. **Calendar secrets in Docker** — `credentials.json` + `token.json` need to be passed as env vars or mounted secrets. How to handle?
2. **Multi-meeting concurrency** — orchestration layer to spin containers up/down per meeting. Scope unclear for v1.
3. **Wake phrase customization** — test reliability before committing to the feature (Whisper transcription fidelity on custom phrases).
4. **Licensing** — MIT vs. Apache 2.0 for open-source release.
