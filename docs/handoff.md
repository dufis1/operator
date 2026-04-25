# Session 166 handoff (2026-04-26) — Phase 14.12.2 COMPLETE in code → live-meeting validation in 167

**Track A claude_cli provider shipped end-to-end with 16 passing tests. Next session runs `docs/live-skill-tests.md` against a real Meet.**

## What shipped this session (commit `28153b9`)

- **`pipeline/providers/claude_cli.py`** — per-meeting `claude -p` subprocess as `LLMProvider`. Lazy spawn, subscription-auth assertion (`apiKeySource == "none"`), paragraph streaming via `--include-partial-messages`, PreToolUse hook + named-pipe IPC, restart-on-death with synthesized opener (probe 7 strategy 2). Set up via `set_permission_handler()` from chat_runner.
- **`pipeline/permission_bridge.py`** — Python rewrite of `perm_bridge.sh`. Stdlib-only imports so it can be invoked by absolute path from claude's hook (avoids the PYTHONPATH dance — brainchild isn't `pip install -e .`'d in dev).
- **`pipeline/permission_chat_handler.py`** — round-trips PreToolUse decisions through meeting chat. `auto_approve` list runs silently; everything else posts a chat prompt and blocks. Reads chat directly + claims consumed IDs in `runner._seen_ids` so the main poll loop doesn't re-feed user replies to the LLM.
- **`build_provider()`** routes `llm.provider: "claude_cli"`. **`config.py`** parses `permissions.{auto_approve, always_ask}` and treats MCP blocks as toggle-only under track A. **`chat_runner._wire_track_a_permissions()`** plugs the handler into the provider before `_loop()`.
- **`agents/claude/config.yaml`** rewritten 398→95 lines for track-A schema. User-level `~/.brainchild/agents/claude/config.yaml` was deleted at end-of-session per user request — first `brainchild run claude` reseeds from the new bundle.
- **Tests:** 8 claude_cli provider smoke tests (real `claude` CLI, subscription auth) + 8 PermissionChatHandler unit tests (fake connector). All 16 pass.
- **`docs/live-skill-tests.md`** rewritten for track A. Every prior use case preserved but reshaped to exercise claude's native tools + inherited MCPs instead of the brainchild skill bundles + `delegate_to_claude_code`. New Session 0 (track-A safety, 6 gates) added.

## Mid-session redesign worth remembering

Worktree isolation was originally point #3 in the spike report's recommendation list. Re-derived from track-A's threat model and dropped: chat-confirmation hook is the safety net, sub-agent opacity handled by denying the `Task` tool in `always_ask`. Track-A claude operates directly on whatever absolute paths the user names — multi-repo workflows in one meeting work naturally. If a future user wants sandbox isolation specifically for experimental refactors, that's an opt-in `permissions.worktree: true` knob to add later, not a default.

## Two hard-won lessons (full entries in agent-context.md)

1. claude in `--input-format stream-json` emits `system/init` only after the first user envelope, not at startup. Don't wait for it at spawn.
2. Bridge scripts invoked by claude must be absolute-path-invoked + stdlib-only — `python -m brainchild.pipeline.<x>` fails because brainchild isn't pip-installed in the dev venv.

## Exact next step

**Session 167: live-meeting validation per `docs/live-skill-tests.md`.** Pre-flight:
- `claude auth status --json` shows authenticated.
- `brainchild build` so the bundled track-A claude config seeds into `~/.brainchild/agents/claude/config.yaml`.
- `claude mcp list` shows the MCPs we'll lean on (Linear, GitHub, Sentry).
- `/tmp/brainchild.log` is wired.

Then run **Session 0** (track-A safety) before anything else — if those 6 gates fail, none of the use-case tests can succeed. Only after Session 0 passes, proceed to Sessions 1 (engineer flow), 2 (PM/sprint), 3 (live-bug triage).

After live validation lands, **14.12.3** (track-B repositioning — wizard becomes "build a custom bot," "claude" reserved name), **14.12.4** (skills migration to `~/.claude/skills/`), and **14.12.5** (README rewrite) remain before MVP can ship.

## Open carry-overs (unchanged from prior handoff)

(a) Stuck-LLM retry semantics still deferred. (b) `chat_runner._send` doesn't surface dead-browser state. (c) Linux adapter parity for ID-based dedup. (d) GitHub `operator → brainchild` repo rename still deferred. (e) Anthropic 30k-tokens/min ceiling — likely dissolves under track A since inner-claude lazy-loads its own tools. (f) Session-160 Chrome-runtime preflight is dead weight — fold into 14.12 cleanup pass when 14.12 ships. (g) `debug/model-log.md` not updated this session — track-A added new log lines (`ClaudeCLI subprocess ready`, `PermissionChatHandler: ...`, `TIMING claude_cli_turn`) but live grep against `/tmp/brainchild.log` is deferred to session 167 once we have a real meeting log to compare against.
