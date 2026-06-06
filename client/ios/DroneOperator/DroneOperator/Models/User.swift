import Foundation

/// Authenticated user information
struct User: Codable {
    let id: String
    let email: String?
    let name: String?
    let provider: AuthProvider?
    
    enum AuthProvider: String, Codable {
        case google = "google"
        case apple = "apple"
        case cognito = "cognito"
    }
    
    var displayName: String {
        name ?? email ?? "User"
    }
    
    /// Get user's initials (up to 2 characters)
    var initials: String {
        if let name = name, !name.isEmpty {
            let parts = name.split(separator: " ")
            if parts.count >= 2 {
                return String(parts[0].prefix(1) + parts[1].prefix(1)).uppercased()
            } else {
                return String(name.prefix(2)).uppercased()
            }
        } else if let email = email {
            return String(email.prefix(2)).uppercased()
        }
        return "U"
    }
}

/// Authentication tokens
struct AuthTokens: Codable {
    let idToken: String  // Google/Apple ID token for API authorization
    let refreshToken: String?  // Google refresh token for token renewal (Apple doesn't provide one)
    let expiresAt: Date?  // When the ID token expires
    
    /// Check if the token is expired or will expire soon (within 5 minutes)
    var isExpiredOrExpiringSoon: Bool {
        guard let expiresAt = expiresAt else {
            // If we don't know expiration, assume it might be expired after 50 minutes
            // (Google ID tokens last 1 hour)
            return false
        }
        // Consider expired if within 5 minutes of expiration
        return Date().addingTimeInterval(5 * 60) >= expiresAt
    }
    
    /// Check if token is completely expired
    var isExpired: Bool {
        guard let expiresAt = expiresAt else { return false }
        return Date() >= expiresAt
    }
}

