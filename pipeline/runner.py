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
import numpy as np
import os
import random
import re
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
from pipeline.latency_probe import LatencyProbe
from pipeline.sanitize import sanitize_for_speech
from pipeline.wake import detect_wake_phrase

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Full wake phrase regex (punctuation-tolerant) for conversation mode reset.
# Matches "hey operator", "hey, operator", etc. — NOT bare "operator".
_FULL_WAKE_RE = re.compile(
    r"[,\s]+".join(re.escape(w) for w in config.WAKE_PHRASE.split()),
    re.IGNORECASE,
)


ACK_CLIPS = [
    os.path.join(_BASE, "assets", "ack_yeah.mp3"),
    os.path.join(_BASE, "assets", "ack_yes.mp3"),
    os.path.join(_BASE, "assets", "ack_mmhm.mp3"),
]

log = logging.getLogger(__name__)


def _normalize_for_match(text: str) -> str:
    """Normalize caption text for comparison.

    Google's ASR rewrites captions between updates — changing case,
    substituting symbols (e.g. 'plus' → '+'), and tweaking punctuation.
    This normalizes both sides so matches aren't lost to cosmetic diffs.
    """
    t = text.lower().strip()
    # Symbol → word
    t = t.replace("+", " plus ").replace("=", " equals ").replace("-", " minus ")
    # Number word → digit (Google ASR freely swaps these)
    for word, digit in [("zero", "0"), ("one", "1"), ("two", "2"), ("three", "3"),
                        ("four", "4"), ("five", "5"), ("six", "6"), ("seven", "7"),
                        ("eight", "8"), ("nine", "9"), ("ten", "10")]:
        t = t.replace(word, digit)
    t = re.sub(r"[^\w\s]", "", t)      # strip punctuation
    return re.sub(r"\s+", " ", t).strip()


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
        self._last_utterance = None   # raw user prompt text (for classifier context)
        self._last_reply = None       # raw LLM reply text (for classifier context)
        self._latency_probe = LatencyProbe()

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run_polling(self, meeting_queue):
        """Wait for meeting URLs from CalDAV, run each one, loop back.

        Used by the macOS menu bar app with CalDAV polling.
        Blocks until stop() is called or a None sentinel is received.
        """
        import queue as _queue
        log.info("STARTUP polling mode — waiting for meetings")
        self._on_state_change("idle", "Waiting for meeting...")

        while not self._stop_event.is_set():
            try:
                meeting_url = meeting_queue.get(timeout=1.0)
            except _queue.Empty:
                continue

            if meeting_url is None:  # sentinel from poller.stop()
                break

            log.info(f"POLLING received meeting URL: {meeting_url}")
            self._on_state_change("idle", f"Joining {meeting_url}...")

            self.run(meeting_url)

            # Clean up after meeting
            self.connector.leave()
            self._stop_event.clear()
            self._transcript_lines.clear()
            self.captions = None
            self.audio = None
            self._on_state_change("idle", "Waiting for meeting...")
            log.info("POLLING meeting ended — waiting for next")

        log.info("POLLING loop exited")

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

        # Start TTS init in background — Kokoro model load (~5s) overlaps with
        # browser launch + page navigation. TTS isn't needed until first response.
        log.info("STARTUP initializing TTS (background)...")
        self._tts_ready = threading.Event()
        def _init_tts():
            self.tts = TTSClient(self._tts_output_device)
            self._tts_ready.set()
            log.info("STARTUP TTS ready (background)")
        threading.Thread(target=_init_tts, daemon=True, name="tts-init").start()

        self.conv = ConversationState(on_state_change=self._on_state_change)

        if meeting_url:
            log.info(f"STARTUP joining meeting {meeting_url}")

            # For caption mode, wire up callbacks before joining
            if self._caption_mode:
                self.connector.set_caption_callback(self.captions.on_caption_update)
                self.captions.set_transcript_callback(self._on_transcript_text)
                # Signal caption loop to exit when browser session ends
                self.connector._on_disconnect = lambda: self.captions.stop()

            self.connector.join(meeting_url)
            # Wait for browser thread to signal join result
            join_status = self.connector.join_status
            if join_status:
                join_timeout = config.IDLE_TIMEOUT_SECONDS + 60
                if not join_status.ready.wait(timeout=join_timeout):
                    log.error(f"STARTUP join timed out ({join_timeout}s)")
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
                    elif "already_running" in reason:
                        print("\n⚠️  Another Operator session is already running.")
                        print("   Use --force to stop it and start a new one.\n")
                        self._on_state_change(
                            "error",
                            "Already running — stop the other session first",
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

        if config.LATENCY_PROBE_ENABLED:
            self._latency_probe.start()
        else:
            log.info("LatencyProbe: disabled via config")
        self.conv.set_idle()
        log.info("STARTUP complete — idle, listening for wake phrase")

        try:
            if self._caption_mode:
                self._caption_loop()
            else:
                self._transcription_loop()
        finally:
            self._latency_probe.stop()
            if self._caption_mode:
                self.captions.stop()
            else:
                self._stop_capture()

    def stop(self):
        """Signal the main loop to exit cleanly."""
        self._stop_event.set()
        self._latency_probe.stop()
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
                        is_prompt=True,
                        no_speech_timeout=CONVERSATION_TIMEOUT,
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
    # Caption-mode loop
    # ------------------------------------------------------------------

    def _caption_loop(self):
        """Main loop for caption mode. Blocks until stop() is called."""
        log.info("AgentRunner: caption loop started")

        while self.captions.capturing and not self._stop_event.is_set():
            # Single call handles both wake detection (real-time) and prompt capture
            speaker, prompt = self.captions.capture_next_wake_utterance()

            if not prompt:
                continue

            log.info(f"TIMING wake_caption speaker={speaker} prompt=\"{prompt[:60]}\"")
            self.conv.set_listening("Listening for prompt...")
            result = self._finalize_prompt(prompt, caption_mode=True, stream_classify=True)
            if result == "pass":
                log.info("TIMING wake PASS — ignoring ambient speech")
                continue

            # Conversation mode: accept follow-ups without re-triggering wake phrase.
            # Streaming first-token classification: PASS exits immediately.
            # "Hey operator" during conversation resets to wake mode.
            log.info("Entering conversation mode")
            while self.captions.capturing and not self._stop_event.is_set():
                self.conv.set_listening("Listening...")
                # Generous backstop — PASS/EXIT handle normal exits; this is
                # a silent safety net for when everyone goes quiet.
                followup_speaker, followup = self.captions.capture_next_wake_utterance(
                    require_wake=False,
                    no_speech_timeout=60,
                )
                if not followup:
                    log.info("Conversation mode: no follow-up — returning to idle")
                    break

                # "Hey operator" resets to wake mode — treat as fresh wake trigger
                m = _FULL_WAKE_RE.search(followup)
                if m:
                    log.info(f"Conversation mode: wake phrase detected — resetting to wake mode")
                    # Send full caption text — LLM gets full context including
                    # any pre-wake-phrase content the user wants answered.
                    self._finalize_prompt(followup, caption_mode=True, stream_classify=True)
                    # Exit conversation loop — outer loop will pick up fresh wake
                    break

                result = self._finalize_prompt(
                    followup, caption_mode=True, stream_classify=True,
                )
                if result == "pass":
                    log.info("Conversation mode: PASS — exiting conversation mode")
                    break
                if result == "exit":
                    log.info("Conversation mode: EXIT — responded and exiting")
                    break
            self.conv.set_idle()

        log.info("AgentRunner: caption loop ended")

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

    def _finalize_prompt(self, prompt, caption_mode=False, stream_classify=False):
        """Stream-classify the utterance and speak the response.

        When stream_classify=True, the LLM can respond with PASS to indicate
        the utterance is not directed at the bot. The first token from the
        streaming response decides: PASS → suppress, anything else → it IS
        the response (play filler, finish streaming, speak).

        Used in both wake mode (catches "hey operator" said to someone else)
        and conversation mode (catches ambient meeting speech).

        Returns:
            "responded" — response was played (stream_classify=True).
            "pass"      — LLM classified as not-for-operator.
            True        — non-streaming success.
            The abort path returns the result of the recursive call.
        """
        if not prompt:
            self.conv.set_idle()
            return "pass" if stream_classify else False

        # Ensure TTS background init has finished before we need to synthesize
        if not self._tts_ready.is_set():
            log.info("TIMING waiting for TTS init...")
            self._tts_ready.wait()

        log.info(f"TIMING prompt_finalized \"{prompt}\"")
        self.conv.set_thinking()

        # Echo guard: pause ingestion so the bot's own speech doesn't re-trigger
        if caption_mode:
            self.captions.abort_event.clear()
            self.captions.is_speaking = True
            log.info("Echo prevention: paused caption processing")
        else:
            self.audio.is_speaking = True
            self.audio.drain_audio_buffer()
            log.info("Echo prevention: paused audio ingestion")

        with self._transcript_lock:
            context = "\n".join(self._transcript_lines[-20:-1]) if len(self._transcript_lines) > 1 else ""

        # Build prompt — streaming paths add PASS/EXIT instructions
        if stream_classify and self._last_utterance and self._last_reply:
            # Conversation mode: include last exchange for context
            full_prompt = (
                f"[Meeting transcript so far]\n{context}\n\n"
                f"[Your last exchange]\nThey asked: {self._last_utterance}\n"
                f"You answered: {self._last_reply}\n\n"
                f"[Someone just said]\n{prompt}\n\n"
                f"[Instruction] You are in a live meeting. Someone spoke after your last response.\n"
                f"If this is NOT directed at you — they're addressing another participant, "
                f"continuing the meeting, or it's ambient speech — respond with only PASS.\n"
                f"If the speaker is wrapping up with you — e.g. \"thanks\", \"that's all\", "
                f"\"got it\" — start your response with EXIT then a space, then your brief "
                f"sign-off (e.g. \"EXIT You're welcome!\"). This signals the conversation is over.\n"
                f"If this IS a follow-up directed at you, respond normally (1-2 short spoken sentences)."
            )
        elif stream_classify:
            # Wake mode: wake phrase was detected but speech may be ambient
            full_prompt = (
                f"[Meeting transcript so far]\n{context}\n\n"
                f"[Someone just said]\n{prompt}\n\n"
                f"[Instruction] This followed the wake phrase \"hey operator\" in a live meeting.\n"
                f"If this seems like ambient speech that happened to contain your name "
                f"— not actually a question or request for you — respond with only PASS.\n"
                f"If this IS directed at you, respond normally (1-2 short spoken sentences)."
            )
        else:
            full_prompt = (
                f"[Meeting transcript so far]\n{context}\n\n"
                f"[Someone just said to you]\n{prompt}"
            )

        response_played = False
        is_exit = False
        filler_done = threading.Event()

        # Filler launcher shared between both paths
        def _start_filler():
            filler_bucket = fillers.classify(prompt)
            filler_clips = fillers.get_clips(filler_bucket)
            if filler_clips:
                clip = filler_clips[0]
                log.info(f"TIMING filler_play_start clip={os.path.basename(clip)} bucket={filler_bucket}")
                self._latency_probe.set_active(False)
                def _play():
                    self.tts.play_clip(clip)
                    log.info("TIMING filler_play_done")
                    filler_done.set()
                threading.Thread(target=_play, daemon=True, name="filler").start()
            else:
                log.info(f"Filler: no clips for bucket={filler_bucket}, skipping")
                filler_done.set()

        try:
            t_finalized = time.time()

            if stream_classify:
                # ── Streaming path: LLM first → classify first token → then filler ──
                t0 = time.time()
                log.info("TIMING llm_stream_start")
                stream = self.llm.ask_stream(full_prompt)

                # Accumulate until we have a non-whitespace token
                first_token = ""
                all_tokens = []
                for token in stream:
                    all_tokens.append(token)
                    first_token += token
                    if first_token.strip():
                        break

                t_first = time.time()
                log.info(f"TIMING llm_first_token elapsed={t_first - t0:.3f}s token=\"{first_token.strip()}\"")

                first_upper = first_token.strip().upper()

                # PASS → not for operator, suppress everything
                if first_upper.startswith("PASS"):
                    log.info("TIMING llm_classified=PASS — not for operator")
                    for _ in stream:
                        pass  # drain the stream
                    filler_done.set()
                    return "pass"

                # EXIT → wrap-up response, will signal conversation exit after playback
                is_exit = first_upper.startswith("EXIT")
                if is_exit:
                    log.info("TIMING llm_classified=EXIT — wrap-up response")
                    # Strip the EXIT prefix from the collected tokens
                    stripped = first_token.strip()[4:].lstrip()
                    all_tokens = [stripped] if stripped else []

                # Not PASS ��� this IS the response. Start filler, collect rest.
                _start_filler()
                for token in stream:
                    all_tokens.append(token)
                reply = "".join(all_tokens)

                t_stream_done = time.time()
                log.info(f"TIMING llm_stream_done elapsed={t_stream_done - t0:.3f}s reply=\"{reply[:60]}\"")
                self.llm.record_exchange(full_prompt, reply)

            else:
                # ── Non-streaming path: filler first → blocking LLM ──
                _start_filler()
                t0 = time.time()
                log.info("TIMING llm_request_sent")
                reply = self.llm.ask(full_prompt)
                log.info(f"TIMING llm_response_received elapsed={time.time() - t0:.3f}s reply=\"{reply[:60]}\"")

            t_llm_resolved = time.time()
            log.info(f"TIMING llm_resolved elapsed_from_finalized={t_llm_resolved - t_finalized:.3f}s")

            # --- Sanitize for TTS ---
            reply = sanitize_for_speech(reply)

            # Track for conversation context
            self._last_utterance = prompt
            self._last_reply = reply

            # Set TTS text for echo detection in caption handler
            if caption_mode:
                self.captions._tts_text = reply

            # --- TTS synthesis ---
            self.conv.set_speaking()
            t_synth_start = time.time()
            log.info("TIMING tts_synthesis_start")
            synthesis_done = threading.Event()
            wav_result = [None]

            def _synthesize():
                t_s = time.time()
                try:
                    wav_result[0] = self.tts.synthesize(reply)
                except Exception as exc:
                    log.error(f"Synthesis error: {exc}")
                finally:
                    log.info(f"TIMING tts_synthesis_done elapsed={time.time() - t_s:.3f}s")
                    synthesis_done.set()

            threading.Thread(target=_synthesize, daemon=True).start()
            synthesis_done.wait()
            synth_elapsed = time.time() - t_synth_start

            t_tts_resolved = time.time()
            log.info(f"TIMING tts_resolved elapsed_from_finalized={t_tts_resolved - t_finalized:.3f}s")

            # --- Wait for filler ---
            t_ready_to_play = time.time()
            filler_done.wait()
            filler_wait_elapsed = time.time() - t_ready_to_play
            log.info(f"TIMING filler_wait_done elapsed={filler_wait_elapsed:.3f}s")

            # --- Interruption check: did the speaker keep talking during processing? ---
            if caption_mode and self.captions.abort_event.is_set():
                with self.captions._lock:
                    updated_text = self.captions._current_text.strip()
                    abort_speaker = self.captions._abort_speaker
                # Same speaker, text extends the original prompt → stream-classify
                if abort_speaker and updated_text:
                    orig_norm = _normalize_for_match(prompt)
                    updated_norm = _normalize_for_match(updated_text)
                    is_continuation = (
                        updated_norm.startswith(orig_norm) and updated_norm != orig_norm
                    )
                    if is_continuation:
                        log.info(
                            f"TIMING interruption_detected — speaker={abort_speaker} "
                            f"original=\"{prompt[:40]}\" updated=\"{updated_text[:40]}\""
                        )
                        # Stream-classify the updated text to decide
                        interrupt_result = self._stream_classify_interruption(updated_text)
                        if interrupt_result == "pass":
                            # Updated text is not for operator — play original response
                            log.info("TIMING interruption_classified=PASS — playing original response")
                        else:
                            # Updated text IS for operator — play interruption filler,
                            # re-process with the full updated text
                            log.info("TIMING interruption_classified=RESPOND — re-processing")
                            self._play_interruption_filler()
                            # Reset echo guard, re-process
                            self.captions.is_speaking = False
                            self.captions._abort_speaker = None
                            self.captions._tts_text = ""
                            self._finalize_prompt(
                                updated_text, caption_mode=True, stream_classify=True,
                            )
                            return "responded"

            # --- Play response (interruptible in caption mode) ---
            # Clear abort_event before playback — any pre-playback events were
            # handled above. Only interruptions DURING playback should stop it.
            if caption_mode:
                self.captions.abort_event.clear()
            self._latency_probe.set_active(False)
            t_play = time.time()
            log.info(f"TIMING response_play_start gap_since_filler_done={t_play - t_ready_to_play:.3f}s")
            if caption_mode:
                # Gate interruptions through classification — don't kill
                # playback for hallucinated captions or background noise.
                confirmed_interrupt = threading.Event()
                playback_done = threading.Event()

                def _classify_playback_interrupt():
                    """Watch for abort_event, classify, set confirmed_interrupt if real."""
                    # Poll both events — exit if playback finishes or abort fires
                    while not playback_done.is_set() and not self.captions.abort_event.is_set():
                        self.captions.abort_event.wait(timeout=0.05)
                    if playback_done.is_set():
                        return  # playback finished normally, nothing to classify
                    with self.captions._lock:
                        interrupt_text = self.captions._current_text.strip()
                        interrupt_speaker = self.captions._abort_speaker
                    if not interrupt_text:
                        log.info("TIMING playback_interrupt_empty — no text, confirming interrupt")
                        confirmed_interrupt.set()
                        return
                    log.info(
                        f"TIMING playback_interrupt_classifying "
                        f"speaker={interrupt_speaker} text=\"{interrupt_text[:60]}\""
                    )
                    result = self._stream_classify_playback_interrupt(
                        interrupt_text, reply
                    )
                    if result == "interrupt":
                        log.info("TIMING playback_interrupt_confirmed — stopping playback")
                        confirmed_interrupt.set()
                    else:
                        log.info("TIMING playback_interrupt_dismissed — continuing playback")

                interrupt_classifier = threading.Thread(
                    target=_classify_playback_interrupt, daemon=True
                )
                interrupt_classifier.start()

                completed = self.tts.play_audio(
                    wav_result[0], interrupt_event=confirmed_interrupt,
                )
                playback_done.set()  # signal classifier thread to exit
                if not completed:
                    log.info("TIMING response_interrupted — user talked over playback")
            else:
                self.tts.play_audio(wav_result[0])
                completed = True
            response_played = True
            t_done = time.time()
            log.info(f"TIMING response_play_done elapsed={t_done - t_play:.3f}s")
            log.info(
                f"TIMING end_to_end — "
                f"llm_wait: {t_synth_start - t_finalized:.3f}s | "
                f"synthesis: {synth_elapsed:.3f}s | "
                f"filler_wait: {filler_wait_elapsed:.3f}s | "
                f"speak: {t_done - t_play:.3f}s | "
                f"total_from_finalized: {t_done - t_finalized:.3f}s"
            )

        except Exception as e:
            log.error(f"Pipeline error: {e}", exc_info=True)
        finally:
            # Wait for filler to finish before resuming captions — its audio
            # plays through BlackHole and would create "You" echo captions.
            filler_done.wait()
            if response_played:
                time.sleep(config.ECHO_GUARD_SECONDS)
            self._latency_probe.set_active(True)
            if caption_mode:
                self.captions.is_speaking = False
                self.captions._abort_speaker = None
                self.captions._tts_text = ""
                log.info("Echo prevention: resumed caption processing")
            else:
                self.audio.drain_audio_buffer()
                self.audio.is_speaking = False
                log.info("Echo prevention: resumed audio ingestion")

        self.conv.set_idle()
        if not stream_classify:
            return True
        return "exit" if is_exit else "responded"

    def _play_acknowledgment(self):
        """Play a random acknowledgment clip through the TTS output device."""
        if not self._tts_ready.is_set():
            log.info("TIMING waiting for TTS init...")
            self._tts_ready.wait()
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

    def _stream_classify_interruption(self, updated_text):
        """Quick stream-classify to check if the updated text is for operator.

        Returns "pass" or "respond".
        """
        with self._transcript_lock:
            context = "\n".join(self._transcript_lines[-20:-1]) if len(self._transcript_lines) > 1 else ""

        last_exchange = ""
        if self._last_utterance and self._last_reply:
            last_exchange = (
                f"[Your last exchange]\nThey asked: {self._last_utterance}\n"
                f"You answered: {self._last_reply}\n\n"
            )
        full_prompt = (
            f"[Meeting transcript so far]\n{context}\n\n"
            f"{last_exchange}"
            f"[Someone just said]\n{updated_text}\n\n"
            f"[Instruction] You are in a live meeting. The speaker continued talking "
            f"after you started processing their earlier utterance.\n"
            f"If this is NOT directed at you, respond with only PASS.\n"
            f"If this IS directed at you, respond normally."
        )

        try:
            stream = self.llm.ask_stream(full_prompt)
            first_token = ""
            for token in stream:
                first_token += token
                if first_token.strip():
                    break
            # Drain
            for _ in stream:
                pass

            if first_token.strip().upper().startswith("PASS"):
                return "pass"
            return "respond"
        except Exception as e:
            log.error(f"Interruption classify error: {e}", exc_info=True)
            return "respond"  # default to re-process on error

    def _stream_classify_playback_interrupt(self, interrupt_text, bot_reply):
        """Classify whether speech during playback is a real interruption.

        Returns "interrupt" or "pass".
        """
        with self._transcript_lock:
            context = "\n".join(self._transcript_lines[-20:-1]) if len(self._transcript_lines) > 1 else ""

        last_exchange = ""
        if self._last_utterance:
            last_exchange = (
                f"[Current exchange]\nThey asked: {self._last_utterance}\n"
                f"You are currently responding: {bot_reply}\n\n"
            )
        full_prompt = (
            f"[Meeting transcript so far]\n{context}\n\n"
            f"{last_exchange}"
            f"[Caption detected during your response]\n\"{interrupt_text}\"\n\n"
            f"[Instruction] You are in a live meeting and currently speaking your "
            f"response aloud. A caption was detected from another participant while "
            f"you were talking.\n"
            f"This could be:\n"
            f"- A real interruption (someone deliberately cutting you off or saying "
            f"\"stop\", \"never mind\", \"actually\", etc.)\n"
            f"- Background noise or ambient speech picked up by their microphone\n"
            f"- A caption hallucination from the speech recognition system (random "
            f"short words like \"What?\", \"Yeah.\", \"Oh.\" that nobody actually said)\n\n"
            f"If this looks like a REAL deliberate interruption, respond with only "
            f"INTERRUPT.\n"
            f"If this is likely noise, hallucination, or not directed at you, respond "
            f"with only PASS."
        )

        try:
            log.info("TIMING playback_interrupt_classify_start")
            stream = self.llm.ask_stream(full_prompt)
            first_token = ""
            for token in stream:
                first_token += token
                if first_token.strip():
                    break
            # Drain
            for _ in stream:
                pass

            token_val = first_token.strip().upper()
            log.info(f"TIMING playback_interrupt_classify_done token=\"{token_val}\"")
            if token_val.startswith("INTERRUPT"):
                return "interrupt"
            return "pass"
        except Exception as e:
            log.error(f"Playback interrupt classify error: {e}", exc_info=True)
            return "interrupt"  # default to interrupt on error (safe side)

    def _play_interruption_filler(self):
        """Play an interruption-acknowledgment filler clip."""
        clips = fillers.get_clips("interruption")
        if clips:
            clip = clips[0]
            log.info(f"TIMING interruption_filler clip={os.path.basename(clip)}")
            self.tts.play_clip(clip)
        else:
            log.info("Interruption filler: no clips available, skipping")

    # ------------------------------------------------------------------
    # Default state change handler
    # ------------------------------------------------------------------

    @staticmethod
    def _log_state_change(state, label):
        log.info(f"State → {state} ({label})")
