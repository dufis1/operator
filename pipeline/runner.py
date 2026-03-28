"""
AgentRunner — shared transcription loop for all entry points.

Wraps the full pipeline: audio capture → wake detection → LLM → TTS.
Platform-agnostic: wire any MeetingConnector and provide a TTS output device.

Usage:
    runner = AgentRunner(
        connector=MacOSAdapter(),
        tts_output_device="coreaudio/BlackHole2ch_UID",
        on_state_change=my_callback,
    )
    runner.run()          # macOS: no URL, calendar poller calls connector.join() separately
    runner.run(url)       # Linux: join immediately, then loop
"""
import logging
import os
import random
import subprocess
import threading
import time

import config
from openai import OpenAI

from pipeline.audio import AudioProcessor, WHISPER_HALLUCINATIONS
from pipeline.conversation import ConversationState, CONVERSATION_TIMEOUT
from pipeline.llm import LLMClient, MAX_TRANSCRIPT_LINES
from pipeline.tts import TTSClient
from pipeline.wake import detect_wake_phrase

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACK_CLIPS = [
    os.path.join(_BASE, "assets", "ack_yeah.mp3"),
    os.path.join(_BASE, "assets", "ack_yes.mp3"),
    os.path.join(_BASE, "assets", "ack_mmhm.mp3"),
]

log = logging.getLogger(__name__)


class AgentRunner:
    """
    Platform-agnostic agent pipeline.

    Args:
        connector:          A MeetingConnector instance (MacOSAdapter, LinuxAdapter, etc.)
        tts_output_device:  mpv audio device string (e.g. "coreaudio/BlackHole2ch_UID")
        on_state_change:    Optional callback(state: str, label: str) for UI updates.
                            Defaults to logging the state transition.
        stop_event:         Optional threading.Event to signal a clean shutdown.
                            If omitted, one is created internally; call runner.stop() to set it.
    """

    def __init__(self, connector, tts_output_device, on_state_change=None, stop_event=None):
        self.connector = connector
        self._tts_output_device = tts_output_device
        self._on_state_change = on_state_change or self._log_state_change
        self._stop_event = stop_event or threading.Event()

        self._transcript_lines = []
        self._transcript_lock = threading.Lock()
        self._capture_proc = None

        self.audio = None
        self.conv = None
        self.llm = None
        self.tts = None

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self, meeting_url=None):
        """Initialise pipeline, optionally join a meeting, then run the transcription loop.

        Blocks until audio capture stops or stop() is called.
        """
        log.info("AgentRunner: loading Whisper model...")
        self.audio = AudioProcessor()

        log.info("AgentRunner: connecting to APIs...")
        openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
        self.llm = LLMClient(openai_client)
        self.tts = TTSClient(self._tts_output_device)

        self.conv = ConversationState(on_state_change=self._on_state_change)

        if meeting_url:
            log.info(f"AgentRunner: joining {meeting_url}")
            self.connector.join(meeting_url)
            # Browser is non-blocking — give it time to reach the pre-join screen.
            time.sleep(12)

        self._start_capture()
        if not self.audio.capturing:
            log.error("AgentRunner: audio capture failed to start — aborting")
            self.connector.leave()
            return

        self.conv.set_idle()
        log.info("AgentRunner: idle — listening for wake phrase")

        try:
            self._transcription_loop()
        finally:
            self._stop_capture()

    def stop(self):
        """Signal the transcription loop to exit cleanly."""
        self._stop_event.set()
        if self.audio:
            self.audio.capturing = False

    # ------------------------------------------------------------------
    # Audio capture
    # ------------------------------------------------------------------

    def _start_capture(self, stderr_tag="capture"):
        """Launch the audio stream via the connector and spin up read loops."""
        try:
            self._capture_proc = self.connector.get_audio_stream()
        except Exception as e:
            log.error(f"AgentRunner: get_audio_stream failed: {e}")
            return
        self.audio.capturing = True
        threading.Thread(
            target=self._read_capture_stderr, args=(stderr_tag,), daemon=True
        ).start()
        threading.Thread(target=self._audio_read_loop, daemon=True).start()
        log.info("AgentRunner: audio capture started")

    def _read_capture_stderr(self, tag):
        for line in self._capture_proc.stderr:
            log.debug(f"[{tag}] {line.decode().rstrip()}")

    def _audio_read_loop(self):
        CHUNK_SIZE = 4096
        while self.audio.capturing:
            chunk = self._capture_proc.stdout.read(CHUNK_SIZE)
            if not chunk:
                log.warning("AgentRunner: capture process stopped (stdout closed)")
                self.audio.capturing = False
                break
            self.audio.feed_audio(chunk)
        log.info("AgentRunner: audio read loop ended")

    def _stop_capture(self):
        if self.audio:
            self.audio.capturing = False
        if self._capture_proc:
            try:
                self._capture_proc.stdin.close()
            except Exception:
                pass
            try:
                self._capture_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._capture_proc.terminate()
            self._capture_proc = None

    # ------------------------------------------------------------------
    # Transcription loop
    # ------------------------------------------------------------------

    def _transcription_loop(self):
        log.info("AgentRunner: transcription loop started")

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

                # Conversation mode: accept follow-ups without re-triggering wake phrase
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
                # Ambient speech — append to rolling transcript
                log.info(f"Utterance: {text}")
                with self._transcript_lock:
                    self._transcript_lines.append(text)
                    if len(self._transcript_lines) > MAX_TRANSCRIPT_LINES:
                        self._transcript_lines = self._transcript_lines[-MAX_TRANSCRIPT_LINES:]

        log.info("AgentRunner: transcription loop ended")

    # ------------------------------------------------------------------
    # Prompt handling
    # ------------------------------------------------------------------

    def _finalize_prompt(self, prompt):
        """Send a finalized prompt to the LLM and speak the response."""
        if not prompt:
            self.conv.set_idle()
            return

        log.info(f"TIMING prompt_finalized \"{prompt}\"")
        self.conv.set_thinking()

        # Echo prevention: pause audio ingestion for the entire think+speak cycle
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
        """Play a random acknowledgment clip through the TTS output device."""
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
    # Default state change handler
    # ------------------------------------------------------------------

    @staticmethod
    def _log_state_change(state, label):
        log.info(f"State → {state} ({label})")
