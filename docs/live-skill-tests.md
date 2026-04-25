# Live skill tests — claude agent

*Phase 15.10.3 follow-up, session 159. Live-meeting test script for the nine finalized bundled skills, batched into three meeting sessions to minimize Meet restarts. Each test has the exact trigger phrase to type, the pass signal, and the log query I'll run when you say "done."*

## Skills under test

| Session | Skill | MCP(s) | What we're checking |
|---|---|---|---|
| 1 | `codebase-walkthrough` | `claude-code` | bot walks a real subsystem with `file:line` citations |
| 1 | `migration-plan` | `claude-code` | Plan-Mode read-only output, 3–7 phases, no code |
| 1 | `test-generation` | `claude-code` | reads sibling tests first, returns worktree branch |
| 1 | live coding delegation | `claude-code` | `delegate_to_claude_code` returns a worktree branch |
| 2 | `scope-estimate` | `linear` (+`claude-code`) | T-shirt size from a Linear ticket |
| 2 | `prd-from-discussion` | none (captions) | structured PRD from caption transcript |
| 2 | `release-notes` | `github` | grouped changelog from a commit range |
| 2 | `pr-review` | `github` | bug + risk callouts citing `file:line` |
| 3 | `live-bug-triage` | `sentry` (+`claude-code`) | five-phase triage from a real Sentry issue |

Skipped this round: `design-handoff-spec` (no Figma); `design-review-feedback`, `schedule-followup`, `standup-summary` (not in the top-10 use cases — separate test pass if needed).

---

## One-time pre-flight (do before session 1)

### A. Verify the build is fresh

```bash
cd /Users/jojo/Desktop/operator
source venv/bin/activate
brainchild build      # confirm the wizard runs cleanly
```

If you already have `~/.brainchild/agents/claude/config.yaml`, just verify your enabled skills include the four new/relevant ones via `brainchild edit claude`:

```yaml
skills:
  enabled:
    - codebase-walkthrough
    - migration-plan
    - test-generation
    - pr-review
    - prd-from-discussion
    - release-notes
    - scope-estimate
    - live-bug-triage
  external_paths:
    - ~/.claude/skills    # your Claude Code skills, additive
```

Per session-153 design, bundled brainchild skills are unchecked by default for the claude preset — you have to flip them on. The eight above are needed for this test pass.

### B. Verify MCPs are enabled + authed in `claude` agent config

`brainchild edit claude` and confirm:

```yaml
mcp_servers:
  claude-code:
    enabled: true
  github:
    enabled: true
  linear:
    enabled: true
  sentry:
    enabled: true
```

If any are off, flip them. If any haven't been authed:
- GitHub: `GITHUB_TOKEN` in `~/.brainchild/.env`
- Linear: `brainchild auth linear` (OAuth)
- Sentry: deferred to session 3 prep (full ceremony below)

### C. Check the log file is wired

```bash
ls -la /tmp/brainchild.log
tail -3 /tmp/brainchild.log
```

You should see recent entries from your last `brainchild run`. If the file is missing, the next live run will create it.

### D. Captions

Captions are on by default for the claude agent (per session 155). When you join the test Meet, **click the CC button in the Meet UI** to actually start streaming captions — `prd-from-discussion` only fires if there's a real transcript in the meeting record.

---

## Session 1 — Engineer flow (~15–20 min)

**Setup:** open a fresh Meet, run `brainchild run claude` against it. Sit alone in the meeting; 1-on-1 mode auto-engages so you don't need to say "@claude" every time.

### Test 1.1 — `codebase-walkthrough`

**Trigger phrase (paste verbatim into Meet chat):**

> walk us through how the chat polling loop works in this codebase

**Expected behavior:**
- bot delegates to `claude-code` (you'll see a confirmation prompt — approve)
- returns a sequence of short messages, one per hop, each citing real `file:line` from `pipeline/chat_runner.py`
- entry point + 2–4 hops + data + external calls + "questions?" close
- ends with an offer to drill deeper

**Pass signal:** every cited `file:line` actually exists in the repo (you can spot-check 2–3 of them); the trace lands on `pipeline/chat_runner.py:_loop` as the polling heart; replies posted as multiple short messages, not one wall.

**Say "done" when:** the bot has posted the "questions?" close OR you've confirmed it's stuck/failed.

**What I'll grep:**
```
grep "SKILLS.*codebase-walkthrough\|delegate_to_claude_code\|LLM reply\|ChatRunner: new message" /tmp/brainchild.log | tail -40
```
Plus the JSONL meeting record for the full bot replies.

---

### Test 1.2 — `migration-plan`

**Trigger phrase:**

> what would it take to migrate this codebase from chat-only to also supporting Slack as a connector? give us a phased plan

**Expected behavior:**
- bot delegates to `claude-code` (Plan Mode shape — no edits)
- returns a structured plan: Migration / Surface area / Phases (3–7) / Risks / Sequencing recommendation / Open questions
- per phase: What / Files / Blast radius / Depends on / Rough size
- **no code in the output** — this is the killer rule

**Pass signal:** at least 3 phases, each naming actual files in the repo (`connectors/base.py`, `connectors/macos_adapter.py`, etc.); breaking-change blast radius declared per phase; no python snippets in the reply.

**Say "done" when:** the plan is fully posted (may span 2–3 messages).

**What I'll grep:** same as 1.1 plus a check that no message in the JSONL contains code-block fences (` ``` `) that look like Python.

---

### Test 1.3 — `test-generation`

**Pre-step (do this NOW before triggering):** create a fixture function. Run this in your terminal:

```bash
mkdir -p /tmp/test_fixtures
cat > /tmp/test_fixtures/email_validator.py <<'EOF'
"""Tiny fixture for live test-generation skill — session 159."""


def is_valid_email(addr: str) -> bool:
    """Return True iff `addr` looks like a syntactically valid email.

    Rules:
      - exactly one '@'
      - non-empty local part (before '@')
      - non-empty domain part (after '@')
      - domain contains at least one '.'
      - no whitespace anywhere

    Does NOT validate against a real SMTP server. Pure string check.
    """
    if not isinstance(addr, str) or not addr or any(c.isspace() for c in addr):
        return False
    if addr.count("@") != 1:
        return False
    local, _, domain = addr.partition("@")
    if not local or not domain:
        return False
    return "." in domain
EOF
```

**Trigger phrase:**

> write tests for the is_valid_email function in /tmp/test_fixtures/email_validator.py

**Expected behavior:**
- bot delegates to `claude-code` against `/tmp/test_fixtures/`
- returns a chat reply: branch name + test file path + cases list + style match + status
- the worktree branch contains a real `test_email_validator.py` with happy path + 2–3 edge cases (empty string, multiple @s, no dot, whitespace, non-string)
- tests are **verified passing** before the worktree returns

**Pass signal:** chat reply names a worktree branch + at least 4 test cases; you can `cd` into the worktree and run `python -m pytest <test-file>` and see all green.

**Say "done" when:** the bot posts the summary chat reply AND you've checked the branch.

**What I'll grep:**
```
grep "SKILLS.*test-generation\|delegate_to_claude_code\|LLM tool_call" /tmp/brainchild.log | tail -30
```

---

### Test 1.4 — Live coding delegation (no skill, raw `delegate_to_claude_code`)

**Trigger phrase:**

> @claude can you add a `--version` flag to the brainchild CLI that prints the package version? Repo path is /Users/jojo/Desktop/operator

**Expected behavior:**
- bot calls `delegate_to_claude_code({task: ..., repo_path: "/Users/jojo/Desktop/operator"})`
- waits 3–10 min (heartbeat messages every ~16–60s — exponential backoff per session 157)
- returns: branch name + diff summary + offer to land

**Pass signal:** worktree branch exists (`git worktree list` from `operator/`); the diff touches `src/brainchild/__main__.py` (new `--version` arg in dispatch + import of `__version__` or pulled from `importlib.metadata`); flag works when run from the worktree (`python -m brainchild --version` prints something).

**Say "done" when:** bot posts the result OR it errors/times out (the 600s timeout per `config.DEFAULT_TOOL_TIMEOUTS` for claude-code).

**What I'll grep:**
```
grep "delegate_to_claude_code\|TIMING\|tool_call name=delegate\|tool result" /tmp/brainchild.log | tail -50
```

You can leave the meeting after this test — the worktree persists.

---

## Session 2 — PM/sprint flow (~15 min)

**Setup:** fresh Meet, `brainchild run claude`, captions ON (CC button).

### Test 2.0 — Pre-step: create a fixture Linear ticket

In your Linear workspace, create a new issue with this exact content (you'll point the bot at it in test 2.1):

- **Title:** Add `brainchild list-skills` subcommand to print enabled skills for the current bot
- **Description:**
  > Today users have to `brainchild edit <bot>` and read the YAML to know which skills are wired in. A `brainchild list-skills <bot>` subcommand should print the enabled skill names + which external paths are mounted, so users can confirm without opening the file.
  >
  > Acceptance: `brainchild list-skills claude` prints (1) bundled-library skills currently `enabled: true`, (2) `external_paths` with skill counts per path, (3) any locked-by-MCP entries from session-159 work.
- Don't actually implement it. The ticket just needs to exist.

Copy the ticket URL (e.g. `https://linear.app/<workspace>/issue/ENG-42/...`).

### Test 2.1 — `scope-estimate`

**Trigger phrase (replace `<linear-url>` with the URL you just copied):**

> scope this ticket: <linear-url>

**Expected behavior:**
- bot calls `linear.get_issue` to fetch the ticket
- optionally delegates to `claude-code` to peek at affected files
- replies with: Size (XS/S/M/L/XL) + Files to touch + Blockers + Risks + Unknowns
- no code in the reply

**Pass signal:** size assigned (S or M is realistic for this scope); "Files to touch" names `src/brainchild/__main__.py` + `src/brainchild/pipeline/skills.py` or `config.py` (real files); at least one risk or unknown surfaced.

**Say "done" when:** estimate is posted.

**What I'll grep:**
```
grep "SKILLS.*scope-estimate\|linear.*get_issue\|LLM tool_call" /tmp/brainchild.log | tail -25
```

---

### Test 2.2 — `prd-from-discussion`

**Pre-step:** captions still streaming (CC on). Speak out loud (microphone on) for ~90 seconds about the following imaginary feature — speak naturally, with pauses, like you're in a real product discussion:

> "OK so we want to add a stealth mode to brainchild. The idea is when stealth is on, the bot doesn't post an intro message when it joins, it doesn't post the failure banner, and it doesn't show its name in the participant list. The use case is sales calls where the user doesn't want the prospect to know an AI is in the room. Open question: should stealth also disable captions ingestion to be safe? And should we let users turn it on per-meeting via a CLI flag, or per-bot in config? I'm leaning per-meeting — `brainchild run claude --stealth <url>` — so it's an explicit decision each time. Goal would be ship a v1 of this within two weeks."

You can paraphrase, but cover: stealth concept, the three behaviors it disables, the use case, the open question, the goal.

**Trigger phrase (after speaking):**

> turn the last 90 seconds of discussion into a PRD

**Expected behavior:**
- bot reads the meeting record JSONL (which has caption entries from your speech)
- emits structured PRD: Problem / User / Goal / Scope / Non-goals / Open questions / Owner
- quotes specific phrasings from your speech ("ship a v1 within two weeks", "per-meeting via a CLI flag", etc.)
- writes "Owner — not stated in discussion" if you didn't name one

**Pass signal:** PRD has at least 5 of the 7 sections; quotes are real phrases from your speech (not invented); open question about captions-ingestion-when-stealth is captured.

**Say "done" when:** PRD is posted.

**What I'll grep:**
```
grep "SKILLS.*prd-from-discussion\|MeetingRecord\|caption" /tmp/brainchild.log | tail -20
```
Plus the JSONL — I'll check that the bot's PRD reply has section headers and isn't generic-template-shaped.

---

### Test 2.3 — `release-notes`

**Pre-step:** none — uses the real repo's commit history.

**Trigger phrase:**

> give us release notes for the last 10 commits on main, repo is github.com/shapirojojo/operator

**Expected behavior:**
- bot calls `github.list_commits` + `github.list_pull_requests` for `shapirojojo/operator`
- groups output: Features / Fixes / Changes / Internal
- each line cites `(#<PR-number>)` or just the commit SHA short prefix if the commit isn't tied to a PR
- breaking-change callout pinned at top if any (probably none in recent commits, that's fine — say "no breaking changes")

**Pass signal:** at least 4–5 commits grouped by intent; PR numbers visible if they exist; recent session-158/159 work shows up (path relocation, Google sign-in, build rename, claude use cases doc).

**Say "done" when:** notes are posted.

**What I'll grep:**
```
grep "SKILLS.*release-notes\|list_commits\|list_pull_requests\|LLM tool_call" /tmp/brainchild.log | tail -25
```

---

### Test 2.4 — `pr-review`

**Pre-step:** find a recent merged PR in `shapirojojo/operator` to point at. If you don't have one with substantive code changes, open a small one now (any small commit on a branch + `gh pr create`). For this test it doesn't matter if the PR is open or merged — the skill works on either. Copy the URL.

**Trigger phrase (replace `<pr-url>`):**

> review this PR: <pr-url>

**Expected behavior:**
- bot calls `github.pull_request_read` + `github.get_file_contents` for the changed files
- replies with: Summary + Bugs + Missing tests + Risks + Style + Questions sections
- every Bugs/Risks claim cites `file:line`
- doesn't approve or request changes (review-only)

**Pass signal:** every cited `file:line` exists in the diff; bugs section is either empty (with explicit "no bugs" if so) OR contains real concerns; nits skipped.

**Say "done" when:** review is posted.

**What I'll grep:**
```
grep "SKILLS.*pr-review\|pull_request_read\|get_file_contents" /tmp/brainchild.log | tail -25
```

You can leave the meeting after this test.

---

## Session 3 — Live ops (`live-bug-triage`, ~25 min including setup)

This one has the highest setup cost. ~15 min of prep before the meeting starts.

### Test 3.0 — Pre-step: Sentry account + project + DSN + fixture trigger

**Step 1 — Create a free Sentry account:** Go to [sentry.io](https://sentry.io), sign up with `shapirojojo@gmail.com` (free tier is plenty). Create a personal organization when prompted.

**Step 2 — Create a project:**
- Project type: Python (standard, not Django/Flask/FastAPI)
- Project name: `brainchild-skill-test`
- Alert frequency: default
- After creation, Sentry shows the DSN — copy it. Looks like `https://abc123@o456789.ingest.us.sentry.io/123`.

**Step 3 — Build the fixture trigger script:**

```bash
mkdir -p /tmp/sentry_demo
cd /tmp/sentry_demo
python -m venv venv
source venv/bin/activate
pip install sentry-sdk

cat > trigger_error.py <<'EOF'
"""Tiny fixture to populate Sentry with a real error — session 159 live test."""
import sentry_sdk
import os

DSN = os.environ.get("SENTRY_DSN")
if not DSN:
    raise SystemExit("set SENTRY_DSN env var first")

sentry_sdk.init(dsn=DSN, traces_sample_rate=0)


def parse_user_age(payload: dict) -> int:
    """Look up nested age. Buggy — assumes profile always present."""
    return int(payload["profile"]["age"])


def main():
    # First call: succeeds, baseline event.
    parse_user_age({"profile": {"age": "29"}})

    # Second call: KeyError — payload missing "profile".
    bad_payloads = [
        {"profile": {"age": "31"}},
        {"name": "alice"},               # missing profile → KeyError
        {"profile": {"name": "bob"}},    # profile present, missing age → KeyError
        {"profile": {"age": "thirty"}},  # ValueError on int()
    ]
    for p in bad_payloads:
        try:
            parse_user_age(p)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            print(f"captured: {type(e).__name__}: {e}")

    sentry_sdk.flush(timeout=5)
    print("done — check Sentry in ~30 sec")


if __name__ == "__main__":
    main()
EOF
```

**Step 4 — Run the trigger:**

```bash
export SENTRY_DSN="<paste-the-dsn-from-step-2>"
python trigger_error.py
```

Should print three "captured: ..." lines.

**Step 5 — Wait 30 sec, then check Sentry's web UI.** You should see one or two issues at `https://<your-org>.sentry.io/issues/`. Click into one and copy its URL — it'll look like `https://<your-org>.sentry.io/issues/<id>/?project=<project-id>`.

**Step 6 — Auth the Sentry MCP in Brainchild:**

```bash
brainchild auth sentry
```

This runs the OAuth flow via `mcp-remote`. Approve in the browser. The MCP cache lands at `~/.mcp-auth/`.

**Step 7 — Verify Sentry MCP reachability:** open a terminal and run:

```bash
brainchild run claude    # opens a fresh Meet
```

Wait for the failure-banner check. If Sentry shows up as "ok" in the readiness output, you're set.

### Test 3.1 — `live-bug-triage`

**Setup:** fresh Meet (the one you just opened in step 7 is fine), bot joined, you alone in 1-on-1 mode.

**Trigger phrase (replace `<sentry-issue-url>` with the URL you copied):**

> prod is broken — what's going on with this Sentry issue: <sentry-issue-url>

**Expected behavior:**
- bot calls `sentry.get_issue` (or equivalent — varies by Sentry MCP version)
- emits five-phase triage as **separate messages**, one per phase:
  1. What's happening — symptom + event count + first/last seen + release
  2. Where it's breaking — top stack frame at `file:line` + Sentry issue URL
  3. Probable cause — one or two sentences, flagged "guess" vs "confirmed"
  4. Proposed fix — what to change + where (name the file)
  5. Delegate? — "Want me to spin up a claude-code worktree and write the patch? [y/n]"

**Pass signal:** five separate messages (not one combined wall); phase 2 cites `trigger_error.py:parse_user_age` (or your specific line); phase 3 doesn't claim certainty without evidence (e.g. doesn't say "this is definitely X" without a stack-frame citation).

**Say "done" when:** all five phases are posted (or the bot stops short and you note where).

**What I'll grep:**
```
grep "SKILLS.*live-bug-triage\|sentry\|LLM tool_call\|LLM reply" /tmp/brainchild.log | tail -40
```

If you optionally want to test phase 5 delegation, reply `y` after the bot asks — it'll spawn a `claude-code` delegation against the fixture repo. Don't go that far unless you're feeling generous; the skill check is the five-phase shape.

---

## Per-test cadence (how we actually run this)

For each test:

1. I post the test number ("kicking off test 1.1 — codebase-walkthrough").
2. You open the Meet (or use the open one), do any pre-step the test calls for, type the trigger phrase verbatim into Meet chat.
3. You watch the bot's reply.
4. When the reply is complete (or has errored), you tell me **"done"**.
5. I run the per-test grep against `/tmp/brainchild.log` + read the relevant slice of `~/.brainchild/history/<slug>.jsonl`.
6. I post a summary: did the skill fire, did the right MCPs get called, did the reply match the pass signal, any errors, any latency anomalies.
7. We move to the next test.

If a test fails, I'll flag it in-line and we decide whether to retry, skip, or stop the session to debug.

## What I'll be looking for in the logs

- **Skill fired:** `SKILLS turn=N slash-invoke: <skill-name>` — the canonical log line for skill invocation
- **MCP tool calls:** `LLM tool_call name=<server>__<tool>` — confirms the right MCP was reached
- **Reply latency:** `TIMING` markers — flag anything over 30s without a heartbeat
- **Errors:** `LLM API call failed`, `LLM context length exceeded`, `MCP.*timed out`, `MCPToolError`
- **Bot's full reply:** the JSONL meeting record entry with `kind: chat` and `sender: <agent-name>`

## Backup logs to grep manually

If you want to peek yourself between tests:

```bash
# Live tail of the brainchild log
tail -f /tmp/brainchild.log

# Bot's own replies in this meeting (replace <slug>):
ls -t ~/.brainchild/history/ | head -3   # find the latest meeting slug
tail -f ~/.brainchild/history/<slug>.jsonl | jq 'select(.kind == "chat" and .sender == "claude")'
```
