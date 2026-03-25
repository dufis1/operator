"""
Test ElevenLabs TTS using the exact model and streaming method used in app.py.
"""
import os
from dotenv import load_dotenv

load_dotenv()

def test_tts_stream():
    from elevenlabs import stream as play_stream
    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])

    print("Streaming TTS via eleven_flash_v2_5 + play_stream (mpv)...")
    audio_stream = client.text_to_speech.stream(
        text="Hello, I am Operator. I am ready to join your meeting.",
        voice_id="JBFqnCBsd6RMkjVDRZzb",  # George
        model_id="eleven_flash_v2_5",
    )
    play_stream(audio_stream)
    print("✅ TTS streaming works")

if __name__ == "__main__":
    test_tts_stream()
