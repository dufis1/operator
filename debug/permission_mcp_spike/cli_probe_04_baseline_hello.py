"""Probe 4 — baseline `claude -p` round-trip under subscription auth.

Goal: before forking spike A (subprocess-per-turn) vs B (subprocess-per-meeting),
confirm the trivial case still works. One-shot subprocess, no hooks, no settings,
no tools requested. Just: spawn claude with a hello prompt, read stream-json,
verify we got assistant text back and apiKeySource is "none".

This is the floor. If this fails, both A and B fail.

Usage:
  ANTHROPIC_API_KEY= python3 cli_probe_04_baseline_hello.py
                    ^ blank to force claude.ai/max subscription auth
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    cmd = [
        "claude",
        "-p", "Say the single word 'hello' and nothing else.",
        "--output-format", "stream-json",
        "--verbose",
    ]
    print(f"[parent] spawning: {' '.join(cmd)}")
    t_start = time.monotonic()

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ},
    )
    stdout, stderr = proc.communicate(timeout=60)
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
    assistant_text = []
    final_result = None
    for e in events:
        etype = e.get("type")
        if etype == "system" and e.get("subtype") == "init":
            api_key_source = e.get("apiKeySource")
        elif etype == "assistant":
            content = (e.get("message") or {}).get("content") or []
            for block in content:
                if block.get("type") == "text":
                    assistant_text.append(block.get("text", ""))
        elif etype == "result":
            final_result = e

    print()
    print("=" * 60)
    print("PROBE 4 (baseline hello) RESULTS")
    print("=" * 60)
    print(f"claude exit code:    {proc.returncode}")
    print(f"total elapsed:       {elapsed:.2f}s")
    print(f"apiKeySource:        {api_key_source!r}")
    print(f"assistant text:      {''.join(assistant_text)!r}")
    if final_result:
        print(f"result.subtype:      {final_result.get('subtype')!r}")
        print(f"result.is_error:     {final_result.get('is_error')}")
    if stderr:
        print()
        print("--- stderr (last 10 lines) ---")
        print("\n".join(stderr.splitlines()[-10:]))

    Path(HERE / "probe4_stream.jsonl").write_text(stdout)
    Path(HERE / "probe4_stderr.txt").write_text(stderr or "")
    print(f"\nartifacts saved: {HERE/'probe4_stream.jsonl'}, {HERE/'probe4_stderr.txt'}")

    ok = (
        proc.returncode == 0
        and api_key_source == "none"
        and bool("".join(assistant_text).strip())
    )
    print()
    print(f"PASS: {ok}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
