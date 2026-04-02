"""
LatencyProbe — background mic monitor for perceived latency measurement.

Runs in a daemon thread, sampling the default input device at 8 kHz.
Detects acoustic speech→silence transitions and logs TIMING events so
log analysis can compare acoustic silence (when the human stopped talking)
against caption/pipeline events (filler start, response start).

Zero impact on main pipeline: all work happens in a daemon thread reading
from PortAudio's ring buffer. No locks shared with the pipeline.

Usage:
    probe = LatencyProbe()
    probe.start()
    probe.set_active(False)   # suppress logging while bot is speaking/filling
    probe.set_active(True)    # resume
    probe.stop()
"""
import logging
import threading
import time

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

# RMS threshold — above ambient noise floor, below normal speech level.
# Tune based on "LatencyProbe: speech peak_rms=..." lines in the log.
_SILENCE_RMS = 0.03
# How many consecutive 100ms silent blocks before we declare silence.
# 3 blocks = 300ms hysteresis, prevents chattering on brief between-word dips.
_SILENCE_HOLD_BLOCKS = 3
# Ignore speech segments shorter than this (filters noise pops)
_MIN_SPEECH_DURATION = 0.3
# Sample rate — low enough to be cheap, high enough for voice envelope
_SAMPLE_RATE = 8000
# 100ms blocks
_BLOCK_SIZE = 800


class LatencyProbe:
    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self._active = True  # gated False while bot is filling/speaking

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="latency-probe")
        self._thread.start()
        log.info("LatencyProbe: started")

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def set_active(self, active: bool):
        """Gate logging. Call set_active(False) when bot starts filling or speaking,
        set_active(True) after echo guard clears, to avoid logging speaker bleed."""
        self._active = active

    def _run(self):
        try:
            self._monitor()
        except Exception as e:
            log.warning(f"LatencyProbe: stopped unexpectedly: {e}")

    def _monitor(self):
        in_speech = False
        speech_start = None
        silence_count = 0  # consecutive silent blocks since speech ended
        peak_rms = 0.0     # highest RMS seen during current speech segment

        try:
            device_info = sd.query_devices(kind="input")
            log.info(f"LatencyProbe: input device = {device_info['name']!r}")
            stream = sd.InputStream(
                samplerate=_SAMPLE_RATE,
                channels=1,
                blocksize=_BLOCK_SIZE,
                dtype="float32",
            )
        except Exception as e:
            log.warning(f"LatencyProbe: could not open input stream: {e}")
            return

        with stream:
            while not self._stop.is_set():
                try:
                    data, _ = stream.read(_BLOCK_SIZE)
                except KeyboardInterrupt:
                    break
                except Exception as e:
                    log.debug(f"LatencyProbe: stream read error: {e}")
                    break

                rms = float(np.sqrt(np.mean(data ** 2)))
                now = time.time()

                if not self._active:
                    # Reset state while gated so stale speech doesn't carry over
                    in_speech = False
                    speech_start = None
                    silence_count = 0
                    continue

                if rms > _SILENCE_RMS:
                    silence_count = 0
                    if rms > peak_rms:
                        peak_rms = rms
                    if not in_speech:
                        in_speech = True
                        speech_start = now
                        peak_rms = rms
                        log.info("TIMING perceived_speech_start")
                else:
                    if in_speech:
                        silence_count += 1
                        if silence_count >= _SILENCE_HOLD_BLOCKS:
                            # Sustained silence — speech has ended
                            duration = now - (speech_start or now)
                            if duration >= _MIN_SPEECH_DURATION:
                                log.info(
                                    f"TIMING perceived_acoustic_silence_end "
                                    f"speech_duration={duration:.2f}s peak_rms={peak_rms:.4f}"
                                )
                            in_speech = False
                            speech_start = None
                            silence_count = 0
                            peak_rms = 0.0
