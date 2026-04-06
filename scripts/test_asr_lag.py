"""
Measure Google Meet ASR-to-DOM lag.

Plays "Hey Operator" through BlackHole and auto-reads /tmp/operator.log
to compute the lag from clip-end to caption appearance.

IMPORTANT: Run ONCE per fresh meeting session. The caption block must be
empty at the start — if "Hey Operator" is already in the accumulated
captions from a prior play, wake fires immediately on old text and the
measurement is contaminated.

Usage:
    source venv/bin/activate
    python scripts/test_asr_lag.py

Operator must be running and joined to a Meet session.
Run this from a second terminal.
"""

import io
import json
import re
import subprocess
import sys
import time
from datetime import datetime

import numpy as np
import soundfile as sf

BLACKHOLE_DEVICE = "coreaudio/BlackHole2ch_UID"
TEST_PHRASE = "Operator"
CLIP_PATH = "/tmp/asr_lag_test_clip.mp3"
OPERATOR_LOG = "/tmp/operator.log"

# Matches log lines like: 2026-04-06 11:33:02,019 INFO ... caption_wake_detected
_WAKE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3}) .*caption_wake_detected"
)


def generate_clip():
    print("Generating test clip via Kokoro...")
    from kokoro import KPipeline
    pipeline = KPipeline(lang_code="a")
    chunks = []
    for _, _, audio_np in pipeline(TEST_PHRASE, voice="af_heart", speed=1.0):
        chunks.append(audio_np)
    if not chunks:
        print("ERROR: Kokoro produced no audio", file=sys.stderr)
        sys.exit(1)
    audio = np.concatenate(chunks)
    buf = io.BytesIO()
    sf.write(buf, audio, 24000, format="WAV")
    wav_bytes = buf.getvalue()

    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", "pipe:0",
            "-af", (
                "silenceremove=start_periods=1:start_threshold=-40dB,"
                "areverse,silenceremove=start_periods=1:start_threshold=-40dB,areverse"
            ),
            "-codec:a", "libmp3lame", "-q:a", "2", CLIP_PATH,
        ],
        input=wav_bytes,
        capture_output=True,
    )
    if result.returncode != 0:
        print("ERROR: ffmpeg failed:", result.stderr.decode()[:300], file=sys.stderr)
        sys.exit(1)


def get_duration(path):
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
        capture_output=True,
    )
    info = json.loads(result.stdout)
    return float(info["streams"][0]["duration"])


def parse_log_timestamp(date_str, ms_str):
    """Parse operator log timestamp to Unix float."""
    dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    return dt.timestamp() + int(ms_str) / 1000.0


def find_wake_in_log(after_epoch):
    """Return the first caption_wake_detected timestamp after after_epoch."""
    try:
        with open(OPERATOR_LOG) as f:
            for line in f:
                m = _WAKE_RE.match(line)
                if m:
                    t = parse_log_timestamp(m.group(1), m.group(2))
                    if t > after_epoch:
                        return t
    except FileNotFoundError:
        print(f"ERROR: {OPERATOR_LOG} not found", file=sys.stderr)
    return None


def fmt(epoch):
    return time.strftime("%H:%M:%S", time.localtime(epoch)) + f".{int((epoch % 1) * 1000):03d}"


def main():
    generate_clip()
    duration = get_duration(CLIP_PATH)
    print(f"Clip duration : {duration:.4f}s")
    print(f"Phrase        : \"{TEST_PHRASE}\"")
    print()
    print("Playing in 2 seconds...")
    time.sleep(2)

    t_play_start = time.time()
    print(f"T_play_start  : {fmt(t_play_start)}")

    subprocess.run(
        ["mpv", "--no-terminal", f"--audio-device={BLACKHOLE_DEVICE}", "--", CLIP_PATH],
        check=False,
    )

    t_mpv_exit = time.time()
    t_clip_end = t_play_start + duration

    print(f"T_clip_end    : {fmt(t_clip_end)}  (play_start + ffprobe duration)")
    print(f"T_mpv_exit    : {fmt(t_mpv_exit)}  (+{t_mpv_exit - t_clip_end:.3f}s after clip_end)")
    print()

    # Give ASR a moment to propagate before reading the log
    print("Waiting for ASR to propagate...")
    time.sleep(2)

    t_wake = find_wake_in_log(after_epoch=t_play_start)
    if t_wake is None:
        print("caption_wake_detected not found in log after T_play_start.")
        print("Check that Operator is running and captions are enabled.")
        return

    lag_from_clip_end = t_wake - t_clip_end
    lag_from_mpv_exit = t_wake - t_mpv_exit

    print(f"caption_wake_detected : {fmt(t_wake)}")
    print()
    print(f"ASR lag (from clip_end) : {lag_from_clip_end * 1000:.0f}ms")
    print(f"ASR lag (from mpv_exit) : {lag_from_mpv_exit * 1000:.0f}ms")
    print()
    if lag_from_clip_end < 0:
        print("WARNING: wake detected before clip_end — caption block may be contaminated")
        print("  (wake fired on accumulated text from a previous play)")
        print("  Rejoin the meeting and run again with a clean caption block.")
    elif lag_from_clip_end < 0.1:
        print("WARNING: lag suspiciously low — caption block may be contaminated.")
        print("  Rejoin the meeting and run again.")


if __name__ == "__main__":
    main()
