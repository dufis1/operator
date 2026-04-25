"""Probe 8 — partial-message stream event shape.

Goal: confirm the shape of partial-message events emitted with
`--include-partial-messages`, so claude_cli.complete_streaming() can
parse incremental text deltas and feed paragraphs to on_paragraph.

Sends one prompt that asks for a multi-paragraph reply, captures every
event verbatim, and prints a summary of types + a couple of sample
partial events for shape inspection.

Usage:
  ANTHROPIC_API_KEY= python3 cli_probe_08_partial_messages.py
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Queue, Empty

HERE = Path(__file__).resolve().parent


def reader_thread(stream, q):
    for line in stream:
        line = line.strip()
        if not line:
            continue
        try:
            q.put(("event", json.loads(line)))
        except json.JSONDecodeError:
            q.put(("raw", line))
    q.put(("eof", None))


def main():
    cmd = [
        "claude", "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--include-partial-messages",
    ]
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, env={**os.environ},
    )
    q = Queue()
    threading.Thread(target=reader_thread, args=(proc.stdout, q), daemon=True).start()

    prompt = (
        "Write a 3-paragraph haiku about cold pizza, with a blank line "
        "between paragraphs. No commentary, just the three paragraphs."
    )
    envelope = {"type": "user", "message": {"role": "user", "content": prompt}}
    proc.stdin.write(json.dumps(envelope) + "\n")
    proc.stdin.flush()

    events = []
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            kind, payload = q.get(timeout=0.5)
        except Empty:
            continue
        if kind == "eof":
            break
        if kind == "raw":
            events.append({"_raw": payload})
            continue
        events.append(payload)
        if payload.get("type") == "result":
            break

    proc.stdin.close()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.terminate()

    Path(HERE / "probe8_stream.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events)
    )

    # Tally event types and sub-types.
    type_counts = {}
    for e in events:
        if "_raw" in e:
            type_counts["_raw"] = type_counts.get("_raw", 0) + 1
            continue
        t = e.get("type", "?")
        st = e.get("subtype")
        key = f"{t}/{st}" if st else t
        type_counts[key] = type_counts.get(key, 0) + 1

    print("=" * 60)
    print("PROBE 8 (partial-messages shape) RESULTS")
    print("=" * 60)
    print(f"total events: {len(events)}")
    for k, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {k}: {c}")

    # Print first 3 events (system-init usually) and the first 5 partial events.
    print("\n--- first 3 events ---")
    for e in events[:3]:
        print(json.dumps(e)[:300])
    print("\n--- first 5 stream_event events (partial messages) ---")
    streamed = [e for e in events if e.get("type") == "stream_event"][:5]
    for e in streamed:
        print(json.dumps(e)[:400])

    print("\n--- result event ---")
    result_evts = [e for e in events if e.get("type") == "result"]
    for e in result_evts:
        print(json.dumps(e)[:600])

    print(f"\nartifact saved: {HERE/'probe8_stream.jsonl'}")


if __name__ == "__main__":
    main()
