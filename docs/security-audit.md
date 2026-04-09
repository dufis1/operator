# Security Vulnerability Audit — Step 9.5

*Session 65, April 8, 2026*

## Scope

Systematic review of input sanitization, credential handling, MCP server sandboxing, and dependency security across the Operator codebase.

## Methodology

Searched all `.py` files for: `shell=True`, `eval(`, `exec(`, `os.system`, `os.popen`, `pickle`, `yaml.load(`, `innerHTML`, `document.write`, subprocess calls with user-controlled strings, API key logging, and credential exposure patterns.

---

## Findings

### FINDING 1: Error message leaks internal details to meeting chat

**File:** `pipeline/chat_runner.py:242`
**Severity:** Medium
**Category:** Information disclosure

When an MCP tool call fails, the raw Python exception string was sent directly to meeting chat via `self._send(f"Tool call failed: {e}")`. Exception messages can contain internal file paths, API error details (including partial URLs with tokens), or stack trace fragments — all visible to every meeting participant.

**Fix applied:** Replaced with a generic error message. The full exception remains in `/tmp/operator.log` for debugging.

---

### FINDING 2: MCP server subprocesses inherit full environment

**File:** `pipeline/mcp_client.py:243`
**Severity:** Low
**Category:** Credential exposure to child processes

MCP servers are launched with `env={**os.environ, **self._srv_config["env"]}`, which means every MCP server subprocess inherits `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, `GITHUB_TOKEN`, and any other secrets in the process environment. A malicious or compromised MCP server could read these.

**Status:** Noted, not fixed. Fixing requires an allowlist of env vars per server, which adds config complexity. Current risk is low because MCP servers are user-configured (not auto-discovered), and both configured servers (Linear via mcp-remote, GitHub via official binary) are trusted. Worth revisiting if Operator ever supports community-contributed or auto-installed MCP servers.

---

### FINDING 3: No depth limit on chained tool calls

**File:** `pipeline/chat_runner.py:260`
**Severity:** Low
**Category:** Denial of service (self-inflicted)

After executing a tool, the LLM can request another tool call (line 260), which triggers another confirmation prompt. There is no depth limit — a pathological LLM response could create an infinite confirmation loop. In practice, the user can break the chain by saying "no" to any confirmation, and the 30s MCP tool timeout (`_ServerHandle.call_tool`) prevents individual calls from hanging. But a runaway loop could spam the chat.

**Status:** Noted. Step 9.10 (comprehensive error handling) is the right place to add a `MAX_TOOL_CHAIN_DEPTH` guard. Current risk is low — requires adversarial LLM behavior and user repeatedly confirming.

---

### FINDING 4: Dependencies are unpinned

**File:** `requirements.txt`
**Severity:** Low
**Category:** Supply chain risk

No version pins in `requirements.txt`. A compromised upstream package could be installed silently on `pip install`. The venv has current, non-vulnerable versions of all packages (checked: openai 2.29.0, playwright 1.58.0, mcp 1.27.0, numpy 2.4.3, cryptography 46.0.5).

**Status:** Noted. Step 10.11 (dependency pinning) already covers this. No action needed now.

---

### FINDING 5: Log file uses default umask

**File:** `__main__.py:94`
**Severity:** Very Low
**Category:** Information disclosure on shared systems

`/tmp/operator.log` is created with the user's default umask (typically 0022), making it world-readable. The log contains meeting URLs, chat messages, LLM prompts/responses, and tool call arguments — but no API keys. On a single-user machine this is fine; on a shared server it could leak meeting content.

**Status:** Noted. Not a concern for the current user base (personal machines). Worth fixing in Phase 10 packaging if Operator targets multi-user servers.

---

## Areas Reviewed — No Issues Found

| Area | Notes |
|------|-------|
| **Shell injection** | No `shell=True` anywhere. All subprocess calls use argument lists. No user-controlled strings interpolated into subprocess commands. |
| **JavaScript injection** | Playwright `evaluate()` calls pass user data as JSON-serialized arguments (not string interpolation). `fill()` is used for chat input (not `innerHTML`). |
| **YAML deserialization** | `config.py` uses `yaml.safe_load()` — safe against arbitrary code execution. |
| **API key logging** | API keys are never logged. Log statements reference model names, token counts, and truncated responses — not secrets. |
| **Auth state handling** | `auth_state.json` and `browser_profile/` are both in `.gitignore`. Cookie injection uses Playwright's `add_cookies()` API (no raw cookie header manipulation). |
| **Prompt injection** | Chat messages flow to the LLM as user messages, not system messages. The system prompt is hardcoded in `config.yaml`. Meeting participants cannot modify the system prompt. Standard LLM prompt injection risks apply (participant could try "ignore your instructions"), but this is inherent to all LLM-powered tools and mitigated by the confirmation flow for tool calls. |
| **File path traversal** | No user-controlled file paths. All file operations use hardcoded paths (`/tmp/operator.log`, `debug/`, `browser_profile/`). |
| **Pickle / unsafe deserialization** | None found. |
| **Dependency vulnerabilities** | All installed packages are current versions with no known CVEs at time of audit. |

---

## Summary

| # | Finding | Severity | Status |
|---|---------|----------|--------|
| 1 | Error message leaks to chat | Medium | **Fixed** |
| 2 | MCP servers inherit full env | Low | Noted (revisit if auto-install added) |
| 3 | No tool chain depth limit | Low | Noted (deferred to step 9.10) |
| 4 | Unpinned dependencies | Low | Noted (covered by step 10.11) |
| 5 | Log file world-readable | Very Low | Noted (revisit in Phase 10) |
