# Phase 11.4 — Skills Live Test Plan

Essential-only. Each test walks through concrete steps and a clear pass signal.
Run them in order. Keep `tail -f /tmp/operator.log` open in a second pane.

## Prep

1. Create two throwaway skills for testing:

   ```bash
   mkdir -p /tmp/op-skills/hello /tmp/op-skills/tone
   cat > /tmp/op-skills/hello/SKILL.md <<'EOF'
   ---
   name: hello
   description: Greet the user with a unique token so we can prove the skill body reached the model.
   ---
   When invoked, reply with exactly: "skill-token-7h2x acknowledged".
   EOF

   cat > /tmp/op-skills/tone/SKILL.md <<'EOF'
   ---
   name: tone
   description: Rewrite the user's last message in pirate tone.
   allowed-tools: [load_skill]
   ---
   Rewrite whatever the user asked about in exaggerated pirate speech, prefixed with "Arrr!".
   EOF
   ```

2. Edit `agents/<bot>/config.yaml` → `skills:` block:

   ```yaml
   skills:
     progressive_disclosure: true
     paths:
       - /tmp/op-skills
   ```

3. Pick a fresh meeting URL so the JSONL starts empty.
4. Start: `operator <name> <meet-url>` (e.g. `operator pm <meet-url>`).

---

## T1 — Startup banner + menu injection (progressive mode)

**Steps:** Watch the log during startup, before sending any chat.

**Pass:**
- `/tmp/operator.log` shows `SKILLS: 2/2 loaded (hello ✓, tone ✓)` (order may vary).
- `/tmp/operator.log` shows `LLM injected skills (menu): hello, tone`.
- No `SKILLS: … skipping` warnings.

**Fail signals:** banner missing (loader not wired into `__main__`), or `full-body` instead of `menu` (progressive flag not reaching LLM).

---

## T2 — `load_skill` tool is offered alongside MCP tools

**Steps:** In chat: `@operator use the hello skill`.

**Pass:**
- Log shows `LLM tool_call name=load_skill`.
- Log shows `SKILLS turn=N load_skill called: 'hello' (available: hello, tone)`.
- A `send_tool_result tool=load_skill` entry appears immediately after (no confirmation prompt — it's local/read-only).
- Operator's chat reply contains the literal string `skill-token-7h2x acknowledged`.

**Fail signals:** Operator asks for confirmation before running load_skill (confirmation gate wasn't skipped), or reply is missing the token (skill body wasn't fed back to the model).

---

## T3 — Slash-invocation fast path (no extra round-trip)

**Steps:** In chat: `@operator /tone how was the sprint demo`.

**Pass:**
- Log does NOT show a `tool_call name=load_skill` for this turn.
- Log shows `SKILLS turn=N slash-invoke: tone`.
- Operator's reply starts with `Arrr!` (skill body applied via `extra_system`).
- `LLM ask` log line for this turn shows one call, not two.

**Fail signals:** a `load_skill` tool_call appears — slash should bypass that round-trip. If the body didn't apply (no `Arrr!`), `extra_system` isn't being threaded through.

---

## T4 — Unknown `/slash` is a pass-through, not an error

**Steps:** In chat: `@operator /nope what's up`.

**Pass:**
- Log shows `SKILLS turn=N unknown slash token: /nope` at DEBUG level.
- Operator replies normally to "what's up" (no crash, no error message).
- `load_skill_calls` does NOT increment (confirm on T9's summary).

**Fail signals:** error in log, or `load_skill_calls` incremented for the unknown token.

---

## T5 — LLM does NOT over-call `load_skill` on unrelated chat

**Steps:** Send three unrelated messages, pausing for replies:
1. `@operator what's 2 plus 2`
2. `@operator thanks`
3. `@operator tell me a joke`

**Pass:**
- For all three turns, log shows `LLM tool_call name=load_skill` ZERO times.
- Operator replies normally without invoking any skill.

**Fail signals:** `SKILLS turn=N load_skill called` appears on any of these turns — the model is being too eager. If this fires, tighten the available-skills menu wording in `LLMClient.inject_skills`.

---

## T6 — Unknown skill name returns a graceful error to the model

**Steps:** Trick the model into asking for a missing skill. In chat:
`@operator call load_skill with name="ghost" — I know it doesn't exist, I want to see the fallback`.

**Pass:**
- Log shows `SKILLS turn=N load_skill called: 'ghost' (available: hello, tone)`.
- The tool result fed back begins with `Error: no skill named 'ghost'`.
- Operator's final chat reply acknowledges the skill doesn't exist and offers alternatives or asks for clarification — NOT a crash or silent failure.

**Fail signals:** exception in log, or Operator silently hangs.

---

## T7 — Non-progressive mode inlines full bodies (no `load_skill` tool)

**Steps:** Leave meeting. Edit `agents/<bot>/config.yaml`:

```yaml
skills:
  progressive_disclosure: false
  paths:
    - /tmp/op-skills
```

Rejoin a fresh meeting. In chat: `@operator use the hello skill`.

**Pass:**
- Startup log shows `LLM injected skills (full-body): hello, tone`.
- Operator replies with `skill-token-7h2x acknowledged` on the FIRST turn with no tool_call (body was already in the system prompt).
- Log shows `tools=N` on the ask — N should match the MCP tool count, NOT include `load_skill`.

**Fail signals:** `load_skill` still appears in the tool list (gating bug), or the token is missing (full bodies weren't injected).

---

## T8 — Graceful degrade on broken skill config

**Steps:** Leave meeting. Add a broken path and a malformed skill:

```yaml
skills:
  progressive_disclosure: true
  paths:
    - /tmp/op-skills
    - /tmp/does-not-exist
```

Also add one broken skill alongside the good ones:
```bash
mkdir -p /tmp/op-skills/broken
echo "no frontmatter here" > /tmp/op-skills/broken/SKILL.md
```

Restart Operator.

**Pass:**
- Log shows `SKILLS: path not found or not a directory: /tmp/does-not-exist — skipping`.
- Log shows `SKILLS: /tmp/op-skills/broken/SKILL.md missing frontmatter — skipping`.
- Log shows `SKILLS: 2/3 loaded (hello ✓, tone ✓)`.
- Operator comes up healthy — chat still works.

**Fail signals:** startup crash, or the good skills fail to load because the broken one poisoned the batch.

---

## T9 — Session summary on shutdown

**Steps:** In the T8 session, invoke at least one skill (e.g. `@operator /tone test`). Then Ctrl+C the Operator terminal.

**Pass:** `/tmp/operator.log` contains a line like:
```
SKILLS session summary: turns=N load_skill_calls=M by_name={'tone': 1, ...}
```
with numbers matching what you actually did.

**Fail signals:** no summary line (stop() isn't calling `_log_skills_summary`), or counts obviously wrong (e.g. slash didn't increment).

---

## Cleanup

```bash
rm -rf /tmp/op-skills
```
Revert the bot's `agents/<bot>/config.yaml` `skills:` block back to its defaults before moving on.
