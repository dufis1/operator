# Operator — Next Steps

*Strategic overview. For the step-by-step human checklist: `refactor-plan.md`. For the AI-agent working document: `agent-context.md`.*

*Last updated: March 26, 2026*

---

## Where We Are

Phase 3 is complete. The full end-to-end pipeline works in a live Google Meet: wake phrase → STT → LLM → TTS → meeting participants can hear Operator. Both a macOS adapter and a Docker-based Linux adapter exist. The three-layer architecture (pipeline / connector / platform shell) is in place and the abstractions are clean.

**The reorientation:** The Phase 3 work was built cloud-first — the Docker adapter targets a DigitalOcean droplet, and the next action in the old plan was droplet deployment. We are stepping back from that. The priority is a scrappy, local-machine-first open-source developer tool, not a managed cloud service. Cloud deployment is a valid future upgrade path, but it is not what we are building toward right now.

---

## The Revised Sequence

### Phase 4 — Reorient: Cloud Cleanup + Linux Local Adapter

**Goal:** Get the repo pointing in the right direction before building anything new.

Two things happen here. First, all cloud/Docker deployment artifacts (`Dockerfile`, `docker/entrypoint.py`, `pulse_setup.sh`, bench containers) move into a `cloud/` subdirectory. They stay in the codebase — the work is not thrown away — but they are clearly separated from the local-machine experience.

Second, the `DockerAdapter` gets adapted into a `LinuxAdapter` for running on a local Linux machine. The core Playwright/PulseAudio approach is identical, but the Docker-specific hardcoded paths (`DISPLAY=:99`, `PULSE_RUNTIME_PATH=/tmp/pulse`, `--no-sandbox`) are replaced with environment-aware defaults. A `scripts/linux_setup.sh` script creates the required PulseAudio virtual sinks on a local machine (the same commands that `pulse_setup.sh` runs inside the container).

**Why first:** Every subsequent step builds on a clean, correctly-oriented repo. The architecture should reflect where we're going, not where we've been.

---

### Phase 5 — Config System (The Loadout)

**Goal:** Every hardcoded constant — agent name, wake phrase, system prompt, LLM model, voice ID, interaction mode, connector type, conversation timeout — moves into a `config.yaml`.

This is the "loadout" from the product strategy: the serializable, shareable unit of agent configuration. A `config.py` reader provides a single place for all modules to get their settings. The `.env` file stays for secrets (API keys); `config.yaml` is for everything else.

**Why this order:** Config is a prerequisite for performance tuning (Phase 6) — you can't efficiently iterate on thresholds and timeouts if they're scattered across source files. It also makes the setup wizard (Phase 8) straightforward to implement.

---

### Phase 6 — Consolidate Entry Points

**Goal:** `app.py` (macOS) and `docker/entrypoint.py` (cloud) share a nearly identical transcription loop. Extract that shared core into `pipeline/runner.py`. The entry points become thin wrappers: instantiate the right connector, hand it to the runner, start the loop.

Add OS auto-detection so a single `python -m operator` command picks the right adapter at runtime (macOS → `MacOSAdapter`, Linux → `LinuxAdapter`).

**Why this order:** Clean architecture before going public. Also a prerequisite for the open-source packaging in Phase 7 (`python -m operator` is only meaningful once there's a unified entry point).

---

### Phase 7 — Performance Iteration

**Goal:** Solid audio quality and reliable pipeline behavior before onboarding any new developers.

Five areas:

1. **Audio quality** — Root cause confirmed (March 2026): PulseAudio virtual sinks default to 44100Hz; Chrome WebRTC runs at 48000Hz. Real-time sample rate conversion causes audible artifacts. Fix: set sinks to 48000Hz in `linux_setup.sh`. QEMU ruled out — tested on native AMD64 droplet.

2. **TTS provider** — Trim local TTS to Kokoro-only (remove Piper and macos_say as shipped options). Kokoro is the local default. For cloud providers, benchmark ElevenLabs and OpenAI TTS on voice quality through WebRTC, latency, cost, and vendor count. Make a final decision before investing further in TTS reliability work.

3. **Latency masking** — Tune the filler phrase silence threshold. Too aggressive: filler collides with the speaker still talking. Too conservative: awkward silence. This is the main dial that hasn't been fully set.

4. **TTS reliability** — Improve error handling and retry logic in `pipeline/tts.py` for whichever provider is chosen in the benchmark. If Piper (local) is chosen, this step is skipped — no API failures possible.

5. **STT accuracy** — Review the `WHISPER_HALLUCINATIONS` filter for new false-positive patterns. Consider whether the `base` model is sufficient or whether `small` is worth the latency trade-off.

**Why before the wizard:** Setup onboards new developers. The experience they arrive to should be reliable. Shipping the wizard while audio quality is flaky creates a bad first impression that's harder to undo than a delayed launch.

---

### Phase 8 — Open-Source Packaging

**Goal:** Replace the OAuth-based meeting detection with CalDAV, then package the project so a stranger can clone and install it.

Four pieces:

1. **CalDAV poller** — `calendar_join.py` is deleted and replaced with `caldav_poller.py`. The new poller connects to the bot's Google Calendar via CalDAV (no Google Cloud project, no OAuth app, no `credentials.json`), polls every 1 minute, and triggers the connector's join method when an accepted event with a Meet link is starting. `google-api-python-client` and `google-auth-oauthlib` are removed; `caldav` is added. This must land before the README is written — CalDAV is the meeting detection story the README tells.

2. **`pyproject.toml`** — Makes `pip install -e .` work. Defines the package name, Python version requirement, and entry points (`operator-setup`, `operator-run`).

3. **`LICENSE`** — MIT. Simplest, most permissive, most open-source-native.

4. **`README.md` rewrite** — The current README is from an earlier version of the product. New one: what it is (one paragraph), quick start (five steps, five minutes), architecture (three-layer diagram), how to swap providers, how to contribute.

**Why this order:** The README is the storefront. You can't make the repo public until these exist — and you don't want to publish with the old OAuth meeting detection.

---

### Phase 9 — Setup Wizard

**Goal:** `operator setup` walks a new developer from zero to a working agent in five minutes. By this point the CalDAV poller is already in place (Phase 8); the wizard's job is to get the credentials configured.

#### Re-runnable subcommands

The wizard is both the onboarding path and the change path. The full wizard runs on first install, but each section is independently re-runnable:

```
operator setup              # full onboarding (first run, or re-run everything)
operator setup voice        # change TTS provider + voice selection
operator setup keys         # update API keys
operator setup calendar     # reconfigure CalDAV credentials
operator setup agent        # change agent name, wake phrase, system prompt
```

Each subcommand detects existing config, shows current values as defaults, and only overwrites what the user changes. This eliminates the "wizard ran once, now I'm stuck editing YAML" problem — the same tool that set things up is the tool that changes them.

#### Full wizard flow

The full `operator setup` prompts for: OpenAI API key (validates), TTS provider and voice (see voice selection below), agent name, wake phrase, interaction mode. OS is auto-detected silently. On macOS: silent `brew install blackhole-2ch`. On Linux: runs `linux_setup.sh` to create PulseAudio virtual sinks.

#### Voice selection (`operator setup voice`)

The voice setup flow:

1. **Local or cloud?** — User picks `local` or a cloud provider (OpenAI, ElevenLabs).
2. **If local (Kokoro):**
   - Print link to Kokoro's HuggingFace Space so user can preview voices.
   - Fetch available voice list from the Kokoro HuggingFace repo (not hardcoded — stays current without code changes).
   - User selects a voice. Default: `af_heart`.
3. **If cloud provider:**
   - Prompt for API key if not already configured (validate it).
   - Fetch available voices from the provider's API at setup time (ElevenLabs `/voices` endpoint; OpenAI's list is small/stable but maintained as a queryable array).
   - Print link to provider's voice preview page (ElevenLabs voice library, OpenAI platform docs).
   - User selects a voice from the live list.
4. **Write selection** to `config.yaml`. One active voice at a time — no multi-voice support for now.

Key design principle: voice lists are fetched live from provider APIs, not maintained as static lists in our code. This avoids staleness without requiring us to ship updates when providers add voices.

#### CalDAV credential flow

The wizard handles the CalDAV credential flow: prompts for the bot's Gmail address; opens myaccount.google.com/apppasswords in the browser automatically; displays inline step-by-step instructions for generating the 16-character app password; prompts the user to paste it back; validates the CalDAV connection before proceeding; stores the credential in the system keychain (macOS Keychain or Linux Secret Service — never in `.env`).

#### Completion

Writes `.env` and `config.yaml` at completion. The user is told: "Accept meeting invites sent to [bot Gmail] and Operator will join automatically."

This is the "set the table once" flow from the product strategy. Until it exists, setup requires reading `agent-context.md` — which is fine for us, not fine for new developers. The re-runnable subcommands ensure it remains the go-to tool for changes, not just initial setup.

---

### Phase 10 — Chat Mode

**Goal:** The agent responds in meeting chat when @mentioned. No audio, no latency masking needed.

Add a `MODE` key to `config.yaml`: `voice` | `chat` | `both`. In chat mode, monitor the meeting chat for `@<agent-name>`, strip the mention, send to LLM, post the response as a chat message. This is also a useful workaround while audio quality is being tuned — a chat-only demo works even if voice isn't perfect yet.

---

### Phase 11 — Visual Feedback

**Goal:** Make the agent feel present during the latency gap.

Two mechanisms, in order of implementation complexity:
1. **Chat acknowledgment** — when the agent is processing, post "On it..." in meeting chat. When done, the response replaces this or follows it.
2. **Emoji reactions** — 🤔 on thinking state, ✅ on response complete.

Depends on chat-panel infrastructure from Phase 10.

---

## What Is Not on This Plan

- **DigitalOcean droplet deployment** — the cloud path exists (Phase 3 work) and is preserved in `cloud/`. It is not a current priority.
- **Tool / MCP integration** — the right move for v1.5 or v2.
- **Loadout sharing / registry** — important for the community flywheel, but post-wizard.
- **Calendar auto-join improvements** — addressed in Phase 9 (CalDAV poller).
- **Windows** — explicitly out of scope for v1.
- **Multi-agent concurrency** — a cloud orchestration problem, not a local one.

---

## Summary Table

| Phase | What | Outcome |
|---|---|---|
| 4 | Cloud cleanup + Linux local adapter | Repo points in the right direction |
| 5 | Config system (loadout) | Everything configurable from `config.yaml` |
| 6 | Consolidate entry points | Unified `python -m operator` |
| 7 | Performance iteration | Solid audio quality + reliable pipeline |
| 8 | Open-source packaging | CalDAV poller replaces OAuth; `pyproject.toml`, `LICENSE`, new `README.md` |
| 9 | Setup wizard | Developer on-ramp, zero-to-working in 5 min |
| 10 | Chat mode | Second interaction mode, demo-ready |
| 11 | Visual feedback | Polish — emoji reactions + chat acknowledgments |
