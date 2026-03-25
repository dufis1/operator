"""
Audio processing for Operator — utterance detection, silence detection, Whisper STT.

No macOS-specific imports. The connector (macos_adapter / docker_adapter) feeds
raw PCM bytes into AudioProcessor.feed_audio(); this module handles everything
from there: silence detection, utterance finalization, and transcription.
"""
import os
import time
import tempfile
import threading
import logging
import numpy as np
import soundfile as sf
from faster_whisper import WhisperModel

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 4  # Float32
UTTERANCE_CHECK_INTERVAL = 0.5   # seconds between audio checks
UTTERANCE_SILENCE_THRESHOLD = 2  # consecutive silent checks = utterance done (~1s)
UTTERANCE_MAX_DURATION = 10      # hard cap: finalize utterance after 10s
UTTERANCE_SILENCE_RMS = 0.02     # RMS below this = silence (tune if needed)
# Whisper hallucinates these on silence — backstop filter
WHISPER_HALLUCINATIONS = {
    "you", "thank you", "thanks", "thanks a lot", "bye", "goodbye",
    "the end", "i'm sorry", "sorry",
}


class AudioProcessor:
    """Manages the audio buffer, silence detection, utterance capture, and Whisper STT."""

    def __init__(self):
        log.info("AudioProcessor: loading Whisper model...")
        self.whisper = WhisperModel("base", device="cpu", compute_type="int8")
        self._audio_buffer = b""
        self._audio_lock = threading.Lock()
        self.capturing = False
        self.is_speaking = False  # Set True by TTS layer to prevent echo

    def feed_audio(self, chunk):
        """Add raw PCM bytes to the buffer. Called by the connector's read loop."""
        if not self.is_speaking:
            with self._audio_lock:
                self._audio_buffer += chunk

    def drain_audio_buffer(self):
        """Drain and return all accumulated audio bytes, resetting the buffer."""
        with self._audio_lock:
            data = self._audio_buffer
            self._audio_buffer = b""
        return data

    def capture_next_utterance(self, is_prompt=False, no_speech_timeout=None):
        """Block until a complete utterance is detected. Returns transcribed text or ''.

        no_speech_timeout: if set, give up and return '' if no speech starts within
        this many seconds. Used for conversation follow-up mode.
        """
        speech_detected = False
        silence_count = 0
        utterance_audio = b""
        speech_start_time = None
        capture_start = time.time()
        label = "prompt" if is_prompt else "ambient"
        log.info(f"TIMING {label}_capture_start")

        while self.capturing:
            time.sleep(UTTERANCE_CHECK_INTERVAL)

            # If no speech has started yet and we've exceeded no_speech_timeout, bail out
            if no_speech_timeout and not speech_detected:
                if time.time() - capture_start > no_speech_timeout:
                    log.info(f"TIMING {label}_timeout (no speech in {no_speech_timeout:.0f}s)")
                    return ""

            raw = self.drain_audio_buffer()
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
                    break
                if time.time() - speech_start_time > UTTERANCE_MAX_DURATION:
                    log.info(f"TIMING {label}_utterance_done max_duration")
                    break

        if not utterance_audio:
            return ""

        audio = np.frombuffer(utterance_audio, dtype=np.float32)
        log.info(f"TIMING {label}_whisper_start")
        text = self.transcribe(audio)
        log.info(f"TIMING {label}_whisper_done \"{text}\"")
        return text

    def transcribe(self, audio):
        """Transcribe a numpy float32 audio array. Returns text string.

        Prepends 0.5s of silence — Whisper drops the first word without it.
        """
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
