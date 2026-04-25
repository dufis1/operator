"""Probe 5 — Spike A: subprocess-per-turn.

Goal: model the production case where each LLMClient.ask() spawns a fresh
`claude -p` process. Measure cold-start cost across multiple turns of a
conversation. The parent owns history (mirrors anthropic.py): each turn
re-sends the full transcript as the prompt.

Conversation:
  T1: "What is 2+2? Reply with just the number."
  T2: "Now multiply that by 3. Reply with just the number."
  T3: "Now subtract 1. Reply with just the number."

For each turn we:
  - rebuild the prompt as a flat transcript (User: / Assistant: lines)
  - spawn claude -p, no --resume, no settings, no hooks
  - parse stream-json, time it end-to-end
  - capture apiKeySource on the first turn

Reports per-turn latency + total wall time + correctness check (final answer == 11).

Usage:
  ANTHROPIC_API_KEY= python3 cli_probe_05_per_turn.py
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

TURNS = [
    "What is 2+2? Reply with just the number, nothing else.",
    "Now multiply that by 3. Reply with just the number, nothing else.",
    "Now subtract 1. Reply with just the number, nothing else.",
]


def render_transcript(history, new_user_msg):
    """Replay prior turns as a flat transcript (parent owns context)."""
    lines = []
    for role, text in history:
        lines.append(f"{role}: {text}")
    lines.append(f"User: {new_user_msg}")
    lines.append("Assistant:")
    return "\n".join(lines)


def run_one_turn(prompt):
    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--no-session-persistence",  # don't pollute the user's session log
    ]
    t_start = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ},
    )
    stdout, stderr = proc.communicate(timeout=120)
    elapsed = time.monotonic() - t_start

    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    api_key_source = None
    text_parts = []
    for e in events:
        if e.get("type") == "system" and e.get("subtype") == "init":
            api_key_source = e.get("apiKeySource")
        elif e.get("type") == "assistant":
            content = (e.get("message") or {}).get("content") or []
            for block in content:
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))

    return {
        "elapsed": elapsed,
        "rc": proc.returncode,
        "api_key_source": api_key_source,
        "text": "".join(text_parts).strip(),
        "stderr": stderr,
        "stdout": stdout,
    }


def main():
    history = []
    results = []
    t_total_start = time.monotonic()

    for i, user_msg in enumerate(TURNS, 1):
        prompt = render_transcript(history, user_msg)
        print(f"\n[turn {i}] sending prompt ({len(prompt)} chars):\n  {prompt!r}")
        r = run_one_turn(prompt)
        print(f"[turn {i}] {r['elapsed']:.2f}s — rc={r['rc']}, "
              f"apiKey={r['api_key_source']!r}, reply={r['text']!r}")
        if r["rc"] != 0:
            print(f"[turn {i}] FAILED. stderr tail:")
            print("\n".join(r["stderr"].splitlines()[-10:]))
            break
        history.append(("User", user_msg))
        history.append(("Assistant", r["text"]))
        results.append(r)

    total_elapsed = time.monotonic() - t_total_start

    # Persist artifacts.
    Path(HERE / "probe5_run.log").write_text(
        json.dumps({
            "turns": [{
                "elapsed": r["elapsed"],
                "rc": r["rc"],
                "api_key_source": r["api_key_source"],
                "text": r["text"],
            } for r in results],
            "total_elapsed": total_elapsed,
        }, indent=2)
    )

    print()
    print("=" * 60)
    print("PROBE 5 (spike A: subprocess-per-turn) RESULTS")
    print("=" * 60)
    for i, r in enumerate(results, 1):
        print(f"  turn {i}: {r['elapsed']:.2f}s -> {r['text']!r}")
    print(f"  total wall: {total_elapsed:.2f}s across {len(results)} turns")
    if results:
        avg = sum(r["elapsed"] for r in results) / len(results)
        print(f"  avg per turn: {avg:.2f}s")
    final_text = results[-1]["text"] if results else ""
    correct = final_text.strip().rstrip(".") == "11"
    all_subscription = all(r["api_key_source"] == "none" for r in results)
    print(f"  final answer correct (==11): {correct}")
    print(f"  all turns under subscription auth: {all_subscription}")
    ok = correct and all_subscription and len(results) == len(TURNS)
    print(f"\nPASS: {ok}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
