import Foundation

/// Connection + tuning defaults for the WAVE ROVER.
///
/// Cloud/auth values (Cognito, IoT, API, S3) are shared with the drone platform — reuse
/// `AWSConfig` copied from the DroneOperator app. This file holds only rover-specific bits.
enum RoverConfig {
    /// Default host of the WAVE ROVER ESP32 web server.
    /// - AP mode (rover as its own hotspot): 192.168.4.1
    /// - STA mode (rover joined building WiFi): set to the DHCP address / Bonjour name.
    static let defaultHost = "192.168.4.1"

    /// The ESP32 firmware exposes JSON control at `GET /js?json=<url-encoded JSON>`.
    static let jsonCommandPath = "/js"

    /// WAVE ROVER JSON command "T" opcodes we use (see Waveshare sub-controller command set).
    enum Opcode {
        static let speedControl = 1     // {"T":1,"L":<m/s>,"R":<m/s>}
        static let emergencyStop = 0    // {"T":0} — stop all motors
        static let feedbackFlowOn = 131 // continuous chassis+IMU feedback
        static let imuQuery = 126       // one-shot IMU read
    }

    // MARK: - Physical parameters (WAVE ROVER)
    static let wheelBase = 0.13         // m, track width (left↔right)
    static let maxWheelSpeed = 0.5      // m/s per Waveshare closed-loop range on encoder bases; base rover is open-loop

    // MARK: - Safety
    /// If no successful command round-trip within this window, ObstacleGuard forces a stop.
    static let commsWatchdogTimeout: TimeInterval = 0.5
    /// Drive-command resend period; keeps the base moving and doubles as a heartbeat.
    static let commandInterval: TimeInterval = 0.1
}
