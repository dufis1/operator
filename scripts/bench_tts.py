#!/usr/bin/env python3
"""
TTS Provider Benchmark — Step 7.3

Evaluates six providers across latency, voice quality through WebRTC, and cost:
  - ElevenLabs (eleven_flash_v2_5)
  - OpenAI tts-1  (nova voice)
  - OpenAI tts-1-hd  (nova voice)
  - OpenAI gpt-4o-mini-tts  (nova voice)
  - macOS say  (Ava Enhanced — macOS only, skipped on Linux)
  - Piper  (en_US-amy-medium, local — skipped if piper-tts not installed)

Runs in phases:
  1. latency    — Measure time-to-first-audio-byte per provider (automated, ~3 min)
  2. clips      — Generate and save one audio file per provider per phrase (automated, ~1 min)
  3. meet       — Join a Meet call, play all clips through WebRTC, collect quality ratings
  4. streaming  — Measure whether TTFAB scales with input length (informs sentence streaming)
  5. report     — Compile everything into bench_results/report.md

Usage:
  # All phases (latency + clips + meet + report):
  python scripts/bench_tts.py --meet-url "https://meet.google.com/xxx-yyy-zzz"

  # Just latency and clips (no meeting needed):
  python scripts/bench_tts.py

  # Individual phases:
  python scripts/bench_tts.py --phase latency
  python scripts/bench_tts.py --phase clips
  python scripts/bench_tts.py --phase meet --meet-url "https://meet.google.com/xxx-yyy-zzz"
  python scripts/bench_tts.py --phase streaming
  python scripts/bench_tts.py --phase report

Results persist in bench_results/ — phases can be re-run independently.
"""

import argparse
import io
import json
import os
import platform
import subprocess
import sys
import time
import wave
from pathlib import Path

# Add project root to sys.path so config.py can be imported
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402  (must come after sys.path manipulation)

# ─────────────────────────────────────────────────────────────────────────────
# Provider definitions
# ─────────────────────────────────────────────────────────────────────────────

# Resolved once at startup — used in PROVIDERS dict and synth_macos_say fallback
def _best_macos_voice_early() -> str | None:
    """Called at module load to populate _MACOS_VOICE. Avoids circular reference."""
    if platform.system() != "Darwin":
        return None
    result = subprocess.run(["say", "-v", "?"], capture_output=True, text=True)
    installed = {line.split("  ")[0].strip() for line in result.stdout.splitlines() if line.strip()}
    for voice in [
        "Ava (Premium)", "Zoe (Premium)",
        "Ava (Enhanced)", "Zoe (Enhanced)",
        "Flo (English (US))", "Shelley (English (US))",
        "Reed (English (US))", "Sandy (English (US))",
        "Eddy (English (US))", "Rocko (English (US))",
        "Samantha",
    ]:
        if voice in installed:
            return voice
    return None

_MACOS_VOICE = _best_macos_voice_early()

PROVIDERS = {
    "elevenlabs": {
        "label":              "ElevenLabs eleven_flash_v2_5",
        "short":              "ElevenLabs",
        "model":              "eleven_flash_v2_5",
        "voice":              config.TTS_VOICE_ID,  # from config.yaml / .env
        "cost_per_1k_chars":  0.40,   # approximate — verify at elevenlabs.io/pricing
        "ext":                "mp3",
    },
    "openai_tts1": {
        "label":              "OpenAI tts-1 (nova)",
        "short":              "OpenAI tts-1",
        "model":              "tts-1",
        "voice":              "nova",
        "cost_per_1k_chars":  0.015,  # $15 / 1M chars
        "ext":                "mp3",
    },
    "openai_tts1hd": {
        "label":              "OpenAI tts-1-hd (nova)",
        "short":              "OpenAI tts-1-hd",
        "model":              "tts-1-hd",
        "voice":              "nova",
        "cost_per_1k_chars":  0.030,  # $30 / 1M chars
        "ext":                "mp3",
    },
    "openai_mini_tts": {
        "label":              "OpenAI gpt-4o-mini-tts (nova)",
        "short":              "OpenAI mini-tts",
        "model":              "gpt-4o-mini-tts",
        "voice":              "nova",
        "cost_per_1k_chars":  0.015,  # approximate — verify at platform.openai.com/pricing
        "ext":                "mp3",
    },
    "macos_say": {
        "label":              f"macOS say ({_MACOS_VOICE or 'unavailable'})",
        "short":              "macOS say",
        "model":              _MACOS_VOICE or "",
        "voice":              _MACOS_VOICE or "",
        "cost_per_1k_chars":  0.0,    # free — built into macOS
        "ext":                "aiff",
    },
    "piper": {
        "label":              "Piper en_US-amy-medium (local)",
        "short":              "Piper amy",
        "model":              "en_US-amy-medium",
        "voice":              "en_US-amy-medium",
        "cost_per_1k_chars":  0.0,
        "ext":                "wav",
    },
    "piper_lessac": {
        "label":              "Piper en_US-lessac-high (local)",
        "short":              "Piper lessac-high",
        "model":              "en_US-lessac-high",
        "voice":              "en_US-lessac-high",
        "cost_per_1k_chars":  0.0,
        "ext":                "wav",
    },
    "kokoro_heart": {
        "label":              "Kokoro af_heart (American Female — Heart)",
        "short":              "Kokoro Heart",
        "model":              "af_heart",
        "voice":              "af_heart",
        "cost_per_1k_chars":  0.0,
        "ext":                "wav",
    },
    "kokoro_emma": {
        "label":              "Kokoro bf_emma (British Female — Emma)",
        "short":              "Kokoro Emma",
        "model":              "bf_emma",
        "voice":              "bf_emma",
        "cost_per_1k_chars":  0.0,
        "ext":                "wav",
    },
    "kokoro_isabella": {
        "label":              "Kokoro bf_isabella (British Female — Isabella)",
        "short":              "Kokoro Isabella",
        "model":              "bf_isabella",
        "voice":              "bf_isabella",
        "cost_per_1k_chars":  0.0,
        "ext":                "wav",
    },
    "kokoro_sky": {
        "label":              "Kokoro af_sky (American Female — Sky)",
        "short":              "Kokoro Sky",
        "model":              "af_sky",
        "voice":              "af_sky",
        "cost_per_1k_chars":  0.0,
        "ext":                "wav",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Test corpus
# ─────────────────────────────────────────────────────────────────────────────

# 8 phrases covering short acks, medium responses, and longer technical replies.
# The same set is used for both latency measurement and clip generation.
PHRASES = [
    ("ack_1",    "Got it, one moment."),
    ("ack_2",    "Absolutely."),
    ("ack_3",    "Sure, I can help with that."),
    ("medium_1", "I've added that to the notes. Is there anything else you'd like me to track?"),
    ("medium_2", "The deadline for the API redesign is end of quarter. Do you want me to flag that as a priority?"),
    ("long_1",   "Based on what you just shared, it sounds like the authentication migration is the blocker for Q3. Would you like me to draft a summary of the dependencies?"),
    ("retry",    "I didn't catch that — could you say it again?"),
    ("clarify",  "Let me look into that for you."),
]

# Subset used for latency timing runs (short phrases only — fast, representative)
LATENCY_PHRASES = [p for p in PHRASES if p[0].startswith("ack") or p[0] == "clarify"]
LATENCY_RUNS = 3  # API calls per phrase (averaged to smooth network jitter)

# Platform detection — determines audio device and adapter used in the Meet session
IS_MAC = platform.system() == "Darwin"

# macOS: mpv plays to BlackHole → Chrome mic → WebRTC
# Linux: mpv plays to PulseAudio MeetingOutput → VirtualMic → Chrome mic → WebRTC
MAC_AUDIO_DEVICE   = "coreaudio/BlackHole2ch_UID"
LINUX_AUDIO_DEVICE = "pulse/MeetingOutput"

# ─────────────────────────────────────────────────────────────────────────────
# Output paths
# ─────────────────────────────────────────────────────────────────────────────

RESULTS_DIR       = ROOT / "benchmarks" / "results"
CLIPS_DIR         = RESULTS_DIR / "clips"
PIPER_MODELS_DIR  = RESULTS_DIR / "piper_models"
LATENCY_FILE      = RESULTS_DIR / "latency.json"
SCORES_FILE       = RESULTS_DIR / "quality_scores.json"
STREAMING_FILE    = RESULTS_DIR / "streaming.json"
REPORT_FILE       = RESULTS_DIR / "report.md"


# ─────────────────────────────────────────────────────────────────────────────
# Provider synthesis functions
# ─────────────────────────────────────────────────────────────────────────────

def synth_elevenlabs(text: str, model: str, voice_id: str):
    """Streams from ElevenLabs. Returns (mp3_bytes, ttfab_s, total_s)."""
    from elevenlabs import ElevenLabs
    client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)
    t0 = time.perf_counter()
    audio = b""
    ttfab = None
    for chunk in client.text_to_speech.stream(
        text=text, voice_id=voice_id, model_id=model
    ):
        if chunk:
            if ttfab is None:
                ttfab = time.perf_counter() - t0
            audio += chunk
    total = time.perf_counter() - t0
    return audio, ttfab or total, total


def synth_openai(text: str, model: str, voice: str):
    """Streams from OpenAI TTS. Returns (mp3_bytes, ttfab_s, total_s)."""
    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    t0 = time.perf_counter()
    audio = b""
    ttfab = None
    with client.audio.speech.with_streaming_response.create(
        model=model, voice=voice, input=text, response_format="mp3"
    ) as resp:
        for chunk in resp.iter_bytes(chunk_size=4096):
            if chunk:
                if ttfab is None:
                    ttfab = time.perf_counter() - t0
                audio += chunk
    total = time.perf_counter() - t0
    return audio, ttfab or total, total


def synth_piper(text: str, model: str):
    """Synthesizes locally via the piper CLI. Returns (wav_bytes, 0.0, total_s).

    TTFAB is reported as 0.0 — Piper is local, so synthesis time IS the only cost.
    Uses subprocess (python -m piper) rather than the Python API to avoid
    piper-phonemize compatibility issues on macOS.
    """
    import tempfile

    onnx_path, json_path = _piper_model_paths(model)
    _ensure_piper_model(model, onnx_path, json_path)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        tmp_path = tf.name

    try:
        t0 = time.perf_counter()
        result = subprocess.run(
            [
                sys.executable, "-m", "piper",
                "--model", str(onnx_path),
                "--config", str(json_path),
                "--output_file", tmp_path,
            ],
            input=text.encode(),
            capture_output=True,
        )
        total = time.perf_counter() - t0

        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode().strip() or "piper exited non-zero")

        audio = Path(tmp_path).read_bytes()
        if len(audio) < 100:
            raise RuntimeError(
                f"Piper produced {len(audio)} bytes — likely silent output. "
                f"stderr: {result.stderr.decode().strip()}"
            )
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    return audio, 0.0, total


def _piper_model_paths(model: str):
    return (
        PIPER_MODELS_DIR / f"{model}.onnx",
        PIPER_MODELS_DIR / f"{model}.onnx.json",
    )


def _ensure_piper_model(model: str, onnx_path: Path, json_path: Path):
    """Download Piper voice model from Hugging Face if not already cached."""
    if onnx_path.exists() and json_path.exists():
        return

    import urllib.request

    PIPER_MODELS_DIR.mkdir(parents=True, exist_ok=True)

    # en_US-amy-medium  →  en/en_US/amy/medium/en_US-amy-medium
    parts = model.split("-")           # ["en_US", "amy", "medium"]
    lang_region = parts[0]             # "en_US"
    lang        = lang_region.split("_")[0]  # "en"
    speaker     = parts[1]             # "amy"
    quality     = parts[2]             # "medium"
    hf_path     = f"{lang}/{lang_region}/{speaker}/{quality}/{model}"
    base_url    = (
        "https://huggingface.co/rhasspy/piper-voices"
        f"/resolve/v1.0.0/{hf_path}"
    )

    for ext, dest in [(".onnx", onnx_path), (".onnx.json", json_path)]:
        if not dest.exists():
            url = base_url + ext
            print(f"  Downloading {dest.name} from Hugging Face...")
            urllib.request.urlretrieve(url, dest)
            print(f"    → {dest}")


def synth_macos_say(text: str, voice: str):
    """Synthesize using the macOS built-in say command. Returns (aiff_bytes, 0.0, total_s).

    TTFAB is 0.0 — local synthesis starts immediately with no network round-trip.
    Requires macOS. Raises RuntimeError on Linux or if voice is unavailable.
    """
    import tempfile
    if not IS_MAC:
        raise RuntimeError("macOS say is only available on macOS")
    actual_voice = _best_macos_voice(voice)
    if actual_voice is None:
        raise RuntimeError("No suitable macOS voice found — download one in System Settings → Accessibility → Spoken Content")
    with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tf:
        tmp_path = tf.name
    try:
        t0 = time.perf_counter()
        result = subprocess.run(
            ["say", "-v", actual_voice, "-o", tmp_path, text],
            capture_output=True,
        )
        total = time.perf_counter() - t0
        if result.returncode != 0:
            raise RuntimeError(f"say failed: {result.stderr.decode().strip()}")
        audio = Path(tmp_path).read_bytes()
        if len(audio) < 100:
            raise RuntimeError(f"say produced {len(audio)} bytes — empty output")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    return audio, 0.0, total


def _best_macos_voice(preferred: str | None = None) -> str | None:
    """Return the best available US English voice, checked against say -v ? output.

    Preference order: Premium (highest quality) → Enhanced → modern neural → classic.
    Uses say -v ? to enumerate installed voices so we never silently fall back to
    a default (say accepts unknown voice names without erroring).
    """
    # Parse actually-installed voices
    result = subprocess.run(["say", "-v", "?"], capture_output=True, text=True)
    installed = set()
    for line in result.stdout.splitlines():
        name = line.split("  ")[0].strip()
        if name:
            installed.add(name)

    if preferred and preferred in installed:
        return preferred

    # Ranked preference list — Premium > Enhanced > modern neural > classic
    candidates = [
        "Ava (Premium)", "Zoe (Premium)",                    # best — download in System Settings
        "Ava (Enhanced)", "Zoe (Enhanced)",                  # good — download in System Settings
        "Flo (English (US))", "Shelley (English (US))",      # built-in neural (macOS Ventura+)
        "Reed (English (US))", "Sandy (English (US))",
        "Eddy (English (US))", "Rocko (English (US))",
        "Samantha",                                          # fallback — old compact voice
    ]
    for voice in candidates:
        if voice in installed:
            return voice
    return None


def synth_kokoro(text: str, voice: str):
    """Synthesize locally via the kokoro Python package. Returns (wav_bytes, 0.0, total_s).

    TTFAB is 0.0 — local synthesis starts immediately with no network round-trip.
    Requires: pip install kokoro soundfile
    Voice IDs follow the pattern: af_<name> (American Female), am_<name> (American Male),
      bf_<name> (British Female), bm_<name> (British Male).
    """
    import io
    import numpy as np
    import soundfile as sf
    from kokoro import KPipeline

    # lang_code: 'a' for American English, 'b' for British English
    lang_code = "b" if voice.startswith("b") else "a"
    pipeline = KPipeline(lang_code=lang_code)

    t0 = time.perf_counter()
    chunks = []
    for _, _, audio_np in pipeline(text, voice=voice, speed=1.0):
        chunks.append(audio_np)
    total = time.perf_counter() - t0

    if not chunks:
        raise RuntimeError(f"Kokoro produced no audio for voice {voice!r}")

    full_audio = np.concatenate(chunks)
    buf = io.BytesIO()
    sf.write(buf, full_audio, samplerate=24000, format="WAV")
    buf.seek(0)
    return buf.read(), 0.0, total


def is_kokoro_available() -> bool:
    try:
        import kokoro  # noqa: F401
        return True
    except ImportError:
        return False


def is_piper_available() -> bool:
    try:
        import piper.voice  # noqa: F401
        return True
    except ImportError:
        return False


def is_macos_say_available() -> bool:
    return IS_MAC and _best_macos_voice() is not None


def synthesize(provider_key: str, text: str):
    """Dispatch to the right provider. Returns (audio_bytes, ttfab_s, total_s)."""
    p = PROVIDERS[provider_key]
    if provider_key == "elevenlabs":
        return synth_elevenlabs(text, p["model"], p["voice"])
    elif provider_key in ("openai_tts1", "openai_tts1hd", "openai_mini_tts"):
        return synth_openai(text, p["model"], p["voice"])
    elif provider_key == "macos_say":
        return synth_macos_say(text, p["voice"])
    elif provider_key in ("piper", "piper_lessac"):
        return synth_piper(text, p["model"])
    elif provider_key.startswith("kokoro_"):
        return synth_kokoro(text, p["voice"])
    else:
        raise ValueError(f"Unknown provider: {provider_key}")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1: Latency
# ─────────────────────────────────────────────────────────────────────────────

def phase_latency() -> dict:
    """Measure TTFAB and total synthesis time for each provider.

    Uses a subset of short phrases to keep the run fast.
    Runs LATENCY_RUNS passes per phrase and averages them to smooth jitter.
    """
    print("\n━━━ PHASE 1: LATENCY BENCHMARK ━━━")
    print(
        f"Measuring {LATENCY_RUNS} runs × {len(LATENCY_PHRASES)} phrases "
        f"× {len(PROVIDERS)} providers...\n"
    )

    results = {}

    for provider_key, provider_info in PROVIDERS.items():
        if provider_key in ("piper", "piper_lessac") and not is_piper_available():
            print(f"  [SKIP] {provider_info['short']} — not installed (pip install piper-tts)\n")
            continue
        if provider_key == "macos_say" and not is_macos_say_available():
            print(f"  [SKIP] macOS say — not available on this platform\n")
            continue
        if provider_key.startswith("kokoro_") and not is_kokoro_available():
            print(f"  [SKIP] {provider_info['short']} — not installed (pip install kokoro)\n")
            continue

        print(f"  ─ {provider_info['label']}")
        runs = []

        for phrase_id, phrase_text in LATENCY_PHRASES:
            ttfabs, totals = [], []
            for run_n in range(1, LATENCY_RUNS + 1):
                try:
                    _, ttfab, total = synthesize(provider_key, phrase_text)
                    ttfabs.append(ttfab)
                    totals.append(total)
                    print(
                        f"    {phrase_id} [{run_n}/{LATENCY_RUNS}]  "
                        f"TTFAB={ttfab:.2f}s  total={total:.2f}s"
                    )
                except Exception as exc:
                    print(f"    {phrase_id} [{run_n}/{LATENCY_RUNS}]  ERROR: {exc}")
                time.sleep(0.5)  # avoid rate-limiting

            if ttfabs:
                runs.append({
                    "phrase":     phrase_id,
                    "ttfab_avg":  sum(ttfabs) / len(ttfabs),
                    "total_avg":  sum(totals) / len(totals),
                    "ttfab_min":  min(ttfabs),
                    "ttfab_max":  max(ttfabs),
                })

        results[provider_key] = runs
        print()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    LATENCY_FILE.write_text(json.dumps(results, indent=2))
    print(f"Saved → {LATENCY_FILE}\n")

    _print_latency_summary(results)
    return results


def _print_latency_summary(results: dict):
    print("  TTFAB averages across all test phrases:")
    for pk, runs in results.items():
        if not runs:
            continue
        avg = sum(r["ttfab_avg"] for r in runs) / len(runs)
        local = pk in ("piper", "piper_lessac", "macos_say") or pk.startswith("kokoro_")
        note = "  ← local synthesis time, no network" if local else ""
        print(f"    {PROVIDERS[pk]['short']:<22}  {avg:.2f}s avg TTFAB{note}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Clip generation
# ─────────────────────────────────────────────────────────────────────────────

def phase_clips() -> dict:
    """Generate one audio file per provider per phrase, saved to bench_results/clips/."""
    print("\n━━━ PHASE 2: CLIP GENERATION ━━━")
    print(
        f"Generating {len(PHRASES)} phrases × {len(PROVIDERS)} providers "
        f"= {len(PHRASES) * len(PROVIDERS)} clips...\n"
    )

    generated = {}

    for provider_key, provider_info in PROVIDERS.items():
        if provider_key in ("piper", "piper_lessac") and not is_piper_available():
            print(f"  [SKIP] {provider_info['short']} — not installed (pip install piper-tts)\n")
            continue
        if provider_key == "macos_say" and not is_macos_say_available():
            print(f"  [SKIP] macOS say — not available on this platform\n")
            continue
        if provider_key.startswith("kokoro_") and not is_kokoro_available():
            print(f"  [SKIP] {provider_info['short']} — not installed (pip install kokoro)\n")
            continue

        clip_dir = CLIPS_DIR / provider_key
        clip_dir.mkdir(parents=True, exist_ok=True)
        print(f"  ─ {provider_info['label']}")
        generated[provider_key] = []

        for phrase_id, phrase_text in PHRASES:
            out_path = clip_dir / f"{phrase_id}.{provider_info['ext']}"

            if out_path.exists():
                print(f"    {phrase_id}: already exists — skipping")
                generated[provider_key].append(str(out_path))
                continue

            try:
                audio, _, _ = synthesize(provider_key, phrase_text)
                out_path.write_bytes(audio)
                kb = len(audio) / 1024
                print(f"    {phrase_id}: {kb:.1f}KB → {out_path.name}")
                generated[provider_key].append(str(out_path))
            except Exception as exc:
                print(f"    {phrase_id}: ERROR — {exc}")

            time.sleep(0.3)

        print()

    print(f"Clips saved → {CLIPS_DIR}/\n")
    return generated


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Meet session — join, play clips, collect quality ratings
# ─────────────────────────────────────────────────────────────────────────────

def phase_meet(meet_url: str, only_providers: list | None = None) -> dict:
    """Join a Meet call, play every clip through WebRTC, then collect quality ratings.

    How it works:
      - The bot (your Operator account) joins via auth_state.json.
      - It plays each clip through mpv → MeetingOutput → VirtualMic → Chrome → WebRTC.
      - Chat messages announce each provider and phrase so you know what you're hearing.
      - You join the same call on your own device and just listen.
      - After all clips play, you're prompted for 1-5 quality ratings in this terminal.

    only_providers: if given, restricts playback and rating to those provider keys only.
    """
    print("\n━━━ PHASE 3: WEBRTC LISTENING TEST ━━━")

    # Check which providers have clips ready
    available = [pk for pk in PROVIDERS if (CLIPS_DIR / pk).exists()
                 and any((CLIPS_DIR / pk).iterdir())]

    if only_providers:
        missing = [pk for pk in only_providers if pk not in available]
        if missing:
            print(f"ERROR: No clips found for: {', '.join(missing)}. Run --phase clips first.")
            return {}
        available = [pk for pk in only_providers if pk in available]

    if not available:
        print("ERROR: No clips found in bench_results/clips/. Run --phase clips first.")
        return {}

    n_clips = sum(
        len([f for f in (CLIPS_DIR / pk).iterdir() if f.is_file()])
        for pk in available
    )
    est_minutes = (n_clips * 9 + 90) // 60  # ~9s per clip (announce + play + gap)

    print(f"Providers ready: {', '.join(PROVIDERS[pk]['short'] for pk in available)}")
    print(f"Total clips:     {n_clips}")
    print(f"Estimated time:  ~{est_minutes} minutes\n")
    print("What you need to do:")
    print(f"  1. Join this meeting on your device: {meet_url}")
    print("  2. The bot will join automatically and play every clip.")
    print("  3. Chat messages will announce each provider and phrase.")
    print("  4. Just listen — no interaction needed during playback.")
    print("  5. After the session you'll rate each provider here (1-5).\n")

    platform_label = "macOS (BlackHole)" if IS_MAC else "Linux (PulseAudio)"
    print(f"Platform: {platform_label}\n")

    input("Press Enter when you're ready (bot joins immediately)...")

    adapter = _get_adapter()
    adapter.join(meet_url)

    print("\nWaiting 20s for bot to join meeting...")
    time.sleep(20)

    # Announcements go to the terminal (always visible).
    # On Linux, they also go to Meet chat. MacOSAdapter.send_chat is not implemented.
    def announce(msg: str):
        print(f"\n  {msg}")
        if not IS_MAC:
            adapter.send_chat(msg)

    announce(
        f"TTS Benchmark starting — {n_clips} clips from "
        f"{len(available)} providers. Sit back and listen."
    )
    time.sleep(3)

    clip_num = 0
    for provider_key in available:
        info = PROVIDERS[provider_key]
        announce(f"━━━ Provider: {info['label']} ━━━")
        time.sleep(4)

        for phrase_id, phrase_text in PHRASES:
            clip_path = CLIPS_DIR / provider_key / f"{phrase_id}.{info['ext']}"
            if not clip_path.exists():
                continue

            clip_num += 1
            preview = phrase_text[:65] + ("..." if len(phrase_text) > 65 else "")
            announce(f"[{clip_num}/{n_clips}] \"{preview}\"")
            time.sleep(1.5)  # give a moment before audio starts

            _play_file(str(clip_path))
            time.sleep(3)    # pause after each clip

        time.sleep(5)  # longer pause between providers

    announce("✓ All clips played. Benchmark complete — thanks for listening!")
    time.sleep(3)
    adapter.leave()
    print("\nMeet session complete.\n")

    return _collect_quality_ratings(available)


def _play_file(path: str):
    """Play an audio file into the meeting audio chain.

    macOS:  mpv → BlackHole → Chrome mic → WebRTC
    Linux:  mpv → PulseAudio MeetingOutput → VirtualMic → Chrome mic → WebRTC
    """
    device = MAC_AUDIO_DEVICE if IS_MAC else LINUX_AUDIO_DEVICE
    subprocess.run(
        ["mpv", "--no-terminal", f"--audio-device={device}", "--", path],
        check=False,
    )


def _get_adapter():
    """Return the right MeetingConnector for the current platform."""
    if IS_MAC:
        from connectors.macos_adapter import MacOSAdapter
        return MacOSAdapter()
    else:
        from connectors.linux_adapter import LinuxAdapter
        return LinuxAdapter(auth_state_file=config.AUTH_STATE_FILE)


def _collect_quality_ratings(available_providers: list) -> dict:
    """Prompt for 1-5 ratings from the user, merge with any existing scores."""
    print("━━━ QUALITY RATINGS ━━━")
    print("Rate each provider's voice as heard through WebRTC in Meet.")
    print("1 = poor  2 = fair  3 = acceptable  4 = good  5 = excellent\n")

    # Load existing scores so partial re-runs accumulate rather than overwrite
    existing = json.loads(SCORES_FILE.read_text()) if SCORES_FILE.exists() else {}

    new_scores = {}
    for pk in available_providers:
        while True:
            raw = input(f"  {PROVIDERS[pk]['label']}: ").strip()
            if raw in ("1", "2", "3", "4", "5"):
                new_scores[pk] = int(raw)
                break
            print("  Enter a number from 1 to 5.")

    merged = {**existing, **new_scores}
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    SCORES_FILE.write_text(json.dumps(merged, indent=2))
    print(f"\nScores saved → {SCORES_FILE}\n")
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Phase: Sentence streaming analysis
# ─────────────────────────────────────────────────────────────────────────────

# Simulates how a 3-sentence LLM response arrives if split at sentence boundaries.
# Each entry is a cumulative slice: s1 alone, s1+s2, s1+s2+s3.
STREAMING_PHRASES = [
    ("s1",    "Got it, I can help with that."),
    ("s1s2",  "Got it, I can help with that. Let me pull up the details for you."),
    ("s1s2s3","Got it, I can help with that. Let me pull up the details for you. Based on what you've shared, the meeting is scheduled for Thursday at two PM."),
]
STREAMING_RUNS = 3


def phase_streaming() -> dict:
    """Measure whether TTFAB scales with input length — the key question for sentence streaming.

    With sentence streaming, the pipeline sends sentence 1 to TTS as soon as the LLM
    outputs it, rather than waiting for the full response. This phase answers:

      - Does a provider's TTFAB increase when given 3 sentences vs 1 sentence?
      - If TTFAB is length-independent, the full benefit of sentence streaming is
        (LLM full-response time − LLM first-sentence time) — essentially free latency.
      - If TTFAB does scale with length, there's an additional per-char startup cost.

    Local providers (macos_say, piper) are skipped — TTFAB is always 0.
    """
    print("\n━━━ PHASE: SENTENCE STREAMING ANALYSIS ━━━")
    print("Measuring TTFAB for 1-sentence, 2-sentence, and 3-sentence inputs.")
    print("Each test is run 3× and averaged.\n")

    # Network providers only — local ones always return TTFAB=0
    network_providers = [
        pk for pk in PROVIDERS
        if pk not in ("piper", "macos_say")
    ]

    results = {}

    for provider_key in network_providers:
        info = PROVIDERS[provider_key]
        print(f"  ─ {info['label']}")
        provider_results = []

        for phrase_id, phrase_text in STREAMING_PHRASES:
            ttfabs = []
            for run_n in range(1, STREAMING_RUNS + 1):
                try:
                    _, ttfab, _ = synthesize(provider_key, phrase_text)
                    ttfabs.append(ttfab)
                    n_sentences = phrase_id.count("s")
                    print(
                        f"    {n_sentences}-sentence [{run_n}/{STREAMING_RUNS}]  "
                        f"TTFAB={ttfab:.3f}s"
                    )
                except Exception as exc:
                    print(f"    {phrase_id} [{run_n}/{STREAMING_RUNS}]  ERROR: {exc}")
                time.sleep(0.5)

            if ttfabs:
                provider_results.append({
                    "phrase_id":  phrase_id,
                    "n_sentences": phrase_id.count("s"),
                    "ttfab_avg":  sum(ttfabs) / len(ttfabs),
                    "ttfab_min":  min(ttfabs),
                    "ttfab_max":  max(ttfabs),
                })

        results[provider_key] = provider_results
        print()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    STREAMING_FILE.write_text(json.dumps(results, indent=2))
    print(f"Saved → {STREAMING_FILE}\n")

    _print_streaming_summary(results)
    return results


def _print_streaming_summary(results: dict):
    print("  TTFAB by input length (does length affect streaming startup?):\n")
    print(f"  {'Provider':<26}  {'1 sentence':>12}  {'2 sentences':>12}  {'3 sentences':>12}  {'delta (1→3)':>12}")
    print(f"  {'-'*26}  {'-'*12}  {'-'*12}  {'-'*12}  {'-'*12}")

    for pk, runs in results.items():
        if not runs:
            continue
        by_n = {r["n_sentences"]: r["ttfab_avg"] for r in runs}
        s1   = by_n.get(1, float("nan"))
        s2   = by_n.get(2, float("nan"))
        s3   = by_n.get(3, float("nan"))
        delta = s3 - s1 if s1 == s1 and s3 == s3 else float("nan")
        sign  = "+" if delta > 0 else ""
        print(
            f"  {PROVIDERS[pk]['short']:<26}  "
            f"{s1:>11.3f}s  {s2:>11.3f}s  {s3:>11.3f}s  "
            f"{sign}{delta:>10.3f}s"
        )

    print()
    print("  Interpretation:")
    print("  • delta ≈ 0 → TTFAB is length-independent. Sentence streaming gives")
    print("    you the full LLM generation time for sentences 2–3 back as latency.")
    print("  • delta > 0 → Provider buffers more text before streaming; shorter")
    print("    inputs start audio sooner AND sentence streaming adds extra benefit.")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4: Report
# ─────────────────────────────────────────────────────────────────────────────

def phase_report() -> str:
    """Compile latency, cost, and quality data into bench_results/report.md."""
    print("\n━━━ PHASE 4: REPORT ━━━")

    latency = json.loads(LATENCY_FILE.read_text()) if LATENCY_FILE.exists() else {}
    scores  = json.loads(SCORES_FILE.read_text())  if SCORES_FILE.exists()  else {}

    # If no quality scores yet, offer to collect them now
    if not scores:
        print("No quality scores found (quality_scores.json missing).")
        ans = input("Collect scores now? (y/n): ").strip().lower()
        if ans == "y":
            available = [pk for pk in PROVIDERS if (CLIPS_DIR / pk).exists()]
            scores = _collect_quality_ratings(available)

    lines = [
        "# TTS Provider Benchmark — Step 7.3",
        "",
        f"**Date:** {time.strftime('%Y-%m-%d')}",
        "",
        "---",
        "",
    ]

    # ── Latency ──────────────────────────────────────────────────────────────
    lines += [
        "## Latency",
        "",
        "_TTFAB = Time to First Audio Byte (when streaming audio starts arriving)._  ",
        "_For Piper, TTFAB = 0 (local synthesis); Total = synthesis time._",
        "",
        "| Provider | TTFAB avg | TTFAB min | TTFAB max | Stream total avg |",
        "|---|---|---|---|---|",
    ]

    for pk, info in PROVIDERS.items():
        runs = latency.get(pk, [])
        if not runs:
            lines.append(f"| {info['label']} | — | — | — | — |")
            continue
        ttfab_avgs  = [r["ttfab_avg"]  for r in runs]
        total_avgs  = [r["total_avg"]  for r in runs]
        ttfab_mins  = [r["ttfab_min"]  for r in runs]
        ttfab_maxs  = [r["ttfab_max"]  for r in runs]
        note = " *(local)*" if pk == "piper" else ""
        lines.append(
            f"| {info['label']} "
            f"| {sum(ttfab_avgs)/len(ttfab_avgs):.2f}s{note} "
            f"| {min(ttfab_mins):.2f}s "
            f"| {max(ttfab_maxs):.2f}s "
            f"| {sum(total_avgs)/len(total_avgs):.2f}s |"
        )

    # ── Cost ─────────────────────────────────────────────────────────────────
    lines += [
        "",
        "---",
        "",
        "## Cost",
        "",
        "_Assumes ~150 chars per response, ~5 TTS calls per meeting._",
        "",
        "| Provider | $/1k chars | Per response | Per meeting (5×) | Per 100 meetings |",
        "|---|---|---|---|---|",
    ]

    for pk, info in PROVIDERS.items():
        cpm = info["cost_per_1k_chars"]
        if cpm == 0:
            lines.append(f"| {info['label']} | $0 | $0 | $0 | $0 |")
        else:
            per_resp = cpm * 150 / 1000
            per_meet = per_resp * 5
            per_100  = per_meet * 100
            lines.append(
                f"| {info['label']} "
                f"| ${cpm:.3f} "
                f"| ${per_resp:.4f} "
                f"| ${per_meet:.3f} "
                f"| ${per_100:.2f} |"
            )

    lines.append("")
    lines.append("_ElevenLabs price is approximate — verify at elevenlabs.io/pricing._")

    # ── Voice quality ─────────────────────────────────────────────────────────
    lines += [
        "",
        "---",
        "",
        "## Voice Quality Through WebRTC",
        "",
    ]

    if scores:
        lines += [
            "_Rated 1–5 after live WebRTC listening session in Google Meet._",
            "",
            "| Provider | Quality (1–5) |",
            "|---|---|",
        ]
        for pk, info in PROVIDERS.items():
            score = scores.get(pk, "—")
            lines.append(f"| {info['label']} | {score} |")
    else:
        lines.append(
            "_No quality scores recorded. Run `--phase meet` to conduct the listening test._"
        )

    # ── Sentence streaming results ────────────────────────────────────────────
    streaming = json.loads(STREAMING_FILE.read_text()) if STREAMING_FILE.exists() else {}
    if streaming:
        lines += [
            "",
            "---",
            "",
            "## Sentence Streaming Analysis",
            "",
            "_Does TTFAB scale with input length? If delta ≈ 0, sentence streaming gives_",
            "_the full LLM generation time for sentences 2–3 back as free latency savings._",
            "",
            "| Provider | TTFAB (1 sentence) | TTFAB (2 sentences) | TTFAB (3 sentences) | Delta (1→3) |",
            "|---|---|---|---|---|",
        ]
        for pk, runs in streaming.items():
            if not runs:
                continue
            by_n = {r["n_sentences"]: r["ttfab_avg"] for r in runs}
            s1 = by_n.get(1); s2 = by_n.get(2); s3 = by_n.get(3)
            delta = (s3 - s1) if s1 is not None and s3 is not None else None
            sign = "+" if delta and delta > 0 else ""
            lines.append(
                f"| {PROVIDERS[pk]['label']} "
                f"| {f'{s1:.3f}s' if s1 is not None else '—'} "
                f"| {f'{s2:.3f}s' if s2 is not None else '—'} "
                f"| {f'{s3:.3f}s' if s3 is not None else '—'} "
                f"| {f'{sign}{delta:.3f}s' if delta is not None else '—'} |"
            )

    # ── Summary comparison ────────────────────────────────────────────────────
    _vendor = {
        "elevenlabs":       "ElevenLabs (new vendor)",
        "openai_tts1":      "None (already using OpenAI)",
        "openai_tts1hd":    "None (already using OpenAI)",
        "openai_mini_tts":  "None (already using OpenAI)",
        "macos_say":        "None (macOS built-in)",
        "piper":            "None (local, no API)",
    }
    _risk = {
        "elevenlabs":       "API outage",
        "openai_tts1":      "API outage",
        "openai_tts1hd":    "API outage",
        "openai_mini_tts":  "API outage",
        "macos_say":        "macOS-only, no Linux",
        "piper":            "None",
    }

    lines += [
        "",
        "---",
        "",
        "## Summary",
        "",
        "| Provider | Quality | TTFAB avg | Cost/meeting | Extra vendor | Failure risk |",
        "|---|---|---|---|---|---|",
    ]

    for pk, info in PROVIDERS.items():
        quality_str = f"{scores[pk]}/5" if pk in scores else "—"
        runs = latency.get(pk, [])
        if runs:
            ttfab_str = f"{sum(r['ttfab_avg'] for r in runs)/len(runs):.2f}s"
        else:
            ttfab_str = "—"
        cpm = info["cost_per_1k_chars"]
        cost_str = f"${cpm * 150 / 1000 * 5:.3f}" if cpm > 0 else "$0"
        lines.append(
            f"| {info['label']} | {quality_str} | {ttfab_str} "
            f"| {cost_str} | {_vendor[pk]} | {_risk[pk]} |"
        )

    # ── Recommendation ────────────────────────────────────────────────────────
    lines += [
        "",
        "---",
        "",
        "## Recommendation",
        "",
    ]

    if scores:
        # Score by: quality (highest weight) → lowest cost → fewest vendors
        def rank(pk):
            q = scores.get(pk, 0)
            cost_penalty = PROVIDERS[pk]["cost_per_1k_chars"]
            vendor_penalty = 1 if pk == "elevenlabs" else 0
            return (q, -cost_penalty, -vendor_penalty)

        best_quality   = max(scores, key=lambda k: scores[k])
        best_practical = max((pk for pk in scores), key=rank)

        lines += [
            f"**Highest quality:** {PROVIDERS[best_quality]['label']} "
            f"(score {scores[best_quality]}/5)",
            "",
            f"**Best practical choice:** {PROVIDERS[best_practical]['label']}",
            "",
            "_Practical ranking weights quality first, then cost, then vendor count._",
        ]
    else:
        lines.append(
            "_Run `--phase meet` to collect quality scores and complete this section._"
        )

    # ── Clip index ────────────────────────────────────────────────────────────
    lines += [
        "",
        "---",
        "",
        "## Audio Clips",
        "",
        "Pre-WebRTC reference clips (synthesized direct from each API, no WebRTC compression):",
        "",
    ]
    for pk, info in PROVIDERS.items():
        clip_dir = CLIPS_DIR / pk
        if clip_dir.exists():
            n = len([f for f in clip_dir.iterdir() if f.is_file()])
            rel = clip_dir.relative_to(ROOT)
            lines.append(f"- `{rel}/` — {n} clips ({info['label']})")

    report = "\n".join(lines) + "\n"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(report)

    print(f"Report saved → {REPORT_FILE}\n")
    print(report)
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TTS Provider Benchmark — Step 7.3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--phase",
        choices=["latency", "clips", "meet", "streaming", "report", "all"],
        default="all",
        help="Which phase to run (default: all = latency + clips + meet + report)",
    )
    parser.add_argument(
        "--meet-url",
        metavar="URL",
        help="Google Meet URL for the WebRTC listening session (required for --phase meet)",
    )
    parser.add_argument(
        "--providers",
        metavar="KEY",
        nargs="+",
        help=(
            f"Limit meet phase to specific provider keys. "
            f"Available: {', '.join(PROVIDERS.keys())}"
        ),
    )
    args = parser.parse_args()

    phases = (
        ["latency", "clips", "meet", "report"]
        if args.phase == "all"
        else [args.phase]
    )

    # If meet is requested but no URL, drop it (or error if explicitly requested)
    if "meet" in phases and not args.meet_url:
        if args.phase == "all":
            print(
                "NOTE: --meet-url not provided. Skipping WebRTC listening session.\n"
                "      Run `--phase meet --meet-url URL` separately when ready.\n"
            )
            phases = ["latency", "clips", "report"]
        else:
            parser.error("--meet-url is required for --phase meet")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if "latency" in phases:
        phase_latency()

    if "clips" in phases:
        phase_clips()

    if "meet" in phases:
        phase_meet(args.meet_url, only_providers=args.providers)

    if "streaming" in phases:
        phase_streaming()

    if "report" in phases:
        phase_report()

    print("✓ Done.")


if __name__ == "__main__":
    main()
