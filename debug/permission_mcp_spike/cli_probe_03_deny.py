"""Probe 3 — CLI path: deny semantics.

Question: when our PreToolUse hook returns deny, what does inner-claude
actually do? Does it abort entirely, retry the same tool, pivot to a
different approach, or surface the deny reason to the user?

This determines our chat UX: if deny means "claude tries again with a
different tool" we have a clean retry semantics for free; if it means
"claude gives up" we need to phrase the chat UI accordingly.

Test: ask inner-claude to write a file. Hook denies. Observe what
claude does next via stream-json.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGE = HERE / "perm_bridge.sh"


def main():
    tmp = tempfile.mkdtemp(prefix="claude-perm-spike-")
    req_pipe = Path(tmp) / "request.pipe"
    resp_pipe = Path(tmp) / "response.pipe"
    os.mkfifo(req_pipe, 0o600)
    os.mkfifo(resp_pipe, 0o600)

    settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{BRIDGE} {req_pipe} {resp_pipe}",
                            "timeout": 120,
                        }
                    ],
                }
            ]
        }
    }
    settings_path = Path(tmp) / "settings.json"
    settings_path.write_text(json.dumps(settings))

    target = Path(tmp) / "should_not_exist.txt"
    task = (
        f"Use the Write tool to create the file {target} "
        f"with the contents 'should_be_blocked'. "
        f"If the write fails, just report the failure. Do NOT retry."
    )

    cmd = [
        "claude", "-p", task,
        "--settings", str(settings_path),
        "--output-format", "stream-json",
        "--verbose",
        "--include-hook-events",
        "--permission-mode", "default",
    ]

    print("[parent] spawning deny probe — first hook call denies, subsequent allow")
    t_start = time.monotonic()
    proc = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env={**os.environ},
    )

    decisions = []
    try:
        with open(req_pipe, "r") as req_in, open(resp_pipe, "w") as resp_out:
            while True:
                line = req_in.readline()
                if not line:
                    break
                t = time.monotonic() - t_start
                tool_request = json.loads(line)
                tool_name = tool_request.get("tool_name", "?")

                # Deny the FIRST Write call; allow everything else.
                if tool_name == "Write" and not any(
                    d["tool"] == "Write" and d["decision"] == "deny" for d in decisions
                ):
                    decision_str = "deny"
                    reason = "probe3: simulated user rejection — please don't write that file"
                else:
                    decision_str = "allow"
                    reason = f"probe3: auto-allow {tool_name}"
                print(f"[parent] [{t:.2f}s] {tool_name} → {decision_str.upper()}")
                decisions.append({"t": t, "tool": tool_name, "decision": decision_str})
                resp_out.write(json.dumps({
                    "permissionDecision": decision_str,
                    "permissionDecisionReason": reason,
                }) + "\n")
                resp_out.flush()
    except FileNotFoundError:
        pass

    stdout, stderr = proc.communicate(timeout=180)
    elapsed = time.monotonic() - t_start

    print()
    print("=" * 60)
    print("PROBE 3 RESULTS — deny semantics")
    print("=" * 60)
    print(f"claude exit code:  {proc.returncode}")
    print(f"total elapsed:     {elapsed:.2f}s")
    print(f"hook calls:        {len(decisions)}")
    print(f"target landed:     {target.exists()}  (expected: False)")
    print()
    for d in decisions:
        print(f"  [{d['t']:6.2f}s] {d['tool']:15s} → {d['decision']}")

    # What did claude do after the deny?
    events = [json.loads(l) for l in stdout.splitlines() if l.strip()]
    final_result = next((e for e in events if e.get("type") == "result"), None)
    if final_result:
        print()
        print(f"final result subtype:    {final_result.get('subtype')}")
        print(f"is_error:                {final_result.get('is_error')}")
        print(f"stop_reason:             {final_result.get('stop_reason')}")
        print(f"terminal_reason:         {final_result.get('terminal_reason')}")
        print(f"permission_denials:      {final_result.get('permission_denials')}")
        result_text = final_result.get("result", "")
        print(f"\nfinal result text (first 400 chars):")
        print(result_text[:400])

    Path(HERE / "probe3_stream.jsonl").write_text(stdout)
    Path(HERE / "probe3_stderr.txt").write_text(stderr or "")


if __name__ == "__main__":
    main()
