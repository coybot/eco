import Foundation
import Network
import NetworkExtension
import SystemConfiguration.CaptiveNetwork

/// Service for communicating with drone during WiFi provisioning
@Observable
final class DroneSetupService {
    
    // MARK: - State
    
    var isConnectedToDrone = false
    var droneInfo: DroneSetupInfo?
    var setupProgress: SetupProgress = .notStarted
    var error: SetupError?
    
    // MARK: - Private
    
    private let droneIP = "192.168.4.1"
    private let dronePort = 80
    private var monitor: NWPathMonitor?
    
    // MARK: - Types
    
    struct DroneSetupInfo: Codable {
        let droneId: String
        let macAddress: String?
        let hotspotName: String?
        
        enum CodingKeys: String, CodingKey {
            case droneId = "drone_id"
            case macAddress = "mac_address"
            case hotspotName = "hotspot_name"
        }
    }
    
    enum SetupProgress: Equatable {
        case notStarted
        case waitingForHotspot
        case connectedToHotspot
        case sendingCredentials
        case waitingForDroneConnection
        case registeringWithCloud
        case completed
        case failed(String)
    }
    
    enum SetupError: LocalizedError {
        case notConnectedToDrone
        case droneConnectionFailed(String)
        case credentialsFailed(String)
        case registrationFailed(String)
        case timeout
        
        var errorDescription: String? {
            switch self {
            case .notConnectedToDrone:
                return "Not connected to drone's hotspot"
            case .droneConnectionFailed(let msg):
                return "Failed to connect to drone: \(msg)"
            case .credentialsFailed(let msg):
                return "Failed to send WiFi credentials: \(msg)"
            case .registrationFailed(let msg):
                return "Failed to register drone: \(msg)"
            case .timeout:
                return "Setup timed out"
            }
        }
    }
    
    // MARK: - Public API
    
    /// Start monitoring for drone hotspot connection
    func startMonitoring() {
        setupProgress = .waitingForHotspot
        
        monitor = NWPathMonitor(requiredInterfaceType: .wifi)
        monitor?.pathUpdateHandler = { [weak self] path in
            Task { @MainActor in
                self?.checkDroneConnection()
            }
        }
        monitor?.start(queue: DispatchQueue.global(qos: .background))
        
        // Initial check
        Task {
            await checkDroneConnection()
        }
    }

    /// Join the drone's access point and remember it
    func joinDroneAccessPoint(ssid: String, password: String) async throws {
        let config = NEHotspotConfiguration(ssid: ssid, passphrase: password, isWEP: false)
        config.joinOnce = false
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            NEHotspotConfigurationManager.shared.apply(config) { error in
                if let error = error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume(returning: ())
                }
            }
        }
    }
    
    /// Stop monitoring
    func stopMonitoring() {
        monitor?.cancel()
        monitor = nil
    }
    
    /// Check if currently connected to drone's hotspot
    @MainActor
    func checkDroneConnection() {
        // Try to reach the drone's provisioning endpoint
        Task {
            do {
                let info = try await fetchDroneInfo()
                self.droneInfo = info
                self.isConnectedToDrone = true
                self.setupProgress = .connectedToHotspot
            } catch {
                self.isConnectedToDrone = false
                if self.setupProgress == .connectedToHotspot {
                    self.setupProgress = .waitingForHotspot
                }
            }
        }
    }
    
    /// Fetch drone info from local API
    func fetchDroneInfo() async throws -> DroneSetupInfo {
        guard let url = URL(string: "http://\(droneIP)/info") else {
            throw SetupError.droneConnectionFailed("Invalid URL")
        }
        
        var request = URLRequest(url: url)
        request.timeoutInterval = 5
        
        let (data, response) = try await URLSession.shared.data(for: request)
        
        guard let httpResponse = response as? HTTPURLResponse,
              httpResponse.statusCode == 200 else {
            throw SetupError.droneConnectionFailed("Bad response")
        }
        
        return try JSONDecoder().decode(DroneSetupInfo.self, from: data)
    }
    
    /// Send WiFi credentials to the drone
    ///
    /// `setupToken` is the drone's hotspot passphrase: the daemon uses the same
    /// secret for the AP and for authorizing /configure, so joining the network
    /// is not by itself proof that you are allowed to reconfigure the drone.
    func configureWiFi(ssid: String, password: String, userId: String, setupToken: String) async throws -> String {
        guard isConnectedToDrone else {
            throw SetupError.notConnectedToDrone
        }
        
        await MainActor.run {
            setupProgress = .sendingCredentials
        }
        
        guard let url = URL(string: "http://\(droneIP)/configure") else {
            throw SetupError.credentialsFailed("Invalid URL")
        }
        
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(setupToken)", forHTTPHeaderField: "Authorization")
        request.timeoutInterval = 10
        
        let body: [String: String] = [
            "ssid": ssid,
            "password": password,
            "user_id": userId
        ]
        
        request.httpBody = try JSONEncoder().encode(body)
        
        let (data, response) = try await URLSession.shared.data(for: request)
        
        guard let httpResponse = response as? HTTPURLResponse else {
            throw SetupError.credentialsFailed("No response")
        }
        
        guard httpResponse.statusCode != 401 else {
            throw SetupError.credentialsFailed("The drone rejected the setup passphrase. Check it against the passphrase printed on the drone.")
        }
        
        guard httpResponse.statusCode == 200 else {
            if let errorJson = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let errorMsg = errorJson["error"] as? String {
                throw SetupError.credentialsFailed(errorMsg)
            }
            throw SetupError.credentialsFailed("HTTP \(httpResponse.statusCode)")
        }
        
        // Parse response to get drone ID
        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let droneId = json["drone_id"] as? String {
            
            await MainActor.run {
                setupProgress = .waitingForDroneConnection
            }
            
            return droneId
        }
        
        throw SetupError.credentialsFailed("No drone ID in response")
    }
    
    /// Complete setup - register drone with cloud
    func completeSetup(droneId: String, droneName: String, apiClient: APIClient) async throws -> Drone {
        await MainActor.run {
            setupProgress = .registeringWithCloud
        }
        
        // Wait a moment for drone to connect to WiFi
        try await Task.sleep(nanoseconds: 3_000_000_000) // 3 seconds
        
        // Register with cloud
        let drone = try await apiClient.registerDrone(droneId: droneId, name: droneName)
        
        await MainActor.run {
            setupProgress = .completed
        }
        
        return drone
    }
    
    /// Get current WiFi SSID (for auto-fill)
    func getCurrentWiFiSSID() -> String? {
        // Note: This requires special entitlements on iOS 14+
        // The user will need to enter SSID manually in most cases
        return nil
    }
}

// MARK: - WiFi Credentials Storage

/// Securely store WiFi credentials temporarily during setup
actor WiFiCredentialsStore {
    static let shared = WiFiCredentialsStore()
    
    private var ssid: String?
    private var password: String?
    
    func store(ssid: String, password: String) {
        self.ssid = ssid
        self.password = password
    }
    
    func retrieve() -> (ssid: String, password: String)? {
        guard let ssid = ssid, let password = password else { return nil }
        return (ssid, password)
    }
    
    func clear() {
        ssid = nil
        password = nil
    }
}

