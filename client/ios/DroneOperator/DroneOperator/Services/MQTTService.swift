import Foundation
import AWSIoT
import AWSCore

/// Custom identity provider for Cognito with social login tokens
class CustomIdentityProvider: NSObject, AWSIdentityProviderManager {
    var tokens: [String: String] = [:]
    
    func logins() -> AWSTask<NSDictionary> {
        return AWSTask(result: tokens as NSDictionary)
    }
}

/// MQTT service for real-time communication with drones via AWS IoT Core
/// Uses AWS SDK iOS (Gen 1) with AWSIoTDataManager for stable MQTT connections
@Observable
final class MQTTService {
    
    // MARK: - State
    
    private(set) var isConnected = false
    private(set) var connectionError: Error?
    
    /// Single source of truth for all drone statuses (shared across views)
    private(set) var droneStatuses: [String: DroneStatus] = [:]
    
    // MARK: - Private
    
    private var iotDataManager: AWSIoTDataManager?
    private var subscriptions: [String: [(Data) -> Void]] = [:]
    private var credentialsProvider: AWSCognitoCredentialsProvider?
    private let identityProvider = CustomIdentityProvider()
    private var lastConnectedTime: Date?
    private var pendingPublishes: [(topic: String, payload: [String: Any], silent: Bool)] = []
    private let reconnectGracePeriod: TimeInterval = 2.0  // Consider "connected" for 2s after disconnect
    
    // MARK: - Singleton
    
    static let shared = MQTTService()
    
    private init() {
        setupAWSConfiguration()
    }
    
    // MARK: - Setup
    
    private func setupAWSConfiguration() {
        // Set AWS SDK log level to warning only (reduce noise)
        AWSDDLog.sharedInstance.logLevel = .warning
        
        // Create credentials provider with custom identity provider for social logins
        credentialsProvider = AWSCognitoCredentialsProvider(
            regionType: .USWest2,
            identityPoolId: AWSConfig.identityPoolId,
            identityProviderManager: identityProvider
        )
        
        // Configure IoT endpoint
        let iotEndpoint = AWSEndpoint(
            urlString: "https://\(AWSConfig.iotEndpoint)"
        )
        
        // Create IoT configuration
        let iotConfiguration = AWSServiceConfiguration(
            region: .USWest2,
            endpoint: iotEndpoint,
            credentialsProvider: credentialsProvider
        )
        
        // Register IoT Data Manager
        AWSIoTDataManager.register(with: iotConfiguration!, forKey: "DroneIoT")
        iotDataManager = AWSIoTDataManager(forKey: "DroneIoT")
    }
    
    // MARK: - Connection
    
    /// Connect to AWS IoT Core using Cognito credentials
    func connect(withToken idToken: String) async throws {
        guard let dataManager = iotDataManager,
              let credentials = credentialsProvider else {
            throw MQTTError.connectionFailed
        }
        
        // Detect if it's a Google or Apple token and use appropriate provider
        let providerName = getTokenIssuer(from: idToken)
        
        // Set the token in our custom identity provider
        identityProvider.tokens = [providerName: idToken]
        
        // Only clear if we have a different token (avoid unnecessary re-auth)
        // This helps maintain session stability
        credentials.clearCredentials()
        
        // Get fresh credentials and identity ID
        let identityId: String = try await withCheckedThrowingContinuation { continuation in
            credentials.getIdentityId().continueWith { task in
                if let error = task.error {
                    print("❌ MQTT: Auth failed: \(error.localizedDescription)")
                    continuation.resume(throwing: error)
                } else if let id = task.result as? String {
                    print("🔑 MQTT: Cognito Identity ID: \(id)")
                    continuation.resume(returning: id)
                } else {
                    continuation.resume(throwing: MQTTError.connectionFailed)
                }
                return nil
            }
        }
        
        // Attach IoT policy to this identity (required for MQTT access)
        do {
            try await APIClient.shared.attachIoTPolicy(identityId: identityId)
            print("✅ MQTT: IoT policy attached")
        } catch {
            // Policy may already be attached, or API may fail - continue anyway
            print("⚠️ MQTT: IoT policy attach skipped: \(error.localizedDescription)")
        }
        
        // Connect using WebSocket with SigV4
        let clientId = "ios-\(UUID().uuidString.prefix(8))"
        
        let connected = dataManager.connectUsingWebSocket(
            withClientId: clientId,
            cleanSession: true
        ) { [weak self] status in
            DispatchQueue.main.async {
                self?.handleConnectionStatus(status)
            }
        }
        
        if !connected {
            print("❌ MQTT: Failed to connect")
            throw MQTTError.connectionFailed
        }
        
        // Wait for connection to establish
        try await Task.sleep(nanoseconds: 2_000_000_000) // 2 seconds
        
        if isConnected {
            // Resubscribe to existing topics
            for topic in subscriptions.keys {
                subscribeToTopic(topic)
            }
        }
    }
    
    /// Disconnect from MQTT broker
    func disconnect() {
        iotDataManager?.disconnect()
        isConnected = false
        subscriptions.removeAll()
        print("🔌 MQTT: Disconnected")
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
            subscribeToTopic(topic)
        }
    }
    
    /// Unsubscribe from a topic
    func unsubscribe(from topic: String) {
        subscriptions.removeValue(forKey: topic)
        
        if isConnected {
            iotDataManager?.unsubscribeTopic(topic)
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
        
        let success = iotDataManager?.publishString(
            jsonString,
            onTopic: topic,
            qoS: .messageDeliveryAttemptedAtLeastOnce
        ) ?? false
        
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
    
    private func subscribeToTopic(_ topic: String) {
        iotDataManager?.subscribe(
            toTopic: topic,
            qoS: .messageDeliveryAttemptedAtLeastOnce,
            messageCallback: { [weak self] payload in
                self?.handleMessage(topic: topic, payload: payload)
            }
        )
    }
    
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
    
    private func handleConnectionStatus(_ status: AWSIoTMQTTStatus) {
        switch status {
        case .connected:
            print("✅ MQTT: Connected")
            isConnected = true
            connectionError = nil
            
        case .connecting:
            // Silent - too noisy
            break
            
        case .disconnected:
            isConnected = false
            
        case .connectionRefused:
            print("❌ MQTT: Connection refused")
            isConnected = false
            connectionError = MQTTError.connectionFailed
            
        case .connectionError:
            // Only log periodically to reduce noise (SDK retries automatically)
            if !isConnected {
                // Already disconnected, suppress repeated errors
            } else {
                print("⚠️ MQTT: Connection error (reconnecting...)")
            }
            isConnected = false
            connectionError = MQTTError.connectionFailed
            
        case .protocolError:
            print("❌ MQTT: Protocol error")
            isConnected = false
            connectionError = MQTTError.connectionFailed
            
        default:
            break // Silent for unknown statuses
        }
    }
    
    /// Detect the issuer from a JWT token
    private func getTokenIssuer(from token: String) -> String {
        let parts = token.split(separator: ".")
        guard parts.count >= 2 else {
            return "cognito-idp.\(AWSConfig.region).amazonaws.com/\(AWSConfig.userPoolId)"
        }
        
        var base64 = String(parts[1])
        while base64.count % 4 != 0 {
            base64.append("=")
        }
        
        guard let data = Data(base64Encoded: base64),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let iss = json["iss"] as? String else {
            return "cognito-idp.\(AWSConfig.region).amazonaws.com/\(AWSConfig.userPoolId)"
        }
        
        // Extract the provider name from the issuer URL
        // Apple: https://appleid.apple.com -> appleid.apple.com
        // Google: https://accounts.google.com -> accounts.google.com
        if let url = URL(string: iss) {
            let host = url.host ?? ""
            let path = url.path
            if path.isEmpty || path == "/" {
                return host
            } else {
                return host + path
            }
        }
        
        return iss.replacingOccurrences(of: "https://", with: "")
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
