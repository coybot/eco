import Foundation

/// WiFi network configuration that the drone can connect to.
///
/// Note: In the current implementation, `password` is stored in DynamoDB and is delivered
/// to the drone via AWS IoT Thing Shadow desired state (offline-safe). Treat as sensitive.
struct WifiNetwork: Identifiable, Codable, Hashable {
    var id: String
    var ssid: String
    var password: String
    var priority: Int
    var enabled: Bool
    
    init(
        id: String = UUID().uuidString,
        ssid: String,
        password: String,
        priority: Int = 0,
        enabled: Bool = true
    ) {
        self.id = id
        self.ssid = ssid
        self.password = password
        self.priority = priority
        self.enabled = enabled
    }
}

