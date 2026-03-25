import Foundation
import ScreenCaptureKit
import CoreMedia
import AVFoundation

// Disable stdout buffering so data flows immediately
setbuf(stdout, nil)

fputs("audio_capture: starting\n", stderr)

// Audio output handler — receives sample buffers, writes raw Float32 PCM to stdout
class AudioOutputHandler: NSObject, SCStreamOutput {
    var callCount = 0
    var totalBytes = 0

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio else { return }
        callCount += 1

        guard let blockBuffer = CMSampleBufferGetDataBuffer(sampleBuffer) else {
            return  // silence — no data
        }

        let length = CMBlockBufferGetDataLength(blockBuffer)
        var data = Data(count: length)
        data.withUnsafeMutableBytes { rawBuffer in
            if let baseAddress = rawBuffer.baseAddress {
                CMBlockBufferCopyDataBytes(blockBuffer, atOffset: 0, dataLength: length, destination: baseAddress)
            }
        }

        // Write raw PCM bytes to stdout
        _ = data.withUnsafeBytes { rawBuffer in
            fwrite(rawBuffer.baseAddress, 1, length, stdout)
        }
        totalBytes += length

        if callCount <= 3 {
            fputs("audio_capture: callback #\(callCount) — wrote \(length) bytes\n", stderr)
        }
    }
}

let handler = AudioOutputHandler()
let semaphore = DispatchSemaphore(value: 0)

SCShareableContent.getWithCompletionHandler { content, error in
    if let error = error {
        fputs("audio_capture: ERROR getting shareable content: \(error.localizedDescription)\n", stderr)
        exit(1)
    }
    guard let content = content, let display = content.displays.first else {
        fputs("audio_capture: ERROR no displays found\n", stderr)
        exit(2)
    }
    fputs("audio_capture: found display \(display.displayID)\n", stderr)

    let filter = SCContentFilter(display: display, excludingWindows: [])
    let config = SCStreamConfiguration()
    config.capturesAudio = true
    config.excludesCurrentProcessAudio = false
    config.sampleRate = 16000
    config.channelCount = 1

    let stream = SCStream(filter: filter, configuration: config, delegate: nil)

    do {
        try stream.addStreamOutput(handler, type: .audio, sampleHandlerQueue: nil)
    } catch {
        fputs("audio_capture: ERROR adding output: \(error.localizedDescription)\n", stderr)
        exit(1)
    }

    stream.startCapture { error in
        if let error = error {
            fputs("audio_capture: ERROR starting capture: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
        fputs("audio_capture: capture started — streaming until stdin closes\n", stderr)

        // Wait for stdin to close (parent process signals shutdown)
        DispatchQueue.global().async {
            while let _ = readLine() {
                // consume any input
            }
            // stdin closed — stop capture
            fputs("audio_capture: stdin closed, stopping capture (\(handler.totalBytes) bytes, \(handler.callCount) callbacks)\n", stderr)
            stream.stopCapture { _ in
                semaphore.signal()
            }
        }
    }
}

semaphore.wait()
fputs("audio_capture: done\n", stderr)
