"""
MCP server that delegates coding tasks to Claude Code via the `claude` CLI.

Single tool: delegate_to_claude_code(task). Runs `claude -p <task> --worktree`
as a subprocess, returns the result. Confirmation-gated as a write tool.

Prerequisite: `claude` CLI installed and authenticated.

Add to config.yaml under mcp_servers:

  delegate:
    command: "python"
    args: ["roster/engineer/delegate_to_claude_code.py"]
    hints: |
      Use delegate_to_claude_code when the user asks you to write, modify,
      or review code. The task string should be a clear, self-contained
      instruction — Claude Code will execute it in an isolated worktree.
"""

import asyncio
import json
import shutil

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

server = Server("delegate")


@server.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="delegate_to_claude_code",
            description=(
                "Delegate a coding task to Claude Code. It runs in an isolated "
                "git worktree — reads, writes, and modifies files, creates "
                "branches and PRs. Pass a clear, self-contained task description."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "The coding task to delegate. Be specific: include "
                            "file paths, function names, or repo context when "
                            "available. Example: 'Add input validation to the "
                            "signup form in src/components/SignupForm.tsx'"
                        ),
                    },
                },
                "required": ["task"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name != "delegate_to_claude_code":
        raise ValueError(f"Unknown tool: {name}")

    task = arguments.get("task", "").strip()
    if not task:
        return [types.TextContent(type="text", text="Error: task cannot be empty.")]

    claude_path = shutil.which("claude")
    if not claude_path:
        return [
            types.TextContent(
                type="text",
                text=(
                    "Error: `claude` CLI not found on PATH. "
                    "Install it: https://docs.anthropic.com/en/docs/claude-code"
                ),
            )
        ]

    cmd = [claude_path, "-p", task, "--worktree", "--output-format", "json"]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except OSError as exc:
        return [
            types.TextContent(type="text", text=f"Error launching claude CLI: {exc}")
        ]

    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        return [
            types.TextContent(
                type="text",
                text=f"Claude Code exited with code {proc.returncode}.\n{err}",
            )
        ]

    raw = stdout.decode(errors="replace").strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [
            types.TextContent(
                type="text", text=f"Claude Code returned non-JSON output:\n{raw[:2000]}"
            )
        ]

    if data.get("is_error"):
        return [
            types.TextContent(
                type="text",
                text=f"Claude Code reported an error:\n{data.get('result', raw[:2000])}",
            )
        ]

    result = data.get("result", "")
    duration_s = round(data.get("duration_ms", 0) / 1000, 1)
    cost = data.get("total_cost_usd", 0)

    summary = result
    if duration_s or cost:
        summary += f"\n\n[Completed in {duration_s}s, cost ${cost:.4f}]"

    return [types.TextContent(type="text", text=summary)]


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
