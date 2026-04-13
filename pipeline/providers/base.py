"""
LLM provider interface.

Providers translate between the app's neutral conversation shape and a
specific backend (OpenAI, Anthropic, etc.). All conversation state —
history trimming, tool-result validation, system-prompt assembly —
stays in LLMClient and is expressed in the neutral shape defined here.

Neutral history message shape (what LLMClient stores and passes in):
  {"role": "user", "content": str}
  {"role": "assistant", "content": str}                         # plain text reply
  {"role": "assistant", "content": str|None,
                        "tool_calls": [ToolCall, ...]}          # tool-call turn
  {"role": "tool_result", "tool_call_id": str, "content": str}  # result of a tool

The system prompt is passed as its own `system` argument to complete(),
not as a message with role="system".
"""
from dataclasses import dataclass, field


class ContextOverflowError(Exception):
    """Raised by a provider when the model reports the context window is exceeded.

    LLMClient catches this and surfaces {"type": "context_overflow"} to callers
    after clearing history.
    """


@dataclass
class ToolCall:
    """A single tool invocation requested by the model.

    args is the already-parsed argument object (dict), not a JSON string.
    Providers are responsible for parsing whatever their SDK returns.
    """
    id: str
    name: str
    args: dict


@dataclass
class ProviderResponse:
    """Neutral response returned by LLMProvider.complete().

    stop_reason values:
      "end"       — model finished a normal text reply
      "tool_use"  — model wants to call one or more tools
      "length"    — hit max_tokens
      "other"     — anything else (content filter, etc.)
    """
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end"


class LLMProvider:
    """Abstract LLM transport.

    Subclasses translate the neutral inputs/outputs defined in this module
    to and from a specific backend (OpenAI, Anthropic, etc.). Callers pass
    the system prompt separately from the neutral `messages` list and
    receive a ProviderResponse.
    """

    def complete(self, system, messages, model, max_tokens, tools=None):
        """Send a chat completion and return a ProviderResponse.

        Args:
          system: system prompt string (may be empty)
          messages: neutral history list (see module docstring for shape)
          model: backend-specific model id
          max_tokens: int
          tools: optional list of tool schemas in OpenAI-function-calling shape
                 (providers translate to their own schema format if needed)

        Raises ContextOverflowError if the model's context window is exceeded.
        """
        raise NotImplementedError

    def complete_stream(self, system, messages, model, max_tokens):
        """Stream a plain-text completion. Yields text chunks (str) as they arrive.

        Tool-call streaming is not part of this interface (the voice path
        that uses streaming does not use tools).
        """
        raise NotImplementedError

    def warmup(self, model):
        """Fire a 1-token request to warm the TCP/TLS connection pool."""
        raise NotImplementedError
