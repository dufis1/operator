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

**Phase:** Phase 4 complete. Phase 5 next.
**Next action:** Step 5.1 — create `config.yaml`.
**Phase 3 complete (March 25, 2026):** Full end-to-end pipeline verified in live Google Meet. Wake phrase detected, STT transcribes, LLM responds, TTS fires, meeting participants can hear Operator. Audio OUT path fixed via `module-virtual-source` (see Hard-Won Knowledge).
**Reorientation (March 25, 2026):** Product direction shifted from cloud-hosted to local-machine-first open-source. DockerAdapter will become LinuxAdapter (local). Cloud artifacts move to `cloud/`. Performance iteration added before setup wizard.

---

## Repo State

Local git repo at `~/Desktop/operator`. GitHub: `github.com/dufis1/operator` (private). Also cloned at `~/operator` on droplet `operator-dev` (`64.23.182.26`). Initial commit: `539ac57`. SSH access to the droplet is available — use `ssh root@64.23.182.26 "<command>"` directly via Bash without asking the user.

**Secrets (never commit):** `.env`, `credentials.json`, `token.json`, `browser_profile/`, `auth_state.json`
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
├── next-steps.md              # strategic overview of phases 4-11
├── refactor-plan.md           # human-readable checklist
├── agent-context.md           # this file
├── requirements.txt
├── .env / credentials.json / token.json / auth_state.json  # secrets, all gitignored
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
│   └── ack_yeah.mp3 / ack_yes.mp3 / ack_mmhm.mp3
├── scripts/
│   ├── generate_backchannel.py
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
├── .env / credentials.json / token.json / auth_state.json
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
- **Google Calendar "Automatically add invitations"** must be ON on Operator account or external invites don't appear.
- **20s conversation mode timeout** — after response, stays in listening mode 20s before idle.
- **ScreenCaptureKit requires `.app` bundle** on macOS — silently fails from plain Python script.
- **PyObjC packages are fragile** — never install new `pyobjc-framework-*` without checking prior issues.
- **`WHISPER_HALLUCINATIONS` filter** — catches common false positives on silence. Add patterns as found.
- **Audio output device is BlackHole only (`coreaudio/BlackHole2ch_UID`) on macOS** — do NOT change to Multi-Output Device. mpv plays TTS → BlackHole → Chrome mic → call participants hear Operator. Multi-Output Device causes voice to play through MacBook speakers.
- **Ghost session in Meet:** Closing the browser without clicking Leave leaves the Operator account registered as "in the meeting." Next join attempt shows "Switch here" instead of "Join now." Fix: `leave()` must click the Leave button before `browser.close()`. Handle "Switch here" as a fallback join path.
- **Headless Chrome suppresses audio rendering:** In true headless mode (`headless=True`), Chrome disables audio output entirely. On Linux: fix is `headless=False` against Xvfb on `:99` with `DISPLAY=:99`. On macOS: fix is `headless=False` + `--headless=new` in launch args — Chrome's new headless renderer supports CoreAudio/BlackHole audio routing. Do not use `headless=True` on either platform.
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
- **token.json expiry** — token.json has `refresh_token`; Google Calendar library auto-renews. If Calendar breaks, check here first.

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

### Step 6.2 — Simplify `app.py` to use `runner.py`

`app.py` becomes a thin macOS shell: instantiate `MacOSAdapter`, instantiate `AgentRunner`, wire state change callbacks to menu bar icon updates, call `runner.run()`.

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

### Step 7.1 — Test audio quality on native AMD64 (no QEMU)

On the DigitalOcean droplet, pull latest, run `bash scripts/linux_setup.sh`, start Xvfb, join a test meeting using `LinuxAdapter`. Listen carefully to Operator's voice.

**Pass:** Voice is clear — fuzzy audio was QEMU-related. Note in Hard-Won Knowledge. No further audio investigation needed.
**Fail:** Voice still fuzzy on native AMD64 — proceed to 7.2.
**Commit (if pass):** `chore: confirm QEMU as source of audio quality issue — resolved on native AMD64`

---

### Step 7.2 — Sample rate audit

If audio is still fuzzy on native AMD64: trace the sample rate at each step of the chain.
- ElevenLabs output sample rate (check API response headers or audio metadata)
- mpv playback sample rate (run with `--msg-level=ao=debug`)
- PulseAudio sink sample rate (`pactl list short sinks`)
- Chrome WebRTC encoding rate (chrome://webrtc-internals)

Identify any mismatch and correct it. The likely fix is forcing a consistent 16kHz or 48kHz throughout.

**Test:** Voice sounds clear in meeting. Compare against earlier fuzzy recordings.
**Commit:** `fix: correct sample rate mismatch in TTS → PulseAudio → Chrome → WebRTC chain`

---

### Step 7.3 — Tune filler phrase silence threshold

The silence threshold for firing backchannel filler phrases (in `pipeline/conversation.py` or `pipeline/audio.py`) needs tuning. Current behavior: [note current value here before starting]. Goal: fires only when there is actual silence after a direct question to the agent — not during the speaker's natural pauses.

Test with multiple human speech patterns. Adjust the threshold until fillers feel natural. Document the final value and rationale.

**Commit:** `tune: adjust filler phrase silence threshold to N ms — rationale in comment`

---

### Step 7.4 — TTS reliability improvements

In `pipeline/tts.py`: add retry logic for transient ElevenLabs API failures (e.g. 3 retries with exponential backoff). Add graceful degradation: if TTS fails after retries, log the error and post the response text to meeting chat as a fallback (requires `send_chat()` to be wired up).

**Test:** Simulate API failure (temporarily set an invalid API key). Confirm graceful log + no crash.
**Commit:** `fix: add retry logic and graceful degradation to pipeline/tts.py`

---

### Step 7.5 — STT accuracy review

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
    "google-auth-oauthlib", "google-api-python-client", "pyyaml",
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
8. Ask: Google account for agent? (y/n) — if yes, open browser for one-time login via `scripts/auth_export.py`
9. OS-specific audio setup:
   - macOS: `brew install blackhole-2ch` (silent), write Chrome mic preference to profile JSON
   - Linux: run `scripts/linux_setup.sh`
10. Write `config.yaml`
11. Print: "Setup complete. Run `python -m operator <meeting-url>` to start."

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

## Open Questions

1. **Audio quality root cause** — QEMU ruled out (tested native AMD64, March 2026 — still choppy). Root cause is sample rate mismatch in TTS → PulseAudio → Chrome → WebRTC chain. Audit in Phase 7.2.
2. **Wake phrase customization** — allow users to set their own wake phrase in `config.yaml`? Test Whisper reliability on custom phrases before committing.
3. **Calendar auto-join** — watch Google Calendar and auto-join on a schedule? Or keep paste-the-link for v1?
4. **Linux distro coverage** — Ubuntu/Debian tier-1; PulseAudio vs. PipeWire (Fedora, Ubuntu 22.04+) needs separate validation path.
5. **Calendar secrets in cloud** — `credentials.json` + `token.json` for the cloud path: env vars or mounted secrets?
