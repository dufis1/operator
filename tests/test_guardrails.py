"""
Unit tests for step 10.4 — BYOMCP guard rails.

Tests the text extension allowlist, binary content detection, pre-execution
blocking in MCPClient, and post-execution validation in LLMClient.

Run:
    source venv/bin/activate
    python tests/test_guardrails.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock
from pipeline.guardrails import is_text_file_path, validate_tool_result


# ---------------------------------------------------------------------------
# is_text_file_path — text extensions allowed
# ---------------------------------------------------------------------------

def test_text_extensions_allowed():
    for ext in [".py", ".md", ".json", ".yaml", ".js", ".ts", ".go", ".rs",
                ".java", ".html", ".css", ".sh", ".sql", ".toml", ".xml",
                ".csv", ".log", ".svg", ".diff", ".lock"]:
        path = f"src/file{ext}"
        assert is_text_file_path(path), f"Expected allowed: {path}"
    print("PASS  test_text_extensions_allowed")


# ---------------------------------------------------------------------------
# is_text_file_path — binary extensions blocked
# ---------------------------------------------------------------------------

def test_binary_extensions_blocked():
    for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp",
                ".tiff", ".mp4", ".mov", ".avi", ".mp3", ".wav", ".flac",
                ".zip", ".tar", ".gz", ".rar", ".7z",
                ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                ".bin", ".exe", ".dll", ".so", ".dylib", ".o", ".a",
                ".onnx", ".pyc", ".class", ".woff", ".woff2", ".ttf", ".eot"]:
        path = f"assets/file{ext}"
        assert not is_text_file_path(path), f"Expected blocked: {path}"
    print("PASS  test_binary_extensions_blocked")


# ---------------------------------------------------------------------------
# is_text_file_path — extensionless files allowed
# ---------------------------------------------------------------------------

def test_extensionless_allowed():
    for name in ["README", "LICENSE", "Makefile", "Dockerfile", "Procfile",
                 "Gemfile", "somefile", "CODEOWNERS", ".gitignore"]:
        # .gitignore has an extension in TEXT_EXTENSIONS, but the rest are extensionless
        assert is_text_file_path(name), f"Expected allowed: {name}"
    # Also with directory prefix
    assert is_text_file_path("src/README"), "Expected allowed: src/README"
    print("PASS  test_extensionless_allowed")


# ---------------------------------------------------------------------------
# is_text_file_path — case insensitive
# ---------------------------------------------------------------------------

def test_case_insensitive():
    for path in ["file.PY", "file.Md", "file.JSON", "file.YAML", "FILE.JS"]:
        assert is_text_file_path(path), f"Expected allowed (case insensitive): {path}"
    for path in ["file.PNG", "file.Jpg", "file.PDF"]:
        assert not is_text_file_path(path), f"Expected blocked (case insensitive): {path}"
    print("PASS  test_case_insensitive")


# ---------------------------------------------------------------------------
# is_text_file_path — paths with directories
# ---------------------------------------------------------------------------

def test_paths_with_directories():
    assert is_text_file_path("src/main.py")
    assert is_text_file_path("docs/guide.md")
    assert not is_text_file_path("images/logo.png")
    assert not is_text_file_path("build/output.bin")
    assert is_text_file_path("config/settings.yaml")
    print("PASS  test_paths_with_directories")


# ---------------------------------------------------------------------------
# validate_tool_result — clean text passes
# ---------------------------------------------------------------------------

def test_validate_clean_text():
    ok, reason = validate_tool_result("def hello():\n    print('hi')\n")
    assert ok, f"Clean text should pass, got reason: {reason}"
    ok, reason = validate_tool_result("")
    assert ok, "Empty string should pass"
    print("PASS  test_validate_clean_text")


# ---------------------------------------------------------------------------
# validate_tool_result — null bytes flagged
# ---------------------------------------------------------------------------

def test_validate_null_bytes():
    ok, reason = validate_tool_result("hello\x00world")
    assert not ok, "Null bytes should be flagged"
    assert "null bytes" in reason
    print("PASS  test_validate_null_bytes")


# ---------------------------------------------------------------------------
# validate_tool_result — base64 image prefixes flagged
# ---------------------------------------------------------------------------

def test_validate_base64_png():
    ok, reason = validate_tool_result("iVBORw0KGgoAAAANSUhEUgAAA...")
    assert not ok, "Base64 PNG should be flagged"
    assert "iVBOR" in reason
    print("PASS  test_validate_base64_png")


def test_validate_base64_jpeg():
    ok, reason = validate_tool_result("/9j/4AAQSkZJRgABAQ...")
    assert not ok, "Base64 JPEG should be flagged"
    assert "/9j/" in reason
    print("PASS  test_validate_base64_jpeg")


def test_validate_data_uri():
    ok, reason = validate_tool_result("data:image/png;base64,iVBORw0KGgo...")
    assert not ok, "data:image URI should be flagged"
    print("PASS  test_validate_data_uri")


# ---------------------------------------------------------------------------
# validate_tool_result — high non-printable ratio flagged
# ---------------------------------------------------------------------------

def test_validate_high_nonprintable():
    # 50% non-printable
    content = "a\x01" * 500
    ok, reason = validate_tool_result(content)
    assert not ok, "High non-printable ratio should be flagged"
    assert "non-printable" in reason
    print("PASS  test_validate_high_nonprintable")


# ---------------------------------------------------------------------------
# validate_tool_result — low non-printable passes
# ---------------------------------------------------------------------------

def test_validate_low_nonprintable():
    # Normal text with tabs and newlines — well under 10%
    content = "hello\tworld\n" * 100
    ok, reason = validate_tool_result(content)
    assert ok, f"Low non-printable should pass, got reason: {reason}"
    print("PASS  test_validate_low_nonprintable")


# ---------------------------------------------------------------------------
# Integration: MCPClient.execute_tool raises on binary path
# ---------------------------------------------------------------------------

def test_mcp_blocks_binary_path():
    from pipeline.mcp_client import MCPClient, MCPToolError

    client = MCPClient()
    # Register a fake server and tool
    client._servers["github"] = MagicMock()
    client._tools["github__get_file_contents"] = {
        "server_name": "github",
        "mcp_tool": MagicMock(),
    }

    try:
        client.execute_tool("github__get_file_contents", {"path": "images/logo.png"})
        assert False, "Should have raised MCPToolError"
    except MCPToolError as e:
        assert "non-text file" in str(e), f"Unexpected error: {e}"
    print("PASS  test_mcp_blocks_binary_path")


# ---------------------------------------------------------------------------
# Integration: MCPClient.execute_tool allows text path
# ---------------------------------------------------------------------------

def test_mcp_allows_text_path():
    from pipeline.mcp_client import MCPClient

    client = MCPClient()
    handle = MagicMock()
    handle.call_tool.return_value = "file contents here"
    client._servers["github"] = handle
    client._tools["github__get_file_contents"] = {
        "server_name": "github",
        "mcp_tool": MagicMock(),
    }

    result = client.execute_tool("github__get_file_contents", {"path": "src/main.py"})
    assert result == "file contents here"
    print("PASS  test_mcp_allows_text_path")


# ---------------------------------------------------------------------------
# Integration: MCPClient.execute_tool allows extensionless path
# ---------------------------------------------------------------------------

def test_mcp_allows_extensionless():
    from pipeline.mcp_client import MCPClient

    client = MCPClient()
    handle = MagicMock()
    handle.call_tool.return_value = "makefile contents"
    client._servers["github"] = handle
    client._tools["github__get_file_contents"] = {
        "server_name": "github",
        "mcp_tool": MagicMock(),
    }

    result = client.execute_tool("github__get_file_contents", {"path": "Makefile"})
    assert result == "makefile contents"
    print("PASS  test_mcp_allows_extensionless")


# ---------------------------------------------------------------------------
# Integration: LLM.send_tool_result replaces binary content in history
# ---------------------------------------------------------------------------

def test_llm_blocks_binary_result():
    from pipeline.llm import LLMClient

    provider = MagicMock()
    llm = LLMClient(provider)

    # Seed history with a tool_call assistant message
    from pipeline.providers import ProviderResponse, ToolCall
    llm._history = [
        {"role": "user", "content": "read that file"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [ToolCall(id="call_abc", name="some__tool", args={})],
        },
    ]

    # Mock the provider to return a text summary message
    provider.complete.return_value = ProviderResponse(
        text="I couldn't read that.", tool_calls=[], stop_reason="end",
    )

    # Send a result with null bytes (simulating binary content)
    llm.send_tool_result("call_abc", "some__tool", "binary\x00content\x00here")

    # Verify the blocked content was sent to the provider, not the binary.
    # (_collapse_tool_exchange removes the tool_result message from history
    # after the summary, so we check what was sent to the provider instead.)
    call_args = provider.complete.call_args
    messages = call_args.kwargs["messages"]
    tool_msg = next(m for m in messages if m.get("role") == "tool_result")
    assert "blocked" in tool_msg["content"], f"Expected block message, got: {tool_msg['content'][:100]}"
    assert "\x00" not in tool_msg["content"], "Binary content leaked into API call"
    print("PASS  test_llm_blocks_binary_result")


# ---------------------------------------------------------------------------
# Integration: LLM.send_tool_result passes clean content
# ---------------------------------------------------------------------------

def test_llm_passes_clean_result():
    from pipeline.llm import LLMClient

    provider = MagicMock()
    llm = LLMClient(provider)

    from pipeline.providers import ProviderResponse, ToolCall
    llm._history = [
        {"role": "user", "content": "read file"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [ToolCall(id="call_xyz", name="some__tool", args={})],
        },
    ]

    provider.complete.return_value = ProviderResponse(
        text="Here's the file.", tool_calls=[], stop_reason="end",
    )

    clean_content = "def hello():\n    return 'world'\n"
    llm.send_tool_result("call_xyz", "some__tool", clean_content)

    # Check what was sent to the provider (collapse removes tool_result from history)
    call_args = provider.complete.call_args
    messages = call_args.kwargs["messages"]
    tool_msg = next(m for m in messages if m.get("role") == "tool_result")
    assert tool_msg["content"] == clean_content, f"Clean content should pass through unchanged"
    print("PASS  test_llm_passes_clean_result")


# ---------------------------------------------------------------------------
# Run all
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_text_extensions_allowed,
        test_binary_extensions_blocked,
        test_extensionless_allowed,
        test_case_insensitive,
        test_paths_with_directories,
        test_validate_clean_text,
        test_validate_null_bytes,
        test_validate_base64_png,
        test_validate_base64_jpeg,
        test_validate_data_uri,
        test_validate_high_nonprintable,
        test_validate_low_nonprintable,
        test_mcp_blocks_binary_path,
        test_mcp_allows_text_path,
        test_mcp_allows_extensionless,
        test_llm_blocks_binary_result,
        test_llm_passes_clean_result,
    ]
    failures = []
    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"FAIL  {t.__name__}: {e}")
            failures.append(t.__name__)

    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
