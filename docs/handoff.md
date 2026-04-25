# Session 165 handoff (2026-04-26) — Phase 14.12.1 spike COMPLETE → ship 14.12.2

**Track A is technically buildable. CLI path validated under subscription auth. PreToolUse policy layering locked. Next session starts Phase 14.12.2 implementation.**

## What shipped this session

Phase 14.12.1 — Permission MCP spike, complete. Three probes ran end-to-end against `claude -p` under the user's Claude Max subscription (no `ANTHROPIC_API_KEY`):

- **Probe 1 (happy path):** PreToolUse hook fires synchronously, parent reads tool-use details via named-pipe IPC, parent's "allow" decision is honored, file is written. Round-trip latency negligible (<1s for IPC). Stream-json's system-init event confirmed `apiKeySource: "none"` end-to-end — actual subscription billing.
- **Probe 2b (sub-agent visibility):** definitively confirmed sub-agent (Task tool) inner tool calls do NOT fire our hook. Top-level `Agent` dispatch DOES fire (we see the full sub-agent prompt and can gate it). Sub-agent's internal Writes appear in stream-json with `parent_tool_use_id` set — visible for chat progress reporting but not gateable inline.
- **Probe 3 (deny path):** clean semantics. Hook returns deny → claude exits 0 with `subtype: "success"`, `permission_denials` populated, our deny reason text appears verbatim in claude's final result. No retries, no loops, no error.

Findings + 1-page architecture write-up at `docs/permission-mcp-spike.md`. Probe scripts at `debug/permission_mcp_spike/cli_probe_*.py` + `perm_bridge.sh`. Stream-json artifacts at `debug/permission_mcp_spike/probe*_stream.jsonl` for forensic re-inspection.

Two corrections to the research that should be folded into anyone reading the spike report: (a) `--permission-prompt-tool` does NOT exist on the actual CLI (early research agents got this wrong; PreToolUse hook is the real CLI mechanism); (b) `--settings <file-or-json>` DOES exist despite docs claiming otherwise — that's the per-invocation registration path. SDK pathway was researched but not probed (definitively API-key-only per [GitHub #559](https://github.com/anthropics/claude-agent-sdk-python/issues/559) — would burn API credits to generate paper-only data on a path that's structurally incompatible with track A's "no API key" promise).

## PreToolUse policy decisions (locked this session)

Three layers of policy, each in its natural home — no overlap, no double-prompts:

1. **brainchild's PreToolUse hook itself — internal plumbing, NOT user-tunable.** It's how brainchild stays in the loop. Disabling it forces yolo mode or stalled-on-no-TTY. Treat it like the chat-polling loop — load-bearing, not a knob.
2. **Native Claude Code `permissions.allow` / `ask` / `deny` rules in `~/.claude/settings.json` — already user-tunable, brainchild composes with them automatically.** Settings hierarchy (user → project → local → CLI override) means our `--settings <our-tempfile>` merges, doesn't replace. The user's solo-CLI rules pre-evaluate before our hook fires.
3. **brainchild's "what to bother the user about in chat" knob — the meeting-specific layer, exposed in track A's config.**

Track A bundled config will ship something like:

```yaml
# ~/.brainchild/agents/claude/config.yaml after the pivot
permissions:
  auto_approve:        # silent in chat
    - Read
    - Grep
    - Glob
    - LS
  always_ask:          # always confirm in chat
    - Bash
    - Write
    - Edit
    - MultiEdit
    - WebFetch
```

Everything not in either list defaults to "ask in chat." Power users tighten/loosen via `brainchild edit claude`. Wizard does NOT expose this surface (track A's pitch is zero setup; YAML edit is the explicit power-user path). Custom hook layering is NOT supported in track A — users wanting full hook customization belong in track B.

## Architectural recommendation for 14.12.2 (from spike report)

1. New `pipeline/providers/claude_cli.py` implementing `LLMProvider` — one `claude -p` subprocess per LLM turn (or per meeting; spike both, default to whichever is simpler), with `--settings <tempfile>` containing a PreToolUse hook pointing to a shipped `pipeline/permission_bridge.py`.
2. Named-pipe IPC between bridge and brainchild parent: bridge writes tool details to request pipe, blocks on response pipe; chat_runner reads request, posts confirmation via the existing `_request_confirmation` flow, awaits user reply, writes decision back.
3. Worktree sandboxing as second line of defense — every track-A run gets `.claude/worktrees/<name>/` (already supported via `--worktree`). Sub-agent opacity contained because writes go into the sandbox.
4. **Subscription-auth assertion at startup** — read system-init event's `apiKeySource` from stream-json, fail loud if not `"none"`. Catches any future env-var leak (the same class of bug that ate the $5 last session) before it bills.
5. Defer the `defer` permissionDecision pattern — synchronous IPC works today; revisit if hook-spawn-per-tool latency becomes a problem.

Spike findings reduce 14.12.2's estimate from 2–3d to **~1.5–2d**. Open questions for that phase (not blockers): subprocess-per-turn vs subprocess-per-meeting, prompt-curation policy (do we still tail meeting record JSONL into the prompt or trust inner-claude's own context loop), and which `--system-prompt*` flag carries our `personality` + `ground_rules` augmentation cleanly.

## Exact next step

**Phase 14.12.2 — track A implementation.** First atomic step: write `pipeline/providers/claude_cli.py` with the `complete()` and `complete_streaming()` methods, mirroring the `anthropic.py` shape. Get it answering a trivial `"hello"` query under subscription auth before wiring permissions. The IPC bridge + chat-runner integration come after that's green. Estimated session 1 of 14.12.2 = ~4–6 hours.

## Open carry-overs (unchanged from prior handoff)

(a) Stuck-LLM retry semantics still deferred. (b) `chat_runner._send` doesn't surface dead-browser state. (c) Linux adapter parity for ID-based dedup. (d) GitHub `operator → brainchild` repo rename still deferred. (e) Anthropic 30k-tokens/min ceiling on the claude bot's 95-tool prompt — likely dissolves under track A since inner-claude lazy-loads its own tools. (f) Session-160 Chrome-runtime preflight is dead weight — fold into 14.12.2 cleanup pass. Phase 15.10.5 (`brainchild worktrees` CLI) remains relevant for track B's engineer bot post-MVP.
