# Phase 15.5.2b — Designer Bundle Live Test Plan

Essential-only. Each test walks through concrete steps and a clear pass
signal. Run them in order. Keep `tail -f /tmp/operator.log` open in a second
pane.

This suite exercises what's NEW in the Designer bundle: **Figma's official
MCP server** (first-run OAuth via `mcp-remote` + read + write-with-confirmation),
the **opinionated designer persona**, the bundled **`design-review-feedback`**
skill loaded via `progressive_disclosure`, and `captions_enabled: true` in
service of the "show me the login screen" hero moment. Battle-tested paths
(chat polling, JSONL, Anthropic provider, Ctrl+C shutdown) are only
smoke-checked.

## Prep

1. Back up the current root config and swap in the Designer config:

   ```bash
   cp config.yaml config.yaml.bak
   cp roster/designer/config.yaml config.yaml
   ```

2. Edit `config.yaml` → set `agent.user_display_name` to your Google Meet
   display name (the template ships with `"Your Name"`).

3. Verify prerequisites:

   ```bash
   grep ANTHROPIC_API_KEY .env       # required
   which npx                          # Figma MCP uses npx + mcp-remote
   ```

   `FIGMA_TOKEN` in `.env` is unused for this bundle (the official server is
   OAuth-authenticated). Leaving it set is harmless.

4. **Turn captions ON in Google Meet before joining** (CC button in the Meet
   toolbar). Bundle's config expects captions; without them, T2 + T6 fail.

5. Pick the fresh meeting URL: `https://meet.google.com/bak-exiq-ekg`.

6. Have the test Figma file URL ready:
   `https://www.figma.com/design/bTJlOWqmnmqVRmc2e4GGYd/Product-carousel.?node-id=2-2`
   (file key `bTJlOWqmnmqVRmc2e4GGYd`, node `2:2` — a product carousel for a
   skincare site).

7. Terminal A — start Operator:

   ```bash
   source venv/bin/activate && python __main__.py https://meet.google.com/bak-exiq-ekg
   ```

8. Terminal B — stream logs:

   ```bash
   tail -f /tmp/operator.log
   ```

---

## T1 — Startup: Figma MCP connects + bundled skill loads

**Steps:** Watch the log during startup, before sending any chat. This is the
first time Figma MCP runs on this machine, so `mcp-remote` will open a browser
window for OAuth — sign in to Figma and approve; the subprocess will wait.

**Pass:**
- Browser opens to Figma OAuth consent. Approve.
- Log shows `MCP server 'figma' connected — N tools` (Figma's official server
  exposes a handful of design + code-context + write tools).
- Log shows `SKILLS: X/Y loaded (…)` and the names include
  `design-review-feedback ✓`.
- No `MCP USER CONFIG: … failed to start` entries.

**Fail signals:**
- Browser doesn't open → check terminal for the auth URL printed by
  `mcp-remote` and paste it manually.
- OAuth rejects with "client not allowed" or similar → the catalog
  enforcement we worried about is real. Fall back to community
  `figma-developer-mcp` (see Fallback section).
- `SKILLS: path not found or not a directory: roster/designer/skills` →
  running from the wrong cwd; start Operator from the repo root.

**After T1 passes — verify the `read_tools` list matches reality:**

The designer config ships with a best-guess `read_tools` list for Figma. Grep
the log for the actual tool names:

```bash
grep "MCP server 'figma' connected" /tmp/operator.log
grep "tool=" /tmp/operator.log | grep figma | head -20
```

If any names you ship in `roster/designer/config.yaml` under `mcp_servers.figma.read_tools`
don't match what the server actually exposes, edit the list in `config.yaml`
(the live one at the repo root, copied from the bundle) and **restart Operator**
(Ctrl+C in Terminal A, re-run the launch command) before continuing to T3.

Mirror any corrections back into `roster/designer/config.yaml` so the bundle
ships with accurate names.

---

## T2 — Captions stream into chat history as `[spoken]`

**Steps:** Speak clearly in Meet for ~10s without saying `@operator`. E.g.
"Hey team, let's look at the product carousel — the spacing on the cards
feels tight."

**Pass:**
- Log shows an `LLM ask` turn (only when you later trigger Operator) whose
  replayed history includes a message with `[spoken] <name>: …` content.
- Operator does NOT reply to the spoken-only utterance (not addressed).
- `grep "\[spoken\]" /tmp/operator.log` after the test shows your phrase
  attributed to your display name.

**Fail signals:**
- No `[spoken]` entries → Meet captions toggle is off, or the caption
  scraper didn't attach. Re-toggle CC and re-run.
- Operator replies to the spoken line → you're alone in the meeting (1-on-1
  mode skips the trigger phrase). Invite a second participant or expect this.

---

## T3 — Figma read: fetch the carousel frame

**Steps:** In chat:
`@operator pull up the product carousel — figma.com/design/bTJlOWqmnmqVRmc2e4GGYd/Product-carousel.?node-id=2-2`

**Pass:**
- Log shows `LLM tool_call name=figma__<read-tool>` (likely
  `figma__get_design_context` or similar).
- Log shows `ChatRunner: auto-executing figma__<read-tool>` (no
  confirmation — assumes the post-T1 `READ_TOOLS` update is in place).
- Log shows `MCP tool result length=N` with N substantial (a real frame
  payload is multi-KB).
- Operator's chat reply summarizes the frame: layout shape (carousel /
  horizontal stack), dominant typography, and 1-2 spacing or hierarchy
  observations. Specific to the actual content — references the product
  cards, not generic "looks like a page."

**Fail signals:**
- Confirmation prompt appears → the tool name in `mcp_servers.figma.read_tools`
  doesn't match what the LLM called. Check the log's `tool_call name=` against
  the list in your `config.yaml`, fix, restart.
- No tool_call → Designer ignored the URL. Re-prompt: "use figma to fetch
  node 2:2 from file bTJlOWqmnmqVRmc2e4GGYd."
- Reply is generic ("a product carousel typically has…") instead of grounded
  in the fetched payload → the LLM didn't read the result; check the raw
  `MCP tool result:` debug entry in the log.

---

## T4 — Figma write: hits the confirmation gate

**Steps:** Pick a write the official MCP exposes (likely
`update_text`, `set_variable`, `create_frame`, or similar — check the
T1 tool list). In chat, ask Designer to perform a small, undoable change
to the test file. Example:
`@operator change the heading text on the carousel from its current value to "Spring Refresh"`

**Pass (confirmation turn):**
- Log shows `LLM tool_call name=figma__<write-tool>`.
- Log shows `ChatRunner: requesting confirmation for figma__<write-tool>`.
- Operator's reply begins with `I'd like to run <write-tool> via figma with: …`
  and includes the target frame and the new value.
- Log does NOT show `auto-executing` for this turn.

Then reply `yes`.

**Pass (execution turn):**
- Log shows `ChatRunner: auto-executing figma__<write-tool>`.
- Log shows `MCP executing tool=<write-tool> server=figma` followed by
  `MCP tool result length=N`.
- Operator's final chat reply confirms the change and includes the file URL
  so you can verify in the Figma desktop/web app.

**Fail signals:**
- Auto-executed on the first turn → a write tool name leaked into
  `READ_TOOLS` (regression — fix immediately).
- Write tool returns a permission error → the OAuth scopes don't include
  edit. Re-auth via `mcp-remote` and approve broader scopes, or accept that
  T4 is read-only on this account.

**Cleanup:** undo the change in Figma's UI after the run.

---

## T5 — Persona check: Designer has opinions

**Steps:** In chat: `@operator the carousel feels off — quick gut check, what's the first thing you'd change?`

**Pass:**
- Reply is 1-3 sentences, leads with the answer (no "Great question…"
  preamble).
- Reply names a specific element and a specific change (a number, a
  property, an element name) — not "maybe try more whitespace."
- Tone is direct and opinionated, not hedging. ("The card padding is the
  thing — bump it from 12 to 16, it's crowding the CTA" is the register.)

**Fail signals:**
- Generic, hedging response ("Design is subjective, but you might
  consider…") → persona system prompt isn't biting. Re-check `config.yaml`
  loaded the designer system_prompt, not a stale one.
- Reply is a 5-bullet structured critique → Designer over-reached for the
  skill instead of keeping it conversational. The skill should fire on
  "review" / "critique" / "feedback", not on "gut check."

---

## T6 — Bundled skill: `design-review-feedback`

**Steps:** Speak ~15s of design context first (so captions land), e.g. "We're
shipping this carousel for the spring drop. Goal is to drive clicks to the
hero product." Then in chat:
`@operator do a full design review on the carousel frame — use the design-review-feedback skill`

**Pass:**
- Log shows `LLM tool_call name=load_skill arguments={"name": "design-review-feedback"}`
  (progressive disclosure is on, so the LLM must call `load_skill` to pull
  the body).
- Log shows the Figma read tool fire (Designer fetches the frame to ground
  the critique, per the skill's "before you write" rule).
- Operator's reply uses the skill's shape: bold section headers
  (`**What works**`, `**What breaks**`, `**Questions**`, `**Suggestions**`),
  with empty sections omitted.
- Bullets are specific (numbers, element names, comparisons), not generic.
- `max_tokens: 400` holds — reply is not cut off mid-word.

**Fail signals:**
- No `load_skill` tool call → LLM ignored the skill menu. Verify
  `design-review-feedback` appeared in T1's SKILLS banner.
- Free-form prose without the structured headers → skill loaded but LLM
  paraphrased through it; tighten the ask ("use the design-review-feedback
  skill verbatim — section headers, bullets").
- Reply truncated mid-section → bump `max_tokens` to 600 and re-run.
- Bullets are generic ("consider improving the visual hierarchy") → the
  skill's "be specific" rule didn't bite. Check the skill body loaded
  correctly via `grep "loading skill design-review-feedback" /tmp/operator.log`.

---

## T7 — Smoke: Ctrl+C shuts down cleanly

**Steps:** In Terminal A, hit Ctrl+C.

**Pass:**
- Log shows `Stopping ChatRunner…` then `MCP server 'figma' shut down` then
  exit.
- No hung subprocess (`pgrep -f mcp-remote` returns nothing after a few
  seconds).

**Fail signals:**
- `mcp-remote` lingers → kill manually with `pkill -f mcp-remote`. File a
  shutdown bug.

---

## Fallback — if T1's official-server OAuth rejects Operator

The "MCP catalog" restriction in Figma's docs is the risk we flagged before
the build. If `mcp-remote` can't complete the OAuth dance, swap in the
community server:

1. Stop Operator.
2. In `config.yaml` under `mcp_servers.figma`, replace the `command` and
   `args`:

   ```yaml
   figma:
     command: "npx"
     args: ["-y", "figma-developer-mcp", "--stdio"]
     env:
       FIGMA_API_KEY: "${FIGMA_TOKEN}"
   ```

3. Make sure `FIGMA_TOKEN` in `.env` is set to your Figma personal access
   token.
4. Re-run T1. The community server exposes `get_figma_data` and
   `download_figma_images` only — read-only, so T4 is N/A in the fallback
   path.

---

## Cleanup (Operator runs this after the live test passes)

```bash
# Restore the original root config
cp config.yaml.bak config.yaml
rm config.yaml.bak

# Verify any read_tools name corrections from T1 are mirrored from the live
# config back into roster/designer/config.yaml so the bundle ships accurate.
diff <(yq '.mcp_servers.figma.read_tools' config.yaml) \
     <(yq '.mcp_servers.figma.read_tools' roster/designer/config.yaml) || true

# Optional — undo the T4 edit in Figma's UI.
# Optional — wipe the test meeting's JSONL:
# rm ~/.operator/history/bak-exiq-ekg.jsonl
```

If T1–T7 all pass, the Designer bundle is ship-ready for Phase 15.5.2b. The
"demo GIF" TODO in `roster/designer/README.md` can be recorded from a re-run
of T2 → T3 (a Figma frame summary materializing in chat seconds after the
spoken "let's look at the carousel" — the hero framing).
