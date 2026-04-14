# Open-Source Audit Report — Session 91

Read-only audit of the chat-mode codebase against the OSS ethos (`project_oss_ethos.md`). Findings are observations, not prescriptions. Audio/voice layer, multi-tenant, and non-English concerns were excluded per the audit plan.

> **Layer 1 parsed session 92** — three actions landed in `roadmap.md` → Post-MVP → "OSS Ethos — Audit Fixes". See `agent-context.md` for the discussion summary (incl. reframing of the "requires a Meet URL" finding). Layer 1 findings removed from this report.

---

> **Layer 2 parsed session 92** — single finding folded into the existing `ChatSource` seam action in `roadmap.md` (same refactor from the interface side as L1-a's from the usage side). Layer 2 removed from this report.

---

> **Layer 3 parsed session 92** — four findings actioned. L3-a (READ_TOOLS allowlist, critical) deferred to the future L5 skill-manifest item. L3-b (per-server quirks) is a new ~2h roadmap item. L3-c (Meet concerns in runner) folded into the `ChatSource` seam item (bumped to ~6h; includes promoting `leave_when_alone` to a config toggle). L3-d (hardcoded user-facing strings) is a new ~30m polish item. Layer 3 removed from this report.

---

> **Layer 4 parsed session 92** — both findings actioned. L4-a (OpenAI-shaped tools surface) is a new ~3h roadmap item introducing a neutral `ToolSchema`; will be bundled with L5's `MCPTool`-neutrality finding. L4-b (closed provider registry) is a new ~30m item adding `PROVIDERS` dict + `register_provider()`. Layer 4 removed from this report.

---

> **Layer 5 parsed session 92** — both findings bundled with L3-a and L4-a into a single ~10-14h post-MVP item: "Skill manifest + neutral `ToolSchema` + install consent". Scope discussion concluded these are mature-product concerns, not April 19 ship blockers; current READ_TOOLS + `confirm_tools` are acceptable for MVP demo to a known audience with known MCP servers. Layer 5 removed from this report.

---

> **Layer 6 parsed session 92** — L6-a (in-process-only history) landed in Phase 11 as step 11.6 pre-MVP (~1.5h); tightly scoped to durable persistence of current shape, not the full `ConversationStore` protocol. L6-b dropped — `CHAT_HISTORY_TURNS` dial + auto-abstracted collapse logic already matches the intended design; premature abstraction to add more retention strategies. Layer 6 removed from this report.

---

> **Layer 7 parsed session 92** — dropped. Reviewed against product vision (hosted inviteable bot, Recall.ai-shaped architecturally). The daemon/API/client split this layer proposed is orthogonal to that vision — containerizing the monolith (one container per meeting) is the right path, and the current monolithic Python process maps cleanly to a container. The `ChatSource` seam already queued handles swappable surfaces; daemonization would be speculative architecture (YAGNI) until a second long-lived client genuinely appears. New "Hosted Operator" phase added to `roadmap.md` capturing the containerization path. Layer 7 removed from this report.

---

> **Layer 8 parsed session 92** — L8-a (CLI override layer) dropped; the field norm for config-heavy long-lived tools (kubectl, terraform, docker-compose, nginx, litellm) is YAML-first with flags reserved for CLI tools you invoke repeatedly. The ethos doc's "layered YAML → env → CLI → per-skill" was one-size-fits-all; for a meeting bot it overweights CLI flags. L8-b is a duplicate of L1-c — already captured in the roadmap. Layer 8 removed from this report.

---

> **Layer 9 parsed session 92** — both findings absorbed by existing roadmap items without new work. L9-a split three ways: `inject_mcp_hints` stays as-is (already user-owned via per-server `hints`); `inject_github_user` folds into L3-b's `identity_tool` + overridable `prompts.identity_guidance` template; `inject_mcp_status` folds into L3-d as a global `prompts.mcp_server_unavailable` template with `{server_name}` / `{log_path}` interpolation (L3-d estimate bumped ~30m → ~45m). L9-b folds into L1-b (log path already centralized there). Clean modular shape: `hints` for durable author intent, template fragments for framework-owned strings, `identity_tool` for dynamic identity resolution. Layer 9 removed from this report.

---

> **Layer 10 parsed session 92.** L10-a is a compliance affirmation — repo-wide grep confirmed zero outbound network activity beyond configured providers + configured TTS + opted-in MCP servers. **Zero-telemetry commitment verified intact.** No action needed. L10-b (DEBUG logs dump user data to hardcoded `/tmp` path) absorbed into L1-b, which now covers log destination + level + content redaction (estimate bumped ~1h → ~1.5h). Layer 10 removed from this report.

---

## Prioritized summary

### Critical (blocks core ethos commitments)
- ~~**[L1] Chat mode requires a Meet URL**~~ — *parsed session 92; reframed as "`ChatRunner` welded to Meet connector", action in roadmap.*
- ~~**[L1 / L8] `OPENAI_API_KEY` required even for Anthropic runs**~~ — *parsed session 92; action in roadmap.*
- **[L3-a] Hardcoded Linear+GitHub `READ_TOOLS` allowlist** — *parsed session 92; deferred to the L5 skill-manifest item (same refactor).*
- ~~**[L5-a] No skill manifest / install consent / sensitivity tiers**~~ — *parsed session 92; bundled into the skill-manifest post-MVP item with L3-a + L4-a + L5-b.*
- ~~**[L7] No headless core + chat API + reference client separation**~~ — *parsed session 92; dropped. Reviewed against hosted-bot vision — containerized monolith (per-meeting) is the right path, not daemon/API split. New "Hosted Operator" phase added to roadmap.*

### Major (awkward but workable)
- ~~**[L2] `MeetingConnector` conflates meeting + chat**~~ — *parsed session 92; folded into the `ChatSource` seam action.*
- ~~**[L3-b] Linear/GitHub quirks leak into runner and MCP client**~~ — *parsed session 92; new roadmap item.*
- ~~**[L3-c] Meet-specific loop concerns baked into `ChatRunner`**~~ — *parsed session 92; folded into the `ChatSource` seam item.*
- ~~**[L4-a] Tools surface at provider boundary is OpenAI-shaped**~~ — *parsed session 92; new roadmap item; will bundle with L5 `MCPTool` refactor.*
- ~~**[L5-b] `MCPTool` is the in-code neutral tool type**~~ — *parsed session 92; bundled into the skill-manifest post-MVP item.*
- ~~**[L6-a] No `ConversationStore` seam; history in-process only**~~ — *parsed session 92; landed as Phase 11.6 pre-MVP (tight scope — durable persistence only).*
- ~~**[L8-b] Import-time required secrets**~~ — *parsed session 92; duplicate of L1-c already on roadmap.*
- ~~**[L9-a] Framework-injected prompt fragments bake wording and behavior**~~ — *parsed session 92; absorbed: `inject_github_user` → L3-b identity_tool; `inject_mcp_status` → L3-d global template; `inject_mcp_hints` already user-owned.*
- ~~**[L1] Log path hardcoded `/tmp/operator.log`**~~ — *parsed session 92; action in roadmap.*

### Minor (polish)
- ~~**[L3-d] Hardcoded user-facing strings in `ChatRunner`**~~ — *parsed session 92; new roadmap item.*
- ~~**[L4-b] `build_provider()` is a closed two-way `if/elif`**~~ — *parsed session 92; new roadmap item.*
- ~~**[L6-b] Retention policy baked into `LLMClient._trim_history` / `_collapse_tool_exchange`**~~ — *parsed session 92; dropped — current dial already matches intended design.*
- ~~**[L8-a] No CLI override layer**~~ — *parsed session 92; dropped. Field norm for config-heavy tools is YAML-first.*
- ~~**[L9-b] Error strings reference `/tmp/operator.log`**~~ — *parsed session 92; absorbed into L1-b log-path item.*
- ~~**[L10-b] DEBUG logs dump user data to a hardcoded path**~~ — *parsed session 92; absorbed into L1-b (log path + level + redaction).*

---

## Skipped — out of scope
- `pipeline/audio.py`, `pipeline/wake.py`, `pipeline/tts.py`, `pipeline/latency_probe.py` — audio/voice, audited separately.
- `connectors/macos_adapter.py`, `audio_capture.swift`, BlackHole/PulseAudio routing — audio plumbing.
- `AgentRunner` (`pipeline/runner.py`) internals — voice-mode orchestrator; only touched where it exposes the provider/LLM seams that chat mode shares.
- `pipeline/calendar_poller.py` — calendar auto-join is voice-mode UX.
- Multi-tenant / hosted service concerns; non-English support.
