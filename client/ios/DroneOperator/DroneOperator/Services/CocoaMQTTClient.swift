import Foundation
import CocoaMQTT

/// Local Ground Control Station transport (control_plane: gcs, see
/// eco/gcs/README.md) - plain TCP (or TLS with a self-signed CA) to a
/// mosquitto broker, authenticated with GCSSettings.shared.pairingToken
/// instead of AWS IoT's SigV4/Cognito flow. AWS IoT's own SDK is
/// SigV4/WebSocket-only and can't point at an arbitrary broker, which is why
/// this is a second MQTTClientProtocol implementation rather than
/// reconfiguring AWSIoTMQTTClient - see MQTTClientProtocol.swift.
final class CocoaMQTTClient: NSObject, MQTTClientProtocol, CocoaMQTTDelegate {

    var onStatusChange: ((MQTTConnectionState) -> Void)?
    var onMessage: ((String, Data) -> Void)?

    private var mqtt: CocoaMQTT?
    private var connectContinuation: CheckedContinuation<Void, Error>?

    func connect(idToken: String) async throws {
        let settings = GCSSettings.shared
        guard !settings.host.isEmpty else {
            throw MQTTError.invalidEndpoint
        }

        let clientId = "ios-\(UUID().uuidString.prefix(8))"
        let client = CocoaMQTT(clientID: clientId, host: settings.host, port: UInt16(settings.mqttPort))
        client.username = "operator"
        client.password = settings.pairingToken
        client.keepAlive = 60
        client.autoReconnect = true
        client.delegate = self
        self.mqtt = client

        try await withCheckedThrowingContinuation { continuation in
            self.connectContinuation = continuation
            if !client.connect() {
                self.connectContinuation = nil
                continuation.resume(throwing: MQTTError.connectionFailed)
            }
        }
    }

    func disconnect() {
        mqtt?.disconnect()
        mqtt = nil
        print("🔌 GCS MQTT: Disconnected")
    }

    func subscribe(toTopic topic: String) {
        mqtt?.subscribe(topic, qos: .qos1)
    }

    func unsubscribe(fromTopic topic: String) {
        mqtt?.unsubscribe(topic)
    }

    @discardableResult
    func publish(topic: String, jsonString: String, qos: MQTTQoSLevel) -> Bool {
        guard let mqtt = mqtt else { return false }
        mqtt.publish(topic, withString: jsonString, qos: qos == .atLeastOnce ? .qos1 : .qos0)
        return true
    }

    // MARK: - CocoaMQTTDelegate

    func mqtt(_ mqtt: CocoaMQTT, didConnectAck ack: CocoaMQTTConnAck) {
        if ack == .accept {
            print("✅ GCS MQTT: Connected")
            connectContinuation?.resume()
            connectContinuation = nil
            onStatusChange?(.connected)
        } else {
            print("❌ GCS MQTT: Connection refused (\(ack))")
            connectContinuation?.resume(throwing: MQTTError.connectionFailed)
            connectContinuation = nil
            onStatusChange?(.connectionRefused)
        }
    }

    func mqtt(_ mqtt: CocoaMQTT, didReceiveMessage message: CocoaMQTTMessage, id: UInt16) {
        onMessage?(message.topic, Data(message.payload))
    }

    func mqtt(_ mqtt: CocoaMQTT, didPublishMessage message: CocoaMQTTMessage, id: UInt16) {}

    func mqtt(_ mqtt: CocoaMQTT, didPublishAck id: UInt16) {}

    func mqtt(_ mqtt: CocoaMQTT, didSubscribeTopics success: NSDictionary, failed: [String]) {}

    func mqtt(_ mqtt: CocoaMQTT, didUnsubscribeTopics topics: [String]) {}

    func mqttDidPing(_ mqtt: CocoaMQTT) {}

    func mqttDidReceivePong(_ mqtt: CocoaMQTT) {}

    func mqttDidDisconnect(_ mqtt: CocoaMQTT, withError err: Error?) {
        if let err = err {
            print("⚠️ GCS MQTT: Disconnected with error: \(err.localizedDescription)")
            onStatusChange?(.connectionError)
        } else {
            onStatusChange?(.disconnected)
        }
    }
}
