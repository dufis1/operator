"""Generate backchannel audio clips using ElevenLabs TTS (George voice).

Run once to create the mp3 files, then commit them to the repo.
Usage: python generate_backchannel.py
"""
import os
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs

load_dotenv()

VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # George
MODEL_ID = "eleven_flash_v2_5"
CLIPS = {
    "backchannel_mmhmm.mp3": "mm-hmm?",
    "backchannel_goon.mp3": "go on",
    "ack_yeah.mp3": "yeah?",
    "ack_yes.mp3": "yes?",
    "ack_mmhm.mp3": "mm-hm?",
}

client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])

for filename, text in CLIPS.items():
    print(f"Generating {filename} (\"{text}\")...")
    audio_stream = client.text_to_speech.stream(
        text=text,
        voice_id=VOICE_ID,
        model_id=MODEL_ID,
    )
    with open(filename, "wb") as f:
        for chunk in audio_stream:
            if chunk:
                f.write(chunk)
    size = os.path.getsize(filename)
    print(f"  Saved {filename} ({size} bytes)")

print("Done. Commit these files to the repo.")
