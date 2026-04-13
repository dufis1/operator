"""
Unit tests for step 9.11 — chat message size management.

Tests the three mechanical behaviors that don't require a live meeting:
  1. Tool result size guard (oversized result → archive placeholder)
  2. context_length_exceeded error → {"type": "context_overflow"} + history cleared
  3. ChatRunner routes context_overflow to the right user message

Run:
    source venv/bin/activate
    python tests/test_911_size_management.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from unittest.mock import MagicMock, patch
import openai

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_llm(mode="chat"):
    from pipeline.llm import LLMClient
    client = MagicMock()
    return LLMClient(client, mode=mode)


def make_text_response(text):
    """Return a mock OpenAI chat completion that yields a text message."""
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = None
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def make_bad_request_error(code):
    """Return an openai.BadRequestError with a given code."""
    err = openai.BadRequestError.__new__(openai.BadRequestError)
    err.code = code
    err.message = f"error code: {code}"
    return err


# ---------------------------------------------------------------------------
# Test 1: tool result size guard
# ---------------------------------------------------------------------------

def test_tool_result_size_guard():
    import config
    llm = make_llm()

    # Seed a fake assistant + tool_call message in history so send_tool_result
    # has a valid history state to build on.
    llm._history = [
        {"role": "user", "content": "check something"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_abc",
                "type": "function",
                "function": {"name": "dummy_tool", "arguments": "{}"},
            }],
        },
    ]

    oversized = "x" * (config.TOOL_RESULT_MAX_CHARS + 1)

    # Mock the API call to return a plain text response
    llm._client.chat.completions.create.return_value = make_text_response("summary")

    llm.send_tool_result("call_abc", "dummy_tool", oversized)

    # _collapse_tool_exchange removes the tool message from history after the
    # summary, so check what was sent to the API instead.
    call_args = llm._client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    tool_msg = next(m for m in messages if m.get("role") == "tool")
    assert "archived" in tool_msg["content"], f"Expected archive placeholder, got: {tool_msg['content'][:100]}"
    assert oversized not in tool_msg["content"], "Raw oversized content leaked into API call"
    print("PASS  test_tool_result_size_guard")


# ---------------------------------------------------------------------------
# Test 2a: context_length_exceeded in ask() → context_overflow + history cleared
# ---------------------------------------------------------------------------

def test_ask_context_overflow():
    llm = make_llm()
    llm._history = [
        {"role": "user", "content": "old message"},
        {"role": "assistant", "content": "old reply"},
    ]

    err = make_bad_request_error("context_length_exceeded")
    llm._client.chat.completions.create.side_effect = err

    result = llm.ask("new message", tools=[{"type": "function", "function": {"name": "t", "parameters": {}}}])

    assert result == {"type": "context_overflow"}, f"Expected context_overflow, got: {result}"
    assert llm._history == [], f"Expected history cleared, got: {llm._history}"
    print("PASS  test_ask_context_overflow")


# ---------------------------------------------------------------------------
# Test 2b: context_length_exceeded in send_tool_result() → context_overflow + history cleared
# ---------------------------------------------------------------------------

def test_send_tool_result_context_overflow():
    llm = make_llm()
    llm._history = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old reply"},
    ]

    err = make_bad_request_error("context_length_exceeded")
    llm._client.chat.completions.create.side_effect = err

    result = llm.send_tool_result("call_xyz", "some_tool", "result content")

    assert result == {"type": "context_overflow"}, f"Expected context_overflow, got: {result}"
    assert llm._history == [], f"Expected history cleared, got: {llm._history}"
    print("PASS  test_send_tool_result_context_overflow")


# ---------------------------------------------------------------------------
# Test 2c: non-context BadRequestError still raises
# ---------------------------------------------------------------------------

def test_other_bad_request_still_raises():
    llm = make_llm()
    err = make_bad_request_error("invalid_api_key")
    llm._client.chat.completions.create.side_effect = err

    try:
        llm.ask("something", tools=[{"type": "function", "function": {"name": "t", "parameters": {}}}])
        assert False, "Should have raised"
    except openai.BadRequestError:
        pass
    print("PASS  test_other_bad_request_still_raises")


# ---------------------------------------------------------------------------
# Test 3: ChatRunner routes context_overflow to user-facing message
# ---------------------------------------------------------------------------

def test_chatrunner_routes_context_overflow():
    from pipeline.chat_runner import ChatRunner

    connector = MagicMock()
    llm = MagicMock()
    mcp = MagicMock()
    mcp.get_openai_tools.return_value = []

    runner = ChatRunner(connector, llm, mcp)
    sent = []
    runner._send = lambda text: sent.append(text)

    # Simulate ask() returning context_overflow
    llm.ask.return_value = {"type": "context_overflow"}
    runner._handle_message("do something")

    assert len(sent) == 1, f"Expected 1 message sent, got {len(sent)}"
    assert "too long" in sent[0].lower() or "cleared" in sent[0].lower(), \
        f"Unexpected overflow message: {sent[0]}"
    print("PASS  test_chatrunner_routes_context_overflow")


def test_chatrunner_routes_context_overflow_from_tool_result():
    from pipeline.chat_runner import ChatRunner

    connector = MagicMock()
    llm = MagicMock()
    mcp = MagicMock()
    mcp.get_openai_tools.return_value = []
    mcp.execute_tool.return_value = "some result"

    runner = ChatRunner(connector, llm, mcp)
    sent = []
    runner._send = lambda text: sent.append(text)

    # Simulate a confirmed tool call whose result triggers overflow
    runner._pending_tool_call = {"id": "c1", "name": "some__tool", "arguments": {}}
    llm.send_tool_result.return_value = {"type": "context_overflow"}

    runner._handle_confirmation("yes")

    overflow_msgs = [m for m in sent if "too long" in m.lower() or "cleared" in m.lower()]
    assert len(overflow_msgs) == 1, f"Expected overflow message, sent: {sent}"
    print("PASS  test_chatrunner_routes_context_overflow_from_tool_result")


# ---------------------------------------------------------------------------
# Run all
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_tool_result_size_guard,
        test_ask_context_overflow,
        test_send_tool_result_context_overflow,
        test_other_bad_request_still_raises,
        test_chatrunner_routes_context_overflow,
        test_chatrunner_routes_context_overflow_from_tool_result,
    ]
    failures = []
    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"FAIL  {t.__name__}: {e}")
            failures.append(t.__name__)

    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
