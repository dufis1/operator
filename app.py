"""
Operator — AI Meeting Participant
Runs in the macOS menu bar. Listens to meeting audio for "operator",
then responds via text-to-speech through a virtual audio device.
"""
import os
import random
import subprocess
import threading
import time
import logging
import soundfile as sf
import numpy as np
from dotenv import load_dotenv
import rumps
from elevenlabs.client import ElevenLabs
from openai import OpenAI
from PyObjCTools.AppHelper import callAfter
from calendar_join import CalendarPoller
from pipeline.audio import AudioProcessor, SAMPLE_RATE, WHISPER_HALLUCINATIONS
from pipeline.wake import detect_wake_phrase
from pipeline.conversation import ConversationState, CONVERSATION_TIMEOUT

load_dotenv()

logging.basicConfig(
    filename="/tmp/operator.log",
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)
# Silence noisy HTTP debug logs from API clients
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("elevenlabs").setLevel(logging.WARNING)

VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # George
MAX_TRANSCRIPT_LINES = 100  # rolling transcript history limit
SYSTEM_PROMPT = (
    "You are Operator, an AI thought partner participating in a meeting. "
    "Your responses will be spoken aloud via text-to-speech, so:\n"
    "- Keep responses to 1-2 SHORT sentences, under 30 words total\n"
    "- Never use markdown, bullet points, or formatting\n"
    "- Speak in plain, natural sentences only\n"
    "- Be direct — no preamble, no filler, no caveats\n"
    "- User input comes from speech-to-text and may contain transcription "
    "errors (e.g. \"shop advice\" instead of \"Shopify's\"). Use surrounding "
    "context to infer the intended words."
)
BLACKHOLE_DEVICE = "coreaudio/BlackHole2ch_UID"

# Maps conversation state names → menu bar icons
STATE_ICONS = {
    "idle":      "⚪",
    "listening": "🔴",
    "thinking":  "🟡",
    "speaking":  "🟢",
}


AUDIO_CAPTURE_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio_capture")
ACK_CLIPS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "ack_yeah.mp3"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "ack_yes.mp3"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "ack_mmhm.mp3"),
]


class OperatorApp(rumps.App):
    def __init__(self):
        super().__init__("⚪", quit_button=None)

        self.status_item = rumps.MenuItem("Loading model...")
        self.menu = [
            self.status_item,
            None,
            rumps.MenuItem("Request Audio Permission", callback=self.request_audio_permission),
            rumps.MenuItem("Test Capture (10s)", callback=self.test_capture),
            None,
            rumps.MenuItem("Quit", callback=self.quit_app),
        ]

        self.conversation_history = []

        # Conversation state machine
        self.conv = ConversationState(on_state_change=self._on_conv_state_change)

        # Audio processor (initialised in _load_and_start after API key check)
        self.audio = None

        # Continuous audio capture state
        self._capture_proc = None

        # Rolling transcript
        self._transcript_lines = []
        self._transcript_lock = threading.Lock()

        # Calendar auto-join
        self._calendar_poller = None

        threading.Thread(target=self._load_and_start, daemon=True).start()

    # ------------------------------------------------------------------
    # Thread-safe UI updates
    # ------------------------------------------------------------------

    def _set_state(self, icon, status_text=None):
        """Update menu bar icon and optional status text from any thread."""
        def update():
            self.title = icon
            if status_text is not None:
                self.status_item.title = status_text
        callAfter(update)
        log.debug(f"State → {icon} {status_text or ''}")

    def _on_conv_state_change(self, state, label):
        """Translate a pipeline conversation state into a menu bar icon update."""
        self._set_state(STATE_ICONS[state], label)

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _check_api_keys(self):
        missing = []
        if not os.environ.get("OPENAI_API_KEY"):
            missing.append("OPENAI_API_KEY")
        if not os.environ.get("ELEVENLABS_API_KEY"):
            missing.append("ELEVENLABS_API_KEY")
        if missing:
            return f"Missing API keys: {', '.join(missing)}. Add them to your .env file."
        return None

    def _load_and_start(self):
        key_error = self._check_api_keys()
        if key_error:
            self._set_state("⚠️", key_error)
            return

        self._set_state("⚪", "Loading Whisper model...")
        self.audio = AudioProcessor()

        self._set_state("⚪", "Connecting to APIs...")
        self.openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.eleven = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])

        self._start_continuous_capture()
        threading.Thread(target=self._transcription_loop, daemon=True).start()

        self._calendar_poller = CalendarPoller()
        self._calendar_poller.start()

        self.conv.set_idle()

    def _play_acknowledgment(self):
        """Play a random acknowledgment clip through BlackHole, with echo prevention."""
        clip = random.choice(ACK_CLIPS)
        clip_name = os.path.basename(clip).replace("ack_", "").replace(".mp3", "")
        log.info(f"Operator says: \"{clip_name}\" (acknowledgment)")
        self.audio.is_speaking = True
        self.audio.drain_audio_buffer()
        subprocess.run(
            ["mpv", "--no-terminal", f"--audio-device={BLACKHOLE_DEVICE}", "--", clip],
            check=False,
        )
        time.sleep(0.2)
        self.audio.drain_audio_buffer()
        self.audio.is_speaking = False
        log.info("TIMING ack_done")

    # ------------------------------------------------------------------
    # Continuous audio capture
    # ------------------------------------------------------------------

    def _start_continuous_capture(self):
        """Launch the Swift helper and read audio continuously."""
        if not os.path.exists(AUDIO_CAPTURE_HELPER):
            log.error(f"Audio capture helper not found: {AUDIO_CAPTURE_HELPER}")
            self._set_state("❌", "Helper not found")
            return

        try:
            self._capture_proc = subprocess.Popen(
                [AUDIO_CAPTURE_HELPER],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
            )
            self.audio.capturing = True
            log.info("Continuous capture: helper launched")
        except OSError as e:
            log.error(f"Continuous capture: failed to launch helper: {e}")
            self._set_state("❌", f"Helper launch failed: {e}")
            return

        # Read stderr logs from Swift helper
        threading.Thread(target=self._read_capture_stderr, daemon=True).start()
        # Read audio data continuously
        threading.Thread(target=self._audio_read_loop, daemon=True).start()

    def _read_capture_stderr(self):
        """Log stderr output from the Swift helper."""
        for line in self._capture_proc.stderr:
            log.debug(f"[swift] {line.decode().rstrip()}")

    def _audio_read_loop(self):
        """Continuously read PCM data from the Swift helper into the audio buffer."""
        CHUNK_SIZE = 4096
        while self.audio.capturing:
            chunk = self._capture_proc.stdout.read(CHUNK_SIZE)
            if not chunk:
                log.warning("Continuous capture: helper stopped (stdout closed)")
                self.audio.capturing = False
                break
            self.audio.feed_audio(chunk)

        log.info("Continuous capture: read loop ended")

    def _stop_continuous_capture(self):
        """Stop the Swift helper."""
        if self.audio:
            self.audio.capturing = False
        if self._capture_proc:
            log.info("Continuous capture: stopping helper")
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
        """Utterance-based loop: detects 'operator' wake phrase via Whisper,
        routes to LLM for prompt utterances, accumulates ambient speech into
        the rolling transcript."""
        log.info("Transcription loop: started")

        while self.audio.capturing:
            text = self.audio.capture_next_utterance(is_prompt=False)
            if not text:
                continue

            text_lower = text.lower()

            if text_lower.strip().strip(".,!?") in WHISPER_HALLUCINATIONS:
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
                        log.info("Prompt was empty after wake phrase, returning to idle")
                        self.conv.set_idle()
                        continue

                # After Operator responds, stay in conversation mode: keep accepting
                # follow-up replies without requiring the wake phrase again.
                # Exit when CONVERSATION_TIMEOUT passes with no speech.
                log.info("Entering conversation mode")
                while self.audio.capturing:
                    self.conv.set_listening("Listening...")
                    followup = self.audio.capture_next_utterance(is_prompt=True, no_speech_timeout=CONVERSATION_TIMEOUT)
                    if not followup:
                        log.info("Conversation mode: no follow-up, returning to idle")
                        break
                    if followup.lower().strip().strip(".,!?") in WHISPER_HALLUCINATIONS:
                        continue
                    self._finalize_prompt(followup)
                self.conv.set_idle()
            else:
                # Ambient (no wake phrase) — add to rolling transcript
                log.info(f"Utterance: {text}")
                with self._transcript_lock:
                    self._transcript_lines.append(text)
                    if len(self._transcript_lines) > MAX_TRANSCRIPT_LINES:
                        self._transcript_lines = self._transcript_lines[-MAX_TRANSCRIPT_LINES:]

        log.info("Transcription loop: ended")

    def _finalize_prompt(self, prompt):
        """Send finalized prompt to the LLM."""
        if not prompt:
            log.info("Prompt was empty after wake phrase, returning to idle")
            self.conv.set_idle()
            return

        log.info(f"TIMING prompt_finalized \"{prompt}\"")
        self.conv.set_thinking()

        # Echo prevention: pause audio ingestion for the entire think+speak cycle
        self.audio.is_speaking = True
        self.audio.drain_audio_buffer()
        log.info("Echo prevention: paused audio ingestion")

        # Build context from rolling transcript
        with self._transcript_lock:
            context = "\n".join(self._transcript_lines[-20:])

        full_prompt = f"[Meeting transcript so far]\n{context}\n\n[Someone just said to you]\n{prompt}"
        log.info(f"Sending to LLM: {full_prompt[:200]}...")

        try:
            log.info("TIMING llm_request_sent")
            t_llm_start = time.time()
            reply = self._ask_llm(full_prompt)
            t_llm_end = time.time()
            log.info(f"TIMING llm_response_received ({t_llm_end - t_llm_start:.1f}s) \"{reply}\"")

            self.conv.set_speaking()
            log.info("TIMING tts_request_sent")
            t_speak_start = time.time()
            self._speak(reply)
            t_speak_end = time.time()
            log.info(f"TIMING tts_playback_done")
            log.info(
                f"Pipeline timing — llm: {t_llm_end - t_llm_start:.1f}s, "
                f"speak: {t_speak_end - t_speak_start:.1f}s, "
                f"total: {t_speak_end - t_llm_start:.1f}s"
            )
        except Exception as e:
            log.error(f"Pipeline error: {e}")
        finally:
            # Echo prevention: drain anything that leaked in, then resume ingestion
            self.audio.drain_audio_buffer()
            self.audio.is_speaking = False
            log.info("Echo prevention: resumed audio ingestion")

        self.conv.set_idle()

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    def _ask_llm(self, utterance):
        self.conversation_history.append({"role": "user", "content": utterance})
        response = self.openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=60,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                *self.conversation_history,
            ],
        )
        reply = response.choices[0].message.content
        self.conversation_history.append({"role": "assistant", "content": reply})
        return reply

    def _speak(self, text):
        t0 = time.time()
        audio_stream = self.eleven.text_to_speech.stream(
            text=text,
            voice_id=VOICE_ID,
            model_id="eleven_flash_v2_5",
        )
        proc = subprocess.Popen(
            ["mpv", "--no-terminal", f"--audio-device={BLACKHOLE_DEVICE}", "--", "-"],
            stdin=subprocess.PIPE,
        )
        first_chunk = True
        for chunk in audio_stream:
            if chunk:
                if first_chunk:
                    log.info(f"TIMING tts_first_chunk ({time.time() - t0:.2f}s)")
                    first_chunk = False
                proc.stdin.write(chunk)
        t_stream_done = time.time()
        log.info(f"TTS stream complete: {t_stream_done - t0:.2f}s")
        proc.stdin.close()
        proc.wait()
        log.info(f"TTS playback done: {time.time() - t0:.2f}s (mpv drain: {time.time() - t_stream_done:.2f}s)")

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def test_capture(self, _):
        log.debug("test_capture: called")
        if not os.path.exists(AUDIO_CAPTURE_HELPER):
            self.status_item.title = f"❌ Helper not found: {AUDIO_CAPTURE_HELPER}"
            return
        self.status_item.title = "🔴 Capturing 10s — play audio now..."
        threading.Thread(target=self._do_capture, daemon=True).start()

    def _do_capture(self):
        CAPTURE_SECONDS = 10
        OUTPUT_PATH = "/tmp/operator_test_capture.wav"
        log.debug("_do_capture: launching Swift helper")

        try:
            proc = subprocess.Popen(
                [AUDIO_CAPTURE_HELPER],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
            )
        except OSError as e:
            log.debug(f"_do_capture: failed to launch helper: {e}")
            self.status_item.title = f"❌ Helper launch failed: {e}"
            return

        # Log stderr from Swift helper in a background thread
        def read_stderr():
            for line in proc.stderr:
                log.debug(f"[swift] {line.decode().rstrip()}")
        threading.Thread(target=read_stderr, daemon=True).start()

        # Read PCM data for the capture duration
        bytes_needed = SAMPLE_RATE * 4 * CAPTURE_SECONDS
        data = b""
        while len(data) < bytes_needed:
            chunk = proc.stdout.read(min(4096, bytes_needed - len(data)))
            if not chunk:
                log.debug(f"_do_capture: helper stopped early after {len(data)} bytes")
                break
            data += chunk

        # Stop the helper
        log.debug("_do_capture: closing stdin to stop helper")
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.debug("_do_capture: helper didn't exit, terminating")
            proc.terminate()

        log.debug(f"_do_capture: helper exited with code {proc.returncode}")

        if not data:
            self.status_item.title = "❌ No audio captured"
            return

        audio = np.frombuffer(data, dtype=np.float32)
        sf.write(OUTPUT_PATH, audio, SAMPLE_RATE)
        duration = len(audio) / SAMPLE_RATE
        log.debug(f"_do_capture: saved {duration:.1f}s to {OUTPUT_PATH}")
        self.status_item.title = f"✅ Captured {duration:.1f}s → {OUTPUT_PATH}"

    def request_audio_permission(self, _):
        """Launch the helper briefly to trigger the Screen Recording permission prompt."""
        try:
            proc = subprocess.Popen(
                [AUDIO_CAPTURE_HELPER],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
            )
            time.sleep(2)
            proc.stdin.close()
            proc.wait(timeout=5)
            self.status_item.title = "Permission requested — check System Settings"
        except Exception as e:
            self.status_item.title = f"Permission error: {e}"

    def quit_app(self, _):
        self._stop_continuous_capture()
        if self._calendar_poller:
            self._calendar_poller.stop()
        rumps.quit_application()


if __name__ == "__main__":
    OperatorApp().run()
