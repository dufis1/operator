"""
OpenAI LLM provider.

Wraps an openai.OpenAI client. Translates OpenAI's context_length_exceeded
BadRequestError into the provider-agnostic ContextOverflowError.
"""
import openai

from pipeline.providers.base import LLMProvider, ContextOverflowError


class OpenAIProvider(LLMProvider):
    def __init__(self, client):
        self._client = client

    def complete(self, messages, model, max_tokens, tools=None):
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["parallel_tool_calls"] = False
        try:
            response = self._client.chat.completions.create(**kwargs)
        except openai.BadRequestError as e:
            if getattr(e, "code", None) == "context_length_exceeded":
                raise ContextOverflowError() from e
            raise
        return response.choices[0].message

    def complete_stream(self, messages, model, max_tokens):
        response = self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
            stream=True,
        )
        for chunk in response:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content

    def warmup(self, model):
        self._client.chat.completions.create(
            model=model,
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
