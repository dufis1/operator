"""
LLM integration for Operator.

Wraps OpenAI chat completions with a system prompt and per-session
conversation history. No macOS imports.
"""
import logging

log = logging.getLogger(__name__)

MAX_TRANSCRIPT_LINES = 100  # rolling transcript history limit

SYSTEM_PROMPT = (
    "You are Operator, an AI thought partner participating in a meeting. "
    "Your responses will be spoken aloud via text-to-speech, so:\n"
    "- Keep responses to 1-2 SHORT sentences, under 30 words total\n"
    "- Never use markdown, bullet points, or formatting\n"
    "- Speak in plain, natural sentences only\n"
    "- Be direct — no preamble, no filler, no caveats\n"
    "- User input comes from speech-to-text and may contain transcription "
    "errors (e.g. \"shop advice\" instead of \"Shopify's\"). Use surrounding "
    "context to infer the intended words."
)


class LLMClient:
    """Sends prompts to GPT and maintains per-session conversation history.

    Pass an OpenAI client at construction time:
        client = LLMClient(openai_client)
        reply = client.ask("What's the plan?")
    """

    def __init__(self, openai_client):
        self._client = openai_client
        self._history = []

    def ask(self, utterance):
        """Send an utterance to GPT and return the reply string."""
        self._history.append({"role": "user", "content": utterance})
        response = self._client.chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=60,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                *self._history,
            ],
        )
        reply = response.choices[0].message.content
        self._history.append({"role": "assistant", "content": reply})
        return reply
