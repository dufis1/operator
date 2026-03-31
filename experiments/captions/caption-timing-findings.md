# Google Meet Caption DOM Behavior — Experiment Results

Date: 2026-03-30
Method: MutationObserver on `document.body`, single-speaker session, ~2.5 minutes

## Key Findings

### 1. Node lifecycle: one node per speaker turn, not per utterance

Meet creates a single DOM node for each speaker and **appends all subsequent speech to it** for extended periods. Even 20-second silence gaps do not trigger a new node for the same speaker.

In our test, 2 nodes were created during the session. Node #1 contained only "Okay." and was immediately replaced (0.00s gap) by node #2, which then accumulated 124 updates over 157 seconds covering the rest of the entire session. The initial node #1 appears to be a Meet initialization artifact (possibly a brief render before the streaming caption element is swapped in).

After leaving, nodes #3–#7 were post-meeting UI elements (buttons, headings), not captions.

**Implication**: We cannot use "new DOM node" as an utterance boundary signal for single-speaker scenarios. We need our own silence detection based on update timing gaps. The initial node replacement (#1 → #2) should not be treated as a meaningful speaker boundary.

### 2. DOM update cadence: ~330ms

When the speaker is actively talking, Meet updates the caption DOM every 0.28–0.37 seconds (median ~0.33s, roughly 3 updates per second). Each update appends 1–3 words.

### 3. Silence is visible as update gaps

When the speaker pauses, no DOM updates occur. The gap duration maps directly to the silence duration:

| Observed gap | Context |
|-------------|---------|
| 1.2–1.8s | Natural breath/micro-pause between phrases |
| 2.7–3.1s | Deliberate short pause (3-second silence test) |
| 4.4–4.9s | ~5-second silence test |
| 6.9–7.3s | Longer deliberate pauses |
| 10.2s | ~10-second silence test |
| 17.3s | Long pause (restarting a phase) |
| 19.8s | ~20-second silence |

**Implication**: A gap of 2–3 seconds between updates reliably indicates the speaker has stopped talking. This is our utterance boundary signal.

### 4. Meet performs ASR corrections mid-stream

Meet's speech recognition rewrites earlier text as it gets more context. Examples:

- `"He?"` corrected to `"He operator."` (recognized word after more audio)
- `"What's the weather? Like in New York"` restructured to `"What's the weather like in New York City?"`
- `"Ten."` reformatted to `"10 seconds."`
- `"never mind"` reformatted to `"Nevermind"` then back to `"never mind"`

The full accumulated text is not strictly append-only. New updates can rewrite the tail of the string.

**Implication**: Always use the full text from the latest update, not accumulated deltas. The delta extraction (checking if new text starts with old text) will fail on corrections — need to handle the case where the new text diverges from the previous.

### 5. No phantom captions during silence

60+ seconds of complete silence produced zero caption events. The observer is clean — no noise from ambient audio or system sounds.

### 6. Post-meeting UI elements leak through

After leaving the meeting, the MutationObserver picked up non-caption DOM changes:
- "Leave call" (button text)
- "No one else is in this meeting"
- "You left the meeting..." (heading + feedback UI)
- "Returning to home screen"

**Implication**: Scope the MutationObserver to the captions `[role="region"][aria-label*="Captions"]` container, or filter out non-caption elements. Additionally, filter known system phrases like "You left the meeting", "No one else is in this meeting", etc.

### 7. Speaker labels are reliable

All 124 updates consistently attributed to "Jojo Shapiro" via the `.NWpY1d, .xoMHSc` badge selectors. No label drops or misattributions observed (single-speaker test only — multi-speaker not yet validated).

## Implications for Operator

### Wake phrase detection
- "operator" appears mid-stream as part of growing text: `"He?"` → `"He operator."` → `"he operator, What's the weather"`
- Detection should watch for "operator" appearing in the latest full text
- Must handle corrections: the word may appear, disappear, then reappear as Meet refines

### Utterance boundary (when to send prompt to LLM)
- Cannot rely on DOM node boundaries (same speaker = same node)
- Use update timing: if no new update for ~2-3 seconds after wake phrase detected, treat utterance as complete
- The full text at that point contains the complete prompt

### Proposed caption-to-pipeline flow
1. Monitor caption updates (~3/sec while speaking)
2. On each update, check full text for wake phrase "operator"
3. Once detected, continue accumulating text
4. When update gap exceeds threshold (e.g., 2.5s), extract prompt from text after "operator"
5. Send to LLM

### What we still need to validate (before coding the refactor)

#### Must test — affects architecture decisions

1. **Multi-speaker node behavior**: Does Meet create a new node when a different person speaks? This determines whether we get free speaker identification and utterance boundaries on speaker changes. Requires a second device in the meeting.

2. **Node text length cap**: The single node hit ~1800 characters in 2.5 minutes. In a real 1-hour meeting, does Meet eventually truncate, replace, or split the node? If node text grows to 50K+ characters, delta detection gets expensive and wake phrase scanning slows down. Test by running a longer session (10+ minutes of continuous speech).

3. **ASR correction window**: We saw Meet rewrite `"He?"` to `"He operator."` How far back does Meet reach to make corrections? If it only rewrites the last few words, everything before that can be treated as finalized text. If it rewrites arbitrarily far back, we need a different approach for prompt extraction. Test by saying a phrase, pausing 1 second, continuing, and tracking whether the earlier text changes after the pause.

#### Nice to validate — won't block initial implementation

4. **Caption availability without a Workspace plan**: Does the bot always have access to captions, or does it depend on the meeting host's Google Workspace plan? If someone on free Gmail hosts, do captions work?

5. **Captions-on timing**: If captions are enabled after someone already started talking, do we get retroactive text or only speech from that point forward?

6. **Overlapping speech**: When two people talk simultaneously, does Meet interleave nodes, drop one speaker, or merge them?

7. **Non-English and technical terms**: How well does Meet handle domain jargon, proper nouns, code-related words like "API endpoint" or "kubectl"? Determines whether caption quality is sufficient to fully replace Whisper.
