import Foundation

/// HTTP client for the Drone API
@Observable
final class APIClient {
    
    // MARK: - State
    
    var isLoading = false
    var lastError: APIError?
    
    // MARK: - Dependencies
    
    private let authService: AuthService
    private let baseURL: URL
    private let decoder: JSONDecoder
    
    // MARK: - Singleton
    
    static let shared = APIClient()
    
    private init(authService: AuthService = .shared) {
        self.authService = authService
        guard let url = URL(string: AWSConfig.apiEndpoint) else {
            fatalError("Invalid API endpoint URL in AWSConfig: \(AWSConfig.apiEndpoint)")
        }
        self.baseURL = url
        self.decoder = JSONDecoder()
        self.decoder.dateDecodingStrategy = .iso8601
    }
    
    // MARK: - Drone Management
    
    /// List all drones registered to the current user
    func listDrones() async throws -> [Drone] {
        let response: DronesResponse = try await request(
            method: "GET",
            path: "/drones"
        )
        return response.drones
    }
    
    /// Register a new drone
    func registerDrone(droneId: String, name: String) async throws -> Drone {
        let body = RegisterDroneRequest(droneId: droneId, name: name)
        let response: DroneRegistrationResponse = try await request(
            method: "POST",
            path: "/drones",
            body: body
        )
        return response.drone
    }
    
    /// Unregister a drone
    func deleteDrone(droneId: String) async throws {
        let _: EmptyResponse = try await request(
            method: "DELETE",
            path: "/drones/\(droneId)"
        )
    }
    
    /// Get the last known status of a drone
    func getDroneStatus(droneId: String) async throws -> DroneStatusResponse {
        return try await request(
            method: "GET",
            path: "/drones/\(droneId)/status"
        )
    }

    /// Update drone fields (currently: name)
    func patchDroneName(droneId: String, name: String) async throws -> Drone {
        let body = PatchDroneRequest(name: name)
        let response: DroneUpdateResponse = try await request(
            method: "PATCH",
            path: "/drones/\(droneId)",
            body: body
        )
        return response.drone
    }
    
    // MARK: - Battery Configuration
    
    /// Get battery configuration for a drone.
    func getBatteryConfig(droneId: String) async throws -> BatteryConfigGetResponse {
        return try await request(
            method: "GET",
            path: "/drones/\(droneId)/battery-config"
        )
    }
    
    /// Update battery configuration for a drone.
    func putBatteryConfig(droneId: String, config: BatteryConfig) async throws -> BatteryConfigPutResponse {
        return try await request(
            method: "PUT",
            path: "/drones/\(droneId)/battery-config",
            body: config
        )
    }
    
    // MARK: - WiFi Configuration
    
    /// Get WiFi networks configured for a drone.
    func getDroneWifi(droneId: String) async throws -> DroneWifiGetResponse {
        return try await request(
            method: "GET",
            path: "/drones/\(droneId)/wifi"
        )
    }
    
    /// Replace the full WiFi networks list for a drone (idempotent).
    func putDroneWifi(droneId: String, networks: [WifiNetwork]) async throws -> DroneWifiPutResponse {
        let body = PutDroneWifiRequest(networks: networks)
        return try await request(
            method: "PUT",
            path: "/drones/\(droneId)/wifi",
            body: body
        )
    }
    
    // MARK: - Auth / IoT
    
    /// Attach IoT policy to a Cognito Identity for MQTT access.
    /// Call this after getting Cognito credentials to enable MQTT.
    func attachIoTPolicy(identityId: String) async throws {
        let body = AttachIoTPolicyRequest(identityId: identityId)
        let _: AttachIoTPolicyResponse = try await request(
            method: "POST",
            path: "/auth/iot-policy",
            body: body
        )
    }
    
    // MARK: - Commands
    
    /// Send a natural language command to a drone
    func sendCommand(droneId: String, command: String) async throws -> CommandResponse {
        let body = DroneCommand(droneId: droneId, command: command)
        return try await request(
            method: "POST",
            path: "/command",
            body: body
        )
    }
    
    // MARK: - Conversations
    
    /// Start a new conversation with a drone
    func startConversation(droneId: String) async throws -> ConversationResponse {
        return try await request(
            method: "POST",
            path: "/drones/\(droneId)/conversations"
        )
    }
    
    /// Get conversation history
    func getConversationHistory(droneId: String, conversationId: String) async throws -> [ChatMessage] {
        let response: ConversationHistoryResponse = try await request(
            method: "GET",
            path: "/drones/\(droneId)/conversations/\(conversationId)"
        )
        return response.messages
    }
    
    /// Send a chat message to a drone conversation
    func sendChatMessage(droneId: String, conversationId: String, message: String) async throws -> ChatResponse {
        let body = ChatMessageRequest(message: message)
        return try await request(
            method: "POST",
            path: "/drones/\(droneId)/conversations/\(conversationId)/messages",
            body: body
        )
    }
    
    /// Send an image selection in response to drone's question
    func sendImageSelection(droneId: String, conversationId: String, optionId: Int) async throws -> ChatResponse {
        let body = ImageSelectionRequest(optionId: optionId)
        return try await request(
            method: "POST",
            path: "/drones/\(droneId)/conversations/\(conversationId)/select",
            body: body
        )
    }
    
    /// Get a pre-signed URL for uploading an image
    func getUploadUrl(droneId: String, conversationId: String, filename: String) async throws -> UploadUrlResponse {
        return try await request(
            method: "POST",
            path: "/drones/\(droneId)/conversations/\(conversationId)/upload-url",
            body: ["filename": filename]
        )
    }
    
    // MARK: - Logs
    
    /// Get recent logs from a drone
    func getLogs(droneId: String, since: String? = nil, limit: Int = 100) async throws -> LogsResponse {
        var queryItems = [URLQueryItem(name: "limit", value: "\(limit)")]
        if let since = since {
            queryItems.append(URLQueryItem(name: "since", value: since))
        }
        return try await request(method: "GET", path: "/drones/\(droneId)/logs", queryItems: queryItems)
    }
    
    // MARK: - Video Streaming
    
    /// Get video signaling channel info (for drone/master)
    func getVideoSignaling(droneId: String) async throws -> VideoSignalingResponse {
        return try await request(
            method: "GET",
            path: "/drones/\(droneId)/video/signaling"
        )
    }
    
    /// Get video viewer credentials and ICE servers (for iOS app/viewer)
    func getVideoViewer(droneId: String) async throws -> VideoViewerResponse {
        return try await request(
            method: "GET",
            path: "/drones/\(droneId)/video/viewer"
        )
    }
    
    // MARK: - Private
    
    private func request<T: Decodable>(
        method: String,
        path: String,
        body: Encodable? = nil,
        queryItems: [URLQueryItem]? = nil,
        isRetry: Bool = false
    ) async throws -> T {
        // Proactively refresh token if it's expiring soon (before making the request)
        if !isRetry {
            do {
                try await authService.refreshTokensIfNeeded()
            } catch {
                // If refresh fails but we still have a token, try the request anyway
                // The token might still be valid
            }
        }
        
        guard let token = authService.idToken else {
            throw APIError.unauthorized
        }
        
        var url = baseURL.appendingPathComponent(path)
        if let queryItems = queryItems, !queryItems.isEmpty {
            var components = URLComponents(url: url, resolvingAgainstBaseURL: true)
            components?.queryItems = queryItems
            url = components?.url ?? url
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        
        if let body = body {
            request.httpBody = try JSONEncoder().encode(body)
        }
        
        isLoading = true
        defer { isLoading = false }
        
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            
            guard let httpResponse = response as? HTTPURLResponse else {
                throw APIError.invalidResponse
            }
            
            switch httpResponse.statusCode {
            case 200...299:
                do {
                    return try decoder.decode(T.self, from: data)
                } catch {
                    #if DEBUG
                    let raw = String(data: data, encoding: .utf8) ?? "<non-utf8 response>"
                    let preview = raw.count > 1200 ? String(raw.prefix(1200)) + "…" : raw
                    print("❌ API decode failed: \(method) \(path) status=\(httpResponse.statusCode)")
                    print("❌ API raw response: \(preview)")
                    #endif
                    throw error
                }
            case 401, 403:
                // Token may be expired - try to refresh and retry once
                if !isRetry {
                    do {
                        try await authService.refreshTokensIfNeeded(force: true)
                        // Token refreshed, retry the request
                        return try await self.request(
                            method: method,
                            path: path,
                            body: body,
                            queryItems: queryItems,
                            isRetry: true
                        )
                    } catch {
                        // Refresh failed - now we need to sign out
                        await MainActor.run {
                            authService.signOut()
                        }
                        throw APIError.unauthorized
                    }
                } else {
                    // Already retried, sign out
                    await MainActor.run {
                        authService.signOut()
                    }
                    throw APIError.unauthorized
                }
            case 404:
                throw APIError.notFound
            case 409:
                if let errorResponse = try? decoder.decode(ErrorResponse.self, from: data) {
                    throw APIError.conflict(errorResponse.error)
                }
                throw APIError.conflict("Resource conflict")
            default:
                if let errorResponse = try? decoder.decode(ErrorResponse.self, from: data) {
                    throw APIError.serverError(errorResponse.error)
                }
                throw APIError.serverError("HTTP \(httpResponse.statusCode)")
            }
        } catch let error as APIError {
            lastError = error
            AppLogger.logAPIError(error, endpoint: path, method: method)
            throw error
        } catch {
            let apiError = APIError.networkError(error.localizedDescription)
            lastError = apiError
            AppLogger.logAPIError(apiError, endpoint: path, method: method)
            throw apiError
        }
    }
}

// MARK: - Request/Response Types

private struct RegisterDroneRequest: Encodable {
    let droneId: String
    let name: String
}

private struct PatchDroneRequest: Encodable {
    let name: String
}

struct DroneUpdateResponse: Decodable {
    let drone: Drone
}

private struct EmptyResponse: Decodable {}

private struct ErrorResponse: Decodable {
    let error: String
}

// MARK: - Conversation Types

private struct ChatMessageRequest: Encodable {
    let message: String
}

private struct ImageSelectionRequest: Encodable {
    let optionId: Int
    
    enum CodingKeys: String, CodingKey {
        case optionId = "option_id"
    }
}

struct ConversationResponse: Decodable {
    let conversationId: String
    let droneId: String
    let createdAt: String
    
    enum CodingKeys: String, CodingKey {
        case conversationId = "conversation_id"
        case droneId = "drone_id"
        case createdAt = "created_at"
    }
}

struct ConversationHistoryResponse: Decodable {
    let conversationId: String
    let messages: [ChatMessage]
    
    enum CodingKeys: String, CodingKey {
        case conversationId = "conversation_id"
        case messages
    }
}

struct ChatResponse: Decodable {
    let status: String
    let messageId: String?
    let immediateResponse: ChatMessage?
    
    enum CodingKeys: String, CodingKey {
        case status
        case messageId = "message_id"
        case immediateResponse = "immediate_response"
    }
}

struct UploadUrlResponse: Decodable {
    let uploadUrl: String
    let imageUrl: String
    
    enum CodingKeys: String, CodingKey {
        case uploadUrl = "upload_url"
        case imageUrl = "image_url"
    }
}

struct LogsResponse: Decodable {
    let droneId: String
    let logs: [LogEntry]
    let count: Int
    
    struct LogEntry: Decodable {
        let timestamp: String
        let level: String
        let source: String
        let message: String
        let code: String?
    }
}

// MARK: - Battery Config Types

struct BatteryConfig: Codable {
    var cellCount: Int
    var capacityMah: Int
    var cellEmptyVoltage: Double
    var cellFullVoltage: Double
    
    static let `default` = BatteryConfig(
        cellCount: 4,
        capacityMah: 5000,
        cellEmptyVoltage: 3.5,
        cellFullVoltage: 4.2
    )
}

struct BatteryConfigGetResponse: Decodable {
    let droneId: String
    let batteryConfig: BatteryConfig
}

struct BatteryConfigPutResponse: Decodable {
    let droneId: String
    let nonce: String
    let batteryConfig: BatteryConfig
}

// MARK: - WiFi Types

private struct PutDroneWifiRequest: Encodable {
    let networks: [WifiNetwork]
}

struct DroneWifiGetResponse: Decodable {
    let droneId: String
    let networks: [WifiNetwork]
    
    enum CodingKeys: String, CodingKey {
        case droneId
        case networks
    }
}

struct DroneWifiPutResponse: Decodable {
    let droneId: String
    let nonce: String
    let networks: [WifiNetwork]
    
    enum CodingKeys: String, CodingKey {
        case droneId
        case nonce
        case networks
    }
}

// MARK: - Auth / IoT Types

struct AttachIoTPolicyRequest: Encodable {
    let identityId: String
}

struct AttachIoTPolicyResponse: Decodable {
    let message: String
    let identityId: String
    let policyName: String
}

// MARK: - Video Streaming Types

struct VideoSignalingResponse: Decodable {
    let success: Bool
    let role: String
    let channelName: String
    let channelARN: String
    let region: String
    let endpoints: [String: String]
}

struct VideoViewerResponse: Decodable {
    let success: Bool
    let role: String
    let channelName: String
    let channelARN: String
    let region: String
    let endpoints: [String: String]
    let signedWssUrl: String
    let clientId: String
    let iceServers: [IceServer]
    
    struct IceServer: Decodable {
        let urls: [String]
        let username: String?
        let credential: String?
        let ttl: Int?
    }
}

// MARK: - Errors

enum APIError: LocalizedError {
    case unauthorized
    case forbidden
    case notFound
    case conflict(String)
    case invalidResponse
    case serverError(String)
    case networkError(String)
    
    var errorDescription: String? {
        switch self {
        case .unauthorized:
            return "Please sign in again"
        case .forbidden:
            return "You don't have permission to access this resource"
        case .notFound:
            return "Resource not found"
        case .conflict(let message):
            return message
        case .invalidResponse:
            return "Invalid response from server"
        case .serverError(let message):
            return "Server error: \(message)"
        case .networkError(let message):
            return "Network error: \(message)"
        }
    }
}

