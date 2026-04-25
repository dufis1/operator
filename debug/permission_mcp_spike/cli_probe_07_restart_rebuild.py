"""Probe 7 — Spike B failure mode: mid-meeting subprocess restart.

Validates that we can recover from a mid-meeting subprocess death by
spawning a fresh `claude -p` and re-feeding history as a synthesized
opener. Without this working, Spike B is fragile.

Conversation (same as probes 5/6 for comparability):
  T1: "What is 2+2?"  -> 4
  T2: "Now multiply that by 3."  -> 12
  --- KILL subprocess here ---
  T3: "Now subtract 1."  -> should still be 11

After kill, we spawn a new subprocess and prepend a synthetic opener
that summarizes the meeting state so far. Two seeding strategies tested
to see which is more robust:

  Strategy 1 — replay as separate user/assistant turns
    Fire each prior (user, assistant) pair into the new subprocess as
    real stream-json envelopes BEFORE the new user message. Mimics what
    actually happened.

  Strategy 2 — single synthesized opener
    One user message: "You are joining a meeting in progress. Here is
    the conversation so far: ... Now respond to this latest message: ..."

Both should produce 11 if the rebuild works. We measure latency for each
since strategy 1 pays per-turn replay cost.

Usage:
  ANTHROPIC_API_KEY= python3 cli_probe_07_restart_rebuild.py
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

ORIGINAL_TURNS = [
    ("What is 2+2? Reply with just the number, nothing else.", "4"),
    ("Now multiply that by 3. Reply with just the number, nothing else.", "12"),
]
FINAL_TURN = "Now subtract 1. Reply with just the number, nothing else."


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


def spawn():
    cmd = [
        "claude",
        "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--no-session-persistence",
    ]
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, env={**os.environ},
    )
    q = Queue()
    threading.Thread(target=reader_thread, args=(proc.stdout, q), daemon=True).start()
    return proc, q


def send_user(proc, text):
    envelope = {"type": "user", "message": {"role": "user", "content": text}}
    proc.stdin.write(json.dumps(envelope) + "\n")
    proc.stdin.flush()


def collect_until_result(q, timeout=120):
    deadline = time.monotonic() + timeout
    text_parts, init_evt, result_evt = [], None, None
    while time.monotonic() < deadline:
        try:
            kind, payload = q.get(timeout=0.5)
        except Empty:
            continue
        if kind == "eof":
            return "".join(text_parts).strip(), init_evt, result_evt, "eof"
        if kind == "raw":
            continue
        etype = payload.get("type")
        if etype == "system" and payload.get("subtype") == "init":
            init_evt = payload
        elif etype == "assistant":
            content = (payload.get("message") or {}).get("content") or []
            for block in content:
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
        elif etype == "result":
            return "".join(text_parts).strip(), init_evt, payload, None
    return "".join(text_parts).strip(), init_evt, result_evt, "timeout"


def kill(proc):
    try:
        proc.stdin.close()
    except Exception:
        pass
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def run_strategy_1_replay():
    """Re-fire each prior (user, assistant) turn into the new subprocess."""
    print("\n--- Strategy 1: replay prior turns as separate envelopes ---")
    proc, q = spawn()
    t_start = time.monotonic()

    # Replay each prior turn. We have to fake the assistant side because
    # claude's stream-json input only accepts type:"user" envelopes (we
    # cannot inject assistant messages directly). So we tell the new
    # claude what it previously said as part of the user message.
    for i, (user_msg, prior_reply) in enumerate(ORIGINAL_TURNS, 1):
        replay_msg = (
            f"[meeting replay turn {i}] User said: {user_msg!r} "
            f"You replied: {prior_reply!r}. Acknowledge with a single dot."
        )
        send_user(proc, replay_msg)
        text, _, result, err = collect_until_result(q)
        if err:
            kill(proc)
            return {"strategy": 1, "ok": False, "error": err, "elapsed": time.monotonic() - t_start}
        print(f"  replay {i}: ack={text!r} ({result.get('subtype') if result else None})")

    # Now send the actual new turn.
    t_final = time.monotonic()
    send_user(proc, FINAL_TURN)
    text, _, result, err = collect_until_result(q)
    final_elapsed = time.monotonic() - t_final
    total_elapsed = time.monotonic() - t_start
    kill(proc)
    return {
        "strategy": 1, "final_text": text, "final_subtype": result.get("subtype") if result else None,
        "final_elapsed": final_elapsed, "total_elapsed": total_elapsed, "error": err,
        "ok": (text.strip().rstrip(".") == "11" and not err),
    }


def run_strategy_2_synth_opener():
    """Single synthesized opener summarizing the conversation so far."""
    print("\n--- Strategy 2: single synthesized opener ---")
    proc, q = spawn()
    t_start = time.monotonic()

    transcript = "\n".join(
        f"User: {u}\nAssistant: {a}"
        for u, a in ORIGINAL_TURNS
    )
    opener = (
        "You are picking up a conversation that was already in progress. "
        "Here is the transcript so far, followed by a new user message you "
        "should answer using the same context:\n\n"
        f"{transcript}\n\n"
        f"New user message: {FINAL_TURN}"
    )
    send_user(proc, opener)
    text, _, result, err = collect_until_result(q)
    final_elapsed = time.monotonic() - t_start
    kill(proc)
    return {
        "strategy": 2, "final_text": text, "final_subtype": result.get("subtype") if result else None,
        "final_elapsed": final_elapsed, "total_elapsed": final_elapsed, "error": err,
        "ok": (text.strip().rstrip(".") == "11" and not err),
    }


def main():
    # Live the original 2-turn convo so we have authentic answers in hand.
    # We don't actually need the live convo for the restart test (we just
    # need the (msg, reply) pairs), but running it once confirms the model
    # produces the expected answers under our setup.
    print("--- Phase 1: original 2-turn conversation (will be killed) ---")
    proc, q = spawn()
    for i, (user_msg, _expected) in enumerate(ORIGINAL_TURNS, 1):
        send_user(proc, user_msg)
        text, _, result, err = collect_until_result(q)
        if err:
            print(f"  pre-kill turn {i} failed: {err}")
            kill(proc)
            sys.exit(2)
        print(f"  turn {i}: {text!r} ({result.get('subtype') if result else None})")
    print("  killing subprocess...")
    kill(proc)

    # Now both restart strategies.
    s1 = run_strategy_1_replay()
    s2 = run_strategy_2_synth_opener()

    Path(HERE / "probe7_run.log").write_text(json.dumps([s1, s2], indent=2, default=str))

    print()
    print("=" * 60)
    print("PROBE 7 (B restart + rebuild) RESULTS")
    print("=" * 60)
    for s in [s1, s2]:
        print(f"  strategy {s['strategy']}: ok={s['ok']} "
              f"final={s.get('final_text')!r} "
              f"final_elapsed={s.get('final_elapsed', 0):.2f}s "
              f"total={s.get('total_elapsed', 0):.2f}s "
              f"err={s.get('error')!r}")
    overall_ok = s1["ok"] and s2["ok"]
    print(f"\nPASS: {overall_ok}")
    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
