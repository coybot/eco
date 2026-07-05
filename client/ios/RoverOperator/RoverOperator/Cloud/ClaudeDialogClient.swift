import Foundation

/// Cloud conversational fallback, used only when the on-device Foundation Model
/// escalates (see DialogAgent). Talks to the eco AWS API Gateway (same Cognito-authenticated
/// pattern as the drone app's APIClient) which forwards to Claude — mirrors
/// `aws/src/handler.py`'s existing drone conversation handling.
///
/// `tokenProvider` is wired from `AuthService.shared.idToken` in RoverOperatorApp,
/// updated whenever auth state changes.
///
/// Server side: `eco/aws/src/rover.py::converse_handler`, routed at POST /rover/converse
/// in `eco/aws/template.yaml` (`RoverConverseFunction`) — deployed and live on the
/// `drone-api` stack, same Cognito-backed authorizer as the rest of the API.
actor ClaudeDialogClient {
    static let shared = ClaudeDialogClient()

    // Async because AuthService (the real provider) is @MainActor-isolated; a
    // synchronous closure couldn't read its idToken from this actor's executor.
    private var tokenProvider: (@Sendable () async -> String?)?

    func setTokenProvider(_ provider: @escaping @Sendable () async -> String?) {
        tokenProvider = provider
    }

    private let baseURL = URL(string: AWSConfig.apiEndpoint)!
    private let session: URLSession = .shared

    private struct ConverseRequest: Codable { let utterance: String }
    private struct ConverseResponse: Codable { let reply: String }

    func converse(_ utterance: String) async throws -> String {
        var req = URLRequest(url: baseURL.appendingPathComponent("rover/converse"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token = await tokenProvider?() {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        req.httpBody = try JSONEncoder().encode(ConverseRequest(utterance: utterance))

        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw ClaudeDialogError.serverError
        }
        return try JSONDecoder().decode(ConverseResponse.self, from: data).reply
    }
}

enum ClaudeDialogError: LocalizedError {
    case serverError
    var errorDescription: String? { "Cloud conversation service unavailable." }
}
