# Phase 15.5.1 — Engineer Bundle Live Test Plan

Essential-only. Each test walks through concrete steps and a clear pass signal.
Run them in order. Keep `tail -f /tmp/operator.log` open in a second pane.

This suite exercises what's NEW in the engineer bundle: the `delegate` MCP
server, the `delegate_to_claude_code` tool, its confirmation gate, and the
600s tool timeout. Battle-tested paths (chat polling, skills, JSONL, GitHub
MCP generally) are only smoke-checked.

## Prep

1. Edit `roster/engineer/config.yaml` → set `agent.user_display_name` to your
   Google Meet display name (the template ships with `"Your Name"`). The new
   runtime loads the bot's config directly — no root `config.yaml` to swap.

2. Verify prerequisites:

   ```bash
   which claude                # Claude Code CLI on PATH + authenticated
   ls github-mcp-server        # GH MCP binary at repo root
   grep -E "ANTHROPIC_API_KEY|GITHUB_TOKEN" .env   # both keys present
   ```

3. Pick a fresh meeting URL so the JSONL starts empty.

4. Terminal A — start Operator:

   ```bash
   ./operator engineer <meet-url>
   ```

5. Terminal B — stream logs:

   ```bash
   tail -f /tmp/operator.log
   ```

---

## T1 — Startup: both MCP servers connect

**Steps:** Watch the log during startup, before sending any chat.

**Pass:**
- Log shows `MCP server 'github' connected — N tools` (N is usually 20+).
- Log shows `MCP server 'delegate' connected — 1 tools`.
- Log shows `SKILLS: X/Y loaded` (X > 0 if `~/.claude/skills` has any).
- No `MCP USER CONFIG: … failed to start` entries.

**Fail signals:**
- Delegate fails with a Python `ImportError`: venv isn't reaching the subprocess. Ensure you launched from the repo root with venv activated — the subprocess inherits your interpreter.
- GitHub fails: binary missing at repo root, or `GITHUB_TOKEN` didn't resolve.

---

## T2 — Delegate tool is offered to the LLM

**Steps:** In chat: `@operator what tools do you have? one line`

**Pass:**
- Log shows `LLM ask … tools=M` where M ≥ 2 (github tools + 1 delegate tool, plus `load_skill` if skills enabled).
- Operator's reply mentions it can delegate coding tasks or names `delegate_to_claude_code` / Claude Code.

**Fail signals:** `tools=0`, or reply denies having tools — delegate server didn't register its tool with the LLM.

---

## T3 — Confirmation gate fires on the delegate tool

**Steps:** In chat:
`@operator use delegate_to_claude_code to count the number of Python files in the pipeline/ directory and tell me the integer`

**Pass:**
- Log shows `LLM tool_call name=delegate__delegate_to_claude_code`.
- Log shows `ChatRunner: requesting confirmation for delegate__delegate_to_claude_code`.
- Operator's chat reply begins with `I'd like to run delegate_to_claude_code via delegate with: task=…`.
- Log does NOT show `ChatRunner: auto-executing` for this turn.

**Fail signals:**
- Auto-executed without asking → `delegate_to_claude_code` leaked into `READ_TOOLS`.
- No `tool_call` line → LLM ignored the directive; tighten phrasing or retry.

---

## T4 — Happy path: approve, heartbeat, summary, footer

**Steps:** Reply `yes`. Wait up to a minute.

**Pass:**
- Log shows `ChatRunner: auto-executing delegate__delegate_to_claude_code`.
- Log shows `MCP executing tool=delegate_to_claude_code server=delegate`.
- Within ~8s, at least one `Still working on that...` appears as a chat message from Operator (heartbeat).
- Eventually log shows `MCP tool result length=N` with N ≥ 50.
- Operator's final chat reply contains the answer (e.g. an integer count).
- In the DEBUG-level log (`MCP tool result:` line), the raw tool result ends with a footer matching `[Completed in X.Xs, cost $Y.YYYY]`. The LLM will typically paraphrase the tool result into a terser chat reply and drop the footer — that's expected, not a failure.

**Fail signals:**
- No heartbeat → task finished under 8s; either pick a beefier task or temporarily drop `tool_heartbeat_seconds` to 3 and re-run.
- No duration/cost footer **in the tool result** → summary format in `delegate_to_claude_code.py` broke.
- Non-JSON branch hit (`Claude Code returned non-JSON output:`) → your `claude` CLI version doesn't honor `--output-format json`; upgrade.

---

## T5 — GitHub MCP co-exists (bundle didn't break reads)

**Steps:** In chat: `@operator what's my github login? use get_me`

**Pass:**
- Log shows `LLM tool_call name=github__get_me`.
- Log shows `ChatRunner: auto-executing github__get_me` (no confirmation — `get_me` is in `READ_TOOLS`).
- Operator's reply contains your GitHub username.

**Fail signals:** confirmation prompt appears (READ_TOOLS allowlist regressed), or GH tools aren't in the LLM's view at all.

---

## T6 — Error path: empty task is caught before spawning claude

**Steps:** In chat:
`@operator call delegate_to_claude_code with task="" — I'm testing the error path`
Reply `yes` to the confirmation.

**Pass:**
- Log shows `MCP tool result length=…` with a short value.
- The result content is `Error: task cannot be empty.` (visible in Operator's chat reply as the summary, or in the log at `MCP tool result:` debug level).
- Operator's final reply acknowledges the error and does NOT report a cost/duration footer (claude was never spawned).
- No `claude` subprocess appears in `ps aux` during this turn.

**Fail signals:** crash, hang, or cost footer present (guard was bypassed and claude actually ran with empty prompt).

---

## T7 — Shutdown is clean; no orphaned subprocesses or worktrees

**Steps:** Ctrl+C Operator. Then run:

```bash
ps aux | grep -E "claude|delegate_to_claude_code" | grep -v grep
git worktree list
```

**Pass:**
- No lingering `claude` processes from this session.
- `git worktree list` shows only your normal worktree(s); any Claude-Code-created worktrees from T4 are either already gone (Claude Code cleans up its own) OR are listed so you can remove them deliberately.
- If any leftover worktrees exist, remove them:
  ```bash
  git worktree remove <path>
  git branch -D <branch>      # if the branch also lingers
  ```

**Fail signals:** a `claude` process is still running after Ctrl+C (the subprocess survived parent kill), or worktrees accumulate without a cleanup path.

---

## Cleanup

```bash
# Revert your display name in roster/engineer/config.yaml if you don't want
# it committed.

# Optional — remove the test meeting's JSONL for a clean slate:
# rm ~/.operator/history/<slug>.jsonl
```

If T1–T5 all pass and T6/T7 degrade gracefully, the engineer bundle is
ship-ready for Phase 15.5.1. The "demo GIF" TODO in `roster/engineer/README.md`
can be recorded from a re-run of T3 → T4.
