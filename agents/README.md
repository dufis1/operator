# Agents

Ready-to-run agent configurations for Operator. Each subfolder is a complete,
working setup — pick one, point Operator at it, join a meeting.

Agents are the "choose your fighter" layer. You can:

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
# 1. Copy the agent's config to the repo root (or point OPERATOR_CONFIG at it)
cp agents/claude-code/config.yaml ./config.yaml

# 2. Fill in your API keys in .env
#    (see the agent's README for which keys it needs)

# 3. Run
python __main__.py https://meet.google.com/xxx-yyyy-zzz
```

Each agent's own `README.md` lists the keys, MCP servers, and any
prerequisites it needs.

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

Must be a complete, runnable Operator config (not a fragment). Copy the root
`config.yaml` as a starting point and modify. The agent should work on a
fresh clone after the user fills in their API keys — no hidden dependencies.

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

## Available agents

| Agent | What it does | Status |
|-------|--------------|--------|
| [`claude-code/`](./claude-code/) | Engineering assistant — files Linear tickets, looks up GitHub issues and PRs, runs your existing `~/.claude/skills/` | canonical |
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

PR template: title `agent: <name> — <one-line pitch>`. Body should include
what the agent does, who it's for, and a link to the demo.
