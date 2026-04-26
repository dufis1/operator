"""
Permission handler that round-trips PreToolUse decisions through meeting chat.

Plugged into ClaudeCLIProvider via set_permission_handler(). Invoked from
the provider's pump thread on every PreToolUse event. Tools matching an
entry in config.PERMISSIONS_AUTO_APPROVE are approved silently; tools
matching config.PERMISSIONS_ALWAYS_ASK — and anything on neither list —
post a confirmation prompt to chat and block until the user replies
(yes/ok/sure => allow, anything else => deny with the user's text as the
reason). always_ask is checked first so an explicit deny pattern beats a
broad allow pattern.

Entries are fnmatch glob patterns. Literal tool names (`Read`, `Bash`)
match exactly; entries containing `*`, `?`, or `[` match by glob —
`mcp__sentry__get_*` covers every read tool from the Sentry MCP server.

Threading: this runs on the provider's pump thread. The handler reads
chat directly from connector.read_chat() while waiting for a reply and
claims consumed messages by adding their IDs to runner._seen_ids — so
the main polling loop doesn't re-feed the user's "ok" to the LLM.
"""
import fnmatch
import logging
import re
import threading
import time

from brainchild import config

log = logging.getLogger(__name__)


_GLOB_CHARS = ("*", "?", "[")


def _matches_any(tool_name, patterns):
    """Return True if tool_name matches any entry in patterns.

    Bare names (no glob characters) match exactly — same shape as the
    pre-pattern set-membership check. Entries with `*`, `?`, or `[` are
    fnmatch globs. Empty / None patterns is a no-op (False).
    """
    if not patterns:
        return False
    for pat in patterns:
        if not pat:
            continue
        if any(c in pat for c in _GLOB_CHARS):
            if fnmatch.fnmatchcase(tool_name, pat):
                return True
        elif tool_name == pat:
            return True
    return False


# Hard upper bound on how long a single permission request can wait for a
# user reply. Set generous — meetings can pause, the user can be talking,
# read chat slowly. After this we auto-deny so the subprocess isn't stuck.
REPLY_TIMEOUT_SECONDS = 600
POLL_INTERVAL = 0.5

# Maximum length of a single tool argument value rendered into the chat
# confirmation prompt. Long values are head…tail-truncated so a 50KB Write
# `content` argument doesn't blow up the chat panel.
ARG_RENDER_MAX = 200
ARG_RENDER_HEAD = 90
ARG_RENDER_TAIL = 90


_AFFIRM_PATTERNS = [
    re.compile(r"\b(yes|ok|okay|sure|approve|approved|confirmed|yep|yeah|y)\b", re.I),
]


def _is_yes(text):
    """Best-effort yes detection, modeled on chat_runner._handle_confirmation."""
    lower = text.lower().strip()
    if "go ahead" in lower or "do it" in lower:
        return True
    return any(p.search(lower) for p in _AFFIRM_PATTERNS)


def _format_confirmation(tool_name, tool_input):
    """Render the tool call as a chat-friendly confirmation prompt.

    Mirrors chat_runner._request_confirmation's shape so the user gets a
    consistent visual across both track-B MCP confirmations and track-A
    PreToolUse confirmations.
    """
    args = tool_input or {}
    if not args:
        body = "  (no arguments)"
    else:
        lines = []
        for k, v in args.items():
            r = v if isinstance(v, str) else repr(v)
            if len(r) > ARG_RENDER_MAX:
                head = r[:ARG_RENDER_HEAD]
                tail = r[-ARG_RENDER_TAIL:]
                r = f"{head}…{tail}"
            lines.append(f"  • {k}: {r}")
        body = "\n".join(lines)
    return f"Run {tool_name}?\n{body}\nOK?"


class PermissionChatHandler:
    """Callable that resolves PreToolUse decisions via meeting chat round-trip.

    Construct once per meeting and set on ClaudeCLIProvider via
    set_permission_handler(). Auto-approves tools in `auto_approve`,
    asks the user in chat for everything else.

    The `runner` reference is needed for two things only:
      - runner._send: serialized chat send that records the message in
        _own_messages so we don't re-read our own confirmation prompt.
      - runner._seen_ids / runner._own_messages: claim consumed user
        replies so the main loop doesn't feed them to the LLM.
    """

    def __init__(self, connector, runner, auto_approve, always_ask):
        self._connector = connector
        self._runner = runner
        # Preserve list ordering so a wizard / config author can layer
        # narrower rules on top of broader globs deterministically.
        self._auto_approve = list(auto_approve or [])
        self._always_ask = list(always_ask or [])
        # Serialize concurrent requests. Tool calls are sequential per
        # turn, but a misbehaving sub-agent or future parallel-tool-use
        # path could fire two — lock makes round-trips strictly ordered.
        self._lock = threading.Lock()

    def __call__(self, tool_name, tool_input):
        # always_ask wins over auto_approve so users can pin a specific
        # deny (e.g. mcp__sentry__analyze_issue_with_seer) on top of a
        # broad allow (mcp__sentry__*). Same precedent as the legacy
        # confirm_tools / read_tools split for track-B bots.
        if _matches_any(tool_name, self._always_ask):
            with self._lock:
                return self._round_trip(tool_name, tool_input)
        if _matches_any(tool_name, self._auto_approve):
            log.info(f"PermissionChatHandler: auto-approve {tool_name!r}")
            return {
                "permissionDecision": "allow",
                "permissionDecisionReason": "auto-approved by config (auto_approve list)",
            }
        with self._lock:
            return self._round_trip(tool_name, tool_input)

    def _round_trip(self, tool_name, tool_input):
        prompt = _format_confirmation(tool_name, tool_input)
        log.info(f"PermissionChatHandler: asking user about {tool_name!r}")
        try:
            self._runner._send(prompt, kind="confirmation")
        except Exception as e:
            log.error(f"PermissionChatHandler: failed to post confirmation: {e}")
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": f"could not post confirmation to chat: {e}",
            }

        reply = self._await_reply(REPLY_TIMEOUT_SECONDS)
        if reply is None:
            log.warning(
                f"PermissionChatHandler: no reply for {tool_name!r} within {REPLY_TIMEOUT_SECONDS}s — denying"
            )
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"no chat reply within {REPLY_TIMEOUT_SECONDS}s; defaulting to deny"
                ),
            }
        if _is_yes(reply):
            return {
                "permissionDecision": "allow",
                "permissionDecisionReason": f"user approved in chat: {reply!r}",
            }
        return {
            "permissionDecision": "deny",
            "permissionDecisionReason": f"user replied (treated as deny): {reply!r}",
        }

    def _await_reply(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                messages = self._connector.read_chat()
            except Exception as e:
                log.warning(f"PermissionChatHandler: read_chat failed: {e}")
                time.sleep(POLL_INTERVAL)
                continue
            for msg in messages:
                msg_id = msg.get("id", "")
                text = (msg.get("text") or "").strip()
                sender = (msg.get("sender") or "").strip()
                if not text:
                    continue
                if msg_id and msg_id in self._runner._seen_ids:
                    continue
                # Skip our own echoes (matches chat_runner._loop logic)
                if sender and sender.lower() == config.AGENT_NAME.lower():
                    continue
                if not sender and text in self._runner._own_messages:
                    continue
                # New user reply — claim it so the main loop doesn't
                # re-feed it to the LLM as a normal message.
                if msg_id:
                    self._runner._seen_ids.add(msg_id)
                log.info(f"PermissionChatHandler: reply received: {text!r}")
                return text
            time.sleep(POLL_INTERVAL)
        return None
