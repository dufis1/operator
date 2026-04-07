"""
ChatRunner — polling loop that reads meeting chat and responds via LLM.

Usage:
    runner = ChatRunner(connector, llm)
    runner.run(meeting_url)   # blocks until stop() is called
"""
import logging
import threading
import time

import config

log = logging.getLogger(__name__)

POLL_INTERVAL = 1.5  # seconds between read_chat() calls


class ChatRunner:
    """Polls meeting chat and responds to messages."""

    def __init__(self, connector, llm):
        self._connector = connector
        self._llm = llm
        self._stop_event = threading.Event()
        # Track messages we've sent so we can ignore our own echoes
        self._own_messages: set[str] = set()
        # Track message IDs we've already processed
        self._seen_ids: set[str] = set()

    def run(self, meeting_url):
        """Join the meeting and start the chat polling loop."""
        log.info(f"ChatRunner: joining {meeting_url}")
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
                else:
                    log.error(f"ChatRunner: join failed: {reason}")
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
        while not self._stop_event.is_set():
            try:
                messages = self._connector.read_chat()
            except Exception as e:
                log.warning(f"ChatRunner: read_chat failed: {e}")
                messages = []

            for msg in messages:
                msg_id = msg.get("id", "")
                text = msg.get("text", "").strip()

                # Skip already-processed messages
                if msg_id and msg_id in self._seen_ids:
                    continue
                if msg_id:
                    self._seen_ids.add(msg_id)

                # Skip empty messages
                if not text:
                    continue

                # Skip our own messages (sender field is empty for now,
                # so we match on exact text we recently sent)
                if text in self._own_messages:
                    self._own_messages.discard(text)
                    continue

                log.info(f"ChatRunner: new message id={msg_id!r} text={text!r}")
                self._handle_message(text)

            self._stop_event.wait(POLL_INTERVAL)

    def _handle_message(self, text):
        """Process a single chat message via LLM."""
        try:
            reply = self._llm.ask(text)
        except Exception as e:
            log.error(f"ChatRunner: LLM call failed: {e}")
            return
        log.info(f"ChatRunner: sending reply={reply!r}")
        self._own_messages.add(reply)
        try:
            self._connector.send_chat(reply)
        except Exception as e:
            log.error(f"ChatRunner: send_chat failed: {e}")
            self._own_messages.discard(reply)
