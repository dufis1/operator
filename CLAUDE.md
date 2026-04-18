# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Operator is a chat-based AI meeting participant. It joins Google Meet, opens the chat panel, watches for messages addressed to it (via the `@operator` trigger phrase, or any message in a 1-on-1), queries an LLM with tool access via MCP (Linear, GitHub), and posts the reply back into meeting chat.

## Commands

### Run

```bash
operator pm https://meet.google.com/xxx-yyyy-zzz   # join a specific Meet
operator pm                                        # auto-open meet.new
operator list                                      # show available agents
operator                                           # usage + agent list
```

Replace `pm` with any bot under `agents/` (`engineer`, `designer`, …). Every
run selects an agent explicitly — there is no ambient root `config.yaml`
anymore. The `operator` wrapper (symlinked into `~/.local/bin/`) handles venv
activation; you can also call `python __main__.py <name> [url]` directly if
the venv is already active.

### Logs & Diagnostics

```bash
tail -f /tmp/operator.log
grep "TIMING" /tmp/operator.log          # latency markers
grep "LLM\|MCP\|ChatRunner" /tmp/operator.log
```

### Tests

Tests are standalone scripts — no pytest runner. Run them individually:

```bash
source venv/bin/activate
python tests/test_chat_hardening.py         # history cap, trigger gating, sender filter
python tests/test_911_size_management.py    # tool-result size + context overflow
python tests/test_912_tool_timeout.py       # tool heartbeat + hard timeout
python tests/test_913_tool_history_collapse.py
python tests/test_915_reconnection.py       # disconnect + grace-period exit
python tests/test_guardrails.py             # binary/null-byte blocking
python tests/test_anthropic_provider.py
python tests/test_mcp_client.py
python tests/test_mcp_shutdown.py
```

## Architecture

### Layer Overview

```
Entry
  __main__.py                 — CLI entry; preflights, builds connector + LLM + MCP, runs ChatRunner

Connectors (platform-specific — implement MeetingConnector)
  connectors/base.py          — abstract: join(), send_chat(), read_chat(),
                                 get_participant_count(), is_connected(), leave()
  connectors/macos_adapter.py — Playwright + persistent Chrome profile
  connectors/linux_adapter.py — Playwright + headless Chromium
  connectors/session.py       — JoinStatus state + browser session bookkeeping

Pipeline (platform-agnostic)
  pipeline/chat_runner.py     — polling loop; trigger detection, 1-on-1 mode,
                                 tool-confirmation flow, participant-based auto-leave
  pipeline/meeting_record.py  — append-only JSONL per meeting at ~/.operator/history/<slug>.jsonl;
                                 single source of truth for chat history (meta header + tail(n))
  pipeline/llm.py             — LLMClient: builds prompt from MeetingRecord tail + in-memory
                                 scratchpad (tool calls/results), MCP status/hints injection
  pipeline/providers/         — neutral LLMProvider interface + OpenAI + Anthropic backends
  pipeline/mcp_client.py      — stdio MCP transport, tool discovery, failure backoff
  pipeline/guardrails.py      — validate tool results (binary/null-byte rejection)
```

### Key Data Flow

1. `MeetingConnector.join()` launches Chrome, signs in via saved session, enters the meeting, opens the chat panel and installs a MutationObserver over the chat DOM.
2. `ChatRunner._loop()` polls `read_chat()` every 500 ms, drops already-seen/own messages, and checks for the trigger phrase (or treats any message as addressed in a 1-on-1).
3. `LLMClient.ask()` reads the tail of the meeting's JSONL via `MeetingRecord.tail(n)` and sends those messages — plus the in-memory tool-loop scratchpad — to the configured provider with MCP tool schemas attached.
4. If the model returns a `tool_call`, `ChatRunner` either auto-executes (read-only tools in the allowlist) or requests user confirmation in chat. Tool result is fed back via `send_tool_result`; the model summarizes or chains.
5. The final text reply goes back through `connector.send_chat()`.

### Configuration

Every run names an agent explicitly (`operator <name> [url]`). Config loading is driven by the `OPERATOR_BOT` env var — the CLI sets this before importing `config`, which then reads `agents/<name>/config.yaml` into module-level constants. There is no root `config.yaml`; there is one config file per bot under `agents/`. Top-level blocks:
- `agent` — `name`, `trigger_phrase`, `conversation_timeout`, `alone_exit_grace_seconds`, `first_contact_hint`, `tagline`
- `llm` — `provider` (`openai` | `anthropic`), `model`, `system_prompt`, `history_messages` (tail size replayed from the meeting record), `max_tokens`, `tool_result_max_chars`, `tool_timeout_seconds`, `tool_heartbeat_seconds`
- `connector` — `browser_profile_dir`, `auth_state_file`, `idle_timeout_seconds`
- `mcp_servers` — per-server command, env, hints, and confirm-tool overrides

API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, etc.) live in a single `.env` at the repo root, shared across all bots. Never commit `.env`, `browser_profile/`, or `auth_state.json`.

### Tool Confirmation

`chat_runner.py` defines `READ_TOOLS` — a set of known read-only MCP tools that auto-execute without confirmation. Any tool not in that set prompts the user in chat before running. Per-server overrides (`confirm_tools`) in the bot's `agents/<name>/config.yaml` can force confirmation on specific tools.

### Participant-based Auto-leave

When the bot has seen at least one other participant and is then alone for `ALONE_EXIT_GRACE_SECONDS`, it leaves automatically. 1-on-1 mode (participant count ≤ `ONE_ON_ONE_THRESHOLD`) skips the trigger-phrase requirement.

## Development Notes

- `docs/agent-context.md` tracks current dev phase, hard-won debugging knowledge, and working context — read it before making structural changes.
- `docs/roadmap.md` has the phase checklist and strategic direction.
- The voice pipeline was decoupled in session 93 (April 2026) and preserved on the `voice-preserved` branch. `main` is chat-only.
- `browser_profile/` and `auth_state.json` hold logged-in Google session state — never commit them.
