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

def test_wake_phrase_detection():
    """Unit tests for pipeline.wake.detect_wake_phrase — no API calls needed."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from pipeline.wake import detect_wake_phrase

    assert detect_wake_phrase("operator what's the plan") == ("inline", "what's the plan"), \
        "inline: trailing prompt should be extracted"

    assert detect_wake_phrase("operator") == ("wake-only", ""), \
        "wake-only: bare wake phrase with no trailing text"

    assert detect_wake_phrase("let's operate on that") == (None, ""), \
        "no match: 'operate' is not the wake phrase"

    print("✅ detect_wake_phrase: all 3 cases pass")


if __name__ == "__main__":
    test_wake_phrase_detection()
    test_pipeline()
