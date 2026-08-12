import Foundation

/// Which control plane the app talks to: AWS (cloud, the unchanged default)
/// or a local Ground Control Station (see eco/gcs/README.md) - a PC, Mac, or
/// NVIDIA Thor on the same network running its own models instead of Bedrock.
enum ControlPlane: String, Codable {
    case cloud
    case gcs
}

/// Runtime-configurable GCS connection settings, persisted across launches
/// (host/port in UserDefaults, the pairing token in the Keychain via the
/// existing KeychainHelper - see AuthService.swift). Cloud mode is
/// unaffected: it keeps using the compiled AWSConfig constants exactly as
/// before. Edited from Views/Settings/ControlPlaneSettingsView.swift.
@Observable
final class GCSSettings {
    static let shared = GCSSettings()

    private enum Keys {
        static let controlPlane = "gcs.controlPlane"
        static let host = "gcs.host"
        static let httpPort = "gcs.httpPort"
        static let mqttPort = "gcs.mqttPort"
        static let pairingTokenKeychainKey = "gcs.pairingToken"
    }

    var controlPlane: ControlPlane {
        didSet { UserDefaults.standard.set(controlPlane.rawValue, forKey: Keys.controlPlane) }
    }
    var host: String {
        didSet { UserDefaults.standard.set(host, forKey: Keys.host) }
    }
    /// The GCS's HTTP API port (gcs/config.yaml's http.port, default 8080).
    var httpPort: Int {
        didSet { UserDefaults.standard.set(httpPort, forKey: Keys.httpPort) }
    }
    /// The GCS's mosquitto broker port (gcs/config.yaml's mqtt.port, default 1883).
    var mqttPort: Int {
        didSet { UserDefaults.standard.set(mqttPort, forKey: Keys.mqttPort) }
    }
    var pairingToken: String {
        didSet {
            if let data = pairingToken.data(using: .utf8) {
                KeychainHelper.save(data, forKey: Keys.pairingTokenKeychainKey)
            }
        }
    }

    var isGCSMode: Bool { controlPlane == .gcs }

    /// The HTTP API base URL for the active control plane - AWSConfig's
    /// compiled endpoint for cloud, or this box's own host:httpPort for GCS.
    var baseURLString: String {
        switch controlPlane {
        case .cloud:
            return AWSConfig.apiEndpoint
        case .gcs:
            return "http://\(host):\(httpPort)"
        }
    }

    private init() {
        let storedPlane = UserDefaults.standard.string(forKey: Keys.controlPlane)
            .flatMap(ControlPlane.init(rawValue:))
        // Default to GCS, not cloud: a fresh clone's AWSConfig.swift ships with
        // placeholder endpoints (see Config/AWSConfig.example.swift), so .cloud
        // would silently fail out of the box. GCS needs no AWS account at all.
        self.controlPlane = storedPlane ?? .gcs
        // Demo build: pre-filled to the local GCS stand-in on the Mac so the
        // app is usable out of the box with no Settings entry (still editable
        // on the Settings screen; overridden by GCS_TEST_* env below).
        self.host = UserDefaults.standard.string(forKey: Keys.host) ?? "10.10.10.38"
        let storedHTTPPort = UserDefaults.standard.integer(forKey: Keys.httpPort)
        self.httpPort = storedHTTPPort == 0 ? 8080 : storedHTTPPort
        let storedMQTTPort = UserDefaults.standard.integer(forKey: Keys.mqttPort)
        self.mqttPort = storedMQTTPort == 0 ? 1883 : storedMQTTPort
        if let data = KeychainHelper.load(forKey: Keys.pairingTokenKeychainKey),
           let token = String(data: data, encoding: .utf8) {
            self.pairingToken = token
        } else {
            self.pairingToken = "kivvgBdqcBzGDKf4rfAusUzqk7g4JVpNRItuy0vYKyE"
        }

        // Test hook: allow a launch environment to pre-configure GCS mode so an
        // automated end-to-end run (e.g. the simulator) can point at a local GCS
        // without driving the Settings UI. Inert in production — these env vars
        // are never set by a normal launch. (SIMCTL_CHILD_GCS_TEST_* on the
        // simctl launch command surfaces here as GCS_TEST_*.)
        let env = ProcessInfo.processInfo.environment
        if let h = env["GCS_TEST_HOST"], !h.isEmpty {
            self.controlPlane = .gcs
            self.host = h
            if let p = env["GCS_TEST_HTTP_PORT"], let n = Int(p) { self.httpPort = n }
            if let p = env["GCS_TEST_MQTT_PORT"], let n = Int(p) { self.mqttPort = n }
            if let t = env["GCS_TEST_TOKEN"], !t.isEmpty { self.pairingToken = t }
        }
    }
}
