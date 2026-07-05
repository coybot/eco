import Foundation

/// Left/right wheel linear velocities (m/s) — exactly the payload of the WAVE ROVER
/// speed command `{"T":1,"L":<left>,"R":<right>}`.
public struct WheelCommand: Equatable, Sendable {
    public var left: Double
    public var right: Double
    public init(left: Double, right: Double) {
        self.left = left
        self.right = right
    }
    public static let stop = WheelCommand(left: 0, right: 0)
}

/// Convert a unicycle command (forward v, turn rate w) into differential wheel speeds.
public enum DifferentialDrive {
    /// - Parameters:
    ///   - v: forward linear velocity (m/s)
    ///   - w: angular velocity (rad/s, CCW positive)
    ///   - wheelBase: track width between left/right wheels (m)
    ///   - maxWheelSpeed: per-wheel saturation (m/s); pair is scaled down together to preserve curvature
    public static func wheels(v: Double, w: Double, wheelBase: Double, maxWheelSpeed: Double) -> WheelCommand {
        let half = wheelBase / 2
        var left = v - w * half
        var right = v + w * half
        let peak = max(abs(left), abs(right))
        if peak > maxWheelSpeed, peak > 0 {
            let s = maxWheelSpeed / peak
            left *= s
            right *= s
        }
        return WheelCommand(left: left, right: right)
    }
}
