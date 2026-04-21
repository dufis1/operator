"""
Anthropic (Claude) LLM provider.

Wraps an anthropic.Anthropic client. Translates the app's neutral
conversation shape (see pipeline/providers/base.py) to and from
Anthropic's Messages API format, and maps Anthropic's "prompt is too
long" BadRequestError into the provider-agnostic ContextOverflowError.
"""
import logging
import time

import anthropic

log = logging.getLogger(__name__)

from brainchild.pipeline.providers.base import (
    LLMProvider,
    ContextOverflowError,
    ToolCall,
    ProviderResponse,
)


def _neutral_to_anthropic_messages(messages):
    """Translate neutral history into Anthropic's messages list.

    System prompt is passed separately and is NOT part of this list.
    """
    out = []
    for m in messages:
        role = m["role"]
        if role == "user":
            out.append({"role": "user", "content": m["content"]})
        elif role == "assistant":
            tool_calls = m.get("tool_calls") or []
            if tool_calls:
                blocks = []
                text = m.get("content")
                if text:
                    blocks.append({"type": "text", "text": text})
                for tc in tool_calls:
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.args,
                    })
                out.append({"role": "assistant", "content": blocks})
            else:
                out.append({"role": "assistant", "content": m.get("content") or ""})
        elif role == "tool_result":
            out.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m["content"],
                }],
            })
        else:
            raise ValueError(f"unknown neutral message role: {role!r}")
    return out


def _openai_tools_to_anthropic(tools):
    """Translate OpenAI-function-calling tool schemas to Anthropic's format.

    Marks the last tool with cache_control so Anthropic caches the entire
    tool-schema block (plus the system prompt that precedes it). Tool
    schemas are static across requests, so this turns a ~20k-token
    per-call payload into ~2k-token cache reads after the first call.
    """
    out = []
    for t in tools:
        fn = t["function"]
        out.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    if out:
        out[-1] = {**out[-1], "cache_control": {"type": "ephemeral"}}
    return out


def _anthropic_response_to_neutral(response):
    """Translate an Anthropic Messages API response into a ProviderResponse."""
    text_parts = []
    tool_calls = []
    for block in response.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
        elif btype == "tool_use":
            tool_calls.append(ToolCall(
                id=block.id,
                name=block.name,
                args=dict(block.input) if block.input else {},
            ))
    text = "".join(text_parts) if text_parts else None

    raw_stop = getattr(response, "stop_reason", None)
    if raw_stop == "tool_use":
        stop_reason = "tool_use"
    elif raw_stop == "end_turn" or raw_stop == "stop_sequence":
        stop_reason = "end"
    elif raw_stop == "max_tokens":
        stop_reason = "length"
    else:
        stop_reason = "other"

    return ProviderResponse(text=text, tool_calls=tool_calls, stop_reason=stop_reason)


def _is_context_overflow(err):
    msg = str(err).lower()
    return "prompt is too long" in msg or "context" in msg and "too long" in msg


class AnthropicProvider(LLMProvider):
    def __init__(self, client):
        self._client = client

    def complete(self, system, messages, model, max_tokens, tools=None):
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": _neutral_to_anthropic_messages(messages),
        }
        if system:
            # Wrap system as a content block with cache_control so it
            # becomes part of the cached prefix (system + tools).
            kwargs["system"] = [{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }]
        if tools:
            kwargs["tools"] = _openai_tools_to_anthropic(tools)
            # Our pipeline feeds back one tool_result at a time; parallel
            # tool_use blocks would leave later ids orphaned and trip a 400.
            # Mirror OpenAI's parallel_tool_calls=False. Revisit post-MVP.
            kwargs["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
        t_start = time.monotonic()
        try:
            response = self._client.messages.create(**kwargs)
        except anthropic.BadRequestError as e:
            if _is_context_overflow(e):
                raise ContextOverflowError() from e
            raise
        except anthropic.RateLimitError as e:
            headers = getattr(getattr(e, "response", None), "headers", {}) or {}
            itpm = headers.get("anthropic-ratelimit-input-tokens-limit", "?")
            reset = headers.get("anthropic-ratelimit-input-tokens-reset", "?")
            log.error(
                "Anthropic rate limit hit (HTTP 429). Your tier caps input tokens "
                "at %s/min; resets at %s. Raise your tier at console.anthropic.com "
                "→ Plans & Billing, or reduce the MCP tool count in config.yaml "
                "(each tool schema costs input tokens on every call).",
                itpm, reset,
            )
            raise

        elapsed = time.monotonic() - t_start
        usage = getattr(response, "usage", None)
        if usage is not None:
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
            inp = getattr(usage, "input_tokens", 0) or 0
            out_tok = getattr(usage, "output_tokens", 0) or 0
            total_in = inp + cache_read + cache_create
            cache_pct = (cache_read * 100 // total_in) if total_in else 0
            log.info(
                f"TIMING llm_call={elapsed:.1f}s "
                f"TOKENS in={inp} cache_read={cache_read} cache_create={cache_create} "
                f"out={out_tok} cache_hit={cache_pct}%"
            )

        return _anthropic_response_to_neutral(response)

    def complete_stream(self, system, messages, model, max_tokens):
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": _neutral_to_anthropic_messages(messages),
        }
        if system:
            kwargs["system"] = system
        with self._client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                if text:
                    yield text

    def warmup(self, model):
        self._client.messages.create(
            model=model,
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
