"""
Text sanitization for TTS output.

Cleans LLM replies before they reach the TTS engine, replacing symbols
and formatting artifacts that synthesizers mispronounce or choke on.
"""
import re


def sanitize_for_speech(text: str) -> str:
    """Make an LLM reply safe and natural for text-to-speech."""
    if not text:
        return text

    # --- Math operators (before markdown strip, so `3 * 4` isn't eaten) ---
    text = re.sub(r"(?<=\d)\s*\+\s*(?=\d)", " plus ", text)
    text = re.sub(r"(?<=\d)\s*-\s*(?=\d)", " minus ", text)
    text = re.sub(r"(?<=\d)\s*\*\s*(?=\d)", " times ", text)
    text = re.sub(r"(?<=\d)\s*/\s*(?=\d)", " divided by ", text)
    text = re.sub(r"(?<=\d)\s*=\s*", " equals ", text)

    # --- Strip markdown-style formatting ---
    # Bold / italic markers
    text = re.sub(r"\*+", "", text)
    # Headings
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    # Inline code backticks
    text = text.replace("`", "")

    # --- Arrows → "then" ---
    text = re.sub(r"\s*[-=]+>\s*", " then ", text)
    text = re.sub(r"\s*<[-=]+\s*", " from ", text)

    # --- Container symbols → spaces ---
    text = re.sub(r"[{}\[\]()]+", " ", text)

    # --- Underscores → spaces (code identifiers) ---
    text = text.replace("_", " ")

    # --- Backslashes → spaces ---
    text = text.replace("\\", " ")

    # --- Em dashes and semicolons → commas ---
    text = text.replace("—", ", ")
    text = text.replace("–", ", ")
    text = text.replace(";", ",")

    # --- Pipes → spaces ---
    text = text.replace("|", " ")

    # --- Ampersand → "and" ---
    text = text.replace("&", " and ")

    # --- Clean up residual whitespace ---
    text = re.sub(r"  +", " ", text)
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    text = text.strip()

    return text
