# Phase 11.3b — Captions Live Test Plan

Essential-only. Each test walks through concrete steps and a clear pass signal.
Run them in order. Keep `tail -f /tmp/operator.log` open in a second pane and
`~/.operator/history/<slug>.jsonl` open in a third.

## Prep

1. Set `transcript.captions_enabled: true` in `roster/<bot>/config.yaml` (e.g. `roster/pm/config.yaml`).
2. Pick a fresh meeting URL so the JSONL starts empty.
3. Start: `./operator <name> <meet-url>` (e.g. `./operator pm <meet-url>`).
4. Have a second participant on another device to generate speech.

---

## T1 — Captions land in the JSONL as `kind: caption`

**Steps:** Once Operator joins, have the other participant say one sentence out loud (no chat, no `@operator`).

**Pass:**
- `/tmp/operator.log` shows `captions: enabled via Shift+C …` (or button fallback).
- `/tmp/operator.log` shows at least one `caption: [<speaker>] …` line during speech.
- `/tmp/operator.log` shows `caption_finalized reason=silence speaker=<speaker> …` after they stop.
- `~/.operator/history/<slug>.jsonl` contains a line with `"kind": "caption"`, the correct `sender`, and the full finalized sentence (not a delta fragment).

**Fail signals:** no `caption:` log lines (observer didn't attach), or multiple caption entries with partial text (finalization broken).

---

## T2 — Only finalized utterances hit disk (no deltas)

**Steps:** Other participant says a longer sentence (5–8 words) in one breath without stopping.

**Pass:**
- Multiple `caption: […]` streaming log lines appear during the sentence.
- Exactly ONE `caption_finalized` log line for that utterance.
- Exactly ONE `kind: caption` JSONL entry, containing the full final sentence.

**Fail signals:** more than one JSONL caption row for a single utterance → deltas are leaking to disk.

---

## T3 — Speaker change finalizes the previous utterance

**Steps:** Participant A says a sentence, then participant B immediately replies (no pause between).

**Pass:**
- Log shows `caption_finalized reason=speaker_change speaker=<A>` before B's text appears.
- JSONL has A's finalized caption then B's, in order, with A's complete.

---

## T4 — Captions and chat interleave in the record

**Steps:** Participant A speaks, then you type a chat message (no trigger needed for this test — we're just checking record ordering), then A speaks again.

**Pass:** `~/.operator/history/<slug>.jsonl` contains caption → chat → caption in timestamp order, each with correct `kind` and `sender`.

---

## T5 — LLM sees `[spoken]` context but doesn't respond to ambient speech

**Steps:** Other participant says out loud: "Should we just ship this on Monday?" — do NOT use `@operator` in chat.

**Pass:**
- The caption lands in the JSONL as `kind: caption`.
- Operator does NOT post anything in chat.

**Fail signals:** Operator replies to unaddressed speech → the `[spoken]` framing isn't steering the model.

---

## T6 — Ambient speech informs an addressed reply

**Steps:** Right after T5, type in chat: `@operator what did they just ask about?`

**Pass:** Operator replies referencing the Monday-ship question, proving the caption was in context. Reply should be concise (1–3 sentences).

---

## T7 — Flag off = zero caption plumbing

**Steps:** Leave meeting. Set `captions_enabled: false`. Rejoin the SAME meeting URL.

**Pass:**
- No `caption bridge exposed` log line.
- No Shift+C / captions-enable log lines.
- No new `kind: caption` entries appended to the JSONL for this session.
- Chat still works normally (send a `@operator ping` — it replies).

---

## T8 — Graceful degrade when captions can't be enabled

**Steps:** (Optional — only if you can simulate.) Disable captions support in the meeting settings, or run on a meeting language where captions aren't offered.

**Pass:** Log shows `captions unavailable — continuing without transcript`. Chat flow still works end-to-end.

---

## T9 — Clean shutdown flushes in-flight utterance

**Steps:** Have the other participant start a sentence. Before they finish, hit Ctrl+C in the operator terminal.

**Pass:** JSONL contains a final `kind: caption` entry with `reason=stop` visible in the log — whatever they had said so far is preserved, not dropped.
