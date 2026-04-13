"""
MCP client manager — connects to configured MCP servers, discovers tools,
and executes tool calls.

Bridges the async MCP SDK into synchronous code via a dedicated event loop
thread. Each MCP server runs as a long-lived async task (required because
the MCP SDK's stdio_client uses anyio task groups that must stay in one task).
All public methods are synchronous and safe to call from any thread.
"""
import asyncio
import json
import logging
import os
import threading

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import config
from pipeline.guardrails import is_text_file_path, log_rejection

log = logging.getLogger(__name__)


class MCPToolError(Exception):
    """Raised when an MCP tool call fails."""
    pass


def _classify_startup_failure(exc: Exception, srv_config: dict) -> str:
    """Turn a startup exception into a plain-English user-facing reason.

    Tailored for the common DIY MCP config mistakes: missing binary,
    unresponsive server, silent crash. Unwraps anyio ExceptionGroups to
    reach the real cause. Falls back to the raw error text.
    """
    cmd = srv_config.get("command", "?")
    # Unwrap anyio/asyncio ExceptionGroups — stdio_client wraps subprocess
    # failures in a TaskGroup, so the first-layer exception is useless noise.
    inner = exc
    while isinstance(inner, BaseExceptionGroup) and inner.exceptions:
        inner = inner.exceptions[0]

    if isinstance(inner, FileNotFoundError):
        return (
            f"the command '{cmd}' was not found — "
            f"check the 'command' field in config.yaml, or ensure the binary is on PATH"
        )
    if isinstance(inner, TimeoutError):
        return (
            f"'{cmd}' did not respond within the startup timeout — "
            f"the binary may have crashed or is waiting for input; try running it manually"
        )
    # Subprocess exited before MCP handshake completed (e.g. `echo` or a server
    # that crashes on startup). anyio raises this from inside stdio_client.
    msg = str(inner).lower()
    if "process exited" in msg or "broken pipe" in msg or "eof" in msg or "connection closed" in msg:
        return (
            f"'{cmd}' exited before the MCP handshake completed — "
            f"the binary likely crashed or is not an MCP server; "
            f"try running the command manually to see its output"
        )
    return f"{type(inner).__name__}: {inner}"


class MCPClient:
    """Manages connections to one or more MCP servers (stdio transport).

    Usage:
        client = MCPClient()
        client.connect_all()          # starts servers, discovers tools
        tools = client.get_openai_tools()  # OpenAI function-calling format
        result = client.execute_tool("linear__create_issue", {...})
        client.shutdown()
    """

    def __init__(self):
        # server_name -> _ServerHandle
        self._servers: dict[str, _ServerHandle] = {}
        # namespaced_tool_name -> { server_name, mcp_tool }
        self._tools: dict[str, dict] = {}
        # server_name -> human-readable failure reason (populated by connect_all)
        self.failed_servers: dict[str, str] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None

    def connect_all(self) -> list[str]:
        """Start all configured MCP servers and discover their tools.

        Returns list of discovered tool names. Logs and skips servers
        that fail to start; failure reasons are stored in self.failed_servers.
        """
        self._start_loop()
        tool_names = []
        for name, srv_config in config.MCP_SERVERS.items():
            try:
                tools = self._connect_server(name, srv_config)
                tool_names.extend(tools)
                log.info(f"MCP server '{name}' connected — {len(tools)} tools")
            except Exception as e:
                reason = _classify_startup_failure(e, srv_config)
                self.failed_servers[name] = reason
                log.error(f"MCP USER CONFIG: server '{name}' failed to start — {reason}")
        return tool_names

    def get_openai_tools(self) -> list[dict]:
        """Return all discovered tools in OpenAI function-calling format."""
        result = []
        for tool_name, info in self._tools.items():
            mcp_tool = info["mcp_tool"]
            result.append({
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": mcp_tool.description or "",
                    "parameters": mcp_tool.inputSchema,
                },
            })
        return result

    def execute_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool call and return the result as a string.

        tool_name is the namespaced name like "linear__create_issue".
        Raises MCPToolError on failure.
        """
        if tool_name not in self._tools:
            raise MCPToolError(f"Unknown tool: {tool_name}")

        info = self._tools[tool_name]
        server_name = info["server_name"]
        original_name = tool_name.split("__", 1)[1] if "__" in tool_name else tool_name
        handle = self._servers[server_name]

        # Strip 'limit' the LLM injects unprompted on Linear list calls —
        # the model ignores prompt hints, so enforce it here.
        if server_name == "linear" and "limit" in arguments:
            log.info(f"MCP stripping unprompted limit={arguments['limit']} from {tool_name}")
            arguments = {k: v for k, v in arguments.items() if k != "limit"}

        # Block binary file reads before the MCP call fires.
        # Works for any server exposing get_file_contents, not just GitHub.
        if original_name == "get_file_contents" and "path" in arguments:
            if not is_text_file_path(arguments["path"]):
                reason = f"Blocked: '{arguments['path']}' has a non-text file extension — only text files are allowed"
                log_rejection(tool_name, arguments, reason, "pre-execution")
                raise MCPToolError(reason)

        log.info(f"MCP executing tool={tool_name} server={server_name}")
        log.debug(f"MCP tool arguments: {json.dumps(arguments)}")

        try:
            result = handle.call_tool(original_name, arguments)
        except MCPToolError:
            raise
        except Exception as e:
            log.error(f"MCP tool execution failed: {e}")
            raise MCPToolError(f"Tool '{tool_name}' failed: {e}") from e

        return result

    def resolve_github_user(self) -> str | None:
        """Call github__get_me to resolve the authenticated GitHub login.

        Returns the login string (e.g. 'dufis1') or None if unavailable.
        """
        tool_name = "github__get_me"
        if tool_name not in self._tools:
            return None
        try:
            result = self.execute_tool(tool_name, {})
            # Result is JSON text with a "login" field
            import json as _json
            data = _json.loads(result)
            login = data.get("login")
            if login:
                log.info(f"MCP resolved GitHub user: {login}")
            return login
        except Exception as e:
            log.warning(f"MCP resolve_github_user failed: {e}")
            return None

    def shutdown(self):
        """Disconnect all servers and stop the event loop thread."""
        if not self._loop:
            return
        for name, handle in list(self._servers.items()):
            try:
                handle.stop()
            except Exception as e:
                log.warning(f"MCP server '{name}' cleanup error: {e}")
        self._servers.clear()
        self._tools.clear()
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread:
            self._loop_thread.join(timeout=5)
        self._loop = None
        self._loop_thread = None
        log.info("MCP client shutdown complete")

    # ── Internal ──────────────────────────────────────────────────────

    def _start_loop(self):
        """Start a dedicated asyncio event loop in a daemon thread."""
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever,
            daemon=True,
            name="mcp-event-loop",
        )
        self._loop_thread.start()

    def _connect_server(self, name, srv_config):
        """Start a server task and wait for tool discovery to complete."""
        handle = _ServerHandle(name, srv_config, self._loop)
        handle.start()
        self._servers[name] = handle

        discovered = []
        for tool in handle.tools:
            namespaced = f"{name}__{tool.name}"
            self._tools[namespaced] = {
                "server_name": name,
                "mcp_tool": tool,
            }
            discovered.append(namespaced)
        return discovered


class _ServerHandle:
    """Manages a single MCP server as a long-lived async task.

    The stdio_client context manager and ClientSession live inside one
    async task for the server's entire lifetime. External callers
    communicate via thread-safe request/response futures.
    """

    def __init__(self, name, srv_config, loop):
        self.name = name
        self._srv_config = srv_config
        self._loop = loop
        self.tools = []  # populated after start()
        self._ready = threading.Event()
        self._error: Exception | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._task: asyncio.Task | None = None

    def start(self, timeout=30):
        """Start the server task and block until tools are discovered."""
        future = asyncio.run_coroutine_threadsafe(self._run(), self._loop)
        # Wait for the server to be ready (or fail)
        if not self._ready.wait(timeout=timeout):
            raise TimeoutError(f"MCP server '{self.name}' did not start within {timeout}s")
        if self._error:
            raise self._error

    def call_tool(self, tool_name, arguments, timeout=30):
        """Execute a tool call (thread-safe, blocks until result)."""
        future = asyncio.run_coroutine_threadsafe(
            self._execute_tool(tool_name, arguments), self._loop
        )
        try:
            return future.result(timeout=timeout)
        except MCPToolError:
            raise
        except Exception as e:
            raise MCPToolError(f"Tool '{tool_name}' failed: {e}") from e

    def stop(self, timeout=6):
        """Signal the server task to shut down and wait for graceful cleanup."""
        if self._shutdown_event:
            self._loop.call_soon_threadsafe(self._shutdown_event.set)
        if self._task:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._wait_then_cancel(), self._loop
                ).result(timeout=timeout)
            except Exception:
                pass

    async def _wait_then_cancel(self):
        """Wait for the task to finish; cancel only as a last resort.

        stdio_client's cleanup sequence (close stdin, wait 2s, SIGTERM,
        wait 2s, SIGKILL) is bounded to ~4s. We wait 5s for it to
        complete naturally. Cancelling immediately would inject
        CancelledError into that cleanup, leaving the subprocess alive.
        """
        if not self._task or self._task.done():
            return
        try:
            async with asyncio.timeout(5):
                await self._task
        except asyncio.TimeoutError:
            log.warning(f"MCP server '{self.name}': graceful shutdown timed out, force-cancelling")
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        except (asyncio.CancelledError, Exception):
            pass

    async def _run(self):
        """Long-lived task: connect, discover tools, serve requests, shutdown."""
        self._task = asyncio.current_task()
        self._shutdown_event = asyncio.Event()
        self._session = None

        try:
            params = StdioServerParameters(
                command=self._srv_config["command"],
                args=self._srv_config["args"],
                env={**os.environ, **self._srv_config["env"]},
            )
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    self._session = session
                    tools_result = await session.list_tools()
                    self.tools = tools_result.tools
                    log.info(f"MCP server '{self.name}': {len(self.tools)} tools discovered")
                    self._ready.set()

                    # Serve until shutdown
                    await self._shutdown_event.wait()

        except Exception as e:
            self._error = e
            self._ready.set()  # unblock start()
            log.error(f"MCP server '{self.name}' task error: {e}")

    async def _execute_tool(self, tool_name, arguments):
        """Execute a tool call on the session (must run on the event loop)."""
        if not self._session:
            raise MCPToolError(f"Server '{self.name}' not connected")
        result = await self._session.call_tool(tool_name, arguments)
        if result.isError:
            error_text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
            log.error(f"MCP tool returned error: {error_text}")
            raise MCPToolError(f"Tool error: {error_text}")
        parts = []
        for c in result.content:
            if hasattr(c, "text"):
                parts.append(c.text)
            elif hasattr(c, "resource") and hasattr(c.resource, "text"):
                parts.append(c.resource.text)
            else:
                log.warning(f"MCP tool result has unhandled content: type={type(c).__name__}")
        result_text = "\n".join(parts)
        log.info(f"MCP tool result length={len(result_text)}")
        log.debug(f"MCP tool result: {result_text[:500]}")
        return result_text
