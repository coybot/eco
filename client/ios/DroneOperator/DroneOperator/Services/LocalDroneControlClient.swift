import Foundation

final class LocalDroneControlClient {
    struct LocalControlStatus: Codable {
        let missionId: String?
        let active: Bool
        let startedAt: Double?
        let lastError: String?
        
        enum CodingKeys: String, CodingKey {
            case missionId = "mission_id"
            case active
            case startedAt = "started_at"
            case lastError = "last_error"
        }
    }
    
    struct MissionResponse: Codable {
        let missionId: String?
        let active: Bool
        let startedAt: Double?
        
        enum CodingKeys: String, CodingKey {
            case missionId = "mission_id"
            case active
            case startedAt = "started_at"
        }
    }
    
    private let baseURL: URL
    private let session: URLSession
    private let tokenKey = "us.astral.drone.pairing"
    
    init(baseURL: URL = URL(string: "https://192.168.4.1:8443")!, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
    }
    
    func setPairingToken(_ token: String) {
        if let data = token.data(using: .utf8) {
            KeychainHelper.save(data, forKey: tokenKey)
        }
    }
    
    func getPairingToken() -> String? {
        guard let data = KeychainHelper.load(forKey: tokenKey) else { return nil }
        return String(data: data, encoding: .utf8)
    }
    
    func sendMission(_ mission: Mission) async throws -> MissionResponse {
        guard mission.isValid else {
            throw LocalControlError.invalidMission("Missing or empty goal")
        }
        let body = try JSONEncoder().encode(mission)
        let response: MissionResponse = try await request(path: "/mission", method: "POST", body: body)
        return response
    }
    
    func abort() async throws -> LocalControlStatus {
        return try await request(path: "/abort", method: "POST", body: Data())
    }
    
    func status() async throws -> LocalControlStatus {
        return try await request(path: "/status", method: "GET", body: nil)
    }
    
    private func request<T: Decodable>(path: String, method: String, body: Data?) async throws -> T {
        guard let token = getPairingToken() else {
            throw LocalControlError.missingPairingToken
        }
        let url = baseURL.appendingPathComponent(path)
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let body = body, !body.isEmpty {
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        
        let (data, response) = try await session.data(for: req)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw LocalControlError.invalidResponse
        }
        guard (200...299).contains(httpResponse.statusCode) else {
            throw LocalControlError.serverError(httpResponse.statusCode)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}

enum LocalControlError: LocalizedError {
    case missingPairingToken
    case invalidMission(String)
    case invalidResponse
    case serverError(Int)
    
    var errorDescription: String? {
        switch self {
        case .missingPairingToken:
            return "Pairing token not set for local control."
        case .invalidMission(let reason):
            return "Invalid mission: \(reason)"
        case .invalidResponse:
            return "Invalid response from drone."
        case .serverError(let code):
            return "Drone returned error \(code)."
        }
    }
}
