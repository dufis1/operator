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

log = logging.getLogger(__name__)

POLL_INTERVAL = 0.5  # seconds between read_chat() calls
PARTICIPANT_CHECK_INTERVAL = 3  # seconds between participant count checks
ONE_ON_ONE_THRESHOLD = 2  # participant count at or below = 1-on-1 mode (skip wake phrase)


class ChatRunner:
    """Polls meeting chat and responds to messages."""

    def __init__(self, connector, llm, mcp_client=None):
        self._connector = connector
        self._llm = llm
        self._mcp = mcp_client
        self._stop_event = threading.Event()
        # Track messages we've sent so we can ignore our own echoes
        self._own_messages: set[str] = set()
        # Track message IDs we've already processed
        self._seen_ids: set[str] = set()
        # Pending tool call awaiting user confirmation
        self._pending_tool_call: dict | None = None
        # Track first names we've already responded to
        self._greeted: set[str] = set()

    def run(self, meeting_url):
        """Join the meeting and start the chat polling loop."""
        log.info(f"ChatRunner: joining {meeting_url}")
        # Skip join if connector was already started (e.g. for parallel MCP init)
        if not self._connector.join_status:
            self._connector.join(meeting_url)

        # Wait for browser to actually join (same logic as AgentRunner)
        join_status = self._connector.join_status
        if join_status:
            join_timeout = config.IDLE_TIMEOUT_SECONDS + 60
            if not join_status.ready.wait(timeout=join_timeout):
                log.error(f"ChatRunner: join timed out ({join_timeout}s)")
                self._connector.leave()
                return
            if not join_status.success:
                reason = join_status.failure_reason or "unknown"
                log.error(f"ChatRunner: join failed: {reason}")
                if "session_expired" in reason:
                    log.error("Re-export session: python scripts/auth_export.py")
                    print("\n❌ Not authenticated — run this to sign in:\n")
                    print("   python scripts/auth_export.py\n")
                elif "already_running" in reason:
                    print("\n⚠️  Another Operator session is already running.")
                    print("   Use --force to stop it and start a new one.\n")
                self._connector.leave()
                return
            if join_status.session_recovered:
                log.warning("ChatRunner: session recovered via cookie injection — "
                            "consider re-running scripts/auth_export.py")

        log.info("ChatRunner: joined — starting chat loop")
        self._loop()

    def stop(self):
        """Signal the polling loop to exit."""
        self._stop_event.set()

    def _loop(self):
        """Main polling loop."""
        last_participant_check = 0
        participant_count = 0
        while not self._stop_event.is_set():
            try:
                messages = self._connector.read_chat()
            except Exception as e:
                log.warning(f"ChatRunner: read_chat failed: {e}")
                messages = []

            # Periodically refresh participant count
            now = time.time()
            if now - last_participant_check >= PARTICIPANT_CHECK_INTERVAL:
                last_participant_check = now
                try:
                    new_count = self._connector.get_participant_count()
                    if new_count != participant_count:
                        log.info(f"ChatRunner: participant count changed {participant_count} → {new_count}")
                    participant_count = new_count
                except Exception as e:
                    log.warning(f"ChatRunner: get_participant_count failed: {e}")

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

                # If we're waiting for tool confirmation, any message is a response
                if self._pending_tool_call:
                    self._handle_confirmation(text)
                    continue

                # In 1-on-1 mode, every message is treated as addressed to us
                wake = config.CHAT_WAKE_PHRASE.lower()
                lower = text.lower()
                has_wake = wake in lower

                if has_wake or one_on_one:
                    # Strip the wake phrase if present
                    if has_wake:
                        prompt = re.sub(re.escape(config.CHAT_WAKE_PHRASE) + r'[,:]?\s*', '', text, count=1, flags=re.IGNORECASE).strip()
                    else:
                        prompt = text
                    if prompt:
                        # Include sender context for the LLM (first name only)
                        first_name = sender.split()[0] if sender else ""
                        llm_text = f"{first_name}: {prompt}" if first_name else prompt
                        if first_name and first_name not in self._greeted:
                            llm_text += f" (First time talking to {first_name})"
                            self._greeted.add(first_name)
                        self._handle_message(llm_text)
                else:
                    # Not addressed to us — store as context so LLM knows what was said
                    first_name = sender.split()[0] if sender else ""
                    context = f"{first_name}: {text}" if first_name else text
                    self._llm.add_context(context)
                    log.debug(f"ChatRunner: stored as context (no wake phrase)")

            self._own_messages -= own_matched

            self._stop_event.wait(POLL_INTERVAL)

    def _handle_message(self, text):
        """Process a single chat message via LLM."""
        try:
            tools = self._mcp.get_openai_tools() if self._mcp else None
            result = self._llm.ask(text, tools=tools)
        except Exception as e:
            log.error(f"ChatRunner: LLM call failed: {e}")
            return

        # No tools path — plain string (backward compat)
        if isinstance(result, str):
            self._send(result)
            return

        if result["type"] == "text":
            self._send(result["content"])
        elif result["type"] == "tool_call":
            self._request_confirmation(result)
        elif result["type"] == "context_overflow":
            self._send("Our conversation got too long — I've cleared the history. What would you like to do next?")

    def _request_confirmation(self, tool_call):
        """Ask user for confirmation before executing a tool."""
        self._pending_tool_call = tool_call
        name = tool_call["name"]
        args = tool_call["arguments"]

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
        self._send(msg)

    def _handle_confirmation(self, text):
        """Process user's yes/no response to a pending tool call."""
        lower = text.lower()

        affirmative = any(w in lower for w in
                          ("yes", "ok", "sure", "go ahead", "do it",
                           "approve", "confirmed", "yep", "yeah"))
        negative = any(w in lower for w in
                       ("no", "cancel", "don't", "stop", "nope",
                        "nah", "nevermind"))

        tc = self._pending_tool_call

        if negative:
            self._pending_tool_call = None
            self._send("OK, cancelled.")
            try:
                self._llm.send_tool_result(
                    tc["id"], tc["name"], "User cancelled this action.")
            except Exception as e:
                log.warning(f"ChatRunner: cancel result call failed: {e}")
            return

        if not affirmative:
            # Ambiguous — ask again
            self._send("Please confirm: yes to proceed, no to cancel.")
            return

        # User confirmed — execute the tool in a background thread with heartbeat + timeout
        self._pending_tool_call = None
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
        hard_timeout = config.TOOL_TIMEOUT_SECONDS
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
            self._send(f"That took too long — no response after {hard_timeout}s. Try again.")
            try:
                self._llm.send_tool_result(
                    tc["id"], tc["name"], f"Error: tool call timed out after {hard_timeout}s")
            except Exception:
                pass
            return

        if error_holder[0]:
            e = error_holder[0]
            log.error(f"ChatRunner: tool execution failed: {e}")
            self._send("Sorry, that tool call failed. Check the logs for details.")
            try:
                self._llm.send_tool_result(tc["id"], tc["name"], f"Error: {e}")
            except Exception:
                pass
            return

        tool_result = result_holder[0]

        # Feed result back to LLM — it may summarize or request another tool
        try:
            tools = self._mcp.get_openai_tools() if self._mcp else None
            result = self._llm.send_tool_result(tc["id"], tc["name"], tool_result, tools=tools)
        except Exception as e:
            log.error(f"ChatRunner: LLM summary failed: {e}")
            self._send("Tool succeeded but I couldn't summarize the result.")
            return

        if isinstance(result, str):
            self._send(result)
        elif result["type"] == "tool_call":
            self._request_confirmation(result)
        elif result["type"] == "context_overflow":
            self._send("Our conversation got too long — I've cleared the history. What would you like to do next?")
        else:
            self._send(result["content"])

    def _send(self, text):
        """Send a chat message and track it as our own."""
        self._own_messages.add(text)
        try:
            self._connector.send_chat(text)
        except Exception as e:
            log.error(f"ChatRunner: send_chat failed: {e}")
            self._own_messages.discard(text)
