import Foundation

/// Connection lifecycle states reported by an MQTTClientProtocol
/// implementation, independent of the underlying transport (AWS IoT Core's
/// AWSIoTMQTTStatus enum, or CocoaMQTT's connect ack / disconnect callbacks).
enum MQTTConnectionState {
    case connecting
    case connected
    case disconnected
    case connectionRefused
    case connectionError
    case protocolError
}

enum MQTTQoSLevel {
    case atMostOnce
    case atLeastOnce
}

/// Abstraction over the underlying MQTT transport, so MQTTService's shared
/// logic (subscription registry, drone-status cache, publish queueing,
/// topic-wildcard matching - none of which changed) works identically
/// whether the transport is AWS IoT Core (cloud, AWSIoTMQTTClient.swift) or a
/// local Ground Control Station's mosquitto broker (CocoaMQTTClient.swift,
/// GCS mode - see eco/gcs/README.md). MQTTService picks the implementation
/// based on GCSSettings.shared.controlPlane.
protocol MQTTClientProtocol: AnyObject {
    var onStatusChange: ((MQTTConnectionState) -> Void)? { get set }
    var onMessage: ((_ topic: String, _ payload: Data) -> Void)? { get set }

    /// idToken is the Cognito id token for cloud mode; pass "" for GCS mode,
    /// which authenticates with GCSSettings.shared.pairingToken instead.
    func connect(idToken: String) async throws
    func disconnect()
    func subscribe(toTopic topic: String)
    func unsubscribe(fromTopic topic: String)
    @discardableResult
    func publish(topic: String, jsonString: String, qos: MQTTQoSLevel) -> Bool
}
