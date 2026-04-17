import os
import sys
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
_ROOT = Path(__file__).parent

BOT_NAME = os.environ.get("OPERATOR_BOT", "").strip()
if not BOT_NAME:
    sys.stderr.write(
        "ERROR: OPERATOR_BOT env var is not set.\n"
        "Run via the CLI: `operator <name> [url]`.\n"
    )
    raise SystemExit(2)

BOT_DIR = _ROOT / "roster" / BOT_NAME
_cfg_path = BOT_DIR / "config.yaml"
if not _cfg_path.exists():
    available = sorted(
        p.name for p in (_ROOT / "roster").iterdir()
        if p.is_dir() and (p / "config.yaml").exists()
    )
    sys.stderr.write(
        f"ERROR: no config found at {_cfg_path}.\n"
        f"Available bots: {', '.join(available) if available else '(none)'}\n"
    )
    raise SystemExit(2)

_config = yaml.safe_load(_cfg_path.read_text())

# Agent
AGENT_NAME           = _config["agent"]["name"]
TRIGGER_PHRASE       = _config["agent"].get("trigger_phrase", "@operator")
USER_DISPLAY_NAME    = _config["agent"].get("user_display_name", "")
CONVERSATION_TIMEOUT = _config["agent"]["conversation_timeout"]
ALONE_EXIT_GRACE_SECONDS = _config["agent"].get("alone_exit_grace_seconds", 60)
FIRST_CONTACT_HINT   = _config["agent"].get("first_contact_hint", "")

# LLM
LLM_PROVIDER           = _config["llm"]["provider"]
LLM_MODEL              = _config["llm"]["model"]
HISTORY_MESSAGES       = _config["llm"].get("history_messages", 40)
MAX_TOKENS             = _config["llm"].get("max_tokens", 150)
TOOL_RESULT_MAX_CHARS  = _config["llm"].get("tool_result_max_chars", 50000)
TOOL_TIMEOUT_SECONDS   = _config["llm"].get("tool_timeout_seconds", 60)
TOOL_HEARTBEAT_SECONDS = _config["llm"].get("tool_heartbeat_seconds", 8)
SYSTEM_PROMPT          = _config["llm"]["system_prompt"]

# Connector
BROWSER_PROFILE_DIR  = _config["connector"]["browser_profile_dir"]
AUTH_STATE_FILE      = _config["connector"]["auth_state_file"]
IDLE_TIMEOUT_SECONDS = _config["connector"].get("idle_timeout_seconds", 600)

# Skills
_skills = _config.get("skills") or {}
SKILLS_PATHS                 = _skills.get("paths") or []
SKILLS_PROGRESSIVE_DISCLOSURE = _skills.get("progressive_disclosure", True)

# Transcript (captions)
_transcript = _config.get("transcript", {})
CAPTIONS_ENABLED        = _transcript.get("captions_enabled", False)
CAPTION_SILENCE_SECONDS = _transcript.get("silence_seconds", 0.7)

# MCP Servers
import logging as _logging
_mcp_log = _logging.getLogger("config.mcp")

def _resolve_env_vars(env_dict, server_name):
    """Replace ${VAR} references with os.environ values.

    Logs a warning for any ${VAR} that resolves to an empty or missing value,
    tagged with the server name so user-configured MCP issues are easy to spot.
    """
    resolved = {}
    for k, v in env_dict.items():
        if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
            var_name = v[2:-1]
            value = os.environ.get(var_name, "")
            if not value:
                _mcp_log.warning(
                    f"MCP USER CONFIG: server '{server_name}' env var {var_name} "
                    f"is empty or missing from .env — tool calls may fail at auth time"
                )
            resolved[k] = value
        else:
            resolved[k] = v
    return resolved

MCP_SERVERS = {}
for _name, _srv in _config.get("mcp_servers", {}).items():
    MCP_SERVERS[_name] = {
        "command": _srv["command"],
        "args": _srv.get("args", []),
        "env": _resolve_env_vars(_srv.get("env", {}), _name),
        "hints": _srv.get("hints", "").strip(),
        "confirm_tools": set(_srv.get("confirm_tools", [])),
        # Tools that auto-execute without user confirmation. Empty set = every
        # tool from this server requires confirmation. Bundle authors declare
        # the read tool names here; the pipeline carries no per-MCP defaults.
        "read_tools": set(_srv.get("read_tools", [])),
    }

# Secrets from .env
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
