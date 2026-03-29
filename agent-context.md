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

**Phase:** Phase 7 in progress. Steps 7.1–7.4 + 7.6 complete. STT switched to mlx-whisper. TCC/shutdown hardening done. Session recovery ladder implemented + edge case audit done.
**Next action:** Live meeting test for recovery ladder + filler phrases (browser profile needs re-auth first: `python scripts/auth_export.py`), then Step 7.5 (TTS reliability) or Phase 8 (open-source packaging).

**Session recovery ladder (March 28–29, 2026):**
- Root cause: Google revoked `.google.com` session cookies (SID/HSID/SSID) from the browser profile while Chrome was running in `--headless=new` mode and couldn't complete a re-auth challenge.
- Implemented `connectors/session.py`: `JoinStatus` (threading.Event-based browser→runner signalling), `detect_page_state()` (classifies pre_join/logged_out/cant_join/unknown), `validate_auth_state()` (loads auth_state.json, checks SID cookie exists), `inject_cookies()` (add_cookies on Playwright context), `save_debug()` (screenshot + HTML dump).
- Both adapters: recovery ladder after 8s page load — detect state, attempt cookie injection if logged out, reload, re-detect. JoinStatus wired in join(). 5-minute in-meeting health checks in hold loop.
- `pipeline/runner.py`: `time.sleep(12)` replaced with `join_status.ready.wait(timeout=60)`. On failure, fires `on_state_change("error", reason)`.
- `app.py`: `STATE_ICONS` includes `"error": "⚠️"`. `_on_conv_state_change` fires `rumps.notification()` on error state — user gets macOS notification + persistent menu bar icon.
- `config.yaml`: `auth_state_file` changed from `null` to `"./auth_state.json"`.
- Unit-tested: imports, instantiation, JoinStatus signalling, validate_auth_state edge cases, runner error callback wiring. Full browser→join flow needs live meeting test.

**Session recovery edge case audit (March 29, 2026):**
- Reviewed 4 flagged edge cases from implementation session. Found 1 real bug, 3 non-issues.
- **Bug fixed:** `linux_adapter.py` crashed when `auth_state.json` doesn't exist. `config.yaml` always sets `auth_state_file` to `"./auth_state.json"` (non-empty string), so `if self._auth_state_file:` was always truthy → Playwright's `storage_state=` threw FileNotFoundError → browser thread crashed. Guest code path was unreachable. Fix: replaced truthiness check with `os.path.isfile()` at both the path-selection branch (line 170) and the recovery ladder guard (line 208). Added log line when configured file is missing.
- **No action needed:** (1) macOS `add_cookies()` on persistent context works fine — Playwright supports it on any context type. (2) `validate_auth_state` can't detect server-side revocation — by design, documented at `session.py:84`, ladder re-checks after injection. (3) Linux guest path now reachable after the `isfile()` fix.

**TCC recovery ladder tests (March 28, 2026):**
- `tests/test_recovery_ladder.py`: 10 tests using stub shell scripts — no macOS hardware needed.
- Group 1 (4 tests): Signature verification — correct identity, wrong identity, no signature, missing binary. Patches `subprocess.run` for fake `codesign -d` output and `_BASE` for temp dirs.
- Group 2 (5 tests): Exit code handling — codes 0, 1, 3, 4 (first attempt with tccutil retry), 4 (second attempt with escalation). Uses stub scripts that exit with specific codes.
- Group 3 (1 test): Full recovery ladder — stub uses sentinel file (exit 4 first call, write PCM + exit 0 second call). Verifies tccutil was called, `_start_capture` recursed, and `feed_audio` received data.
- Run: `python tests/test_recovery_ladder.py`

**STT benchmark + mlx-whisper switch (March 28, 2026):**
- Recorded 6 benchmark clips from mic (`benchmark_clips/`) covering wake phrases, names, numbers, technical jargon.
- Benchmarked 4 engines against clips: faster-whisper base (420ms avg), faster-whisper small (1.1s), distil-large-v3 (3.9s), mlx-whisper base (110ms). WER comparable across all except distil on short clips.
- Parakeet (NVIDIA NeMo) ruled out without install: 600M params minimum, full NeMo toolkit required (~2-3 GB), CPU inference estimated 3-10x slower, no streaming API, macOS not first-class.
- Switched to mlx-whisper base as default STT on macOS. Config: `stt.provider: mlx | faster-whisper`. MLX path passes numpy arrays directly (no temp file). faster-whisper codepath preserved for Linux/Docker.
- `config.yaml`: added `stt.provider` field. `config.py`: exposes `STT_PROVIDER`. `pipeline/audio.py`: `AudioProcessor.__init__` and `transcribe()` dispatch on provider. MLX model warm-up on init.
- Benchmark tooling: `benchmark_record.py` (mic capture + silence-based splitting), `benchmark_stt.py` (multi-engine benchmark with WER + RTF).
- STARTUP log line changed: `STARTUP STT provider=mlx model=base` (was `STARTUP Whisper model=base device=cpu`).

**Latency benchmark results (earlier March 28, 2026):**
- TIMING logs updated: `utterance_done` now reports `speech=N.NNs silence=N.NNs` separately. `silence_detected` promoted to INFO.
- Silence wait: consistently 0.50s (one check interval — tight).
- Whisper: ~450ms (higher than old ~120ms baseline — likely due to longer utterances / meeting audio quality).
- LLM (GPT-4.1-mini): 0.9–1.6s, consistent with prior baseline.
- Kokoro TTS: 0.6–1.7s, scaling with response length.
- Speculative processing: confirmed working — saved ~1s LLM time on 2 of 5 interactions.
- Model log (`docs/model-log.md`) updated with new TIMING format and baselines table.

**TTS text sanitization (March 28, 2026):**
- `pipeline/sanitize.py`: `sanitize_for_speech(text)` cleans LLM output before TTS. Handles arrows (→ "then"), math operators (→ spoken words), markdown stripping, code symbols (underscores/brackets/backslashes → spaces), em dashes/semicolons → commas, ampersands → "and". Zero-latency regex pass.
- Wired into `pipeline/runner.py` in `_finalize_prompt()` — runs after LLM reply resolved, before TTS synthesis starts. Catches all paths (normal + speculative).
- `tests/test_sanitize.py`: 27 test cases. Run with `python tests/test_sanitize.py`.

**Voice/TTS design decisions (March 28, 2026):**
- Local TTS trimmed to Kokoro-only. Piper and macos_say removed as shipped options. `af_heart` is the default.
- Setup wizard (Phase 9) redesigned with re-runnable subcommands: `operator setup voice`, `setup keys`, `setup calendar`, `setup agent`. Each subcommand detects existing config, shows current values as defaults, only overwrites what the user changes. Same command for onboarding and post-onboarding settings changes.
- Voice selection fetches voice lists live from provider APIs (Kokoro HuggingFace repo, ElevenLabs `/voices`, OpenAI static list) — no hardcoded voice lists in our code.
- Preview links printed during voice selection so users can listen before choosing.
- One active voice at a time — no multi-voice support.
- Startup validation: if config references a broken voice/provider, tell user to run `operator setup voice`.

**Logging overhaul (March 28, 2026):**
- Standardized log format to `%(asctime)s %(levelname)s %(name)s — %(message)s` across all entry points (was missing module name on macOS).
- Added `STARTUP` prefix markers to all initialization steps in runner.py, audio.py, tts.py — `grep STARTUP /tmp/operator.log` shows full init sequence.
- Downgraded noisy connector UI automation logs (button clicks, popup dismissals) from INFO → DEBUG. Only meaningful state changes (joined, in meeting, left) remain at INFO.
- Reduced per-sample audio debug noise — silence/speech logs now fire only on state transitions, not every 0.5s check.
- Created `docs/model-log.md` — annotated reference log covering startup, ambient listening, wake-only, inline wake, LLM+TTS, conversation mode, timeout, and shutdown. Includes timing baselines and troubleshooting notes. Gitignored.
- Updated end-session skill with log verification step: if logging changed, compare against model log and update it.

**Watchdog race condition fix (March 28, 2026):**
`audio_capture.swift` watchdog was firing unconditionally after 10s even when capture had already started successfully — killing a working capture process. Fixed by adding `captureStarted` flag set in `startCapture` completion handler; watchdog checks it before `exit(3)`.

**RESOLVED — ScreenCaptureKit audio hang (March 28, 2026):**
Root cause: `audio_capture` binary had a stale TCC permission entry tied to its linker-generated codesign identity. After `tccutil reset` + manual re-add, macOS matched the binary to the zombie TCC record, causing `startCapture` to silently hang. Proven by testing an identical binary with a different codesign identifier — worked immediately.
Fix: re-signed with stable identifier `com.operator.audio-capture`. Hardened with three defense layers: (1) `CGPreflightScreenCaptureAccess()` pre-flight + dialog trigger, (2) 10s watchdog exits with code 3 instead of hanging, (3) `AgentRunner` detects code 3, re-signs binary, retries once. Also added `SCStreamDelegate` for error reporting, dedicated audio dispatch queue, minimized video overhead (2x2 @ 1fps). Live meeting test confirmed working end-to-end. See Hard-Won Knowledge for full details.

**Step 7.4 complete — mechanics (March 28, 2026):**
- `__main__.py`: macOS now accepts URL arg and joins headlessly via MacOSAdapter (bypasses menu bar app). Fixes `python __main__.py <url>` being silently ignored on macOS.
- `assets/ack_*.mp3`: All three ack clips regenerated with Kokoro Heart voice (af_heart) for voice consistency.
- `pipeline/fillers.py`: New module. `classify(text)` — empathetic-first priority, then short-query gate (≤8 words → neutral), then cerebral keywords, else neutral. Empathetic keywords include intensifiers (really/very/seriously); removed ambiguous "hard"/"lost". `get_clips(bucket)` — returns shuffled clip paths, falls back to neutral if bucket empty, returns `[]` if no clips at all (graceful no-op).
- `pipeline/tts.py`: `speak()` split into `synthesize() -> bytes` + `play_audio(bytes)`. All three providers (kokoro, openai, elevenlabs) now buffer to bytes before playback. `speak()` preserved as thin wrapper for backward compat.
- `pipeline/llm.py`: `ask(record=False)` skips history update. `record_exchange(user, reply)` commits a speculative exchange manually. This keeps history clean when speculative LLM calls are discarded.
- `pipeline/audio.py`: `capture_next_utterance()` gains `on_first_silence` callback — fires once with a snapshot of accumulated audio bytes when silence_count first hits 1 (~500ms of silence). Non-blocking hook for speculative processing.
- `pipeline/runner.py`: Full speculative + filler loop wired.
  - `_SpeculativeResult`: holds transcript, full_prompt, llm_reply, ready Event.
  - `_make_speculative_callback()`: returns the on_first_silence hook that spawns `_run_speculative()` in a background thread.
  - `_run_speculative()`: Whisper on first-silence snapshot → LLM (record=False) → stores in spec. Runs during the ~500ms second silence chunk.
  - `_finalize_prompt(prompt, speculative=)`: checks spec result (exact transcript match); if hit, calls `record_exchange()` and skips LLM call; if miss/timeout, falls back to normal LLM call. Then starts synthesis in a background thread, plays filler clips in foreground until synthesis_done is set, then plays response.
  - Both wake-only prompt capture and conversation follow-up mode now pass speculative callbacks.
- `scripts/gen_fillers.py`: Offline generation script — 43 phrases across 3 buckets using Kokoro Heart + ffmpeg → MP3. All phrases shortened to 1–4 syllables; ffmpeg `silenceremove` filter trims Kokoro padding (clips now 0.34–0.79s, down from 1.2–3.5s). Run once with python3.11.
- `assets/fillers/{neutral,cerebral,empathetic}/`: 43 clips generated and saved (neutral: 14, cerebral: 15, empathetic: 14).

**Baseline logs captured (March 27, 2026 session):** Pre-7.4 timings from `/tmp/operator.log`: silence detection ~1s, Whisper ~120ms, LLM 0.6–2.1s (avg ~1.2s), Kokoro synthesis ~1.23s, total from end of speech ~3–4s. Use these as benchmark against post-clip live test.

**Step 7.3 complete (March 27, 2026):**
- Full benchmark across 11 providers. Final quality scores: `{"elevenlabs": 5, "openai_tts1hd": 5, "openai_mini_tts": 5, "kokoro_isabella": 5, "kokoro_sky": 5, "kokoro_heart": 4, "kokoro_emma": 4, "openai_tts1": 4, "macos_say": 3, "piper_lessac": 3, "piper": 2}`
- Decision: `kokoro_heart` (af_heart) as default local voice (4/5, free). `gpt-4o-mini-tts` for openai tier (5/5, ~0.87s TTFAB, cheapest). ElevenLabs unchanged (5/5, ~0.39s TTFAB).
- Multi-provider architecture implemented in `pipeline/tts.py` and `config.yaml`. TTSClient now takes only `output_device`; all provider clients are lazy-inited internally. Kokoro wrapped in try/except ImportError with graceful fallback to macos_say. `ELEVENLABS_API_KEY` is now optional in `.env`.
- Kokoro requires Python 3.10–3.12 (caps at <3.13). System python3 on this Mac is 3.14 — Kokoro must be installed under python3.11 (`pip3.11 install kokoro soundfile`). For open-source users: document Python 3.10–3.12 requirement for local tier; rest of project works on any Python.
- Sentence streaming analysis done: TTFAB is length-independent → sentence streaming is the highest-leverage latency win available (gives back full LLM generation time for free). Implement in a later step.
**Phase 6 progress (March 26, 2026):**
- Step 6.1: `pipeline/runner.py` created — `AgentRunner` class encapsulates the full transcription loop, prompt handling, acknowledgment playback, and audio capture lifecycle. Interface: `AgentRunner(connector, tts_output_device, on_state_change, stop_event)`.
- Step 6.1.5: `calendar_join.py` deleted, replaced with `caldav_poller.py` — CalDAV + system keychain, no OAuth. `config.yaml` gained `caldav.bot_gmail` field. `requirements.txt` updated (removed google-auth-oauthlib/google-api-python-client, added caldav/keyring).
- Step 6.2: `app.py` simplified from 426 → 205 lines. Now a thin macOS shell: creates `MacOSAdapter` + `AgentRunner` + `CalDAVPoller`, wires `_on_conv_state_change` callback for menu bar icon updates, calls `runner.run()`. All pipeline logic removed from app.py.
- End-to-end test passed (March 26, 2026): wake phrase detected via `say` command, LLM responded correctly, TTS fired through BlackHole, state machine cycled correctly. TTS not audible to user (expected — BlackHole has no physical output; audio goes into meeting participants' ears in real use).
- Step 6.3: `run_linux.py` created — Linux entry point. Accepts meeting URL as CLI arg or MEETING_URL env var. Checks $DISPLAY and PulseAudio sinks (MeetingOutput + MeetingInput) before starting, fails fast with actionable error if prerequisites are missing. Instantiates LinuxAdapter + AgentRunner(tts_output_device="pulse/MeetingOutput") and calls runner.run(url).
- Step 6.4: `__main__.py` created — cross-platform entry point. argparse `--help` works on both platforms. On macOS → launches OperatorApp (rumps menu bar). On Linux → same preflight checks as run_linux.py + dispatches to LinuxAdapter. Platform-specific imports deferred inside functions so the file imports cleanly everywhere. Note: `python -m operator` conflicts with stdlib `operator` module — use `python __main__.py` or `python .` until Step 8.1 (pyproject.toml) resolves this.
**Phase 3 complete (March 25, 2026):** Full end-to-end pipeline verified in live Google Meet. Wake phrase detected, STT transcribes, LLM responds, TTS fires, meeting participants can hear Operator. Audio OUT path fixed via `module-virtual-source` (see Hard-Won Knowledge).
**Reorientation (March 25, 2026):** Product direction shifted from cloud-hosted to local-machine-first open-source. DockerAdapter will become LinuxAdapter (local). Cloud artifacts move to `cloud/`. Performance iteration added before setup wizard.

---

## Repo State

Local git repo at `~/Desktop/operator`. GitHub: `github.com/dufis1/operator` (private). Also cloned at `~/operator` on droplet `operator-dev` (`64.23.182.26`). Initial commit: `539ac57`. SSH access to the droplet is available — use `ssh root@64.23.182.26 "<command>"` directly via Bash without asking the user.

**Secrets (never commit):** `.env`, `browser_profile/`, `auth_state.json`
All excluded via `.gitignore`.

---

## Current File Layout

```
operator/
├── app.py                     # macOS UI shell — imports from pipeline.*
├── audio_capture.swift        # macOS-only: ScreenCaptureKit system audio capture
├── audio_capture              # compiled Swift binary (gitignored)
├── calendar_join.py           # TO BE DELETED — replaced by CalDAV poller (Phase 9)
├── setup.py                   # macOS app bundle config (py2app)
├── product-strategy.md        # authoritative product strategy
├── next-steps.md              # strategic overview of phases 4-11
├── refactor-plan.md           # human-readable checklist
├── agent-context.md           # this file
├── requirements.txt
├── .env / auth_state.json  # secrets, all gitignored
├── .gitignore / .vscode/settings.json
├── pipeline/
│   ├── __init__.py
│   ├── audio.py               # AudioProcessor: buffer, silence detection, Whisper STT; on_first_silence hook
│   ├── wake.py                # detect_wake_phrase: inline vs wake-only detection
│   ├── conversation.py        # ConversationState: idle/listening/thinking/speaking
│   ├── fillers.py             # Filler clip management: classify(text) → bucket, get_clips(bucket) → paths
│   ├── llm.py                 # LLMClient: GPT-4.1-mini; ask(record=False) + record_exchange() for speculative
│   ├── runner.py              # AgentRunner: speculative Whisper+LLM + filler loop + pipeline orchestration
│   └── tts.py                 # TTSClient: synthesize()->bytes + play_audio(bytes) split; speak() wrapper
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
│   ├── ack_yeah.mp3 / ack_yes.mp3 / ack_mmhm.mp3  # Kokoro Heart voice
│   └── fillers/
│       ├── neutral/            # clips pending gen_fillers.py
│       ├── cerebral/
│       └── empathetic/
├── scripts/
│   ├── generate_backchannel.py
│   ├── gen_fillers.py         # run with python3.11 to populate assets/fillers/
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
├── .env / auth_state.json
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
- **20s conversation mode timeout** — after response, stays in listening mode 20s before idle.
- **ScreenCaptureKit requires `.app` bundle** on macOS — silently fails from plain Python script.
- **ScreenCaptureKit TCC entries are tied to codesign identity** — if the `audio_capture` binary is recompiled without a stable `--identifier`, macOS generates a hash-based identity. After a TCC reset or macOS update, the old identity's permission entry becomes stale — `startCapture` silently hangs forever (no error, no dialog). Fix: always sign with `codesign --force --sign - --identifier com.operator.audio-capture audio_capture` after compiling. The binary has three layers of defense: (1) `CGPreflightScreenCaptureAccess()` pre-flight check that can trigger the permission dialog, (2) a 10-second watchdog that exits with code 4 if `startCapture` hangs, and (3) Python-side pre-launch codesign identity verification. Never ship `audio_capture` with only a linker-generated signature.
- **ScreenCaptureKit phantom hang (March 2026):** `startCapture` hung with no error despite `CGPreflightScreenCaptureAccess()` returning true and `SCShareableContent.get` succeeding — with no code changes, no recompile, and Screen Recording permission visibly toggled on. Recompiling, re-signing, toggling permission, and restarting VS Code all failed. A full system restart fixed it. Root cause: macOS `tccd` (TCC daemon) cached a stale permission denial in memory that didn't match the on-disk database. Likely triggered by an unclean process shutdown or sleep/wake cycle. Recovery ladder: (1) try `tccutil reset ScreenCapture` to flush TCC cache without rebooting, (2) if that fails, full system restart. Monitor for recurrence.
- **Chrome SingletonLock stale after unclean shutdown** — if the Operator process is killed (SIGKILL, crash, force quit) without running `browser.close()`, Chrome leaves a `SingletonLock` file in `browser_profile/` that blocks the next launch with "Failed to create a Process Singleton." The MacOSAdapter pre-launch cleanup (line 100-103) removes it automatically. To prevent: `__main__.py` now has SIGTERM handlers and try/finally blocks on both macOS and Linux entry points, and `_browser_session` closes the browser in a finally block. Only SIGKILL and hard crashes can still leave the lock.
- **Do NOT auto-re-sign `audio_capture` at runtime** — the original auto-retry logic re-signed the binary with `codesign --force --sign -` on capture failure, which changed the binary's code identity and invalidated the Screen Recording TCC grant for the parent app. This caused a cascading failure: the first run's re-sign broke all subsequent runs until the user toggled permission off/on and restarted the terminal app. Removed March 2026. Recovery for capture failures is now: (1) pre-launch codesign identity verification, (2) differentiated exit codes (3=permission denied, 4=watchdog timeout), (3) automatic `tccutil reset ScreenCapture` + retry on exit 4, (4) escalate to user with clear message.
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
- **CalDAV requires a Gmail app password** — a regular Gmail password will not work. App passwords are generated at myaccount.google.com/apppasswords (requires 2-Step Verification enabled on the account).
- **CalDAV app password must be stored in system keychain** — macOS Keychain or Linux Secret Service. Never store in `.env` or commit to the repo.
- **CalDAV poll interval is 1 minute** — this is the safe rate limit floor for Google's CalDAV endpoint. Do not poll faster.
- **Only accepted events appear via CalDAV** — the bot's Gmail must have accepted the meeting invite for the event to be visible. The user must accept invites on the bot's behalf; Operator cannot auto-accept.
- **Chrome requires `--no-sandbox` when running as root on a server** — without it, Chrome's audio service sandbox blocks PulseAudio socket access. Symptom: VirtualMic stays SUSPENDED, Meet shows "Microphone not found." Add to `launch_args` for both auth and guest paths.
- **Playwright `env=` in `launch()` replaces the full process environment** — passing `env={"DISPLAY": ":99"}` strips `XDG_RUNTIME_DIR`, `HOME`, `PATH`, etc. Chrome loses PulseAudio socket discovery. Fix: do NOT pass `env=` at all; set `DISPLAY` in the caller's environment before launching Python (`DISPLAY=:99 python3 run_linux.py`).
- **Chrome 130+ uses PipeWire by default for WebRTC audio on Linux** — if PipeWire is not installed, WebRTC audio capture silently fails (VirtualMic SUSPENDED) while video and regular Chrome audio still work. Fix: add `--disable-features=WebRTCPipeWireCapturer` to Chrome launch args to force PulseAudio.
- **Kokoro 0.9.4 requires spaCy `en_core_web_sm` model** — `KPipeline` downloads it at first run via `pip`. On systems where `pip` is not on PATH (Homebrew Python 3.14), download fails with "No package installer found." Fix: install the model directly — `pip3.11 install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl`. Kokoro is installed under Python 3.11 (`pip3.11 install kokoro soundfile`); run benchmark via `python3.11 scripts/bench_tts.py`.
- **Kokoro voice `am_cloud` does not exist** — the full voice list is in the HuggingFace repo `hexgrad/Kokoro-82M/voices/`. American Female: af_heart, af_sky, af_bella, af_sarah, af_nova, af_alloy, etc. British Female: bf_emma, bf_isabella, bf_alice, bf_lily. Use `am_michael` or `am_puck` for American Male.
- **PulseAudio user-mode on the droplet dies without `--exit-idle-time=-1`** — `pulseaudio --daemonize` as root exits immediately at idle. Fix: `pulseaudio --daemonize --exit-idle-time=-1`. Add to startup procedure.
- **Whisper cold-start on the droplet is ~28s; subsequent runs are <2s** — first inference triggers JIT/model warmup. Not fixable in code; warm up Whisper before entering the transcription loop or use a persistent inference thread. Addressed in Step 7.6.
- **mpv drain inflated to ~5s for short TTS clips** — mpv buffers aggressively; drain time does not track audio duration. Investigate `--audio-buffer=50` or streaming TTS directly to parec/pacat to bypass mpv.

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

### Step 6.1.5 — Replace `calendar_join.py` with `caldav_poller.py`

Do this before simplifying `app.py` so that the old `CalendarPoller` import is gone before the thin-shell refactor — one clean pass instead of two partial ones.

Delete `calendar_join.py`. Create `caldav_poller.py` in the repo root. The poller:
- Connects to the bot's Google Calendar via CalDAV using `caldav` library + app password from system keychain
- Polls every 60 seconds (do not poll faster — this is Google's safe rate floor)
- For each event starting within the join window: checks that the event is accepted and has a Google Meet link
- Calls `connector.join(meet_url)` for matching events

Keychain access: use `keyring` library (`keyring.get_password("operator", bot_gmail)`). The setup wizard (Phase 9) writes the credential; the poller reads it. For this step, store the credential manually: `keyring.set_password("operator", bot_gmail, app_password)`.

CalDAV connection pattern:
```python
import caldav, keyring
password = keyring.get_password("operator", bot_gmail)
client = caldav.DAVClient(
    url="https://www.google.com/calendar/dav/{bot_gmail}/events/",
    username=bot_gmail,
    password=password,
)
```

Remove `google-api-python-client` and `google-auth-oauthlib` from `requirements.txt`. Add `caldav` and `keyring`.

**Test:** With app password manually stored in keychain, create a test calendar event with a Meet link starting in 2 minutes → confirm poller calls `connector.join()`.
**Commit:** `feat: replace calendar_join.py with caldav_poller.py — CalDAV-based meeting detection`

---

### Step 6.2 — Simplify `app.py` to use `runner.py`

`app.py` becomes a thin macOS shell: instantiate `MacOSAdapter`, instantiate `AgentRunner`, wire state change callbacks to menu bar icon updates, call `runner.run()`. Wire `caldav_poller.py` here instead of the old `CalendarPoller`.

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

### Step 7.1 — Test audio quality on native AMD64 (no QEMU) ✅

Tested on `operator-dev` droplet (64.23.182.26, native AMD64, no Docker). Audio still choppy — QEMU ruled out. Root cause is sample rate mismatch (see Step 7.2). See Hard-Won Knowledge for full finding.

---

### Step 7.2 — Sample rate audit + fix ✅

**Diagnosed (March 26, 2026):** PulseAudio virtual sinks default to 44100Hz. Chrome's WebRTC engine runs at 48000Hz. PulseAudio's real-time 44100→48000 SRC (sample rate conversion) using the default `speex-float-1` resampler causes audible artifacts.

**Fix:** Added `rate=48000` to both `pactl load-module module-null-sink` calls in `scripts/linux_setup.sh`. Also fixed three blockers in `LinuxAdapter` discovered during live test (March 27, 2026):
1. Added `--no-sandbox` to Chrome launch args — required when running as root; without it Chrome's audio sandbox blocks PulseAudio.
2. Removed `env={"DISPLAY": display}` from `p.chromium.launch()` — Playwright replaces the full environment if `env=` is passed, stripping `XDG_RUNTIME_DIR` and breaking PulseAudio socket discovery.
3. Added `--disable-features=WebRTCPipeWireCapturer` — Chrome 130+ tries PipeWire first; fails silently on droplet (no PipeWire). Forces PulseAudio for WebRTC audio.

**Result:** Voice confirmed clear through WebRTC in live meeting (March 27, 2026). VirtualMic RUNNING, audio flowing to parec, Whisper transcribing correctly.

---

### Step 7.3 — TTS provider benchmark

ElevenLabs was chosen without a systematic evaluation. Before investing further in TTS reliability (Step 7.5), benchmark all three viable providers against each other in the actual meeting audio chain (after the 48kHz fix is in place).

**Providers to test:**
- **ElevenLabs** (`eleven_flash_v2_5`) — current provider. High voice quality. Requires paid plan.
- **OpenAI TTS** (`tts-1` / `tts-1-hd`) — same OpenAI API key already in use. Streaming supported. One fewer vendor.
- **Piper** (local, open source) — runs on the machine, no API call, no cost, outputs natively at any sample rate. Lower voice quality but aligns with open-source-first direction.

**Test phrases:** Use the same 8–10 phrases for all three — mix of short acknowledgments ("Got it, one moment"), longer explanations (2–3 sentences), and technical language.

**Measure for each:**
1. Latency to first audio chunk (time from `speak()` call to audio starting)
2. Total playback time per phrase
3. Cost per character (or $0 for Piper)
4. Setup complexity (install steps, dependencies added)
5. Voice quality through WebRTC — listen in an actual meeting, not just locally. WebRTC's Opus codec compresses audio; naturalness degrades differently per voice.

**Decision criteria:** Document scores in a short table. Pick the provider that best balances quality-through-WebRTC, latency, and vendor count. Update `config.yaml`, `requirements.txt`, and `pipeline/tts.py` for the winning provider.

**Test:** Full wake → LLM → TTS → meeting participants hear Operator cycle with the chosen provider.
**Commit:** `feat: switch TTS provider to [provider] — benchmark results in commit body`

---

### Step 7.4 — Tune filler phrase silence threshold

The silence threshold for firing backchannel filler phrases (in `pipeline/conversation.py` or `pipeline/audio.py`) needs tuning. Current behavior: [note current value here before starting]. Goal: fires only when there is actual silence after a direct question to the agent — not during the speaker's natural pauses.

Test with multiple human speech patterns. Adjust the threshold until fillers feel natural. Document the final value and rationale.

**Commit:** `tune: adjust filler phrase silence threshold to N ms — rationale in comment`

---

### Step 7.5 — TTS reliability improvements

After the provider decision in Step 7.3: add retry logic for transient API failures (e.g. 3 retries with exponential backoff) for whichever provider was chosen. Add graceful degradation: if TTS fails after retries, log the error and post the response text to meeting chat as a fallback (requires `send_chat()` to be wired up). Skip this step if Piper was chosen (local — no API failures possible).

**Test:** Simulate API failure (temporarily set an invalid API key). Confirm graceful log + no crash.
**Commit:** `fix: add retry logic and graceful degradation to pipeline/tts.py`

---

### Step 7.6 — STT accuracy review

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
    "caldav", "pyyaml",
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
8. CalDAV setup:
   - Ask: bot's Gmail address (e.g. yourname.operator@gmail.com)
   - Open `https://myaccount.google.com/apppasswords` in the default browser automatically (`webbrowser.open(...)`)
   - Display inline instructions: "1. Sign in if prompted. 2. Under 'Select app', choose 'Other' and name it Operator. 3. Click Generate. 4. Copy the 16-character password."
   - Ask: paste the 16-character app password
   - Validate: attempt a CalDAV connection (`caldav.DAVClient(...)`) — print success or error before proceeding
   - Store credential in system keychain: `keyring.set_password("operator", bot_gmail, app_password)`
   - Print: "Accept meeting invites sent to [bot_gmail] and Operator will join automatically."
9. Ask: Google account for agent? (y/n) — if yes, open browser for one-time login via `scripts/auth_export.py`
10. OS-specific audio setup:
    - macOS: `brew install blackhole-2ch` (silent), write Chrome mic preference to profile JSON
    - Linux: run `scripts/linux_setup.sh`
11. Write `config.yaml` (include `caldav.bot_gmail` field)
12. Print: "Setup complete. Run `python -m operator <meeting-url>` to start."

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

## Key Decisions

- **Meeting detection:** CalDAV polling (1 min interval). App password stored in system keychain. No OAuth, no Cloud Console, no credentials.json. Implemented in Phase 9.
- **Guest join:** Locked default. "Ask to join" — host admits the bot. Authenticated join via `auth_state.json` is opt-in only. Existing connector join logic is unchanged.
- **Platform scope:** Google Meet only for v1. Zoom and Teams are v2.

---

## Open Questions

1. **Audio quality root cause** — QEMU ruled out (tested native AMD64, March 2026 — still choppy). Root cause is sample rate mismatch in TTS → PulseAudio → Chrome → WebRTC chain. Audit in Phase 7.2.
2. **Wake phrase customization** — allow users to set their own wake phrase in `config.yaml`? Test Whisper reliability on custom phrases before committing.
3. ~~**Calendar auto-join**~~ — **Resolved.** CalDAV polling (1 min interval) implemented in Phase 9. Bot's Gmail receives invites; user accepts on bot's behalf; Operator polls and auto-joins.
4. **Linux distro coverage** — Ubuntu/Debian tier-1; PulseAudio vs. PipeWire (Fedora, Ubuntu 22.04+) needs separate validation path.
5. ~~**Calendar secrets in cloud**~~ — **Moot.** CalDAV uses only a Gmail app password stored in system keychain. No `credentials.json`, no `token.json`, no OAuth app.
