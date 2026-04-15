"""
LLM integration for Operator.

Wraps a provider-agnostic chat interface with a system prompt. Conversation
history lives in a MeetingRecord (JSONL file on disk, one line per observed
chat message) — `ask()` replays the tail of that record on each call. Tool
calls and tool results are protocol-level and stay in a small in-memory
scratchpad that clears when the tool loop ends.
"""
import logging
import config
from pipeline.guardrails import validate_tool_result, log_rejection
from pipeline.providers import ContextOverflowError
from pipeline.meeting_record import MeetingRecord

log = logging.getLogger(__name__)


class LLMClient:
    """Sends prompts to an LLM provider and builds context from a MeetingRecord.

    Typical use:
        record = MeetingRecord(slug="pgy-qauk-frn")
        client = LLMClient(provider, record=record)
        reply = client.ask("What's the plan?")

    If `record` is None, an in-memory MeetingRecord is created automatically.
    """

    def __init__(self, provider, record: MeetingRecord | None = None):
        self._provider = provider
        self._record = record if record is not None else MeetingRecord(slug=None)
        # In-memory tool-loop scratchpad — assistant tool_call messages and
        # tool_result messages that are protocol-level (not chat content).
        # Cleared at the start of every new user turn and after the final
        # assistant text that closes a tool loop.
        self._scratch: list[dict] = []
        self._max_messages = config.HISTORY_MESSAGES
        self._system_prompt = config.SYSTEM_PROMPT
        self._max_tokens = config.MAX_TOKENS
        # Session-local set of first names already greeted. Resets on restart
        # so a fresh Operator process will re-greet participants once.
        self._greeted: set[str] = set()
        # Cached text of the current MCP-status block so inject_mcp_status can
        # be called more than once per session without stacking duplicates.
        self._mcp_status_text: str = ""

    def set_record(self, record: MeetingRecord):
        """Attach (or replace) the MeetingRecord backing this client."""
        self._record = record

    def inject_mcp_hints(self, servers: dict):
        """Append per-server hints from config to the system prompt."""
        sections = []
        for name, srv in servers.items():
            hints = srv.get("hints", "").strip()
            if hints:
                sections.append(f"\n{name} tool usage:\n{hints}")
        if sections:
            self._system_prompt += "\n" + "\n".join(sections)
            log.info(f"LLM injected MCP hints for: {', '.join(s for s, srv in servers.items() if srv.get('hints', '').strip())}")

    def inject_mcp_status(
        self,
        loaded: list[str],
        failed_to_load: dict[str, str],
        disabled_runtime: dict[str, str] | None = None,
    ):
        """Tell the LLM which MCP servers are actually available this session.

        Safe to call more than once — replaces any previously-injected block
        rather than stacking.
        """
        disabled_runtime = disabled_runtime or {}
        parts = []
        if loaded:
            parts.append(f"MCP servers loaded this session: {', '.join(loaded)}.")
        if failed_to_load:
            names = ", ".join(failed_to_load.keys())
            parts.append(
                f"MCP servers that FAILED to load: {names}. "
                f"If the user asks about tools from a failed server, tell them it failed "
                f"to load and to check /tmp/operator.log — do not pretend the tool exists."
            )
        if disabled_runtime:
            names = ", ".join(disabled_runtime.keys())
            parts.append(
                f"MCP servers DISABLED this session after repeated tool-call failures: {names}. "
                f"Do not attempt these tools. If the user asks, tell them the server was "
                f"disabled due to repeated failures and to check /tmp/operator.log."
            )

        if self._mcp_status_text and self._mcp_status_text in self._system_prompt:
            self._system_prompt = self._system_prompt.replace(self._mcp_status_text, "")
        self._mcp_status_text = ("\n" + " ".join(parts)) if parts else ""
        if self._mcp_status_text:
            self._system_prompt += self._mcp_status_text
        log.info(
            f"LLM injected MCP status — loaded={loaded} "
            f"failed_to_load={list(failed_to_load.keys())} "
            f"disabled_runtime={list(disabled_runtime.keys())}"
        )

    def inject_github_user(self, login: str):
        """Add the authenticated GitHub login to the system prompt."""
        hint = f"\nThe authenticated GitHub user's login is \"{login}\". Always use \"{login}\" as the owner — never guess from chat display names."
        self._system_prompt += hint
        log.info(f"LLM injected GitHub user: {login}")

    def _tail_messages(self) -> list[dict]:
        """Build neutral-shape messages from the meeting record tail.

        On each participant's first appearance this session, append
        `config.FIRST_CONTACT_HINT` (rendered with their first name) to
        that one message so the LLM knows to greet them by name once.
        """
        entries = self._record.tail(self._max_messages)
        agent = (config.AGENT_NAME or "").lower()
        hint_template = config.FIRST_CONTACT_HINT
        messages: list[dict] = []
        for e in entries:
            kind = e.get("kind", "chat")
            if kind not in ("chat", "caption"):
                continue
            sender = (e.get("sender") or "").strip()
            text = e.get("text", "")
            if sender.lower() == agent:
                messages.append({"role": "assistant", "content": text})
                continue
            first = sender.split()[0] if sender else ""
            if kind == "caption":
                # Spoken context — not addressed to the bot. Prefix so the
                # LLM can weigh it as ambient room talk vs direct chat.
                content = f"[spoken] {first}: {text}" if first else f"[spoken] {text}"
                messages.append({"role": "user", "content": content})
                continue
            content = f"{first}: {text}" if first else text
            if hint_template and first and first not in self._greeted:
                self._greeted.add(first)
                try:
                    hint = hint_template.format(first_name=first)
                except (KeyError, IndexError):
                    hint = hint_template
                content = f"{content} {hint}"
            messages.append({"role": "user", "content": content})
        return messages

    def _build_messages(self, extra_user_msg: str | None = None) -> list[dict]:
        """tail (chat) + scratch (in-flight tool loop) + optional trailing user turn."""
        messages = self._tail_messages()
        messages.extend(self._scratch)
        if extra_user_msg is not None:
            messages.append({"role": "user", "content": extra_user_msg})
        return messages

    def ask(self, message, record=True, tools=None):
        """Send a message to the LLM and return the reply.

        ChatRunner is expected to have appended this message to the meeting
        record already, so it appears once in the tail. If `record` is False,
        the record was NOT pre-populated and we pass `message` as an extra
        trailing user turn without persisting it.

        When tools is None: returns a plain string.
        When tools is provided (chat + MCP): returns a dict with either:
          {"type": "text", "content": "..."}
          {"type": "tool_call", "id": "...", "name": "...", "arguments": {...}}
        """
        # New user turn — drop any stale tool-loop scratch
        self._scratch = []

        if record:
            messages = self._build_messages()
        else:
            messages = self._build_messages(extra_user_msg=message)

        log.info(
            f"LLM ask model={config.LLM_MODEL} max_tokens={self._max_tokens} "
            f"messages={len(messages)} prompt_chars={len(message)} "
            f"tools={len(tools) if tools else 0}"
        )
        log.debug(f"LLM message: {message}")

        try:
            response = self._provider.complete(
                system=self._system_prompt,
                messages=messages,
                model=config.LLM_MODEL,
                max_tokens=self._max_tokens,
                tools=tools,
            )
        except ContextOverflowError:
            log.warning("LLM context length exceeded — shrinking replay window")
            self._max_messages = max(2, self._max_messages // 2)
            return {"type": "context_overflow"}
        except Exception as e:
            log.error(f"LLM API call failed: {e}", exc_info=True)
            raise

        if not tools:
            reply = response.text
            log.info(f"LLM reply=\"{reply[:80]}\"")
            return reply

        if response.tool_calls:
            tc = response.tool_calls[0]
            log.info(f"LLM tool_call name={tc.name}")
            self._scratch.append({
                "role": "assistant",
                "content": response.text,
                "tool_calls": response.tool_calls,
            })
            return {
                "type": "tool_call",
                "id": tc.id,
                "name": tc.name,
                "arguments": tc.args,
            }
        reply = response.text
        log.info(f"LLM reply=\"{reply[:80]}\"")
        return {"type": "text", "content": reply}

    def ask_stream(self, message):
        """Stream tokens from the LLM. Does NOT record to the meeting record."""
        messages = self._build_messages(extra_user_msg=message)
        log.info(f"LLM ask_stream model={config.LLM_MODEL} max_tokens={self._max_tokens} messages={len(messages)} prompt_chars={len(message)}")
        try:
            yield from self._provider.complete_stream(
                system=self._system_prompt,
                messages=messages,
                model=config.LLM_MODEL,
                max_tokens=self._max_tokens,
            )
        except Exception as e:
            log.error(f"LLM API stream failed: {e}", exc_info=True)
            raise

    def warmup(self):
        """Fire a 1-token request to establish the TCP/TLS connection pool."""
        try:
            self._provider.warmup(config.LLM_MODEL)
            log.info("LLM warmup complete")
        except Exception as e:
            log.warning(f"LLM warmup failed (non-fatal): {e}")

    def send_tool_result(self, tool_call_id: str, tool_name: str, result_content: str, tools=None):
        """Feed a tool result back to the model and get the next response.

        Returns a plain string when tools is None, or a dict like ask() when
        tools is provided.
        """
        if len(result_content) > config.TOOL_RESULT_MAX_CHARS:
            shown = config.TOOL_RESULT_MAX_CHARS
            total = len(result_content)
            log.warning(f"LLM tool result too large: {total} chars — archiving, showing hint")
            result_content = (
                f"[tool result archived — {shown} of {total} chars shown. "
                f"Call the tool again with a narrower scope to retrieve more]"
            )
        is_safe, reason = validate_tool_result(result_content)
        if not is_safe:
            log_rejection(tool_name, {"result_length": len(result_content)}, reason, "post-execution")
            result_content = (
                f"[tool result blocked — {reason}. "
                f"Try requesting a text file or a different resource.]"
            )
        self._scratch.append({
            "role": "tool_result",
            "tool_call_id": tool_call_id,
            "content": result_content,
        })
        log.info(f"LLM send_tool_result tool={tool_name} result_len={len(result_content)}")

        messages = self._build_messages()
        try:
            response = self._provider.complete(
                system=self._system_prompt,
                messages=messages,
                model=config.LLM_MODEL,
                max_tokens=self._max_tokens,
                tools=tools,
            )
        except ContextOverflowError:
            log.warning("LLM context length exceeded in tool result — shrinking replay window")
            self._max_messages = max(2, self._max_messages // 2)
            self._scratch = []
            return {"type": "context_overflow"}
        except Exception as e:
            log.error(f"LLM tool result call failed: {e}", exc_info=True)
            raise

        if tools and response.tool_calls:
            tc = response.tool_calls[0]
            log.info(f"LLM follow-up tool_call name={tc.name}")
            self._scratch.append({
                "role": "assistant",
                "content": response.text,
                "tool_calls": response.tool_calls,
            })
            return {
                "type": "tool_call",
                "id": tc.id,
                "name": tc.name,
                "arguments": tc.args,
            }

        reply = response.text
        log.info(f"LLM tool summary=\"{reply[:80]}\"")
        # Final text closes the tool loop — drop protocol scratch; the summary
        # lands in the meeting record via ChatRunner._send().
        self._scratch = []
        if tools:
            return {"type": "text", "content": reply}
        return reply
