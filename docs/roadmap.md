# Operator — Roadmap

*Last updated: April 12, 2026 (session 89)*

> **Current status: Roadmap trimmed for 7-day MVP (ship by April 19, 2026).** Phases 10–16 restructured against `docs/mvp-bar.md`. ~37h of work against ~50–55h available. All deferred items preserved in Post-MVP section. **Phase 10 complete; Phase 11 in progress.** Session 89 shipped step 11.1 (abstract LLM provider interface) — pure refactor extracting OpenAI transport into `pipeline/providers/`, LLMClient now takes a provider; live-validated in chat-mode Meet with a Linear tool call. Session 88 shipped 10.6 (MCP runtime failure backoff). Session 87 shipped 10.3 + MCP startup banner. Session 86 alone-exit auto-leave now live-validated on both chat and voice paths. Notion/Slack/Brave pressure testing dropped from MVP scope (only Linear + GitHub ship tested).

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
| 11.2 | Anthropic API backend — Claude as alternative LLM provider | ⬜ | ~3h |
| 11.3 | Meeting transcript as context — feed full meeting chat history (not just current message) to LLM during tool calls, so requests like "create a ticket for the auth bug Alice just described" actually work | ⬜ | ~2h |
| 11.4 | Skill file loading — users drop markdown files in a `skills/` directory (path configurable); contents appended to the system prompt at runtime so the bot reflects user identity, team conventions, ticket formats, etc. Delivers the "your AI, not Gemini" customization layer promised in `docs/mvp-bar.md` | ⬜ | ~2h |

**Phase total: ~10h**

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

## Phase 16: README & Launch ← MVP GATE

*Ship it.*

| Step | Description | Status | Est. |
|------|-------------|--------|------|
| 16.1 | Rewrite `README.md` — what it is, quick start, architecture, "meetings that produce artifacts" positioning. Includes: MCP compatibility notes (tested servers + known quirks), BYOMCP failure patterns and mitigation guidance, one annotated example config | ⬜ | ~4h |
| 16.2 | Demo video/GIF — 30s screen recording of chat-based tool use in a live meeting, embedded at top of README | ⬜ | ~2h |

**Phase total: ~6h**

---

**MVP total: ~40h across Phases 10–16 (against ~50–55h available through April 19, 2026)**

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
| Setup wizard | 15.1 | `operator setup` — guided API key entry, voice selection, MCP server auth |
| MCP OAuth setup step | 15.2 | Authenticate each MCP server during onboarding so tokens are cached |
| Auto-populate per-MCP hints | 15.3 | Resolve identity (GitHub `get_me`, etc.) during onboarding |
| First-run smoke test | 15.4 | Automated health check after setup |

### Platform Expansion
| Item | Origin | Description |
|------|--------|-------------|
| `ChatConnector` interface | 17.1 | Abstract chat read/write for multi-platform support |
| Zoom connector | 17.2 | Chat DOM selectors, join flow, auth for Zoom |
| Microsoft Teams connector | 17.3 | Chat DOM selectors, join flow, auth for Teams |

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
