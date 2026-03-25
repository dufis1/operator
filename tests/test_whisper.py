"""
Baby step 4: Record from mic and transcribe with Whisper.
"""
import sounddevice as sd
import numpy as np
import tempfile
import soundfile as sf
from faster_whisper import WhisperModel

SAMPLE_RATE = 16000
DURATION = 5

print("Loading Whisper model (will download ~150MB on first run, be patient)...")
model = WhisperModel("tiny", device="cpu", compute_type="int8")
print("Model ready.\n")

print("Recording for 5 seconds... speak now!")
audio = sd.rec(
    int(DURATION * SAMPLE_RATE),
    samplerate=SAMPLE_RATE,
    channels=1,
    dtype="float32"
)
sd.wait()
print("Done. Transcribing...\n")

# faster-whisper needs a file path, so write to a temp file
with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
    sf.write(f.name, audio, SAMPLE_RATE)
    tmp_path = f.name

segments, info = model.transcribe(tmp_path, language="en")
text = " ".join(seg.text.strip() for seg in segments)

print(f"You said: {text}")
print("✅ Whisper transcription works")
