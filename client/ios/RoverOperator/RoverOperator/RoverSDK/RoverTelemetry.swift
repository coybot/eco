import Foundation

/// Chassis + IMU feedback streamed by the WAVE ROVER ESP32 (feedback flow, T≈1001).
/// Fields mirror the Waveshare sub-controller feedback frame; all optional since the
/// exact set varies by firmware/base. Used for the Phase-0 "IMU streams" check and as a
/// secondary heading/tip sensor alongside ARKit.
struct RoverFeedback: Codable {
    let T: Int?
    let L: Double?     // left wheel speed (m/s)
    let R: Double?     // right wheel speed (m/s)
    let ax: Double?    // accel
    let ay: Double?
    let az: Double?
    let gx: Double?    // gyro
    let gy: Double?
    let gz: Double?
    let roll: Double?
    let pitch: Double?
    let yaw: Double?
    let v: Double?     // bus voltage

    enum CodingKeys: String, CodingKey {
        case T, L, R, ax, ay, az, gx, gy, gz, v
        case roll = "r", pitch = "p", yaw = "y"
    }
}

extension RoverFeedback {
    /// Parse one newline-delimited JSON feedback frame; returns nil on malformed input.
    static func parse(_ line: String) -> RoverFeedback? {
        guard let data = line.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(RoverFeedback.self, from: data)
    }

    /// Rough tip-over guard input: true if pitch/roll magnitude exceeds a threshold (rad).
    func isTipping(threshold: Double = 0.6) -> Bool {
        (abs(roll ?? 0) > threshold) || (abs(pitch ?? 0) > threshold)
    }
}
