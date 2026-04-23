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

### Phase 15.7.3 — OAuth cache-path fail-fast + `brainchild auth` (session 148)

- **Setup:** Move the Linear token cache aside:
  ```
  mv ~/.mcp-auth/mcp-remote-*/fcc436b0d1e0a1ed9a2b15bbd638eb13_tokens.json /tmp/
  ```
  (Filename is `md5("https://mcp.linear.app/mcp")_tokens.json`.) Then
  launch `brainchild pm` and join a Meet.
- **Verify:**
  1. Bot joins in normal time — **no 30+s hang** waiting for OAuth.
  2. On-join banner fires: `Heads-up — linear didn't load (needs auth — run brainchild auth linear). Ask for details.`
  3. When prompted in chat ("why did linear fail?"), LLM reply relays
     the specific fix (run the auth command) rather than "check logs."
  4. Exit the bot.
- **Re-authorize step:** Run `brainchild auth linear`. Browser should
  open for Linear OAuth. Approve. CLI should print
  `✓ Token cached at ~/.mcp-auth/mcp-remote-<ver>/<hash>_tokens.json`
  and exit 0 within seconds of approval.
- **Post-test:** Launch `brainchild pm` again — no banner, Linear loads,
  Linear tool calls work.
- **Why deferred:** Needs a real Linear OAuth roundtrip, a browser, and
  a live Meet join.

## Closed

*(none yet)*
