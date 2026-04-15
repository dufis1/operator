"""
Caption finalizer — turns streaming caption deltas into finalized utterances
appended to the MeetingRecord.

A single "utterance" is one continuous span of speech from one speaker. The
caption observer streams partial updates (~3/sec during speech); we buffer the
latest text and flush it when either:

  1. the speaker changes, or
  2. no caption update has arrived for SILENCE_SECONDS.

Only the final text for each utterance is written — intermediate deltas never
hit disk. This matches chat messages (one record per finalized message) and
keeps MeetingRecord.tail(n) readable for the LLM.
"""
import logging
import threading
import time

log = logging.getLogger(__name__)

_POLL_INTERVAL = 0.1  # silence checker tick


class TranscriptFinalizer:
    """Buffers caption updates and flushes finalized utterances to a MeetingRecord.

    Lifecycle:
        tf = TranscriptFinalizer(record, silence_seconds=0.7)
        connector.set_caption_callback(tf.on_caption_update)
        # ... meeting runs ...
        tf.stop()   # flushes any pending utterance
    """

    def __init__(self, record, silence_seconds: float = 0.7):
        self._record = record
        self._silence_seconds = silence_seconds
        self._lock = threading.Lock()

        self._current_speaker: str | None = None
        self._current_text: str = ""
        self._last_update_time: float = 0.0

        self._stop = threading.Event()
        self._silence_thread = threading.Thread(
            target=self._silence_loop, daemon=True, name="TranscriptFinalizer-silence"
        )
        self._silence_thread.start()

    # ── Caption callback (browser thread) ─────────────────────────────

    def on_caption_update(self, speaker: str, text: str, timestamp: float) -> None:
        """Called by the connector on every caption DOM update."""
        to_finalize: tuple[str, str, float] | None = None
        with self._lock:
            # Speaker change → flush whatever the previous speaker had
            if self._current_speaker and speaker != self._current_speaker and self._current_text:
                to_finalize = (
                    self._current_speaker,
                    self._current_text,
                    self._last_update_time,
                )
            self._current_speaker = speaker
            self._current_text = text
            self._last_update_time = timestamp

        if to_finalize:
            self._emit(*to_finalize, reason="speaker_change")

    # ── Silence detection (background thread) ─────────────────────────

    def _silence_loop(self) -> None:
        while not self._stop.wait(_POLL_INTERVAL):
            to_finalize: tuple[str, str, float] | None = None
            with self._lock:
                if (
                    self._current_speaker
                    and self._current_text
                    and time.time() - self._last_update_time >= self._silence_seconds
                ):
                    to_finalize = (
                        self._current_speaker,
                        self._current_text,
                        self._last_update_time,
                    )
                    self._current_speaker = None
                    self._current_text = ""
            if to_finalize:
                self._emit(*to_finalize, reason="silence")

    # ── Finalization ──────────────────────────────────────────────────

    def _emit(self, speaker: str, text: str, timestamp: float, reason: str) -> None:
        text = text.strip()
        if not text:
            return
        log.info(f"caption_finalized reason={reason} speaker={speaker} text=\"{text[:80]}\"")
        try:
            self._record.append(speaker, text, kind="caption", timestamp=timestamp)
        except Exception as e:
            log.warning(f"TranscriptFinalizer: record.append failed: {e}")

    def stop(self) -> None:
        """Flush any pending utterance and stop the silence thread."""
        self._stop.set()
        to_finalize: tuple[str, str, float] | None = None
        with self._lock:
            if self._current_speaker and self._current_text:
                to_finalize = (
                    self._current_speaker,
                    self._current_text,
                    self._last_update_time,
                )
                self._current_speaker = None
                self._current_text = ""
        if to_finalize:
            self._emit(*to_finalize, reason="stop")
        self._silence_thread.join(timeout=1.0)
