"""
TTS integration for Operator.

Wraps ElevenLabs text-to-speech and local audio clip playback via mpv.
The output device is a constructor parameter — macOS callers pass BlackHole;
Docker callers pass a PulseAudio sink name.

No macOS imports.
"""
import subprocess
import time
import logging
import config

log = logging.getLogger(__name__)


class TTSClient:
    """Streams ElevenLabs TTS and plays audio clips through a given output device.

    Args:
        eleven_client:  An ElevenLabs client instance.
        output_device:  mpv --audio-device string.
                        macOS: "coreaudio/BlackHole2ch_UID"
                        Docker: PulseAudio sink name, e.g. "pulse/MeetingOutput"
    """

    def __init__(self, eleven_client, output_device):
        self._eleven = eleven_client
        self._output_device = output_device

    def speak(self, text):
        """Stream text to ElevenLabs TTS and play through the output device."""
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
        t_stream_done = time.time()
        log.info(f"TTS stream complete: {t_stream_done - t0:.2f}s")
        proc.stdin.close()
        proc.wait()
        log.info(f"TTS playback done: {time.time() - t0:.2f}s (mpv drain: {time.time() - t_stream_done:.2f}s)")

    def play_clip(self, path):
        """Play a local audio file through the output device."""
        subprocess.run(
            ["mpv", "--no-terminal", f"--audio-device={self._output_device}", "--", path],
            check=False,
        )
