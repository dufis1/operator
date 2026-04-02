"""
Caption processing for Operator — wake detection and prompt capture from DOM text.

Text-side equivalent of AudioProcessor. Receives streaming caption updates from
CaptionsAdapter and produces finalized utterances for the runner.

No audio, no Whisper. Silence is detected by timing gaps between DOM updates
rather than RMS energy. Wake phrase detection happens in real-time on every
caption update (~330ms cadence during speech).
"""
import logging
import re
import threading
import time

import config

log = logging.getLogger(__name__)

# Compile wake phrase pattern tolerant of punctuation between words.
# "hey operator" → r'hey[,\s]+operator' — matches "hey operator", "hey, operator", etc.
_WAKE_RE = re.compile(
    r"[,\s]+".join(re.escape(w) for w in config.WAKE_PHRASE.split())
)

# Configurable thresholds (from config.yaml)
SPECULATIVE_SECONDS  = config.CAPTION_SPECULATIVE_SECONDS   # default 1.0
FINALIZATION_SECONDS = config.CAPTION_FINALIZATION_SECONDS   # default 1.5

# If ASR hasn't added terminal punctuation by this time, finalize anyway.
# Prevents indefinite hold for commands/fragments that ASR never punctuates.
FINALIZATION_HARD_TIMEOUT = FINALIZATION_SECONDS * 2.5  # default 3.75s

# How often the silence-detection loop checks for gaps
_POLL_INTERVAL = 0.1  # 100ms — fast enough to catch 1.0s speculative threshold

# Maximum prompt length (chars) to prevent indefinite accumulation
_MAX_PROMPT_CHARS = 2000


class CaptionProcessor:
    """Processes streaming caption text and produces finalized wake-triggered prompts.

    Lifecycle:
        1. CaptionsAdapter calls on_caption_update() on every DOM mutation.
        2. Runner calls capture_next_wake_utterance() which blocks until a wake
           phrase is detected and the speaker finishes talking.
        3. Returns (speaker, prompt) for the runner to send to the LLM.

    All caption text — including non-wake utterances — is also forwarded to
    an optional transcript callback so the runner can maintain meeting context.
    """

    def __init__(self):
        self._lock = threading.Lock()

        # Current caption state (updated by on_caption_update)
        self._current_speaker = None
        self._current_text = ""
        self._last_update_time = 0.0

        # Wake detection state
        self._wake_detected = False
        self._wake_position = -1       # char index where wake phrase ends
        self._speculative_fired = False

        # Signalling between caption updates and the blocking capture call
        self._wake_event = threading.Event()        # set when wake phrase first found
        self._finalized_event = threading.Event()    # set when prompt is finalized
        self._cancel_event = threading.Event()       # set to abort current capture

        # Results passed from the update callback to the blocking capture call
        self._result_speaker = None
        self._result_prompt = None

        # Echo guard: pause processing while bot is speaking
        self.is_speaking = False

        # Optional callback for ALL caption text (for transcript context)
        self._transcript_callback = None

        # Active flag (mirrors audio.capturing)
        self.capturing = False

        # Set per capture cycle; False → follow-up mode (no wake required)
        self._require_wake = True

    def set_transcript_callback(self, fn):
        """Register fn(speaker, text) called on every caption update.

        Used by the runner to feed all meeting speech into the transcript
        rolling window — not just wake-triggered prompts.
        """
        self._transcript_callback = fn

    # ── Called by CaptionsAdapter on every DOM update ────────────────

    def on_caption_update(self, speaker, text, timestamp):
        """Process a caption update from the browser.

        Called from the Playwright browser thread on every MutationObserver
        firing (~3/sec during speech). Must be fast — no blocking.
        """
        if self.is_speaking:
            return
        if speaker == "You":
            return  # Operator's own TTS audio reflected back — ignore

        with self._lock:
            # Feed transcript callback (all speech, not just wake-triggered)
            if self._transcript_callback:
                try:
                    self._transcript_callback(speaker, text)
                except Exception:
                    pass

            # Speaker change: if someone else starts talking, finalize
            # whatever the previous speaker was saying.
            if self._current_speaker and speaker != self._current_speaker:
                log.info(f"caption: speaker change {self._current_speaker} -> {speaker}")
                if self._wake_detected:
                    self._do_finalize("speaker_change")
                elif not self._require_wake and self._current_text.strip():
                    self._do_finalize("speaker_change", prompt_override=self._current_text.strip())

            self._current_speaker = speaker
            self._current_text = text
            self._last_update_time = timestamp

            # Real-time wake detection on every update (skipped in follow-up mode)
            if self._require_wake:
                text_lower = text.lower()
                if not self._wake_detected:
                    m = _WAKE_RE.search(text_lower)
                    if m:
                        self._wake_position = m.end()
                        self._wake_detected = True
                        prompt_so_far = text[self._wake_position:].strip().strip(",.:?!")
                        log.info(
                            f"TIMING caption_wake_detected speaker={speaker} "
                            f"prompt_so_far=\"{prompt_so_far[:60]}\""
                        )
                        self._wake_event.set()
                else:
                    # Wake was detected — check if ASR correction removed it
                    if not _WAKE_RE.search(text_lower):
                        log.info("TIMING caption_wake_retracted (ASR correction removed wake phrase)")
                        self._wake_detected = False
                        self._wake_position = -1
                        self._speculative_fired = False
                        self._wake_event.clear()

    # ── Blocking API for the runner ─────────────────────────────────

    def capture_next_wake_utterance(self, no_speech_timeout=None, on_speculative=None, require_wake=True):
        """Block until wake phrase detected (or first caption in follow-up mode) + silence confirmed.

        Args:
            no_speech_timeout: seconds to wait for ANY caption activity before
                               giving up (used for conversation follow-up mode).
            on_speculative:    callback(prompt_text: str) fired at SPECULATIVE_SECONDS
                               of silence after wake detection. Same role as
                               AudioProcessor's on_first_silence.
            require_wake:      if False, skip wake phrase detection and treat the
                               first caption update as the start of a prompt. Used
                               for conversation follow-up so participants don't need
                               to repeat "hey operator".

        Returns:
            (speaker: str, prompt: str) — the speaker who triggered and text after "operator"
                                          (or full caption text in follow-up mode).
            ("", "") if timed out or cancelled.
        """
        # Reset state for this capture cycle
        self._reset_state()
        self._require_wake = require_wake
        capture_start = time.time()
        log.info(f"TIMING caption_capture_start (timeout={no_speech_timeout} require_wake={require_wake})")

        # Phase 1: Wait for wake phrase (or first caption update in follow-up mode)
        while self.capturing and not self._cancel_event.is_set():
            if self._wake_event.wait(timeout=_POLL_INTERVAL):
                break

            # Follow-up mode: any caption update is enough to proceed
            if not require_wake:
                with self._lock:
                    if self._last_update_time > capture_start:
                        break

            # Check no_speech_timeout: if no caption updates at all, bail
            if no_speech_timeout:
                with self._lock:
                    last = self._last_update_time
                # No updates since capture started
                if last <= capture_start and time.time() - capture_start > no_speech_timeout:
                    log.info(f"TIMING caption_timeout (no captions in {no_speech_timeout:.0f}s)")
                    return ("", "")
                # Updates stopped (person stopped talking)
                if last > capture_start and time.time() - last > no_speech_timeout:
                    log.info(f"TIMING caption_timeout (silence for {no_speech_timeout:.0f}s)")
                    return ("", "")

        if (require_wake and not self._wake_detected) or self._cancel_event.is_set():
            return ("", "")

        if require_wake:
            log.info("TIMING caption_wake_confirmed — entering silence detection")
        else:
            log.info("TIMING caption_followup_started — entering silence detection")

        # Phase 2: Silence detection — wait for speech to stop after wake (or first caption)
        while self.capturing and not self._cancel_event.is_set():
            if self._finalized_event.wait(timeout=_POLL_INTERVAL):
                break

            with self._lock:
                if require_wake and not self._wake_detected:
                    # Wake was retracted by ASR correction — go back to waiting
                    log.info("TIMING caption_wake_lost — returning to wake detection")
                    break

                gap = time.time() - self._last_update_time
                # In follow-up mode use full caption text; otherwise extract post-wake text
                if require_wake:
                    current_prompt = self._extract_prompt()
                    active = self._wake_detected and bool(current_prompt)
                else:
                    current_prompt = self._current_text.strip()
                    active = bool(current_prompt)

                # Speculative callback at SPECULATIVE_SECONDS
                if gap >= SPECULATIVE_SECONDS and not self._speculative_fired and active:
                    self._speculative_fired = True
                    log.info(
                        f"TIMING caption_speculative_fire gap={gap:.2f}s "
                        f"prompt=\"{current_prompt[:60]}\""
                    )
                    if on_speculative:
                        try:
                            on_speculative(current_prompt)
                        except Exception as e:
                            log.error(f"Speculative callback error: {e}")

                # Finalization at FINALIZATION_SECONDS — but require terminal punctuation
                # to avoid cutting off mid-sentence when ASR emits words in slow batches.
                # Hard timeout overrides if ASR never adds punctuation (e.g. bare commands).
                if gap >= FINALIZATION_SECONDS and active:
                    # Check raw caption text (not extracted prompt — that has punctuation stripped)
                    raw_tail = self._current_text.rstrip()
                    prompt_complete = bool(raw_tail) and raw_tail[-1] in '.?!'
                    if prompt_complete or gap >= FINALIZATION_HARD_TIMEOUT:
                        self._do_finalize("silence", prompt_override=None if require_wake else current_prompt)

            # If wake was retracted, restart the wake wait (only in wake-required mode)
            if require_wake and not self._wake_detected and not self._finalized_event.is_set():
                return self.capture_next_wake_utterance(
                    no_speech_timeout=no_speech_timeout,
                    on_speculative=on_speculative,
                    require_wake=require_wake,
                )

        # Return result
        with self._lock:
            speaker = self._result_speaker or ""
            prompt = self._result_prompt or ""

        if prompt:
            log.info(f"TIMING caption_prompt_finalized speaker={speaker} prompt=\"{prompt[:80]}\"")
        return (speaker, prompt)

    def stop(self):
        """Signal the capture loop to exit."""
        self.capturing = False
        self._cancel_event.set()
        self._wake_event.set()
        self._finalized_event.set()

    # ── Internal helpers ─────────────────────────────────────────────

    def _reset_state(self):
        """Clear all state for a new capture cycle."""
        with self._lock:
            self._wake_detected = False
            self._wake_position = -1
            self._speculative_fired = False
            self._result_speaker = None
            self._result_prompt = None
            # Don't clear _current_speaker/_current_text/_last_update_time —
            # they represent live caption state that keeps updating.
        self._wake_event.clear()
        self._finalized_event.clear()
        self._cancel_event.clear()

    def _extract_prompt(self):
        """Extract the prompt text (everything after the wake phrase). Must hold _lock."""
        if not self._wake_detected or self._wake_position < 0:
            return ""
        prompt = self._current_text[self._wake_position:].strip().strip(",.:?!")
        if len(prompt) > _MAX_PROMPT_CHARS:
            prompt = prompt[:_MAX_PROMPT_CHARS]
            log.warning(f"caption: prompt truncated to {_MAX_PROMPT_CHARS} chars")
        return prompt

    def _do_finalize(self, reason, prompt_override=None):
        """Finalize the current prompt. Must hold _lock."""
        prompt = prompt_override if prompt_override is not None else self._extract_prompt()
        self._result_speaker = self._current_speaker
        self._result_prompt = prompt
        gap = time.time() - self._last_update_time
        log.info(
            f"TIMING caption_finalized reason={reason} gap={gap:.2f}s "
            f"speaker={self._current_speaker} prompt=\"{prompt[:80]}\""
        )
        self._finalized_event.set()
