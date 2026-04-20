"""
ChatRunner — polling loop that reads meeting chat and responds via LLM.

Usage:
    runner = ChatRunner(connector, llm)
    runner.run(meeting_url)   # blocks until stop() is called
"""
import logging
import re
import threading
import time

import config
from pipeline import ui
from pipeline.meeting_record import MeetingRecord, slug_from_url

log = logging.getLogger(__name__)

POLL_INTERVAL = 0.5  # seconds between read_chat() calls
PARTICIPANT_CHECK_INTERVAL = 3  # seconds between participant count checks
ONE_ON_ONE_THRESHOLD = 2  # participant count at or below = 1-on-1 mode (skip trigger phrase)

LOAD_SKILL_TOOL = "load_skill"  # synthetic local tool — not routed to MCP
_SLASH_RE = re.compile(r"^/([A-Za-z0-9_\-]+)\s*")


class ChatRunner:
    """Polls meeting chat and responds to messages."""

    def __init__(
        self,
        connector,
        llm,
        mcp_client=None,
        meeting_record: MeetingRecord | None = None,
        skills: list | None = None,
        skills_progressive: bool = True,
    ):
        self._connector = connector
        self._llm = llm
        self._mcp = mcp_client
        self._record = meeting_record
        self._stop_event = threading.Event()
        # Track messages we've sent so we can ignore our own echoes
        self._own_messages: set[str] = set()
        # Track message IDs we've already processed
        self._seen_ids: set[str] = set()
        # Pending tool call awaiting user confirmation
        self._pending_tool_call: dict | None = None
        # Skills — lookup by name; synthetic `load_skill` is offered only when
        # progressive disclosure is on AND there's at least one skill loaded.
        self._skills = {s.name: s for s in (skills or [])}
        self._skills_progressive = skills_progressive
        # SKILLS per-session counters for the summary log.
        self._turn_count = 0
        self._load_skill_calls = 0
        self._load_skill_by_name: dict[str, int] = {}
        # Self-intro on join. Background thread generates the text; main loop
        # posts it (so send_chat stays single-threaded). User-message
        # processing is deferred until the intro lands; messages that arrive
        # during the gap are persisted to the record as normal and buffered
        # for in-order replay once the intro posts.
        self._intro_ready = threading.Event()
        self._intro_text = ""
        self._intro_posted = not config.INTRO_ON_JOIN
        self._pre_intro_buffer: list[dict] = []

    def run(self, meeting_url):
        """Join the meeting and start the chat polling loop."""
        log.info(f"ChatRunner: joining {meeting_url}")
        # Open a meeting record for this URL if one wasn't provided.
        if self._record is None:
            slug = slug_from_url(meeting_url)
            self._record = MeetingRecord(
                slug=slug,
                meta={"meet_url": meeting_url},
            )
            self._llm.set_record(self._record)
        # Skip join if connector was already started (e.g. for parallel MCP init)
        if not self._connector.join_status:
            self._connector.join(meeting_url)

        # Wait for browser to actually join
        join_status = self._connector.join_status
        if join_status:
            join_timeout = config.LOBBY_WAIT_SECONDS + 60
            if not join_status.ready.wait(timeout=join_timeout):
                log.error(f"ChatRunner: join timed out ({join_timeout}s)")
                self._connector.leave()
                return
            if not join_status.success:
                reason = join_status.failure_reason or "unknown"
                log.error(f"ChatRunner: join failed: {reason}")
                if "session_expired" in reason:
                    log.error("Re-export session: python scripts/auth_export.py")
                    ui.err("Not authenticated — run: python scripts/auth_export.py")
                elif "already_running" in reason:
                    ui.warn("Another Operator session is already running. Use --force to stop it and start a new one.")
                else:
                    ui.err(f"Join failed: {reason}")
                self._connector.leave()
                return
            if join_status.session_recovered:
                log.warning("ChatRunner: session recovered via cookie injection — "
                            "consider re-running scripts/auth_export.py")

        log.info("ChatRunner: joined")
        ui.ok("Joined meeting — listening for chat.")
        if config.INTRO_ON_JOIN:
            threading.Thread(target=self._generate_intro, daemon=True).start()
        log.info("ChatRunner: starting chat loop")
        self._loop()

    def _generate_intro(self):
        """Background-thread LLM call for the self-intro.

        Stores the result in _intro_text and signals via _intro_ready. The
        main loop is responsible for sending it (so send_chat is never
        called off-thread). On generation failure, _intro_text stays empty
        and the main loop will skip the post.
        """
        try:
            self._intro_text = self._llm.intro()
        except Exception as e:
            log.error(f"ChatRunner: intro generation failed — skipping: {e}")
            self._intro_text = ""
        self._intro_ready.set()

    def stop(self):
        """Signal the polling loop to exit."""
        self._stop_event.set()
        self._log_skills_summary()

    def _log_skills_summary(self):
        """Emit the per-session SKILLS usage tally. Safe to call multiple times."""
        if not self._skills and not self._load_skill_calls:
            return
        by_name = dict(self._load_skill_by_name) if self._load_skill_by_name else {}
        log.info(
            f"SKILLS session summary: turns={self._turn_count} "
            f"load_skill_calls={self._load_skill_calls} by_name={by_name}"
        )

    def _loop(self):
        """Main polling loop."""
        last_participant_check = 0
        participant_count = 0
        saw_others = False
        alone_since = None
        while not self._stop_event.is_set():
            # Detect unexpected browser session death (crash, page loss, etc.)
            if not self._connector.is_connected():
                log.warning("ChatRunner: connector disconnected unexpectedly — exiting loop")
                ui.warn("Meeting connection lost — chat loop stopped.")
                break

            # Post the self-intro the first iteration after generation completes,
            # then drain anything that arrived during the gap.
            if not self._intro_posted and self._intro_ready.is_set():
                if self._intro_text:
                    self._send(self._intro_text)
                self._intro_posted = True
                if self._pre_intro_buffer:
                    log.info(f"ChatRunner: draining {len(self._pre_intro_buffer)} pre-intro msg(s)")
                    buffered = self._pre_intro_buffer
                    self._pre_intro_buffer = []
                    for buf in buffered:
                        self._dispatch_user_message(buf["text"], buf["one_on_one"])

            try:
                messages = self._connector.read_chat()
            except Exception as e:
                log.warning(f"ChatRunner: read_chat failed: {e}")
                messages = []

            # Bail out before doing any more work if shutdown fired while we
            # were blocked in read_chat — prevents a stray final iteration
            # (and its participant-count log) after SIGINT.
            if self._stop_event.is_set():
                break

            # Periodically refresh participant count
            now = time.time()
            if now - last_participant_check >= PARTICIPANT_CHECK_INTERVAL:
                last_participant_check = now
                try:
                    new_count = self._connector.get_participant_count()
                    if self._stop_event.is_set():
                        break
                    if new_count != participant_count:
                        log.info(f"ChatRunner: participant count changed {participant_count} → {new_count}")
                    participant_count = new_count
                except Exception as e:
                    log.warning(f"ChatRunner: get_participant_count failed: {e}")

                if participant_count > 1:
                    saw_others = True
                    alone_since = None
                elif saw_others and participant_count == 1:
                    if alone_since is None:
                        alone_since = now
                        log.info("ChatRunner: alone in meeting — grace timer started")
                    elif now - alone_since >= config.ALONE_EXIT_GRACE_SECONDS:
                        log.info(
                            f"ChatRunner: alone for {int(now - alone_since)}s — auto-leaving"
                        )
                        ui.ok("Everyone left — dropping from the meeting.")
                        self._connector.leave()
                        return

            one_on_one = participant_count <= ONE_ON_ONE_THRESHOLD

            # Track which own-message texts matched this batch so we can
            # discard AFTER the full batch — Meet creates multiple DOM
            # elements per message (different IDs, same text), so we must
            # keep the text in the set until all duplicates are filtered.
            own_matched = set()

            for msg in messages:
                msg_id = msg.get("id", "")
                text = msg.get("text", "").strip()
                sender = msg.get("sender", "").strip()

                # Skip already-processed messages
                if msg_id and msg_id in self._seen_ids:
                    continue
                if msg_id:
                    self._seen_ids.add(msg_id)

                # Skip empty messages
                if not text:
                    continue

                # Skip our own messages — prefer sender name, fall back to text match
                if sender and sender.lower() == config.AGENT_NAME.lower():
                    log.debug(f"ChatRunner: skipping own message (sender={sender!r})")
                    continue
                if not sender and text in self._own_messages:
                    log.debug(f"ChatRunner: skipping own message (text match)")
                    own_matched.add(text)
                    continue

                log.info(f"ChatRunner: new message sender={sender!r} id={msg_id!r} text={text!r} one_on_one={one_on_one}")

                # Persist every observed chat message to the meeting record —
                # this is the single source of truth the LLM replays from.
                # When a tool confirmation is pending, this message is a reply
                # to the harness ("ok"/"no"), not a turn in the real conversation.
                # Tag it `confirmation` so it's audited but filtered out of the
                # LLM prompt (pipeline/llm.py only replays kind in {chat, caption}).
                msg_kind = "confirmation" if self._pending_tool_call else "chat"
                if self._record is not None:
                    self._record.append(sender=sender, text=text, kind=msg_kind)

                # If we're waiting for tool confirmation, any message is a response
                if self._pending_tool_call:
                    self._handle_confirmation(text)
                    continue

                # Defer LLM-side processing until the self-intro has posted —
                # buffered messages drain in order at the top of the iteration
                # that observes the intro completing.
                if not self._intro_posted:
                    self._pre_intro_buffer.append({"text": text, "one_on_one": one_on_one})
                    continue

                self._dispatch_user_message(text, one_on_one)

            self._own_messages -= own_matched

            self._stop_event.wait(POLL_INTERVAL)

    def _tools_for_llm(self) -> list | None:
        """Combined tool list: MCP tools + synthetic `load_skill` when applicable."""
        tools = list(self._mcp.get_openai_tools()) if self._mcp else []
        if self._skills and self._skills_progressive:
            names = sorted(self._skills.keys())
            tools.append({
                "type": "function",
                "function": {
                    "name": LOAD_SKILL_TOOL,
                    "description": (
                        "Load the full instructions for a named skill from the available-skills "
                        "list. Call ONLY when the user's request clearly matches a skill's "
                        "description; never for unrelated chit-chat."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "enum": names,
                                "description": "Name of the skill to load.",
                            }
                        },
                        "required": ["name"],
                    },
                },
            })
        return tools or None

    def _dispatch_user_message(self, text: str, one_on_one: bool):
        """Trigger-check a chat message and route it to the LLM if addressed.

        Called both from the live polling loop and from the post-intro buffer
        drain. Pure routing — message persistence and seen-id tracking happen
        upstream, before this is invoked.
        """
        trigger = config.TRIGGER_PHRASE.lower()
        has_trigger = trigger in text.lower()
        if has_trigger or one_on_one:
            if has_trigger:
                prompt = re.sub(
                    re.escape(config.TRIGGER_PHRASE) + r'[,:]?\s*',
                    '', text, count=1, flags=re.IGNORECASE,
                ).strip()
            else:
                prompt = text
            if prompt:
                self._handle_message(prompt)
        else:
            log.debug("ChatRunner: stored as context (no trigger phrase)")

    def _handle_message(self, text):
        """Process a single chat message via LLM."""
        self._turn_count += 1
        # Slash-invocation fast path — /<name> (after trigger phrase is already stripped).
        # If it matches a loaded skill, prepend the body to the user message so the
        # LLM sees the full instructions without the extra load_skill round-trip.
        extra_system = ""
        m = _SLASH_RE.match(text)
        if m:
            candidate = m.group(1)
            skill = self._skills.get(candidate)
            if skill:
                log.info(f"SKILLS turn={self._turn_count} slash-invoke: {candidate}")
                self._load_skill_calls += 1
                self._load_skill_by_name[candidate] = self._load_skill_by_name.get(candidate, 0) + 1
                extra_system = (
                    f"\n\nThe user invoked the \"{candidate}\" skill. Follow these instructions "
                    f"for this turn:\n{skill.body}"
                )
            else:
                log.debug(f"SKILLS turn={self._turn_count} unknown slash token: /{candidate}")

        try:
            tools = self._tools_for_llm()
            result = self._llm.ask(text, tools=tools, extra_system=extra_system)
        except Exception as e:
            log.error(f"ChatRunner: LLM call failed: {e}")
            return

        # No tools path — plain string (backward compat)
        if isinstance(result, str):
            self._send(result)
            return

        self._dispatch_result(result)

    def _needs_confirmation(self, tool_call):
        """Return True if this tool call requires user confirmation.

        Policy is purely per-server config (no pipeline-level tool-name knowledge):
          1. If the server lists this tool in `confirm_tools`, always confirm.
          2. If the server lists this tool in `read_tools`, auto-execute.
          3. Otherwise, confirm — safe-by-default for tools the bundle didn't declare.
        """
        name = tool_call["name"]
        parts = name.split("__", 1)
        server = parts[0] if len(parts) == 2 else None
        tool = parts[1] if len(parts) == 2 else name

        if server and server in config.MCP_SERVERS:
            if tool in config.MCP_SERVERS[server]["confirm_tools"]:
                return True
            if tool in config.MCP_SERVERS[server]["read_tools"]:
                return False

        return True

    def _request_confirmation(self, tool_call):
        """Ask user for confirmation before executing a tool."""
        self._pending_tool_call = tool_call
        name = tool_call["name"]
        args = tool_call["arguments"]

        # Strip 'limit' the LLM injects unprompted on Linear list calls
        if name.startswith("linear__"):
            args.pop("limit", None)

        # "linear__create_issue" -> tool "create_issue", server "linear"
        parts = name.split("__", 1)
        display_server = parts[0] if len(parts) == 2 else ""
        display_tool = parts[1] if len(parts) == 2 else name

        arg_summary = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:5])

        msg = f"I'd like to run {display_tool}"
        if display_server:
            msg += f" via {display_server}"
        msg += f" with: {arg_summary}. OK?"
        log.info(f"ChatRunner: requesting confirmation for {name}")
        self._send(msg, kind="confirmation")

    def _handle_confirmation(self, text):
        """Process user's yes/no response to a pending tool call."""
        lower = text.lower()
        words = set(re.findall(r"\b\w+\b", lower))

        affirmative = bool(words & {
            "yes", "ok", "sure", "approve", "confirmed", "yep", "yeah"
        }) or "go ahead" in lower or "do it" in lower

        tc = self._pending_tool_call

        if affirmative:
            # Fall through to tool execution below
            pass
        else:
            # Not a clear yes — treat as a correction, not a cancellation.
            # Pass the user's feedback back to the LLM so it can re-propose
            # with adjusted parameters.
            self._pending_tool_call = None
            reason = f"User wants to adjust this call and said: \"{text}\" — re-propose the corrected tool call."
            try:
                tools = self._tools_for_llm()
                result = self._llm.send_tool_result(
                    tc["id"], tc["name"], reason, tools=tools)
            except Exception as e:
                log.warning(f"ChatRunner: correction result call failed: {e}")
                return

            self._dispatch_result(result)
            return

        # User confirmed — execute
        self._pending_tool_call = None
        self._execute_and_respond(tc)

    def _dispatch_result(self, result):
        """Route an LLM result (text, tool_call, or context_overflow)."""
        if isinstance(result, str):
            self._send(result)
        elif result["type"] == "text":
            self._send(result["content"])
        elif result["type"] == "tool_call":
            if result["name"] == LOAD_SKILL_TOOL:
                self._handle_load_skill(result)
            elif self._needs_confirmation(result):
                self._request_confirmation(result)
            else:
                self._execute_and_respond(result)
        elif result["type"] == "context_overflow":
            self._send("Our conversation got too long — I've cleared the history. What would you like to do next?")

    def _handle_load_skill(self, tc):
        """Resolve a load_skill call locally and feed the skill body back as the tool result."""
        name = (tc.get("arguments") or {}).get("name", "")
        skill = self._skills.get(name)
        available = ", ".join(sorted(self._skills.keys())) or "<none>"
        log.info(f"SKILLS turn={self._turn_count} load_skill called: {name!r} (available: {available})")
        self._load_skill_calls += 1
        self._load_skill_by_name[name] = self._load_skill_by_name.get(name, 0) + 1
        if skill:
            result_content = f"[skill: {skill.name}]\n{skill.body}"
        else:
            result_content = (
                f"Error: no skill named {name!r}. Available skills: {available}. "
                f"Proceed without a skill or ask the user to clarify."
            )
        try:
            tools = self._tools_for_llm()
            result = self._llm.send_tool_result(tc["id"], tc["name"], result_content, tools=tools)
        except Exception as e:
            log.error(f"ChatRunner: load_skill follow-up failed: {e}")
            self._send("Couldn't load that skill — check the logs.")
            return
        self._dispatch_result(result)

    def _execute_and_respond(self, tc):
        """Execute a tool call in a background thread with heartbeat + timeout, then feed result to LLM."""
        log.info(f"ChatRunner: auto-executing {tc['name']}")
        t_exec_start = time.monotonic()
        result_holder = [None]
        error_holder = [None]
        done_event = threading.Event()

        def _run_tool():
            try:
                result_holder[0] = self._mcp.execute_tool(tc["name"], tc["arguments"])
            except Exception as exc:
                error_holder[0] = exc
            finally:
                done_event.set()

        threading.Thread(target=_run_tool, daemon=True).start()

        heartbeat_interval = config.TOOL_HEARTBEAT_SECONDS
        hard_timeout = self._mcp.tool_timeout_for(tc["name"]) or config.TOOL_TIMEOUT_SECONDS
        deadline = time.time() + hard_timeout
        timed_out = False

        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                timed_out = True
                break
            if done_event.wait(timeout=min(heartbeat_interval, remaining)):
                break  # tool finished (success or error)
            # Still running — send heartbeat if deadline not reached
            if time.time() < deadline:
                self._send("Still working on that...")

        if timed_out:
            log.error(f"ChatRunner: tool {tc['name']} timed out after {hard_timeout}s")
            self._record_mcp_outcome(tc["name"], success=False)
            self._handle_tool_failure(
                tc,
                f"The tool call timed out after {hard_timeout} seconds with no response. "
                f"Tell the user plainly that it timed out, note that this often means the "
                f"service is slow or the task is too large, and suggest a narrower request.",
                fallback=f"That took too long — no response after {hard_timeout}s. Try a narrower request, or retry.",
            )
            return

        if error_holder[0]:
            e = error_holder[0]
            log.error(f"ChatRunner: tool execution failed: {e}")
            self._record_mcp_outcome(tc["name"], success=False)
            self._handle_tool_failure(
                tc,
                f"The tool call failed with this error: {e}\n\n"
                f"Tell the user what went wrong in one short sentence (reference the "
                f"specific cause from the error — 404, auth, network, missing arg, etc.), "
                f"and suggest one concrete next step. Do not say 'check the logs'.",
                fallback=f"That tool call failed: {str(e)[:200]}",
            )
            return

        self._record_mcp_outcome(tc["name"], success=True)

        tool_result = result_holder[0]
        tool_elapsed = time.monotonic() - t_exec_start
        result_size = len(tool_result) if isinstance(tool_result, str) else 0
        log.info(f"TIMING tool_exec={tool_elapsed:.1f}s name={tc['name']} result_bytes={result_size}")

        # Feed result back to LLM — it may summarize or request another tool
        try:
            tools = self._tools_for_llm()
            result = self._llm.send_tool_result(tc["id"], tc["name"], tool_result, tools=tools)
        except Exception as e:
            log.error(f"ChatRunner: LLM summary failed: {e}")
            self._send("Tool succeeded but I couldn't summarize the result.")
            return

        self._dispatch_result(result)

    def _handle_tool_failure(self, tc, error_signpost: str, fallback: str):
        """Let the LLM author the user-facing failure message.

        Signposts the tool result with the error + instructions (plain summary +
        suggested next step) and dispatches the follow-up. If the follow-up LLM
        call itself fails (rate limit, network), falls back to a terse one-liner
        so the user is never left silent.
        """
        try:
            tools = self._tools_for_llm()
            result = self._llm.send_tool_result(
                tc["id"], tc["name"], error_signpost, tools=tools,
            )
        except Exception as llm_err:
            log.error(f"ChatRunner: LLM error-summary call failed: {llm_err}")
            self._send(fallback)
            return
        self._dispatch_result(result)

    def _send(self, text, kind: str = "chat"):
        """Send a chat message, append it to the meeting record, and track it as our own.

        `kind` is persisted to the record but filtered by `pipeline/llm.py` when
        building the LLM prompt (only `chat` and `caption` are replayed).
        Harness plumbing — tool-confirmation prompts — passes `kind="confirmation"`
        so it's audited but invisible to the model (prevents the model from
        mimicking the harness's own wording back at the user).
        """
        self._own_messages.add(text)
        if self._record is not None:
            self._record.append(sender=config.AGENT_NAME, text=text, kind=kind)
        try:
            self._connector.send_chat(text)
        except Exception as e:
            log.error(f"ChatRunner: send_chat failed: {e}")
            self._own_messages.discard(text)

    def _record_mcp_outcome(self, tool_name: str, success: bool):
        """Record a tool-call outcome against its server; announce if it tripped.

        On the first failure that disables a server, sends one chat message and
        reinjects MCP status so the LLM's next turn sees the updated picture.
        """
        if not self._mcp:
            return
        server = self._mcp.server_for_tool(tool_name)
        if not server:
            return
        tripped = self._mcp.record_tool_result(server, success)
        if not tripped:
            return
        self._send(
            f"The {server} server seems to be having issues — skipping it for the rest of this session."
        )
        loaded = [
            n for n in config.MCP_SERVERS
            if n not in self._mcp.failed_servers and n not in self._mcp.disabled_servers
        ]
        try:
            self._llm.inject_mcp_status(
                loaded,
                self._mcp.failed_servers,
                self._mcp.disabled_servers,
            )
        except Exception as e:
            log.warning(f"ChatRunner: reinject mcp_status failed: {e}")
