"""
Inner benchmark script — runs INSIDE the Docker container.

Invoked by Dockerfile.probe_b2's ENTRYPOINT.
Does two things:
  1. Starts PulseAudio and checks whether it's running.
  2. Transcribes each clip in /app/benchmark_clips/ with faster-whisper base,
     computes WER against ground_truth.json.

Prints a single JSON object to stdout, then exits 0 (pass) or 1 (fail).
"""

import json
import subprocess
import sys
from pathlib import Path

CLIPS_DIR = Path("/app/benchmark_clips")
GROUND_TRUTH_FILE = CLIPS_DIR / "ground_truth.json"
WER_THRESHOLD = 0.20


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref_words = reference.lower().split()
    hyp_words = hypothesis.lower().split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
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


def check_pulseaudio() -> bool:
    """Start PulseAudio with a null sink and verify it responds."""
    try:
        subprocess.run(
            ["pulseaudio", "--start", "--exit-idle-time=-1"],
            capture_output=True, text=True, timeout=10,
        )
        check = subprocess.run(
            ["pulseaudio", "--check"],
            capture_output=True, text=True, timeout=5,
        )
        return check.returncode == 0
    except Exception:
        return False


def main():
    output = {"pulseaudio_ok": False, "clips": {}, "avg_wer": None, "pass": False}

    output["pulseaudio_ok"] = check_pulseaudio()

    with open(GROUND_TRUTH_FILE) as f:
        ground_truth = json.load(f)

    from faster_whisper import WhisperModel
    model = WhisperModel("base", device="cpu", compute_type="int8")

    wers = []
    for clip_path in sorted(CLIPS_DIR.glob("clip_*.wav")):
        gt = ground_truth.get(clip_path.name, "")
        segments, _ = model.transcribe(
            str(clip_path),
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
        )
        text = " ".join(seg.text.strip() for seg in segments)
        wer = word_error_rate(gt, text)
        wers.append(wer)
        output["clips"][clip_path.name] = {
            "ground_truth": gt,
            "transcript": text,
            "wer": round(wer, 4),
        }

    if wers:
        avg_wer = sum(wers) / len(wers)
        output["avg_wer"] = round(avg_wer, 4)
        output["pass"] = output["pulseaudio_ok"] and avg_wer <= WER_THRESHOLD

    print(json.dumps(output, indent=2))
    sys.exit(0 if output["pass"] else 1)


if __name__ == "__main__":
    main()
