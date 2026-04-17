Here's a precise test sheet for everything touched today. Run against a fresh meeting URL so you start with no prior JSONL.

**1. File creation + header line**
- Join `https://meet.google.com/<slug>`. Confirm `~/.operator/history/<slug>.jsonl` exists.
- `head -1 ~/.operator/history/<slug>.jsonl` → one-line JSON with `"kind": "meta"`, `"slug"`, `"meet_url"`, `"created_at"`.
- Leave and rejoin the same URL → header is NOT rewritten (still one meta line, new chat lines append below).

**2. Every message lands in the JSONL**
- From another account, send: `hi there` (non-addressed, no trigger).
- `tail -f` the file: should see `{"kind":"chat","sender":"<other>","text":"hi there"}` — even though Operator didn't reply.
- Send `@operator what's up`. Expect: incoming chat line, then Operator's reply as a `sender: "Operator"` chat line, all in the same file.

**3. First-contact greeting (config-driven)**
- First-ever message from person A with `@operator hey`: Operator's reply should greet them by first name once.
- Second message from A: Operator should NOT greet again (hint is suppressed after first use).
- A third participant B joins and sends `@operator hi`: B gets greeted by name once.
- To prove it's config-driven: edit `agent.first_contact_hint` in `roster/<bot>/config.yaml` (e.g. set it to `""`) and restart — no greeting. Set it back.

**4. History replay (the core of 11.3a)**
- In a multi-turn conversation, ask something that depends on earlier context (e.g., "what was my first question?"). Operator should answer using the JSONL tail, not ask again.
- Kill Operator mid-meeting, restart against the same URL. Ask: "what were we just discussing?" → tail is read from the existing file; Operator answers with prior context.
- This exercises both the tail read AND the "persistence absorbed into 11.3a" point.

**5. `history_messages` cap**
- `grep -c kind ~/.operator/history/<slug>.jsonl` — confirm the file grows unbounded (no mid-file trimming), but the LLM only sees the last 40 entries (default). Hard to observe externally; trust the unit test unless you want to set `history_messages: 3` and watch it forget turn 2.

**6. Tool-loop scratchpad (the "no collapse" change)**
- Ask Operator to run a Linear tool, e.g. `@operator list my open issues`. Read-only → auto-executes.
- `grep tool ~/.operator/history/<slug>.jsonl` → zero matches. Tool calls and tool_results live only in memory; the JSONL should contain only the user turn + Operator's summary reply.
- Ask a chained-tool question (e.g., `@operator list issues then open the first one`). Same check: only user + final summary make it to disk.

**7. Write-tool confirmation flow still works**
- `@operator create a linear issue titled 'demo'`. Operator should ask `I'd like to run create_issue... OK?` (non-read → confirm).
- Reply `yes` → executes and summarizes.
- Check JSONL: you should see the user's request, the confirm-Q from Operator, the user's "yes", and the final success reply — all as chat lines. No tool_use/tool_result protocol noise in the file.
- Retry with a correction: ask for an issue, then reply something other than yes (e.g. "change the title to foo"). Operator should re-propose with the new title rather than cancel.

**8. Non-addressed messages as context**
- A second participant types `the Q3 plan is locked` (no `@operator`).
- Then say `@operator recap`. Operator should reference Q3 — proves non-addressed messages are in the tail and get replayed.

**9. 1-on-1 mode still works**
- Join the meeting with only one other participant. Send messages without `@operator`. Operator should reply (participant_count ≤ 2).

**10. Alone-exit**
- Confirm others were present (count > 1), then have everyone leave. Operator should log "alone in meeting — grace timer started" and leave after 60s.

**11. Sanity on rename**
- `grep -r history_turns .` (excluding `.git`) should return nothing.
- `grep -r HISTORY_MESSAGES config*.py` should hit `config.py`.

**12. Logs to watch**
- `tail -f /tmp/operator.log | grep -E "MeetingRecord|LLM ask|LLM tool|scratch|first_contact"` — confirms the record is opening, tail is building with the right message count, and scratch gets cleared after tool loops.

If anything in #2, #3, #4, or #6 goes sideways that's the signal something in 11.3a regressed. #7 is the highest-risk interaction (confirmation flow overlaid on the new scratch model).