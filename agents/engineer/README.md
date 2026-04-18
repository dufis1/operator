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
# 1. Fill in your API keys
cp agents/engineer/.env.example .env
# Edit .env with your keys

# 2. Update your display name in agents/engineer/config.yaml
#    agent.user_display_name: "Your Name"

# 3. Run — direct URL or auto-open meet.new
operator engineer https://meet.google.com/xxx-yyyy-zzz
operator engineer
```

## Worktree cleanup

Every time Engineer delegates to Claude Code, the CLI creates an isolated git
worktree under `.claude/worktrees/<name>/` and a matching `worktree-<name>`
branch. **Claude Code does not auto-remove these** once the delegation
finishes, so they accumulate across meetings.

Prune them periodically:

```bash
# List active worktrees
git worktree list

# Remove a specific one (and its branch)
git worktree remove .claude/worktrees/<name>
git branch -D worktree-<name>

# Or: bulk-prune worktrees whose directories have been deleted
git worktree prune
```

## Demo

<!-- TODO: 15s GIF showing delegate_to_claude_code creating a branch mid-meeting -->

