"""
Audit all filler and acknowledgment phrases by playing each clip
and printing the phrase text. 1-second pause between clips.

Also plays a live-generated example of a full operator answer
(filler → response) using the same Kokoro voice.

Usage:
    source venv/bin/activate
    python scripts/audit_fillers.py
"""
import io
import os
import subprocess
import tempfile
import time

import numpy as np
import soundfile as sf
from kokoro import KPipeline

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Filler phrases mapped to their files (from gen_fillers.py)
FILLERS = {
    "neutral": [
        "On it!",
        "Let's see...",
        "For sure.",
        "I gotcha.",
        "Sure thing.",
        "I know this.",
    ],
    "cerebral": [
        "Let me think...",
        "Good question!",
        "Let's see...",
        "Interesting...",
        "Let me check...",
        "One moment...",
        "I'm digging into this.",
        "Composing my thoughts...",
        "I have an answer for you.",
        "Here's what I found."
    ],
    "empathetic": [
        "I hear you.",
        "Of course.",
        "I know exactly what you mean.",
        "I get that.",
        "Totally.",
        "For sure.",
        "I get what you're saying."
    ],
    "interruption": [
        "Heard you.",
        "One sec.",
        "Hang on.",
        "Got it.",
        "Okay okay...",
        "Hold on!!!",
    ],
}

EXAMPLE_ANSWER = "Paris is the capital of France."


def play(path):
    subprocess.run(["afplay", path], check=True)


def synthesize_to_tempfile(pipeline, text, voice="af_heart"):
    chunks = []
    for _, _, audio_np in pipeline(text, voice=voice, speed=1.0):
        chunks.append(audio_np)
    if not chunks:
        return None
    audio = np.concatenate(chunks)
    buf = io.BytesIO()
    sf.write(buf, audio, 24000, format="WAV")
    wav_bytes = buf.getvalue()

    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", "pipe:0",
         "-codec:a", "libmp3lame", "-q:a", "2", tmp.name],
        input=wav_bytes,
        capture_output=True,
    )
    if result.returncode != 0:
        os.unlink(tmp.name)
        return None
    return tmp.name


def main():
    print("Loading Kokoro pipeline...")
    pipeline = KPipeline(lang_code="a")
    voice = "af_heart"

    print("Pre-generating example answer...")
    answer_path = synthesize_to_tempfile(pipeline, EXAMPLE_ANSWER, voice=voice)
    if not answer_path:
        print("ERROR: could not synthesize example answer")
        return

    # Filler buckets
    for bucket, phrases in FILLERS.items():
        print("\n" + "=" * 50)
        print(f"FILLERS — {bucket.upper()}")
        print("=" * 50)
        for i, phrase in enumerate(phrases):
            clip = os.path.join(BASE, "assets", "fillers", bucket, f"filler_{i:02d}.mp3")
            print(f"\n>>> [{bucket}/filler_{i:02d}.mp3] {phrase} → {EXAMPLE_ANSWER}")
            play(clip)
            play(answer_path)
            time.sleep(0.5)

    os.unlink(answer_path)
    print("\n\nDone!")


if __name__ == "__main__":
    main()
