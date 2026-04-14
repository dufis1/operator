"""
LLM integration for Operator.

Wraps a provider-agnostic chat interface with a system prompt and per-session
conversation history. The actual API transport lives in pipeline/providers/.
No macOS imports.
"""
import logging
import config
from pipeline.guardrails import validate_tool_result, log_rejection
from pipeline.providers import ContextOverflowError

log = logging.getLogger(__name__)

MAX_TRANSCRIPT_LINES = 100  # rolling transcript history limit

class LLMClient:
    """Sends prompts to an LLM provider and maintains per-session conversation history.

    Pass an LLMProvider at construction time:
        provider = OpenAIProvider(openai_client)
        client = LLMClient(provider)
        reply = client.ask("What's the plan?")
    """

    def __init__(self, provider):
        self._provider = provider
        self._history = []
        self._max_pairs = config.HISTORY_TURNS  # user+assistant pairs to keep
        self._system_prompt = config.SYSTEM_PROMPT
        self._max_tokens = config.MAX_TOKENS
        # Cached text of the current MCP-status block so inject_mcp_status can
        # be called more than once per session (e.g. after a runtime trip)
        # without stacking duplicate blocks in the system prompt.
        self._mcp_status_text: str = ""

    def inject_mcp_hints(self, servers: dict):
        """Append per-server hints from config to the system prompt.

        servers: config.MCP_SERVERS dict — only entries with non-empty
        'hints' values are injected.
        """
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

        Three buckets:
          - loaded: started and still healthy
          - failed_to_load: never started (startup-time config/binary failure)
          - disabled_runtime: started fine, then tripped the failure threshold mid-session

        Safe to call more than once — replaces any previously-injected block
        rather than stacking, so a mid-session re-inject after a runtime trip
        leaves exactly one status block in the prompt.
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
        """Add the authenticated GitHub login to the system prompt.

        This is dynamic (resolved at startup via get_me) so it lives in code,
        not in config hints.
        """
        hint = f"\nThe authenticated GitHub user's login is \"{login}\". Always use \"{login}\" as the owner — never guess from chat display names."
        self._system_prompt += hint
        log.info(f"LLM injected GitHub user: {login}")

    def ask(self, message, record=True, tools=None):
        """Send a message to the LLM and return the reply.

        When tools is None: returns a plain string.
        When tools is provided (chat + MCP): returns a dict with either:
          {"type": "text", "content": "..."}
          {"type": "tool_call", "id": "...", "name": "...", "arguments": {...}}

        record=False: result is NOT added to conversation history.
        Call record_exchange() later if you decide to use the result.
        """
        messages = [
            *self._history,
            {"role": "user", "content": message},
        ]
        log.info(f"LLM ask model={config.LLM_MODEL} max_tokens={self._max_tokens} history_msgs={len(self._history)} prompt_chars={len(message)} tools={len(tools) if tools else 0}")
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
            log.warning(f"LLM context length exceeded — clearing history")
            self._history = []
            return {"type": "context_overflow"}
        except Exception as e:
            log.error(f"LLM API call failed: {e}", exc_info=True)
            raise

        # No tools provided — return plain string
        if not tools:
            reply = response.text
            log.info(f"LLM reply=\"{reply[:80]}\"")
            if record:
                self._history.append({"role": "user", "content": message})
                self._history.append({"role": "assistant", "content": reply})
                self._trim_history()
            return reply

        # Tools provided — check if model wants to call one
        if response.tool_calls:
            tc = response.tool_calls[0]
            log.info(f"LLM tool_call name={tc.name}")
            if record:
                self._history.append({"role": "user", "content": message})
                self._history.append({
                    "role": "assistant",
                    "content": response.text,
                    "tool_calls": response.tool_calls,
                })
                self._trim_history()
            return {
                "type": "tool_call",
                "id": tc.id,
                "name": tc.name,
                "arguments": tc.args,
            }
        else:
            reply = response.text
            log.info(f"LLM reply=\"{reply[:80]}\"")
            if record:
                self._history.append({"role": "user", "content": message})
                self._history.append({"role": "assistant", "content": reply})
                self._trim_history()
            return {"type": "text", "content": reply}

    def ask_stream(self, message):
        """Stream tokens from the LLM. Yields token strings as they arrive.

        Does NOT record to history — call record_exchange() if you use the result.
        """
        messages = [
            *self._history,
            {"role": "user", "content": message},
        ]
        log.info(f"LLM ask_stream model={config.LLM_MODEL} max_tokens={self._max_tokens} history_msgs={len(self._history)} prompt_chars={len(message)}")
        log.debug(f"LLM message: {message}")
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
        """Fire a 1-token request to establish the TCP/TLS connection pool.

        Not recorded to history. Call once at startup in a background thread.
        """
        try:
            self._provider.warmup(config.LLM_MODEL)
            log.info("LLM warmup complete")
        except Exception as e:
            log.warning(f"LLM warmup failed (non-fatal): {e}")

    def record_exchange(self, message: str, reply: str):
        """Commit a user/assistant exchange to history without an API call."""
        self._history.append({"role": "user", "content": message})
        self._history.append({"role": "assistant", "content": reply})
        self._trim_history()

    def add_context(self, text: str):
        """Add a message to history as context without triggering a response."""
        self._history.append({"role": "user", "content": text})
        self._trim_history()

    def send_tool_result(self, tool_call_id: str, tool_name: str, result_content: str, tools=None):
        """Feed a tool result back to the model and get the next response.

        Call this after executing a tool call. The LLM will either summarize
        the result into a user-facing message, or request another tool call.

        Returns a plain string when tools is None,
        or a dict like ask() when tools is provided:
          {"type": "text", "content": "..."}
          {"type": "tool_call", "id": "...", "name": "...", "arguments": {...}}
        """
        if len(result_content) > config.TOOL_RESULT_MAX_CHARS:
            shown = config.TOOL_RESULT_MAX_CHARS
            total = len(result_content)
            log.warning(f"LLM tool result too large: {total} chars — archiving, showing hint")
            result_content = (
                f"[tool result archived — {shown} of {total} chars shown. "
                f"Call the tool again with a narrower scope to retrieve more]"
            )
        # Validate content for binary/non-text data before it enters history
        is_safe, reason = validate_tool_result(result_content)
        if not is_safe:
            log_rejection(tool_name, {"result_length": len(result_content)}, reason, "post-execution")
            result_content = (
                f"[tool result blocked — {reason}. "
                f"Try requesting a text file or a different resource.]"
            )
        self._history.append({
            "role": "tool_result",
            "tool_call_id": tool_call_id,
            "content": result_content,
        })
        log.info(f"LLM send_tool_result tool={tool_name} result_len={len(result_content)}")

        try:
            response = self._provider.complete(
                system=self._system_prompt,
                messages=list(self._history),
                model=config.LLM_MODEL,
                max_tokens=self._max_tokens,
                tools=tools,
            )
        except ContextOverflowError:
            log.warning(f"LLM context length exceeded in tool result — clearing history")
            self._history = []
            return {"type": "context_overflow"}
        except Exception as e:
            log.error(f"LLM tool result call failed: {e}", exc_info=True)
            raise

        # Check for follow-up tool call
        if tools and response.tool_calls:
            tc = response.tool_calls[0]
            log.info(f"LLM follow-up tool_call name={tc.name}")
            self._history.append({
                "role": "assistant",
                "content": response.text,
                "tool_calls": response.tool_calls,
            })
            self._trim_history()
            return {
                "type": "tool_call",
                "id": tc.id,
                "name": tc.name,
                "arguments": tc.args,
            }

        reply = response.text
        log.info(f"LLM tool summary=\"{reply[:80]}\"")
        self._history.append({"role": "assistant", "content": reply})
        self._collapse_tool_exchange()
        self._trim_history()
        if tools:
            return {"type": "text", "content": reply}
        return reply

    def _collapse_tool_exchange(self):
        """Strip intermediate tool messages after a tool exchange completes.

        Collapses [user, asst[tool_calls], tool_result, ..., asst[summary]] down
        to [user, asst[summary]]. The summary is already visible in chat; the raw
        tool results and tool_call assistant messages are bulk that serves no
        purpose in future context. If the LLM needs the data again, it re-calls
        the tool.

        Only runs when the last history entry is a plain assistant text message
        (not a tool_call request), so chained tool calls are left intact until
        the final summary arrives.
        """
        if not self._history:
            return
        summary = self._history[-1]
        if summary.get("role") != "assistant" or summary.get("tool_calls"):
            return  # not a final text summary — nothing to collapse yet
        i = len(self._history) - 2
        removed = 0
        while i >= 0:
            msg = self._history[i]
            if msg["role"] == "tool_result" or (msg["role"] == "assistant" and msg.get("tool_calls")):
                self._history.pop(i)
                removed += 1
                i -= 1
            else:
                break
        if removed:
            log.debug(f"LLM collapsed tool exchange: removed {removed} intermediate messages")

    def _trim_history(self):
        """Keep only the most recent _max_pairs user/assistant pairs.

        Context-only messages (user messages without a following assistant
        reply) don't count toward the pair limit — but context before the
        oldest kept pair is dropped.
        """
        # Walk backwards, counting pairs. Once we've found _max_pairs pairs,
        # everything before that point gets dropped.
        pairs = 0
        keep_from = 0
        i = len(self._history) - 1
        while i >= 0:
            if (i >= 1
                    and self._history[i]["role"] == "assistant"
                    and self._history[i - 1]["role"] == "user"):
                pairs += 1
                if pairs == self._max_pairs:
                    keep_from = i - 1
                    break
                i -= 2
            else:
                i -= 1
        if keep_from > 0:
            self._history = self._history[keep_from:]
