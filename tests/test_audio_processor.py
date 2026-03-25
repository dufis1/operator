"""
Tests for pipeline.audio.AudioProcessor.

No BlackHole or Swift helper required. Tests the buffer logic, echo prevention,
silence detection, and Whisper transcription in isolation.

Run all tests:
    python tests/test_audio_processor.py

Skip the mic test (CI / no microphone):
    python tests/test_audio_processor.py --no-mic
"""
import sys
import time
import threading
import numpy as np
sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])  # repo root

from pipeline.audio import AudioProcessor, SAMPLE_RATE, UTTERANCE_SILENCE_RMS

# One shared instance so Whisper only loads once.
print("Loading AudioProcessor (Whisper model)...")
proc = AudioProcessor()
print("Ready.\n")


# ---------------------------------------------------------------------------
# Pure logic tests — no hardware
# ---------------------------------------------------------------------------

def test_buffer_operations():
    """feed_audio + drain_audio_buffer round-trip."""
    p = AudioProcessor.__new__(AudioProcessor)
    import threading
    p._audio_buffer = b""
    p._audio_lock = threading.Lock()
    p.is_speaking = False

    p.feed_audio(b"\x01\x02\x03\x04")
    assert p.drain_audio_buffer() == b"\x01\x02\x03\x04"
    assert p.drain_audio_buffer() == b""  # second drain is empty
    print("✅ buffer operations")


def test_echo_prevention():
    """feed_audio is a no-op while is_speaking=True."""
    p = AudioProcessor.__new__(AudioProcessor)
    import threading
    p._audio_buffer = b""
    p._audio_lock = threading.Lock()

    p.is_speaking = True
    p.feed_audio(b"\x01\x02\x03\x04")
    assert p.drain_audio_buffer() == b""

    p.is_speaking = False
    p.feed_audio(b"\x01\x02\x03\x04")
    assert p.drain_audio_buffer() == b"\x01\x02\x03\x04"
    print("✅ echo prevention")


# ---------------------------------------------------------------------------
# Whisper tests — no microphone needed
# ---------------------------------------------------------------------------

def test_transcribe_silence():
    """Transcribing silence returns an empty string (VAD filter suppresses hallucinations)."""
    silence = np.zeros(SAMPLE_RATE * 2, dtype=np.float32)
    result = proc.transcribe(silence)
    assert result == "", f"Expected '' for silence, got: '{result}'"
    print("✅ transcribe silence → ''")


def test_capture_next_utterance_synthetic():
    """
    Feed synthetic speech (noise above RMS threshold) then silence into the buffer
    via a background thread. Verify capture_next_utterance() returns without error.
    """
    rng = np.random.default_rng(42)
    # Centered noise in [-0.1, 0.1] — RMS ≈ 0.058, well above UTTERANCE_SILENCE_RMS=0.02
    speech = ((rng.random(SAMPLE_RATE * 2).astype(np.float32) * 2 - 1) * 0.1)
    silence = np.zeros(SAMPLE_RATE * 2, dtype=np.float32)
    audio_bytes = np.concatenate([speech, silence]).tobytes()

    proc.capturing = True

    def feeder():
        chunk = 4096
        for i in range(0, len(audio_bytes), chunk):
            if not proc.capturing:
                break
            proc.feed_audio(audio_bytes[i:i + chunk])
            time.sleep(0.005)
        # After all audio is fed, stop capturing so the loop exits
        proc.capturing = False

    threading.Thread(target=feeder, daemon=True).start()
    text = proc.capture_next_utterance(is_prompt=False)

    assert isinstance(text, str)
    print(f"✅ capture_next_utterance (synthetic noise → '{text}')")


# ---------------------------------------------------------------------------
# Mic test — requires built-in mic, no BlackHole needed
# ---------------------------------------------------------------------------

def test_transcribe_mic():
    """Record 5s from the built-in mic and transcribe. Speak a short phrase when prompted."""
    try:
        import sounddevice as sd
    except ImportError:
        print("⚠️  sounddevice not available, skipping mic test")
        return

    print("\nSpeak a short phrase now (recording 5s)...")
    audio = sd.rec(SAMPLE_RATE * 5, samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    audio = audio.squeeze()

    result = proc.transcribe(audio)
    print(f"Whisper heard: '{result}'")
    assert isinstance(result, str)
    print("✅ transcribe mic")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    skip_mic = "--no-mic" in sys.argv

    test_buffer_operations()
    test_echo_prevention()
    test_transcribe_silence()
    test_capture_next_utterance_synthetic()

    if not skip_mic:
        test_transcribe_mic()
    else:
        print("⏭️  mic test skipped (--no-mic)")

    print("\n✅ All AudioProcessor tests passed")
