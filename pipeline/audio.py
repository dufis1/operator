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
import config

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

# MLX model repo mapping
_MLX_REPOS = {
    "tiny":  "mlx-community/whisper-tiny-mlx",
    "base":  "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
}


class AudioProcessor:
    """Manages the audio buffer, silence detection, utterance capture, and Whisper STT."""

    def __init__(self):
        self._stt_provider = config.STT_PROVIDER
        log.info(f"STARTUP STT provider={self._stt_provider} model={config.STT_MODEL}")
        if self._stt_provider == "mlx":
            import mlx_whisper
            self._mlx_whisper = mlx_whisper
            self._mlx_repo = _MLX_REPOS.get(config.STT_MODEL, _MLX_REPOS["base"])
            # Warm up: first call downloads + compiles the model
            mlx_whisper.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), path_or_hf_repo=self._mlx_repo, language="en")
        else:
            from faster_whisper import WhisperModel
            self.whisper = WhisperModel(config.STT_MODEL, device=config.STT_DEVICE, compute_type=config.STT_COMPUTE_TYPE)
        log.info("STARTUP Whisper model loaded")
        self._audio_buffer = b""
        self._audio_lock = threading.Lock()
        self.capturing = False
        self.is_speaking = False  # Set True by TTS layer to prevent echo
        # Debug: dump all captured audio (including echo) to WAV
        self._debug_dump = os.environ.get("OPERATOR_DUMP_AUDIO") == "1"
        self._debug_wav = None
        if self._debug_dump:
            dump_path = "/tmp/operator_audio_dump.wav"
            self._debug_wav = sf.SoundFile(dump_path, mode="w", samplerate=SAMPLE_RATE, channels=1, subtype="FLOAT")
            log.info(f"DEBUG audio dump enabled → {dump_path}")

    def feed_audio(self, chunk):
        """Add raw PCM bytes to the buffer. Called by the connector's read loop."""
        with self._audio_lock:
            # Debug: write ALL audio (including echo) under lock
            if self._debug_wav is not None:
                samples = np.frombuffer(chunk, dtype=np.float32)
                self._debug_wav.write(samples)
            if not self.is_speaking:
                self._audio_buffer += chunk

    def drain_audio_buffer(self):
        """Drain and return all accumulated audio bytes, resetting the buffer."""
        with self._audio_lock:
            data = self._audio_buffer
            self._audio_buffer = b""
        return data

    def capture_next_utterance(self, is_prompt=False, no_speech_timeout=None, on_first_silence=None):
        """Block until a complete utterance is detected. Returns transcribed text or ''.

        no_speech_timeout:  give up and return '' if no speech starts within N seconds.
                            Used for conversation follow-up mode.
        on_first_silence:   optional callable(audio_bytes: bytes) fired once when the
                            first silence chunk is detected.  Used to kick off speculative
                            Whisper + LLM processing while we wait for the second chunk
                            to confirm end-of-speech.
        """
        # Conversation follow-ups (no_speech_timeout set) may re-enter after
        # a timeout with stale audio in the buffer — drain it so we don't
        # Whisper old data.  Other call sites need the buffered audio intact.
        if no_speech_timeout:
            self.drain_audio_buffer()

        speech_detected = False
        silence_count = 0
        first_silence_fired = False
        utterance_audio = b""
        speech_start_time = None
        silence_start_time = None
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
                    silence_start_time = None
                    utterance_audio += raw
                elif speech_detected:
                    utterance_audio += raw
                    silence_count += 1
                    if silence_count == 1:
                        silence_start_time = time.time()
                        log.info(f"TIMING {label}_silence_detected rms={rms:.4f}")
            elif speech_detected:
                silence_count += 1
                if silence_count == 1:
                    silence_start_time = time.time()
                    log.info(f"TIMING {label}_silence_detected (no audio)")

            # Fire speculative callback on the first silence chunk
            if (speech_detected
                    and silence_count == 1
                    and not first_silence_fired
                    and on_first_silence):
                first_silence_fired = True
                on_first_silence(bytes(utterance_audio))
                log.debug("Speculative: on_first_silence fired")

            if speech_detected:
                if silence_count >= UTTERANCE_SILENCE_THRESHOLD:
                    now = time.time()
                    speech_dur = silence_start_time - speech_start_time
                    silence_dur = now - silence_start_time
                    log.info(f"TIMING {label}_utterance_done speech={speech_dur:.2f}s silence={silence_dur:.2f}s")
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
        if self._is_repetition_hallucination(text):
            log.info(f"TIMING {label}_whisper_rejected_repetition")
            return ""
        return text

    @staticmethod
    def _is_repetition_hallucination(text):
        """Detect Whisper hallucinations that repeat a word/phrase many times."""
        words = text.lower().split()
        if len(words) <= 10:
            return False
        from collections import Counter
        # Check unigrams
        counts = Counter(words)
        if counts.most_common(1)[0][1] / len(words) > 0.5:
            return True
        # Check bigrams (catches "I know I know I know...")
        bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words) - 1)]
        if bigrams:
            bcounts = Counter(bigrams)
            if bcounts.most_common(1)[0][1] / len(bigrams) > 0.5:
                return True
        return False

    def transcribe(self, audio):
        """Transcribe a numpy float32 audio array. Returns text string.

        Prepends 0.5s of silence — Whisper drops the first word without it.
        """
        silence_pad = np.zeros(int(SAMPLE_RATE * 0.5), dtype=np.float32)
        audio = np.concatenate([silence_pad, audio])

        if self._stt_provider == "mlx":
            result = self._mlx_whisper.transcribe(
                audio, path_or_hf_repo=self._mlx_repo, language="en",
            )
            return result["text"].strip()

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
