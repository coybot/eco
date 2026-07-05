import Foundation
import AWSIoT
import AWSCore

/// Custom identity provider for Cognito with the rover's own (Cognito email/password) token.
class CustomIdentityProvider: NSObject, AWSIdentityProviderManager {
    var tokens: [String: String] = [:]

    func logins() -> AWSTask<NSDictionary> {
        return AWSTask(result: tokens as NSDictionary)
    }
}

/// MQTT client for AWS IoT Core — forked from DroneOperator's MQTTService.swift.
///
/// Deliberately reuses the existing `drone/{id}/...` topic namespace rather than adding a
/// new `rover/*` one: the deployed IoT policy (`drone-policy-dev`) only grants
/// publish/subscribe on `drone/*` topics, and the backend already treats "drone" as a
/// generic vehicle slot — `handler.py` already branches on a `vehicleType: rover` field
/// for a different (Jetson-based) ground rover. Publishing WAVE ROVER telemetry to
/// `drone/{roverId}/status` needs zero IoT policy or Lambda changes: `DroneStatusRule`
/// (SELECT * FROM 'drone/+/status') stores whatever JSON we send, keyed by topic(2).
///
/// Dropped from the original: the `droneStatuses` cache/`isDroneOnline` logic — that's
/// fleet-of-drones UI state RoverOperator doesn't have a use for yet. Kept: generic
/// connect/publish/subscribe, since Phase-3 remote teleop override needs subscribe too.
@Observable
@MainActor
final class MQTTService {

    private(set) var isConnected = false
    private(set) var connectionError: Error?

    private var iotDataManager: AWSIoTDataManager?
    private var subscriptions: [String: [(Data) -> Void]] = [:]
    private var credentialsProvider: AWSCognitoCredentialsProvider?
    private let identityProvider = CustomIdentityProvider()
    private var lastConnectedTime: Date?
    private var pendingPublishes: [(topic: String, payload: [String: Any], silent: Bool)] = []
    private let reconnectGracePeriod: TimeInterval = 2.0

    static let shared = MQTTService()

    private init() {
        setupAWSConfiguration()
    }

    private func setupAWSConfiguration() {
        AWSDDLog.sharedInstance.logLevel = .warning

        credentialsProvider = AWSCognitoCredentialsProvider(
            regionType: .USWest2,
            identityPoolId: AWSConfig.identityPoolId,
            identityProviderManager: identityProvider
        )

        let iotEndpoint = AWSEndpoint(urlString: "https://\(AWSConfig.iotEndpoint)")
        let iotConfiguration = AWSServiceConfiguration(
            region: .USWest2,
            endpoint: iotEndpoint,
            credentialsProvider: credentialsProvider
        )

        AWSIoTDataManager.register(with: iotConfiguration!, forKey: "RoverIoT")
        iotDataManager = AWSIoTDataManager(forKey: "RoverIoT")
    }

    // MARK: - Connection

    func connect(withToken idToken: String) async throws {
        guard let dataManager = iotDataManager,
              let credentials = credentialsProvider else {
            throw MQTTError.connectionFailed
        }

        let providerName = getTokenIssuer(from: idToken)
        identityProvider.tokens = [providerName: idToken]
        credentials.clearCredentials()

        let identityId: String = try await withCheckedThrowingContinuation { continuation in
            credentials.getIdentityId().continueWith { task in
                if let error = task.error {
                    continuation.resume(throwing: error)
                } else if let id = task.result as? String {
                    continuation.resume(returning: id)
                } else {
                    continuation.resume(throwing: MQTTError.connectionFailed)
                }
                return nil
            }
        }

        do {
            try await APIClient.shared.attachIoTPolicy(identityId: identityId, idToken: idToken)
        } catch {
            // Policy may already be attached, or the API may be briefly unavailable —
            // MQTT connect can still succeed if it was attached on a previous run.
            AppLogger.nav.error("MQTT: IoT policy attach skipped: \(error.localizedDescription)")
        }

        let clientId = "ios-rover-\(UUID().uuidString.prefix(8))"
        let connected = dataManager.connectUsingWebSocket(
            withClientId: clientId,
            cleanSession: true
        ) { [weak self] status in
            DispatchQueue.main.async { self?.handleConnectionStatus(status) }
        }

        if !connected {
            throw MQTTError.connectionFailed
        }

        try await Task.sleep(nanoseconds: 2_000_000_000)

        if isConnected {
            for topic in subscriptions.keys { subscribeToTopic(topic) }
        }
    }

    func disconnect() {
        iotDataManager?.disconnect()
        isConnected = false
        subscriptions.removeAll()
    }

    // MARK: - Subscriptions

    func subscribe(to topic: String, handler: @escaping (Data) -> Void) {
        subscriptions[topic, default: []].append(handler)
        if isConnected { subscribeToTopic(topic) }
    }

    func unsubscribe(from topic: String) {
        subscriptions.removeValue(forKey: topic)
        if isConnected { iotDataManager?.unsubscribeTopic(topic) }
    }

    // MARK: - Publish

    private var isEffectivelyConnected: Bool {
        if isConnected { return true }
        if let lastConnected = lastConnectedTime,
           Date().timeIntervalSince(lastConnected) < reconnectGracePeriod {
            return true
        }
        return false
    }

    /// Publish a JSON-encodable payload. `silent` suppresses logging for high-frequency
    /// telemetry so it doesn't drown out other logs.
    func publish(to topic: String, payload: [String: Any], silent: Bool = true) {
        if isEffectivelyConnected {
            doPublish(topic: topic, payload: payload, silent: silent)
        } else {
            pendingPublishes.append((topic, payload, silent))
            if pendingPublishes.count > 10 { pendingPublishes.removeFirst() }
        }
    }

    private func doPublish(topic: String, payload: [String: Any], silent: Bool) {
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let jsonString = String(data: data, encoding: .utf8) else { return }
        _ = iotDataManager?.publishString(jsonString, onTopic: topic, qoS: .messageDeliveryAttemptedAtLeastOnce)
    }

    private func flushPendingPublishes() {
        guard isConnected, !pendingPublishes.isEmpty else { return }
        let pending = pendingPublishes
        pendingPublishes.removeAll()
        for (topic, payload, silent) in pending { doPublish(topic: topic, payload: payload, silent: silent) }
    }

    // MARK: - Private

    private func subscribeToTopic(_ topic: String) {
        iotDataManager?.subscribe(
            toTopic: topic,
            qoS: .messageDeliveryAttemptedAtLeastOnce,
            messageCallback: { [weak self] payload in self?.handleMessage(topic: topic, payload: payload) }
        )
    }

    private func handleMessage(topic: String, payload: Data) {
        for (pattern, handlers) in subscriptions where topicMatches(pattern: pattern, topic: topic) {
            for handler in handlers { DispatchQueue.main.async { handler(payload) } }
        }
    }

    private func handleConnectionStatus(_ status: AWSIoTMQTTStatus) {
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
        default:
            break
        }
    }

    private func getTokenIssuer(from token: String) -> String {
        let parts = token.split(separator: ".")
        guard parts.count >= 2 else {
            return "cognito-idp.\(AWSConfig.region).amazonaws.com/\(AWSConfig.userPoolId)"
        }
        var base64 = String(parts[1])
        while base64.count % 4 != 0 { base64.append("=") }
        guard let data = Data(base64Encoded: base64),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let iss = json["iss"] as? String else {
            return "cognito-idp.\(AWSConfig.region).amazonaws.com/\(AWSConfig.userPoolId)"
        }
        if let url = URL(string: iss) {
            let host = url.host ?? ""
            let path = url.path
            return path.isEmpty || path == "/" ? host : host + path
        }
        return iss.replacingOccurrences(of: "https://", with: "")
    }

    private func topicMatches(pattern: String, topic: String) -> Bool {
        let patternParts = pattern.split(separator: "/")
        let topicParts = topic.split(separator: "/")
        var pi = 0, ti = 0
        while pi < patternParts.count && ti < topicParts.count {
            let p = String(patternParts[pi])
            if p == "#" { return true }
            if p == "+" || p == String(topicParts[ti]) { pi += 1; ti += 1 } else { return false }
        }
        return pi == patternParts.count && ti == topicParts.count
    }
}

enum MQTTError: LocalizedError {
    case connectionFailed
    var errorDescription: String? { "Failed to connect to MQTT broker" }
}
