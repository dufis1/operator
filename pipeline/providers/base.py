"""
LLM provider interface.

Providers are thin transports — they take messages + tool schemas, hit the
backing API, and return a response. All conversation state, history trimming,
tool-result validation, and system-prompt assembly stay in LLMClient.

The response shape returned by complete() is currently OpenAI-shaped
(object with .content, .tool_calls, .to_dict()) since that's the format
LLMClient already expects. A second provider (e.g. Anthropic) is responsible
for adapting its native response to that shape.
"""


class ContextOverflowError(Exception):
    """Raised by a provider when the model reports the context window is exceeded.

    LLMClient catches this and surfaces {"type": "context_overflow"} to callers
    after clearing history.
    """


class LLMProvider:
    """Abstract LLM transport. Subclasses implement complete/complete_stream/warmup."""

    def complete(self, messages, model, max_tokens, tools=None):
        """Send a chat completion and return the resulting message.

        Returns an OpenAI-shaped message object with .content (str|None),
        .tool_calls (list|None), and .to_dict() support.

        Raises ContextOverflowError if the model's context window is exceeded.
        """
        raise NotImplementedError

    def complete_stream(self, messages, model, max_tokens):
        """Stream a chat completion. Yields content chunks (str) as they arrive."""
        raise NotImplementedError

    def warmup(self, model):
        """Fire a 1-token request to warm the TCP/TLS connection pool."""
        raise NotImplementedError
