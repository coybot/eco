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

/// AWS IoT Core transport (cloud mode, the unchanged default) - AWS SDK iOS
/// (Gen 1) with AWSIoTDataManager over a Cognito-authenticated WebSocket.
/// Extracted unmodified from MQTTService.swift when the GCS control-plane
/// switch was added; see MQTTClientProtocol.swift and CocoaMQTTClient.swift
/// for the counterpart used in GCS mode.
final class AWSIoTMQTTClient: NSObject, MQTTClientProtocol {

    var onStatusChange: ((MQTTConnectionState) -> Void)?
    var onMessage: ((String, Data) -> Void)?

    private var iotDataManager: AWSIoTDataManager?
    private var credentialsProvider: AWSCognitoCredentialsProvider?
    private let identityProvider = CustomIdentityProvider()

    override init() {
        super.init()
        setupAWSConfiguration()
    }

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
    func connect(idToken: String) async throws {
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
    }

    func disconnect() {
        iotDataManager?.disconnect()
        print("🔌 MQTT: Disconnected")
    }

    // MARK: - Subscriptions

    func subscribe(toTopic topic: String) {
        iotDataManager?.subscribe(
            toTopic: topic,
            qoS: .messageDeliveryAttemptedAtLeastOnce,
            messageCallback: { [weak self] payload in
                self?.onMessage?(topic, payload)
            }
        )
    }

    func unsubscribe(fromTopic topic: String) {
        iotDataManager?.unsubscribeTopic(topic)
    }

    // MARK: - Publish

    @discardableResult
    func publish(topic: String, jsonString: String, qos: MQTTQoSLevel) -> Bool {
        return iotDataManager?.publishString(
            jsonString,
            onTopic: topic,
            qoS: qos == .atLeastOnce ? .messageDeliveryAttemptedAtLeastOnce : .messageDeliveryAttemptedAtMostOnce
        ) ?? false
    }

    // MARK: - Private

    private func handleConnectionStatus(_ status: AWSIoTMQTTStatus) {
        switch status {
        case .connected:
            print("✅ MQTT: Connected")
            onStatusChange?(.connected)

        case .connecting:
            // Silent - too noisy
            break

        case .disconnected:
            onStatusChange?(.disconnected)

        case .connectionRefused:
            print("❌ MQTT: Connection refused")
            onStatusChange?(.connectionRefused)

        case .connectionError:
            print("⚠️ MQTT: Connection error (reconnecting...)")
            onStatusChange?(.connectionError)

        case .protocolError:
            print("❌ MQTT: Protocol error")
            onStatusChange?(.protocolError)

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
}
