"""
Unit tests for step 9.12 — tool call timeout + heartbeat.

Tests three behaviors without a live meeting:
  1. Fast tool: completes before first heartbeat — no "Still working" messages
  2. Slow tool: takes longer than heartbeat — sends heartbeat(s), then delivers result
  3. Timeout: exceeds hard timeout — sends failure message, error fed to LLM

Config values are overridden to small numbers so tests run quickly.

Run:
    source venv/bin/activate
    python tests/test_912_tool_timeout.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BRAINCHILD_BOT", "pm")

import time
from unittest.mock import MagicMock
import config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_runner(tool_delay_seconds, heartbeat=1, timeout=4):
    """
    Build a ChatRunner with a slow mock MCP tool.
    Overrides config heartbeat/timeout so tests don't take forever.
    Returns (runner, sent_list, llm_mock, mcp_mock).
    """
    config.TOOL_HEARTBEAT_SECONDS = heartbeat
    config.TOOL_TIMEOUT_SECONDS = timeout

    from pipeline.chat_runner import ChatRunner

    connector = MagicMock()
    llm = MagicMock()
    mcp = MagicMock()
    mcp.get_openai_tools.return_value = []
    mcp.tool_timeout_for.return_value = None
    mcp.record_tool_result.return_value = False  # server not tripped
    llm.send_tool_result.return_value = {"type": "text", "content": "Done."}

    def slow_tool(name, args):
        time.sleep(tool_delay_seconds)
        return "tool result"

    mcp.execute_tool.side_effect = slow_tool

    runner = ChatRunner(connector, llm, mcp)
    sent = []
    runner._send = lambda text: sent.append(text)

    return runner, sent, llm, mcp


# ---------------------------------------------------------------------------
# Test 1: fast tool — no heartbeat
# ---------------------------------------------------------------------------

def test_fast_tool_no_heartbeat():
    """Tool finishes well before the first heartbeat — no 'Still working' sent."""
    runner, sent, llm, mcp = make_runner(tool_delay_seconds=0.1, heartbeat=1, timeout=4)
    runner._pending_tool_call = {"id": "c1", "name": "fast__tool", "arguments": {}}

    runner._handle_confirmation("yes")

    heartbeats = [m for m in sent if "Still working" in m]
    assert len(heartbeats) == 0, f"Expected no heartbeats, got: {sent}"
    llm.send_tool_result.assert_called_once()
    assert "Done." in sent, f"Expected 'Done.' in sent, got: {sent}"
    print(f"PASS  test_fast_tool_no_heartbeat  sent={sent}")


# ---------------------------------------------------------------------------
# Test 2: slow tool — heartbeat fires, result delivered
# ---------------------------------------------------------------------------

def test_slow_tool_sends_heartbeat():
    """Tool takes 2.5s with a 1s heartbeat — at least one heartbeat, no timeout."""
    runner, sent, llm, mcp = make_runner(tool_delay_seconds=2.5, heartbeat=1, timeout=6)
    runner._pending_tool_call = {"id": "c2", "name": "slow__tool", "arguments": {}}

    runner._handle_confirmation("yes")

    heartbeats = [m for m in sent if "Still working" in m]
    timeout_msgs = [m for m in sent if "too long" in m.lower()]

    assert len(heartbeats) >= 1, f"Expected at least one heartbeat, got: {sent}"
    assert len(timeout_msgs) == 0, f"Unexpected timeout message: {sent}"
    llm.send_tool_result.assert_called_once()
    # Result should have been delivered (not an error result)
    call_args = llm.send_tool_result.call_args[0]
    assert call_args[2] == "tool result", f"Expected tool result, got: {call_args[2]}"
    print(f"PASS  test_slow_tool_sends_heartbeat  heartbeats={len(heartbeats)} sent={sent}")


# ---------------------------------------------------------------------------
# Test 3: hung tool — hard timeout fires
# ---------------------------------------------------------------------------

def test_timeout_sends_failure_message():
    """Tool runs longer than timeout — timeout signpost fed to LLM for user-facing summary."""
    runner, sent, llm, mcp = make_runner(tool_delay_seconds=10, heartbeat=1, timeout=3)
    runner._pending_tool_call = {"id": "c3", "name": "hung__tool", "arguments": {}}

    runner._handle_confirmation("yes")

    llm.send_tool_result.assert_called_once()
    result_content = llm.send_tool_result.call_args[0][2]
    assert "timed out" in result_content.lower(), \
        f"Expected 'timed out' in error signpost, got: {result_content}"
    print(f"PASS  test_timeout_sends_failure_message  signpost={result_content[:80]!r}")


def test_timeout_fallback_when_llm_fails():
    """On timeout, if the LLM follow-up itself fails, the user gets the terse fallback."""
    runner, sent, llm, mcp = make_runner(tool_delay_seconds=10, heartbeat=1, timeout=3)
    llm.send_tool_result.side_effect = RuntimeError("LLM down")
    runner._pending_tool_call = {"id": "c4", "name": "hung__tool", "arguments": {}}

    runner._handle_confirmation("yes")

    timeout_msgs = [m for m in sent if "too long" in m.lower()]
    assert len(timeout_msgs) == 1, f"Expected fallback timeout message, got: {sent}"
    assert "Done." not in sent, f"Unexpected success message: {sent}"
    print(f"PASS  test_timeout_fallback_when_llm_fails  sent={sent}")


# ---------------------------------------------------------------------------
# Run all
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_fast_tool_no_heartbeat,
        test_slow_tool_sends_heartbeat,
        test_timeout_sends_failure_message,
        test_timeout_fallback_when_llm_fails,
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
