# Phase 15.5.2b — Designer Bundle Live Test Plan

Essential-only. Each test walks through concrete steps and a clear pass
signal. Run them in order. Keep `tail -f /tmp/operator.log` open in a second
pane.

This suite exercises what's NEW in the Designer bundle: the **GLips Figma
MCP server** (`figma-developer-mcp`, PAT-authenticated, read-only), the
**opinionated designer persona**, the bundled **`design-review-feedback`**
skill loaded via `progressive_disclosure`, and `captions_enabled: true` in
service of the "show me the login screen" hero moment. Battle-tested paths
(chat polling, JSONL, Anthropic provider, Ctrl+C shutdown) are only
smoke-checked.

Write operations (T4) live in the Power-ups section, not the default path —
enabling them requires swapping the MCP server.

## Prep

1. Edit `roster/designer/config.yaml` → set `agent.user_display_name` to your
   Google Meet display name (the template ships with `"Your Name"`). The
   runtime loads the bot's config directly — no root `config.yaml` to swap.

2. Verify prerequisites:

   ```bash
   grep ANTHROPIC_API_KEY .env       # required
   grep FIGMA_TOKEN .env             # required — personal access token from figma.com/settings
   which npx                          # GLips MCP runs via npx
   ```

   The config maps `${FIGMA_TOKEN}` → `FIGMA_API_KEY` in the server's env, so
   a missing or empty `FIGMA_TOKEN` surfaces at T1 as a handshake failure.

3. **Turn captions ON in Google Meet before joining** (CC button in the Meet
   toolbar). Bundle's config expects captions; without them, T2 + T6 fail.

4. Pick a fresh meeting URL: `https://meet.google.com/xxx-yyyy-zzz`. Use a
   new one each run — stale slugs get bounced.

5. Have a Figma file URL ready with a substantive multi-node frame (not a
   single-node wrapper image). The `Plant-shop-curved-carousel-(Community)`
   file works well: copy it to your account from Figma Community, then use
   `https://www.figma.com/design/<yourFileKey>/Plant-shop-curved-carousel?node-id=1-2`.
   A frame with 5+ child nodes, auto-layout, and real typography exercises
   T3's grounded-critique check well.

6. Terminal A — start Operator:

   ```bash
   ./operator designer https://meet.google.com/xxx-yyyy-zzz
   ```

7. Terminal B — stream logs:

   ```bash
   tail -f /tmp/operator.log
   ```

---

## T1 — Startup: Figma MCP connects + bundled skill loads

**Steps:** Watch the log during startup, before sending any chat. No browser
popup — authentication is PAT-based, so `figma-developer-mcp` starts silently
via npx.

**Pass:**
- Log shows `MCP server 'figma' connected — 2 tools` (GLips exposes
  `get_figma_data` and `download_figma_images`).
- Log shows `MCP: 1/1 servers loaded (figma ✓)`.
- Log shows `SKILLS: X/Y loaded (…)` and the names include
  `design-review-feedback ✓`.
- No `MCP USER CONFIG: … failed to start` entries.

**Fail signals:**
- `MCP: 0/1 servers loaded (figma ✗)` → most likely `FIGMA_TOKEN` is empty
  or invalid. Regenerate a PAT at `figma.com/settings`, update `.env`, and
  relaunch. `npx` download issues are a less common cause; run
  `npx -y figma-developer-mcp --stdio --help` manually to surface them.
- `SKILLS: path not found or not a directory: roster/designer/skills` →
  running from the wrong cwd; start Operator from the repo root.

The bundle's `read_tools` list (`get_figma_data`, `download_figma_images`)
matches GLips' actual surface, so no mirror-back step is needed for the
default path. If you swap to a different server (see Power-ups), verify the
tool names via:

```bash
grep "MCP server 'figma' connected" /tmp/operator.log
grep "tool_call name=figma" /tmp/operator.log | head -20
```

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

**Steps:** In chat (replace the URL with the Figma file you prepped in step 6):
`@operator pull up the carousel — <your-figma-url>`

**Pass:**
- Log shows `LLM tool_call name=figma__get_figma_data`.
- Log shows `ChatRunner: auto-executing figma__get_figma_data` (no
  confirmation — `get_figma_data` is in the bundle's `read_tools`).
- Log shows `MCP tool result length=N` with N substantial (a real frame
  payload is multi-KB; single-node fetches on trivial nodes can be <1KB —
  if so, pick a richer frame).
- Operator's chat reply summarizes the frame: layout shape (carousel /
  horizontal stack), dominant typography, and 1-2 spacing or hierarchy
  observations. Specific to the actual content — references the product
  cards, not generic "looks like a page."

**Fail signals:**
- Confirmation prompt appears → somehow `get_figma_data` is missing from
  `mcp_servers.figma.read_tools`. Fix the bundle and restart.
- No tool_call → Designer ignored the URL. Re-prompt: "use figma to fetch
  node <id> from file <key>."
- Tool result returns a thin payload (a single RECTANGLE when you expected a
  multi-frame carousel) → the node-id points at a wrapper image, not the
  actual frame. This is **expected** to trigger Designer's anti-hallucination
  guard: the reply should flag the mismatch and ask for the right node-id,
  *not* fabricate card-level critique. That's a pass for the guard, a fail
  for the test fixture — send a different URL and re-run.
- Reply is generic ("a product carousel typically has…") on a substantive
  payload → the LLM didn't read the result; check the raw
  `MCP tool result:` debug entry in the log.

---

## T4 — Figma write: hits the confirmation gate

**N/A on the default path.** GLips (`figma-developer-mcp`) is read-only —
the only tools it exposes are `get_figma_data` and `download_figma_images`,
so there's nothing to gate. Write-operation testing lives with whichever
power-up you enable for mutations (see Power-ups section). For the default
bundle, treat T4 as automatically passing and move to T5.

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
  consider…") → persona system prompt isn't biting. Re-check
  `roster/designer/config.yaml` loaded the designer system_prompt.
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

## Power-ups — swap in a different Figma MCP server

The default GLips server covers T1–T3, T5, T6, T7. To exercise write
operations (T4), swap `mcp_servers.figma` in `roster/designer/config.yaml`
for one of these and re-run the relevant subset:

**Grab `cursor-talk-to-figma-mcp`** — ~25 mutation tools (`create_frame`,
`set_text_content`, `move_node`, etc.). Requires installing their Figma
plugin and running a local WebSocket bridge (`bun socket`); operates only
on the file currently open in the plugin, not an arbitrary URL. Good for
T4 but T3's "paste a Figma URL" UX is lost.

```yaml
figma:
  command: "bunx"
  args: ["cursor-talk-to-figma-mcp@latest"]
  read_tools:
    - get_document_info
    - get_selection
    - read_my_design
    - get_node_info
    - get_nodes_info
    - get_annotations
    - get_reactions
    - get_styles
    - get_local_components
    - get_instance_overrides
    - scan_text_nodes
    - scan_nodes_by_types
    - join_channel
  confirm_tools: []
```

After swapping, start the bridge in a separate shell (`bun socket`), install
the plugin in Figma from
`github.com/grab/cursor-talk-to-figma-mcp`, then re-run T1 and run T4 against
the file you've opened in the plugin.

**Figma's official MCP server** — OAuth-authenticated, full read + write,
but currently gated by Figma's MCP catalog. When we live-tested during
session 112 (2026-04-16), dynamic client registration returned
`403 Forbidden`, confirming the gate is hard. If Operator is ever added to
the catalog, this becomes the best option — no plugin, no bridge:

```yaml
figma:
  command: "npx"
  args: ["-y", "mcp-remote", "https://mcp.figma.com/mcp"]
  read_tools:
    - get_design_context
    - get_code
    - get_image
    - get_variable_defs
    - get_components
    - get_styles
  confirm_tools: []
```

Re-run T1 — the log will show `HTTP 403 ... registerClient` if the catalog
gate is still closed.

---

## Cleanup (Operator runs this after the live test passes)

```bash
# Revert any temporary changes in roster/designer/config.yaml if you don't
# want them committed (e.g. display name, or a swapped-in write-capable
# mcp_servers.figma block from the Power-ups section).

# Optional — undo the T4 edit in Figma's UI.
# Optional — wipe the test meeting's JSONL:
# rm ~/.operator/history/bak-exiq-ekg.jsonl
```

If T1–T7 all pass, the Designer bundle is ship-ready for Phase 15.5.2b. The
"demo GIF" TODO in `roster/designer/README.md` can be recorded from a re-run
of T2 → T3 (a Figma frame summary materializing in chat seconds after the
spoken "let's look at the carousel" — the hero framing).
