"""
Wake phrase detection for Operator.

No macOS-specific imports. Pure string logic — safe to use in any connector.
"""

WAKE_PHRASE = "operator"


def detect_wake_phrase(text):
    """Scan a transcript utterance for the wake phrase.

    Returns a tuple:
      ("inline", prompt)  — wake phrase followed by a prompt in the same utterance
                            e.g. "operator what's the plan" → ("inline", "what's the plan")
      ("wake-only", "")   — wake phrase alone, no trailing content
                            e.g. "operator" → ("wake-only", "")
      (None, "")          — no wake phrase found
                            e.g. "let's operate on that" → (None, "")
    """
    text_lower = text.lower()
    if WAKE_PHRASE not in text_lower:
        return (None, "")

    idx = text_lower.find(WAKE_PHRASE)
    trailing = text[idx + len(WAKE_PHRASE):].strip().strip(",.:?!")

    if trailing:
        return ("inline", trailing)
    return ("wake-only", "")
