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
        "On it.",
        "Okay, give me just a moment.",
        "Right, one sec.",
        "Yeah, let me work through that.",
        "Alright, just a moment.",
        "Let me think...",
        "Sure thing, one sec.",
        "Let me put that together.",
        "Yeah, give me just a sec.",
        "Let me sort through this.",
        "Yeah.",
        "For sure.",
        "Cool. Let me look into that.",
        "Here's what I got for you.",
    ],
    "cerebral": [
        "That's a good question. Let me be precise about this.",
        "Interesting. Let me work through this.",
        "I want to make sure I get this right.",
        "Give me a moment to think through that properly.",
        "There's a few angles here.",
        "This one's got some layers. Give me a sec.",
        "Okay, let me consider the angles here.",
        "I want to get you the right answer, so give me a sec.",
        "Hmm. Let me think through that.",
        "Let me work through this one.",
        "Good question. Give me a moment.",
        "I want to give you a thorough answer, one sec.",
        "I'm digging, just give me a sec here.",
        "Just composing my findings. Almost have it. Here.",
        "Okay, I have thoughts.",
    ],
    "empathetic": [
        "I hear you.",
        "Right. I understand.",
        "Yeah, that makes total sense.",
        "Gotcha. Yeah, let me think on that.",
        "I hear you. Let me think about that for a moment.",
        "Yeah, I get that. Give me a sec.",
        "That's a fair point. Let me think.",
        "I understand. One moment.",
        "I hear you on that.",
        "Right, I want to give you a thoughtful answer here.",
        "Yeah, absolutely. Give me a sec.",
        "Mm, gotcha. Let me sit with that.",
        "Totally. Totally.",
        "Mhmm. Mhmmm.",
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
