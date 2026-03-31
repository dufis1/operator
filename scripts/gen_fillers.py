"""
Generate filler audio clips for Operator using Kokoro Heart voice.

Run with:
    source venv/bin/activate
    python3.11 scripts/gen_fillers.py

Output: assets/fillers/{neutral,cerebral,empathetic}/filler_NN.mp3
"""
import io
import os
import subprocess
import sys

import numpy as np
import soundfile as sf
from kokoro import KPipeline

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILLERS = {
    "neutral": [
        "On it!",
        "Yeah.",
        "Sure.",
        "Alright.",
        "Right...",
        "Let's see...",
        "Yep!",
        "For sure.",
        "Gotcha.",
        "Cool.",
        "Sure thing.",
        "I know this.",
        "Gotchyu."
    ],
    "cerebral": [
        "Let me think...",
        "Good question!",
        "Let's see...",
        "Interesting...",
        "Let me check...",
        "One moment...",
        "So...",
        "I'm digging into this. Okay...",
        "Composing my thoughts...",
        "I have an answer for you.",
        "Here's what I found."

    ],
    "empathetic": [
        "I hear you.",
        "Of course.",
        "Gotcha.",
        "I know exactly what you mean.",
        "I get that.",
        "Totally.",
        "Yeah... okay.",
        "Sure.",
        "For sure.",
        "I get what you're saying."
    ],
}


def synthesize(pipeline, text, voice="af_heart"):
    chunks = []
    for _, _, audio_np in pipeline(text, voice=voice, speed=1.0):
        chunks.append(audio_np)
    if not chunks:
        return None
    audio = np.concatenate(chunks)
    buf = io.BytesIO()
    sf.write(buf, audio, 24000, format="WAV")
    return buf.getvalue()


def main():
    print("Loading Kokoro pipeline...")
    pipeline = KPipeline(lang_code="a")
    voice = "af_heart"

    for bucket, phrases in FILLERS.items():
        out_dir = os.path.join(_BASE, "assets", "fillers", bucket)
        os.makedirs(out_dir, exist_ok=True)
        print(f"\n[{bucket}]")
        for i, phrase in enumerate(phrases):
            out_path = os.path.join(out_dir, f"filler_{i:02d}.mp3")
            wav = synthesize(pipeline, phrase, voice=voice)
            if wav is None:
                print(f"  SKIP ({phrase!r}) — no audio produced")
                continue
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", "pipe:0",
                 "-af", "silenceremove=start_periods=1:start_threshold=-40dB,"
                        "areverse,silenceremove=start_periods=1:start_threshold=-40dB,areverse",
                 "-codec:a", "libmp3lame", "-q:a", "2", out_path],
                input=wav,
                capture_output=True,
            )
            if result.returncode == 0:
                print(f"  OK filler_{i:02d}.mp3  {phrase!r}")
            else:
                print(f"  FAIL filler_{i:02d}.mp3  {phrase!r}")
                print(result.stderr.decode()[:200])

    print("\nDone. Clips saved to assets/fillers/")


if __name__ == "__main__":
    main()
