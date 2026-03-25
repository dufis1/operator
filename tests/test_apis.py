"""
Baby step 1: Verify both API keys work.
"""
import os
from dotenv import load_dotenv

load_dotenv()

def test_anthropic():
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=32,
        messages=[{"role": "user", "content": "Say 'API works' and nothing else."}]
    )
    print(f"✅ Anthropic: {message.content[0].text}")

def test_elevenlabs():
    from elevenlabs.client import ElevenLabs
    client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
    # Just fetch the list of available voices — no audio generated, no quota used
    voices = client.voices.get_all()
    print(f"✅ ElevenLabs: connected, {len(voices.voices)} voices available")

if __name__ == "__main__":
    print("Testing API connections...\n")
    try:
        test_anthropic()
    except Exception as e:
        print(f"❌ Anthropic failed: {e}")

    try:
        test_elevenlabs()
    except Exception as e:
        print(f"❌ ElevenLabs failed: {e}")
