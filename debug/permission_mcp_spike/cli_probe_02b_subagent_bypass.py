"""Probe 2b — sub-agent permission visibility under bypassPermissions.

Hypothesis: sub-agents inherit parent's permission mode but their inner
tool calls do NOT route through our PreToolUse hook. With
--permission-mode bypassPermissions on parent, sub-agent should proceed
silently — files appear without our hook firing for them.

If this confirms: track A's design must accept sub-agent opacity. We
gate the parent's Task tool_use (visible in our hook), but inner
sub-agent ops are sandboxed (per-session worktree) instead of
chat-confirmed.
"""

import json
import os
import subprocess
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
            "PreToolUse": [{
                "matcher": "*",
                "hooks": [{"type": "command", "command": f"{BRIDGE} {req_pipe} {resp_pipe}", "timeout": 60}]
            }]
        }
    }
    Path(tmp, "settings.json").write_text(json.dumps(settings))

    target_a = Path(tmp) / "alpha.txt"
    target_b = Path(tmp) / "beta.txt"
    task = (
        f"Use the Task tool to dispatch a general-purpose sub-agent. Tell it: "
        f"'Use Write to create {target_a} (contents `A`). Then Write to create "
        f"{target_b} (contents `B`). Done.'"
    )

    cmd = [
        "claude", "-p", task,
        "--settings", str(Path(tmp, "settings.json")),
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
    ]

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
                tr = json.loads(line)
                print(f"[parent] [{t:.2f}s] hook FIRED: {tr.get('tool_name')}")
                decisions.append({"t": t, "tool": tr.get("tool_name"), "input": tr.get("tool_input", {})})
                resp_out.write(json.dumps({
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "probe2b",
                }) + "\n")
                resp_out.flush()
    except FileNotFoundError:
        pass

    stdout, stderr = proc.communicate(timeout=300)
    elapsed = time.monotonic() - t_start

    print()
    print("=" * 60)
    print("PROBE 2B RESULTS — sub-agent under bypassPermissions")
    print("=" * 60)
    print(f"exit code:           {proc.returncode}")
    print(f"elapsed:             {elapsed:.2f}s")
    print(f"hook fires (total):  {len(decisions)}")
    print(f"alpha.txt landed:    {target_a.exists()}")
    print(f"beta.txt landed:     {target_b.exists()}")
    print()
    for d in decisions:
        print(f"  [{d['t']:6.2f}s] {d['tool']}")

    events = [json.loads(l) for l in stdout.splitlines() if l.strip()]
    subagent_writes = [
        block for e in events if e.get("type") == "assistant"
        for block in (e.get("message") or {}).get("content") or []
        if block.get("type") == "tool_use" and block.get("name") == "Write"
        and e.get("parent_tool_use_id")
    ]
    print(f"\nstream-json sub-agent Write count:  {len(subagent_writes)}")
    print(f"hook Write count:                    {sum(1 for d in decisions if d['tool']=='Write')}")
    print()
    if target_a.exists() and target_b.exists() and sum(1 for d in decisions if d['tool']=='Write') == 0:
        print("✓ CONFIRMED: sub-agent's Write calls bypass our PreToolUse hook entirely.")
        print("  Files were written, but our hook never fired for them.")
        print("  Top-level Agent (Task) dispatch DID fire (visible in decisions list).")
    elif sum(1 for d in decisions if d['tool']=='Write') >= 2:
        print("? sub-agent's Writes DID fire our hook (unexpected).")
    else:
        print("? Inconclusive — sub-agent didn't write the files.")

    Path(HERE / "probe2b_stream.jsonl").write_text(stdout)


if __name__ == "__main__":
    main()
