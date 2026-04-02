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

    def ask(self, utterance, record=True):
        """Send an utterance to GPT and return the reply string.

        record=False: result is NOT added to conversation history.
        Call record_exchange() later if you decide to use the result.
        """
        messages = [
            {"role": "system", "content": config.SYSTEM_PROMPT},
            *self._history,
            {"role": "user", "content": utterance},
        ]
        log.info(f"LLM ask model={config.LLM_MODEL} history_turns={len(self._history)//2} utterance=\"{utterance[:80]}\"")
        try:
            response = self._client.chat.completions.create(
                model=config.LLM_MODEL,
                max_tokens=60,
                messages=messages,
            )
        except Exception as e:
            log.error(f"LLM API call failed: {e}", exc_info=True)
            raise
        reply = response.choices[0].message.content
        log.info(f"LLM reply=\"{reply[:80]}\"")
        if record:
            self._history.append({"role": "user", "content": utterance})
            self._history.append({"role": "assistant", "content": reply})
        return reply

    def record_exchange(self, utterance: str, reply: str):
        """Commit a user/assistant exchange to history without an API call.

        Used when a speculative LLM result is accepted.
        """
        self._history.append({"role": "user", "content": utterance})
        self._history.append({"role": "assistant", "content": reply})
