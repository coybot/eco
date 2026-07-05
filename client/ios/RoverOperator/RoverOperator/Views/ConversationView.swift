import SwiftUI

/// Phase-2 voice UI. Push-to-talk (hold the mic button) rather than always-on wake-word
/// listening — simpler and more reliable on a noisy, engine/ground-equipment-loud ramp.
struct ConversationView: View {
    let nav: NavigationController

    @State private var speechIn = SpeechIn()
    @State private var speechOut = SpeechOut()
    @State private var dialog: DialogAgent?
    @State private var reply = ""
    @State private var authorized = false

    var body: some View {
        VStack(spacing: 20) {
            Text(statusLabel).font(.headline)

            Text(speechIn.partialTranscript)
                .foregroundStyle(.secondary)
                .frame(minHeight: 40)
                .multilineTextAlignment(.center)

            Image(systemName: "mic.circle.fill")
                .font(.system(size: 72))
                .foregroundStyle(speechIn.state == .listening ? .red : .accentColor)
                .gesture(
                    DragGesture(minimumDistance: 0)
                        .onChanged { _ in startListening() }
                        .onEnded { _ in speechIn.stop() }
                )

            if !reply.isEmpty {
                Text(reply)
                    .font(.body)
                    .padding()
                    .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
            }

            Spacer()
        }
        .padding()
        .task {
            authorized = await speechIn.requestAuthorization()
            dialog = DialogAgent(nav: nav)
        }
    }

    private var statusLabel: String {
        if !authorized { return "Enable Speech Recognition in Settings" }
        switch speechIn.state {
        case .listening: return "Listening…"
        case .unavailable: return "Speech recognition unavailable"
        case .idle: return "Hold to talk"
        }
    }

    private func startListening() {
        guard authorized, speechIn.state != .listening else { return }
        try? speechIn.start { utterance in
            Task {
                guard let dialog else { return }
                let text = await dialog.handle(utterance)
                reply = text
                speechOut.speak(text)
            }
        }
    }
}
