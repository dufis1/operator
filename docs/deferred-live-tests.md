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

### Phase 15.7.4 — wizard readiness status screen + inline OAuth (session 149)

- **Setup:** Run `brainchild setup`. Pick a preset (or "custom"), then at
  step 2 (Tools) toggle a mix: enable `claude-code` + `linear` + one
  env-auth MCP whose token is missing from `.env` (e.g. notion with
  `NOTION_API_KEY` unset).
- **Verify:**
  1. After the MCP picker closes, a `Readiness:` block renders with
     one line per enabled MCP. Expected shape:
     - `✓ github` (when `GITHUB_TOKEN` set)
     - `⚠ linear — run \`brainchild auth linear\` once to authorize (...)` if cache absent; else `✓ linear`
     - `✗ notion — set NOTION_API_KEY in .env (https://www.notion.so/profile/integrations)`
     - `✓ claude-code` (when binary + login ok) or `✗ claude-code — not logged in — run \`claude auth login\``
  2. Below the table, a dim one-liner reminding the user that
     `claude-code` delegations need a git-initialized repo path (only
     when claude-code is in the report).
  3. If any `⚠` oauth_needed, an inline `Authorize <name> now? [Y/n]`
     prompt fires; answering `y` hands the terminal to mcp-remote,
     browser opens, approval seeds the token cache, wizard re-renders
     readiness with ✓ and moves on.
  4. Declining inline auth (`n`) keeps the ⚠ and proceeds to the next
     step without hanging.
  5. `Press Enter to continue` pauses before step 3 so the user can
     read the status screen.
- **Graceful degradation to verify:**
  - Temporarily rename `git` (`sudo mv /usr/bin/git /usr/bin/git.bak`),
    re-enable claude-code — row should read `✗ claude-code — git CLI
    not on PATH — install git first (https://docs.claude.com/...)`.
    Restore after.
  - With `claude` binary present but logged out (`claude auth logout`),
    row should read `✗ claude-code — not logged in — run \`claude auth
    login\``.
- **Post-test:** Finish the wizard, launch the bot, confirm runtime
  still works end-to-end (the 15.7.4.5 runtime pre-flight will make
  this tighter in the next phase).
- **Why deferred:** The wizard is interactive TUI — can't unit-test the
  rich/Prompt rendering without a real terminal; the inline OAuth arm
  needs a live browser popup.

### Phase 15.7.4 — claude-code git-init graceful failure (session 149)

- **Setup:** Launch `brainchild engineer` (or any bot with claude-code
  enabled) into a Meet. In chat, ask the bot to delegate a task against
  a folder that is NOT a git repo — e.g. `/tmp/not-a-repo` (create it
  first: `mkdir /tmp/not-a-repo`).
- **Verify:** The bot's chat reply should relay (paraphrased) that the
  path isn't a git repo, include the path, and instruct the user to
  run `git init` in that folder or point at a folder that's already a
  git repo. No stack trace, no generic "tool failed" message.
- **Why deferred:** Needs a live Meet + claude-code subprocess.

## Closed

*(none yet)*
