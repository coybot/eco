import Foundation

/// Minimal HTTP client for the drone-api — forked from DroneOperator's APIClient.swift,
/// trimmed to just the one call MQTTService needs (attach the Cognito identity's IoT
/// policy before connecting). RoverOperator doesn't need drone/mission management, so the
/// large surface of the original (listDrones, conversations, groups, video, ...) is
/// deliberately not ported.
actor APIClient {
    static let shared = APIClient()

    private let baseURL: URL

    private init() {
        guard let url = URL(string: AWSConfig.apiEndpoint) else {
            fatalError("Invalid API endpoint URL in AWSConfig: \(AWSConfig.apiEndpoint)")
        }
        self.baseURL = url
    }

    private struct AttachIoTPolicyRequest: Encodable { let identityId: String }
    private struct AttachIoTPolicyResponse: Decodable {}

    /// Attach this Cognito identity to the shared `drone-policy-dev` IoT policy —
    /// required once per identity before it can publish/subscribe over MQTT.
    func attachIoTPolicy(identityId: String, idToken: String) async throws {
        var request = URLRequest(url: baseURL.appendingPathComponent("auth/iot-policy"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(idToken)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONEncoder().encode(AttachIoTPolicyRequest(identityId: identityId))

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw APIClientError.serverError
        }
        _ = try? JSONDecoder().decode(AttachIoTPolicyResponse.self, from: data)
    }
}

enum APIClientError: LocalizedError {
    case serverError
    var errorDescription: String? { "Failed to attach IoT policy." }
}
