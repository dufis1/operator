# Operator — Roadmap

*Last updated: April 14, 2026 (session 100)*

> **Current status: Phase 11.3b scaffold landed (code-complete, awaiting live test); caption bridge now late-bind safe so calendar mode is unblocked. MVP ship target still April 19, 2026.** **Session 100 (April 14)** — landed the late-bind caption fix: `MacOSAdapter` now exposes `window.__onCaption` and injects the observer at browser startup whenever `CAPTIONS_ENABLED` is true, independent of whether a callback is registered. `set_caption_callback()` is now safe to call before OR after `join()`, so calendar-polling mode can wire a per-meeting `MeetingRecord` + `TranscriptFinalizer` once a URL arrives via the calendar queue without a browser restart. Added `tests/test_caption_late_bind.py` with 7 cases (drop-with-no-callback, register/receive, late-bind, swap routes, None unregisters, exception swallowed without breaking bridge, `filter_caption` short-circuit) — all pass; full chat-only suite still green. Per-meeting calendar wiring itself still needs `ChatRunner.run_polling()` (referenced from `__main__.py:304` but not present on chat-only main — voice-era leftover). **Session 99 (April 14)** — ported the Google Meet caption observer from `voice-preserved` into main: new `connectors/captions_js.py` holds the verbatim MutationObserver JS + enable/filter helpers; `MacOSAdapter` gains `set_caption_callback()` and the JS bridge; new `pipeline/transcript.py` buffers deltas and flushes finalized utterances on speaker-change or silence gap; `MeetingRecord` gets `kind: caption` entries interleaved with chat by timestamp; `LLMClient._tail_messages` renders them as `[spoken] <name>: <text>` so the model reads them as ambient room talk, not prompts to respond. Gated behind `transcript.captions_enabled: false` (privacy default). Unit-tested the finalizer + interleaving; all existing tests still green. Test plan written at `docs/11_3_b_testing.md`. **Known limitations to address post-live-test:** (a) calendar-mode captions need a separate wiring pass (currently only direct-URL path wires captions), (b) no automated regression test for observer drift if Meet's caption DOM shifts. **Session 98 (April 14)** — Phase 11.3a COMPLETE and live-Meet validated. MVP ship target still April 19, 2026.** **Session 98 (April 14)** — walked the full `docs/11_3_a_testing.md` sheet against a fresh meeting (`bxb-jytq-tmc`). All 12 tests passed (Test 5 skipped per doc; Test 3 config-driven proof skipped). Confirm flow + correction re-propose works (`save_issue` `demo` → MOJ-16; `foo`→`gloo` correction → MOJ-17). Tool-loop scratchpad never persists (zero `tool_use`/`tool_result` lines on disk). Alone-exit grace fired at 61s. Two minor non-blockers noted: one DOM-poll missed a bare `ok` confirmation (recovered on retry), and the LLM occasionally needs a nudge to surface non-addressed context. Closed the session by auditing `CLAUDE.md` against the post-11.3a codebase — fixed three stale sections (architecture overview missing `meeting_record.py`, data-flow describing pre-11.3a "rolling history", config block listing wrong fields under `agent`/`llm`/`connector`). **Session 97 (April 14)** — positioning session after competitive research surfaced [joinly](https://github.com/joinly-ai/joinly) as a near-peer (multi-platform, voice, MCP, BYOLLM, MIT, hosted cloud, shipped 6+ months ago). Response: lean into **"Claude Code in your Google Meet"** as the launch framing and **opinionated quickstart** as the implicit differentiator. Added new **Phase 15.5 (Opinionated Quickstart)** — agents gallery + minimal `operator setup` wizard + a second chat-native agent slot. `agents/` directory created with format `README.md`; `claude-code` is the canonical starter. `docs/mvp-bar.md` updated with sharpened 3-part differentiation and a new Launch Strategy section (hero framings, visual hooks, distribution ranking, gallery as distribution primitive). Session 96 shipped 11.3a — per-meeting JSONL at `~/.operator/history/<slug>.jsonl` as the single source of truth for chat history. Phase 11 remaining: 11.3b (captions, ~3h), 11.4 (skill loading, ~2h), 11.5 (Claude Code skill import, ~2h), 11.7 (provider keys optional, ~15m). 11.6 absorbed into 11.3a. After Phase 11: Phase 12 validation, 13 polish, 14 package, 15 cross-platform testing, 15.5 opinionated quickstart (new), 16 launch. Fast-follow audit items remain opportunistic. Post-MVP: Hosted Operator (~30-35h). Zero-telemetry intact.

---

## Completed Phases

<details>
<summary>Phases 1–6 + Caption Refactor + MCP Integration (all ✅)</summary>

### Phase 1: Extract the Agent Pipeline ✅
Pulled all "brain" logic out of `app.py` into `pipeline/` with zero macOS-specific code.

### Phase 2: Define the Connector Interface ✅
Defined `MeetingConnector` abstract interface. Wrapped macOS logic as `MacOSAdapter`.

### Phase 3: Docker/Cloud Adapter ✅
Built headless Linux adapter in Docker. Verified end-to-end in live Google Meet.

### Phase 4: Reorient — Cloud Cleanup + Linux Local Adapter ✅
Moved cloud artifacts into `cloud/`. Adapted Docker adapter into `LinuxAdapter` for local Linux machines.

### Phase 5: Config System ✅
All hardcoded constants moved into `config.yaml` with `config.py` reader.

### Phase 6: Consolidate Entry Points ✅
Shared loop extracted into `pipeline/runner.py`. OS auto-detection via `python -m operator`. Calendar poller replaced `calendar_join.py`.

### Caption Refactor ✅
Replaced ScreenCaptureKit + Whisper with Google Meet DOM caption scraping. Eliminates echo problem, privacy issues, and Whisper dependency on macOS.

### Phase 11 (original): MCP Integration ✅
MCP client connects to configured servers at startup, discovers tools. Tool-call loop in LLMClient handles tool_call → execute → result → re-prompt. Chat-specific LLM settings. Config schema for `mcp_servers`. Validated with Linear and GitHub MCP servers end-to-end in live Meet. GitHub MCP server upgraded from deprecated npm package to official Go binary (`github/github-mcp-server` v0.32.0).

### Phase 7: Performance Iteration ✅ (partial)
Audio quality, TTS 3-tier architecture, latency masking, STT accuracy (mlx-whisper), streaming classification, playback interrupt classification, latency docs. Deferred to voice phase: TTS error handling/retry, premature finalization at 0.7s silence threshold.

</details>

---

## Phase 8: Chat-First MVP (THE CURRENT FOCUS)

*The pivot. Ship a chat-based task delegation bot as v1. Voice layers on top later.*

**Why:** A real user (engineer at a remote company) wants to delegate tasks to the bot via Google Chat during meetings. Chat I/O is simpler than voice — no wake detection, no latency tuning, no TTS. Ship something, get feedback, expand from there.

**Scope:** Google Meet only. Mac + Linux (Playwright is cross-platform, so Linux support is essentially free for chat-based interaction). Zoom/Teams deferred until there's confirmed user demand — each platform requires its own DOM selectors, join flow, and auth story.

**Core reframe:** Voice and chat are interaction layers over a shared capability layer. The audio pipeline stays in the codebase untouched.

| Step | Description | Status |
|------|-------------|--------|
| 8.0 | Clean the house — reorganize root, consolidate docs | ✅ |
| 8.1 | Chat I/O proof of concept — bot reads and writes Google Chat messages during a live meeting (echo test, no LLM). Create `ChatRunner` alongside `AgentRunner` — same `LLMClient`, simpler I/O loop. No codebase reorg needed; existing connector/pipeline separation already fits. | ✅ echo test passing e2e |
| 8.2 | Wire up the brain — connect chat input to LLM, respond in chat | ✅ |
| 8.2.1 | Chat hardening — history cap (configurable), wake phrase gating for multi-participant, sender field extraction from DOM | ✅ |
| 8.2.2 | Meeting lifecycle — pre-join user gate, end-time auto-leave, stale meeting skip, Ctrl+C clean shutdown, alone-exit auto-leave (session 86: after others were present, drop count→1 for 60s triggers leave; chat path tested live, voice path mirrored untested) | ✅ |
| 8.3 | Ship to friend — minimal setup, clear instructions, get it in his hands | ✅ | ~2h |

---

## Phase 9: Hardening & Reliability

*Make what we have bulletproof before shipping to strangers.*

| Step | Description | Status | Est. |
|------|-------------|--------|------|
| 9.1 | UI dependency audit — inventory every DOM selector and UI interaction; classify as stable (API-backed) vs. fragile (class names, layout-dependent) | ✅ | ~2h |
| 9.2 | ~~DOM regression test suite~~ — deferred to Phase 12 (post-MVP maintenance tooling) | ⏭️ | — |
| 9.3 | ~~Self-healing selectors~~ — deferred to Phase 12 (follows regression suite) | ⏭️ | — |
| 9.4 | Race condition audit — systematic review of threading, queue interactions, shutdown paths, and browser thread coordination | ✅ | ~3h |
| 9.5 | Security vulnerability audit — input sanitization, credential handling, MCP server sandboxing, dependency audit | ✅ | ~2h |
| 9.6 | Simultaneous meeting handling — single-meeting design: queue overlaps, skip ended meetings, log warnings | ✅ | ~1h |
| 9.7 | Calendar polling startup latency — profile and optimize the slow path from launch to first meeting join | ✅ | ~1h |
| 9.8 | ~~Log cleanup~~ — deferred to Phase 12 (post-MVP polish) | ⏭️ | — |
| 9.9 | ~~Latency audit~~ — deferred to Phase 12 (post-MVP polish) | ⏭️ | — |
| 9.10 | ~~Comprehensive error handling pass~~ — deferred to Phase 12 (post-MVP polish) | ⏭️ | — |
| 9.11 | Chat message size management — investigate Google Meet chat character limits, truncate/summarize long tool results, fix overly verbose Operator responses | ✅ | ~2h |
| 9.12 | Tool call timeout + heartbeat — visible "still working..." in chat for long-running calls, hard timeout with graceful failure | ✅ | ~2h |
| 9.13 | Context window management — strategy for summarizing/truncating older chat history as conversation grows, prevent silent context overflow | ✅ | ~3h |
| 9.14 | ~~Idempotency guards~~ — deferred to Phase 12 | ⏭️ | — |
| 9.15 | Offline/reconnection behavior — detect browser crash/page loss via `is_connected()` on connector; ChatRunner exits loop cleanly; health check tightened to 30s with `page.is_closed()` detection. Decision: exit cleanly (no auto-rejoin). | ✅ | ~2h |
| 9.16 | ~~Edge case pass~~ — deferred to Phase 12 | ⏭️ | — |

---

## Phase 10: MCP Finalization & Hardening

*Finalize MCP as a first-class capability. Trimmed for MVP — see Post-MVP for deferred items.*

| Step | Description | Status | Est. |
|------|-------------|--------|------|
| 10.1 | Per-MCP `hints` field in config — server-specific LLM guidance injected into system prompt | ✅ | ~1h |
| 10.7 | Per-server hints content — Linear + GitHub hints filled, validated via 12-test suite, then audited and trimmed. Removed over-directing hints (list_issues filter mandate, preemptive size check, search_issues preference, project summary follow-up). Added status-recovery hint (project vs team confusion). Revised search_code hint. Confirmation handler rewritten: word-boundary matching for affirmatives only, all non-affirmative responses pass user's message to LLM for interpretation. | ✅ | ~4h |
| 10.4 | BYOMCP guard rails — execution timeouts, result size caps with truncation + guidance message ("Result was too large — add a hint to use more targeted queries"), binary/non-text content detection and rejection (prevents the image-poisoning session-brick from GitHub G7), `confirm-all` default for untested servers, verbose MCP debug logging to `/tmp/operator.log` (full request/response/rejection reason so users can self-diagnose and write hints). Covers 3 critical code-change findings: G7 binary poison, G6 large file context blow, L4 unfiltered list_issues | ✅ | ~4h |
| 10.2 | Tool confirmation — auto-approve reads, confirm writes. Hardcoded `READ_TOOLS` allowlist, per-server `confirm_tools` override in config, centralized `_dispatch_result` routing | ✅ | ~2h |
| 10.3 | User-defined MCP servers — users add custom servers in config with command, args, env, hints. Config-load env var warnings, categorized startup failure messages, LLM-aware loaded/failed server status, `--check-mcp` validation CLI | ✅ | ~2h |
| 10.5 | Startup health check — on launch, start each configured MCP server, call `list_tools()`, report failures before joining a meeting. No CI job, no dry-run tool calls — just "can we connect?" | ✅ (via 10.3 `--check-mcp`) | ~1h |
| 10.6 | Runtime failure backoff — per-server consecutive error counter on MCPClient; after 3 failures a server is disabled (tools filtered from `get_openai_tools`, `execute_tool` short-circuits with LLM-facing steering text). One-shot chat announcement + idempotent `inject_mcp_status` re-inject with a new `disabled_runtime` bucket. Timeouts count as failures via `_record_mcp_outcome` in chat_runner. Unit-tested + live-validated with `debug/flaky_mcp_server.py` | ✅ | ~1h |

**Phase total: ~12h**

---

## Phase 11: Multi-Model & Customization

*Break the OpenAI lock-in and deliver the "your AI, not a generic bot" half of the MVP positioning. Voice deferred — see Post-MVP.*

| Step | Description | Status | Est. |
|------|-------------|--------|------|
| 11.1 | Abstract LLM provider interface — swap between OpenAI and Anthropic without code changes | ✅ | ~3h |
| 11.2 | Anthropic API backend — Claude as alternative LLM provider | ✅ | ~3h |
| 11.3a | Meeting record as single source of truth — every chat-panel message (addressed, non-addressed, Operator's own replies) appends to a per-meeting JSONL file at `~/.operator/history/<meeting_id>.jsonl`. `LLMClient.ask()` reads the tail of that file and replays it as context on each call; no parallel in-memory pair buffer competing with it. Absorbs what was 11.6 (persistence) — the file IS the record. Tool-call/tool-result state stays in a small in-memory scratchpad for the in-flight turn only. `history_turns` renamed to `history_messages`. Meta header line in the JSONL (`kind: meta`, url + slug + timestamp) makes each file self-describing. Config-driven first-contact greeting via `agent.first_contact_hint` with `{first_name}` placeholder. Unit-tested end-to-end; live-test sheet at `docs/11_3_a_testing.md`. | ✅ | ~4h |
| 11.3b | Captions as transcript source — port the Google Meet live-captions DOM scraper from the `voice-preserved` branch (`CaptionsAdapter` / `pipeline/captions.py`) into a new `pipeline/transcript.py`. Captions append to the same meeting-record JSONL with `kind: caption`, unified with chat messages. Gate behind a `transcript.captions_enabled` config flag (default off — user opt-in for privacy). Degrade gracefully when captions aren't turned on in the meeting. | 🧪 scaffold landed — live test pending (`docs/11_3_b_testing.md`) | ~3h |
| 11.4 | Skill file loading — users drop markdown files in a `skills/` directory (path configurable); contents appended to the system prompt at runtime so the bot reflects user identity, team conventions, ticket formats, etc. Delivers the "your AI, not Gemini" customization layer promised in `docs/mvp-bar.md` | ⬜ | ~2h |
| 11.5 | Import Claude Code skills — reference-style import of user's existing `~/.claude/skills/` into Operator. `config.yaml` gets a `skills:` list of paths; on startup, resolve each path, parse `SKILL.md` frontmatter, inject `name + description` into the system prompt (progressive disclosure — full body loads when the model asks for it). Missing paths warn + skip, don't crash. Supports a gitignored `skills.local.yaml` for personal overrides so teams can share a baseline `config.yaml`. Startup log line prints resolved vs. skipped skills. Pitched as "Claude Code in Google Meet" — bring your instruction skills. Filter/warn on `allowed-tools` Operator can't honor (Bash/Edit/Write/Read). See `docs/agent-context.md` for full implementation notes. | ⬜ | ~2h |
| 11.6 | ~~Conversation history persistence~~ — **absorbed into 11.3a.** The meeting record file IS the persistence layer; no separate step needed. | ✅ (by absorption) | — |
| 11.7 | Provider API keys optional at import (L1-c audit) — `config.py:96` currently does `os.environ["OPENAI_API_KEY"]`, which KeyErrors on import when the user has chosen Anthropic-only. Change to `.get(..., "")` so the required-key check happens in `build_provider()` based on the configured provider. Also unblocks `--check-mcp` without a provider key. Fixes a real out-of-box BYOK-Anthropic crash for anyone cloning the repo. | ⬜ | ~15m |

**Phase total: ~14.25h** (was 13.75h — 11.3 split into 11.3a/11.3b with 11.6 absorbed)

---

## Phase 12: Validation

*Quick validation pass across both LLM providers. Not a full test matrix.*

| Step | Description | Status | Est. |
|------|-------------|--------|------|
| 12.1 | Model validation — test core chat + MCP flow with GPT-4.1-mini and Claude (one model per provider). Verify tool calls, confirmations, error handling | ⬜ | ~2h |
| 12.2 | MCP cross-provider validation — verify Linear + GitHub tools work correctly with both OpenAI and Anthropic. Focus on tool call format differences between providers | ⬜ | ~1h |

**Phase total: ~3h**

---

## Phase 13: Polish

*Clean stdout, clean config. No latency audit, no comprehensive error pass.*

| Step | Description | Status | Est. |
|------|-------------|--------|------|
| 13.1 | Config audit & cleanup — remove dead keys, ensure every key is necessary, document what each controls | ⬜ | ~2h |
| 13.2 | Log cleanup — structured, consistent log levels; clean stdout for normal operation (no debug spam, no stack traces for expected errors), verbose debug stays in `/tmp/operator.log` | ⬜ | ~2h |

**Phase total: ~4h**

---

## Phase 14: Package

*Minimal packaging for "clone and run."*

| Step | Description | Status | Est. |
|------|-------------|--------|------|
| 14.1 | Add `LICENSE` (MIT) | ⬜ | ~5m |
| 14.2 | Dependency pinning — `requirements.txt` with pinned versions for reproducible installs. No Python version matrix, no lockfile tooling | ⬜ | ~1h |

**Phase total: ~1h**

---

## Phase 15: Cross-Platform Testing

*Prove it works on both platforms. Replaces the setup wizard (deferred to Post-MVP).*

| Step | Description | Status | Est. |
|------|-------------|--------|------|
| 15.1 | Linux testing — dedicated session on a real Linux box (not Docker). Fresh clone, full setup, join a meeting, chat interaction, MCP tool use. Fix whatever breaks | ⬜ | ~3h |
| 15.2 | Fresh clone test (macOS) — new directory, follow the README exactly, no prior state. Verify the "one sitting" promise | ⬜ | ~1h |

**Phase total: ~4h**

---

## Phase 15.5: Opinionated Quickstart

*The "choose your fighter → add power-ups → go" layer. Implicit differentiator vs. joinly's framework-shaped positioning. See `docs/mvp-bar.md` for strategy context.*

| Step | Description | Status | Est. |
|------|-------------|--------|------|
| 15.5.1 | Agents gallery + `claude-code` starter — `agents/README.md` defines the format (done session 97). Build out `agents/claude-code/` as the canonical example: complete runnable `config.yaml`, short `README.md` covering what/who/needs/setup/demo, optional bundled skills in `agents/claude-code/skills/`, `.env.example` for required keys. Verify end-to-end on a fresh clone. Depends on 11.5 (Claude Code skill import) being complete. | ⬜ | ~1.5h |
| 15.5.2 | `operator setup` interactive wizard (minimal) — `python -m operator setup` script that: (a) asks which agent to start from (list of `agents/*` folders + "blank / custom"); (b) copies that agent's `config.yaml` to repo root (or user-specified path); (c) prompts for API keys and writes them to `.env`; (d) optionally runs `--check-mcp` before finishing. No daemon, no background service — one-shot config-writer. Writes are atomic; re-running overwrites safely. ~150 lines of Python. Matches OpenClaw's onboarding shape without pulling forward a full background-service architecture. | ⬜ | ~2h |
| 15.5.3 | Second chat-native agent — user will pick the concrete agent near launch. Candidates: `standup` (files standup summaries + blockers to Linear), `triage` (converts feature-request chatter into Linear tickets), `incident-commander` (opens GitHub issues during incident calls, cross-links, pings oncall), `interview-notes` (structured candidate signal), `research` (live web lookup via Tavily MCP, drops citations). Translator was considered but is voice-native. Goal: one more folder in `agents/` so the gallery reads as a set, not a single example. | ⬜ | ~1h |

**Phase total: ~4.5h**

---

## Phase 16: README & Launch ← MVP GATE

*Ship it.*

| Step | Description | Status | Est. |
|------|-------------|--------|------|
| 16.1 | Rewrite `README.md` — what it is, quick start, architecture, "meetings that produce artifacts" positioning. **Must read as 3 steps** (choose an agent → add your keys/power-ups → join a meeting); agents gallery (`agents/`) is the entry point; link `claude-code` as the canonical starter. Includes: MCP compatibility notes (tested servers + known quirks), BYOMCP failure patterns and mitigation guidance, one annotated example config. Explicit "Claude Code in your Google Meet" framing in the hero. | ⬜ | ~4h |
| 16.2 | Demo video/GIF — 30s screen recording embedded at top of README. **Hero hook: an artifact (Linear ticket / GitHub comment) materializing in chat while the speaker is still mid-sentence.** Visual surprise is the point; feature enumeration is not. Second demo (optional): drag-and-drop a skills folder and watch behavior change. | ⬜ | ~2h |
| 16.3 | Launch campaign prep — draft 3 hero-framing posts (see `docs/mvp-bar.md` Launch Strategy section) tailored to their target channels: (a) "Claude Code in your Google Meet" for r/ClaudeAI + Claude-focused creators; (b) "The AI in my standup filed 3 Linear tickets before I finished talking" for r/ExperiencedDevs + eng newsletters; (c) a role-specific build tied to the second chat-native agent from 15.5.3. Prepare the seeded-PR plan for `agents/` (personas + use cases, 3 PRs queued for week 1). Direct-outreach shortlist: 10–20 Claude Code power users in SF. Identify 2–3 AI/PM newsletter operators for earned or paid mentions (Pika's playbook). | ⬜ | ~3h |

**Phase total: ~9h**

---

**MVP total: ~53.5h across Phases 10–16 (against ~50–55h available through April 19, 2026). Right at the ceiling — 15.5 and 16.3 are the new swelling items; if the budget slips, 15.5.3 can move to week 1 post-launch and 16.3 can start earlier in parallel with 15.5.**

---

## Phase 17: Upstream Drift Monitoring

*First post-MVP priority. Detect silent breakage from upstream changes (model deprecations, MCP tool schema drift, DOM changes, dep bumps) before users hit them in a live meeting.*

**Why:** The product sits on top of many moving surfaces we don't control — LLM APIs, hosted MCP servers, pinned binaries, Google Meet DOM, Playwright/Chromium, virtual audio stack, OS APIs. Nothing currently watches any of them. Failures only surface mid-meeting.

| Step | Description | Est. |
|------|-------------|------|
| 17.1 | Automated diff checks (weekly cron, opens GitHub issue on diff): (a) OpenAI + Anthropic model-list endpoints vs. configured models; (b) `list_tools()` schema snapshot diff per configured MCP server (catches hosted Linear/Gmail/Calendar changes + local GitHub binary drift); (c) GitHub releases for pinned MCP binaries (GitHub MCP `v0.32.0`, `mcp-remote`); (d) `pip list --outdated` filtered to critical deps (openai, anthropic, playwright, faster-whisper, mlx-whisper, rumps, kokoro); (e) Python + macOS EOL date reminders | ~3h |
| 17.2 | Weekly smoke canary — headless run joins a test Meet, sends a chat message, invokes one read tool per configured MCP server, asserts success. Single pass catches Meet UI drift, OAuth expiry, hosted MCP outages, BlackHole/mpv regressions, Playwright/Chromium bumps | ~2h |
| 17.3 | Manual checkpoint runbook — quarterly ~15min checklist for fuzzy surfaces not amenable to automation: Google Meet / OpenAI / GitHub / Linear ToS pages, Meet UI walkthrough, macOS version compatibility, kext/codesigning requirements, Kokoro model repo status | ~1h |

**Phase total: ~6h**

---

## Post-MVP

*Everything below was scoped out of the 7-day MVP window. Prioritize based on user feedback after launch.*

### MCP Enhancements
| Item | Origin | Description |
|------|--------|-------------|
| Configurable confirmation modes | 10.2 (original) | Full 4-mode system: `auto-all`, `read-auto`, `confirm-all`, `session-trust` + `batch_preview` toggle |
| Read-only tool classification engine | 10.3 (original) | Auto-classify tools as read/write from descriptions at discovery time |
| Managed MCP client layer | 10.6 | Point at an MCP proxy/gateway (Cloudflare, etc.) instead of local stdio servers |
| Pin MCP server versions | 10.7 | Lock `mcp-remote` and GitHub binary to specific versions in config |
| MCP server CI health check | 10.8 (original) | CI job that starts servers + dry-run tool calls, alert on failure |
| Runtime failure monitoring dashboard | 10.9 (original) | Per-server failure rate tracking, threshold alerts, diagnostics surface |
| Idempotency guards | 10.10 | Detect duplicate tool actions from repeated requests; dedup logic beyond write confirmation |
| Parallel tool calls | 11.2 | Currently disabled on both providers (`parallel_tool_calls=False` on OpenAI, `disable_parallel_tool_use=True` on Anthropic) so the one-tool-at-a-time loop in `LLMClient.ask()` stays safe. Re-enable for skills that fan out across MCP servers (e.g. "what's on my plate across Linear + GitHub?"). Requires `LLMClient` to execute N tool_calls per turn and feed back N tool_results before the next LLM turn. |

### Multi-Modal & Voice
| Item | Origin | Description |
|------|--------|-------------|
| Local LLM support | 11.3 | Ollama/llama.cpp for zero-API-key deployment. Experimental tier — local models are weak at tool use |
| Voice: premature finalization fix | 11.5 | Resolve 0.7s silence threshold cutting off mid-sentence |
| Voice: TTS reliability | 11.6 | Error handling and retry logic for TTS |
| Voice: partial-wake validation | 11.7 | Validate idea #6 from latency.md |
| Voice: MODE config key | 11.8 | `voice` \| `chat` \| `both` in config.yaml |

### Testing & Hardening
| Item | Origin | Description |
|------|--------|-------------|
| OpenAI model matrix | 12.1 (original) | Full matrix: GPT-4.1-mini, GPT-4.1, GPT-4o, o3-mini |
| Full MCP pressure testing | 12.2 (original) | Every tool x every model, explicit + implicit + indirect requests |
| DOM regression test suite | 12.3 / 9.2 | Automated tests against live Meet on a schedule |
| Self-healing selectors | 12.4 / 9.3 | Fallback strategies when primary selectors fail |
| Edge case pass | 12.5 / 9.16 | Boundary conditions: empty meetings, rapid join/leave, Unicode, concurrent tool calls, memory leaks |
| Latency audit | 13.3 / 9.9 | Profile end-to-end chat path, shave unnecessary delays |
| Comprehensive error handling | 13.4 / 9.10 | Rate limiting, runaway loop prevention, full graceful failure pass |
| Telemetry / diagnostics | 13.5 | Opt-in anonymous usage stats |
| Dependabot + pip-audit | 13.6 | Automated dependency PRs + CVE detection in CI |
| Log cleanup (advanced) | 9.8 | Beyond MVP log cleanup — structured logging, log rotation |
| Idempotency guards | 9.14 | Duplicate action prevention |

### Packaging & Community
| Item | Origin | Description |
|------|--------|-------------|
| `pyproject.toml` | 14.1 | Package name, Python version, entry points |
| Python version matrix | 14.3 (expanded) | Test across 3.11, 3.12; lockfile tooling |
| CI/CD pipeline | 14.4 | Automated tests on PR, release tagging, PyPI publish |
| Contributing guide | 14.5 | Code standards, PR process, how to add MCP servers |
| MCP compatibility matrix (full) | 14.6 | Detailed tested servers doc with model-specific behavior notes |
| Changelog / release notes | 14.7 | CHANGELOG.md, semver tagging |
| Issue templates | 14.8 | GitHub issue/bug/feature request templates |
| Code of conduct | 14.9 | Community standards |
| Architecture docs | 14.10 | Visual diagrams for contributors |
| Example configs (multiple) | 14.11 | Pre-built configs for common setups (minimal, full MCP, local-only) |

### Setup & Onboarding
| Item | Origin | Description |
|------|--------|-------------|
| Setup wizard (full) | 15.1 | `operator setup` — full guided experience: API key entry, voice selection, MCP server auth, model selection UI, rerun-safe state. Minimal version shipped in Phase 15.5.2 (agent-picker + keys + config write); this is the expanded version with voice, MCP OAuth, and polished UX. |
| Agents gallery expansion | 15.5 | Grow `agents/` beyond the 2 launch agents. Curate community contributions, add more job-shaped examples (release-manager, customer-success, RFC-reviewer, etc.), maintain an `awesome-operator` list for agents hosted in external repos. Ongoing post-launch. |
| MCP OAuth setup step | 15.2 | Authenticate each MCP server during onboarding so tokens are cached |
| Auto-populate per-MCP hints | 15.3 | Resolve identity (GitHub `get_me`, etc.) during onboarding |
| First-run smoke test | 15.4 | Automated health check after setup |

### Platform Expansion
| Item | Origin | Description |
|------|--------|-------------|
| `ChatConnector` interface | 17.1 | Abstract chat read/write for multi-platform support |
| Zoom connector | 17.2 | Chat DOM selectors, join flow, auth for Zoom |
| Microsoft Teams connector | 17.3 | Chat DOM selectors, join flow, auth for Teams |

### Hosted Operator — Inviteable Bot
*The north-star product vision: Operator is a service users invite to their meeting by calendar invite or email (e.g. `operator@yourdomain.com`), no local install required. Shopify-style: "invite the bot, it joins and participates as an expert." Recall.ai-shaped architecturally (one ephemeral container per meeting), not daemon/API-shaped. Explicit scope note: this phase does NOT require the Layer 7 "headless core + chat API + reference client" refactor — the current monolithic Python process IS the container. Path B from the session 92 discussion.*

| Item | Description | Est. |
|------|-------------|------|
| Containerize the monolith | Package the existing chat-mode `__main__.py` flow into a Docker image. Strip macOS-only paths at build time; target Linux-in-container as the canonical runtime. Build on top of existing Phase 3/4 Linux adapter work. One container = one meeting, stateless, dies on meeting end. | ~4h |
| Meeting-invite receiver | A service endpoint that detects when the hosted bot is invited to a meeting. Options: (a) email listener that parses calendar invites sent to `operator@domain`, (b) Google Calendar webhook on a service account. Extract the meeting URL; hand off to the orchestrator. | ~6h |
| Orchestrator | On each new invite: spawn a fresh container pinned to that meeting URL, with per-user config/skills/MCP credentials mounted in. Tear it down when the meeting ends or times out. Target platform: Kubernetes, Fly.io Machines, or similar lightweight container-per-request runtime. | ~8h |
| Hosted Google auth | The bot needs to log into Google Meet as a real account to join. Single service account + domain-wide delegation, OR per-user OAuth where each user grants the hosted bot permission to join on their behalf. Second option is the OSS-friendly path. Non-trivial auth work; tokens stored encrypted per-user. | ~6h |
| Multi-tenant config isolation | Each user's skills, MCP credentials, and system prompt injected into *their* container only. Process-boundary isolation (one container per user-meeting) means no in-core multi-tenancy logic needed, but the config-mounting path has to be bulletproof. | ~3h |
| Observability + kill switch | Per-container logs aggregated somewhere queryable. Global kill switch for abuse. Rate limiting per user. Cost tracking (each meeting burns LLM tokens + container minutes). | ~4h |
| Billing / limits (if commercial) | Only if this goes beyond "free service for friends." Out of scope until product direction settles. | — |

**Phase total: ~30-35h** (excluding billing). Large phase; not a single-session item. Real prerequisite: the `ChatSource` seam lands first so the container doesn't carry Meet-specific quirks into its generic chat loop.

---

### OSS Ethos — Audit Fixes
*Findings from the session 91 read-only audit (`docs/oss-audit-report.md`). **All 10 layers parsed in session 92.** Two items promoted to Phase 11 pre-MVP (11.6 persistence, 11.7 optional provider keys). Dropped: L6-b, L7, L8-a. Absorbed: L8-b into L1-c; L9-a into L3-b + L3-d; L9-b + L10-b into L1-b. L10-a is a compliance affirmation (zero-telemetry verified intact). The remaining items below are split into two buckets: **fast-follow candidates** (small enough to squeeze in pre-MVP if time permits) and **post-MVP** (must wait — mature-product shape or too large).*

#### Fast-follow candidates *(pre-MVP if time permits, else immediately after launch)*
*Total: ~10h 45m across all five — none individually gates the demo, but bundled they meaningfully sharpen the OSS shape.*

| Item | Origin | Description | Est. |
|------|--------|-------------|------|
| Configurable log destination + level | L1-b + L9-b + L10-b | Hardcoded `/tmp/operator.log` in `__main__.py:214` (referenced in user-facing error strings in `llm.py` and `chat_runner.py`). `/tmp` is world-readable on multi-user systems and cleared on reboot. Log level is also hardcoded to DEBUG — at that level, logs contain tool call arguments, first 500 chars of tool results, and user utterances (local-only, but users may not realize). Add `logging.destination` AND `logging.level` to `config.yaml` with platform-appropriate defaults (e.g. `~/.operator/operator.log`, level `INFO`); resolve path once and interpolate into user-facing strings instead of hardcoding; at `INFO` level, skip content-dump debug lines; sanity-pass for token/secret redaction. | ~1.5h |
| `ChatSource` seam + stdin source | L1-a + L2 + L3-c | Today `ChatRunner` can only be driven by the Meet connector — every end-to-end test spins up Playwright + Chromium, and Meet-specific concerns (participant count, DOM echo dedup via duplicate-ID quirk, `ONE_ON_ONE_THRESHOLD`, `PARTICIPANT_CHECK_INTERVAL`, alone-exit grace) live inside the generic chat loop. Extract a `ChatSource` protocol (`read_chat`, `send_chat`, `is_connected`); Meet connector becomes one implementation; push Meet-specific concerns down into `MeetAdapter`; add a `StdinChatSource` for fast local dev and isolated integration tests. While refactoring, promote the "leave when alone" behavior to a proper config toggle (`leave_when_alone: true\|false` + existing `alone_exit_grace_seconds`) and drop `ONE_ON_ONE_THRESHOLD` as a magic constant in favor of a simple "≤1 human" check. Standard ports-and-adapters pattern. Not user-facing — internal tool. Also a prerequisite for the future Hosted Operator phase. | ~6h |
| Per-server quirk config | L3-b + L9-a (partial) | Today Linear's `limit`-arg stripping and GitHub's `get_me` identity injection are hardcoded as `if server == "linear"` / `if tool == "github__get_me"` branches across `chat_runner.py` and `mcp_client.py`. Adding a Notion/Slack quirk today means editing three files. Add declarative fields to each MCP server block: `strip_args: [name, ...]` (framework strips these from any tool call on that server) and `identity_tool: <tool_name>` (framework calls it at startup, resolves the identity value, and injects guidance via an overridable `prompts.identity_guidance` template). Removes all per-server branches. Absorbs `inject_github_user` from L9-a. | ~2h |
| Open provider registry | L4-b | `pipeline/providers/__init__.py:11-29` is a closed two-way `if name == "openai" / elif "anthropic" / else raise`. Swap for a module-level `PROVIDERS = {"openai": _build_openai, "anthropic": _build_anthropic}` dict plus a public `register_provider(name, factory)` so users can plug in Ollama/Gemini/local proxies without forking. | ~30m |
| Framework-owned strings as config templates | L3-d + L9-a (partial) | Strings like *"Still working on that..."*, *"That took too long — no response after Ns. Try again."*, *"Sorry, that tool call failed. Check the logs for details."*, the confirmation-request prompt, and the history-truncation notice are hardcoded in `chat_runner.py:263-414`. Also absorbs `inject_mcp_status`: add a global `prompts.mcp_server_unavailable` template with `{server_name}` and `{log_path}` interpolation. Move each string to a named template in `config.yaml` under a `prompts.*` block. | ~45m |

#### Post-MVP *(must wait — size or shape)*

| Item | Origin | Description | Est. |
|------|--------|-------------|------|
| Skill manifest + neutral `ToolSchema` + install consent | L3-a + L4-a + L5-a + L5-b | The largest item in the OSS fixes list; bundles four audit findings because they describe the same missing thing from four angles. Define a skill manifest (YAML alongside or replacing the current `mcp_servers` block) declaring each tool's name, description, input schema, and sensitivity tier (`read` \| `write` \| `sensitive`). Introduce a neutral `ToolSchema` dataclass in `pipeline/providers/base.py`; `MCPClient` discovers MCP tools and converts them to `ToolSchema` at ingestion, merging declared manifest metadata (especially sensitivity). Each provider translates `ToolSchema` at its own boundary; rename `get_openai_tools` → `get_tools`. `ChatRunner` derives confirm-vs-auto from `ToolSchema.sensitivity` (unknown = confirm). Install-time consent: on first-enable of a skill, print the manifest (tools + sensitivities) and require a yes/no to proceed; cache consent in user state. Sensitive-tier tools always prompt mid-conversation regardless of install consent. Replaces the hardcoded `READ_TOOLS` set (L3-a), the OpenAI-shaped tools surface (L4-a), and the MCP-SDK-class-as-neutral-type (L5-b) all at once; delivers the named ethos shape commitment (L5-a). Ship default manifests for Linear/GitHub in the repo; document the format so users/authors can write their own. Too large for MVP: ~10-14h (manifest format + loader, `ToolSchema` + MCPClient conversion, provider translation updates, sensitivity-driven confirmation + READ_TOOLS removal, install consent flow, docs + example manifests, integration buffer). | ~10-14h |

---

## Key Decisions

- **Architecture:** Three-layer separation (pipeline / connector / platform shell)
- **Primary platform:** Local machine (macOS + Linux). Cloud is upgrade path.
- **Input (macOS Meet):** DOM caption scraping. Audio pipeline preserved behind `connector.type: audio`.
- **STT (audio fallback):** mlx-whisper base on macOS; faster-whisper base on Linux.
- **LLM:** GPT-4.1-mini (default), Claude (alternative). User picks provider in config.
- **TTS:** Three-tier — local (Kokoro) / openai / elevenlabs. Default: Kokoro af_heart.
- **Meeting detection:** Browser-based Google Calendar scraping (30s interval).
- **Licensing:** MIT
- **Python target:** 3.11
- **Pivot (April 2026):** Chat-first v1, voice layered on later. Motivated by real user demand for task delegation via meeting chat.
- **MVP scope (April 2026):** Google Meet only, Mac + Linux. 7-day ship window (April 12–19). See `docs/mvp-bar.md` for the full MVP bar definition.
- **MVP model support (April 2026):** OpenAI + Anthropic. No local models — too weak at agentic tool use, would undermine the demo.
- **V1 positioning (April 2026):** "Meetings that produce artifacts, not just words." Tool use during meetings is the moat — no competitor does this. Pika wins on presentation (avatar/voice), Recall wins on infrastructure (multi-platform), Operator wins on capability (MCP tool use, live context, extensibility).

### Open Questions

- **Recall.ai as optional connector?** Recall offers managed meeting bot infrastructure ($0.50/hr) covering Zoom, Meet, Teams, Webex via a single API. Could add `connector: recall` in config.yaml as an alternative to self-hosted connectors — eliminates browser automation, audio routing, and platform maintenance. Tradeoff: proprietary dependency vs. drastically reduced plumbing burden. Hybrid model (self-hosted default, Recall optional) preserves open source spirit. Could skip building Zoom/Teams connectors entirely.

---

## Not On This Plan

- DigitalOcean droplet deployment (preserved in `cloud/`)
- Loadout sharing / registry
- Windows support
- Multi-agent concurrency
