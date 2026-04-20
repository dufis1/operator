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

BOT_DIR = _ROOT / "agents" / BOT_NAME
_cfg_path = BOT_DIR / "config.yaml"
if not _cfg_path.exists():
    available = sorted(
        p.name for p in (_ROOT / "agents").iterdir()
        if p.is_dir() and (p / "config.yaml").exists()
    )
    sys.stderr.write(
        f"ERROR: no config found at {_cfg_path}.\n"
        f"Available bots: {', '.join(available) if available else '(none)'}\n"
    )
    raise SystemExit(2)

_config = yaml.safe_load(_cfg_path.read_text())

# ── User-facing config (read from agents/<name>/config.yaml) ──────────────

# Agent
AGENT_NAME           = _config["agent"]["name"]
TRIGGER_PHRASE       = _config["agent"].get("trigger_phrase", "@operator")
FIRST_CONTACT_HINT   = _config["agent"].get("first_contact_hint", "")
AGENT_TAGLINE        = _config["agent"].get("tagline", "")
INTRO_ON_JOIN        = _config["agent"].get("intro_on_join", True)

# LLM
LLM_PROVIDER           = _config["llm"]["provider"]
LLM_MODEL              = _config["llm"]["model"]
HISTORY_MESSAGES       = _config["llm"].get("history_messages", 40)

# System prompt is authored as two top-level blocks — `personality` (who the
# bot is, its voice) and `ground_rules` (always-true constraints). They're
# concatenated here with personality first and ground_rules last; rules-last
# gains adherence because LLMs weight end-of-prompt content more heavily.
# Either block may be absent/empty — omitted blocks just drop out.
PERSONALITY   = (_config.get("personality") or "").strip()
GROUND_RULES  = (_config.get("ground_rules") or "").strip()
SYSTEM_PROMPT = "\n\n".join(b for b in (PERSONALITY, GROUND_RULES) if b)

# Skills
_skills = _config.get("skills") or {}
SKILLS_PATHS                 = _skills.get("paths") or []
SKILLS_PROGRESSIVE_DISCLOSURE = _skills.get("progressive_disclosure", True)

# Transcript (captions)
_transcript = _config.get("transcript", {})
CAPTIONS_ENABLED        = _transcript.get("captions_enabled", False)

# ── INTERNAL TUNING ───────────────────────────────────────────────────────
# These used to live in each bot's config.yaml; they're tuned-once internals
# that shipped identical across bots. Edit here to change runtime behavior
# globally. A single per-MCP-server override exists: `tool_timeout_seconds`
# under an mcp_servers[<name>] block wins over TOOL_TIMEOUT_SECONDS below.
ALONE_EXIT_GRACE_SECONDS = 60      # once we've seen a peer and they leave, exit after this many seconds
LOBBY_WAIT_SECONDS       = 600     # max wait in Meet waiting room for host to admit us
CAPTION_SILENCE_SECONDS  = 0.7     # dead-air gap before a buffered caption chunk commits to history
MAX_TOKENS               = 1000    # runaway guard on LLM output; "be brief" system-prompt does the real shaping
TOOL_RESULT_MAX_CHARS    = 50000   # truncate a single tool result above this length before feeding to the LLM
TOOL_TIMEOUT_SECONDS     = 60      # per-tool-call hard timeout; overridable per-MCP in config.yaml
TOOL_HEARTBEAT_SECONDS   = 8       # how often to post "still working..." during a long tool call
BROWSER_PROFILE_DIR      = "./browser_profile"   # persistent Chrome profile (cookies, Google login)
AUTH_STATE_FILE          = "./auth_state.json"   # Playwright storageState JSON for quick re-auth

# ── MCP servers ───────────────────────────────────────────────────────────
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
    # Blocks with `enabled: false` are declared but dormant — kept in config so
    # the setup wizard can toggle them on without re-authoring env/hints/tools.
    # Default is enabled when the field is absent (backward-compat).
    if not _srv.get("enabled", True):
        continue
    _block = {
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
    # Optional per-server hard timeout override (e.g. delegate runs minutes).
    if "tool_timeout_seconds" in _srv:
        _block["tool_timeout_seconds"] = _srv["tool_timeout_seconds"]
    MCP_SERVERS[_name] = _block

# Secrets from .env
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
