"""
Test the full text → Claude → TTS pipeline (no audio input needed).
Verifies that all three core components work together after the cleanup.
"""
import os
from dotenv import load_dotenv

load_dotenv()

VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"
SYSTEM_PROMPT = (
    "You are Operator, an AI thought partner participating in a meeting. "
    "Your responses will be spoken aloud via text-to-speech, so:\n"
    "- Keep responses to 2-3 sentences maximum\n"
    "- Never use markdown, bullet points, or formatting\n"
    "- Speak in plain, natural sentences only"
)

def test_pipeline():
    import anthropic
    from elevenlabs import stream as play_stream
    from elevenlabs.client import ElevenLabs

    claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    eleven = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])

    prompt = "What's one useful tip for keeping a remote brainstorm session focused?"
    print(f"Prompt: {prompt}\n")

    print("Asking Claude...")
    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=100,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    reply = response.content[0].text
    print(f"Claude: {reply}\n")

    print("Speaking via ElevenLabs...")
    audio_stream = eleven.text_to_speech.stream(
        text=reply,
        voice_id=VOICE_ID,
        model_id="eleven_flash_v2_5",
    )
    play_stream(audio_stream)
    print("✅ Full pipeline works")

if __name__ == "__main__":
    test_pipeline()
