# Open-Source Audit Report — Session 91

Read-only audit of the chat-mode codebase against the OSS ethos (`project_oss_ethos.md`). Findings are observations, not prescriptions. Audio/voice layer, multi-tenant, and non-English concerns were excluded per the audit plan.

---

## [Layer 1] Chat mode requires a Google Meet URL
**Severity:** critical
**Where:** `__main__.py:346-350, 270, 439-443`
**Finding:** (a) Chat mode unconditionally instantiates `MacOSAdapter`/`LinuxAdapter` and calls `connector.join(meeting_url)`; `_run_macos_terminal` hard-errors if `--chat` is passed without a URL. (b) Only connector *type* is configurable (`auto | audio | meet-captions`), all of which are Meet-in-a-browser. (c) A terminal/IPC/HTTP chat surface that runs without joining a meeting is not pluggable — there is no seam.
**Why it violates ethos:** "Agentic LLM loop is the product" + "headless core + chat API + reference CLI client" — today the LLM loop cannot be driven except by joining Google Meet.
**Suggested direction:** split `ChatRunner` from "meeting connector" — the runner should accept any chat source (CLI stdin, local socket, WS), with the Meet connector being one implementation.

## [Layer 1] Log path is hardcoded to `/tmp/operator.log`
**Severity:** major
**Where:** `__main__.py:214`, also referenced in `llm.py:84,90` and `chat_runner.py` error strings
**Finding:** (a) `logging.basicConfig(filename="/tmp/operator.log")` and multiple user-facing strings tell the user to `tail /tmp/operator.log`. (b) Nothing — log destination is not in `config.yaml`. (c) `logging.destination` should be a config key (file path, stderr, or eventually a pluggable sink).
**Why it violates ethos:** "Logging destination" is listed as user-overridable default.
**Suggested direction:** add `logging.destination` to `config.yaml` (default `/tmp/operator.log`); stop hardcoding the path in user-facing strings.

## [Layer 1] `OPENAI_API_KEY` is required even with `provider: anthropic`
**Severity:** critical
**Where:** `config.py:96`
**Finding:** (a) `OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]` — KeyError on import if unset. (b) `ANTHROPIC_API_KEY` correctly uses `.get(..., "")`. (c) Required-key check should follow the configured provider.
**Why it violates ethos:** BYOK is the README quickstart path; forcing an OpenAI key when the user selected Anthropic breaks the "bring your own provider" promise.
**Suggested direction:** make every provider key optional at import; `build_provider()` enforces presence of just the selected provider's key (the Anthropic branch already does this; do the same for OpenAI).

---

## [Layer 2] Connector interface mixes meeting and chat concerns
**Severity:** major
**Where:** `connectors/base.py:1-43`
**Finding:** (a) `MeetingConnector` conflates `join()/leave()`, `get_audio_stream()/send_audio()`, `send_chat()/read_chat()`, and `get_participant_count()/is_connected()` in one interface. (b) Nothing — the interface is a single abstract class. (c) Separate `ChatSource` (read/send text) and `MeetingSession` (join/leave/participants) interfaces; chat mode should need only the former.
**Why it violates ethos:** swappable seams — today, to add a non-Meet chat surface you must still satisfy the full meeting contract.
**Suggested direction:** extract a `ChatSource` protocol with just `read_chat/send_chat/is_connected`; make `MeetingConnector` compose it.

---

## [Layer 3] `READ_TOOLS` allowlist is a hardcoded Linear+GitHub list
**Severity:** critical
**Where:** `pipeline/chat_runner.py:19-38, 244`
**Finding:** (a) A Python `set` literal enumerates ~40 Linear/GitHub tool names as "safe to auto-run". (b) `confirm_tools` in `config.yaml` only *adds* to the confirm list — it cannot mark new tools as read-safe. (c) The permissioning model should come from a per-skill/per-server *manifest* declaring each tool's sensitivity tier, not from a Python allowlist.
**Why it violates ethos:** "Skills: manifest + install consent + sensitive-tier confirmation" — today there is no manifest, and auto-approval is gated by a closed list of tools we happen to know about.
**Suggested direction:** replace `READ_TOOLS` with a declared `sensitivity: read|write|sensitive` field per tool in the MCP server config (or, better, a skill manifest). Unknown = confirm. Let users/skill authors own this.

## [Layer 3] Linear/GitHub-specific special cases leak into the runner and MCP client
**Severity:** major
**Where:** `chat_runner.py:252-254`, `mcp_client.py:181-183, 186-191, 211-230`
**Finding:** (a) `ChatRunner._request_confirmation` strips `"limit"` if `name.startswith("linear__")`; `MCPClient.execute_tool` does the same; `MCPClient.resolve_github_user` is a dedicated method calling `github__get_me`; `LLMClient.inject_github_user` is a dedicated prompt injection. (b) Nothing is configurable — these are `if server == "linear"` / `if tool_name == "github__get_me"` branches. (c) Per-server quirk handling should live in that server's *config block* (e.g. `strip_args: [limit]`, `identity_tool: get_me`) or in a per-skill Python plugin — not in the neutral runner/client.
**Why it violates ethos:** "clean enough that adding a new backend is ~100 lines, not a refactor" — today, adding a Notion or Slack quirk means editing three files.
**Suggested direction:** move quirks into each server's config section; expose a small plugin point (e.g. `preprocess_args(tool, args)`) so skills can register their own fixups.

## [Layer 3] Hardcoded Google-Meet concerns inside the chat runner
**Severity:** major
**Where:** `chat_runner.py:40-42, 121-144, 146, 154-176`
**Finding:** (a) `POLL_INTERVAL=0.5`, `PARTICIPANT_CHECK_INTERVAL=3`, `ONE_ON_ONE_THRESHOLD=2`, alone-exit grace, own-message echo dedup via Meet's duplicate-DOM-id quirk — all baked in. (b) Nothing. (c) These are Meet-specific; a CLI chat source has no "participants" or "echo" concept. Runner should depend only on a `ChatSource`.
**Why it violates ethos:** conflates chat-loop logic with one specific surface, preventing "headless core + chat API".
**Suggested direction:** push participant-count and echo-dedup concerns *into* the Meet connector (it already knows the DOM); `ChatRunner` should just read messages and decide whether to respond.

## [Layer 3] Hardcoded user-facing strings in the runner
**Severity:** minor
**Where:** `chat_runner.py:263-268, 289, 317, 350, 354, 366, 384, 412-414`
**Finding:** (a) "I'd like to run X with: …. OK?", "Still working on that…", "That took too long — no response after Ns. Try again.", "Sorry, that tool call failed. Check the logs for details.", "Our conversation got too long — I've cleared the history…", "The {server} server seems to be having issues…". (b) Nothing. (c) Response tone/wording is an ethos-tier default — these should either flow through the LLM (signpost via tool-result) or be templatable.
**Why it violates ethos:** "Response tone/length is overridable" — users can't change these without editing code.
**Suggested direction:** for user-facing phrases the LLM should author, pass as tool-result strings; for system-level fallbacks, expose as templates in config.

---

## [Layer 4] Tool schema at the provider boundary is OpenAI-shaped
**Severity:** major
**Where:** `pipeline/providers/base.py:74`, `pipeline/providers/anthropic.py:64-82`, `pipeline/mcp_client.py:114-133`
**Finding:** (a) `LLMProvider.complete(tools=…)` documents tools as "OpenAI-function-calling shape"; `AnthropicProvider` translates *from OpenAI shape to Anthropic shape* inside `_openai_tools_to_anthropic`; `MCPClient.get_openai_tools()` emits the OpenAI `{"type":"function","function":{…}}` envelope. (b) Nothing — this is the neutral shape today. (c) A provider-neutral `ToolSchema` dataclass (name/description/input_schema), with each provider translating both sides.
**Why it violates ethos:** "Provider-neutral internal shapes; translate at the edge, never leak provider-specific fields into core." The 11.1/11.2 work got history neutral but left the tools surface OpenAI-shaped.
**Suggested direction:** introduce `ToolSchema` in `providers/base.py`; `MCPClient` returns `list[ToolSchema]`; both providers translate at their boundary. Rename `get_openai_tools` → `get_tools`.

## [Layer 4] `build_provider()` hard-codes a two-way switch
**Severity:** minor
**Where:** `pipeline/providers/__init__.py:11-29`
**Finding:** (a) `if name == "openai" … elif name == "anthropic" … else raise`. (b) Provider choice is configurable; provider *set* is not. (c) Entry-point registration (a dict or `importlib.entry_points` group) so a user can register a local/Ollama provider without editing the factory.
**Why it violates ethos:** minor violation of the swappable-seam commitment — the seam exists but the registry is closed.
**Suggested direction:** `PROVIDERS = {"openai": _build_openai, "anthropic": _build_anthropic}` as a dict, plus a public `register_provider(name, factory)` for third parties.

---

## [Layer 5] No manifest / install consent / sensitivity tiers
**Severity:** critical
**Where:** `config.yaml:75-103`, `chat_runner.py:231-244`
**Finding:** (a) MCP servers are listed in `config.yaml` with `command/args/env/hints/confirm_tools`; enabling a server grants its entire tool surface. (b) Only `confirm_tools` can opt-into confirmation per tool name. (c) A skill/server manifest declaring tools, sensitivity tiers (`read|write|sensitive`), requested scopes, and an install-time consent step. Sensitive calls (spend, send-as-user) should always require mid-conversation confirmation regardless of install consent.
**Why it violates ethos:** one of the named shape commitments — "Skills have manifests + install-time consent; sensitive capabilities always surface confirmation."
**Suggested direction:** define a manifest format (YAML alongside the MCP server entry, or a separate `skills/` dir); show it at install time; let the runtime derive confirm-or-not from declared sensitivity.

## [Layer 5] `MCPTool` (via the MCP SDK) is the in-code neutral tool shape
**Severity:** major
**Where:** `mcp_client.py:85, 114-133, 269-275`
**Finding:** (a) `self._tools` stores `{"server_name": …, "mcp_tool": mcp.Tool}`; `get_openai_tools()` reads `mcp_tool.description` and `mcp_tool.inputSchema` directly. (b) Nothing. (c) A neutral `ToolSchema` (name/description/input_schema/sensitivity) with MCP as one ingestion source. The ethos explicitly calls this out: "MCP-only tool protocol is fine, but `MCPTool` should not be the in-code neutral shape."
**Why it violates ethos:** direct callout in the ethos doc.
**Suggested direction:** introduce `ToolSchema`; `MCPClient._connect_server` converts each `mcp.Tool` into it on discovery.

---

## [Layer 6] No `ConversationStore` seam; history is in-process only
**Severity:** major
**Where:** `pipeline/llm.py:28-30, 218-225, 339-363`
**Finding:** (a) `self._history = []` in memory; trimmed in-place; dropped on process exit. (b) `CHAT_HISTORY_TURNS` caps pair count. (c) A `ConversationStore` protocol (append/read/clear) with an in-memory default and e.g. SQLite/JSONL-on-user-machine implementations. History does NOT leave the user's machine today (core ethos OK), but it also can't persist across restarts or be swapped for a user-chosen store.
**Why it violates ethos:** "Conversation storage backend" is a listed overridable default, and "swappable seams" names conversation store explicitly.
**Suggested direction:** extract the history list behind a `ConversationStore` interface; ship an in-memory default; design the file-backed store next.

## [Layer 6] Trim/collapse logic is baked in, not delegated to the store
**Severity:** minor
**Where:** `llm.py:308-363`
**Finding:** (a) `_collapse_tool_exchange` and `_trim_history` implement one specific retention policy (drop intermediate tool exchanges, keep N pairs). (b) `CHAT_HISTORY_TURNS` is the only knob. (c) These are policy, not mechanism — should live behind the `ConversationStore` so users can pick "keep everything", "summarize", etc.
**Why it violates ethos:** closes a policy decision the ethos marks as overridable.
**Suggested direction:** once `ConversationStore` exists, move collapse/trim into a `RetentionPolicy`.

---

## [Layer 7] No headless core + chat API + client separation
**Severity:** critical
**Where:** `__main__.py:238-286, 435-462`, `pipeline/chat_runner.py` overall
**Finding:** (a) `__main__.py` builds connector, LLM, MCP, and `ChatRunner` in one flow; `ChatRunner` is instantiated with live objects and runs the loop inline. (b) `--chat` toggles mode; nothing else. (c) The target is `operator serve` (headless core exposing a stable chat API) + `operator run` (reference CLI client). Today the process *is* the UI — there is no daemon, no API, no alternate client.
**Why it violates ethos:** the named target architecture. Flagged explicitly in the audit plan.
**Suggested direction:** design the chat API (minimally: POST message, GET stream of replies, GET/POST confirmations) now, even if the first "client" is still the Meet connector. This unlocks a CLI client, a future web UI, and testing without a browser.

---

## [Layer 8] Config is YAML-only; no CLI override layer
**Severity:** minor
**Where:** `__main__.py:152-185`, `config.py:1-99`
**Finding:** (a) `argparse` exposes only `meeting_url`, `--force`, `--chat`, `--check-mcp`. Everything else (provider, model, wake phrase, MCP server list) is YAML-only. (b) YAML + env-var interpolation for MCP env blocks. (c) Layered override: YAML → env → CLI flags → per-skill. MVP explicitly OK'd YAML+env; flagging as a tracked gap, not an urgent fix.
**Why it violates ethos:** "Config is layered: YAML → env → CLI flags → per-skill overrides."
**Suggested direction:** after the skill manifest lands, add CLI `--set llm.provider=openai` style overrides.

## [Layer 8] Secrets loading is import-time and fails hard
**Severity:** major
**Where:** `config.py:96`
**Finding:** Duplicate of the Layer 1 finding but in a different dimension: any import of `config` KeyErrors without `OPENAI_API_KEY`, which also means scripts like `--check-mcp` cannot run without a provider key, even though they do not hit any LLM.
**Why it violates ethos:** violates "zero friction BYOK" for non-OpenAI users and for tooling that does not need an LLM at all.
**Suggested direction:** make all secret loads lazy / `.get(..., "")`; error only when the specific subsystem is actually used.

---

## [Layer 9] Prompt-injection helpers bake tone and behavior into code
**Severity:** major
**Where:** `llm.py:42-112`
**Finding:** (a) `inject_mcp_hints` appends server hints verbatim; `inject_mcp_status` writes fixed sentences like *"If the user asks about tools from a failed server, tell them it failed to load and to check /tmp/operator.log"*; `inject_github_user` writes *"Always use \"{login}\" as the owner — never guess from chat display names"*. (b) `hints` per server is user-editable; status/github-user wording is not. (c) A prompt-fragments registry: each behavior is a named, user-overridable string template.
**Why it violates ethos:** "System prompt — user owns the whole thing" — true for the root prompt, but sub-prompts the framework injects are not user-owned.
**Suggested direction:** move each injected block to a named template in config (e.g. `prompts.mcp_status_unavailable`), with sensible defaults.

## [Layer 9] Fallback error strings reference our log file
**Severity:** minor
**Where:** `llm.py:84, 90`, `chat_runner.py:366`, several places in `__main__.py`
**Finding:** User-visible error strings say "check /tmp/operator.log" — coupling the UX to the hardcoded log destination.
**Why it violates ethos:** compounds the Layer 1 log-path issue into user-facing output.
**Suggested direction:** resolve the log destination from config and interpolate into these strings (or drop the path entirely).

---

## [Layer 10] No network calls beyond configured providers and MCP servers
**Severity:** — (compliance note)
**Where:** repo-wide grep
**Finding:** Outbound network activity is limited to (a) the configured LLM provider SDK, (b) the configured TTS provider (out of scope — voice), and (c) the MCP servers the user opted into. No analytics, crash-reporting, or heartbeat endpoints. Zero-telemetry commitment **holds** in chat mode.
**Why it satisfies ethos:** confirms the non-negotiable "zero telemetry" core.

## [Layer 10] DEBUG logs contain user data
**Severity:** minor
**Where:** `mcp_client.py:194, 401`, `llm.py:130`
**Finding:** (a) DEBUG-level lines dump tool arguments (`json.dumps(arguments)`), first 500 chars of tool results, and user utterances. (b) Log level is module-default, not config-controlled; log file is `/tmp/operator.log`. (c) This is local-only, so it does not violate "no data leaves user's machine" — but combined with the hardcoded log path and shared `/tmp`, it's worth exposing a `logging.level` knob.
**Why it is a minor concern:** data doesn't leave the machine but the destination is non-obvious to users and shared-tmp on multi-user systems can leak locally.
**Suggested direction:** add `logging.level` to config; default DEBUG → INFO once the auditor agrees on what minimum is worth keeping.

---

## Prioritized summary

### Critical (blocks core ethos commitments)
- **[L1] Chat mode requires a Meet URL** — chat is welded to Google Meet; no headless path.
- **[L1 / L8] `OPENAI_API_KEY` required even for Anthropic runs** — breaks BYOK quickstart.
- **[L3] Hardcoded Linear+GitHub `READ_TOOLS` allowlist** — permissioning is a closed Python list, not a manifest.
- **[L5] No skill manifest / install consent / sensitivity tiers** — named shape commitment absent entirely.
- **[L7] No headless core + chat API + reference client separation** — named target architecture absent.

### Major (awkward but workable)
- **[L2] `MeetingConnector` conflates meeting + chat** — no `ChatSource` seam.
- **[L3] Linear/GitHub quirks leak into runner and MCP client** — per-server plugin point missing.
- **[L3] Meet-specific loop concerns baked into `ChatRunner`** — participant count, echo dedup, 1-on-1 detection.
- **[L4] Tools surface at provider boundary is OpenAI-shaped** — `_openai_tools_to_anthropic` translates the wrong direction.
- **[L5] `MCPTool` is the in-code neutral tool type** — ethos explicitly flags this.
- **[L6] No `ConversationStore` seam; history in-process only** — named overridable default.
- **[L8] Import-time required secrets** — duplicate of L1 but broader (e.g. `--check-mcp` inherits the constraint).
- **[L9] Framework-injected prompt fragments bake wording and behavior** — sub-prompts not user-owned.
- **[L1] Log path hardcoded `/tmp/operator.log`** — named overridable default.

### Minor (polish)
- **[L3] Hardcoded user-facing strings in `ChatRunner`** — "Still working…", confirmation msg, etc.
- **[L4] `build_provider()` is a closed two-way `if/elif`** — no registration API.
- **[L6] Retention policy baked into `LLMClient._trim_history` / `_collapse_tool_exchange`** — not pluggable.
- **[L8] No CLI override layer** — YAML + env only (acknowledged MVP scope).
- **[L9] Error strings reference `/tmp/operator.log`** — compounds the log-path coupling.
- **[L10] DEBUG logs dump user data to a hardcoded path** — add log level + destination controls.

---

## Skipped — out of scope
- `pipeline/audio.py`, `pipeline/wake.py`, `pipeline/tts.py`, `pipeline/latency_probe.py` — audio/voice, audited separately.
- `connectors/macos_adapter.py`, `audio_capture.swift`, BlackHole/PulseAudio routing — audio plumbing.
- `AgentRunner` (`pipeline/runner.py`) internals — voice-mode orchestrator; only touched where it exposes the provider/LLM seams that chat mode shares.
- `pipeline/calendar_poller.py` — calendar auto-join is voice-mode UX.
- Multi-tenant / hosted service concerns; non-English support.
