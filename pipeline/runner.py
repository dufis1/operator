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
import itertools
import logging
import numpy as np
import os
import random
import subprocess
import threading
import time

import config
from openai import OpenAI

from connectors.captions_adapter import CaptionsAdapter
from pipeline.audio import AudioProcessor, WHISPER_HALLUCINATIONS
from pipeline.captions import CaptionProcessor
from pipeline.conversation import ConversationState, CONVERSATION_TIMEOUT
from pipeline import fillers
from pipeline.llm import LLMClient, MAX_TRANSCRIPT_LINES
from pipeline.tts import TTSClient
from pipeline.sanitize import sanitize_for_speech
from pipeline.wake import detect_wake_phrase

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _SpeculativeResult:
    """Holds the in-flight result of a speculative Whisper + LLM call."""
    def __init__(self):
        self.transcript = None        # Whisper result on the first-silence snapshot
        self.full_prompt = None       # Context-augmented prompt sent to LLM
        self.llm_reply = None         # LLM reply (None if not yet done or failed)
        self.for_assistant = None     # classifier result (True/False/None=not run)
        self.ready = threading.Event()


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
        self._caption_mode = isinstance(connector, CaptionsAdapter)

        self._transcript_lines = []
        self._transcript_lock = threading.Lock()
        self._capture_proc = None

        self.audio = None
        self.captions = None  # CaptionProcessor, set in run() for caption mode
        self.conv = None
        self.llm = None
        self.tts = None

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self, meeting_url=None):
        """Initialise pipeline, optionally join a meeting, then run the main loop.

        Blocks until capture stops or stop() is called.
        """
        log.info("STARTUP begin")

        if self._caption_mode:
            log.info("STARTUP mode=captions (DOM-based, no Whisper)")
            self.captions = CaptionProcessor()
        else:
            log.info("STARTUP mode=audio (ScreenCaptureKit + Whisper)")
            log.info("STARTUP loading Whisper model...")
            self.audio = AudioProcessor()

        log.info("STARTUP connecting to APIs...")
        openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
        self.llm = LLMClient(openai_client)
        log.info("STARTUP initializing TTS...")
        self.tts = TTSClient(self._tts_output_device)

        self.conv = ConversationState(on_state_change=self._on_state_change)

        if meeting_url:
            log.info(f"STARTUP joining meeting {meeting_url}")

            # For caption mode, wire up the callback before joining
            if self._caption_mode:
                self.connector.set_caption_callback(self.captions.on_caption_update)
                self.captions.set_transcript_callback(self._on_transcript_text)

            self.connector.join(meeting_url)
            # Wait for browser thread to signal join result
            join_status = self.connector.join_status
            if join_status:
                if not join_status.ready.wait(timeout=60):
                    log.error("STARTUP join timed out (60s)")
                    self._on_state_change("error", "Join timed out")
                    self.connector.leave()
                    return
                if not join_status.success:
                    reason = join_status.failure_reason or "unknown"
                    log.error(f"STARTUP join failed: {reason}")
                    if "session_expired" in reason:
                        log.error("Re-export session: python scripts/auth_export.py")
                        print("\n❌ Not authenticated — run this to sign in:\n")
                        print("   python scripts/auth_export.py\n")
                        self._on_state_change(
                            "error",
                            "Session expired — re-authenticate with scripts/auth_export.py",
                        )
                    else:
                        self._on_state_change("error", f"Join failed: {reason}")
                    self.connector.leave()
                    return
                if join_status.session_recovered:
                    log.warning("STARTUP session recovered via cookie injection — "
                                "consider re-running scripts/auth_export.py")
            else:
                time.sleep(12)  # fallback for connectors without join_status

        if self._caption_mode:
            self.captions.capturing = True
            log.info("STARTUP caption processing active")
        else:
            log.info("STARTUP starting audio capture...")
            self._start_capture()
            if not self.audio.capturing:
                log.error("STARTUP audio capture failed — aborting")
                print("\n❌ Audio capture failed to start — check logs at /tmp/operator.log\n")
                self.connector.leave()
                return

        self.conv.set_idle()
        log.info("STARTUP complete — idle, listening for wake phrase")

        try:
            if self._caption_mode:
                self._caption_loop()
            else:
                self._transcription_loop()
        finally:
            if self._caption_mode:
                self.captions.stop()
            else:
                self._stop_capture()

    def stop(self):
        """Signal the main loop to exit cleanly."""
        self._stop_event.set()
        if self.audio:
            self.audio.capturing = False
        if self.captions:
            self.captions.stop()

    # ------------------------------------------------------------------
    # Audio capture
    # ------------------------------------------------------------------

    def _start_capture(self, stderr_tag="capture", _tcc_retried=False):
        """Launch the audio stream via the connector and spin up read loops."""
        self._verify_audio_capture_signature()
        self._tcc_retried = _tcc_retried
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
                self.audio.capturing = False
                rc = self._capture_proc.wait()
                if rc == 3:
                    log.error(
                        "AgentRunner: Screen Recording permission denied. "
                        "Grant permission to your terminal app in "
                        "System Settings > Privacy & Security > Screen Recording."
                    )
                    print("\n❌ Screen Recording permission denied — grant it here:\n")
                    print("   System Settings > Privacy & Security > Screen Recording\n")
                elif rc == 4 and not self._tcc_retried:
                    log.warning(
                        "AgentRunner: audio capture hung (exit 4) — "
                        "resetting TCC cache and retrying..."
                    )
                    try:
                        subprocess.run(
                            ["tccutil", "reset", "ScreenCapture"],
                            capture_output=True, timeout=5,
                        )
                        log.info("AgentRunner: TCC ScreenCapture cache reset")
                    except Exception as e:
                        log.warning(f"AgentRunner: tccutil reset failed: {e}")
                    self._start_capture(_tcc_retried=True)
                    return
                elif rc == 4:
                    log.error(
                        "AgentRunner: audio capture hung after TCC reset "
                        "(exit 4). macOS Screen Recording permission cache "
                        "is stuck. Please restart Operator. If that doesn't "
                        "work, restart your Mac."
                    )
                    print("\n❌ Audio capture is stuck — restart Operator or restart your Mac\n")
                elif rc != 0:
                    log.error(f"AgentRunner: audio capture exited with code {rc}")
                else:
                    log.warning("AgentRunner: capture process stopped (stdout closed)")
                break
            self.audio.feed_audio(chunk)
        log.info("AgentRunner: audio read loop ended")

    @staticmethod
    def _verify_audio_capture_signature():
        """Check audio_capture binary exists and has the expected codesign identity."""
        binary = os.path.join(_BASE, "audio_capture")
        if not os.path.exists(binary):
            log.warning("AgentRunner: audio_capture binary not found — skipping signature check")
            return
        try:
            result = subprocess.run(
                ["codesign", "-d", "--verbose=1", binary],
                capture_output=True, text=True, timeout=5,
            )
            # codesign -d outputs to stderr
            output = result.stderr.strip()
            if result.returncode != 0:
                log.warning(
                    f"AgentRunner: audio_capture has no valid signature ({output}). "
                    "Run: codesign --force --sign - --identifier "
                    "com.operator.audio-capture audio_capture"
                )
                print("\n⚠️  audio_capture needs re-signing — run this:\n")
                print("   codesign --force --sign - --identifier com.operator.audio-capture audio_capture\n")
            elif "com.operator.audio-capture" not in output:
                log.warning(
                    f"AgentRunner: audio_capture has unexpected identity: {output}. "
                    "Screen Recording permission may not work. "
                    "Run: codesign --force --sign - --identifier "
                    "com.operator.audio-capture audio_capture"
                )
                print("\n⚠️  audio_capture needs re-signing — run this:\n")
                print("   codesign --force --sign - --identifier com.operator.audio-capture audio_capture\n")
            else:
                log.debug(f"AgentRunner: audio_capture signature OK — {output}")
        except Exception as e:
            log.debug(f"AgentRunner: codesign check skipped: {e}")

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
                    spec = _SpeculativeResult()
                    prompt = self.audio.capture_next_utterance(
                        is_prompt=True,
                        on_first_silence=self._make_speculative_callback(spec),
                    )
                    if prompt:
                        self._finalize_prompt(prompt, speculative=spec)
                    else:
                        log.info("Prompt empty after wake phrase — returning to idle")
                        self.conv.set_idle()
                        continue

                # Conversation mode: accept follow-ups without re-triggering wake phrase
                log.info("Entering conversation mode")
                while self.audio.capturing and not self._stop_event.is_set():
                    self.conv.set_listening("Listening...")
                    spec = _SpeculativeResult()
                    followup = self.audio.capture_next_utterance(
                        is_prompt=True,
                        no_speech_timeout=CONVERSATION_TIMEOUT,
                        on_first_silence=self._make_speculative_callback(spec),
                    )
                    if not followup:
                        log.info("Conversation mode: no follow-up — returning to idle")
                        break
                    if followup.lower().strip().strip(".,!?") in WHISPER_HALLUCINATIONS:
                        continue
                    self._finalize_prompt(followup, speculative=spec)
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
    # Caption-mode loop
    # ------------------------------------------------------------------

    def _caption_loop(self):
        """Main loop for caption mode. Blocks until stop() is called."""
        log.info("AgentRunner: caption loop started")

        while self.captions.capturing and not self._stop_event.is_set():
            # Single call handles both wake detection (real-time) and prompt capture
            spec = _SpeculativeResult()
            speaker, prompt = self.captions.capture_next_wake_utterance(
                on_speculative=self._make_caption_speculative_callback(spec),
            )

            if not prompt:
                continue

            log.info(f"TIMING wake_caption speaker={speaker} prompt=\"{prompt[:60]}\"")
            self.conv.set_listening("Listening for prompt...")
            self._finalize_prompt(prompt, speculative=spec, caption_mode=True)

            # Conversation mode: accept follow-ups without re-triggering wake phrase.
            # Exit when the LLM classifier (run in parallel with speculative LLM)
            # decides the speaker has moved on.
            log.info("Entering conversation mode")
            while self.captions.capturing and not self._stop_event.is_set():
                self.conv.set_listening("Listening...")
                spec = _SpeculativeResult()
                followup_speaker, followup = self.captions.capture_next_wake_utterance(
                    require_wake=False,
                    on_speculative=self._make_caption_speculative_callback(spec, run_classifier=True),
                )
                if not followup:
                    log.info("Conversation mode: capture ended — returning to idle")
                    break
                # Wait for speculative thread (classifier result lives inside it)
                spec.ready.wait()
                if spec.for_assistant is False:
                    log.info("Conversation mode: utterance not for assistant — returning to idle")
                    break
                self._finalize_prompt(followup, speculative=spec, caption_mode=True)
            self.conv.set_idle()

        log.info("AgentRunner: caption loop ended")

    def _make_caption_speculative_callback(self, spec: _SpeculativeResult, run_classifier: bool = False):
        """Return an on_speculative callback for caption mode (no Whisper step)."""
        def callback(prompt_text: str):
            threading.Thread(
                target=self._run_caption_speculative,
                args=(prompt_text, spec, run_classifier),
                daemon=True,
                name="speculative-caption",
            ).start()
        return callback

    def _run_caption_speculative(self, prompt_text: str, spec: _SpeculativeResult, run_classifier: bool = False):
        """Run speculative LLM on caption prompt text. No Whisper needed.

        When run_classifier=True (follow-up mode), appends a PASS instruction
        so the model doubles as a classifier: it replies "PASS" if not addressed,
        or a normal response if it is. One call, zero added latency.
        """
        try:
            spec.transcript = prompt_text  # For match-checking in _finalize_prompt
            log.info(f"TIMING caption_speculative_llm_start prompt=\"{prompt_text[:60]}\"")

            with self._transcript_lock:
                # Exclude the last line (current utterance) — it's already in the prompt section
                context = "\n".join(self._transcript_lines[-20:-1]) if len(self._transcript_lines) > 1 else ""

            if run_classifier:
                spec.full_prompt = (
                    f"[Meeting transcript so far]\n{context}\n\n"
                    f"[Someone just said]\n{prompt_text}\n\n"
                    f"[Note] This may or may not be addressed to you. "
                    f"If it is not addressed to you, respond with only the word PASS."
                )
                spec.llm_reply = self.llm.ask(spec.full_prompt, record=False)
                spec.for_assistant = not spec.llm_reply.strip().upper().startswith("PASS")
                log.info(f"TIMING caption_combined_classify for_assistant={spec.for_assistant}")
            else:
                spec.full_prompt = (
                    f"[Meeting transcript so far]\n{context}\n\n"
                    f"[Someone just said to you]\n{prompt_text}"
                )
                spec.llm_reply = self.llm.ask(spec.full_prompt, record=False)

            log.info(f"TIMING caption_speculative_llm_done reply=\"{spec.llm_reply[:60]}\"")
        except Exception as e:
            log.error(f"Speculative caption LLM error: {e}", exc_info=True)
        finally:
            spec.ready.set()

    def _on_transcript_text(self, speaker, text):
        """Callback from CaptionProcessor — feeds ALL caption text into transcript."""
        with self._transcript_lock:
            # Use the latest full text for this speaker (not deltas)
            # Replace the last entry if same speaker, append if new speaker
            entry = f"{speaker}: {text}"
            if self._transcript_lines and self._transcript_lines[-1].startswith(f"{speaker}: "):
                self._transcript_lines[-1] = entry
            else:
                self._transcript_lines.append(entry)
            if len(self._transcript_lines) > MAX_TRANSCRIPT_LINES:
                self._transcript_lines = self._transcript_lines[-MAX_TRANSCRIPT_LINES:]

    # ------------------------------------------------------------------
    # Prompt handling
    # ------------------------------------------------------------------

    def _finalize_prompt(self, prompt, speculative=None, caption_mode=False):
        """Resolve the LLM reply and speak it, playing fillers while synthesis runs."""
        if not prompt:
            self.conv.set_idle()
            return

        log.info(f"TIMING prompt_finalized \"{prompt}\"")
        self.conv.set_thinking()

        # Echo guard: pause ingestion so the bot's own speech doesn't re-trigger
        if caption_mode:
            self.captions.is_speaking = True
            log.info("Echo prevention: paused caption processing")
        else:
            self.audio.is_speaking = True
            self.audio.drain_audio_buffer()
            log.info("Echo prevention: paused audio ingestion")

        with self._transcript_lock:
            # Exclude the last line (current utterance) — it's already in the prompt section
            context = "\n".join(self._transcript_lines[-20:-1]) if len(self._transcript_lines) > 1 else ""

        full_prompt = (
            f"[Meeting transcript so far]\n{context}\n\n"
            f"[Someone just said to you]\n{prompt}"
        )

        try:
            # --- LLM: use speculative result if it matches, else call normally ---
            t0 = time.time()
            if (speculative
                    and speculative.ready.is_set()
                    and speculative.transcript == prompt
                    and speculative.llm_reply):
                reply = speculative.llm_reply
                self.llm.record_exchange(speculative.full_prompt, reply)
                log.info(f"TIMING llm_speculative_hit \"{reply}\"")
            else:
                # If speculative is still in-flight, wait briefly for it
                if speculative and not speculative.ready.is_set():
                    speculative.ready.wait(timeout=0.3)
                    if (speculative.ready.is_set()
                            and speculative.transcript == prompt
                            and speculative.llm_reply):
                        reply = speculative.llm_reply
                        self.llm.record_exchange(speculative.full_prompt, reply)
                        log.info(f"TIMING llm_speculative_hit (late) \"{reply}\"")
                    else:
                        log.info("TIMING llm_request_sent")
                        reply = self.llm.ask(full_prompt)
                        log.info(f"TIMING llm_response_received ({time.time() - t0:.1f}s) \"{reply}\"")
                else:
                    log.info("TIMING llm_request_sent")
                    reply = self.llm.ask(full_prompt)
                    log.info(f"TIMING llm_response_received ({time.time() - t0:.1f}s) \"{reply}\"")

            # --- Sanitize for TTS ---
            reply = sanitize_for_speech(reply)

            # --- TTS synthesis in background, fillers in foreground ---
            self.conv.set_speaking()
            t_tts = time.time()
            log.info("TIMING tts_request_sent")

            synthesis_done = threading.Event()
            wav_result = [None]

            def _synthesize():
                try:
                    wav_result[0] = self.tts.synthesize(reply)
                except Exception as exc:
                    log.error(f"Synthesis error: {exc}")
                finally:
                    synthesis_done.set()

            threading.Thread(target=_synthesize, daemon=True).start()

            # Play filler clips until synthesis is ready
            filler_bucket = fillers.classify(prompt)
            log.info(f"Filler bucket: {filler_bucket} (prompt: {prompt!r})")
            filler_clips = fillers.get_clips(filler_bucket)
            if filler_clips:
                for clip in itertools.cycle(filler_clips):
                    if synthesis_done.is_set():
                        break
                    log.info(f"Filler clip: {os.path.basename(clip)}")
                    self.tts.play_clip(clip)

            synthesis_done.wait()

            # Play the actual response
            t_play = time.time()
            self.tts.play_audio(wav_result[0])
            t_done = time.time()
            log.info(
                f"Pipeline timing — llm: {t_play - t0:.1f}s, "
                f"speak: {t_done - t_play:.1f}s, "
                f"total: {t_done - t0:.1f}s"
            )

        except Exception as e:
            log.error(f"Pipeline error: {e}", exc_info=True)
        finally:
            time.sleep(config.ECHO_GUARD_SECONDS)
            if caption_mode:
                self.captions.is_speaking = False
                log.info("Echo prevention: resumed caption processing")
            else:
                self.audio.drain_audio_buffer()
                self.audio.is_speaking = False
                log.info("Echo prevention: resumed audio ingestion")

        self.conv.set_idle()

    def _make_speculative_callback(self, spec: _SpeculativeResult):
        """Return an on_first_silence callback that kicks off speculative processing."""
        def callback(audio_bytes: bytes):
            threading.Thread(
                target=self._run_speculative,
                args=(audio_bytes, spec),
                daemon=True,
                name="speculative",
            ).start()
        return callback

    def _run_speculative(self, audio_bytes: bytes, spec: _SpeculativeResult):
        """Run Whisper + LLM on first-silence audio snapshot. Non-blocking."""
        try:
            audio = np.frombuffer(audio_bytes, dtype=np.float32)
            text = self.audio.transcribe(audio)
            spec.transcript = text
            log.debug(f"Speculative Whisper: {text!r}")

            if text and text.lower().strip().strip(".,!?") not in WHISPER_HALLUCINATIONS:
                with self._transcript_lock:
                    context = "\n".join(self._transcript_lines[-20:])
                spec.full_prompt = (
                    f"[Meeting transcript so far]\n{context}\n\n"
                    f"[Someone just said to you]\n{text}"
                )
                spec.llm_reply = self.llm.ask(spec.full_prompt, record=False)
                log.debug(f"Speculative LLM: {spec.llm_reply!r}")
        except Exception as e:
            log.debug(f"Speculative processing error: {e}")
        finally:
            spec.ready.set()

    def _play_acknowledgment(self):
        """Play a random acknowledgment clip through the TTS output device."""
        clip = random.choice(ACK_CLIPS)
        clip_name = os.path.basename(clip).replace("ack_", "").replace(".mp3", "")
        log.info(f"Operator says: \"{clip_name}\" (acknowledgment)")
        self.audio.is_speaking = True
        self.audio.drain_audio_buffer()
        self.tts.play_clip(clip)
        time.sleep(0.5)
        self.audio.drain_audio_buffer()
        self.audio.is_speaking = False
        log.info("TIMING ack_done")

    # ------------------------------------------------------------------
    # Default state change handler
    # ------------------------------------------------------------------

    @staticmethod
    def _log_state_change(state, label):
        log.info(f"State → {state} ({label})")
