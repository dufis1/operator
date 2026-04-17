# Roster

Ready-to-run roster configurations for Operator. Each subfolder is a complete,
working setup — pick one, point Operator at it, join a meeting.

The roster is the "choose your fighter" layer. You can:

- **Use one as-is** — copy its `config.yaml` and run.
- **Power it up** — add your own MCP servers, drop extra skill files in, change the model.
- **Contribute one** — open a PR with a new folder. See [Contributing](#contributing) below.

Operator itself is model-agnostic (OpenAI or Anthropic) and skill-format-neutral
(any markdown file with YAML frontmatter works). Roster members in this folder lean on
the [Claude Code skill format](https://docs.claude.com/claude-code) because it's
the most widely adopted skill format in the wild — but nothing about a roster member
is Claude-specific. Swap the model in `config.yaml` and the same skills work.

---

## Using a roster member

```bash
# 1. Fill in your API keys in .env
#    (see the roster member's README for which keys it needs)

# 2. Run — name the bot you want, optionally pass a Meet URL
operator engineer https://meet.google.com/xxx-yyyy-zzz
operator engineer                                      # auto-opens meet.new
operator list                                          # show all bots
```

Every run names the bot explicitly. Config lives in `roster/<name>/config.yaml`
and is loaded at runtime — there is no ambient root `config.yaml`. Each roster
member's own `README.md` lists the keys, MCP servers, and any prerequisites it
needs.

---

## Folder layout

Every roster member is a self-contained folder:

```
roster/<name>/
  README.md          # What it does, who it's for, setup, 15s demo GIF
  config.yaml        # Complete Operator config — model, MCP servers, skills path
  skills/            # Optional. Markdown skill files bundled with this roster member.
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
`config.yaml` as a starting point and modify. The roster member should work on a
fresh clone after the user fills in their API keys — no hidden dependencies.

**Declare your MCP's read tools.** Each `mcp_servers.<name>` block takes a
`read_tools:` list of tool names that auto-execute without user confirmation.
Anything not listed confirms by default — safe-by-default for unknown tools,
but a friction wall if you forget to populate the list. Look at the canonical
roster members (`engineer/`, `pm/`) for the shape, and start your MCP
once locally to see the actual tool names in the startup log
(`MCP server '<name>' connected — N tools`). Write tools should be omitted —
they belong behind the confirmation gate.

### `skills/`

Optional. If the roster member ships its own skills, put them here. Skills are
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

Skill files can also live in `~/.claude/skills/` — the roster member's `config.yaml`
points at whichever directories it wants to load from.

---

## Available roster members

| Roster member | What it does | Status |
|---------------|--------------|--------|
| [`engineer/`](./engineer/) | Engineering assistant — looks up GitHub issues and PRs, delegates coding tasks to Claude Code, runs your existing `~/.claude/skills/` | canonical |
| [`pm/`](./pm/) | Product / standup partner — files Linear tickets from spoken commitments, drafts PRDs from discussion, posts structured standup summaries | canonical |
| [`designer/`](./designer/) | Design-review partner — pulls up Figma frames mid-meeting, critiques layout and hierarchy, edits files when asked | canonical |
| _more coming — open a PR_ |  |  |

---

## Contributing a roster member

We want this folder to grow. If you've built an Operator setup for a specific
job — standup bot, incident commander, interview notes, research assistant,
customer call notes — open a PR.

Ground rules:

1. **One job per roster member.** Tight scope beats broad capability. "standup bot"
   beats "general meeting helper."
2. **It must run on a fresh clone.** Someone following only your README should
   reach the aha moment in under 10 minutes.
3. **Show the artifact.** Include a demo GIF/video of the thing the roster member
   produces (the Linear ticket, the GitHub comment, the doc update). The
   artifact is the point.
4. **Keep secrets out.** `.env.example` only, never real keys.
5. **Credit skill sources.** If a skill was adapted from someone else's
   public skill library, link to the source in the skill's frontmatter.

PR template: title `roster: <name> — <one-line pitch>`. Body should include
what the roster member does, who it's for, and a link to the demo.
