"""Probe 6 — Spike B: subprocess-per-meeting (long-lived stream-json).

Goal: model the alternative production case where one `claude -p` process
is alive for the whole meeting. Parent feeds user messages over stdin in
stream-json format and reads replies off stdout.

The stream-json input shape is undocumented (see GH issue 24594). We probe
empirically: try the obvious envelope `{"type":"user","message":{"role":"user","content":"..."}}`
and verify a `result` event comes back. If that fails we can iterate on the shape.

Conversation (same as Spike A so we can compare apples-to-apples):
  T1: "What is 2+2? Reply with just the number."
  T2: "Now multiply that by 3."
  T3: "Now subtract 1."

Reports per-turn latency (time from stdin write to terminal `result` event)
plus subprocess startup/teardown wall time.

Usage:
  ANTHROPIC_API_KEY= python3 cli_probe_06_per_meeting.py
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

TURNS = [
    "What is 2+2? Reply with just the number, nothing else.",
    "Now multiply that by 3. Reply with just the number, nothing else.",
    "Now subtract 1. Reply with just the number, nothing else.",
]


def reader_thread(stream, q):
    """Read stdout line-by-line and push parsed JSON onto a queue."""
    for line in stream:
        line = line.strip()
        if not line:
            continue
        try:
            q.put(("event", json.loads(line)))
        except json.JSONDecodeError:
            q.put(("raw", line))
    q.put(("eof", None))


def send_user_message(proc, text):
    """Write one stream-json envelope to claude's stdin."""
    envelope = {
        "type": "user",
        "message": {"role": "user", "content": text},
    }
    proc.stdin.write(json.dumps(envelope) + "\n")
    proc.stdin.flush()


def collect_until_result(q, timeout):
    """Pull events from queue until we see a `result` event or timeout.

    Returns (assistant_text, system_init_event_or_None, result_event_or_None,
             all_events_seen, error_string_or_None).
    """
    deadline = time.monotonic() + timeout
    text_parts = []
    init_evt = None
    result_evt = None
    events = []
    while time.monotonic() < deadline:
        try:
            kind, payload = q.get(timeout=0.5)
        except Empty:
            continue
        if kind == "eof":
            return "".join(text_parts).strip(), init_evt, result_evt, events, "stream_eof"
        if kind == "raw":
            events.append({"_raw": payload})
            continue
        events.append(payload)
        etype = payload.get("type")
        if etype == "system" and payload.get("subtype") == "init":
            init_evt = payload
        elif etype == "assistant":
            content = (payload.get("message") or {}).get("content") or []
            for block in content:
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
        elif etype == "result":
            result_evt = payload
            return "".join(text_parts).strip(), init_evt, result_evt, events, None
    return "".join(text_parts).strip(), init_evt, result_evt, events, "timeout"


def main():
    cmd = [
        "claude",
        "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--no-session-persistence",
    ]
    print(f"[parent] spawning long-lived: {' '.join(cmd)}")
    t_spawn = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env={**os.environ},
    )

    out_q = Queue()
    err_lines = []
    out_thread = threading.Thread(target=reader_thread, args=(proc.stdout, out_q), daemon=True)
    err_thread = threading.Thread(target=lambda: err_lines.extend(proc.stderr), daemon=True)
    out_thread.start()
    err_thread.start()

    spawn_elapsed = time.monotonic() - t_spawn
    print(f"[parent] subprocess up in {spawn_elapsed:.2f}s")

    results = []
    fatal_error = None
    for i, user_msg in enumerate(TURNS, 1):
        print(f"\n[turn {i}] sending: {user_msg!r}")
        t_turn_start = time.monotonic()
        try:
            send_user_message(proc, user_msg)
        except BrokenPipeError as e:
            fatal_error = f"broken pipe on turn {i}: {e}"
            break
        text, init_evt, result_evt, events, err = collect_until_result(out_q, timeout=120)
        turn_elapsed = time.monotonic() - t_turn_start
        if err:
            print(f"[turn {i}] FAILED ({err}) after {turn_elapsed:.2f}s. saw {len(events)} events.")
            print(f"[turn {i}] first 3 events: {events[:3]}")
            print(f"[turn {i}] stderr tail: {err_lines[-10:] if err_lines else '(none)'}")
            fatal_error = err
            break
        api_key_source = (init_evt or {}).get("apiKeySource")
        print(f"[turn {i}] {turn_elapsed:.2f}s — apiKey={api_key_source!r}, reply={text!r}, "
              f"result.subtype={result_evt.get('subtype') if result_evt else None!r}")
        results.append({
            "elapsed": turn_elapsed,
            "text": text,
            "api_key_source": api_key_source,
            "events": len(events),
        })

    # Close stdin to signal end of conversation; collect any trailing events.
    t_teardown = time.monotonic()
    try:
        proc.stdin.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.terminate()
        proc.wait(timeout=5)
    teardown_elapsed = time.monotonic() - t_teardown

    Path(HERE / "probe6_run.log").write_text(
        json.dumps({
            "spawn_elapsed": spawn_elapsed,
            "teardown_elapsed": teardown_elapsed,
            "turns": results,
            "fatal_error": fatal_error,
            "exit_code": proc.returncode,
        }, indent=2)
    )
    Path(HERE / "probe6_stderr.txt").write_text("".join(err_lines) or "")

    print()
    print("=" * 60)
    print("PROBE 6 (spike B: subprocess-per-meeting) RESULTS")
    print("=" * 60)
    print(f"  spawn:    {spawn_elapsed:.2f}s")
    for i, r in enumerate(results, 1):
        print(f"  turn {i}:   {r['elapsed']:.2f}s -> {r['text']!r} ({r['events']} events)")
    print(f"  teardown: {teardown_elapsed:.2f}s")
    print(f"  exit:     {proc.returncode}")
    if fatal_error:
        print(f"  fatal:    {fatal_error}")
        print(f"  stderr tail: {err_lines[-10:] if err_lines else '(none)'}")
    if results:
        avg = sum(r["elapsed"] for r in results) / len(results)
        total = sum(r["elapsed"] for r in results)
        print(f"  total turn time: {total:.2f}s, avg/turn: {avg:.2f}s")
    final_text = results[-1]["text"] if results else ""
    correct = final_text.strip().rstrip(".") == "11"
    all_subscription = all(r["api_key_source"] == "none" for r in results) if results else False
    print(f"  final answer correct (==11): {correct}")
    print(f"  all turns under subscription auth: {all_subscription}")
    ok = correct and all_subscription and len(results) == len(TURNS) and not fatal_error
    print(f"\nPASS: {ok}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
