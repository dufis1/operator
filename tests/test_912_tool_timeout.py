"""
Unit tests for step 9.12 — tool-call heartbeat + per-MCP timeout.

Session 157: timeouts were moved from chat_runner into the MCP layer so each
server can carry its own deadline (see config.DEFAULT_TOOL_TIMEOUTS + the
per-server `tool_timeout_seconds` override). chat_runner's only job during a
tool call is driving exponential-backoff heartbeats; when the MCP raises
(including timeout as an MCPToolError), the normal error-handler path feeds
an informative signpost to the LLM.

Covers:
  1. Fast tool: completes before first heartbeat — no "Still working"
  2. Slow tool: takes longer than heartbeat — at least one heartbeat, result delivered
  3. Heartbeat backoff: interval doubles up to the cap
  4. MCP-reported timeout: MCPToolError propagates to the LLM with "timed out"
  5. MCP-reported timeout + LLM follow-up fails: fallback message to chat

Run:
    source venv/bin/activate
    python tests/test_912_tool_timeout.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
os.environ.setdefault("BRAINCHILD_BOT", "pm")

import time
from unittest.mock import MagicMock
from brainchild import config
from brainchild.pipeline.mcp_client import MCPToolError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_runner(heartbeat=1, heartbeat_max=4):
    """Build a ChatRunner with a mock MCP. Caller sets execute_tool behavior."""
    config.TOOL_HEARTBEAT_SECONDS = heartbeat
    config.TOOL_HEARTBEAT_MAX_SECONDS = heartbeat_max

    from brainchild.pipeline.chat_runner import ChatRunner

    connector = MagicMock()
    llm = MagicMock()
    mcp = MagicMock()
    mcp.get_openai_tools.return_value = []
    mcp.tool_timeout_for.return_value = None
    mcp.record_tool_result.return_value = False  # server not tripped
    llm.send_tool_result.return_value = {"type": "text", "content": "Done."}

    runner = ChatRunner(connector, llm, mcp)
    sent = []
    runner._send = lambda text: sent.append(text)

    return runner, sent, llm, mcp


def _slow_tool_returning(delay, value="tool result"):
    def _fn(name, args):
        time.sleep(delay)
        return value
    return _fn


def _slow_tool_raising(delay, exc):
    def _fn(name, args):
        time.sleep(delay)
        raise exc
    return _fn


# ---------------------------------------------------------------------------
# Test 1: fast tool — no heartbeat
# ---------------------------------------------------------------------------

def test_fast_tool_no_heartbeat():
    """Tool finishes well before the first heartbeat — no verb ping sent."""
    runner, sent, llm, mcp = make_runner(heartbeat=1)
    mcp.execute_tool.side_effect = _slow_tool_returning(0.1)
    runner._pending_tool_call = {"id": "c1", "name": "fast__tool", "arguments": {}}

    runner._handle_confirmation("yes")

    # Heartbeats are present-participle verbs ending in "..." (Strutting…, Loping…, etc.)
    heartbeats = [m for m in sent if m.endswith("...")]
    assert len(heartbeats) == 0, f"Expected no heartbeats, got: {sent}"
    llm.send_tool_result.assert_called_once()
    assert "Done." in sent, f"Expected 'Done.' in sent, got: {sent}"
    print(f"PASS  test_fast_tool_no_heartbeat  sent={sent}")


# ---------------------------------------------------------------------------
# Test 2: slow tool — heartbeat fires, result delivered
# ---------------------------------------------------------------------------

def test_slow_tool_sends_heartbeat():
    """Tool takes 2.5s with a 1s heartbeat — at least one heartbeat."""
    runner, sent, llm, mcp = make_runner(heartbeat=1)
    mcp.execute_tool.side_effect = _slow_tool_returning(2.5)
    runner._pending_tool_call = {"id": "c2", "name": "slow__tool", "arguments": {}}

    runner._handle_confirmation("yes")

    heartbeats = [m for m in sent if m.endswith("...")]
    timeout_msgs = [m for m in sent if "timed out" in m.lower()]

    assert len(heartbeats) >= 1, f"Expected at least one heartbeat, got: {sent}"
    assert len(timeout_msgs) == 0, f"Unexpected timeout message: {sent}"
    llm.send_tool_result.assert_called_once()
    call_args = llm.send_tool_result.call_args[0]
    assert call_args[2] == "tool result", f"Expected tool result, got: {call_args[2]}"
    print(f"PASS  test_slow_tool_sends_heartbeat  heartbeats={len(heartbeats)} sent={sent}")


# ---------------------------------------------------------------------------
# Test 3: fixed cadence — interval stays constant, does NOT back off
# ---------------------------------------------------------------------------

def test_heartbeat_interval_is_fixed_cadence():
    """Two heartbeats at ~1s each — spacing should stay constant, not double.

    Session 163 swapped exponential backoff for a fixed cadence: stretching
    gaps read as 'the bot hung' to users; steady pings read as 'still alive'.
    """
    runner, sent, llm, mcp = make_runner(heartbeat=1, heartbeat_max=8)

    stamps = []
    original_send = runner._send
    def _send_with_ts(text):
        stamps.append((time.monotonic(), text))
        original_send(text)
    runner._send = _send_with_ts

    mcp.execute_tool.side_effect = _slow_tool_returning(2.6)
    runner._pending_tool_call = {"id": "c3", "name": "slow__tool", "arguments": {}}

    t0 = time.monotonic()
    runner._handle_confirmation("yes")
    # Heartbeat messages are present-participle verbs ending in "..." — not "Still working".
    heartbeats = [t for t, m in stamps if m.endswith("...")]

    assert len(heartbeats) >= 2, f"Expected at least 2 heartbeats in 2.6s, got {len(heartbeats)}: {stamps}"
    first_gap = heartbeats[0] - t0
    second_gap = heartbeats[1] - heartbeats[0]
    # Fixed cadence: both gaps ~1s. Allow generous jitter for scheduler noise.
    assert 0.8 <= first_gap <= 1.6, f"First heartbeat gap {first_gap:.2f}s outside [0.8, 1.6]"
    assert 0.8 <= second_gap <= 1.6, f"Second heartbeat gap {second_gap:.2f}s outside [0.8, 1.6] (cadence should be fixed, not doubling)"
    print(f"PASS  test_heartbeat_interval_is_fixed_cadence  first={first_gap:.2f}s second={second_gap:.2f}s")


# ---------------------------------------------------------------------------
# Test 4: MCP-reported timeout flows through as a tool error
# ---------------------------------------------------------------------------

def test_mcp_timeout_is_signposted_to_llm():
    """When the MCP layer raises MCPToolError('timed out'), the LLM follow-up gets a 'timed out' signpost."""
    runner, sent, llm, mcp = make_runner(heartbeat=1)
    mcp.execute_tool.side_effect = _slow_tool_raising(
        0.2, MCPToolError("Tool 'hung__tool' timed out after 30s on MCP server 'hung'.")
    )
    runner._pending_tool_call = {"id": "c4", "name": "hung__tool", "arguments": {}}

    runner._handle_confirmation("yes")

    llm.send_tool_result.assert_called_once()
    result_content = llm.send_tool_result.call_args[0][2]
    assert "timed out" in result_content.lower(), \
        f"Expected 'timed out' in error signpost, got: {result_content}"
    print(f"PASS  test_mcp_timeout_is_signposted_to_llm  signpost={result_content[:80]!r}")


def test_mcp_timeout_fallback_when_llm_fails():
    """On MCP-reported timeout, if the LLM follow-up itself fails, fallback hits chat."""
    runner, sent, llm, mcp = make_runner(heartbeat=1)
    mcp.execute_tool.side_effect = _slow_tool_raising(
        0.2, MCPToolError("Tool 'hung__tool' timed out after 30s on MCP server 'hung'.")
    )
    llm.send_tool_result.side_effect = RuntimeError("LLM down")
    runner._pending_tool_call = {"id": "c5", "name": "hung__tool", "arguments": {}}

    runner._handle_confirmation("yes")

    failure_msgs = [m for m in sent if "failed" in m.lower() or "timed out" in m.lower()]
    assert len(failure_msgs) >= 1, f"Expected fallback failure message, got: {sent}"
    assert "Done." not in sent, f"Unexpected success message: {sent}"
    print(f"PASS  test_mcp_timeout_fallback_when_llm_fails  sent={sent}")


# ---------------------------------------------------------------------------
# Run all
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_fast_tool_no_heartbeat,
        test_slow_tool_sends_heartbeat,
        test_heartbeat_interval_is_fixed_cadence,
        test_mcp_timeout_is_signposted_to_llm,
        test_mcp_timeout_fallback_when_llm_fails,
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
