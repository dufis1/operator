"""
LLM integration for Operator.

Wraps OpenAI chat completions with a system prompt and per-session
conversation history. No macOS imports.
"""
import json
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

    def __init__(self, openai_client, mode="voice"):
        self._client = openai_client
        self._history = []
        self._mode = mode
        self._max_pairs = config.CHAT_HISTORY_TURNS  # user+assistant pairs to keep
        if mode == "chat":
            self._system_prompt = config.CHAT_SYSTEM_PROMPT
            self._max_tokens = config.CHAT_MAX_TOKENS
        else:
            self._system_prompt = config.SYSTEM_PROMPT
            self._max_tokens = 60

    def ask(self, utterance, record=True, tools=None):
        """Send an utterance to GPT and return the reply.

        When tools is None (voice path, backward compat): returns a plain string.
        When tools is provided (chat + MCP): returns a dict with either:
          {"type": "text", "content": "..."}
          {"type": "tool_call", "id": "...", "name": "...", "arguments": {...}}

        record=False: result is NOT added to conversation history.
        Call record_exchange() later if you decide to use the result.
        """
        messages = [
            {"role": "system", "content": self._system_prompt},
            *self._history,
            {"role": "user", "content": utterance},
        ]
        log.info(f"LLM ask model={config.LLM_MODEL} mode={self._mode} max_tokens={self._max_tokens} history_msgs={len(self._history)} prompt_chars={len(utterance)} tools={len(tools) if tools else 0}")
        log.debug(f"LLM utterance: {utterance}")

        kwargs = {
            "model": config.LLM_MODEL,
            "max_tokens": self._max_tokens,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as e:
            log.error(f"LLM API call failed: {e}", exc_info=True)
            raise

        message = response.choices[0].message

        # No tools provided (voice path) — return plain string for backward compat
        if not tools:
            reply = message.content
            log.info(f"LLM reply=\"{reply[:80]}\"")
            if record:
                self._history.append({"role": "user", "content": utterance})
                self._history.append({"role": "assistant", "content": reply})
                self._trim_history()
            return reply

        # Tools provided — check if model wants to call one
        if message.tool_calls:
            tc = message.tool_calls[0]
            log.info(f"LLM tool_call name={tc.function.name}")
            if record:
                self._history.append({"role": "user", "content": utterance})
                self._history.append(message.to_dict())
                self._trim_history()
            return {
                "type": "tool_call",
                "id": tc.id,
                "name": tc.function.name,
                "arguments": json.loads(tc.function.arguments),
            }
        else:
            reply = message.content
            log.info(f"LLM reply=\"{reply[:80]}\"")
            if record:
                self._history.append({"role": "user", "content": utterance})
                self._history.append({"role": "assistant", "content": reply})
                self._trim_history()
            return {"type": "text", "content": reply}

    def ask_stream(self, utterance):
        """Stream tokens from GPT. Yields token strings as they arrive.

        Does NOT record to history — call record_exchange() if you use the result.
        """
        messages = [
            {"role": "system", "content": self._system_prompt},
            *self._history,
            {"role": "user", "content": utterance},
        ]
        log.info(f"LLM ask_stream model={config.LLM_MODEL} mode={self._mode} max_tokens={self._max_tokens} history_msgs={len(self._history)} prompt_chars={len(utterance)}")
        log.debug(f"LLM utterance: {utterance}")
        try:
            response = self._client.chat.completions.create(
                model=config.LLM_MODEL,
                max_tokens=self._max_tokens,
                messages=messages,
                stream=True,
            )
        except Exception as e:
            log.error(f"LLM API stream failed: {e}", exc_info=True)
            raise
        for chunk in response:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content

    def warmup(self):
        """Fire a 1-token request to establish the TCP/TLS connection pool.

        Not recorded to history. Call once at startup in a background thread.
        """
        try:
            self._client.chat.completions.create(
                model=config.LLM_MODEL,
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            log.info("LLM warmup complete")
        except Exception as e:
            log.warning(f"LLM warmup failed (non-fatal): {e}")

    def record_exchange(self, utterance: str, reply: str):
        """Commit a user/assistant exchange to history without an API call.

        Used when a speculative LLM result is accepted.
        """
        self._history.append({"role": "user", "content": utterance})
        self._history.append({"role": "assistant", "content": reply})
        self._trim_history()

    def add_context(self, text: str):
        """Add a message to history as context without triggering a response."""
        self._history.append({"role": "user", "content": text})
        self._trim_history()

    def send_tool_result(self, tool_call_id: str, tool_name: str, result_content: str):
        """Feed a tool result back to the model and get the summary response.

        Call this after executing a tool call. The LLM will summarize the
        result into a user-facing message.
        """
        self._history.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result_content,
        })
        messages = [
            {"role": "system", "content": self._system_prompt},
            *self._history,
        ]
        log.info(f"LLM send_tool_result tool={tool_name} result_len={len(result_content)}")
        try:
            response = self._client.chat.completions.create(
                model=config.LLM_MODEL,
                max_tokens=self._max_tokens,
                messages=messages,
            )
        except Exception as e:
            log.error(f"LLM tool result call failed: {e}", exc_info=True)
            raise
        reply = response.choices[0].message.content
        log.info(f"LLM tool summary=\"{reply[:80]}\"")
        self._history.append({"role": "assistant", "content": reply})
        self._trim_history()
        return reply

    def _trim_history(self):
        """Keep only the most recent _max_pairs user/assistant pairs.

        Context-only messages (user messages without a following assistant
        reply) don't count toward the pair limit — but context before the
        oldest kept pair is dropped.
        """
        # Walk backwards, counting pairs. Once we've found _max_pairs pairs,
        # everything before that point gets dropped.
        pairs = 0
        keep_from = 0
        i = len(self._history) - 1
        while i >= 0:
            if (i >= 1
                    and self._history[i]["role"] == "assistant"
                    and self._history[i - 1]["role"] == "user"):
                pairs += 1
                if pairs == self._max_pairs:
                    keep_from = i - 1
                    break
                i -= 2
            else:
                i -= 1
        if keep_from > 0:
            self._history = self._history[keep_from:]
