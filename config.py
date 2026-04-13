import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
_ROOT = Path(__file__).parent
_config = yaml.safe_load((_ROOT / "config.yaml").read_text())

# Agent
AGENT_NAME           = _config["agent"]["name"]
WAKE_PHRASE          = _config["agent"]["wake_phrase"]
SYSTEM_PROMPT        = _config["agent"]["system_prompt"]
CHAT_WAKE_PHRASE     = _config["agent"].get("chat_wake_phrase", "operator")
INTERACTION_MODE     = _config["agent"]["interaction_mode"]
CONVERSATION_TIMEOUT = _config["agent"]["conversation_timeout"]
ECHO_GUARD_SECONDS   = _config["agent"].get("echo_guard_seconds", 1.0)
USER_DISPLAY_NAME    = _config["agent"].get("user_display_name", "")

# LLM
LLM_PROVIDER         = _config["llm"]["provider"]
LLM_MODEL            = _config["llm"]["model"]
CHAT_HISTORY_TURNS      = _config["llm"].get("chat_history_turns", 20)
CHAT_MAX_TOKENS         = _config["llm"].get("chat_max_tokens", 150)
TOOL_RESULT_MAX_CHARS   = _config["llm"].get("tool_result_max_chars", 50000)
TOOL_TIMEOUT_SECONDS    = _config["llm"].get("tool_timeout_seconds", 60)
TOOL_HEARTBEAT_SECONDS  = _config["llm"].get("tool_heartbeat_seconds", 8)
CHAT_SYSTEM_PROMPT      = _config["llm"].get("chat_system_prompt", _config["agent"]["system_prompt"])

# TTS
TTS_PROVIDER     = _config["tts"]["provider"]
TTS_LOCAL_VOICE  = _config["tts"].get("local_voice", "kokoro_heart")
TTS_OPENAI_MODEL = _config["tts"].get("openai_model", "gpt-4o-mini-tts")
TTS_OPENAI_VOICE = _config["tts"].get("openai_voice", "nova")
TTS_VOICE_ID     = _config["tts"].get("voice_id", "")
TTS_MODEL        = _config["tts"].get("model", "")

# STT
STT_PROVIDER     = _config["stt"].get("provider", "faster-whisper")
STT_MODEL        = _config["stt"]["model"]
STT_DEVICE       = _config["stt"]["device"]
STT_COMPUTE_TYPE = _config["stt"]["compute_type"]

# Connector
CONNECTOR_TYPE            = _config["connector"]["type"]
BROWSER_PROFILE_DIR       = _config["connector"]["browser_profile_dir"]
AUTH_STATE_FILE           = _config["connector"]["auth_state_file"]
IDLE_TIMEOUT_SECONDS      = _config["connector"].get("idle_timeout_seconds", 600)

# Captions
_captions = _config.get("captions", {})
CAPTION_SILENCE_SECONDS = _captions.get("silence_seconds", 0.7)

# Diagnostics
_diagnostics = _config.get("diagnostics", {})
LATENCY_PROBE_ENABLED = _diagnostics.get("latency_probe", True)
DEBUG_AUDIO           = _diagnostics.get("debug_audio", False)

# MCP Servers
def _resolve_env_vars(env_dict):
    """Replace ${VAR} references with os.environ values."""
    resolved = {}
    for k, v in env_dict.items():
        if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
            resolved[k] = os.environ.get(v[2:-1], "")
        else:
            resolved[k] = v
    return resolved

MCP_SERVERS = {}
for _name, _srv in _config.get("mcp_servers", {}).items():
    MCP_SERVERS[_name] = {
        "command": _srv["command"],
        "args": _srv.get("args", []),
        "env": _resolve_env_vars(_srv.get("env", {})),
        "hints": _srv.get("hints", "").strip(),
        "confirm_tools": set(_srv.get("confirm_tools", [])),
    }

# Secrets from .env
OPENAI_API_KEY     = os.environ["OPENAI_API_KEY"]
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
