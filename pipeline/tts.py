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
            from kokoro import KPipeline
            voice_id = _KOKORO_VOICES[config.TTS_LOCAL_VOICE]
            lang_code = "b" if voice_id.startswith("b") else "a"
            self._kokoro_pipeline = KPipeline(lang_code=lang_code)
            self._kokoro_voice = voice_id
            log.info(f"Kokoro TTS ready (voice={voice_id})")
        except ImportError:
            log.warning(
                "Kokoro not installed (requires Python 3.10–3.12 and: pip install kokoro soundfile). "
                "Falling back to macos_say."
            )
            # Runtime fallback — mutate config so speak() routes correctly
            config.TTS_LOCAL_VOICE = "macos_say"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def speak(self, text):
        """Synthesize text and play through the output device."""
        t0 = time.time()
        provider = config.TTS_PROVIDER
        if provider == "local":
            self._speak_local(text)
        elif provider == "openai":
            self._speak_openai(text)
        elif provider == "elevenlabs":
            self._speak_elevenlabs(text)
        else:
            raise ValueError(f"Unknown TTS provider: {provider!r}")
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

    def _speak_local(self, text):
        voice = config.TTS_LOCAL_VOICE
        if voice in _KOKORO_VOICES:
            self._speak_kokoro(text)
        elif voice == "macos_say":
            self._speak_macos_say(text)
        elif voice in _PIPER_VOICES:
            self._speak_piper(text, voice)
        else:
            raise ValueError(f"Unknown local_voice: {voice!r}")

    def _speak_kokoro(self, text):
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
            return

        audio = np.concatenate(chunks)
        buf = io.BytesIO()
        sf.write(buf, audio, 24000, format="WAV")
        wav_bytes = buf.getvalue()
        log.info(f"TIMING tts_synth_done ({time.time() - t0:.2f}s)")

        proc = subprocess.Popen(
            ["mpv", "--no-terminal", f"--audio-device={self._output_device}", "--", "-"],
            stdin=subprocess.PIPE,
        )
        proc.stdin.write(wav_bytes)
        proc.stdin.close()
        proc.wait()

    def _speak_macos_say(self, text):
        if platform.system() != "Darwin":
            log.error("macos_say is only available on macOS")
            return
        voice = _best_macos_voice()
        if voice is None:
            log.error(
                "No macOS voice available — download one in "
                "System Settings → Accessibility → Spoken Content"
            )
            return
        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tf:
            tmp_path = tf.name
        try:
            subprocess.run(["say", "-v", voice, "-o", tmp_path, text], check=True, capture_output=True)
            self.play_clip(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _speak_piper(self, text, local_voice):
        from pathlib import Path
        model = _PIPER_VOICES[local_voice]
        models_dir = Path(__file__).parent.parent / "bench_results" / "piper_models"
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
            self.play_clip(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # OpenAI provider
    # ------------------------------------------------------------------

    def _speak_openai(self, text):
        if self._openai is None:
            from openai import OpenAI
            self._openai = OpenAI(api_key=config.OPENAI_API_KEY)

        t0 = time.time()
        proc = subprocess.Popen(
            ["mpv", "--no-terminal", f"--audio-device={self._output_device}", "--", "-"],
            stdin=subprocess.PIPE,
        )
        first_chunk = True
        with self._openai.audio.speech.with_streaming_response.create(
            model=config.TTS_OPENAI_MODEL,
            voice=config.TTS_OPENAI_VOICE,
            input=text,
            response_format="mp3",
        ) as resp:
            for chunk in resp.iter_bytes(chunk_size=4096):
                if chunk:
                    if first_chunk:
                        log.info(f"TIMING tts_first_chunk ({time.time() - t0:.2f}s)")
                        first_chunk = False
                    proc.stdin.write(chunk)
        proc.stdin.close()
        proc.wait()

    # ------------------------------------------------------------------
    # ElevenLabs provider
    # ------------------------------------------------------------------

    def _speak_elevenlabs(self, text):
        if self._eleven is None:
            from elevenlabs.client import ElevenLabs
            self._eleven = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)

        t0 = time.time()
        audio_stream = self._eleven.text_to_speech.stream(
            text=text,
            voice_id=config.TTS_VOICE_ID,
            model_id=config.TTS_MODEL,
        )
        proc = subprocess.Popen(
            ["mpv", "--no-terminal", f"--audio-device={self._output_device}", "--", "-"],
            stdin=subprocess.PIPE,
        )
        first_chunk = True
        for chunk in audio_stream:
            if chunk:
                if first_chunk:
                    log.info(f"TIMING tts_first_chunk ({time.time() - t0:.2f}s)")
                    first_chunk = False
                proc.stdin.write(chunk)
        proc.stdin.close()
        proc.wait()


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
