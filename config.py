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
INTERACTION_MODE     = _config["agent"]["interaction_mode"]
CONVERSATION_TIMEOUT = _config["agent"]["conversation_timeout"]

# LLM
LLM_PROVIDER = _config["llm"]["provider"]
LLM_MODEL    = _config["llm"]["model"]

# TTS
TTS_PROVIDER = _config["tts"]["provider"]
TTS_VOICE_ID = _config["tts"]["voice_id"]
TTS_MODEL    = _config["tts"]["model"]

# STT
STT_MODEL        = _config["stt"]["model"]
STT_DEVICE       = _config["stt"]["device"]
STT_COMPUTE_TYPE = _config["stt"]["compute_type"]

# Connector
CONNECTOR_TYPE      = _config["connector"]["type"]
BROWSER_PROFILE_DIR = _config["connector"]["browser_profile_dir"]
AUTH_STATE_FILE     = _config["connector"]["auth_state_file"]

# CalDAV
CALDAV_BOT_GMAIL = _config["caldav"]["bot_gmail"]

# Secrets from .env
OPENAI_API_KEY     = os.environ["OPENAI_API_KEY"]
ELEVENLABS_API_KEY = os.environ["ELEVENLABS_API_KEY"]
