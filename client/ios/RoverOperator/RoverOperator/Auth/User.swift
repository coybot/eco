import Foundation

/// Authenticated user — forked from DroneOperator's Models/User.swift, trimmed to the
/// Cognito email/password provider only (see AuthService "why not Google").
struct User: Codable {
    let id: String
    let email: String?

    var displayName: String { email ?? "User" }
}

struct AuthTokens: Codable {
    let idToken: String
    let refreshToken: String?
    let expiresAt: Date?

    var isExpiredOrExpiringSoon: Bool {
        guard let expiresAt else { return false }
        return Date().addingTimeInterval(5 * 60) >= expiresAt
    }

    var isExpired: Bool {
        guard let expiresAt else { return false }
        return Date() >= expiresAt
    }
}
