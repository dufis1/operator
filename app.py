"""
Operator — AI Meeting Participant
Runs in the macOS menu bar. Listens to meeting audio for "operator",
then responds via text-to-speech through a virtual audio device.
"""
import os
import random
import subprocess
import tempfile
import threading
import time
import logging
import soundfile as sf
import numpy as np
from dotenv import load_dotenv
from faster_whisper import WhisperModel
import rumps
from elevenlabs.client import ElevenLabs
from openai import OpenAI
from PyObjCTools.AppHelper import callAfter
from calendar_join import CalendarPoller

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

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 4  # Float32
VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # George
WAKE_PHRASE = "operator"
MAX_TRANSCRIPT_LINES = 100  # rolling transcript history limit
UTTERANCE_CHECK_INTERVAL = 0.5   # seconds between audio checks
UTTERANCE_SILENCE_THRESHOLD = 2  # consecutive silent checks = utterance done (~1s)
UTTERANCE_MAX_DURATION = 10      # hard cap: finalize utterance after 10s
UTTERANCE_SILENCE_RMS = 0.02     # RMS below this = silence (tune if needed)
SHORT_UTTERANCE_THRESHOLD = 3.5  # seconds — skip backchannel for quick questions
# How long to wait for continuation after a backchannel NO before giving up
BACKCHANNEL_CONTINUATION_TIMEOUT = 10.0
# Whisper tiny hallucinates these on silence — backstop filter
WHISPER_HALLUCINATIONS = {
    "you", "thank you", "thanks", "thanks a lot", "bye", "goodbye",
    "the end", "i'm sorry", "sorry",
}
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


AUDIO_CAPTURE_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio_capture")
BACKCHANNEL_CLIPS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "backchannel_mmhmm.mp3"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "backchannel_goon.mp3"),
]
ACK_CLIPS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ack_yeah.mp3"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ack_yes.mp3"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ack_mmhm.mp3"),
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

        # Continuous audio capture state
        self._capture_proc = None
        self._audio_buffer = b""
        self._audio_lock = threading.Lock()
        self._capturing = False

        # Rolling transcript
        self._transcript_lines = []
        self._transcript_lock = threading.Lock()

        # Echo prevention — pause audio ingestion while speaking
        self._speaking = False

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
        self.whisper = WhisperModel("base", device="cpu", compute_type="int8")

        self._set_state("⚪", "Connecting to APIs...")
        self.openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.eleven = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])

        self._start_continuous_capture()
        threading.Thread(target=self._transcription_loop, daemon=True).start()

        self._calendar_poller = CalendarPoller()
        self._calendar_poller.start()

        self._set_state("⚪", "Listening for 'operator'...")

    def _play_backchannel(self):
        """Play a random backchannel clip through BlackHole (blocking)."""
        clip = random.choice(BACKCHANNEL_CLIPS)
        clip_name = os.path.basename(clip).replace("backchannel_", "").replace(".mp3", "")
        log.info(f"Operator says: \"{clip_name}\" (backchannel)")
        subprocess.run(
            ["mpv", "--no-terminal", f"--audio-device={BLACKHOLE_DEVICE}", "--", clip],
            check=False,
        )
        log.info("TIMING backchannel_done")

    def _play_acknowledgment(self):
        """Play a random acknowledgment clip through BlackHole, with echo prevention."""
        clip = random.choice(ACK_CLIPS)
        clip_name = os.path.basename(clip).replace("ack_", "").replace(".mp3", "")
        log.info(f"Operator says: \"{clip_name}\" (acknowledgment)")
        self._speaking = True
        self._drain_audio_buffer()
        subprocess.run(
            ["mpv", "--no-terminal", f"--audio-device={BLACKHOLE_DEVICE}", "--", clip],
            check=False,
        )
        time.sleep(0.2)
        self._drain_audio_buffer()
        self._speaking = False
        log.info("TIMING ack_done")

    def _check_completeness(self, text):
        """Ask GPT-4.1-mini whether the transcribed text is a complete thought. Returns True/False."""
        log.info(f"TIMING completeness_check_start \"{text}\"")
        try:
            # Include recent conversation context so follow-up questions
            # (e.g. "What temperature is it typically?") are recognized as complete
            with self._transcript_lock:
                recent = self._transcript_lines[-5:]
            context_block = ""
            if recent:
                context_block = "Recent conversation:\n" + "\n".join(recent) + "\n\n"

            response = self.openai_client.chat.completions.create(
                model="gpt-4.1-mini",
                max_tokens=3,
                messages=[
                    {"role": "system", "content": "You determine if a spoken utterance sounds like a complete thought, question, or statement that someone would expect a response to. Follow-up questions that reference a prior topic count as complete. Reply only YES or NO."},
                    {"role": "user", "content": f"{context_block}New utterance: {text}"},
                ],
            )
            answer = response.choices[0].message.content.strip().upper()
            is_complete = "YES" in answer
            log.info(f"TIMING completeness_check_done complete={is_complete} answer=\"{answer}\"")
            return is_complete
        except Exception as e:
            log.error(f"Completeness check failed: {e}")
            return True  # fail-open: treat as complete if check fails

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
            self._capturing = True
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
        """Continuously read PCM data from the Swift helper into _audio_buffer."""
        CHUNK_SIZE = 4096
        while self._capturing:
            chunk = self._capture_proc.stdout.read(CHUNK_SIZE)
            if not chunk:
                log.warning("Continuous capture: helper stopped (stdout closed)")
                self._capturing = False
                break
            # Echo prevention: discard audio while Operator is speaking
            if self._speaking:
                continue
            with self._audio_lock:
                self._audio_buffer += chunk

        log.info(f"Continuous capture: read loop ended, total bytes: {len(self._audio_buffer)}")

    def _stop_continuous_capture(self):
        """Stop the Swift helper."""
        self._capturing = False
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

    def _drain_audio_buffer(self):
        """Drain and return all accumulated audio bytes, resetting the buffer."""
        with self._audio_lock:
            data = self._audio_buffer
            self._audio_buffer = b""
        return data

    def _capture_next_utterance(self, is_prompt=False, no_speech_timeout=None):
        """Block until a complete utterance is detected. Returns transcribed text or ''.

        no_speech_timeout: if set, give up and return '' if no speech starts within
        this many seconds. Used for conversation follow-up mode.
        """
        speech_detected = False
        silence_count = 0
        utterance_audio = b""
        speech_start_time = None
        capture_start = time.time()
        # After a backchannel NO, track deadline for continuation so we don't hang forever
        continuation_deadline = None
        label = "prompt" if is_prompt else "ambient"
        log.info(f"TIMING {label}_capture_start")

        while self._capturing:
            time.sleep(UTTERANCE_CHECK_INTERVAL)

            # If no speech has started yet and we've exceeded no_speech_timeout, bail out
            if no_speech_timeout and not speech_detected:
                if time.time() - capture_start > no_speech_timeout:
                    log.info(f"TIMING {label}_timeout (no speech in {no_speech_timeout:.0f}s)")
                    return ""

            # If we're waiting for the user to continue after a backchannel NO,
            # give up after BACKCHANNEL_CONTINUATION_TIMEOUT seconds of silence
            if continuation_deadline and not speech_detected:
                if time.time() > continuation_deadline:
                    log.info("TIMING prompt_utterance_timeout (no continuation after backchannel)")
                    break

            raw = self._drain_audio_buffer()
            if raw:
                rms = float(np.sqrt(np.mean(np.frombuffer(raw, dtype=np.float32) ** 2)))
                if rms >= UTTERANCE_SILENCE_RMS:
                    speech_detected = True
                    if speech_start_time is None:
                        speech_start_time = time.time()
                        log.info(f"TIMING {label}_speech_first rms={rms:.4f}")
                    silence_count = 0
                    utterance_audio += raw
                    log.debug(f"Utterance speech (rms={rms:.4f})")
                elif speech_detected:
                    utterance_audio += raw
                    silence_count += 1
                    log.debug(f"Utterance silence {silence_count}/{UTTERANCE_SILENCE_THRESHOLD} (rms={rms:.4f})")
            elif speech_detected:
                silence_count += 1
                log.debug(f"Utterance no-audio silence {silence_count}/{UTTERANCE_SILENCE_THRESHOLD}")

            if speech_detected:
                if silence_count >= UTTERANCE_SILENCE_THRESHOLD:
                    speech_duration = time.time() - speech_start_time
                    log.info(f"TIMING {label}_utterance_done silence {speech_duration:.1f}s")

                    # Backchannel path: for prompt utterances ≥3.5s, check completeness
                    if is_prompt and speech_duration >= SHORT_UTTERANCE_THRESHOLD:
                        # Transcribe first to check if thought is complete
                        audio = np.frombuffer(utterance_audio, dtype=np.float32)
                        log.info(f"TIMING {label}_whisper_start (completeness check)")
                        partial_text = self._transcribe(audio)
                        log.info(f"TIMING {label}_whisper_done \"{partial_text}\"")

                        if partial_text and self._check_completeness(partial_text):
                            # Complete thought — finalize without backchannel
                            return partial_text
                        else:
                            # Incomplete — NOW play backchannel, then keep listening
                            bc_thread = threading.Thread(target=self._play_backchannel, daemon=True)
                            bc_thread.start()
                            log.info("TIMING backchannel_continue_listening")
                            bc_thread.join()
                            time.sleep(0.2)  # let any remaining echo clear the audio pipeline
                            self._drain_audio_buffer()
                            silence_count = 0
                            speech_detected = False
                            speech_start_time = None
                            continuation_deadline = time.time() + BACKCHANNEL_CONTINUATION_TIMEOUT
                            continue
                    else:
                        break
                if time.time() - speech_start_time > UTTERANCE_MAX_DURATION:
                    log.info(f"TIMING {label}_utterance_done max_duration")
                    break

        if not utterance_audio:
            return ""

        audio = np.frombuffer(utterance_audio, dtype=np.float32)
        log.info(f"TIMING {label}_whisper_start")
        text = self._transcribe(audio)
        log.info(f"TIMING {label}_whisper_done \"{text}\"")
        return text

    def _transcription_loop(self):
        """Utterance-based loop: detects 'operator' wake phrase via Whisper,
        routes to LLM for prompt utterances, accumulates ambient speech into
        the rolling transcript."""
        log.info("Transcription loop: started")

        while self._capturing:
            text = self._capture_next_utterance(is_prompt=False)
            if not text:
                continue

            text_lower = text.lower()

            if text_lower.strip().strip(".,!?") in WHISPER_HALLUCINATIONS:
                log.info(f"Ignoring hallucination: {text}")
                continue

            if WAKE_PHRASE in text_lower:
                # Find where "operator" appears and extract any trailing text
                idx = text_lower.find(WAKE_PHRASE)
                trailing = text[idx + len(WAKE_PHRASE):].strip().strip(",.:?!")

                if trailing:
                    # Inline: "operator, what's 2 plus 2?" — use trailing text directly
                    log.info(f"TIMING wake_inline prompt=\"{trailing}\"")
                    self._set_state("🔴", "Listening for prompt...")
                    self._finalize_prompt(trailing)
                else:
                    # Wake-only: "operator" — acknowledge, then capture the next utterance as the prompt
                    log.info("TIMING wake_only waiting_for_prompt")
                    self._set_state("🔴", "Listening for prompt...")
                    self._play_acknowledgment()
                    prompt = self._capture_next_utterance(is_prompt=True)
                    if prompt:
                        self._finalize_prompt(prompt)
                    else:
                        log.info("Prompt was empty after wake phrase, returning to idle")
                        self._set_state("⚪", "Listening for 'operator'...")
                        continue

                # After Operator responds, stay in conversation mode: keep accepting
                # follow-up replies without requiring the wake phrase again.
                # Exit when 20s pass with no speech.
                log.info("Entering conversation mode")
                while self._capturing:
                    self._set_state("🔴", "Listening...")
                    followup = self._capture_next_utterance(is_prompt=True, no_speech_timeout=20.0)
                    if not followup:
                        log.info("Conversation mode: no follow-up, returning to idle")
                        break
                    if followup.lower().strip().strip(".,!?") in WHISPER_HALLUCINATIONS:
                        continue
                    self._finalize_prompt(followup)
                self._set_state("⚪", "Listening for 'operator'...")
            else:
                # Ambient — add to rolling transcript
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
            self._set_state("⚪", "Listening for 'operator'...")
            return

        log.info(f"TIMING prompt_finalized \"{prompt}\"")
        self._set_state("🟡", "Thinking...")

        # Echo prevention: pause audio ingestion for the entire think+speak cycle
        self._speaking = True
        self._drain_audio_buffer()
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

            self._set_state("🟢", "Speaking...")
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
            self._drain_audio_buffer()
            self._speaking = False
            log.info("Echo prevention: resumed audio ingestion")

        self._set_state("⚪", "Listening for 'operator'...")

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    def _transcribe(self, audio):
        # Whisper reliably drops the first word without a short silence pad at the start.
        # Prepend 0.5s of silence so the model has context before speech begins.
        silence_pad = np.zeros(int(SAMPLE_RATE * 0.5), dtype=np.float32)
        audio = np.concatenate([silence_pad, audio])
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            sf.write(f.name, audio, SAMPLE_RATE)
            tmp_path = f.name
        segments, _ = self.whisper.transcribe(
            tmp_path, language="en",
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
        )
        os.unlink(tmp_path)
        return " ".join(seg.text.strip() for seg in segments).strip()

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
