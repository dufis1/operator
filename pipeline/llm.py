"""
LLM integration for Operator.

Wraps OpenAI chat completions with a system prompt and per-session
conversation history. No macOS imports.
"""
import logging
import config

log = logging.getLogger(__name__)

MAX_TRANSCRIPT_LINES = 100  # rolling transcript history limit


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
            model=config.LLM_MODEL,
            max_tokens=60,
            messages=[
                {"role": "system", "content": config.SYSTEM_PROMPT},
                *self._history,
            ],
        )
        reply = response.choices[0].message.content
        self._history.append({"role": "assistant", "content": reply})
        return reply
