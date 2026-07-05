import Foundation
import ARKit
import RoverNav

/// Publishes periodic rover telemetry to AWS IoT on `drone/{roverId}/status` — the
/// existing `DroneStatusRule` (SELECT * FROM 'drone/+/status') stores whatever we send
/// here with zero backend changes (see MQTTService's doc comment for why this topic,
/// not a new `rover/*` one).
@MainActor
final class RoverTelemetryPublisher {
    let roverId: String

    private let ar: ARSessionManager
    private let nav: NavigationController
    private var task: Task<Void, Never>?

    init(ar: ARSessionManager, nav: NavigationController) {
        self.ar = ar
        self.nav = nav
        self.roverId = Self.loadOrCreateRoverId()
    }

    func start(interval: TimeInterval = 2.0) {
        stop()
        task = Task { [weak self] in
            while let self, !Task.isCancelled {
                self.publishOnce()
                try? await Task.sleep(for: .seconds(interval))
            }
        }
    }

    func stop() {
        task?.cancel()
        task = nil
    }

    private func publishOnce() {
        var payload: [String: Any] = [
            "vehicleType": "rover",
            "navState": navStateLabel,
            "trackingState": trackingLabel,
            "forwardClearanceM": ar.forwardClearance.isFinite ? ar.forwardClearance : -1,
        ]
        if let pose = ar.pose {
            payload["poseX"] = pose.position.x
            payload["poseY"] = pose.position.y
            payload["yawRad"] = pose.yaw
        }
        MQTTService.shared.publish(to: "drone/\(roverId)/status", payload: payload)
    }

    private var navStateLabel: String {
        switch nav.state {
        case .idle: return "idle"
        case .planning: return "planning"
        case .driving: return "driving"
        case .arrived: return "arrived"
        case .failed: return "failed"
        }
    }

    private var trackingLabel: String {
        switch ar.trackingState {
        case .normal: return "normal"
        case .limited: return "limited"
        case .notAvailable: return "none"
        @unknown default: return "unknown"
        }
    }

    // MARK: - Rover ID

    /// Stable per-install identifier, analogous to the drone daemon's auto-generated
    /// `drone-<hash>` id (see root CLAUDE.md) — generated once, persisted, not tied to
    /// the DroneTable registration flow (which is a separate, not-yet-built pairing UI).
    private static let idKey = "us.astral.rover.id"

    private static func loadOrCreateRoverId() -> String {
        if let existing = UserDefaults.standard.string(forKey: idKey) { return existing }
        let id = "rover-\(UUID().uuidString.prefix(8))"
        UserDefaults.standard.set(id, forKey: idKey)
        return id
    }
}
