"""
Tests for pipeline.sanitize — TTS text sanitization.

Run:  python tests/test_sanitize.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.sanitize import sanitize_for_speech


def test(label, input_text, expected):
    result = sanitize_for_speech(input_text)
    status = "PASS" if result == expected else "FAIL"
    if status == "FAIL":
        print(f"  {status}: {label}")
        print(f"    input:    {input_text!r}")
        print(f"    expected: {expected!r}")
        print(f"    got:      {result!r}")
    else:
        print(f"  {status}: {label}")
    return status == "PASS"


passed = 0
failed = 0

print("=== sanitize_for_speech tests ===\n")

cases = [
    # --- Arrows ---
    ("arrow chain", "Move -> Jump -> Attack", "Move then Jump then Attack"),
    ("fat arrow", "input => output", "input then output"),
    ("left arrow", "result <-- source", "result from source"),

    # --- Code identifiers ---
    ("underscored name", "Try queue_entry and see if that works",
     "Try queue entry and see if that works"),

    # --- Math ---
    ("addition", "2 + 2 = 4", "2 plus 2 equals 4"),
    ("subtraction", "10 - 3 = 7", "10 minus 3 equals 7"),
    ("multiplication", "3 * 4 = 12", "3 times 4 equals 12"),
    ("division", "10 / 2 = 5", "10 divided by 2 equals 5"),

    # --- Brackets and parens ---
    ("function call", "Try queue_entry() and see", "Try queue entry and see"),
    ("brackets", "See section [overview] for details", "See section overview for details"),
    ("curly braces", "The config {timeout: 30} is set", "The config timeout: 30 is set"),

    # --- Markdown formatting ---
    ("bold", "This is **important** stuff", "This is important stuff"),
    ("italic", "This is *really* key", "This is really key"),
    ("inline code", "Run the `deploy` command", "Run the deploy command"),
    ("heading", "## Summary\nHere is the plan.", "Summary\nHere is the plan."),

    # --- Punctuation replacements ---
    ("em dash", "The plan—as discussed—is ready", "The plan, as discussed, is ready"),
    ("en dash", "The plan–as discussed–is ready", "The plan, as discussed, is ready"),
    ("semicolons", "First point; second point", "First point, second point"),

    # --- Misc symbols ---
    ("backslash", "path\\to\\file", "path to file"),
    ("ampersand", "Tom & Jerry", "Tom and Jerry"),
    ("pipe", "option A | option B", "option A option B"),

    # --- Edge cases ---
    ("empty string", "", ""),
    ("none passthrough", None, None),
    ("already clean", "The meeting starts at three.", "The meeting starts at three."),
    ("whitespace cleanup", "too   many   spaces", "too many spaces"),

    # --- Combined ---
    ("complex combo",
     "Move -> Jump -> attack",
     "Move then Jump then attack"),
    ("code in sentence",
     "Call update_user_profile() to fix it",
     "Call update user profile to fix it"),
]

for label, input_text, expected in cases:
    if test(label, input_text, expected):
        passed += 1
    else:
        failed += 1

print(f"\n{'=' * 40}")
print(f"Results: {passed} passed, {failed} failed out of {passed + failed}")
if failed:
    print("SOME TESTS FAILED")
    sys.exit(1)
else:
    print("ALL TESTS PASSED")
