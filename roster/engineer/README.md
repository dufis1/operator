# Engineer

Engineering assistant — looks up GitHub issues and PRs, delegates coding tasks
to Claude Code, and brings your existing `~/.claude/skills/` into meetings.

## Who it's for

Engineers who want a coding partner in their meetings. Ask it to look up a PR,
check a file, or delegate a task to Claude Code — it writes code in an isolated
worktree and reports back.

## What you need

- **Anthropic API key** — for the LLM
- **GitHub personal access token** — for repo access
- **`claude` CLI** — installed and authenticated ([install guide](https://docs.anthropic.com/en/docs/claude-code))
- **GitHub MCP server binary** — `github-mcp-server` in the repo root ([releases](https://github.com/github/github-mcp-server/releases))

## Setup

```bash
# 1. Copy the config
cp roster/engineer/config.yaml ./config.yaml

# 2. Fill in your API keys
cp roster/engineer/.env.example .env
# Edit .env with your keys

# 3. Update your display name in config.yaml
#    agent.user_display_name: "Your Name"

# 4. Run
python __main__.py https://meet.google.com/xxx-yyyy-zzz
```

## Demo

<!-- TODO: 15s GIF showing delegate_to_claude_code creating a branch mid-meeting -->
