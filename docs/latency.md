# Operator Latency Reference

End-to-end latency from wake phrase start to first audio byte out, measured from a live session on **2026-04-06**. Prompt: `"Operator."` Reply: `"Yes, I'm here. How can I assist you?"`

---

## Pipeline Step Table

T=0 is anchored at the **first caption DOM update** (`"Oper."` at `11:56:46.744`).  
Actual speech began ~330ms before T=0 (Google Meet caption ASR lag — not observable in logs).

| Step | Timestamp | T+ (ms) | Step Duration |
|------|-----------|---------|---------------|
| **User starts saying wake phrase** *(estimated — 330ms before first caption)* | ~11:56:46.414 | −330ms | — |
| First caption DOM update (`"Oper."`) — earliest log signal of speech | 11:56:46.744 | 0ms | — |
| Caption updates to full word (`"Operator."`) — wake phrase confirmed | 11:56:47.076 | +332ms | 332ms (ASR completes word) |
| Silence detection window begins | 11:56:47.076 | +332ms | — |
| Silence threshold hit — prompt finalized (0.72s no new captions) | 11:56:47.797 | +1,053ms | **720ms** (silence wait) |
| Prompt passed to runner / LLM stream request sent | 11:56:47.798 | +1,054ms | <1ms |
| LLM first token arrives (`"Yes"`) | 11:56:48.229 | +1,485ms | **431ms** (LLM time-to-first-token) |
| Filler clip starts playing (concurrent with remaining LLM stream) | 11:56:48.229 | +1,485ms | — |
| LLM stream done — full reply received | 11:56:48.410 | +1,666ms | **181ms** (remaining stream tokens) |
| TTS synthesis starts (Kokoro af_heart) | 11:56:48.412 | +1,668ms | <1ms |
| Filler clip finishes playing | 11:56:49.086 | +2,342ms | 674ms (filler clip length) |
| TTS synthesis done | 11:56:49.276 | +2,532ms | **864ms** (Kokoro synthesis) |
| `response_play_start` — mpv process spawned | 11:56:49.309 | +2,565ms | 33ms |
| **First audio byte piped to BlackHole** ← *user first hears response* | 11:56:49.448 | +2,704ms | **139ms** (mpv pipe overhead) |
| Response playback complete | 11:56:52.432 | +5,688ms | 2,984ms (audio playback) |

---

## Summary

| Segment | Duration |
|---------|----------|
| Speech end → caption finalized *(silence wait + ASR lag)* | ~1,050ms |
| LLM time-to-first-token | 431ms |
| LLM full stream | 613ms total (from prompt sent) |
| TTS synthesis (Kokoro) | 864ms |
| mpv spawn + pipe | 172ms |
| **First word onset from speech end** | **~2,700ms** |
| **First word onset from wake phrase start** | **~3,030ms** |

From the log's own end-to-end line:
```
llm_wait: 0.614s | synthesis: 0.864s | filler_wait: 0.000s | speak: 3.156s | total_from_finalized: 4.634s
```

`filler_wait: 0.000s` — filler fully covered the dead air; TTS was ready before the filler finished, so playback started with no gap.

---

## Notes

- **Caption ASR lag** (~330ms): Google Meet captions trail actual speech by ~330ms per DOM mutation gap logs. This is unobservable latency — the pipeline cannot start until the first caption appears.
- **Silence wait** (720ms): Configurable via `captions.silence_seconds` in `config.yaml` (currently `0.7`). This is the largest single controllable bottleneck.
- **Filler strategy**: Filler clip plays immediately on LLM first token, covering synthesis dead air. In this cycle it was surplus — synthesis finished 190ms before the filler ended (`filler_wait: 0.000s`).
- **STT step**: Not present — captions path skips Whisper entirely. Google Meet ASR is the STT.

---

## Ideas for Reducing Latency

### 1. Switch back to Whisper STT

**What to reconsider:** The original motivation for switching to captions was to enable mid-utterance speculative LLM firing. But looking at the pipeline table, the captions path has a structural ~330ms unrecoverable lag (caption ASR delay) on top of the 720ms silence wait — meaning the pipeline cannot start until at least ~1,050ms after speech ends. Whisper base (mlx) benchmarked at 110ms average transcription time, meaning at silence_seconds=0 the Whisper path could finalize in ~110ms from speech end — substantially faster to finalization than captions at current settings.

**What this depends on:**
- Whether speculative processing is performant and reliable enough to actually deliver wall-clock savings. Speculative fires mid-utterance before the person finishes speaking, which captions supports but Whisper does not (Whisper needs a complete audio chunk). If speculative is consistently saving time in practice, captions retain an edge. If speculative is frequently re-running or being discarded, that advantage shrinks.
- Whether longer utterances push Whisper transcription time past 0.5–1s. The benchmark clips were short (wake phrases, names, numbers). A longer prompt like "Operator, what's the difference between a Series A and a Series B round" has not been benchmarked. This needs to be tested before drawing conclusions.

**Suggested test:** Benchmark Whisper (mlx base) transcription time on a set of long utterances (10–20 words) and compare against the current captions pipeline's finalization time for the same prompts.

---

### 2. Caption path: single-fire speculative (don't allow re-runs)

**Background:** The pipeline previously had separate speculative (1.0s) and finalization (1.5s) thresholds, allowing an early LLM call to start while the person was still speaking. This was consolidated into a single `silence_seconds: 0.7` threshold because lowering the speculative threshold caused the speculative call to keep re-running as new caption words arrived — each restart added latency rather than saving it.

**Unexplored alternative:** Rather than consolidating thresholds, cap speculative to fire exactly once per utterance — the first time silence_seconds is hit, regardless of whether new captions arrive afterward. If a new caption comes in after the speculative call has already fired, do not restart it; let it complete. The result could abort naturally if the finalized prompt diverges significantly, but avoids the compounding re-run overhead that made the dual-threshold approach brittle.

**What to validate:** Whether a speculative call fired on partial text is more often correct (and therefore usable) than wasted. If most prompts are short and complete within the first silence window anyway, this may have limited benefit over the current approach.

---

### 3. Investigate LLM first-token overhead

**Observation from the table:** LLM first token took **431ms**, but the remaining tokens (completing the full reply) took only **181ms** more. Intuitively, a single classification token should be faster than generating a full sentence — the reverse ratio suggests the 431ms is not pure generation time but includes request overhead: TCP round-trip, server queue time, and prompt processing (the full system prompt + meeting transcript + instruction block is sent on every call).

**Ideas to explore:**

- **Split classify from generate:** Run classification (PASS vs respond) as a separate, minimal API call with a stripped-down prompt — no meeting transcript context, just the utterance and a binary instruction. If PASS, discard. If respond, fire the full generation call with context. The classify call would be smaller and potentially faster to first token. The tradeoff is two sequential API calls on respond paths, but if the classify call resolves faster than the current 431ms, the net could still be lower.

- **Local classifier model:** Run classification entirely on-device with a lightweight model (e.g. a quantized Llama or Phi model via llama.cpp/mlx-lm). Classification is a simple binary decision that doesn't require GPT-4.1-mini's capability. A local model could resolve in <100ms with no network round-trip, then fire the remote generation call only when needed.

- **Reduce prompt size:** The prompt currently includes the full meeting transcript (`_transcript_lines[-20:-1]`). Trimming context or summarizing older turns before sending could reduce prompt-processing time on the server side and shrink the payload, both of which affect time-to-first-token.

---

### 4. Per-speaker silence calibration from speech pattern analysis

**Idea:** Analyze a speaker's natural speech rhythm early in the session — measuring the gaps between their words, phrases, and sentences — and use those distributions to set silence thresholds that are calibrated to how *that person* actually speaks, rather than a fixed global value.

The core intuition: a threshold that works well for someone who speaks in short punchy sentences will feel sluggish for them and premature for someone who thinks aloud with long pauses mid-sentence. If we can observe that a given speaker's typical inter-sentence gap is, say, 400–600ms, we can tighten the silence window for them specifically without risking false triggers.

**Rough framing (not a spec):** The minimum observed gap between complete sentences in the calibration sample could inform the speculative threshold floor; the maximum gap before a new sentence begins could inform the finalization threshold ceiling. The pipeline would learn these values passively during the first few minutes of a meeting.

**What would need to be figured out:**
- How much speech is needed for a reliable sample, and whether the calibration window is short enough to be useful within a typical meeting.
- Whether caption timing data is sufficient for this analysis, or whether it requires raw audio (caption DOM gaps don't perfectly reflect acoustic silence — ASR batching introduces its own timing noise).
- How to handle multiple speakers in a meeting — thresholds would ideally be per-speaker, not per-session.

---

### 5. Dedicated audio-based wake detection to bypass caption ASR lag

**Idea:** The ~330ms caption ASR lag is structural and unrecoverable — the pipeline cannot react until Google Meet's ASR has processed and emitted the caption DOM update. For wake detection specifically, this lag is pure dead time: we're waiting on Google's ASR just to know the user said "hey operator." A dedicated on-device audio listener running in parallel could detect the wake phrase directly from the audio stream with sub-100ms latency, sidestepping captions entirely for this one step.

**How it would work:** A lightweight wake detector runs continuously on the meeting audio (via ScreenCaptureKit / the existing audio capture path). When it fires, it signals the pipeline to begin silence detection and prompt capture immediately — 300–400ms earlier than the first caption would arrive. The caption path would still handle prompt transcription after wake; this only changes when the clock starts.

**Candidate approaches:**
- **Picovoice Porcupine**: On-device wake word engine, <1ms CPU per frame, custom wake word models available. Commercial license for production use; free tier for hobbyist/dev. This is likely what was being considered previously.
- **OpenWakeWord**: Open-source alternative, runs via ONNX, supports custom phrases. Less polished than Porcupine but no licensing cost.
- **On-device Whisper (mlx, wake-only mode)**: Run mlx-whisper on short rolling audio windows (e.g. 1–2s) looking only for the wake phrase. Higher CPU cost than a dedicated wake engine but reuses existing infrastructure and needs no new dependency.

**Tradeoff to evaluate:** The existing wake detection via captions has a useful property — it only triggers when Google Meet has attributed speech to the correct speaker. An audio-based detector has no speaker attribution; in a multi-participant meeting it could trigger on anyone saying "hey operator." Whether that matters depends on the use case (solo sessions vs. group meetings).
