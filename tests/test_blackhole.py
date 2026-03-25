"""
Test that TTS audio routes to BlackHole (not default speakers).
You should hear nothing from your speakers if it's working correctly.
"""
import os
import subprocess
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs

load_dotenv()

client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])

print("Streaming TTS to BlackHole...")
audio_stream = client.text_to_speech.stream(
    text="Hello, I am Operator. Testing BlackHole routing.",
    voice_id="JBFqnCBsd6RMkjVDRZzb",
    model_id="eleven_flash_v2_5",
)
proc = subprocess.Popen(
    ["mpv", "--no-terminal", "--audio-device=coreaudio/BlackHole2ch_UID", "--", "-"],
    stdin=subprocess.PIPE,
)
for chunk in audio_stream:
    if chunk:
        proc.stdin.write(chunk)
proc.stdin.close()
proc.wait()
print("✅ Done — no audio from speakers means BlackHole routing is working")
