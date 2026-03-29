"""
Benchmark STT engines against recorded clips.
Usage: python benchmark_stt.py [--engine whisper-base|whisper-small|distil-whisper|mlx-whisper|all]
"""
import os
import sys
import time
import json
import numpy as np
import soundfile as sf

CLIPS_DIR = "benchmark_clips"
GROUND_TRUTH_FILE = os.path.join(CLIPS_DIR, "ground_truth.txt")
RESULTS_FILE = "benchmark_stt_results.json"


def load_ground_truth():
    gt = {}
    with open(GROUND_TRUTH_FILE) as f:
        for line in f:
            fname, text = line.strip().split("|", 1)
            gt[fname] = text
    return gt


def word_error_rate(ref: str, hyp: str) -> float:
    ref_words = ref.lower().split()
    hyp_words = hyp.lower().split()
    r, h = len(ref_words), len(hyp_words)
    d = [[0] * (h + 1) for _ in range(r + 1)]
    for i in range(r + 1):
        d[i][0] = i
    for j in range(h + 1):
        d[0][j] = j
    for i in range(1, r + 1):
        for j in range(1, h + 1):
            if ref_words[i - 1] == hyp_words[j - 1]:
                d[i][j] = d[i - 1][j - 1]
            else:
                d[i][j] = 1 + min(d[i - 1][j], d[i][j - 1], d[i - 1][j - 1])
    return d[r][h] / max(r, 1)


def _run_engine(name, clips, ground_truth, transcribe_fn):
    results = []
    for fname in clips:
        path = os.path.join(CLIPS_DIR, fname)
        audio_dur = len(sf.read(path)[0]) / 16000
        t0 = time.perf_counter()
        text = transcribe_fn(path)
        elapsed = time.perf_counter() - t0
        gt = ground_truth.get(fname, "")
        wer = word_error_rate(gt, text)
        rtf = elapsed / audio_dur if audio_dur > 0 else 0
        results.append({
            "clip": fname, "engine": name,
            "transcript": text, "ground_truth": gt,
            "latency_s": round(elapsed, 3), "rtf": round(rtf, 3),
            "wer": round(wer, 3), "audio_dur_s": round(audio_dur, 2),
        })
        print(f"  {fname}: {elapsed:.3f}s (RTF {rtf:.2f}) WER={wer:.1%} — \"{text}\"")
    return results


def benchmark_whisper_base(clips, ground_truth):
    from faster_whisper import WhisperModel
    print("\n=== faster-whisper base (cpu, int8) ===")
    model = WhisperModel("base", device="cpu", compute_type="int8")
    def transcribe(path):
        segs, _ = model.transcribe(path, language="en")
        return " ".join(s.text.strip() for s in segs)
    return _run_engine("whisper-base", clips, ground_truth, transcribe)


def benchmark_whisper_small(clips, ground_truth):
    from faster_whisper import WhisperModel
    print("\n=== faster-whisper small (cpu, int8) ===")
    model = WhisperModel("small", device="cpu", compute_type="int8")
    def transcribe(path):
        segs, _ = model.transcribe(path, language="en")
        return " ".join(s.text.strip() for s in segs)
    return _run_engine("whisper-small", clips, ground_truth, transcribe)


def benchmark_distil_whisper(clips, ground_truth):
    from faster_whisper import WhisperModel
    print("\n=== distil-whisper large-v3 (cpu, int8) ===")
    model = WhisperModel("distil-large-v3", device="cpu", compute_type="int8")
    def transcribe(path):
        segs, _ = model.transcribe(path, language="en")
        return " ".join(s.text.strip() for s in segs)
    return _run_engine("distil-large-v3", clips, ground_truth, transcribe)


def benchmark_mlx_whisper(clips, ground_truth):
    import mlx_whisper
    print("\n=== mlx-whisper base (Apple Silicon) ===")
    # Warm up model load
    mlx_whisper.transcribe("benchmark_clips/clip_01.wav", path_or_hf_repo="mlx-community/whisper-base-mlx")
    print("  (model loaded, starting benchmark)")
    def transcribe(path):
        result = mlx_whisper.transcribe(path, path_or_hf_repo="mlx-community/whisper-base-mlx", language="en")
        return result["text"].strip()
    return _run_engine("mlx-whisper-base", clips, ground_truth, transcribe)


def print_summary(all_results):
    engines = list(dict.fromkeys(r["engine"] for r in all_results))  # preserve order
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  {'Engine':<22} {'Avg Latency':>11} {'Avg RTF':>9} {'Avg WER':>9}")
    print(f"  {'-'*22} {'-'*11} {'-'*9} {'-'*9}")
    for engine in engines:
        ers = [r for r in all_results if r["engine"] == engine]
        avg_lat = np.mean([r["latency_s"] for r in ers])
        avg_rtf = np.mean([r["rtf"] for r in ers])
        avg_wer = np.mean([r["wer"] for r in ers])
        print(f"  {engine:<22} {avg_lat:>10.3f}s {avg_rtf:>9.3f} {avg_wer:>8.1%}")


ENGINES = {
    "whisper-base": benchmark_whisper_base,
    "whisper-small": benchmark_whisper_small,
    "distil-whisper": benchmark_distil_whisper,
    "mlx-whisper": benchmark_mlx_whisper,
}

if __name__ == "__main__":
    engine = "all"
    if "--engine" in sys.argv:
        idx = sys.argv.index("--engine")
        engine = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "all"

    ground_truth = load_ground_truth()
    clips = sorted(ground_truth.keys())
    all_results = []

    if engine == "all":
        for fn in ENGINES.values():
            all_results.extend(fn(clips, ground_truth))
    elif engine in ENGINES:
        all_results.extend(ENGINES[engine](clips, ground_truth))
    else:
        print(f"Unknown engine: {engine}. Options: {', '.join(ENGINES.keys())}, all")
        sys.exit(1)

    print_summary(all_results)

    with open(RESULTS_FILE, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults saved to {RESULTS_FILE}")
