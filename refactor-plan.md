# Operator — Refactor Plan

*Last updated: March 25, 2026 — For full technical detail, give `agent-context.md` to any coding agent.*

> **Current status: Phase 3 complete. Phase 4 starting.** Next: Step 4.1 — chat mode (`@operator` mentions in meeting chat).

---

## Current Status

> **Phase 2 complete and verified.** Connector interface defined, `MacOSAdapter` wrapping ScreenCaptureKit audio capture and Playwright/Chrome join. End-to-end tested live in Google Meet (March 24, 2026) — `MacOSAdapter` instantiated, Swift helper launched, meeting joined, full wake → LLM → TTS cycle confirmed.
>
> **Phase 3 in progress.** Steps 3.1–3.5 complete: pipeline imports validated on Linux, production Dockerfile built (`python:3.11-slim` + PulseAudio + Playwright/Chromium), PulseAudio virtual audio routing confirmed, STT benchmark passes (avg WER 3.3%, QEMU-adjusted latency < 1.5s on native x86_64). Next: Step 3.6 — `DockerAdapter`.

---

## Environment Setup

| # | Step | Status |
|---|------|--------|
| A | Recover secrets from old machine (.env, credentials.json, token.json) | ✅ Done |
| B | Create `.gitignore` | ✅ Done |
| C | Create `requirements.txt` | ✅ Done |
| D | Create Python venv and install dependencies | ✅ Done |
| E | Fix VS Code `.env` warning | ✅ Done |
| F | Upgrade Python 3.9 → 3.11 via Homebrew, recreate venv | ✅ Done |
| G | Recreate `browser_profile/` by signing into Operator Google account | ✅ Done |

---

## Phase -1: Pre-Validation Probes

*Must pass before any refactoring starts. Scripts are already written in `tests/`.*

| Step | Description | Status |
|------|-------------|--------|
| A.1 | Run headless Chrome probe — no stealth config (`probe_a1_headless_meet.py`) | ✅ Pass |
| A.2 | Run headless Chrome probe — with anti-detection config (`probe_a2_stealth_meet.py`) | ✅ Pass |
| B.1 | Install Docker Desktop | ✅ Pass |
| B.2 | Build minimal PulseAudio audio-test container, check Whisper accuracy | ✅ Pass |

**Decision gate:**
| Probe A | Probe B | Path forward |
|---------|---------|--------------|
| ✅ | ✅ | Proceed as planned |
| ✅ | ❌ | Use CDP audio capture instead of PulseAudio in Phase 3 |
| ❌ | ✅ | Switch to Recall.ai for meeting join; discuss before proceeding |
| ❌ | ❌ | Both contingencies apply; stop and discuss |

---

## Phase 0: Codebase Cleanup

*Goal: Remove dead files, organize what remains. No functionality changes. App should work identically before and after each step.*

| Step | Description | Status |
|------|-------------|--------|
| 0.1 | Delete completed STT benchmark files | ✅ |
| 0.2 | Delete `spec.md` (superseded by `product-strategy.md`) | ✅ |
| 0.3 | Move root-level test files into `tests/` | ✅ |
| 0.4 | Move `generate_backchannel.py` into new `scripts/` folder | ✅ |
| 0.5 | Move audio clips into new `assets/` folder, update paths in `app.py` | ✅ (backchannel clips + logic removed from scope) |

---

## Phase 1: Extract the Agent Pipeline

*Goal: Pull all "brain" logic out of `app.py` into a `pipeline/` folder with zero macOS-specific code. After this phase, the pipeline can run on any OS.*

| Step | Description | Status |
|------|-------------|--------|
| 1.1 | Create `pipeline/` package scaffold | ✅ |
| 1.2 | Extract audio processing → `pipeline/audio.py` | ✅ |
| 1.3 | Extract wake phrase detection → `pipeline/wake.py` | ✅ |
| 1.4 | Extract conversation state machine → `pipeline/conversation.py` | ✅ |
| 1.5 | Extract LLM calls → `pipeline/llm.py` | ✅ |
| 1.6 | Extract TTS → `pipeline/tts.py` (make output device a parameter, not hardcoded) | ✅ |

---

## Phase 2: Define the Connector Interface

*Goal: Define what a "meeting connector" must do, in code. Wrap the existing macOS logic behind that interface.*

| Step | Description | Status |
|------|-------------|--------|
| 2.1 | Create `connectors/` package scaffold | ✅ |
| 2.2 | Define `MeetingConnector` abstract interface → `connectors/base.py` | ✅ |
| 2.3 | Wrap macOS logic as `MacOSAdapter` → `connectors/macos_adapter.py` | ✅ |

---

## Phase 3: Docker Container Adapter

*Goal: Build a Docker adapter that runs on Linux in the cloud. Cloud provider: DigitalOcean (~$12/month, Ubuntu 22.04).*

**DigitalOcean setup (one-time, before Step 3.1):**

| Step | Description | Status |
|------|-------------|--------|
| 3.0a | Create DigitalOcean account | ✅ |
| 3.0b | Generate SSH key, add to DigitalOcean | ✅ |
| 3.0c | Create Droplet (Ubuntu 22.04, $12/mo, region closest to you) | ✅ |
| 3.0d | SSH into Droplet, install Docker | ✅ |
| 3.0e | Push code to Droplet via GitHub | ✅ |
| 3.0f | Set API keys as environment variables on Droplet | ✅ |

**Docker adapter build:**

| Step | Description | Status |
|------|-------------|--------|
| 3.1 | Validate `pipeline/` imports cleanly on Linux | ✅ |
| 3.2 | Create `docker/` folder and base Dockerfile | ✅ |
| 3.3 | *(requirements.txt already done — see Env C)* | ✅ |
| 3.4 | Set up PulseAudio virtual audio in the container | ✅ |
| 3.5 | Validate Whisper accuracy on container audio | ✅ |
| 3.6 | Implement `DockerAdapter` → `connectors/docker_adapter.py` | ✅ |
| 3.7 | Create `docker/entrypoint.py`, wire adapter to pipeline | ✅ |
| 3.8 | Build daily smoke test (`tests/test_smoke_docker.py`) | ✅ |

---

## Phase 4: Product Features

*Goal: Add capabilities beyond voice-only — chat mode, visual feedback, loadout config.*

| Step | Description | Status |
|------|-------------|--------|
| 4.1 | Chat mode: respond in meeting chat when @mentioned | ⬜ |
| 4.2 | Visual feedback: emoji reactions + chat acknowledgments | ⬜ |
| 4.3 | Loadout config file (`config.yaml`) — externalize all configuration | ⬜ |

---

## Phase 5: Setup Wizard (MVP)

*Goal: Command-line wizard so a developer can configure Operator without editing files.*

| Step | Description | Status |
|------|-------------|--------|
| 5.1 | Create `scripts/setup_wizard.py` — walks through API keys, voice, connector type | ⬜ |

---

## Key Decisions Made

- **Cloud provider:** DigitalOcean (simple, predictable pricing, great docs for non-infra users)
- **Python target:** 3.11 (current 3.9 is EOL; Docker container will run 3.11)
- **Wake word:** Whisper-based inline detection (Porcupine was removed — the `.ppn` file is discarded)
- **STT:** faster-whisper (benchmark complete, decision locked)
- **LLM:** GPT-4.1-mini
- **TTS:** ElevenLabs
- **If Probe A fails:** Fall back to Recall.ai API (handles meeting join on their infrastructure)
- **If Probe B fails:** Use CDP (Chrome DevTools Protocol) to capture audio directly from browser

## Open Questions

1. **Calendar secrets in Docker** — `credentials.json` and `token.json` need to be passed as env vars or mounted secrets in a container. How to handle?
2. **Multi-meeting concurrency** — each meeting runs in its own container. What orchestrates spinning them up/down?
3. **Wake phrase customization** — works as long as Whisper transcribes reliably. Test before committing to the feature.
4. **Licensing** — MIT vs. Apache 2.0 for open-source release.
