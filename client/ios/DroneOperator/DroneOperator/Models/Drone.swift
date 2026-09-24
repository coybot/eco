import Foundation

/// Represents a registered drone
struct Drone: Identifiable, Codable, Hashable {
    let userId: String
    let droneId: String
    var name: String
    let registeredAt: String
    var status: String
    
    var id: String { droneId }
    
    var isOnline: Bool {
        status == "online"
    }
    
    var registrationDate: Date? {
        ISO8601DateFormatter().date(from: registeredAt)
    }
}

/// Drone telemetry/status from API
struct DroneStatus: Codable {
    let droneId: String
    var status: String?  // "online", "offline", etc.
    var position: Position?
    var attitude: Attitude?
    var battery: Double?
    var armed: Bool?
    var mode: String?  // Flight mode (e.g., "STABILIZE", "GUIDED", "AUTO")
    var lastUpdateMs: Double?  // Epoch milliseconds (parsed from various formats)
    var result: CommandResult?
    var isOnline: Bool?
    var ttl: Int?  // TTL for DynamoDB
    // Battery, as estimated on the drone (resting-voltage curve on the ground,
    // verified current or sag-compensated voltage in the air) - not the FC's
    // raw counter, which reset at boot and froze with a dead current sensor.
    var voltage: Double?
    var batterySource: String?
    var batteryWarnings: [String]?
    var preflight: Preflight?
    var batteryBudget: BatteryBudget?

    /// May it take off, and if not why. Recomputed on every heartbeat.
    struct Preflight: Codable {
        let canTakeoff: Bool
        let reason: String?
        let minTakeoffPct: Double?
        let reservePct: Double?
    }

    /// In flight: roughly how many more ~30 s actions before it must head home.
    struct BatteryBudget: Codable {
        let actionsLeft: Int?
        let verdict: String?
        let reason: String?
    }
    
    struct Position: Codable {
        let latitude: Double
        let longitude: Double
        let altitude: Double
    }
    
    struct Attitude: Codable {
        let roll: Double
        let pitch: Double
        let yaw: Double
    }
    
    struct CommandResult: Codable {
        let success: Bool
        let stdout: String?
        let stderr: String?
        let returncode: Int?
        let error: String?
    }
    
    enum CodingKeys: String, CodingKey {
        case droneId, status, position, attitude, battery, armed, mode
        case lastUpdate, result, isOnline, ttl
        case voltage, batterySource, batteryWarnings, preflight, batteryBudget
    }
    
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        droneId = try container.decode(String.self, forKey: .droneId)
        status = try container.decodeIfPresent(String.self, forKey: .status)
        position = try container.decodeIfPresent(Position.self, forKey: .position)
        attitude = try container.decodeIfPresent(Attitude.self, forKey: .attitude)
        battery = try container.decodeIfPresent(Double.self, forKey: .battery)
        armed = try container.decodeIfPresent(Bool.self, forKey: .armed)
        mode = try container.decodeIfPresent(String.self, forKey: .mode)
        result = try container.decodeIfPresent(CommandResult.self, forKey: .result)
        isOnline = try container.decodeIfPresent(Bool.self, forKey: .isOnline)
        ttl = try container.decodeIfPresent(Int.self, forKey: .ttl)
        // try? so an older or newer daemon's shape never breaks decoding the rest.
        voltage = try? container.decodeIfPresent(Double.self, forKey: .voltage)
        batterySource = try? container.decodeIfPresent(String.self, forKey: .batterySource)
        batteryWarnings = try? container.decodeIfPresent([String].self, forKey: .batteryWarnings)
        preflight = try? container.decodeIfPresent(Preflight.self, forKey: .preflight)
        batteryBudget = try? container.decodeIfPresent(BatteryBudget.self, forKey: .batteryBudget)
        
        // Handle lastUpdate as either Double (epoch ms) or String (ISO8601)
        if let doubleValue = try? container.decodeIfPresent(Double.self, forKey: .lastUpdate) {
            lastUpdateMs = doubleValue
        } else if let stringValue = try? container.decodeIfPresent(String.self, forKey: .lastUpdate) {
            // Try ISO8601 parsing
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = formatter.date(from: stringValue) {
                lastUpdateMs = date.timeIntervalSince1970 * 1000
            } else {
                // Try without fractional seconds
                formatter.formatOptions = [.withInternetDateTime]
                if let date = formatter.date(from: stringValue) {
                    lastUpdateMs = date.timeIntervalSince1970 * 1000
                } else {
                    lastUpdateMs = nil
                }
            }
        } else {
            lastUpdateMs = nil
        }
    }
    
    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(droneId, forKey: .droneId)
        try container.encodeIfPresent(status, forKey: .status)
        try container.encodeIfPresent(position, forKey: .position)
        try container.encodeIfPresent(attitude, forKey: .attitude)
        try container.encodeIfPresent(battery, forKey: .battery)
        try container.encodeIfPresent(armed, forKey: .armed)
        try container.encodeIfPresent(mode, forKey: .mode)
        try container.encodeIfPresent(lastUpdateMs, forKey: .lastUpdate)
        try container.encodeIfPresent(result, forKey: .result)
        try container.encodeIfPresent(isOnline, forKey: .isOnline)
        try container.encodeIfPresent(ttl, forKey: .ttl)
        try container.encodeIfPresent(voltage, forKey: .voltage)
        try container.encodeIfPresent(batterySource, forKey: .batterySource)
        try container.encodeIfPresent(batteryWarnings, forKey: .batteryWarnings)
        try container.encodeIfPresent(preflight, forKey: .preflight)
        try container.encodeIfPresent(batteryBudget, forKey: .batteryBudget)
    }
    
    var lastUpdateDate: Date? {
        guard let lastUpdateMs else { return nil }
        return Date(timeIntervalSince1970: lastUpdateMs / 1000.0)
    }
}

/// Command sent to a drone
struct DroneCommand: Codable {
    let droneId: String
    let command: String
    
    enum CodingKeys: String, CodingKey {
        case droneId = "drone_id"
        case command
    }
}

/// Response from the command API
struct CommandResponse: Codable {
    let status: String
    let droneId: String
    let originalCommand: String
    let generatedCode: String
    
    enum CodingKeys: String, CodingKey {
        case status
        case droneId = "drone_id"
        case originalCommand = "original_command"
        case generatedCode = "generated_code"
    }
}

/// API response wrappers
struct DronesResponse: Codable {
    let drones: [Drone]
    let count: Int
}

struct DroneRegistrationResponse: Codable {
    let message: String
    let drone: Drone
}

struct DroneStatusResponse: Codable {
    let drone: Drone
    let status: DroneStatus
}

