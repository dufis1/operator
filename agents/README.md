# Agents

Ready-to-run agent configurations for Operator. Each subfolder is a complete,
working setup — pick one, point Operator at it, join a meeting.

The `agents/` folder is the "choose your fighter" layer. You can:

- **Use one as-is** — copy its `config.yaml` and run.
- **Power it up** — add your own MCP servers, drop extra skill files in, change the model.
- **Contribute one** — open a PR with a new folder. See [Contributing](#contributing) below.

Operator itself is model-agnostic (OpenAI or Anthropic) and skill-format-neutral
(any markdown file with YAML frontmatter works). Agents in this folder lean on
the [Claude Code skill format](https://docs.claude.com/claude-code) because it's
the most widely adopted skill format in the wild — but nothing about an agent
is Claude-specific. Swap the model in `config.yaml` and the same skills work.

---

## Using an agent

```bash
# 1. Fill in your API keys in .env
#    (see the agent's README for which keys it needs)

# 2. Run — name the bot you want, optionally pass a Meet URL
operator engineer https://meet.google.com/xxx-yyyy-zzz
operator engineer                                      # auto-opens meet.new
operator list                                          # show all bots
```

Every run names the bot explicitly. Config lives in `agents/<name>/config.yaml`
and is loaded at runtime — there is no ambient root `config.yaml`. Each agents
member's own `README.md` lists the keys, MCP servers, and any prerequisites it
needs.

---

## Folder layout

Every agent is a self-contained folder:

```
agents/<name>/
  README.md          # What it does, who it's for, setup, 15s demo GIF
  config.yaml        # Complete Operator config — model, MCP servers, skills path
  skills/            # Optional. Markdown skill files bundled with this agent.
    <skill>.md
  .env.example       # Optional. Lists required env vars with placeholder values.
```

### `README.md` structure

Keep it short. Five sections:

1. **What it does** — one sentence.
2. **Who it's for** — the person who would install this.
3. **What you need** — API keys, MCP servers, access to external systems.
4. **Setup** — copy-pasteable commands.
5. **Demo** — a GIF or 15–30s video showing the "aha" moment.

### `config.yaml`

Must be a complete, runnable Operator config (not a fragment). Every
`agents/<name>/config.yaml` has the same six top-level blocks: `agent`,
`llm`, `connector`, `skills`, `transcript`, `mcp_servers`. Full field
reference is in [Config reference](#config-reference) below, and `engineer/`
or `pm/` is a working template to start from. The agent should work on a
fresh clone after the user fills in their API keys — no hidden dependencies.

**Declare your MCP's read tools.** Each `mcp_servers.<name>` block takes a
`read_tools:` list of tool names that auto-execute without user confirmation.
Anything not listed confirms by default — safe-by-default for unknown tools,
but a friction wall if you forget to populate the list. Start your MCP once
locally to see the actual tool names in the startup log
(`MCP server '<name>' connected — N tools`). Write tools should be omitted
from that list — they belong behind the confirmation gate.

### `skills/`

Optional. If the agent ships its own skills, put them here. Skills are
markdown files with YAML frontmatter:

```markdown
---
name: file-linear-ticket
description: Create a Linear ticket from a bug described in chat
---

When a user describes a bug or feature request in meeting chat, create a
Linear ticket with:
- Title: short summary (< 80 chars)
- Description: reproduction steps if given, else full quote
- Label: "bug" or "feature"
...
```

Skill files can also live in `~/.claude/skills/` — the agent's `config.yaml`
points at whichever directories it wants to load from.

---

## Config reference

Every `agents/<name>/config.yaml` loads through `config.py` into module-level
constants that the runtime reads. The tables below are the source of truth
for what each field does and what the runtime expects — shipped config files
stay comment-free on purpose.

### `agent:`

| Field | Type | Default | What it does |
|---|---|---|---|
| `name` | string | required | Display name shown in chat and in `operator list` (e.g. `PM`). |
| `trigger_phrase` | string | `@operator` | Substring that marks a message as addressed to the bot in a multi-party meeting. Ignored in 1-on-1s (any message is treated as addressed). |
| `conversation_timeout` | int (seconds) | required | Idle gap after which a conversation thread is considered finished and the bot drops first-contact context. |
| `alone_exit_grace_seconds` | int (seconds) | `60` | Once the bot has seen at least one peer, then becomes alone, it leaves after this many seconds. |
| `first_contact_hint` | string | `""` | Extra line appended to the system prompt the first turn the bot talks to a given person. Supports `{first_name}` substitution. |
| `tagline` | string | `""` | One-liner shown in `operator list`, the setup wizard picker, and the build card. |

### `llm:`

| Field | Type | Default | What it does |
|---|---|---|---|
| `provider` | `openai` \| `anthropic` | required | Which provider backend to use. Switches the underlying SDK and tool-call format. |
| `model` | string | required | Provider-specific model ID (e.g. `claude-sonnet-4-5`, `gpt-4o`). |
| `system_prompt` | string | required | The bot's persona and operating instructions. YAML block scalar (`\|`) is preferred so newlines render. |
| `history_messages` | int | `40` | How many tail messages from the meeting's JSONL are replayed as chat history each turn. |
| `max_tokens` | int | `150` | Upper bound on LLM output tokens per turn. |
| `tool_result_max_chars` | int | `50000` | Tool results larger than this are truncated before being fed back to the LLM. |
| `tool_timeout_seconds` | int (seconds) | `60` | Hard timeout for a single tool call. After this the call is cancelled and an error is returned to the model. |
| `tool_heartbeat_seconds` | int (seconds) | `8` | How often the bot posts a "still working…" update in chat while a long tool call is in flight. |

### `connector:`

| Field | Type | Default | What it does |
|---|---|---|---|
| `browser_profile_dir` | path | required | Persistent Chrome profile directory so Google sign-in survives restarts. Never commit this. |
| `auth_state_file` | path | required | Playwright `storageState` JSON for quick re-auth. Never commit this. |
| `idle_timeout_seconds` | int (seconds) | `600` | If the browser session idles this long, the bot closes it. |

### `skills:`

| Field | Type | Default | What it does |
|---|---|---|---|
| `paths` | list of paths | `[]` | Directories to scan for `SKILL.md` files. Relative paths resolve from the repo root; `~` expands. |
| `progressive_disclosure` | bool | `true` | If true, only the skill name + description are visible to the LLM until it explicitly asks to load the body — keeps the prompt lean. |

### `transcript:`

| Field | Type | Default | What it does |
|---|---|---|---|
| `captions_enabled` | bool | `false` | Ingest Google Meet live captions as ambient context (each line tagged `[spoken]` in the prompt). Requires captions to be turned on in the Meet UI. |
| `silence_seconds` | float (seconds) | `0.7` | Dead-air gap after which a buffered caption chunk is committed to history. Lower = faster reactivity, more fragmentation; higher = cleaner chunks, more lag. |

### `mcp_servers:`

A map of `<server-name>` → server block. Disabled blocks are skipped by the
loader but kept in the file so the setup wizard can flip them on without
re-authoring env/tools/hints.

| Field | Type | Default | What it does |
|---|---|---|---|
| `enabled` | bool | `true` | Master switch. `false` = skip this server at load time. |
| `description` | string | `""` | One-line description of what this server does, shown in the `operator setup` wizard's power-ups picker. Wizard-only; runtime ignores it. |
| `command` | string | required | Executable to run (e.g. `npx`, `./github-mcp-server`). |
| `args` | list of strings | `[]` | Args passed to `command`. |
| `env` | map | `{}` | Env vars for the server process. `${VAR}` is substituted from your repo-root `.env`; an empty/missing value logs a warning at startup. |
| `hints` | string | `""` | Free-form guidance about this server's tools, appended to the system prompt whenever tools from this server are available. |
| `read_tools` | list of strings | `[]` | Tool names that auto-execute without user confirmation. Anything not in this list prompts the user in chat before running. |
| `confirm_tools` | list of strings | `[]` | Overrides `read_tools` — tools named here always prompt for confirmation, even if also listed under `read_tools`. |

---

## Available agents

| Agent | What it does | Status |
|-------|--------------|--------|
| [`engineer/`](./engineer/) | Engineering assistant — looks up GitHub issues and PRs, delegates coding tasks to Claude Code, runs your existing `~/.claude/skills/` | canonical |
| [`pm/`](./pm/) | Product / standup partner — files Linear tickets from spoken commitments, drafts PRDs from discussion, posts structured standup summaries | canonical |
| [`designer/`](./designer/) | Design-review partner — pulls up Figma frames mid-meeting, critiques layout and hierarchy, edits files when asked | canonical |
| _more coming — open a PR_ |  |  |

---

## Contributing an agent

We want this folder to grow. If you've built an Operator setup for a specific
job — standup bot, incident commander, interview notes, research assistant,
customer call notes — open a PR.

Ground rules:

1. **One job per agent.** Tight scope beats broad capability. "standup bot"
   beats "general meeting helper."
2. **It must run on a fresh clone.** Someone following only your README should
   reach the aha moment in under 10 minutes.
3. **Show the artifact.** Include a demo GIF/video of the thing the agent
   produces (the Linear ticket, the GitHub comment, the doc update). The
   artifact is the point.
4. **Keep secrets out.** `.env.example` only, never real keys.
5. **Credit skill sources.** If a skill was adapted from someone else's
   public skill library, link to the source in the skill's frontmatter.

PR template: title `agents: <name> — <one-line pitch>`. Body should include
what the agent does, who it's for, and a link to the demo.
