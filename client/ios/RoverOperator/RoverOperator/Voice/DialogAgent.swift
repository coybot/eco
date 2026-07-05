import Foundation
import FoundationModels
import RoverNav

/// Conversation brain for the ramp-agent persona (Hawthorne Airport baggage handling).
///
/// The on-device Apple Foundation Model is **primary**: it parses speech into a
/// `RoverIntent` entirely on-device — free, private, and it keeps working in WiFi dead
/// spots exactly where the robot most needs to still respond (e.g. mid-hallway). Claude
/// (cloud) is only invoked when the on-device model flags `needsEscalation` (open-ended
/// chat, general knowledge) or is unavailable/fails to parse.
///
/// Deliberately NOT using FoundationModels' `Tool` calling to drive the motors directly:
/// the parsed `RoverIntent` is dispatched through this app's own switch/lookup below, so
/// there is an explicit, auditable step between "the model produced this" and "the rover
/// moved" rather than letting generated output call actuation code directly.
@Observable
@MainActor
final class DialogAgent {
    private let nav: NavigationController
    private let places: () -> [String: Vec2]
    private var session: LanguageModelSession?

    init(nav: NavigationController, places: @escaping () -> [String: Vec2] = WorldMapStore.places) {
        self.nav = nav
        self.places = places
    }

    /// True if the on-device Foundation Model is present and ready on this device/OS.
    var onDeviceAvailable: Bool {
        if case .available = SystemLanguageModel.default.availability { return true }
        return false
    }

    /// Handle one utterance end-to-end: parse on-device, act or escalate, return the
    /// text to speak.
    func handle(_ utterance: String) async -> String {
        guard onDeviceAvailable else {
            return await escalateToClaude(utterance)
        }

        do {
            let result = try await currentSession().respond(to: utterance, generating: RoverIntent.self)
            let intent = result.content

            if intent.needsEscalation || intent.action == .unknown {
                return await escalateToClaude(utterance)
            }

            switch intent.action {
            case .navigate:
                return act(destination: intent.destination)
            case .stop:
                nav.cancel()
                return "Stopping."
            case .greet:
                return "Hi — tell me where to take this load, or say stop."
            case .unknown:
                return await escalateToClaude(utterance)
            }
        } catch {
            // On-device parse failed outright — fall back to cloud rather than leaving
            // the request without any response.
            return await escalateToClaude(utterance)
        }
    }

    private func act(destination: String) -> String {
        guard let goal = places()[destination.lowercased()] else {
            return "I don't know how to get to \(destination) yet."
        }
        nav.navigate(to: goal)
        return "On my way to \(destination)."
    }

    private func currentSession() -> LanguageModelSession {
        if let session { return session }
        let s = LanguageModelSession(instructions: """
            You are the voice interface of an airport ramp baggage-handling robot. Parse \
            the ground crew's utterance into a RoverIntent. Only set action to navigate \
            when a concrete destination is named (e.g. a gate, jet bridge, or the cart \
            yard). Set needsEscalation for small talk, general knowledge questions, or \
            anything that isn't about moving the robot.
            """)
        session = s
        return s
    }

    // MARK: - Cloud escalation

    private func escalateToClaude(_ utterance: String) async -> String {
        do {
            return try await ClaudeDialogClient.shared.converse(utterance)
        } catch {
            return "Sorry, I'm having trouble understanding right now."
        }
    }
}
