# Operator — Roadmap

*Last updated: April 6, 2026 (session 50)*

> **Current status: Chat hardening complete.** History cap, wake phrase gating (`/operator`), and sender extraction all verified in live Google Meet with multiple participants. Next: ship to friend (step 8.3). MCP/tool-use integration queued as Phase 11.

---

## Completed Phases

<details>
<summary>Phases 1–6 + Caption Refactor (all ✅)</summary>

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

</details>

---

## Phase 7: Performance Iteration (partial)

*Goal: Solid audio quality and reliable pipeline behavior.*

| Step | Description | Status |
|------|-------------|--------|
| 7.1 | Audio quality — rule out QEMU, fix sample rate mismatch | ✅ |
| 7.2 | TTS provider benchmark — evaluate providers, build 3-tier architecture | ✅ |
| 7.3 | Latency masking — speculative processing + filler clips | ✅ |
| 7.4 | STT accuracy — benchmark alternatives, switch to mlx-whisper | ✅ |
| 7.5 | TTS reliability — error handling and retry logic | ⬜ Deferred |
| 7.6 | Streaming first-token classification + conversation mode | ✅ |
| 7.7 | Playback interrupt classification | ✅ |
| 7.8 | Latency documentation + measurement | ✅ See `docs/latency.md` |

**Top open issue:** Premature finalization at 0.7s silence threshold cuts off mid-sentence prompts. See `docs/latency.md` for pipeline measurements and six reduction ideas.

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
| 8.3 | Ship to friend — minimal setup, clear instructions, get it in his hands | ⬜ |

---

## Phase 9: Voice Interaction (after chat MVP ships)

*Layer voice as a second interaction modality on top of the proven chat capability layer.*

| Step | Description | Status |
|------|-------------|--------|
| 9.1 | Resolve premature finalization (0.7s silence threshold) | ⬜ |
| 9.2 | TTS reliability — error handling and retry logic | ⬜ |
| 9.3 | Validate partial-wake idea (#6 from latency.md) | ⬜ |
| 9.4 | Add `MODE` key to config.yaml: `voice` \| `chat` \| `both` | ⬜ |

---

## Phase 10: Open-Source Packaging

*Package the project so a stranger can clone and install it.*

| Step | Description | Status |
|------|-------------|--------|
| 10.1 | Add `pyproject.toml` — package name, Python version, entry points | ⬜ |
| 10.2 | Add `LICENSE` (MIT) | ⬜ |
| 10.3 | Rewrite `README.md` — what it is, quick start, architecture | ⬜ |
| 10.4 | Setup wizard (`operator setup`) with re-runnable subcommands | ⬜ |

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
- **MVP scope (April 2026):** Google Meet only, Mac + Linux. Platform cost is in meeting service (DOM selectors, auth), not OS — Playwright is cross-platform. Zoom/Teams deferred to Phase 11, demand-driven.

---

## Phase 11: Tool Use / MCP Integration

*Give Operator the ability to take actions — not just chat — via MCP-based tool plugins.*

**Why:** A chat bot that can only talk is limited. Real task delegation ("create a Linear ticket for the login crash") requires tool execution. MCP is the standard protocol — existing servers for Linear, GitHub, Slack, Notion, Jira, Google Calendar, etc. Users add integrations by config, not code.

**Architecture:** Operator becomes an MCP client. At startup it connects to configured MCP servers, discovers their tools, and passes tool definitions to the LLM. When the LLM returns a `tool_call`, Operator executes it via the MCP server and feeds the result back to the LLM for summarization.

| Step | Description | Status |
|------|-------------|--------|
| 11.1 | MCP client — connect to configured servers at startup, discover tools | ⬜ |
| 11.2 | Tool-call loop in LLMClient — handle tool_call → execute → result → re-prompt cycle | ⬜ |
| 11.3 | Chat-specific LLM settings — separate `max_tokens`, system prompt for chat vs. voice | ⬜ |
| 11.4 | Config schema for `mcp_servers` in config.yaml (command, args, env) | ⬜ |
| 11.5 | Validate with Linear MCP server end-to-end in live Meet | ⬜ |

---

## Phase 12: Meeting Platform Expansion (demand-driven)

*Add support for Zoom and/or Microsoft Teams. Only pursue when a real user needs it.*

Each platform requires: DOM chat selectors, join flow, auth handling, and ongoing selector maintenance as UIs change. Architect Phase 8 with a thin chat read/write abstraction so new platforms are additive (new implementation, not a rewrite).

| Step | Description | Status |
|------|-------------|--------|
| 12.1 | Define `ChatConnector` interface (read messages, send messages, platform identity) | ⬜ |
| 12.2 | Zoom — spike on chat DOM, implement connector | ⬜ |
| 12.3 | Microsoft Teams — spike on chat DOM, implement connector | ⬜ |

---

## Not On This Plan

- DigitalOcean droplet deployment (preserved in `cloud/`)
- Loadout sharing / registry
- Windows support
- Multi-agent concurrency
