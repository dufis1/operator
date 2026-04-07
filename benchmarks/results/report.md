# TTS Provider Benchmark — Step 7.3

**Date:** 2026-03-26

---

## Latency

_TTFAB = Time to First Audio Byte (when streaming audio starts arriving)._  
_For Piper, TTFAB = 0 (local synthesis); Total = synthesis time._

| Provider | TTFAB avg | TTFAB min | TTFAB max | Stream total avg |
|---|---|---|---|---|
| ElevenLabs eleven_flash_v2_5 | 0.44s | 0.31s | 0.87s | 0.47s |
| OpenAI tts-1 (nova) | 1.68s | 0.89s | 2.69s | 1.82s |
| OpenAI tts-1-hd (nova) | 2.31s | 1.52s | 2.94s | 2.43s |
| Piper en_US-amy-medium (local) | — | — | — | — |

---

## Cost

_Assumes ~150 chars per response, ~5 TTS calls per meeting._

| Provider | $/1k chars | Per response | Per meeting (5×) | Per 100 meetings |
|---|---|---|---|---|
| ElevenLabs eleven_flash_v2_5 | $0.400 | $0.0600 | $0.300 | $30.00 |
| OpenAI tts-1 (nova) | $0.015 | $0.0022 | $0.011 | $1.12 |
| OpenAI tts-1-hd (nova) | $0.030 | $0.0045 | $0.022 | $2.25 |
| Piper en_US-amy-medium (local) | $0 | $0 | $0 | $0 |

_ElevenLabs price is approximate — verify at elevenlabs.io/pricing._

---

## Voice Quality Through WebRTC

_Rated 1–5 after live WebRTC listening session in Google Meet._

| Provider | Quality (1–5) |
|---|---|
| ElevenLabs eleven_flash_v2_5 | 5 |
| OpenAI tts-1 (nova) | 4 |
| OpenAI tts-1-hd (nova) | 5 |
| Piper en_US-amy-medium (local) | 2 |

---

## Summary

| Provider | Quality | TTFAB avg | Cost/meeting | Extra vendor | Failure risk |
|---|---|---|---|---|---|
| ElevenLabs eleven_flash_v2_5 | 5/5 | 0.44s | $0.300 | ElevenLabs (new vendor) | API outage |
| OpenAI tts-1 (nova) | 4/5 | 1.68s | $0.011 | None (already using OpenAI) | API outage |
| OpenAI tts-1-hd (nova) | 5/5 | 2.31s | $0.022 | None (already using OpenAI) | API outage |
| Piper en_US-amy-medium (local) | 2/5 | — | $0 | None (local, no API) | None |

---

## Recommendation

**Highest quality:** ElevenLabs eleven_flash_v2_5 (score 5/5)

**Best practical choice:** OpenAI tts-1-hd (nova)

_Practical ranking weights quality first, then cost, then vendor count._

---

## Audio Clips

Pre-WebRTC reference clips (synthesized direct from each API, no WebRTC compression):

- `bench_results/clips/elevenlabs/` — 8 clips (ElevenLabs eleven_flash_v2_5)
- `bench_results/clips/openai_tts1/` — 8 clips (OpenAI tts-1 (nova))
- `bench_results/clips/openai_tts1hd/` — 8 clips (OpenAI tts-1-hd (nova))
- `bench_results/clips/piper/` — 8 clips (Piper en_US-amy-medium (local))
