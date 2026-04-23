# Deferred live-smoke tests

Running log of behaviors that unit tests can't cover because they require a
real browser join, a real MCP server, or a real LLM provider roundtrip.
Work through this list during **Phase 14.8 clean-mac smoke** and **15.1
Linux smoke**, or whenever doing an end-to-end meeting run for any reason.

**Append, don't replace.** Each entry: phase/session that introduced it,
what to verify, what to input, and the pass criterion. Mark ✅ with the
verifying session once the test has been run successfully and the result
matches — a failure gets a 🔄 and a note of what actually happened.

---

## Open

### Phase 15.7.2 — MCP startup-failure banner (session 148)

- **Setup:** Run a bot with a deliberately-broken MCP. Easiest today:
  unset `FIGMA_TOKEN` before launching `brainchild designer`, or unset
  `GITHUB_TOKEN` before `brainchild engineer`.
- **Verify:** One compact banner posts in chat on join, *before* the
  bot's LLM-generated intro: `Heads-up — figma didn't load (missing
  FIGMA_TOKEN). Ask for details.`
- **Pass criteria:**
  - Exactly one banner, no duplicates as the chat loop iterates.
  - When the user then asks "why did figma fail?", the LLM's reply
    references the specific kind + fix from the system prompt block
    (not a generic "check the logs").
  - When all MCPs load clean, no banner posts.
- **Why deferred:** Requires a live Meet join; can't be exercised
  without the Chrome profile + LLM provider.

## Closed

*(none yet)*
