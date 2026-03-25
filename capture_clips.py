#!/usr/bin/env python3
"""
Standalone clip capture for STT benchmarking.

Uses the same Swift audio_capture helper and RMS-based utterance detection
as Operator, but saves each utterance as a WAV file instead of transcribing.

Usage:
    python capture_clips.py

Join a meeting first, then run this script. Speak your test phrases.
Each detected utterance is saved to benchmark_clips/clip_001.wav etc.
Press Ctrl+C to stop.
"""

import os
import sys
import signal
import struct
import threading
import time
import wave

import numpy as np

# ── Audio constants (must match app.py / audio_capture.swift) ──────────────
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 4  # Float32
CHUNK_SIZE = 4096

# ── Utterance detection (same thresholds as app.py) ────────────────────────
UTTERANCE_CHECK_INTERVAL = 0.5   # seconds between checks
UTTERANCE_SILENCE_THRESHOLD = 2  # consecutive silent checks → utterance done
UTTERANCE_MAX_DURATION = 10      # hard cap in seconds
UTTERANCE_SILENCE_RMS = 0.02     # RMS below this = silence

# ── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIO_CAPTURE_HELPER = os.path.join(SCRIPT_DIR, "audio_capture")
CLIPS_DIR = os.path.join(SCRIPT_DIR, "benchmark_clips")


class ClipCapture:
    def __init__(self):
        self._audio_buffer = b""
        self._buffer_lock = threading.Lock()
        self._capturing = False
        self._proc = None
        self._clip_count = self._next_clip_number()

    def _next_clip_number(self):
        """Find the next available clip number in benchmark_clips/."""
        existing = [
            f for f in os.listdir(CLIPS_DIR)
            if f.startswith("clip_") and f.endswith(".wav")
        ]
        if not existing:
            return 1
        nums = []
        for f in existing:
            try:
                nums.append(int(f.replace("clip_", "").replace(".wav", "")))
            except ValueError:
                pass
        return max(nums) + 1 if nums else 1

    def start(self):
        import subprocess

        if not os.path.isfile(AUDIO_CAPTURE_HELPER):
            print(f"ERROR: audio_capture helper not found at {AUDIO_CAPTURE_HELPER}")
            sys.exit(1)

        os.makedirs(CLIPS_DIR, exist_ok=True)

        print("Starting audio capture (same path as Operator)...")
        self._proc = subprocess.Popen(
            [AUDIO_CAPTURE_HELPER],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
        )
        self._capturing = True

        # Read thread — accumulates audio into buffer
        self._read_thread = threading.Thread(target=self._audio_read_loop, daemon=True)
        self._read_thread.start()

        # Stderr thread — log Swift helper messages
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()

        # Give the helper a moment to initialize
        time.sleep(1.0)
        print(f"Listening... Speak your test phrases. Clips save to {CLIPS_DIR}/")
        print("Press Ctrl+C to stop.\n")

        try:
            self._capture_loop()
        except KeyboardInterrupt:
            print("\nStopping...")
        finally:
            self.stop()

    def stop(self):
        self._capturing = False
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            self._proc.wait(timeout=3)
        print(f"Done. {self._clip_count - 1} clip(s) in {CLIPS_DIR}/")

    def _audio_read_loop(self):
        """Read raw PCM from Swift helper stdout, accumulate in buffer."""
        while self._capturing:
            try:
                chunk = self._proc.stdout.read(CHUNK_SIZE)
                if not chunk:
                    break
                with self._buffer_lock:
                    self._audio_buffer += chunk
            except Exception:
                break

    def _read_stderr(self):
        """Log Swift helper stderr messages."""
        while self._capturing:
            try:
                line = self._proc.stderr.readline()
                if not line:
                    break
                # Only show first few lines to avoid noise
            except Exception:
                break

    def _drain_buffer(self):
        """Drain and return accumulated audio buffer."""
        with self._buffer_lock:
            data = self._audio_buffer
            self._audio_buffer = b""
        return data

    def _capture_loop(self):
        """Main loop — detect utterances and save as WAV files."""
        while self._capturing:
            audio = self._capture_next_utterance()
            if audio:
                self._save_clip(audio)

    def _capture_next_utterance(self):
        """
        Wait for speech, accumulate audio, finalize on silence.
        Same logic as app.py _capture_next_utterance().
        Returns raw Float32 PCM bytes, or None.
        """
        speech_detected = False
        silence_count = 0
        utterance_audio = b""
        speech_start_time = None

        while self._capturing:
            time.sleep(UTTERANCE_CHECK_INTERVAL)
            raw = self._drain_buffer()

            if not raw:
                if speech_detected:
                    silence_count += 1
                continue

            rms = float(np.sqrt(np.mean(np.frombuffer(raw, dtype=np.float32) ** 2)))

            if rms >= UTTERANCE_SILENCE_RMS:
                if not speech_detected:
                    speech_detected = True
                    speech_start_time = time.time()
                    print("  [speech detected]", end="", flush=True)
                silence_count = 0
                utterance_audio += raw
            elif speech_detected:
                utterance_audio += raw
                silence_count += 1

            # Check finalization
            if speech_detected:
                speech_duration = time.time() - speech_start_time

                # Silence-based finalization
                if silence_count >= UTTERANCE_SILENCE_THRESHOLD:
                    print(f" → {speech_duration:.1f}s")
                    return utterance_audio

                # Duration-based finalization
                if speech_duration > UTTERANCE_MAX_DURATION:
                    print(f" → {speech_duration:.1f}s (max duration)")
                    return utterance_audio

        return None

    def _save_clip(self, pcm_data):
        """Save raw Float32 PCM as 16-bit WAV file."""
        # Convert Float32 → Int16
        samples = np.frombuffer(pcm_data, dtype=np.float32)
        int16_samples = np.clip(samples * 32767, -32768, 32767).astype(np.int16)

        filename = f"clip_{self._clip_count:03d}.wav"
        filepath = os.path.join(CLIPS_DIR, filename)

        with wave.open(filepath, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(int16_samples.tobytes())

        duration = len(samples) / SAMPLE_RATE
        print(f"  Utterance {self._clip_count} saved → {filename} ({duration:.1f}s)")
        self._clip_count += 1


if __name__ == "__main__":
    ClipCapture().start()
