import Foundation
import ScreenCaptureKit
import CoreMedia
import AVFoundation

// Disable stdout buffering so data flows immediately
setbuf(stdout, nil)

fputs("audio_capture: starting\n", stderr)

// Log parent process for permission diagnostics
let parentPID = getppid()
if let parentApp = NSRunningApplication.init(processIdentifier: parentPID) {
    let name = parentApp.localizedName ?? "unknown"
    let bundle = parentApp.bundleIdentifier ?? "no-bundle-id"
    fputs("audio_capture: parent process: \(name) (\(bundle), pid=\(parentPID))\n", stderr)
} else {
    // Walk up the process tree to find the terminal app
    fputs("audio_capture: parent pid=\(parentPID) (not an NSRunningApplication — likely a shell)\n", stderr)
}

// Stream delegate to catch async errors
class StreamDelegate: NSObject, SCStreamDelegate {
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fputs("audio_capture: DELEGATE stream stopped with error: \(error.localizedDescription)\n", stderr)
        exit(1)
    }
}

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
let delegate = StreamDelegate()
let semaphore = DispatchSemaphore(value: 0)
var captureStarted = false

// Pre-flight: check Screen Recording permission before attempting capture
if !CGPreflightScreenCaptureAccess() {
    fputs("audio_capture: Screen Recording permission not granted — requesting...\n", stderr)
    CGRequestScreenCaptureAccess()
    // Give the user a few seconds to respond to the dialog
    Thread.sleep(forTimeInterval: 3)
    if !CGPreflightScreenCaptureAccess() {
        fputs("audio_capture: FATAL — Screen Recording permission denied.\n", stderr)
        fputs("audio_capture: Grant permission in System Settings > Privacy & Security > Screen Recording\n", stderr)
        exit(3)
    }
}
fputs("audio_capture: Screen Recording permission OK\n", stderr)

fputs("audio_capture: requesting shareable content...\n", stderr)

SCShareableContent.getWithCompletionHandler { content, error in
    if let error = error {
        fputs("audio_capture: ERROR getting shareable content: \(error.localizedDescription)\n", stderr)
        exit(1)
    }
    guard let content = content else {
        fputs("audio_capture: ERROR content is nil\n", stderr)
        exit(2)
    }

    fputs("audio_capture: displays=\(content.displays.count), windows=\(content.windows.count), apps=\(content.applications.count)\n", stderr)

    guard let display = content.displays.first else {
        fputs("audio_capture: ERROR no displays found\n", stderr)
        exit(2)
    }
    fputs("audio_capture: using display \(display.displayID) (\(display.width)x\(display.height))\n", stderr)

    let filter = SCContentFilter(display: display, excludingWindows: [])
    let config = SCStreamConfiguration()
    config.capturesAudio = true
    config.excludesCurrentProcessAudio = false
    config.sampleRate = 16000
    config.channelCount = 1
    // Minimize video overhead since we only want audio
    config.width = 2
    config.height = 2
    config.minimumFrameInterval = CMTime(value: 1, timescale: 1) // 1 fps

    let stream = SCStream(filter: filter, configuration: config, delegate: delegate)

    do {
        try stream.addStreamOutput(handler, type: .audio, sampleHandlerQueue: DispatchQueue(label: "audio"))
    } catch {
        fputs("audio_capture: ERROR adding output: \(error.localizedDescription)\n", stderr)
        exit(1)
    }

    fputs("audio_capture: calling startCapture...\n", stderr)
    stream.startCapture { error in
        if let error = error {
            fputs("audio_capture: ERROR starting capture: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
        captureStarted = true
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

    // Watchdog: if startCapture hasn't completed in 10 seconds, something is wrong
    DispatchQueue.global().asyncAfter(deadline: .now() + 10) {
        if captureStarted { return }  // capture succeeded — watchdog no longer needed
        fputs("audio_capture: FATAL — startCapture hung for 10s.\n", stderr)
        fputs("audio_capture: CGPreflightScreenCaptureAccess() passed but startCapture did not complete.\n", stderr)
        fputs("audio_capture: This is likely a stale macOS permission cache (TCC daemon).\n", stderr)
        exit(4)
    }
}

semaphore.wait()
fputs("audio_capture: done\n", stderr)
