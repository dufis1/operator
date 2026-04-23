---
name: live-bug-triage
description: Triage a live production bug — use when the user says "prod is broken", "we're seeing errors", "X is throwing", or names a Sentry issue / error ID mid-meeting.
---

# Live bug triage

When the user says prod is broken and wants to triage it in the meeting,
walk them from symptom → Sentry events → probable root cause → proposed fix.
Optionally delegate the fix to `claude-code` in a sandbox so the team can
review it before it lands.

## Before you write

You will need the Sentry MCP. If it's not enabled or not authed, say so
and ask the user to run `brainchild auth sentry` first — do not guess at
an issue from memory. If the user dropped a Sentry issue URL, extract
`organizationSlug` + `projectSlug` + `issueId` from it. If they only
named a symptom ("500s on `/checkout`"), start with `search_issues`
scoped by project + timeframe + keyword.

## Shape

Emit the triage in phases. Post each phase as its own short message so the
team can react — do not dump the whole thing at once.

```
(1) **What's happening** — one sentence on the symptom, sourced from Sentry (not from the user's description). Include event count + first-seen/last-seen + affected release.

(2) **Where it's breaking** — the top stack frame (file:line + function). Link to the Sentry issue.

(3) **Probable cause** — one or two sentences on why, grounded in the stack trace + breadcrumbs. Flag "guess" vs "confirmed".

(4) **Proposed fix** — one or two sentences on what to change + where. Name the file, not the layer.

(5) **Delegate?** — "Want me to spin up a claude-code worktree and write the patch? [y/n]" — only if the fix is localized enough to delegate.
```

## Rules

- **Ground every claim in the Sentry payload.** Before you say "this is a
  timezone bug", cite the stack frame or breadcrumb that makes you think
  so. "Guess" is a fine label; "it's probably X" without backing looks
  like hallucination.
- **One phase per message.** (1) posts, the team reacts, (2) posts. Do not
  pre-emit the whole triage — the team may redirect you after phase 1
  ("that's a known thing, skip") and save you the work.
- **Event count vs frequency.** "10 events in the last hour" and
  "10 events since 2024" have wildly different urgencies. Lead with
  the frequency, not the total.
- **If first-seen = last-seen and count is 1, say so and stop.** A
  one-off is not a live bug — redirect to normal triage.
- **Never claim a fix is verified without a test.** "This should fix it"
  is accurate; "this fixes it" requires running the repro. If the user
  wants verification, that's where claude-code delegation helps.
- **Delegation is opt-in.** Phase 5 always asks. A wrong patch merged
  under pressure is worse than no patch. Confirm repo_path before
  delegating, and pass the Sentry issue URL + top stack frame into the
  delegated task string.
- **No claude-code? Skip phase 5.** If `claude-code` is disabled on this
  agent, phase 5 becomes "here's the fix — apply it manually:" plus the
  file + diff sketch.
- **Post the Sentry issue URL once, in phase 2.** Do not repeat it every
  message. The team can click through from that one link.
