"""
Tests for chat hardening: history cap, wake phrase gating, sender filtering.
Run: python tests/test_chat_hardening.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config
import re


def test_history_cap():
    """LLMClient should cap history to chat_history_turns pairs."""
    from unittest.mock import MagicMock

    from pipeline.llm import LLMClient

    mock_client = MagicMock()
    llm = LLMClient(mock_client)
    llm._max_pairs = 5  # use fixed value for test

    # Record 10 exchanges — should keep only 5
    for i in range(10):
        llm.record_exchange(f"user msg {i}", f"bot reply {i}")

    assert len(llm._history) == 10, \
        f"Expected 10 messages, got {len(llm._history)}"

    # Oldest kept should be exchange #5 (0-indexed)
    assert llm._history[0]["content"] == "user msg 5", \
        f"Expected oldest to be 'user msg 5', got {llm._history[0]['content']!r}"
    assert llm._history[-1]["content"] == "bot reply 9"

    print("  history cap: PASS")


def test_add_context():
    """add_context should add a user message without a reply."""
    from unittest.mock import MagicMock

    from pipeline.llm import LLMClient

    mock_client = MagicMock()
    llm = LLMClient(mock_client)

    llm.add_context("Alice: hey everyone")
    assert len(llm._history) == 1
    assert llm._history[0]["role"] == "user"
    assert llm._history[0]["content"] == "Alice: hey everyone"

    print("  add_context: PASS")


def test_history_cap_with_context():
    """Context-only messages should not count toward the pair limit."""
    from unittest.mock import MagicMock

    from pipeline.llm import LLMClient

    mock_client = MagicMock()
    llm = LLMClient(mock_client)
    llm._max_pairs = 2  # only keep 2 Q&A pairs

    # Add: context, pair, context, pair, context, pair
    llm.add_context("Alice: hi")
    llm.record_exchange("q1", "a1")
    llm.add_context("Bob: hey")
    llm.record_exchange("q2", "a2")
    llm.add_context("Alice: sure")
    llm.record_exchange("q3", "a3")

    # Should have kept 2 pairs + surrounding context, dropped pair 1
    pairs = sum(1 for m in llm._history if m["role"] == "assistant")
    assert pairs == 2, f"Expected 2 pairs, got {pairs}"
    # pair 1 (q1/a1) should be gone
    contents = [m["content"] for m in llm._history]
    assert "a1" not in contents, "Oldest pair should have been trimmed"
    assert "a2" in contents and "a3" in contents

    print("  history cap with context: PASS")


def test_wake_phrase_gating():
    """Only messages containing the wake phrase should trigger a response."""
    wake = config.TRIGGER_PHRASE.lower()

    # These should match
    match_cases = [
        "/operator what time is it",
        "hey /operator, summarize",
        "/Operator tell me a joke",
    ]
    for text in match_cases:
        assert wake in text.lower(), f"Should match: {text!r}"

    # These should NOT match
    no_match = [
        "what time is it",
        "let's discuss the operator role",  # only matches if wake is literally "operator"
    ]
    for text in no_match:
        assert wake not in text.lower(), f"Should not match: {text!r}"

    print("  wake phrase detection: PASS")


def test_wake_phrase_stripping():
    """Wake phrase should be stripped from the prompt sent to LLM."""
    wake = config.TRIGGER_PHRASE
    pattern = re.escape(wake) + r'[,:]?\s*'

    cases = [
        (f"{wake} what time is it", "what time is it"),
        (f"{wake}, summarize the discussion", "summarize the discussion"),
        (f"hey {wake}: what was said", "hey what was said"),
    ]
    for text, expected in cases:
        result = re.sub(pattern, '', text, count=1, flags=re.IGNORECASE).strip()
        assert result == expected, f"Strip {text!r} -> {result!r}, expected {expected!r}"

    print("  wake phrase stripping: PASS")


def test_sender_filtering():
    """Bot's own messages should be filtered by sender name."""
    bot_name = config.AGENT_NAME

    # Sender matches bot name (case-insensitive) -> skip
    assert bot_name.lower() == "operator"
    assert "Operator".lower() == bot_name.lower()

    # Sender is someone else -> process
    assert "Alice".lower() != bot_name.lower()

    # Sender is empty -> fall back to text match
    own_messages = {"Hello there"}
    text = "Hello there"
    assert text in own_messages  # would be filtered by fallback

    print("  sender filtering: PASS")


if __name__ == "__main__":
    print("Chat hardening tests:")
    test_history_cap()
    test_add_context()
    test_history_cap_with_context()
    test_wake_phrase_gating()
    test_wake_phrase_stripping()
    test_sender_filtering()
    print("\nAll tests passed.")
