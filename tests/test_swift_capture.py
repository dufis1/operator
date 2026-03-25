"""
Step 7: Test that Python can launch the Swift helper,
read raw PCM from its stdout, and save a valid WAV file.
"""
import subprocess
import numpy as np
import soundfile as sf
import os
import sys
import threading

SAMPLE_RATE = 16000
CAPTURE_SECONDS = 5
HELPER_PATH = os.path.join(os.path.dirname(__file__), "audio_capture")
OUTPUT_PATH = "/tmp/test_swift_capture.wav"


def read_stderr(proc):
    """Print stderr from the Swift helper so we can see its logs."""
    for line in proc.stderr:
        print(f"  [swift] {line}", end="")


def main():
    if not os.path.exists(HELPER_PATH):
        print(f"ERROR: Swift helper not found at {HELPER_PATH}")
        sys.exit(1)

    print(f"Launching Swift helper: {HELPER_PATH}")
    proc = subprocess.Popen(
        [HELPER_PATH],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
    )

    # Read stderr in a background thread so we see Swift's log messages
    stderr_thread = threading.Thread(target=read_stderr, args=(proc,), daemon=True)
    stderr_thread.start()

    # Read raw Float32 PCM from stdout for CAPTURE_SECONDS
    bytes_needed = SAMPLE_RATE * 4 * CAPTURE_SECONDS  # 4 bytes per Float32 sample
    print(f"Reading {bytes_needed} bytes ({CAPTURE_SECONDS}s of audio)...")

    data = b""
    while len(data) < bytes_needed:
        chunk = proc.stdout.read(min(4096, bytes_needed - len(data)))
        if not chunk:
            print(f"  Swift helper stopped early after {len(data)} bytes")
            break
        data += chunk

    print(f"Read {len(data)} bytes from Swift helper")

    # Stop the helper by closing stdin
    print("Closing stdin to stop helper...")
    proc.stdin.close()
    proc.wait(timeout=5)
    print(f"Helper exited with code {proc.returncode}")

    # Convert to numpy and save
    audio = np.frombuffer(data, dtype=np.float32)
    print(f"Audio: {len(audio)} samples, {len(audio)/SAMPLE_RATE:.2f}s")
    print(f"Signal range: {audio.min():.4f} to {audio.max():.4f}")

    sf.write(OUTPUT_PATH, audio, SAMPLE_RATE)
    print(f"Saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
