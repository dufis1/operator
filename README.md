# Brainchild

Chat-based AI meeting participant for Google Meet. Joins, reads chat, replies
via an LLM with tool access (Linear, GitHub, and other MCP servers you wire
up), and leaves when everyone else does.

```bash
brainchild run pm                                        # open a fresh Meet
brainchild run pm https://meet.google.com/xxx-yyyy-zzz   # join a specific Meet
brainchild try pm                                        # terminal test-drive, no Meet
brainchild                                               # show available agents
```

`pm` is a sample bot under `agents/`. Drop in `brainchild build` to create your own.

## Privacy & logs

Brainchild writes a detailed diagnostic log to **`/tmp/brainchild.log`** on every
run. For now, this file contains:

- The Meet URL the bot joined (a capability token — anyone with it can join).
- Chat messages the bot sees, including sender names.
- LLM prompt/response metadata and tool call arguments + results.
- Captions, when `transcript.captions_enabled: true` in the agent config.

**The file never leaves your machine**, but it is plain text in a shared
directory — treat it like any other local artifact. macOS typically clears
`/tmp` on reboot; Linux may not. Delete it manually if it matters.

Chat history also lands in `~/.brainchild/history/<slug>.jsonl` — that's the
durable record the bot replays from between turns. Same sensitivity profile.

### Never commit these

API keys live in a single `.env` at `~/.brainchild/.env`, shared across all
bots. The following files hold secrets or logged-in Google session state and
must stay local:

- `~/.brainchild/.env` — API keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, GITHUB_TOKEN, …)
- `credentials.json` — Google OAuth client secrets
- `token.json` — Google OAuth access/refresh tokens
- `~/.brainchild/auth_state.json` — Playwright storage state (Google session cookies)
- `~/.brainchild/browser_profile/` — persistent Chrome profile (Google session cookies)

All of the above are ignored by `.gitignore`. If you see one show up in
`git status` untracked, something has gone wrong — don't `git add .` blindly.
See `docs/security.md` for the full threat model.

## Voice mode

Each bot has a `voice` setting under `agent:` that controls how it
communicates across three surfaces: progress narration ("Working: …"),
confirmation prompts ("Want me to …?"), and reply content. Two modes:

- **`plain`** — meeting-friendly. Translates tool names and arguments
  into plain English for non-developer audiences. Confirmation prompt:
  "Want me to grab the Sentry issue? (yes/no)". Narrator: "Checking
  Sentry...". Reply content: leads with cause-and-fix in plain English,
  offers technical detail as follow-up. **Default for new bots.**
- **`technical`** — developer-flavored. Tool names verbatim, full
  parameter dump in confirmation prompts, file:line citations and
  code blocks in replies. Use when you want full transparency.

Switch in `agents/<bot>/config.yaml`:

```yaml
agent:
  name: "MyBot"
  voice: plain        # or technical
```

Imperative fields (URLs, file paths, Bash commands) are shown verbatim
in **both** modes — these describe what's about to happen and you need
to see them to make a sensible yes/no decision.

The pre-session-169 `permission_verbosity: terse | verbose` field still
loads with a deprecation log (`terse` → `plain`, `verbose` → `technical`).
Move the value to `agent.voice` to silence the warning.

## MCP permissions

For the `claude` agent (track A), built-in tools (Read, Bash, Write, …) are
gated by the `permissions` block in `agents/<bot>/config.yaml`. The `brainchild
build` wizard walks you through the built-in tools as a checklist; tools listed
under `auto_approve` run silently, anything under `always_ask` (and anything
not on either list) pauses the bot for a chat confirmation.

**MCP tools** (Sentry, Linear, GitHub, etc.) ask by default — every Sentry
issue lookup, every Linear ticket fetch, every GitHub PR read. To skip the
prompt for routine reads, edit the YAML and add fnmatch patterns:

```yaml
permissions:
  auto_approve:
    - Read
    - Grep
    - Glob
    - LS
    - WebSearch
    - ToolSearch
    # Per-server read auto-approval. Patterns are fnmatch globs.
    - "mcp__sentry__get_*"
    - "mcp__sentry__list_*"
    - "mcp__sentry__search_*"
    - "mcp__claude_ai_Linear__get_*"
    - "mcp__claude_ai_Linear__list_*"
  always_ask:
    - Bash
    - Write
    - Edit
    - MultiEdit
    - NotebookEdit
    - WebFetch
    - Task
    # Specific deny on top of a broad allow — always_ask wins on overlap:
    - "mcp__sentry__analyze_issue_with_seer"
```

`always_ask` is matched first, so an explicit deny pattern beats a broader
allow pattern on the same tool.

**Audit your patterns after upgrading an MCP server.** MCP tool names are
server-controlled. If a server renames `get_resource` → `fetch_resource`, your
`get_*` glob silently stops covering the renamed tool — which fails safe (the
bot starts asking again) but is worth a glance after `claude mcp` upgrades.

## Uninstall

```bash
uv tool uninstall brainchild   # removes the CLI + PATH shim
rm -rf ~/.brainchild           # removes agents, history, and .env
```

## More

- `CLAUDE.md` — architecture, commands, configuration layout.
- `docs/roadmap.md` — phase plan.
- `docs/agent-context.md` — current development state.
