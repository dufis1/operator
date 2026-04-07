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

# LLM
LLM_PROVIDER         = _config["llm"]["provider"]
LLM_MODEL            = _config["llm"]["model"]
CHAT_HISTORY_TURNS   = _config["llm"].get("chat_history_turns", 20)

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

# Secrets from .env
OPENAI_API_KEY     = os.environ["OPENAI_API_KEY"]
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
