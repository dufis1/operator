"""
Unit tests for step 9.13 — tool exchange history collapse.

After a tool call completes, the intermediate messages (asst[tool_calls] and
role=tool) should be stripped from history, leaving only [user, asst[summary]].
This prevents raw tool results from accumulating in context across a session.

Three cases:
  1. Single tool call: 4 messages collapse to 2
  2. Chained tool calls (A → B → summary): 6 messages collapse to 2
  3. Plain text exchange: no collapse (nothing to strip)

Run:
    source venv/bin/activate
    python tests/test_913_tool_history_collapse.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock


def make_llm():
    from pipeline.llm import LLMClient
    provider = MagicMock()
    return LLMClient(provider, mode="chat")


def make_text_message(text):
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = None
    return msg


# ---------------------------------------------------------------------------
# Test 1: single tool call collapses to [user, asst[summary]]
# ---------------------------------------------------------------------------

def test_single_tool_call_collapses():
    llm = make_llm()

    # Simulate history after user asked + LLM requested a tool
    llm._history = [
        {"role": "user", "content": "create a ticket"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "linear__create_issue", "arguments": "{}"}}
        ]},
    ]

    llm._provider.complete.return_value = make_text_message("Done — ticket LIN-42 created.")

    llm.send_tool_result("call_1", "linear__create_issue", "x" * 10000)

    assert len(llm._history) == 2, f"Expected 2 messages, got {len(llm._history)}: {[m['role'] for m in llm._history]}"
    assert llm._history[0]["role"] == "user"
    assert llm._history[1]["role"] == "assistant"
    assert llm._history[1].get("tool_calls") is None
    assert llm._history[1]["content"] == "Done — ticket LIN-42 created."
    print("PASS  test_single_tool_call_collapses")


# ---------------------------------------------------------------------------
# Test 2: chained tool calls (A → B → summary) collapse to [user, asst[summary]]
# ---------------------------------------------------------------------------

def test_chained_tool_calls_collapse():
    llm = make_llm()

    # Simulate: user asked → LLM called tool A → tool A result fed back →
    # LLM called tool B (send_tool_result returned tool_call, not text) →
    # now resolving tool B with final summary
    llm._history = [
        {"role": "user", "content": "list issues then create one"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_A", "type": "function",
             "function": {"name": "linear__list_issues", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "call_A", "content": "x" * 5000},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_B", "type": "function",
             "function": {"name": "linear__create_issue", "arguments": "{}"}}
        ]},
    ]

    llm._provider.complete.return_value = make_text_message("Listed and created LIN-43.")

    llm.send_tool_result("call_B", "linear__create_issue", "y" * 5000)

    assert len(llm._history) == 2, f"Expected 2 messages, got {len(llm._history)}: {[m['role'] for m in llm._history]}"
    assert llm._history[0]["role"] == "user"
    assert llm._history[1]["role"] == "assistant"
    assert llm._history[1].get("tool_calls") is None
    assert llm._history[1]["content"] == "Listed and created LIN-43."
    print("PASS  test_chained_tool_calls_collapse")


# ---------------------------------------------------------------------------
# Test 3: plain text exchange is untouched
# ---------------------------------------------------------------------------

def test_plain_text_exchange_unchanged():
    from pipeline.llm import LLMClient
    provider = MagicMock()
    llm = LLMClient(provider, mode="chat")

    llm._history = [
        {"role": "user", "content": "what time is it"},
        {"role": "assistant", "content": "I don't have clock access."},
    ]

    # Call collapse directly — nothing should change
    llm._collapse_tool_exchange()

    assert len(llm._history) == 2
    assert llm._history[0]["role"] == "user"
    assert llm._history[1]["role"] == "assistant"
    assert llm._history[1]["content"] == "I don't have clock access."
    print("PASS  test_plain_text_exchange_unchanged")


# ---------------------------------------------------------------------------
# Run all
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_single_tool_call_collapses,
        test_chained_tool_calls_collapse,
        test_plain_text_exchange_unchanged,
    ]
    failures = []
    for t in tests:
        try:
            t()
        except Exception as e:
            import traceback
            print(f"FAIL  {t.__name__}: {e}")
            traceback.print_exc()
            failures.append(t.__name__)

    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
