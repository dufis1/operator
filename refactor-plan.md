# Operator — Refactor Plan

*Human-readable checklist. For technical detail and step-by-step instructions, give `agent-context.md` to a coding agent. For strategic rationale, see `next-steps.md`.*

*Last updated: March 28, 2026*

> **Current status: Phase 7 in progress — Step 7.6 complete + TCC hardening done + recovery ladder tests written.** Diagnosed Google session revocation in browser profile (`.google.com` SID/HSID/SSID cookies removed server-side while headless Chrome couldn't complete re-auth challenge). Planned session recovery ladder: detect logged-out state post-navigation, auto-inject cookies from `auth_state.json`, signal join status to runner via `JoinStatus` threading primitive, add in-meeting health checks. Plan written, ready for implementation. Next: implement session recovery ladder, then live meeting test for filler phrases.

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
| H | New machine setup (BlackHole, mpv, Swift helper, app bundle) | ✅ Done |

---

## Phase -1: Pre-Validation Probes ✅

| Step | Description | Status |
|------|-------------|--------|
| A.1 | Headless Chrome probe — no stealth config | ✅ Pass |
| A.2 | Headless Chrome probe — with anti-detection config | ✅ Pass |
| B.1 | Install Docker Desktop | ✅ Pass |
| B.2 | PulseAudio audio-test container, Whisper accuracy benchmark | ✅ Pass |

---

## Phase 0: Codebase Cleanup ✅

| Step | Description | Status |
|------|-------------|--------|
| 0.1 | Delete completed STT benchmark files | ✅ |
| 0.2 | Delete `spec.md` | ✅ |
| 0.3 | Move root-level test files into `tests/` | ✅ |
| 0.4 | Move `generate_backchannel.py` into `scripts/` | ✅ |
| 0.5 | Move audio clips into `assets/`, update paths | ✅ |

---

## Phase 1: Extract the Agent Pipeline ✅

*Goal: Pull all "brain" logic out of `app.py` into `pipeline/` with zero macOS-specific code.*

| Step | Description | Status |
|------|-------------|--------|
| 1.1 | Create `pipeline/` package scaffold | ✅ |
| 1.2 | Extract audio processing → `pipeline/audio.py` | ✅ |
| 1.3 | Extract wake phrase detection → `pipeline/wake.py` | ✅ |
| 1.4 | Extract conversation state machine → `pipeline/conversation.py` | ✅ |
| 1.5 | Extract LLM calls → `pipeline/llm.py` | ✅ |
| 1.6 | Extract TTS → `pipeline/tts.py` (output device as parameter) | ✅ |

---

## Phase 2: Define the Connector Interface ✅

*Goal: Define what a "meeting connector" must do in code. Wrap macOS logic behind that interface.*

| Step | Description | Status |
|------|-------------|--------|
| 2.1 | Create `connectors/` package scaffold | ✅ |
| 2.2 | Define `MeetingConnector` abstract interface → `connectors/base.py` | ✅ |
| 2.3 | Wrap macOS logic as `MacOSAdapter` → `connectors/macos_adapter.py` | ✅ |

---

## Phase 3: Docker/Cloud Adapter ✅

*Goal: Build a headless Linux adapter running in Docker. Verified end-to-end in live Google Meet.*

| Step | Description | Status |
|------|-------------|--------|
| 3.0 | DigitalOcean droplet setup (one-time) | ✅ |
| 3.1 | Validate `pipeline/` imports cleanly on Linux | ✅ |
| 3.2 | Create `docker/` folder and base Dockerfile | ✅ |
| 3.4 | Set up PulseAudio virtual audio in the container | ✅ |
| 3.5 | Validate Whisper accuracy on container audio | ✅ |
| 3.6 | Implement `DockerAdapter` → `connectors/docker_adapter.py` | ✅ |
| 3.7 | Create `docker/entrypoint.py`, wire adapter to pipeline | ✅ |
| 3.8 | Build daily smoke test (`tests/test_smoke_docker.py`) | ✅ |

---

## Phase 4: Reorient — Cloud Cleanup + Linux Local Adapter

*Goal: Move cloud deployment artifacts out of the way. Adapt the Docker adapter for local Linux machines.*

| Step | Description | Status |
|------|-------------|--------|
| 4.1 | Move `docker/` folder and Dockerfiles into `cloud/docker/` | ✅ |
| 4.2 | Create `connectors/linux_adapter.py` from `docker_adapter.py` — remove Docker-specific hardcoded paths | ✅ |
| 4.3 | Create `scripts/linux_setup.sh` — creates PulseAudio virtual sinks on a local Linux machine | ✅ |
| 4.4 | Update `connectors/__init__.py` and any imports referencing `DockerAdapter` | ✅ |
| 4.5 | Verify `LinuxAdapter` works end-to-end on a local Linux machine (or native droplet without Docker) | ✅ |
| 4.6 | Verify `MacOSAdapter` works end-to-end on local macOS after reorientation (wake phrase → LLM → TTS → meeting participants hear Operator) | ✅ |

---

## Phase 5: Config System (The Loadout)

*Goal: Move all hardcoded constants into `config.yaml`. This is the "loadout" — the shareable unit of agent configuration.*

| Step | Description | Status |
|------|-------------|--------|
| 5.1 | Create `config.yaml` with all configurable values (LLM model, voice ID, wake phrase, agent name, etc.) | ✅ |
| 5.2 | Create `config.py` reader — single source of truth for all modules | ✅ |
| 5.3 | Wire `config.py` into `pipeline/` modules (replace hardcoded constants) | ✅ |
| 5.4 | Wire `config.py` into both adapters and entry points | ✅ |

---

## Phase 6: Consolidate Entry Points

*Goal: Extract the shared transcription loop into `pipeline/runner.py`. Add OS auto-detection so `python -m operator` works on both platforms.*

| Step | Description | Status |
|------|-------------|--------|
| 6.1 | Extract shared transcription loop → `pipeline/runner.py` | ✅ |
| 6.1.5 | Replace `calendar_join.py` with `caldav_poller.py` — CalDAV + keychain, before app.py refactor | ✅ |
| 6.2 | Simplify `app.py` to use `runner.py` and `caldav_poller.py` (macOS menu bar shell only) | ✅ |
| 6.3 | Create Linux entry point using `runner.py` | ✅ |
| 6.4 | Add OS auto-detection — `python -m operator` picks the right adapter | ✅ |

---

## Phase 7: Performance Iteration

*Goal: Solid audio quality and reliable pipeline behavior before onboarding new developers.*

| Step | Description | Status |
|------|-------------|--------|
| 7.1 | Audio quality — test on native AMD64 (DigitalOcean droplet without Docker) to confirm/rule out QEMU as cause of fuzzy audio | ✅ Done — audio still choppy, QEMU ruled out |
| 7.2 | Audio quality — fix 44100Hz→48000Hz sample rate mismatch: set PulseAudio virtual sinks to 48kHz in `linux_setup.sh` | ✅ Done — also fixed 3 Chrome audio bugs in `LinuxAdapter` (no-sandbox, env= override, PipeWire). Voice clear. |
| 7.3 | TTS provider benchmark — evaluate ElevenLabs vs OpenAI TTS vs Piper on voice quality through WebRTC, latency, cost, and vendor count. Make final provider decision. | ✅ Done — kokoro_heart default; full 3-tier architecture in pipeline/tts.py + config.yaml |
| 7.4 | Latency masking — speculative processing + filler clip pipeline | ✅ Done — mechanics wired; clips pending async generation session |
| 7.5 | TTS reliability — improve error handling and retry logic in `pipeline/tts.py` for chosen provider (skip if Piper chosen — local, no API failures) | ⬜ |
| 7.6 | STT accuracy — benchmark STT alternatives; switch to mlx-whisper for 4x latency win | ✅ Done — mlx-whisper base at 110ms vs faster-whisper base at 420ms |

---

## Phase 8: Open-Source Packaging

*Goal: Package the project so a stranger can clone and install it.*

| Step | Description | Status |
|------|-------------|--------|
| 8.1 | Add `pyproject.toml` — package name, Python version, entry points (`operator-setup`, `operator-run`) | ⬜ |
| 8.2 | Add `LICENSE` (MIT) | ⬜ |
| 8.3 | Rewrite `README.md` — what it is, quick start, architecture, how to swap providers, how to contribute | ⬜ |

---

## Phase 9: Setup Wizard

*Goal: `operator setup` walks a new developer from zero to a working agent in five minutes. Re-runnable subcommands (`operator setup voice`, `setup keys`, etc.) serve as the settings UI for post-onboarding changes.*

| Step | Description | Status |
|------|-------------|--------|
| 9.1 | Scaffold `operator setup` CLI with subcommand routing — `setup` (full), `setup voice`, `setup keys`, `setup calendar`, `setup agent`. Each detects existing config and shows current values as defaults. | ⬜ |
| 9.2 | Implement `setup keys` — prompt for OpenAI API key (validate), ElevenLabs key (optional, validate if provided). Write to `.env`. | ⬜ |
| 9.3 | Implement `setup voice` — local vs cloud selection. Local: Kokoro-only, fetch voice list from HuggingFace repo, print preview link. Cloud: prompt for provider (OpenAI/ElevenLabs), fetch voices from provider API, print preview link. Write to `config.yaml`. | ⬜ |
| 9.4 | Implement `setup agent` — agent name, wake phrase, system prompt, interaction mode. Write to `config.yaml`. | ⬜ |
| 9.5 | Implement `setup calendar` — CalDAV credential flow: prompt for bot Gmail, open apppasswords URL, inline instructions, validate CalDAV connection, store in system keychain. | ⬜ |
| 9.6 | Implement full `operator setup` — chains all subcommands in sequence. OS-aware audio driver install (macOS: BlackHole, Linux: PulseAudio sinks). | ⬜ |
| 9.7 | Startup validation — on `operator run`, check config for broken/missing voice/provider and print "run `operator setup voice` to fix". | ⬜ |
| 9.8 | Test from scratch with no `.env` — follow prompts, confirm working on first meeting | ⬜ |

---

## Phase 10: Chat Mode

*Goal: Agent responds in meeting chat when @mentioned. No audio or latency complexity.*

| Step | Description | Status |
|------|-------------|--------|
| 10.1 | Add `MODE` key to `config.yaml`: `voice` \| `chat` \| `both` | ⬜ |
| 10.2 | Implement chat monitoring in `LinuxAdapter` — poll for `@<agent-name>`, send to LLM, post response | ⬜ |
| 10.3 | Implement same in `MacOSAdapter` | ⬜ |
| 10.4 | Test: type `@operator what's 2+2?` in meeting chat → agent responds in chat | ⬜ |

---

## Phase 11: Visual Feedback

*Goal: Make the agent feel present during the latency gap.*

| Step | Description | Status |
|------|-------------|--------|
| 11.1 | Chat acknowledgment — post "On it..." when processing; follow with response | ⬜ |
| 11.2 | Emoji reactions — 🤔 on thinking state, ✅ on response complete | ⬜ |

---

## Key Decisions Made

- **Architecture:** Three-layer separation (pipeline / connector / shell) — locked in, proven
- **Primary platform:** Local machine (macOS + Linux), not cloud. Cloud is upgrade path.
- **Wake word:** Whisper-based inline detection — Porcupine removed
- **STT:** mlx-whisper base on macOS (110ms, Apple Silicon accelerated); faster-whisper base on Linux/Docker (420ms, CPU int8). Config-switchable via `stt.provider`.
- **LLM:** GPT-4.1-mini
- **TTS:** Three-tier architecture — `tts.provider: local | openai | elevenlabs`. Default: `local/kokoro_heart` (af_heart, 4/5, free). OpenAI tier: `gpt-4o-mini-tts` (5/5, ~0.87s TTFAB). ElevenLabs tier: `eleven_flash_v2_5` (5/5, ~0.39s TTFAB). Kokoro requires Python 3.10–3.12; falls back to `macos_say` gracefully if unavailable.
- **Guest join:** Locked default. "Ask to join" — host admits the bot. Authenticated join via `auth_state.json` is opt-in only.
- **Meeting detection:** CalDAV polling (1 min interval), app password stored in system keychain. No OAuth, no credentials.json.
- **Licensing:** MIT (decided)
- **Python target:** 3.11

## Open Questions

1. ~~**Audio quality root cause**~~ — **Resolved.** 48kHz fix + 3 `LinuxAdapter` Chrome fixes. Voice clear through WebRTC (March 27, 2026).
2. **Wake phrase customization** — let users choose their own wake phrase? Requires Whisper reliability testing on custom phrases.
3. ~~**Calendar auto-join**~~ — **Resolved.** CalDAV polling (Phase 9).
4. **Linux distro coverage** — Ubuntu/Debian as tier-1; PulseAudio vs. PipeWire (default on Fedora, Ubuntu 22.04+) needs separate validation.