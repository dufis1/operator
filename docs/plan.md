# Plan — Phase 15.5.5: `operator setup` wizard

*Session 118 · 2026-04-17 · locked in with user before implementation.*

## Goal

Ship a guided TUI so a new user (or an existing one tweaking a preset) can spin up a working `agents/<name>/` bundle in a few minutes without hand-editing YAML. Covers: picking a starting point, choosing which MCPs to enable, choosing which skills come along, supplying API keys, and writing the bundle to disk atomically.

Entry point: `operator setup`. New dep: `rich` for the TUI.

Est. ~4–4.5h (plus ~45m pre-step refactor, below).

## Pre-step — MCP `enabled` flag refactor

Before building the wizard we normalize how "available but not wired" MCPs are expressed. Today each bot's `config.yaml` uses commented-out YAML blocks for power-ups (notion, slack, brave, etc.). The wizard would have to text-scan comments to discover them — fragile.

**Change:** every MCP a bot could plausibly use lives as a real YAML block with an explicit `enabled: true` or `enabled: false` field. `config.py` filters `enabled: false` blocks at load time so runtime code never sees them (zero blast radius downstream — all consumers already iterate `config.MCP_SERVERS`, the filtered dict).

- `config.py` — add `if not srv.get("enabled", True): continue` in the `MCP_SERVERS` loop. Default = `true` for backward-compat.
- `agents/pm/config.yaml` — uncomment notion/slack/brave_search, add `enabled: false`. Existing linear+github stay `enabled: true`.
- `agents/engineer/config.yaml` — preserve current enabled state (github, delegate). Add linear/notion/slack/brave_search as `enabled: false` blocks cloned from pm's baseline (hints, env, read_tools included).
- `agents/designer/config.yaml` — preserve figma. Add github/linear/notion/slack/brave_search as `enabled: false`.

**pm becomes the from-scratch baseline.** When a user picks "start from scratch" in the wizard, we clone pm's full `mcp_servers` block with every server forced to `enabled: false`, and the user toggles on what they want.

## The flow (five steps)

### Step 1 — Fighter select

Two modes:

- **Start from scratch.** Wizard prompts for `agent.name`, `trigger_phrase`, `user_display_name`, `tagline`. Writes to a new `agents/<name>/`. A collision on `<name>` (directory already exists) re-prompts for a different name — state is preserved, wizard does not quit.
- **Build on a preset.** Render the roster as a picker (face + name + tagline, same visuals as `operator list`). User picks one. **Name and identity are inherited from the preset** — no rename prompt. The wizard operates in edit-in-place mode and rewrites `agents/<preset-name>/`. Also doubles as a "reconfigure an existing bot" tool.

### Step 2 — Power-ups (MCPs)

Show every `mcp_servers` block in the base bot's `config.yaml` as a numbered list, with `[x]` for `enabled: true` and `[ ]` for `enabled: false`. User types a comma-separated list of numbers to toggle (or Enter to accept defaults). We collect the env-var names each enabled MCP block references (`${LINEAR_API_KEY}`, `${GITHUB_TOKEN}`, etc.) for use in Step 4.

From-scratch mode: clone pm's full `mcp_servers` block (pm is the baseline), every server starts `enabled: false`, user toggles on.

**UX pattern** (reused for skills in Step 3): numbered-list with `[x]`/`[ ]` state labels + single text prompt to toggle. No checkbox widget, no new deps beyond `rich`. Empty input = accept current state (happy path is one keystroke).

### Step 3 — Skills

Two halves on one screen:

- **Add your own (at top).** Free-form path entries. Default suggestion: `~/.claude/skills` if it exists. Accepts either a folder containing `SKILL.md` **or** a single `.md` file. Single-file case: wizard wraps it in a folder named after the file stem and drops the file in as `SKILL.md`.
- **Bundled skills from the base bot.** Each `agents/<base>/skills/*/SKILL.md` rendered in the same numbered-list toggle pattern (name + description from frontmatter, `[x]` default). **All checked by default.** Toggling off means that folder is not copied into the new bot.

If the base bot has no `skills/` folder (e.g. engineer), the bundled half is skipped and only the "add your own" entry shows.

**Implementation choice — copy, not reference.** Selected skills (both user-added and bundled) are physically copied into `agents/<name>/skills/`. The new bot's `skills.paths` in `config.yaml` points at that one local folder. Rationale: self-contained bundle, portable across machines/IDEs, can be committed to the repo, no path-breakage when users change environments. The `skills.paths` list is still free to hand-edit if someone wants to add a live-reference path later.

**MCP/skill coupling.** None. The two steps are independent. If a skill references an MCP tool that's disabled, the model simply doesn't have that tool and either asks for it or degrades gracefully — no crash. README template notes the tradeoff so users aren't surprised.

### Step 4 — API keys

For every env var referenced by a selected MCP that isn't already present in the repo-root `.env`, prompt for a value. Append to `.env` on confirm. **Never overwrite an existing key** without explicit confirmation. Keys can be skipped (MCP will fail at startup; user can add the key later).

### Step 5 — Atomic write

Build the entire bundle in a tempdir: `config.yaml`, copied `skills/` subfolders, freshly-minted `portrait.txt` via `face.write_if_missing()`, and a README stub that includes the MCP/skill-coupling note.

`os.rename` the tempdir into `agents/<name>/`. On any error during the build, the tempdir is cleaned up and the existing `agents/<name>/` is untouched.

Preset/edit-in-place mode: same tempdir approach, then swap — move current `agents/<name>/` to `agents/<name>.bak-<timestamp>/`, rename new bundle into place, delete `.bak` on success. Abort leaves the `.bak` for recovery.

## Files touched

**Pre-step (MCP `enabled` refactor):**
- **Modified:** `config.py` — filter `enabled: false` blocks at load.
- **Modified:** `agents/pm/config.yaml`, `agents/engineer/config.yaml`, `agents/designer/config.yaml` — full MCP roster spelled out with explicit `enabled` on each block.

**Wizard:**
- **New:** `pipeline/setup.py` — wizard logic (rich TUI). Probably ~400–500 lines.
- **New:** `tests/test_setup.py` — skill copy (folder + single file), MCP toggle round-trip (`enabled` flip), atomic-write rollback, collision re-prompt, edit-in-place swap.
- **Modified:** `__main__.py` — add `operator setup` subcommand dispatch.
- **Modified:** `requirements.txt` — add `rich`.
- **Modified:** `docs/agent-context.md` + `docs/roadmap.md` — mark 15.5.5 done after land.

## Decisions already locked with user

| Question | Decision |
|---|---|
| Step 1 mode | Two modes: from-scratch (user names) vs preset (name inherited, edit-in-place) |
| Copy vs reference user skills | **Copy** — portability > freshness |
| Single file vs folder skill input | **Both** — wrap single files in a stub folder |
| Bundled skills toggle | **Per-skill checkboxes**, default all checked |
| MCP/skill dependency wiring | **None** — keep steps independent, README notes the tradeoff |
| No bundled skills case | Show only "wire your own" |
| Name collision on from-scratch | Re-prompt, don't quit |
| Preset overwrite risk | Accepted — edit-in-place with `.bak-<timestamp>` swap for recovery |
| Scope | Wizard-only; no runtime skill-loader changes |
| Disabled MCPs in config | **`enabled: false` field** on real YAML blocks (replaces commented-out blocks). `config.py` filters at load. |
| From-scratch baseline | **pm** — wizard clones pm's `mcp_servers`, forces all to `enabled: false`, user toggles on |
| TUI widget choice | Numbered list with `[x]`/`[ ]` + text toggle prompt. No checkbox widget, no `questionary` dep. |

## Out of scope

- Voice-era setup (MCP OAuth flows, voice selection, model-selection UI) — that's Phase 15.1 post-MVP.
- Editing `.env` keys the user already has (only appends missing ones).
- Detecting/warning about skill-MCP dependencies (intentionally omitted).
- Publishing/sharing the generated bundle anywhere — it's just written to local disk.
