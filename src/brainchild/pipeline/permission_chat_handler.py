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


def _human_size(n):
    """Compact byte-size: '845 B', '12.3 KB', '4.2 MB'."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


# Field names whose values are *imperative* — they describe what's about
# to happen and the user needs to see them verbatim to make a sensible
# yes/no decision. Same principle as Bash command staying verbatim.
# Length-only collapsing rules don't apply to these.
_IMPERATIVE_FIELD_NAMES = {
    "url", "path", "file_path", "command",
    "query", "pattern", "search",
    "notebook_path",
}
_IMPERATIVE_MAX_LEN = 1000  # cap so a pathological case can't blow up chat


def _show_imperative(value):
    """Render an imperative field value verbatim, with a generous cap.

    URLs / paths / commands almost never legitimately exceed 1KB; if
    they do, head…tail truncate so chat doesn't break, but never
    collapse to a size hint (the safety check needs the literal value).
    """
    s = value if isinstance(value, str) else repr(value)
    if len(s) > _IMPERATIVE_MAX_LEN:
        head = s[: _IMPERATIVE_MAX_LEN // 2 - 1]
        tail = s[-(_IMPERATIVE_MAX_LEN // 2 - 1):]
        s = f"{head}…{tail}"
    return s


def _format_terse(tool_name, args):
    """One-line summary that hides bulk content but keeps imperative fields.

    Bash commands are NEVER summarized — the user's safety check depends
    on seeing the literal command. Other tools collapse content/blob
    fields into size hints.
    """
    if tool_name == "Bash":
        cmd = args.get("command", "")
        if len(cmd) > 300:
            cmd = cmd[:290] + "…"
        return f"Bash: {cmd}"
    # Read-only / discovery tools (auto-approved by default — these
    # surface mostly via the progress narrator). Keep names short and
    # lead with what the user cares about: which file/pattern.
    if tool_name == "Read":
        return f"Read {args.get('file_path', '?')}"
    if tool_name == "Grep":
        pat = args.get("pattern", "?")
        path = args.get("path", "")
        return f"Grep {pat!r}" + (f" in {path}" if path else "")
    if tool_name == "Glob":
        return f"Glob {args.get('pattern', '?')}"
    if tool_name == "LS":
        return f"LS {args.get('path', '?')}"
    if tool_name == "WebSearch":
        return f"WebSearch {args.get('query', '?')}"
    if tool_name == "Write":
        path = args.get("file_path", "?")
        size = _human_size(len(args.get("content") or ""))
        return f"Write {path} ({size})"
    if tool_name == "Edit":
        path = args.get("file_path", "?")
        return f"Edit {path}"
    if tool_name == "MultiEdit":
        path = args.get("file_path", "?")
        n = len(args.get("edits") or [])
        return f"MultiEdit {path} ({n} hunks)"
    if tool_name == "NotebookEdit":
        path = args.get("notebook_path", "?")
        return f"NotebookEdit {path}"
    if tool_name == "WebFetch":
        url = args.get("url", "?")
        prompt = (args.get("prompt") or "").strip()
        if len(prompt) > 80:
            prompt = prompt[:77] + "…"
        return f"WebFetch {url} — {prompt}" if prompt else f"WebFetch {url}"
    if tool_name == "Task":
        desc = args.get("description") or args.get("prompt") or ""
        if len(desc) > 120:
            desc = desc[:117] + "…"
        return f"Task: {desc}" if desc else "Task (no description)"
    # Unknown tool — compact fallback. Imperative fields (url/path/command/
    # …) are shown verbatim regardless of length: they describe *what* the
    # tool will do, and hiding them defeats the safety check. Bulky payload
    # fields collapse to size hints. Anything short renders verbatim.
    parts = []
    for k, v in args.items():
        if k in _IMPERATIVE_FIELD_NAMES:
            parts.append(f"{k}={_show_imperative(v)}")
            continue
        r = v if isinstance(v, str) else repr(v)
        if len(r) > 80:
            parts.append(f"{k}=({_human_size(len(r))})")
        else:
            parts.append(f"{k}={r}")
    body = ", ".join(parts)
    return f"{tool_name}: {body}" if body else tool_name


# Friendly-name lookup for MCP server prefixes — keeps plain-mode prompts
# out of underscore-noise. Anything not listed renders the raw server
# slug as a fallback (e.g. "Slack", "Notion" if those aren't here yet).
_MCP_SERVER_FRIENDLY = {
    "sentry":               "Sentry",
    "linear":               "Linear",
    "github":               "GitHub",
    "claude_ai_Linear":     "Linear",
    "claude_ai_Gmail":      "Gmail",
    "claude_ai_Google_Calendar": "Google Calendar",
    "claude_ai_Google_Drive":    "Google Drive",
    "claude-ai-linear":     "Linear",
    "claude-ai-gmail":      "Gmail",
    "claude-ai-google-calendar": "Google Calendar",
    "claude-ai-google-drive":    "Google Drive",
}

# Verb hints for the `mcp__<server>__<verb>_<subject>` naming convention
# most servers follow. Lets us turn unfamiliar tool names into plausible
# English without enumerating every tool.
_MCP_VERB_FRIENDLY = {
    "get":     "look up",
    "list":    "list",
    "find":    "find",
    "search":  "search for",
    "fetch":   "fetch",
    "save":    "save",
    "create":  "create",
    "update":  "update",
    "delete":  "delete",
    "remove":  "remove",
    "send":    "send",
    "post":    "post",
    "analyze": "analyze",
    "extract": "extract",
}


def _friendly_mcp_name(tool_name):
    """Decompose mcp__<server>__<rest> into (friendly_server, action_phrase).

    Returns (server, phrase) on success, (None, None) if the name doesn't
    follow the convention. The phrase tries to read like English — "look
    up the issue" rather than "get_issue".
    """
    if not tool_name.startswith("mcp__"):
        return None, None
    parts = tool_name.split("__", 2)
    if len(parts) < 3:
        return None, None
    _, server, rest = parts
    friendly = _MCP_SERVER_FRIENDLY.get(server, server.replace("_", " ").replace("-", " "))
    # Split verb_subject on first underscore (or hyphen, some servers use it).
    sep = "_" if "_" in rest else ("-" if "-" in rest else "")
    if sep:
        verb_raw, subject = rest.split(sep, 1)
    else:
        verb_raw, subject = rest, ""
    verb = _MCP_VERB_FRIENDLY.get(verb_raw.lower(), verb_raw.replace("_", " ").replace("-", " "))
    subject = subject.replace("_", " ").replace("-", " ").strip()
    if subject:
        phrase = f"{verb} a {subject}" if not subject.startswith(("a ", "an ", "the ")) else f"{verb} {subject}"
    else:
        phrase = verb
    return friendly, phrase


def _format_plain(tool_name, args):
    """Plain-English summary of a tool call, suitable for non-developers.

    Used for both the confirmation prompt ("Want me to … ?") and the
    progress narrator ("Working: …"). The caller wraps with the right
    framing — this function returns just the action phrase. Imperative
    fields (URLs, file paths, commands) are still shown so the user
    knows *which* file or command, just embedded in conversational
    language.
    """
    if tool_name == "Bash":
        cmd = args.get("command", "")
        if len(cmd) > 200:
            cmd = cmd[:190] + "…"
        return f"run a shell command: `{cmd}`"
    if tool_name == "Read":
        return f"read the file `{args.get('file_path', '?')}`"
    if tool_name == "Grep":
        pat = args.get("pattern", "?")
        return f"search the code for `{pat}`"
    if tool_name == "Glob":
        return f"find files matching `{args.get('pattern', '?')}`"
    if tool_name == "LS":
        return f"list the contents of `{args.get('path', '?')}`"
    if tool_name == "WebSearch":
        return f"search the web for `{args.get('query', '?')}`"
    if tool_name == "ToolSearch":
        # Pure metadata lookup; never user-facing in plain mode at the
        # imperative level, but the narrator may still surface it.
        return "look up some tool details"
    if tool_name == "Write":
        return f"write a new file at `{args.get('file_path', '?')}`"
    if tool_name == "Edit":
        return f"edit `{args.get('file_path', '?')}`"
    if tool_name == "MultiEdit":
        n = len(args.get("edits") or [])
        return f"make {n} edit{'s' if n != 1 else ''} to `{args.get('file_path', '?')}`"
    if tool_name == "NotebookEdit":
        return f"edit the notebook `{args.get('notebook_path', '?')}`"
    if tool_name == "WebFetch":
        return f"fetch `{args.get('url', '?')}`"
    if tool_name == "Task":
        desc = args.get("description") or "handle a sub-task"
        if len(desc) > 80:
            desc = desc[:77] + "…"
        return f"spin off a sub-agent to {desc.lower().rstrip('.')}"
    # MCP tool? Try to translate via the naming convention.
    server, phrase = _friendly_mcp_name(tool_name)
    if server and phrase:
        return f"{phrase} in {server}"
    # Unknown tool — generic fallback. Honest rather than confidently
    # wrong. Imperative fields (if any) still surface verbatim so the
    # user has SOMETHING to evaluate.
    imperative_bits = [
        f"{k}={_show_imperative(v)}"
        for k, v in args.items()
        if k in _IMPERATIVE_FIELD_NAMES
    ]
    detail = f" ({', '.join(imperative_bits)})" if imperative_bits else ""
    return f"use the {tool_name} tool{detail}"


def _format_verbose(tool_name, args):
    """Verbatim parameter dump with head…tail truncation for long values."""
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


def _format_confirmation(tool_name, tool_input):
    """Render the tool call as a chat-friendly confirmation prompt.

    Voice is per-bot: `agent.voice: plain | technical` in
    agents/<name>/config.yaml.

      plain     — meeting-friendly. "Want me to read the file
                  `/path/x.py`? (yes/no)" — good for non-developers.
      technical — developer-flavored. Shows tool name + args verbatim
                  with bulk-content collapsed: "Run? Write /tmp/x.py
                  (1.2 KB) OK?"

    Bash commands and other imperative fields (URLs, file paths) stay
    verbatim in BOTH modes — the user's approval decision depends on
    seeing the literal target.
    """
    args = tool_input or {}
    voice = getattr(config, "VOICE", "plain")
    if voice == "technical":
        # Power-user mode: full param dump with head…tail truncation.
        # Falls through to _format_verbose for max transparency.
        return _format_verbose(tool_name, args)
    return f"Want me to {_format_plain(tool_name, args)}? (yes/no)"


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
