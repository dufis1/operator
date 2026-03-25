#!/usr/bin/env python3
"""
STT Provider Benchmark — compare transcription accuracy across providers
using real meeting audio captured via capture_clips.py.

Usage:
    python benchmark_stt.py

Reads WAV files from benchmark_clips/, prompts for ground truth if not
already in ground_truth.json, runs each clip through all available providers,
and prints a comparison table.

Providers with missing API keys are skipped automatically.
"""

import json
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np

# ── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
CLIPS_DIR = SCRIPT_DIR / "benchmark_clips"
GROUND_TRUTH_FILE = CLIPS_DIR / "ground_truth.json"
RESULTS_FILE = SCRIPT_DIR / "benchmark_results.json"

# ── Load env ───────────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(SCRIPT_DIR / ".env")


# ═══════════════════════════════════════════════════════════════════════════
# Word Error Rate
# ═══════════════════════════════════════════════════════════════════════════

def word_error_rate(reference: str, hypothesis: str) -> float:
    """Compute WER using Levenshtein distance on word sequences."""
    ref_words = reference.lower().split()
    hyp_words = hypothesis.lower().split()

    if not ref_words:
        return 0.0 if not hyp_words else 1.0

    # Dynamic programming — edit distance
    d = [[0] * (len(hyp_words) + 1) for _ in range(len(ref_words) + 1)]
    for i in range(len(ref_words) + 1):
        d[i][0] = i
    for j in range(len(hyp_words) + 1):
        d[0][j] = j

    for i in range(1, len(ref_words) + 1):
        for j in range(1, len(hyp_words) + 1):
            if ref_words[i - 1] == hyp_words[j - 1]:
                d[i][j] = d[i - 1][j - 1]
            else:
                d[i][j] = 1 + min(d[i - 1][j], d[i][j - 1], d[i - 1][j - 1])

    return d[len(ref_words)][len(hyp_words)] / len(ref_words)


def detect_wake_phrase(text: str) -> bool:
    """Check if 'operator' appears in the transcript."""
    return "operator" in text.lower()


# ═══════════════════════════════════════════════════════════════════════════
# Provider implementations
# ═══════════════════════════════════════════════════════════════════════════

def transcribe_faster_whisper(audio_path: str, model_size: str) -> dict:
    """Transcribe using faster-whisper (local CPU)."""
    from faster_whisper import WhisperModel

    t0 = time.time()
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(
        audio_path,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
    )
    text = " ".join(seg.text.strip() for seg in segments)
    latency = time.time() - t0

    return {"text": text, "latency": latency}


def transcribe_mlx_whisper(audio_path: str) -> dict:
    """Transcribe using mlx-whisper (local Metal GPU)."""
    import mlx_whisper

    t0 = time.time()
    result = mlx_whisper.transcribe(
        audio_path,
        path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
    )
    text = result.get("text", "").strip()
    latency = time.time() - t0

    return {"text": text, "latency": latency}


def transcribe_deepgram(audio_path: str) -> dict:
    """Transcribe using Deepgram Nova-3 (cloud)."""
    from deepgram import DeepgramClient

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        return None

    client = DeepgramClient(api_key=api_key)

    with open(audio_path, "rb") as f:
        audio_data = f.read()

    t0 = time.time()
    response = client.listen.v1.media.transcribe_file(
        request=audio_data,
        model="nova-3",
        smart_format=True,
    )
    text = response.results.channels[0].alternatives[0].transcript
    latency = time.time() - t0

    return {"text": text, "latency": latency}


def transcribe_assemblyai(audio_path: str) -> dict:
    """Transcribe using AssemblyAI (cloud)."""
    import assemblyai as aai

    api_key = os.getenv("ASSEMBLYAI_API_KEY")
    if not api_key:
        return None

    aai.settings.api_key = api_key

    t0 = time.time()
    config = aai.TranscriptionConfig(speech_models=["universal-3-pro"])
    transcriber = aai.Transcriber(config=config)
    transcript = transcriber.transcribe(audio_path)

    if transcript.status == aai.TranscriptStatus.error:
        return {"text": f"[ERROR: {transcript.error}]", "latency": time.time() - t0}

    text = transcript.text or ""
    latency = time.time() - t0

    return {"text": text, "latency": latency}


def transcribe_speechmatics(audio_path: str) -> dict:
    """Transcribe using Speechmatics Batch (cloud)."""
    import asyncio
    api_key = os.getenv("SPEECHMATICS_API_KEY")
    if not api_key:
        return None

    from speechmatics.batch import AsyncClient, TranscriptionConfig

    async def _run():
        client = AsyncClient(api_key=api_key)
        async with client:
            transcript = await client.transcribe(
                audio_path,
                transcription_config=TranscriptionConfig(language="en"),
            )
            return transcript

    t0 = time.time()
    transcript = asyncio.run(_run())

    # Extract text from transcript object
    if hasattr(transcript, 'results'):
        results = transcript.results
    elif isinstance(transcript, dict):
        results = transcript.get("results", [])
    else:
        results = []

    words = []
    for r in results:
        # Handle both dict and Speechmatics model objects
        if isinstance(r, dict):
            rtype = r.get("type")
            content = r["alternatives"][0]["content"] if r.get("alternatives") else ""
        else:
            rtype = getattr(r, "type", None)
            content = r.alternatives[0].content if getattr(r, "alternatives", None) else ""
        if rtype == "word":
            words.append(content)
    text = " ".join(words)
    latency = time.time() - t0

    return {"text": text, "latency": latency}


# ═══════════════════════════════════════════════════════════════════════════
# Provider registry
# ═══════════════════════════════════════════════════════════════════════════

PROVIDERS = [
    {
        "name": "faster-whisper base (current)",
        "fn": lambda path: transcribe_faster_whisper(path, "base"),
        "type": "local",
        "requires_key": None,
    },
    {
        "name": "faster-whisper turbo",
        "fn": lambda path: transcribe_faster_whisper(path, "turbo"),
        "type": "local",
        "requires_key": None,
    },
    {
        "name": "mlx-whisper large-v3-turbo",
        "fn": transcribe_mlx_whisper,
        "type": "local (Metal)",
        "requires_key": None,
    },
    {
        "name": "Deepgram Nova-3",
        "fn": transcribe_deepgram,
        "type": "cloud",
        "requires_key": "DEEPGRAM_API_KEY",
    },
    {
        "name": "AssemblyAI",
        "fn": transcribe_assemblyai,
        "type": "cloud",
        "requires_key": "ASSEMBLYAI_API_KEY",
    },
    {
        "name": "Speechmatics",
        "fn": transcribe_speechmatics,
        "type": "cloud",
        "requires_key": "SPEECHMATICS_API_KEY",
    },
]


# ═══════════════════════════════════════════════════════════════════════════
# Main benchmark logic
# ═══════════════════════════════════════════════════════════════════════════

def load_clips():
    """Find all WAV files in benchmark_clips/, sorted by name."""
    clips = sorted(CLIPS_DIR.glob("clip_*.wav"))
    if not clips:
        print(f"No clips found in {CLIPS_DIR}/")
        print("Run capture_clips.py first to record test phrases.")
        sys.exit(1)
    return clips


def load_ground_truth():
    """Load or create ground truth JSON."""
    if GROUND_TRUTH_FILE.exists():
        with open(GROUND_TRUTH_FILE) as f:
            return json.load(f)
    return {}


def save_ground_truth(gt):
    with open(GROUND_TRUTH_FILE, "w") as f:
        json.dump(gt, f, indent=2)


def prompt_ground_truth(clips, existing_gt):
    """Prompt user for ground truth text for each clip."""
    gt = dict(existing_gt)
    missing = [c for c in clips if c.name not in gt]

    if not missing:
        return gt

    print("─" * 60)
    print("Enter ground truth for each clip (what you actually said).")
    print("Press Enter to play the clip first (requires 'afplay').\n")

    for clip in missing:
        # Show clip info
        with wave.open(str(clip), "rb") as wf:
            duration = wf.getnframes() / wf.getframerate()
        print(f"  {clip.name} ({duration:.1f}s)")

        # Offer playback
        play = input("  Play clip? [Y/n]: ").strip().lower()
        if play != "n":
            os.system(f"afplay '{clip}'")

        text = input("  Ground truth: ").strip()
        if text:
            gt[clip.name] = text
        else:
            print("  (skipped)")

    save_ground_truth(gt)
    print()
    return gt


def run_benchmark(clips, ground_truth):
    """Run all clips through all available providers."""
    results = {}

    # Check which providers are available
    available = []
    for p in PROVIDERS:
        key = p["requires_key"]
        if key and not os.getenv(key):
            print(f"  Skipping {p['name']} (no {key})")
        else:
            available.append(p)

    print(f"\nBenchmarking {len(clips)} clip(s) × {len(available)} provider(s)...\n")

    for clip in clips:
        clip_name = clip.name
        clip_path = str(clip)
        gt = ground_truth.get(clip_name, "")
        results[clip_name] = {"ground_truth": gt, "providers": {}}

        with wave.open(clip_path, "rb") as wf:
            duration = wf.getnframes() / wf.getframerate()

        print(f"{'─' * 60}")
        print(f"  {clip_name} ({duration:.1f}s)")
        if gt:
            print(f"  Ground truth: {gt}")
        print()

        for provider in available:
            name = provider["name"]
            print(f"    {name}...", end=" ", flush=True)

            try:
                result = provider["fn"](clip_path)
                if result is None:
                    print("skipped (no API key)")
                    continue

                text = result["text"]
                latency = result["latency"]
                wer = word_error_rate(gt, text) if gt else None
                wake = detect_wake_phrase(text)

                print(f"{latency:.2f}s")
                print(f"      Text: {text}")
                if wer is not None:
                    print(f"      WER:  {wer:.1%}")
                print(f"      Wake: {'YES' if wake else 'no'}")

                results[clip_name]["providers"][name] = {
                    "text": text,
                    "latency_s": round(latency, 3),
                    "wer": round(wer, 4) if wer is not None else None,
                    "wake_detected": wake,
                }

            except Exception as e:
                print(f"ERROR: {e}")
                results[clip_name]["providers"][name] = {"error": str(e)}

        print()

    return results


def print_summary(results):
    """Print a summary table across all clips."""
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    # Aggregate stats per provider
    provider_stats = {}
    for clip_name, clip_data in results.items():
        for pname, pdata in clip_data["providers"].items():
            if "error" in pdata:
                continue
            if pname not in provider_stats:
                provider_stats[pname] = {"wers": [], "latencies": [], "wakes": 0, "total": 0}
            stats = provider_stats[pname]
            stats["total"] += 1
            stats["latencies"].append(pdata["latency_s"])
            if pdata.get("wer") is not None:
                stats["wers"].append(pdata["wer"])
            if pdata.get("wake_detected"):
                stats["wakes"] += 1

    # Print table
    print(f"\n{'Provider':<32} {'Avg WER':>8} {'Avg Lat':>8} {'Wake':>6}")
    print("─" * 60)

    for pname, stats in provider_stats.items():
        avg_wer = np.mean(stats["wers"]) if stats["wers"] else float("nan")
        avg_lat = np.mean(stats["latencies"]) if stats["latencies"] else float("nan")
        wake_str = f"{stats['wakes']}/{stats['total']}"

        wer_str = f"{avg_wer:.1%}" if not np.isnan(avg_wer) else "n/a"
        lat_str = f"{avg_lat:.2f}s" if not np.isnan(avg_lat) else "n/a"

        print(f"  {pname:<30} {wer_str:>8} {lat_str:>8} {wake_str:>6}")

    print()


def main():
    clips = load_clips()
    print(f"Found {len(clips)} clip(s) in {CLIPS_DIR}/\n")

    # Ground truth
    gt = load_ground_truth()
    gt = prompt_ground_truth(clips, gt)

    # Run benchmark
    results = run_benchmark(clips, gt)

    # Save results
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Results saved to {RESULTS_FILE}")

    # Summary
    print_summary(results)


if __name__ == "__main__":
    main()
