import Foundation

/// MQTT facade for real-time communication with drones - AWS IoT Core
/// (cloud, AWSIoTMQTTClient) or a local Ground Control Station's mosquitto
/// broker (control_plane: gcs, CocoaMQTTClient), chosen from
/// GCSSettings.shared.controlPlane. Everything below this class's transport
/// selection (subscription registry, drone-status cache, publish queueing,
/// topic-wildcard matching) is unchanged from before the GCS switch existed.
@Observable
final class MQTTService {

    // MARK: - State

    private(set) var isConnected = false
    private(set) var connectionError: Error?

    /// Single source of truth for all drone statuses (shared across views)
    private(set) var droneStatuses: [String: DroneStatus] = [:]

    // MARK: - Private

    private var client: MQTTClientProtocol
    private var subscriptions: [String: [(Data) -> Void]] = [:]
    private var lastConnectedTime: Date?
    private var pendingPublishes: [(topic: String, payload: [String: Any], silent: Bool)] = []
    private let reconnectGracePeriod: TimeInterval = 2.0  // Consider "connected" for 2s after disconnect

    // MARK: - Singleton

    static let shared = MQTTService()

    private init() {
        self.client = MQTTService.makeClient()
        wireClientCallbacks()
    }

    private static func makeClient() -> MQTTClientProtocol {
        GCSSettings.shared.isGCSMode ? CocoaMQTTClient() : AWSIoTMQTTClient()
    }

    private func wireClientCallbacks() {
        client.onStatusChange = { [weak self] status in
            DispatchQueue.main.async {
                self?.handleConnectionStatus(status)
            }
        }
        client.onMessage = { [weak self] topic, payload in
            self?.handleMessage(topic: topic, payload: payload)
        }
    }

    // MARK: - Connection

    /// Connect to the active control plane's MQTT broker. `idToken` is the
    /// Cognito id token for cloud mode; ignored for GCS mode (the client
    /// authenticates with GCSSettings.shared.pairingToken instead).
    func connect(withToken idToken: String) async throws {
        // If the control plane changed since the last connect (e.g. the user
        // just switched it in Settings), rebuild the underlying client.
        let wantsGCS = GCSSettings.shared.isGCSMode
        let hasGCSClient = client is CocoaMQTTClient
        if wantsGCS != hasGCSClient {
            client.disconnect()
            client = MQTTService.makeClient()
            wireClientCallbacks()
        }

        try await client.connect(idToken: idToken)

        if isConnected {
            // Resubscribe to existing topics
            for topic in subscriptions.keys {
                client.subscribe(toTopic: topic)
            }
        }
    }

    /// Disconnect from MQTT broker
    func disconnect() {
        client.disconnect()
        isConnected = false
        subscriptions.removeAll()
    }

    // MARK: - Subscriptions

    /// Subscribe to a topic for real-time updates
    func subscribe(to topic: String, handler: @escaping (Data) -> Void) {
        if subscriptions[topic] == nil {
            subscriptions[topic] = []
        }
        subscriptions[topic]?.append(handler)

        // If already connected, subscribe immediately
        if isConnected {
            client.subscribe(toTopic: topic)
        }
    }

    /// Unsubscribe from a topic
    func unsubscribe(from topic: String) {
        subscriptions.removeValue(forKey: topic)

        if isConnected {
            client.unsubscribe(fromTopic: topic)
        }
    }

    // MARK: - Drone Status (Single Source of Truth)

    /// Update drone status - called from API responses or MQTT messages
    func updateDroneStatus(droneId: String, status: DroneStatus) {
        droneStatuses[droneId] = status
    }

    /// Get drone status
    func getDroneStatus(droneId: String) -> DroneStatus? {
        return droneStatuses[droneId]
    }

    /// Check if a drone is online based on cached status
    func isDroneOnline(droneId: String) -> Bool {
        guard let status = droneStatuses[droneId] else { return false }

        // Use backend-calculated isOnline if available
        if let backendOnline = status.isOnline {
            return backendOnline
        }

        // Prefer backend TTL if provided
        if let ttl = status.ttl {
            return TimeInterval(ttl) > Date().timeIntervalSince1970
        }

        // Fallback: check lastUpdate timestamp locally
        guard let lastUpdate = status.lastUpdateDate else { return false }
        return Date().timeIntervalSince(lastUpdate) < 30
    }

    /// Check if we're effectively connected (or within grace period after disconnect)
    private var isEffectivelyConnected: Bool {
        if isConnected { return true }

        // Allow publishes during brief disconnections (SDK reconnecting)
        if let lastConnected = lastConnectedTime,
           Date().timeIntervalSince(lastConnected) < reconnectGracePeriod {
            return true
        }
        return false
    }

    /// Publish a message to a topic
    func publish(to topic: String, payload: [String: Any], silent: Bool = false) {
        // Try to publish if connected or within grace period
        if isEffectivelyConnected {
            doPublish(topic: topic, payload: payload, silent: silent)
        } else {
            // Queue for retry when reconnected
            if !silent {
                print("⏳ MQTT: Queuing publish to \(topic) (reconnecting...)")
            }
            pendingPublishes.append((topic, payload, silent))

            // Limit queue size
            if pendingPublishes.count > 10 {
                pendingPublishes.removeFirst()
            }
        }
    }

    private func doPublish(topic: String, payload: [String: Any], silent: Bool) {
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let jsonString = String(data: data, encoding: .utf8) else {
            print("❌ MQTT: Failed to serialize payload")
            return
        }

        let success = client.publish(topic: topic, jsonString: jsonString, qos: .atLeastOnce)

        if !silent {
            if success {
                print("📤 MQTT: Published to \(topic)")
            } else {
                print("❌ MQTT: Publish failed to \(topic)")
            }
        }
    }

    /// Flush any pending publishes after reconnection
    private func flushPendingPublishes() {
        guard isConnected, !pendingPublishes.isEmpty else { return }

        let pending = pendingPublishes
        pendingPublishes.removeAll()

        print("📤 MQTT: Flushing \(pending.count) queued messages")
        for (topic, payload, silent) in pending {
            doPublish(topic: topic, payload: payload, silent: silent)
        }
    }

    // MARK: - Private Methods

    private func handleMessage(topic: String, payload: Data) {
        // Only log chat messages, not frequent status updates
        if !topic.contains("/status") {
            print("📨 MQTT: Received on \(topic)")
            if let jsonString = String(data: payload, encoding: .utf8) {
                let preview = jsonString.count > 600 ? String(jsonString.prefix(600)) + "…" : jsonString
                print("📨 MQTT payload: \(preview)")
            } else {
                print("📨 MQTT payload: \(payload.count) bytes (non-utf8)")
            }
        }

        // Find matching handlers (support wildcards)
        for (pattern, handlers) in subscriptions {
            if topicMatches(pattern: pattern, topic: topic) {
                for handler in handlers {
                    DispatchQueue.main.async {
                        handler(payload)
                    }
                }
            }
        }
    }

    private func handleConnectionStatus(_ status: MQTTConnectionState) {
        switch status {
        case .connected:
            isConnected = true
            connectionError = nil
            lastConnectedTime = Date()
            flushPendingPublishes()

        case .connecting:
            break

        case .disconnected:
            isConnected = false

        case .connectionRefused, .connectionError, .protocolError:
            isConnected = false
            connectionError = MQTTError.connectionFailed
        }
    }

    /// Check if topic matches a subscription pattern (supports + and # wildcards)
    private func topicMatches(pattern: String, topic: String) -> Bool {
        let patternParts = pattern.split(separator: "/")
        let topicParts = topic.split(separator: "/")

        var patternIndex = 0
        var topicIndex = 0

        while patternIndex < patternParts.count && topicIndex < topicParts.count {
            let patternPart = String(patternParts[patternIndex])

            if patternPart == "#" {
                return true // # matches everything after
            } else if patternPart == "+" {
                // + matches single level
                patternIndex += 1
                topicIndex += 1
            } else if patternPart == String(topicParts[topicIndex]) {
                patternIndex += 1
                topicIndex += 1
            } else {
                return false
            }
        }

        return patternIndex == patternParts.count && topicIndex == topicParts.count
    }
}

// MARK: - Errors

enum MQTTError: LocalizedError {
    case invalidEndpoint
    case connectionFailed
    case notConnected

    var errorDescription: String? {
        switch self {
        case .invalidEndpoint:
            return "Invalid MQTT endpoint"
        case .connectionFailed:
            return "Failed to connect to MQTT broker"
        case .notConnected:
            return "Not connected to MQTT broker"
        }
    }
}
