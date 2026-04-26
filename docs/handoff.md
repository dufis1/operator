# Session 168 handoff (2026-04-25) — Phase 14.12.2 live-meeting Sessions 1+2 COMPLETE (8/8 PASS) + 4 UX fixes shipped + PR #5 open

**Sessions 1 (engineer flow) and 2 (PM/sprint flow) of `docs/live-skill-tests.md` both passed end-to-end (8/8). Four UX fixes shipped during the live run (cwd, intro pre-warm, terse prompts, progress narrator) — all in commit `0c1bcc9` on branch `session-168-track-a-ux-polish`, opened as PR #5. Next session runs Session 3 (live ops / Sentry triage, ~25m incl. ~15m Sentry setup).**

## What shipped this session

Single commit `0c1bcc9`, 5 files, +233/-11 lines on branch `session-168-track-a-ux-polish` (PR #5 open at github.com/dufis1/operator/pull/5).

1. **`pipeline/providers/__init__.py:41-46` + `claude_cli.py:100-104`** — `build_provider()` passes `cwd=os.getcwd()` to `ClaudeCLIProvider`. Before: cwd defaulted to `$HOME`, so when the user said "walk us through this codebase," the bot's first action was `Bash: ls /Users/jojo` to find a repo. After: bot sees the user's invocation dir, mirroring `claude` CLI behavior.
2. **`chat_runner.py:174-181` + `285-298`** — intro pre-warm + participant-count seed. Moved `_generate_intro` thread to BEFORE `_connector.join()` (LLM call doesn't need browser state, parallelizes with the 5–10s join). Added a synchronous `get_participant_count()` at the top of `_loop()` so `saw_others=True` is set immediately if the room is non-empty, instead of waiting up to 3s for the next `PARTICIPANT_CHECK_INTERVAL` poll. Combined: join → intro-posted dropped from ~8s to ~1–2s.
3. **`permission_chat_handler.py:39-160`** — terse confirmation prompts. New `_format_terse(tool_name, args)` collapses bulk content fields per tool: `Write /tmp/foo.py (1.2 KB)`, `MultiEdit src/x.py (3 hunks)`, `WebFetch <url> — <prompt>`. Bash stays verbatim (user's safety check needs the literal command). New config knob `agent.permission_verbosity: terse | verbose` (default terse). Verbose mode regression-tested live.
4. **`claude_cli.py:115-121,491-499,775-792` + `chat_runner.py:139-144,180-211,294,890`** — progress narrator. `ClaudeCLIProvider.set_progress_callback(callback)` late-binds; streaming pump scans `assistant` event content for `tool_use` blocks and fires the callback. `ChatRunner._on_tool_use` only narrates auto-approved tools, throttled by `min_silence_seconds=4` AND `throttle_seconds=5`. Posts as `"Working: Read /tmp/foo.py; Grep 'def main' in src/"`. Config block `agent.progress_narration: { enabled, min_silence_seconds, throttle_seconds }`.

## Live test results

| Session | Tests | Result |
|---|---|---|
| 1 (engineer) | 1.1 walkthrough, 1.2 migration plan, 1.3 test gen, 1.4 live `--version` edit | 4/4 PASS |
| 2 (PM/sprint) | 2.1 Linear scope (fixture ENG-167), 2.2 PRD from caption, 2.3 release notes via gh, 2.4 PR review | 4/4 PASS |

Cumulative session-168: **8/8 PASS**.

## Side-features installed / created

- **`gh` CLI installed via brew + `gh auth login`** as `dufis1`. Test 2.4 first run used WebFetch (function-level cites only); after install, re-run got line-level `file:line` cites via `gh pr diff 5`. **Add `gh` as an explicit prereq in the 14.12.5 README rewrite.**
- **Linear ticket ENG-167** created via Linear MCP as the Test 2.1 fixture: "Add `brainchild list-skills` subcommand to print enabled skills for the current bot," in the Engineering team.

## Three new Hard-Won Knowledge entries (full text in `docs/agent-context.md`)

1. When wrapping a CLI that uses `cwd`, mirror it — don't pin to `$HOME` for "predictability" (you invisibly strip context the inner tool needs).
2. Intro latency was ordering, not the LLM call — parallelize independent slow paths and pre-seed gates from prior knowledge before the polling loop starts.
3. Confirmation prompts that dump bulk content invert the safety check (users skim past the wall and approve). Hide the *payload*, keep the *imperative* — Bash commands stay verbatim.

## Exact next step

**Session 169: Session 3 of `docs/live-skill-tests.md`** (live ops / Sentry bug triage, ~25m total).

- ~15m setup: Sentry account at sentry.io with `shapirojojo@gmail.com` → create Python project `brainchild-track-a-test` → copy DSN → `mkdir /tmp/sentry_demo && cd /tmp/sentry_demo && python -m venv venv && source venv/bin/activate && pip install sentry-sdk` → drop in the `trigger_error.py` fixture from `docs/live-skill-tests.md` Test 3.0 → `export SENTRY_DSN=...` → `python trigger_error.py` → wait 30s → copy issue URL from `https://<your-org>.sentry.io/issues/`.
- Verify Sentry MCP is in claude's setup: `claude mcp list | grep -i sentry`. If missing, add via Claude Code's mcp commands or the claude.ai connectors UI.
- Then run Test 3.1: post `prod is broken — what's going on with this Sentry issue: <sentry-issue-url>` in Meet chat. Pass signal: 5 separate phase messages (symptom → location with `file:line` → cause → fix → "want me to write the patch?").

After Session 3 lands, **14.12.3** (track-B repositioning), **14.12.4** (skills migration to `~/.claude/skills/`), and **14.12.5** (README rewrite — adds `gh` as a prereq) remain before MVP can ship.

## Open carry-overs (running list, latest at top)

- **(NEW)** `permission_chat_handler.py:122` operator-precedence — `return f"{tool_name}: " + ", ".join(parts) if parts else tool_name` parses as `(...) if parts else tool_name`. Empty-args branch correctly returns just `tool_name` without colon, but it's "correct by accident." Wrap the truthy branch in parens. Surfaced by Claude's own structured PR review of #5.
- **(NEW)** No tests for any session-168 code (`_format_terse`, `_format_confirmation` verbosity switch, `ChatRunner._on_tool_use` throttle/silence gating). Verified `grep _format_terse|set_progress_callback|_on_tool_use|PROGRESS_NARRATION|PERMISSION_VERBOSITY tests/` returns 0 hits.
- **(NEW)** `_on_tool_use` calls `self._send` outside the narration lock from the provider pump thread — Playwright `send_chat` is now called from main loop + intro thread + pump thread. Verify thread-safety or serialize.
- **(NEW)** `_narration_auto_approve` captured at wire time (`chat_runner.py:173`) — won't pick up live config reloads. Intentional? Confirm before 14.12.5 ships.
- **(NEW)** Magic numbers `min_silence=4s` / `throttle=5s` are guesses, not empirically tuned. Worth a note in the config docstring or a session of A/B tuning.
- **(NEW)** `Task` (sub-agent) calls aren't currently narrated — could run for minutes silently when in auto-approve mode.
- **(NEW)** PR #5 contains 41 commits / 109 files / +10.5k lines (sessions 152–168) because local `main` is 40 commits ahead of `origin/main`. When ready to land: either push local main first (`git push origin main`) so future PRs diff cleanly, or split by phase. Bot's own review caught and recommended both options.
- **(NEW)** New log lines (`ChatRunner: progress narrator wired`, `ChatRunner: seed participant_count=...`) need to flow into `debug/model-log.md` whenever that file gets created during 14.12 finalization.
- **(carried)** Synthesized opener still doesn't replay tool calls or tool results — only `(user_text, assistant_text)` pairs in `_turn_history`. Acceptable for honest prior text; would inherit corruption if any. Real fix is recording `tool_use` + `tool_result` events on the wire and replaying them in the opener — invasive, worth its own phase.
- **(carried)** Session-160 Chrome-runtime preflight is dead weight under the bundled-Chromium pivot — fold into 14.12 cleanup pass.
- **(carried)** GitHub `operator → brainchild` repo rename still deferred (current remote: `dufis1/operator`).
- **(carried)** Stuck-LLM retry semantics, `chat_runner._send` dead-browser detection, Linux adapter ID-based dedup parity.
