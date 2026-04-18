# Phase 15.5.2 — PM Bundle Live Test Plan

Essential-only. Each test walks through concrete steps and a clear pass signal.
Run them in order. Keep `tail -f /tmp/operator.log` open in a second pane.

This suite exercises what's NEW in the PM bundle: **Linear MCP** (first-run
OAuth + read + write-with-confirmation), **`captions_enabled: true`** by
default, the **two bundled skills** (`prd-from-discussion`, `standup-summary`)
loaded from `agents/pm/skills/` via `progressive_disclosure`, and the bumped
`max_tokens: 400`. Battle-tested paths (chat polling, JSONL, GitHub MCP,
Ctrl+C shutdown) are only smoke-checked.

## Prep

1. Verify prerequisites:

   ```bash
   ls github-mcp-server                           # GH MCP binary at repo root
   grep -E "ANTHROPIC_API_KEY|GITHUB_TOKEN" .env  # both keys present
   which npx                                      # Linear MCP uses npx + mcp-remote
   ```

2. **Turn captions ON in Google Meet before joining** (CC button in the Meet
   toolbar). Bundle's config expects captions; without them, T2/T6 will fail.

3. Pick the fresh meeting URL: `https://meet.google.com/tfb-tpnb-kpw`.

5. Terminal A — start Operator:

   ```bash
   operator pm https://meet.google.com/tfb-tpnb-kpw
   ```

6. Terminal B — stream logs:

   ```bash
   tail -f /tmp/operator.log
   ```

---

## T1 — Startup: Linear + GitHub + bundled skills load

**Steps:** Watch the log during startup, before sending any chat. If this is
the first time Linear MCP has run on this machine, `mcp-remote` will open a
browser window for OAuth — complete the auth; the subprocess will wait.

**Pass:**
- Log shows `MCP server 'linear' connected — N tools` (Linear typically exposes ~30 tools).
- Log shows `MCP server 'github' connected — N tools` (usually 40+).
- Log shows `SKILLS: X/Y loaded (…)` and the names include `prd-from-discussion ✓` **and** `standup-summary ✓`.
- No `MCP USER CONFIG: … failed to start` entries.

**Fail signals:**
- Linear hangs on OAuth → complete auth in the browser; re-run if the
  subprocess timed out.
- `SKILLS: path not found or not a directory: agents/pm/skills` → running
  from the wrong cwd; start Operator from the repo root.
- Neither bundled skill appears in the SKILLS banner → the `skills.paths`
  order broke; re-check `agents/pm/config.yaml`.

---

## T2 — Captions stream into chat history as `[spoken]`

**Steps:** Speak clearly in Meet for ~10s without saying `@operator`. E.g.
"Hey team, quick note — onboarding flow is still blocked on the Figma review."

**Pass:**
- Log shows an `LLM ask` turn (only when you later trigger Operator) whose
  replayed history includes a message with `[spoken] <name>: …` content.
- Operator does NOT reply to the spoken-only utterance (not addressed).
- If you `grep "\[spoken\]" /tmp/operator.log` after the test, you should see
  your phrase attributed to your display name.

**Fail signals:**
- No `[spoken]` entries anywhere → Meet captions toggle is off, or the
  caption scraper didn't attach (check browser console or connector logs).
- Operator replies to the spoken line → you're alone in the meeting (1-on-1
  mode skips the trigger phrase). Invite a second participant or expect this.

---

## T3 — Linear read: `list_issues` auto-executes

**Steps:** In chat: `@operator list my 3 most recent Linear issues, one line each`

**Pass:**
- Log shows `LLM tool_call name=linear__list_issues`.
- Log shows `ChatRunner: auto-executing linear__list_issues` (no
  confirmation — `list_issues` is in `READ_TOOLS`).
- Operator's chat reply contains at least one issue title with a bare Linear
  URL (not markdown-linked).

**Fail signals:**
- Confirmation prompt appears → `READ_TOOLS` regressed or a `confirm_tools`
  entry was misconfigured.
- No `tool_call` line → LLM didn't see Linear's tools; re-check T1's
  connected tool count.

---

## T4 — Linear write: `save_issue` hits the confirmation gate

**Steps:** In chat:
`@operator file a Linear issue titled "Operator smoke test 15.5.2" with description "Test ticket — safe to delete"`

**Pass (confirmation turn):**
- Log shows `LLM tool_call name=linear__save_issue`.
- Log shows `ChatRunner: requesting confirmation for linear__save_issue`.
- Operator's reply begins with `I'd like to run save_issue via linear with: …`
  and includes the title and description you requested.
- Log does NOT show `auto-executing` for this turn.

Then reply `yes`.

**Pass (execution turn):**
- Log shows `ChatRunner: auto-executing linear__save_issue`.
- Log shows `MCP executing tool=save_issue server=linear` followed by
  `MCP tool result length=N` with N > 50.
- Operator's final chat reply contains the new ticket's Linear URL (bare).

**Fail signals:**
- Auto-executed on the first turn → `save_issue` leaked into `READ_TOOLS`
  (write tool regression — serious).
- `yes` didn't trigger execution → confirmation state machine broke.
- Final reply omits the URL → the LLM dropped the artifact link; check
  `MCP tool result:` debug entry to confirm the URL was in the raw result.

**Cleanup:** delete the test ticket from Linear's UI after the run.

---

## T5 — Bundled skill: `standup-summary`

**Steps:** Speak a few one-line "updates" in Meet first (~15s total), e.g.
"Alex finished the login API yesterday. Blocker for Sam is waiting on design.
We decided to ship the opt-in banner on Thursday." Then in chat:
`@operator wrap it up — give me a standup summary`

**Pass:**
- Log shows `LLM tool_call name=load_skill arguments={"name": "standup-summary"}`
  (progressive disclosure is on, so the LLM must call `load_skill` to pull
  the body).
- Log shows `SKILLS: loading skill standup-summary` (or equivalent load line).
- Operator's reply uses the bundled skill's shape: bold section headers
  (`**Decisions**`, `**Action items**`, `**Blockers**`, `**Open questions**`),
  with empty sections omitted.
- Owners attributed by name where spoken; action item content reflects the
  spoken updates (not generic filler).

**Fail signals:**
- No `load_skill` tool call → LLM ignored the skill menu. Check that
  `standup-summary` appeared in T1's SKILLS banner and that its description
  is discoverable (re-check `skills.paths` ordering).
- Free-form prose without the structured headers → skill loaded but LLM
  paraphrased through it; try tightening the ask ("use the standup-summary
  skill").
- Reply truncated mid-section → `max_tokens: 400` too low for this meeting
  size; bump to 600 and re-run.

---

## T6 — Bundled skill: `prd-from-discussion`

**Steps:** Speak ~20s of "discussion" in Meet: state a problem, name a user
type, name a goal. E.g. "New users can't find the skills marketplace.
We want PMs especially to land there on first login. Goal is to get 50% of
new PMs to view it in their first session." Then in chat:
`@operator draft a PRD from what we just discussed`

**Pass:**
- Log shows `LLM tool_call name=load_skill arguments={"name": "prd-from-discussion"}`.
- Operator's reply follows the skill's shape:
  `**Problem** — …`, `**User** — …`, `**Goal** — …`, and so on.
- Sections with nothing from the discussion are either omitted OR labeled
  `not stated in discussion` (per the skill's own rule).
- `max_tokens: 400` holds — reply is not cut off mid-word.

**Fail signals:**
- All sections read `not stated in discussion` → captions didn't land in
  LLM context; re-verify T2 first.
- No skill headers → see T5 fail signals.
- Reply truncates → bump `max_tokens` and re-run.

---

## T7 — GitHub MCP co-exists (bundle didn't break reads)

**Steps:** In chat: `@operator what's my github login? use get_me`

**Pass:**
- Log shows `LLM tool_call name=github__get_me`.
- Log shows `ChatRunner: auto-executing github__get_me` (no confirmation).
- Operator's reply contains your GitHub username.

**Fail signals:**
- Confirmation prompt → `READ_TOOLS` allowlist regressed.
- No tool_call → GH tools missing from the LLM's view; re-check T1.

---

## Cleanup

```bash
# Revert your display name in agents/pm/config.yaml if you don't want it committed.
# Optional — delete the T4 smoke-test ticket from Linear's UI.
# Optional — wipe the test meeting's JSONL:
# rm ~/.operator/history/tfb-tpnb-kpw.jsonl
```

If T1–T7 all pass, the PM bundle is ship-ready for Phase 15.5.2. The
"demo GIF" TODO in `agents/pm/README.md` can be recorded from a re-run of
T3 → T4 (Linear ticket materializing mid-chat — the hero framing).
