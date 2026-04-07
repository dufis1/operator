"""
TTS integration for Operator.

Supports three provider tiers configured via tts.provider in config.yaml:

  local      — Kokoro neural TTS (free, requires Python 3.10–3.12), macOS say, or Piper.
               Select voice with tts.local_voice.
  openai     — gpt-4o-mini-tts via OpenAI streaming API.
  elevenlabs — ElevenLabs eleven_flash_v2_5 streaming API.

The output device is a constructor parameter — macOS callers pass BlackHole;
Docker/Linux callers pass a PulseAudio sink name.

No macOS-specific imports at module level.
"""
import io
import logging
import os
import platform
import subprocess
import sys
import tempfile
import time

import config

log = logging.getLogger(__name__)

# Maps config local_voice name → Kokoro voice ID
_KOKORO_VOICES = {
    "kokoro_heart":    "af_heart",
    "kokoro_sky":      "af_sky",
    "kokoro_emma":     "bf_emma",
    "kokoro_isabella": "bf_isabella",
}

# Maps config local_voice name → Piper model name
_PIPER_VOICES = {
    "piper_lessac": "en_US-lessac-high",
    "piper_amy":    "en_US-amy-medium",
}


class TTSClient:
    """Speaks text through the configured TTS provider.

    API clients and heavy models are initialised lazily on first use.

    Args:
        output_device: mpv --audio-device string.
                       macOS: "coreaudio/BlackHole2ch_UID"
                       Docker: PulseAudio sink name, e.g. "pulse/MeetingOutput"
    """

    def __init__(self, output_device):
        self._output_device = output_device
        self._openai = None
        self._eleven = None
        self._kokoro_pipeline = None
        self._kokoro_voice = None

        if config.TTS_PROVIDER == "local" and config.TTS_LOCAL_VOICE in _KOKORO_VOICES:
            self._init_kokoro()

    def _init_kokoro(self):
        try:
            import warnings
            t0 = time.monotonic()
            # Suppress noisy-but-harmless warnings from Kokoro's dependencies:
            #   - HF Hub: unauthenticated download notice (cosmetic)
            #   - PyTorch: LSTM dropout with num_layers=1 (no-op in model arch)
            #   - PyTorch: weight_norm deprecation (upstream library issue)
            # The unauthenticated-request warning is emitted by the child
            # logger huggingface_hub.utils._http, so suppress the whole tree.
            for _hf_name in ("huggingface_hub", "huggingface_hub.utils._http"):
                logging.getLogger(_hf_name).setLevel(logging.ERROR)
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message=".*dropout option adds dropout.*")
                warnings.filterwarnings("ignore", message=".*torch.nn.utils.weight_norm.*")
                from kokoro import KPipeline
                t_import = time.monotonic()
                log.info(f"TIMING tts_kokoro_import={t_import - t0:.1f}s")
                voice_id = _KOKORO_VOICES[config.TTS_LOCAL_VOICE]
                lang_code = "b" if voice_id.startswith("b") else "a"
                self._kokoro_pipeline = KPipeline(lang_code=lang_code, repo_id="hexgrad/Kokoro-82M")
                t_pipeline = time.monotonic()
                log.info(f"TIMING tts_kokoro_pipeline={t_pipeline - t_import:.1f}s")
            # Restore HF loggers (only cosmetic — these loggers aren't used after init)
            for _hf_name in ("huggingface_hub", "huggingface_hub.utils._http"):
                logging.getLogger(_hf_name).setLevel(logging.WARNING)
            self._kokoro_voice = voice_id
            log.info(f"STARTUP Kokoro TTS ready (voice={voice_id}) total={time.monotonic() - t0:.1f}s")
        except ImportError:
            log.warning(
                "Kokoro not installed (requires Python 3.10–3.12 and: pip install kokoro soundfile). "
                "Falling back to macos_say."
            )
            print("\n⚠️  Kokoro not installed — falling back to macOS say. To install:\n")
            print("   pip install kokoro soundfile\n")
            # Runtime fallback — mutate config so speak() routes correctly
            config.TTS_LOCAL_VOICE = "macos_say"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def synthesize(self, text) -> bytes:
        """Synthesize text and return raw audio bytes (WAV/MP3). Does not play.

        Use play_audio() to play the result, or pass it to a background thread
        while fillers play.
        """
        provider = config.TTS_PROVIDER
        if provider == "local":
            return self._synthesize_local(text)
        elif provider == "openai":
            return self._synthesize_openai(text)
        elif provider == "elevenlabs":
            return self._synthesize_elevenlabs(text)
        else:
            raise ValueError(f"Unknown TTS provider: {provider!r}")

    def play_audio(self, audio_bytes: bytes, interrupt_event=None):
        """Play raw audio bytes through the output device via mpv.

        Args:
            interrupt_event: optional threading.Event. If set during playback,
                             mpv is terminated immediately (playback interruption).

        Returns True if playback completed, False if interrupted or failed.
        """
        if not audio_bytes:
            log.warning("TTS play_audio: received empty audio bytes — nothing to play")
            return False
        log.info(f"TTS play_audio: {len(audio_bytes)} bytes → device={self._output_device}")
        if config.DEBUG_AUDIO:
            import datetime
            os.makedirs("debug", exist_ok=True)
            ts = datetime.datetime.now().strftime("%H%M%S_%f")[:9]
            debug_path = f"debug/tts_{ts}.wav"
            with open(debug_path, "wb") as f:
                f.write(audio_bytes)
            log.info(f"TTS debug: synthesis saved to {debug_path}")
        t_mpv_start = time.time()
        proc = subprocess.Popen(
            ["mpv", "--no-terminal", f"--audio-device={self._output_device}", "--", "-"],
            stdin=subprocess.PIPE,
        )
        t_mpv_spawned = time.time()
        log.info(f"TIMING mpv_spawned elapsed={t_mpv_spawned - t_mpv_start:.3f}s")
        proc.stdin.write(audio_bytes)
        proc.stdin.close()
        t_mpv_piped = time.time()
        log.info(f"TIMING mpv_audio_piped elapsed={t_mpv_piped - t_mpv_spawned:.3f}s")

        if interrupt_event:
            # Poll for interruption during playback
            while proc.poll() is None:
                if interrupt_event.is_set():
                    proc.terminate()
                    proc.wait(timeout=2)
                    log.info("TTS play_audio: interrupted by user speech")
                    return False
                time.sleep(0.05)
            rc = proc.returncode
        else:
            rc = proc.wait()

        if rc != 0:
            log.error(f"TTS play_audio: mpv exited with code {rc}")
            return False
        log.info("TTS play_audio: done")
        return True

    def speak(self, text):
        """Synthesize and immediately play. Convenience wrapper for simple callers."""
        t0 = time.time()
        audio = self.synthesize(text)
        self.play_audio(audio)
        log.info(f"TTS speak done ({time.time() - t0:.2f}s)")

    def play_clip(self, path):
        """Play a local audio file through the output device."""
        subprocess.run(
            ["mpv", "--no-terminal", f"--audio-device={self._output_device}", "--", path],
            check=False,
        )

    # ------------------------------------------------------------------
    # Local provider
    # ------------------------------------------------------------------

    def _synthesize_local(self, text) -> bytes:
        voice = config.TTS_LOCAL_VOICE
        if voice in _KOKORO_VOICES:
            return self._synthesize_kokoro(text)
        elif voice == "macos_say":
            return self._synthesize_macos_say(text)
        elif voice in _PIPER_VOICES:
            return self._synthesize_piper(text, voice)
        else:
            raise ValueError(f"Unknown local_voice: {voice!r}")

    def _synthesize_kokoro(self, text) -> bytes:
        import numpy as np
        import soundfile as sf

        if self._kokoro_pipeline is None:
            raise RuntimeError("Kokoro pipeline not initialized")

        t0 = time.time()
        chunks = []
        for _, _, audio_np in self._kokoro_pipeline(text, voice=self._kokoro_voice, speed=1.0):
            chunks.append(audio_np)

        if not chunks:
            log.error("Kokoro produced no audio")
            return b""

        audio = np.concatenate(chunks)
        buf = io.BytesIO()
        sf.write(buf, audio, 24000, format="WAV")
        log.info(f"TIMING tts_synth_done ({time.time() - t0:.2f}s)")
        return buf.getvalue()

    def _synthesize_macos_say(self, text) -> bytes:
        if platform.system() != "Darwin":
            log.error("macos_say is only available on macOS")
            return b""
        voice = _best_macos_voice()
        if voice is None:
            log.error(
                "No macOS voice available — download one in "
                "System Settings → Accessibility → Spoken Content"
            )
            print("\n❌ No macOS voice installed — download one here:\n")
            print("   System Settings > Accessibility > Spoken Content\n")
            return b""
        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tf:
            tmp_path = tf.name
        try:
            subprocess.run(["say", "-v", voice, "-o", tmp_path, text], check=True, capture_output=True)
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _synthesize_piper(self, text, local_voice) -> bytes:
        from pathlib import Path
        model = _PIPER_VOICES[local_voice]
        models_dir = Path(__file__).parent.parent / "benchmarks" / "results" / "piper_models"
        onnx = models_dir / f"{model}.onnx"
        cfg = models_dir / f"{model}.onnx.json"
        if not onnx.exists():
            raise RuntimeError(
                f"Piper model not found: {onnx}. "
                "Run: python scripts/bench_tts.py --phase clips"
            )
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tmp_path = tf.name
        try:
            subprocess.run(
                [sys.executable, "-m", "piper",
                 "--model", str(onnx), "--config", str(cfg),
                 "--output_file", tmp_path],
                input=text.encode(), capture_output=True, check=True,
            )
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # OpenAI provider
    # ------------------------------------------------------------------

    def _synthesize_openai(self, text) -> bytes:
        if self._openai is None:
            from openai import OpenAI
            self._openai = OpenAI(api_key=config.OPENAI_API_KEY)

        t0 = time.time()
        chunks = []
        with self._openai.audio.speech.with_streaming_response.create(
            model=config.TTS_OPENAI_MODEL,
            voice=config.TTS_OPENAI_VOICE,
            input=text,
            response_format="mp3",
        ) as resp:
            for chunk in resp.iter_bytes(chunk_size=4096):
                if chunk:
                    chunks.append(chunk)
        log.info(f"TIMING tts_synth_done ({time.time() - t0:.2f}s)")
        return b"".join(chunks)

    # ------------------------------------------------------------------
    # ElevenLabs provider
    # ------------------------------------------------------------------

    def _synthesize_elevenlabs(self, text) -> bytes:
        if self._eleven is None:
            from elevenlabs.client import ElevenLabs
            self._eleven = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)

        t0 = time.time()
        audio_stream = self._eleven.text_to_speech.stream(
            text=text,
            voice_id=config.TTS_VOICE_ID,
            model_id=config.TTS_MODEL,
        )
        chunks = [chunk for chunk in audio_stream if chunk]
        log.info(f"TIMING tts_synth_done ({time.time() - t0:.2f}s)")
        return b"".join(chunks)


def _best_macos_voice() -> str | None:
    """Return the best available macOS US English voice."""
    result = subprocess.run(["say", "-v", "?"], capture_output=True, text=True)
    installed = {line.split("  ")[0].strip() for line in result.stdout.splitlines() if line.strip()}
    for voice in [
        "Ava (Premium)", "Zoe (Premium)",
        "Ava (Enhanced)", "Zoe (Enhanced)",
        "Flo (English (US))", "Shelley (English (US))",
        "Reed (English (US))", "Sandy (English (US))",
        "Eddy (English (US))", "Rocko (English (US))",
        "Samantha",
    ]:
        if voice in installed:
            return voice
    return None
