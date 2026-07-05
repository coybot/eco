import Foundation
import Speech
import AVFoundation

/// On-device speech-to-text. `requiresOnDeviceRecognition = true` so the rover keeps
/// understanding the ground crew with no WiFi — matches the all-Apple, offline-first
/// voice stack (see eco/rover/docs/architecture.md).
@Observable
@MainActor
final class SpeechIn {
    enum State: Equatable { case idle, listening, unavailable }

    private(set) var state: State = .idle
    private(set) var partialTranscript = ""

    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let audioEngine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?

    func requestAuthorization() async -> Bool {
        await withCheckedContinuation { c in
            SFSpeechRecognizer.requestAuthorization { status in
                c.resume(returning: status == .authorized)
            }
        }
    }

    /// Start listening; invokes `onFinal` once a completed utterance is recognized.
    /// Push-to-talk is the intended usage (see ConversationView) — always-on listening
    /// risks false wake triggers in the engine/ground-equipment noise on an active ramp.
    func start(onFinal: @escaping (String) -> Void) throws {
        guard let recognizer, recognizer.isAvailable else {
            state = .unavailable
            throw SpeechError.recognizerUnavailable
        }
        stop()

        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        req.requiresOnDeviceRecognition = true
        request = req

        let node = audioEngine.inputNode
        let format = node.outputFormat(forBus: 0)
        node.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            req.append(buffer)
        }
        audioEngine.prepare()
        try audioEngine.start()
        state = .listening

        task = recognizer.recognitionTask(with: req) { [weak self] result, error in
            guard let self else { return }
            Task { @MainActor in
                if let result {
                    self.partialTranscript = result.bestTranscription.formattedString
                    if result.isFinal {
                        let text = result.bestTranscription.formattedString
                        self.stop()
                        onFinal(text)
                    }
                }
                if error != nil { self.stop() }
            }
        }
    }

    func stop() {
        audioEngine.inputNode.removeTap(onBus: 0)
        if audioEngine.isRunning { audioEngine.stop() }
        request?.endAudio()
        task?.cancel()
        task = nil
        request = nil
        if state != .unavailable { state = .idle }
    }
}

enum SpeechError: Error { case recognizerUnavailable }
