# Operator — Roadmap

*Last updated: April 9, 2026 (session 69)*

> **Current status: Phase 9 complete (session 69).** All active steps done: 9.1 (selector hardening), 9.4 (race condition audit), 9.5 (security audit), 9.6 (simultaneous meeting handling), 9.7 (calendar polling startup latency), 9.11 (chat message size management). Steps 9.2/9.3/9.8/9.9/9.10 deferred to Phase 12. Next: Phase 10 (setup & onboarding).

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
MCP client connects to configured servers at startup, discovers tools. Tool-call loop in LLMClient handles tool_call → execute → result → re-prompt. Chat-specific LLM settings. Config schema for `mcp_servers`. Validated with Linear and GitHub MCP servers end-to-end in live Meet.

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
| 8.2.2 | Meeting lifecycle — pre-join user gate, end-time auto-leave, stale meeting skip, Ctrl+C clean shutdown | ✅ |
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
| 9.12 | Tool call timeout + heartbeat — visible "still working..." in chat for long-running calls, hard timeout with graceful failure | ⬜ | ~2h |
| 9.13 | Context window management — strategy for summarizing/truncating older chat history as conversation grows, prevent silent context overflow | ⬜ | ~3h |
| 9.14 | Idempotency guards — prevent duplicate tool actions from repeated requests ("create a ticket" said twice), confirmation before write operations | ⬜ Post-v1 | ~3h |
| 9.15 | Offline/reconnection behavior — handle internet drops, Playwright page loss, browser crashes; decide: crash, wait, or rejoin | ⬜ | ~4h |
| 9.16 | Edge case pass — systematic audit of boundary conditions: empty meetings, rapid join/leave, malformed chat input, Unicode/emoji in messages, MCP server returning unexpected data, concurrent tool calls, browser memory leaks in long meetings | ⬜ | ~4h |

---

## Phase 10: Open-Source Packaging ← V1 RELEASE GATE

*Package the project so a stranger can clone, install, and run it in under 15 minutes.*

| Step | Description | Status | Est. |
|------|-------------|--------|------|
| 10.1 | Config audit & cleanup — remove dead keys, ensure every key is necessary, document what each controls; balance simplicity with tool-oriented customizability | ⬜ | ~2h |
| 10.2 | Add `pyproject.toml` — package name, Python version, entry points | ⬜ | ~1h |
| 10.3 | Add `LICENSE` (MIT) | ⬜ | ~5m |
| 10.4 | Rewrite `README.md` — what it is, quick start, architecture, "meetings that produce artifacts" positioning | ⬜ | ~3h |
| 10.5 | Demo video/GIF — 30s screen recording of chat-based tool use in a live meeting, embedded at top of README | ⬜ | ~2h |
| 10.6 | Setup wizard (`operator setup`) — delightful, guided, breezy; auto-detect OS, walk through API keys, voice selection, MCP server auth | ⬜ | ~6h |
| 10.7 | MCP OAuth setup step in wizard — authenticate each configured MCP server (Linear, GitHub, etc.) so tokens are cached before first meeting | ⬜ | ~4h |
| 10.8 | First-run smoke test — automated health check after setup: LLM reachable? MCP servers connect? Browser profile valid? Surface issues before first meeting | ⬜ | ~3h |
| 10.9 | Upgrade GitHub MCP server — deprecated npm package → official Go binary from `github/github-mcp-server` | ✅ | — |
| 10.10 | Example configs / quickstart templates — pre-built config.yaml examples for common setups (minimal, full MCP, local-only) | ⬜ | ~1h |
| 10.11 | Dependency pinning + reproducible installs — lockfile, pinned versions, tested Python version matrix (3.11, 3.12) | ⬜ | ~2h |
| 10.12 | CI/CD pipeline — automated tests on PR, release tagging, PyPI publish workflow | ⬜ | ~4h |
| 10.13 | Contributing guide — how to contribute, code standards, PR process, how to add MCP servers | ⬜ | ~1h |
| 10.14 | MCP server compatibility matrix — documented list of tested servers, known quirks, model-specific behavior notes | ⬜ | ~2h |
| 10.15 | Changelog / release notes — CHANGELOG.md, semver tagging, clear upgrade path between versions | ⬜ | ~1h |
| 10.16 | Issue templates — GitHub issue/bug/feature request templates for consistent community reporting | ⬜ | ~30m |
| 10.17 | Code of conduct | ⬜ | ~15m |
| 10.18 | Architecture docs — visual diagrams (data flow, layer separation), aimed at contributors not just users | ⬜ | ~3h |

---

## Phase 11: Multi-Model & Provider Support

*Break the OpenAI lock-in. Enable local-only mode as a differentiator.*

| Step | Description | Status | Est. |
|------|-------------|--------|------|
| 11.1 | Abstract LLM provider interface — swap between OpenAI, Anthropic, local without code changes | ⬜ | ~3h |
| 11.2 | Anthropic API backend — Claude as alternative LLM provider | ⬜ | ~3h |
| 11.3 | OpenAI model matrix testing — validate behavior across GPT-4.1-mini, GPT-4.1, GPT-4o, o3-mini | ⬜ | ~3h |
| 11.4 | MCP tool pressure testing — every tool × every supported model, explicit + implicit + indirect requests | ⬜ | ~4h |
| 11.5 | Meeting transcript as context — feed full meeting chat history (not just current message) to LLM during tool calls | ⬜ | ~2h |
| 11.6 | Local LLM support — Ollama/llama.cpp for fully zero-API-key deployment (with local Whisper + Kokoro TTS) | ⬜ | ~4h |
| 11.7 | Telemetry / diagnostics (opt-in) — anonymous usage stats to understand what's breaking in the wild, with clear opt-out | ⬜ | ~4h |

---

## Phase 12: MCP Hardening & Extensibility

*Make MCP integration robust, configurable, and open to user-defined servers.*

| Step | Description | Status | Est. |
|------|-------------|--------|------|
| 12.1 | Per-MCP `hints` field in config — server-specific LLM guidance injected into system prompt | ⬜ | ~1h |
| 12.2 | Setup wizard auto-populates hints — resolve identity (GitHub `get_me`, etc.) during onboarding, store in config | ⬜ | ~2h |
| 12.3 | Configurable tool confirmation modes — `auto-all`, `read-auto`, `confirm-all`, `session-trust` + `batch_preview` toggle | ⬜ | ~4h |
| 12.4 | Read-only tool classification — tag tools at discovery time from descriptions, auto-approve reads in `read-auto` mode | ⬜ | ~2h |
| 12.5 | User-defined MCP servers — users add custom servers in config with command, args, env, hints | ⬜ | ~1h |
| 12.6 | User-defined MCP guard rails — validation at setup, execution timeouts, result size caps, `confirm-all` default for untrusted servers | ⬜ | ~3h |
| 12.7 | Optional managed MCP client layer — allow users to point at an MCP proxy/gateway instead of local stdio servers (Cloudflare, etc.) | ⬜ Post-v1 | ~4h |
| 12.8 | DOM regression test suite — automated tests against a live Meet session on a schedule, catch selector breakage early (moved from 9.2) | ⬜ | ~4h |
| 12.9 | Self-healing selectors — fallback strategies when primary selectors fail (multiple selector candidates, semantic search, graceful degradation) (moved from 9.3) | ⬜ | ~6h |
| 12.10 | Pin MCP server versions — lock `mcp-remote` to a specific version in config, pin GitHub binary version; prevent surprise breakage from upstream changes | ⬜ | ~1h |
| 12.11 | Dependabot + pip-audit — add `.github/dependabot.yml` for automated dependency PRs; add `pip-audit` step to CI smoke test for CVE detection | ⬜ | ~1h |
| 12.12 | MCP server health check — weekly CI job that starts each configured MCP server and calls `list_tools()`; alert on failure. Extend step 10.8 smoke test with a dry-run tool call per server | ⬜ | ~2h |
| 12.13 | MCP tool call failure monitoring — track per-server `MCPToolError` rate at runtime; log warning when failure rate spikes above threshold; surface in diagnostics | ⬜ | ~2h |
| 12.14 | Log cleanup — structured, consistent log levels; clean stdout for normal operation, verbose for debug (moved from 9.8) | ⬜ | ~2h |
| 12.15 | Latency audit — profile end-to-end chat path, identify and shave unnecessary delays (moved from 9.9) | ⬜ | ~2h |
| 12.16 | Comprehensive error handling pass — graceful MCP server failure, tool call rate limiting, runaway loop prevention, user-friendly error messages in chat (no stack traces) (moved from 9.10) | ⬜ | ~4h |
| 12.17 | MCP-specific format and context hints — after finalizing supported MCP servers, add per-server hints covering response format guidance and context window hygiene (e.g. prefer targeted calls over whole-file retrieval). Depends on 12.1 hints infrastructure. Informed by patterns established in step 9.11. | ⬜ | ~2h |

---

## Phase 13: Voice Interaction

*Layer voice as a second interaction modality on top of the proven chat capability layer.*

| Step | Description | Status | Est. |
|------|-------------|--------|------|
| 13.1 | Resolve premature finalization (0.7s silence threshold) | ⬜ | ~3h |
| 13.2 | TTS reliability — error handling and retry logic | ⬜ | ~2h |
| 13.3 | Validate partial-wake idea (#6 from latency.md) | ⬜ | ~2h |
| 13.4 | Add `MODE` key to config.yaml: `voice` \| `chat` \| `both` | ⬜ | ~2h |

---

## Phase 14: Meeting Platform Expansion (demand-driven)

*Add support for Zoom and/or Microsoft Teams. Only pursue when a real user needs it.*

Each platform requires: DOM chat selectors, join flow, auth handling, and ongoing selector maintenance as UIs change. Architect Phase 8 with a thin chat read/write abstraction so new platforms are additive (new implementation, not a rewrite).

**Alternative path:** If Recall.ai is adopted as optional infrastructure (see Open Questions), this phase reduces to building a single `RecallConnector` that wraps their API — instant multi-platform support without per-platform DOM work.

| Step | Description | Status | Est. |
|------|-------------|--------|------|
| 14.1 | Define `ChatConnector` interface (read messages, send messages, platform identity) | ⬜ | ~2h |
| 14.2 | Zoom — spike on chat DOM, implement connector | ⬜ | ~8h |
| 14.3 | Microsoft Teams — spike on chat DOM, implement connector | ⬜ | ~8h |

---

## Key Decisions

- **Architecture:** Three-layer separation (pipeline / connector / platform shell)
- **Primary platform:** Local machine (macOS + Linux). Cloud is upgrade path.
- **Input (macOS Meet):** DOM caption scraping. Audio pipeline preserved behind `connector.type: audio`.
- **STT (audio fallback):** mlx-whisper base on macOS; faster-whisper base on Linux.
- **LLM:** GPT-4.1-mini
- **TTS:** Three-tier — local (Kokoro) / openai / elevenlabs. Default: Kokoro af_heart.
- **Meeting detection:** Browser-based Google Calendar scraping (30s interval).
- **Licensing:** MIT
- **Python target:** 3.11
- **Pivot (April 2026):** Chat-first v1, voice layered on later. Motivated by real user demand for task delegation via meeting chat.
- **MVP scope (April 2026):** Google Meet only, Mac + Linux. Platform cost is in meeting service (DOM selectors, auth), not OS — Playwright is cross-platform. Zoom/Teams deferred to Phase 14, demand-driven.
- **V1 positioning (April 2026):** "Meetings that produce artifacts, not just words." Tool use during meetings is the moat — no competitor does this. Pika wins on presentation (avatar/voice), Recall wins on infrastructure (multi-platform), Operator wins on capability (MCP tool use, live context, extensibility).

### Open Questions

- **Recall.ai as optional connector?** Recall offers managed meeting bot infrastructure ($0.50/hr) covering Zoom, Meet, Teams, Webex via a single API. Could add `connector: recall` in config.yaml as an alternative to self-hosted connectors — eliminates browser automation, audio routing, and platform maintenance. Tradeoff: proprietary dependency vs. drastically reduced plumbing burden. Hybrid model (self-hosted default, Recall optional) preserves open source spirit. Relevant to Phase 14 — could skip building Zoom/Teams connectors entirely.
- **Local LLM support?** Swap GPT-4.1-mini for Ollama/llama.cpp to enable a fully zero-API-key deployment. Combined with existing local Whisper + Kokoro TTS, this would make Operator runnable with no paid services at all — a genuine differentiator. Tradeoff: local models are weaker at agentic tool use (MCP) and response quality. Could offer as a config tier: `llm: local | openai`.

---

## Not On This Plan

- DigitalOcean droplet deployment (preserved in `cloud/`)
- Loadout sharing / registry
- Windows support
- Multi-agent concurrency
