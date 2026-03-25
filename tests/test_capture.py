"""
Test that ScreenCaptureKit can capture system audio.
Captures 10 seconds and saves to /tmp/operator_test_capture.wav.

Must be run from inside a .app bundle — run via:
    python setup.py py2app -A && open dist/Operator.app
(won't work if run directly with `python test_capture.py`)
"""
import time
import threading
import numpy as np
import soundfile as sf
import Cocoa
import ScreenCaptureKit
import CoreMedia

SAMPLE_RATE = 16000
CAPTURE_SECONDS = 10
OUTPUT_PATH = "/tmp/operator_test_capture.wav"

audio_chunks = []
capture_done = threading.Event()


class StreamDelegate(Cocoa.NSObject):
    def stream_didOutputSampleBuffer_ofType_(self, stream, sample_buffer, output_type):
        if output_type != ScreenCaptureKit.SCStreamOutputTypeAudio:
            return

        # Extract audio from CMSampleBuffer
        block_buffer = CoreMedia.CMSampleBufferGetDataBuffer(sample_buffer)
        if block_buffer is None:
            return

        length, data_pointer, _ = CoreMedia.CMBlockBufferGetDataPointer(
            block_buffer, 0, None, None
        )
        if data_pointer is None:
            return

        # Convert to numpy array (float32 interleaved)
        arr = np.frombuffer(data_pointer[:length], dtype=np.float32)
        audio_chunks.append(arr.copy())

    def stream_didStopWithError_(self, stream, error):
        print(f"Stream stopped: {error}")
        capture_done.set()


def run_capture():
    # Get shareable content (required before creating a filter)
    content_ref = [None]
    error_ref = [None]
    done = threading.Event()

    def content_handler(content, error):
        content_ref[0] = content
        error_ref[0] = error
        done.set()

    ScreenCaptureKit.SCShareableContent.getShareableContentWithCompletionHandler_(
        content_handler
    )
    done.wait(timeout=5)

    if error_ref[0] or content_ref[0] is None:
        print(f"❌ Could not get shareable content: {error_ref[0]}")
        print("   Make sure Screen & System Audio Recording permission is granted.")
        return

    content = content_ref[0]

    # Create a filter that captures all displays (audio only — we don't need video)
    displays = content.displays()
    if not displays:
        print("❌ No displays found")
        return

    content_filter = ScreenCaptureKit.SCContentFilter.alloc().initWithDisplay_excludingWindows_(
        displays[0], []
    )

    # Configure stream: audio only, no video
    config = ScreenCaptureKit.SCStreamConfiguration.alloc().init()
    config.setCapturesAudio_(True)
    config.setExcludesCurrentProcessAudio_(False)

    # Audio format: mono, 16kHz, float32
    config.setSampleRate_(SAMPLE_RATE)
    config.setChannelCount_(1)

    # Create and start stream
    delegate = StreamDelegate.alloc().init()
    stream = ScreenCaptureKit.SCStream.alloc().initWithFilter_configuration_delegate_(
        content_filter, config, delegate
    )

    error_ptr = [None]
    stream.addStreamOutput_type_sampleHandlerQueue_error_(
        delegate,
        ScreenCaptureKit.SCStreamOutputTypeAudio,
        None,
        error_ptr,
    )
    if error_ptr[0]:
        print(f"❌ addStreamOutput error: {error_ptr[0]}")
        return

    start_done = threading.Event()

    def start_handler(error):
        if error:
            print(f"❌ startCapture error: {error}")
        else:
            print(f"✅ Capturing for {CAPTURE_SECONDS} seconds — play audio in the meeting now...")
        start_done.set()

    stream.startCaptureWithCompletionHandler_(start_handler)
    start_done.wait(timeout=5)

    time.sleep(CAPTURE_SECONDS)

    stop_done = threading.Event()

    def stop_handler(error):
        if error:
            print(f"❌ stopCapture error: {error}")
        stop_done.set()

    stream.stopCaptureWithCompletionHandler_(stop_handler)
    stop_done.wait(timeout=5)

    # Save captured audio to WAV
    if not audio_chunks:
        print("❌ No audio captured — check permission and that audio was playing")
        return

    audio = np.concatenate(audio_chunks)
    sf.write(OUTPUT_PATH, audio, SAMPLE_RATE)
    duration = len(audio) / SAMPLE_RATE
    print(f"✅ Saved {duration:.1f}s of audio to {OUTPUT_PATH}")
    print(f"   Open it in QuickTime to verify it captured the meeting audio.")


if __name__ == "__main__":
    run_capture()
