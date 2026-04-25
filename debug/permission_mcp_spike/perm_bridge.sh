#!/bin/bash
# PreToolUse hook bridge — forwards inner-claude's tool_use details to the
# parent (probe) process via $REQ_PIPE, blocks until the parent writes a
# decision to $RESP_PIPE, then prints that decision as the hook's JSON
# stdout (per Claude Code's PreToolUse hook contract).
#
# Args: $1 = REQ_PIPE  (write tool details here)
#       $2 = RESP_PIPE (read decision from here)
#
# stdin: claude provides JSON with `tool_name`, `tool_input`, `tool_use_id`.
# stdout: must be a single JSON object of shape
#   {"hookSpecificOutput": {"hookEventName": "PreToolUse",
#                            "permissionDecision": "allow"|"deny"|...,
#                            "permissionDecisionReason": "..."}}
# exit 0 with stdout = JSON  → claude reads decision
# exit 2                     → blocks tool, feeds stderr to claude
# any other                  → non-blocking error, claude proceeds anyway

set -euo pipefail

REQ_PIPE="$1"
RESP_PIPE="$2"

# Forward the tool_use details to parent (parent reads them off REQ_PIPE).
# Claude provides JSON on stdin like {"tool_name":"...","tool_input":{...},...}.
# We echo it through unchanged so the parent has full context.
INPUT=$(cat)
echo "$INPUT" > "$REQ_PIPE"

# Block until parent writes decision to RESP_PIPE.
DECISION=$(cat "$RESP_PIPE")

# Wrap parent's decision in the hookSpecificOutput envelope claude expects.
# Parent sends raw {"permissionDecision":"...", "permissionDecisionReason":"..."}.
echo "$DECISION" | python3 -c "
import json, sys
inner = json.load(sys.stdin)
out = {
    'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': inner.get('permissionDecision', 'deny'),
        'permissionDecisionReason': inner.get('permissionDecisionReason', ''),
    }
}
print(json.dumps(out))
"
exit 0
