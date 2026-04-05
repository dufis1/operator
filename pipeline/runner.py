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


class _SpeculativeResult:
    """Holds the in-flight result of a speculative Whisper + LLM call."""
    def __init__(self):
        self.transcript = None        # Whisper result on the first-silence snapshot
        self.full_prompt = None       # Context-augmented prompt sent to LLM
        self.llm_reply = None         # LLM reply (None if not yet done or failed)
        self.synth_bytes = None       # Pre-rendered TTS audio (bytes), if available
        self.for_assistant = None     # classifier result (True/False/None=not run)
        self.was_soft_pass = False    # True when this cycle follows a soft PASS
        self.llm_done = threading.Event()  # set when LLM reply + classification available
        self.ready = threading.Event()


ACK_CLIPS = [
    os.path.join(_BASE, "assets", "ack_yeah.mp3"),
    os.path.join(_BASE, "assets", "ack_yes.mp3"),
    os.path.join(_BASE, "assets", "ack_mmhm.mp3"),
]

log = logging.getLogger(__name__)


def _normalize_for_match(text: str) -> str:
    """Normalize caption text for speculative matching.

    Google's ASR rewrites captions between updates — changing case,
    substituting symbols (e.g. 'plus' → '+'), and tweaking punctuation.
    This normalizes both sides so speculative hits aren't lost to cosmetic diffs.
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
            if not self._finalize_prompt(prompt, speculative=spec, caption_mode=True):
                log.info("TIMING abort — entering conversation mode to capture full utterance")
                # Fall through to conversation mode (no wake required) so we
                # pick up the rest of what the user was saying.

            # Conversation mode: accept follow-ups without re-triggering wake phrase.
            # Two-strike PASS system: first PASS is "soft" (stay listening),
            # only a second consecutive PASS exits conversation mode.
            log.info("Entering conversation mode")
            soft_pass_active = False
            while self.captions.capturing and not self._stop_event.is_set():
                self.conv.set_listening("Listening...")
                spec = _SpeculativeResult()
                spec.was_soft_pass = soft_pass_active
                followup_speaker, followup = self.captions.capture_next_wake_utterance(
                    require_wake=False,
                    no_speech_timeout=CONVERSATION_TIMEOUT,
                    on_speculative=self._make_caption_speculative_callback(spec, run_classifier=True),
                )
                if not followup:
                    log.info("Conversation mode: capture ended — returning to idle")
                    break
                # Wait for classifier verdict (lives inside speculative thread).
                # Only need LLM, not TTS — TTS is used opportunistically later.
                # Timeout handles the case where utterance finalized (e.g. via
                # speaker-change) before the speculative threshold was reached.
                if not spec.llm_done.wait(timeout=3.0):
                    log.info("Conversation mode: speculative never fired — treating as RESPOND")

                # --- RESPOND path (happy path) ---
                if spec.for_assistant is not False:
                    soft_pass_active = False
                    self._finalize_prompt(followup, speculative=spec, caption_mode=True)
                    continue

                # --- PASS path: two-strike logic ---
                # If finalized text grew beyond speculative snapshot, re-classify on full text
                spec_words = len(spec.transcript.split()) if spec.transcript else 0
                final_words = len(followup.split())
                word_delta = final_words - spec_words

                if word_delta > 2:
                    log.info(
                        f"Conversation mode: soft PASS re-classify "
                        f"(spec={spec_words} final={final_words} delta={word_delta})"
                    )
                    if self._reclassify_full_text(followup):
                        log.info("Conversation mode: re-classify flipped PASS→RESPOND")
                        soft_pass_active = False
                        self._finalize_prompt(followup, speculative=None, caption_mode=True)
                        continue

                # Second strike → hard exit
                if soft_pass_active:
                    log.info("Conversation mode: second PASS — returning to idle")
                    break

                # First strike → soft PASS, stay listening.
                # Check if caption text grew after finalization (e.g. "How about?"
                # finalized, then "How about France?" arrived 98ms later).
                # If so, re-classify with the full text before committing to PASS.
                with self.captions._lock:
                    latest_text = self.captions._current_text.strip()
                if latest_text and not _normalize_for_match(latest_text).endswith(_normalize_for_match(followup)):
                    log.info(
                        f"Conversation mode: soft PASS but caption text grew — "
                        f"finalized=\"{followup[:40]}\" current=\"{latest_text[:40]}\""
                    )
                    if self._reclassify_full_text(latest_text):
                        log.info("Conversation mode: extended text flipped PASS→RESPOND")
                        soft_pass_active = False
                        self._finalize_prompt(latest_text, speculative=None, caption_mode=True)
                        continue

                log.info("Conversation mode: soft PASS — staying in conversation mode")
                soft_pass_active = True
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
                # Build last-exchange context from tracked utterance/reply
                last_exchange = ""
                if self._last_utterance and self._last_reply:
                    last_exchange = f"[Your last exchange]\nThey asked: {self._last_utterance}\nYou answered: {self._last_reply}\n\n"

                # If this follows a soft PASS, tell the classifier about the prior decision
                soft_pass_note = ""
                if spec.was_soft_pass:
                    soft_pass_note = (
                        "[Context] You previously concluded the conversation was over "
                        "and decided to PASS. Now someone has spoken again. "
                        "Re-evaluate carefully: is this new speech directed at you?\n\n"
                    )

                spec.full_prompt = (
                    f"[Meeting transcript so far]\n{context}\n\n"
                    f"{last_exchange}"
                    f"{soft_pass_note}"
                    f"[Someone just said]\n{prompt_text}\n\n"
                    f"[Instruction] You are in a live meeting with multiple participants. "
                    f"You just answered a question. Decide: is this new utterance a follow-up "
                    f"directed at you, or has the speaker moved on — e.g. addressing another "
                    f"participant, changing the subject, or continuing the meeting without you? "
                    f"If it is for you, respond normally. "
                    f"If it is NOT for you, respond with only the word PASS."
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
            spec.llm_done.set()

            # Speculative TTS: synthesize audio while waiting for finalization.
            # If the prompt doesn't change, _finalize_prompt skips synthesis entirely.
            reply = spec.llm_reply
            if reply and (not run_classifier or spec.for_assistant):
                reply_clean = sanitize_for_speech(reply)
                if self._tts_ready.is_set() and self.tts:
                    try:
                        log.info("TIMING caption_speculative_tts_start")
                        spec.synth_bytes = self.tts.synthesize(reply_clean)
                        log.info(f"TIMING caption_speculative_tts_done bytes={len(spec.synth_bytes)}")
                    except Exception as e:
                        log.warning(f"Speculative TTS failed (will retry in finalize): {e}")
        except Exception as e:
            log.error(f"Speculative caption LLM error: {e}", exc_info=True)
        finally:
            spec.llm_done.set()  # ensure runner never deadlocks
            spec.ready.set()

    def _reclassify_full_text(self, full_text: str) -> bool:
        """Re-classify finalized text after speculative PASS on partial.

        Returns True if directed at assistant, False if PASS.
        """
        with self._transcript_lock:
            context = "\n".join(self._transcript_lines[-20:-1]) if len(self._transcript_lines) > 1 else ""

        last_exchange = ""
        if self._last_utterance and self._last_reply:
            last_exchange = (
                f"[Your last exchange]\nThey asked: {self._last_utterance}\n"
                f"You answered: {self._last_reply}\n\n"
            )

        prompt = (
            f"[Meeting transcript so far]\n{context}\n\n"
            f"{last_exchange}"
            f"[Someone just said]\n{full_text}\n\n"
            f"[Instruction] You are in a live meeting. Decide: is this utterance a follow-up "
            f"directed at you or has the speaker moved on? "
            f"Respond with YES if for you, PASS if not."
        )
        try:
            reply = self.llm.ask(prompt, record=False)
            result = not reply.strip().upper().startswith("PASS")
            log.info(f"TIMING reclassify_full_text result={result} reply=\"{reply[:60]}\"")
            return result
        except Exception as e:
            log.error(f"Re-classify LLM error: {e}", exc_info=True)
            return False  # default to PASS on error

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

    def _finalize_prompt(self, prompt, speculative=None, caption_mode=False, allow_abort=True):
        """Resolve the LLM reply and speak it, playing fillers while synthesis runs.

        Returns True if the response was played, False if aborted.
        """
        if not prompt:
            self.conv.set_idle()
            return False

        # Ensure TTS background init has finished before we need to synthesize
        if not self._tts_ready.is_set():
            log.info("TIMING waiting for TTS init...")
            self._tts_ready.wait()

        log.info(f"TIMING prompt_finalized \"{prompt}\"")
        self.conv.set_thinking()

        # Echo guard: pause ingestion so the bot's own speech doesn't re-trigger
        if caption_mode:
            self.captions.abort_event.clear()
            self.captions._filler_done_at = float('inf')  # grace active until filler finishes
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

        response_played = False
        filler_done = threading.Event()
        try:
            t_finalized = time.time()

            # --- Step 3: Start filler immediately, concurrent with LLM wait ---
            # Skip filler when: (a) abort retry — user already heard one, or
            # (b) speculative LLM+TTS already finished — nothing to wait for.
            prompt_norm = _normalize_for_match(prompt)
            spec_ready = (speculative
                          and speculative.ready.is_set()
                          and _normalize_for_match(speculative.transcript or "") == prompt_norm
                          and speculative.llm_reply
                          and speculative.synth_bytes)
            # Also skip filler when LLM is done and TTS is in-flight — it'll
            # finish by the time we reach Step 2 (we wait for it there).
            spec_tts_inflight = (not spec_ready
                                 and speculative
                                 and speculative.llm_done.is_set()
                                 and speculative.llm_reply
                                 and _normalize_for_match(speculative.transcript or "") == prompt_norm)
            skip_filler = not allow_abort or spec_ready or spec_tts_inflight
            if spec_ready:
                log.info("TIMING filler_skipped — speculative LLM+TTS already complete")
            elif spec_tts_inflight:
                log.info("TIMING filler_skipped — speculative LLM done, TTS in-flight")
            filler_bucket = fillers.classify(prompt)
            filler_clips = fillers.get_clips(filler_bucket) if not skip_filler else []
            if filler_clips:
                clip = filler_clips[0]
                log.info(f"TIMING filler_play_start clip={os.path.basename(clip)} bucket={filler_bucket}")
                self._latency_probe.set_active(False)
                def _play_filler():
                    self.tts.play_clip(clip)
                    if caption_mode:
                        self.captions._filler_done_at = time.monotonic()
                    log.info("TIMING filler_play_done")
                    filler_done.set()
                threading.Thread(target=_play_filler, daemon=True, name="filler").start()
            else:
                if allow_abort and not spec_ready:
                    log.info(f"Filler: no clips for bucket={filler_bucket}, skipping")
                if caption_mode:
                    self.captions._filler_done_at = time.monotonic()
                filler_done.set()

            # --- Step 1: Resolve LLM — speculative only, no duplicate call ---
            t0 = time.time()
            reply = None

            if (speculative
                    and speculative.llm_done.is_set()
                    and _normalize_for_match(speculative.transcript or "") == prompt_norm
                    and speculative.llm_reply):
                # Already done before we got here
                reply = speculative.llm_reply
                self.llm.record_exchange(speculative.full_prompt, reply)
                log.info(f"TIMING llm_speculative_hit waited=0.00s reply=\"{reply[:60]}\"")
            elif speculative and not speculative.llm_done.is_set():
                # In-flight — wait for LLM only, TTS checked separately in Step 2
                t_wait_start = time.time()
                speculative.llm_done.wait()
                t_waited = time.time() - t_wait_start
                if _normalize_for_match(speculative.transcript or "") == prompt_norm and speculative.llm_reply:
                    reply = speculative.llm_reply
                    self.llm.record_exchange(speculative.full_prompt, reply)
                    log.info(f"TIMING llm_speculative_hit waited={t_waited:.3f}s reply=\"{reply[:60]}\"")
                else:
                    reason = "transcript_mismatch" if _normalize_for_match(speculative.transcript or "") != prompt_norm else "no_reply"
                    log.info(f"TIMING llm_speculative_miss reason={reason} waited={t_waited:.3f}s")

            if reply is None:
                # No speculative, or speculative failed/mismatched — fresh call
                log.info("TIMING llm_request_sent")
                reply = self.llm.ask(full_prompt)
                log.info(f"TIMING llm_response_received elapsed={time.time() - t0:.3f}s reply=\"{reply[:60]}\"")

            t_llm_resolved = time.time()
            log.info(f"TIMING llm_resolved elapsed_from_finalized={t_llm_resolved - t_finalized:.3f}s")

            # --- Sanitize for TTS ---
            reply = sanitize_for_speech(reply)

            # Track for classifier context in conversation follow-up mode
            self._last_utterance = prompt
            self._last_reply = reply

            # --- Step 2: TTS synthesis (skip if speculative TTS already rendered) ---
            self.conv.set_speaking()
            t_synth_start = time.time()

            # If speculative LLM matched but TTS is still in-flight, wait for it
            # rather than starting a redundant fresh synthesis.
            if (speculative
                    and not speculative.synth_bytes
                    and speculative.llm_done.is_set()
                    and not speculative.ready.is_set()
                    and _normalize_for_match(speculative.transcript or "") == prompt_norm):
                speculative.ready.wait()

            if (speculative
                    and speculative.synth_bytes
                    and _normalize_for_match(speculative.transcript or "") == prompt_norm):
                wav_result = [speculative.synth_bytes]
                synth_elapsed = 0.0
                log.info(f"TIMING tts_speculative_hit bytes={len(wav_result[0])}")
            else:
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

            # --- Step 4: Wait for filler ---
            t_ready_to_play = time.time()
            filler_done.wait()  # usually already set; no delay if filler finished first
            filler_wait_elapsed = time.time() - t_ready_to_play
            log.info(f"TIMING filler_wait_done elapsed={filler_wait_elapsed:.3f}s")

            # --- Abort check: user kept talking after premature finalization ---
            # Two signals: (1) abort_event set by a non-"You" caption during
            # is_speaking, or (2) caption text grew beyond the finalized prompt
            # in the gap between finalization and is_speaking being set.
            if caption_mode and allow_abort:
                self.captions.abort_event.wait(timeout=0.15)
                abort = self.captions.abort_event.is_set()
                if not abort:
                    with self.captions._lock:
                        latest = self.captions._current_text.strip()
                    # Check if caption text grew with NEW content beyond the prompt.
                    # Use "ends with" to avoid false positives from the wake phrase
                    # prefix (prompt is post-wake, _current_text includes it).
                    latest_norm = _normalize_for_match(latest)
                    if latest_norm and not latest_norm.endswith(prompt_norm):
                        log.info(
                            f"TIMING abort_text_grew — finalized=\"{prompt[:40]}\" "
                            f"current=\"{latest[:40]}\""
                        )
                        abort = True
                if abort:
                    with self.captions._lock:
                        updated_prompt = self.captions._current_text.strip()
                    log.info(f"TIMING abort_triggered — re-processing with \"{updated_prompt[:60]}\"")
                    # Re-process with allow_abort=False to prevent infinite
                    # loops from filler echo being misattributed by Google Meet.
                    return self._finalize_prompt(updated_prompt, speculative=None,
                                                 caption_mode=True, allow_abort=False)

            # --- Step 5: Response plays ---
            self._latency_probe.set_active(False)
            t_play = time.time()
            log.info(f"TIMING response_play_start gap_since_filler_done={t_play - t_ready_to_play:.3f}s")
            self.tts.play_audio(wav_result[0])
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
                log.info("Echo prevention: resumed caption processing")
            else:
                self.audio.drain_audio_buffer()
                self.audio.is_speaking = False
                log.info("Echo prevention: resumed audio ingestion")

        self.conv.set_idle()
        return True

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
            spec.llm_done.set()
            spec.ready.set()

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

    # ------------------------------------------------------------------
    # Default state change handler
    # ------------------------------------------------------------------

    @staticmethod
    def _log_state_change(state, label):
        log.info(f"State → {state} ({label})")
