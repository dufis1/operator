"""
Docker entry point for Operator.

Reads configuration from environment variables, instantiates DockerAdapter
and the agent pipeline, then runs the main transcription loop until the
container is stopped (SIGINT / SIGTERM).

Required env vars:
    OPENAI_API_KEY
    MEETING_URL          Google Meet link to join on startup

Optional env vars (in addition to below):
    ELEVENLABS_API_KEY   Required only when tts.provider = elevenlabs

Optional env vars:
    BROWSER_PROFILE_DIR  Path for persistent Chromium profile (default: /tmp/operator_browser_profile)
"""
import logging
import os
import random
import signal
import sys
import threading
import time

from dotenv import load_dotenv
from openai import OpenAI

# Entrypoint lives at /app/docker/entrypoint.py — one level up is the repo root.
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE)

from connectors.docker_adapter import DockerAdapter
from pipeline.audio import AudioProcessor, SAMPLE_RATE, WHISPER_HALLUCINATIONS
from pipeline.conversation import ConversationState, CONVERSATION_TIMEOUT
from pipeline.llm import LLMClient, MAX_TRANSCRIPT_LINES
from pipeline.tts import TTSClient
from pipeline.wake import detect_wake_phrase

# Load .env if present — keys may also come from the container environment directly.
load_dotenv(os.path.join(_BASE, ".env"), override=False)

logging.basicConfig(
    stream=sys.stdout,
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)
# Silence noisy HTTP debug logs from API clients
for noisy in ("httpcore", "httpx", "openai", "elevenlabs"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

PULSE_OUTPUT_DEVICE = "pulse/MeetingOutput"

ACK_CLIPS = [
    os.path.join(_BASE, "assets", "ack_yeah.mp3"),
    os.path.join(_BASE, "assets", "ack_yes.mp3"),
    os.path.join(_BASE, "assets", "ack_mmhm.mp3"),
]


class DockerOperator:
    """Headless Operator pipeline wired to DockerAdapter."""

    def __init__(self):
        self._stop_event = threading.Event()
        self._transcript_lines = []
        self._transcript_lock = threading.Lock()
        self._capture_proc = None

        # State machine — no UI callbacks in Docker, just log state changes.
        self.conv = ConversationState(on_state_change=self._on_state_change)
        self.audio = None
        self.connector = None
        self.tts = None
        self.llm = None

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self):
        """Check config, initialise all components, join meeting, start loop."""
        self._check_env_or_exit()

        meeting_url = os.environ["MEETING_URL"]
        browser_profile = os.environ.get(
            "BROWSER_PROFILE_DIR", "/tmp/operator_browser_profile"
        )

        log.info("DockerOperator: loading Whisper model...")
        self.audio = AudioProcessor()

        log.info("DockerOperator: connecting to APIs...")
        openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.llm = LLMClient(openai_client)
        self.tts = TTSClient(PULSE_OUTPUT_DEVICE)

        auth_state_file = os.environ.get("AUTH_STATE_FILE")
        self.connector = DockerAdapter(user_data_dir=browser_profile, auth_state_file=auth_state_file)

        log.info(f"DockerOperator: joining meeting {meeting_url}")
        self.connector.join(meeting_url)
        # Give the browser time to reach the pre-join screen before we start
        # capturing audio — the join() call is non-blocking.
        time.sleep(12)

        self._start_capture()
        if not self.audio.capturing:
            log.error("DockerOperator: audio capture failed to start — exiting")
            self.connector.leave()
            sys.exit(1)

        self.conv.set_idle()
        log.info("DockerOperator: idle — listening for wake phrase")

        try:
            self._transcription_loop()
        finally:
            self._shutdown()

    # ------------------------------------------------------------------
    # Audio capture
    # ------------------------------------------------------------------

    def _start_capture(self):
        """Start parec via DockerAdapter and spin up the read loop."""
        try:
            self._capture_proc = self.connector.get_audio_stream()
        except Exception as e:
            log.error(f"DockerOperator: get_audio_stream failed: {e}")
            return
        self.audio.capturing = True
        threading.Thread(target=self._read_capture_stderr, daemon=True).start()
        threading.Thread(target=self._audio_read_loop, daemon=True).start()
        log.info("DockerOperator: audio capture started")

    def _read_capture_stderr(self):
        for line in self._capture_proc.stderr:
            log.debug(f"[parec] {line.decode().rstrip()}")

    def _audio_read_loop(self):
        CHUNK_SIZE = 4096
        while self.audio.capturing:
            chunk = self._capture_proc.stdout.read(CHUNK_SIZE)
            if not chunk:
                log.warning("DockerOperator: parec stopped (stdout closed)")
                self.audio.capturing = False
                break
            self.audio.feed_audio(chunk)
        log.info("DockerOperator: audio read loop ended")

    # ------------------------------------------------------------------
    # Transcription + pipeline loop  (mirrors app.py logic exactly)
    # ------------------------------------------------------------------

    def _transcription_loop(self):
        log.info("DockerOperator: transcription loop started")

        while self.audio.capturing and not self._stop_event.is_set():
            text = self.audio.capture_next_utterance(is_prompt=False)
            if not text:
                continue

            if text.lower().strip().strip(".,!?") in WHISPER_HALLUCINATIONS:
                log.info(f"Ignoring hallucination: {text}")
                continue

            wake_type, trailing = detect_wake_phrase(text)

            if wake_type is not None:
                if wake_type == "inline":
                    log.info(f"TIMING wake_inline prompt=\"{trailing}\"")
                    self.conv.set_listening("Listening for prompt...")
                    self._finalize_prompt(trailing)
                else:  # wake-only
                    log.info("TIMING wake_only waiting_for_prompt")
                    self.conv.set_listening("Listening for prompt...")
                    self._play_acknowledgment()
                    prompt = self.audio.capture_next_utterance(is_prompt=True)
                    if prompt:
                        self._finalize_prompt(prompt)
                    else:
                        log.info("Prompt empty after wake phrase — returning to idle")
                        self.conv.set_idle()
                        continue

                # Conversation mode: accept follow-ups without re-triggering wake phrase.
                log.info("Entering conversation mode")
                while self.audio.capturing and not self._stop_event.is_set():
                    self.conv.set_listening("Listening...")
                    followup = self.audio.capture_next_utterance(
                        is_prompt=True, no_speech_timeout=CONVERSATION_TIMEOUT
                    )
                    if not followup:
                        log.info("Conversation mode: no follow-up — returning to idle")
                        break
                    if followup.lower().strip().strip(".,!?") in WHISPER_HALLUCINATIONS:
                        continue
                    self._finalize_prompt(followup)
                self.conv.set_idle()
            else:
                # Ambient speech (no wake phrase) — append to rolling transcript
                log.info(f"Utterance: {text}")
                with self._transcript_lock:
                    self._transcript_lines.append(text)
                    if len(self._transcript_lines) > MAX_TRANSCRIPT_LINES:
                        self._transcript_lines = self._transcript_lines[-MAX_TRANSCRIPT_LINES:]

        log.info("DockerOperator: transcription loop ended")

    def _finalize_prompt(self, prompt):
        """Send finalized prompt to LLM, speak the response."""
        if not prompt:
            self.conv.set_idle()
            return

        log.info(f"TIMING prompt_finalized \"{prompt}\"")
        self.conv.set_thinking()

        self.audio.is_speaking = True
        self.audio.drain_audio_buffer()
        log.info("Echo prevention: paused audio ingestion")

        with self._transcript_lock:
            context = "\n".join(self._transcript_lines[-20:])

        full_prompt = (
            f"[Meeting transcript so far]\n{context}\n\n"
            f"[Someone just said to you]\n{prompt}"
        )
        log.info(f"Sending to LLM: {full_prompt[:200]}...")

        try:
            log.info("TIMING llm_request_sent")
            t0 = time.time()
            reply = self.llm.ask(full_prompt)
            t_llm = time.time()
            log.info(f"TIMING llm_response_received ({t_llm - t0:.1f}s) \"{reply}\"")

            self.conv.set_speaking()
            log.info("TIMING tts_request_sent")
            t_tts = time.time()
            self.tts.speak(reply)
            t_done = time.time()
            log.info(
                f"Pipeline timing — llm: {t_llm - t0:.1f}s, "
                f"speak: {t_done - t_tts:.1f}s, "
                f"total: {t_done - t0:.1f}s"
            )
        except Exception as e:
            log.error(f"Pipeline error: {e}")
        finally:
            self.audio.drain_audio_buffer()
            self.audio.is_speaking = False
            log.info("Echo prevention: resumed audio ingestion")

        self.conv.set_idle()

    def _play_acknowledgment(self):
        """Play a random acknowledgment clip through PulseAudio, with echo prevention."""
        clip = random.choice(ACK_CLIPS)
        clip_name = os.path.basename(clip).replace("ack_", "").replace(".mp3", "")
        log.info(f"Operator says: \"{clip_name}\" (acknowledgment)")
        self.audio.is_speaking = True
        self.audio.drain_audio_buffer()
        self.tts.play_clip(clip)
        time.sleep(0.2)
        self.audio.drain_audio_buffer()
        self.audio.is_speaking = False
        log.info("TIMING ack_done")

    # ------------------------------------------------------------------
    # State + shutdown
    # ------------------------------------------------------------------

    def _on_state_change(self, state, label):
        log.info(f"State → {state} ({label})")

    def _shutdown(self):
        log.info("DockerOperator: shutting down...")
        if self.audio:
            self.audio.capturing = False
        if self._capture_proc:
            try:
                self._capture_proc.terminate()
            except Exception:
                pass
        if self.connector:
            self.connector.leave()
        log.info("DockerOperator: shutdown complete")

    # ------------------------------------------------------------------
    # Config check
    # ------------------------------------------------------------------

    @staticmethod
    def _check_env_or_exit():
        required = ["OPENAI_API_KEY", "MEETING_URL"]
        import config as _cfg
        if _cfg.TTS_PROVIDER == "elevenlabs":
            required.append("ELEVENLABS_API_KEY")
        missing = [k for k in required if not os.environ.get(k)]
        if missing:
            log.error(f"Missing required env vars: {', '.join(missing)}")
            sys.exit(1)


# ------------------------------------------------------------------
# Signal handling + main
# ------------------------------------------------------------------

def _make_signal_handler(operator: DockerOperator):
    def handler(signum, frame):
        log.info(f"Received signal {signum} — stopping")
        operator._stop_event.set()
        operator.audio.capturing = False
    return handler


if __name__ == "__main__":
    op = DockerOperator()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _make_signal_handler(op))
    op.run()
