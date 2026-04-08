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

log = logging.getLogger(__name__)


class MCPToolError(Exception):
    """Raised when an MCP tool call fails."""
    pass


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
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None

    def connect_all(self) -> list[str]:
        """Start all configured MCP servers and discover their tools.

        Returns list of discovered tool names. Logs and skips servers
        that fail to start.
        """
        self._start_loop()
        tool_names = []
        for name, srv_config in config.MCP_SERVERS.items():
            try:
                tools = self._connect_server(name, srv_config)
                tool_names.extend(tools)
                log.info(f"MCP server '{name}' connected — {len(tools)} tools")
            except Exception as e:
                log.error(f"MCP server '{name}' failed to start: {e}")
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

    def stop(self, timeout=5):
        """Signal the server task to shut down."""
        if self._shutdown_event:
            self._loop.call_soon_threadsafe(self._shutdown_event.set)
        if self._task:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._cancel_task(), self._loop
                ).result(timeout=timeout)
            except Exception:
                pass

    async def _cancel_task(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _run(self):
        """Long-lived task: connect, discover tools, serve requests, shutdown."""
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
