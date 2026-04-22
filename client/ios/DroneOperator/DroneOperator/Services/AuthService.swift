import Foundation
import AuthenticationServices
import CryptoKit

/// Handles authentication via Apple/Google sign-in
@Observable
final class AuthService: NSObject {
    
    // MARK: - State
    
    var currentUser: User?
    var isAuthenticated: Bool { currentUser != nil }
    var isLoading = false
    var error: AuthError?
    
    private var tokens: AuthTokens?
    private var webAuthSession: ASWebAuthenticationSession?
    
    // MARK: - Singleton
    
    static let shared = AuthService()
    
    private override init() {
        super.init()
        loadStoredSession()
    }
    
    // MARK: - Public API
    
    /// Get the current ID token for API requests
    var idToken: String? {
        tokens?.idToken
    }
    
    /// Sign in with Apple
    @MainActor
    func signInWithApple() async throws {
        isLoading = true
        error = nil
        
        let request = ASAuthorizationAppleIDProvider().createRequest()
        request.requestedScopes = [.fullName, .email]
        
        let controller = ASAuthorizationController(authorizationRequests: [request])
        
        do {
            let authorization = try await withCheckedThrowingContinuation { continuation in
                let delegate = AppleSignInDelegate(continuation: continuation)
                controller.delegate = delegate
                controller.performRequests()
                
                // Keep delegate alive
                objc_setAssociatedObject(controller, "delegate", delegate, .OBJC_ASSOCIATION_RETAIN)
            }
            
            guard let credential = authorization.credential as? ASAuthorizationAppleIDCredential,
                  let identityToken = credential.identityToken,
                  let tokenString = String(data: identityToken, encoding: .utf8) else {
                throw AuthError.signInFailed("Failed to get Apple ID token")
            }
            
            // Store the Apple ID token (no refresh token for Apple)
            self.tokens = createAppleTokens(idToken: tokenString)
            
            // Build user from Apple credential
            self.currentUser = User(
                id: credential.user,
                email: credential.email,
                name: [credential.fullName?.givenName, credential.fullName?.familyName]
                    .compactMap { $0 }
                    .joined(separator: " ")
                    .nilIfEmpty,
                provider: .apple
            )
            
            saveSession()
            isLoading = false
            AppLogger.logAuthEvent("Apple sign-in successful", error: nil)
            logDebugAuthContext(reason: "Apple sign-in")
            
        } catch {
            isLoading = false
            self.error = .signInFailed(error.localizedDescription)
            AppLogger.logAuthEvent("Apple sign-in failed", error: error)
            throw error
        }
    }
    
    /// Sign in with Google
    @MainActor
    func signInWithGoogle() async throws {
        guard !AWSConfig.googleClientId.isEmpty else {
            throw AuthError.signInFailed("Google Client ID not configured")
        }
        
        isLoading = true
        error = nil
        
        do {
            let tokenResponse = try await googleOAuthFlow()
            self.tokens = createTokens(
                idToken: tokenResponse.idToken,
                refreshToken: tokenResponse.refreshToken,
                expiresIn: tokenResponse.expiresIn
            )
            self.currentUser = try await fetchUserInfo(token: tokenResponse.idToken)
            saveSession()
            isLoading = false
            AppLogger.logAuthEvent("Google sign-in successful", error: nil)
            logDebugAuthContext(reason: "Google sign-in")
        } catch {
            isLoading = false
            self.error = .signInFailed(error.localizedDescription)
            AppLogger.logAuthEvent("Google sign-in failed", error: error)
            throw error
        }
    }
    
    /// Sign out
    func signOut() {
        AppLogger.logAuthEvent("User signed out", error: nil)
        tokens = nil
        currentUser = nil
        clearSession()
    }
    
    /// Refresh tokens if needed (or forced)
    /// Returns true if tokens were refreshed, false if not needed
    @discardableResult
    func refreshTokensIfNeeded(force: Bool = false) async throws -> Bool {
        guard let currentTokens = tokens else {
            throw AuthError.notAuthenticated
        }
        
        // Check if refresh is needed
        guard force || currentTokens.isExpiredOrExpiringSoon else {
            return false  // Token is still valid
        }
        
        // Apple Sign-In doesn't support refresh tokens - user must re-authenticate
        if currentUser?.provider == .apple {
            // Apple tokens last longer and are managed differently
            // If expired, we need to re-authenticate
            if currentTokens.isExpired {
                throw AuthError.tokenRefreshFailed
            }
            return false
        }
        
        // Google: use refresh token if available
        guard let refreshToken = currentTokens.refreshToken else {
            // No refresh token available - try to continue with existing token
            // Only fail if token is completely expired
            if currentTokens.isExpired {
                throw AuthError.tokenRefreshFailed
            }
            return false
        }
        
        do {
            let tokenResponse = try await refreshGoogleToken(refreshToken: refreshToken)
            
            // Update tokens - keep the existing refresh token since Google doesn't return it on refresh
            self.tokens = createTokens(
                idToken: tokenResponse.idToken,
                refreshToken: refreshToken,  // Keep existing refresh token
                expiresIn: tokenResponse.expiresIn
            )
            
            saveSession()
            AppLogger.logAuthEvent("Token refreshed successfully", error: nil)
            return true
        } catch {
            AppLogger.logAuthEvent("Token refresh failed", error: error)
            throw AuthError.tokenRefreshFailed
        }
    }
    
    // MARK: - Token Handling
    
    private func createTokens(idToken: String, refreshToken: String?, expiresIn: Int) -> AuthTokens {
        let expiresAt = Date().addingTimeInterval(TimeInterval(expiresIn))
        return AuthTokens(idToken: idToken, refreshToken: refreshToken, expiresAt: expiresAt)
    }
    
    /// Create tokens for Apple Sign-In (no refresh token, long expiration)
    private func createAppleTokens(idToken: String) -> AuthTokens {
        // Apple ID tokens are valid for ~24 hours but we'll be conservative
        let expiresAt = Date().addingTimeInterval(23 * 60 * 60)  // 23 hours
        return AuthTokens(idToken: idToken, refreshToken: nil, expiresAt: expiresAt)
    }
    
    private func fetchUserInfo(token: String) async throws -> User {
        // Decode the JWT to get user info (Google ID token)
        let parts = token.split(separator: ".")
        guard parts.count >= 2 else {
            throw AuthError.signInFailed("Invalid token format")
        }
        
        var base64 = String(parts[1])
        // Pad base64 string
        while base64.count % 4 != 0 {
            base64.append("=")
        }
        
        guard let data = Data(base64Encoded: base64),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw AuthError.signInFailed("Failed to decode token")
        }
        
        let sub = json["sub"] as? String ?? UUID().uuidString
        let email = json["email"] as? String
        let name = json["name"] as? String
        
        return User(id: sub, email: email, name: name, provider: .google)
    }
    
    // MARK: - Google OAuth Flow
    
    @MainActor
    private func googleOAuthFlow() async throws -> GoogleTokenResponse {
        let codeVerifier = generateCodeVerifier()
        let codeChallenge = generateCodeChallenge(from: codeVerifier)
        
        guard var components = URLComponents(string: "https://accounts.google.com/o/oauth2/v2/auth") else {
            throw AuthError.signInFailed("Invalid Google OAuth URL")
        }
        components.queryItems = [
            URLQueryItem(name: "client_id", value: AWSConfig.googleClientId),
            URLQueryItem(name: "redirect_uri", value: AWSConfig.googleOAuthRedirectURI),
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "scope", value: "openid email profile"),
            URLQueryItem(name: "code_challenge", value: codeChallenge),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "access_type", value: "offline"),  // Request refresh token
            URLQueryItem(name: "prompt", value: "consent")  // Force consent to ensure refresh token is returned
        ]
        
        guard let authURL = components.url else {
            throw AuthError.signInFailed("Invalid OAuth URL")
        }
        
        let callbackURL = try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<URL, Error>) in
            let session = ASWebAuthenticationSession(
                url: authURL,
                callbackURLScheme: AWSConfig.googleIOSURLScheme
            ) { url, error in
                if let error = error {
                    continuation.resume(throwing: error)
                } else if let url = url {
                    continuation.resume(returning: url)
                } else {
                    continuation.resume(throwing: AuthError.signInFailed("No callback URL"))
                }
            }
            
            session.prefersEphemeralWebBrowserSession = false
            session.presentationContextProvider = self
            
            self.webAuthSession = session
            session.start()
        }
        
        guard let code = URLComponents(url: callbackURL, resolvingAgainstBaseURL: false)?
            .queryItems?
            .first(where: { $0.name == "code" })?
            .value else {
            throw AuthError.signInFailed("No authorization code")
        }
        
        // Exchange code for tokens (id_token + refresh_token)
        return try await exchangeGoogleCode(code: code, codeVerifier: codeVerifier)
    }
    
    /// Response from Google token exchange
    struct GoogleTokenResponse {
        let idToken: String
        let refreshToken: String?
        let expiresIn: Int  // seconds until expiration
    }
    
    private func exchangeGoogleCode(code: String, codeVerifier: String) async throws -> GoogleTokenResponse {
        guard let url = URL(string: "https://oauth2.googleapis.com/token") else {
            throw AuthError.signInFailed("Invalid Google token URL")
        }
        
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        
        let body = [
            "client_id": AWSConfig.googleClientId,
            "code": code,
            "code_verifier": codeVerifier,
            "grant_type": "authorization_code",
            "redirect_uri": AWSConfig.googleOAuthRedirectURI
        ]
        .map { "\($0.key)=\($0.value.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? $0.value)" }
        .joined(separator: "&")
        
        request.httpBody = body.data(using: .utf8)
        
        let (data, _) = try await URLSession.shared.data(for: request)
        
        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let idToken = json?["id_token"] as? String else {
            throw AuthError.signInFailed("No ID token from Google")
        }
        
        let refreshToken = json?["refresh_token"] as? String
        let expiresIn = json?["expires_in"] as? Int ?? 3600  // Default 1 hour
        
        return GoogleTokenResponse(idToken: idToken, refreshToken: refreshToken, expiresIn: expiresIn)
    }
    
    /// Refresh the Google ID token using the stored refresh token
    private func refreshGoogleToken(refreshToken: String) async throws -> GoogleTokenResponse {
        guard let url = URL(string: "https://oauth2.googleapis.com/token") else {
            throw AuthError.tokenRefreshFailed
        }
        
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        
        let body = [
            "client_id": AWSConfig.googleClientId,
            "refresh_token": refreshToken,
            "grant_type": "refresh_token"
        ]
        .map { "\($0.key)=\($0.value.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? $0.value)" }
        .joined(separator: "&")
        
        request.httpBody = body.data(using: .utf8)
        
        let (data, response) = try await URLSession.shared.data(for: request)
        
        guard let httpResponse = response as? HTTPURLResponse,
              httpResponse.statusCode == 200 else {
            throw AuthError.tokenRefreshFailed
        }
        
        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let idToken = json?["id_token"] as? String else {
            throw AuthError.tokenRefreshFailed
        }
        
        let expiresIn = json?["expires_in"] as? Int ?? 3600
        
        // Note: refresh_token is NOT returned on refresh calls - keep the existing one
        return GoogleTokenResponse(idToken: idToken, refreshToken: nil, expiresIn: expiresIn)
    }
    
    private func generateCodeVerifier() -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        return Data(bytes).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
    
    private func generateCodeChallenge(from verifier: String) -> String {
        let data = Data(verifier.utf8)
        let hash = SHA256.hash(data: data)
        return Data(hash).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
    
    // MARK: - Session Persistence
    
    private let sessionKey = "us.astral.drone.session"
    
    private func saveSession() {
        guard let tokens = tokens, let user = currentUser else { return }
        
        let session = StoredSession(tokens: tokens, user: user)
        if let data = try? JSONEncoder().encode(session) {
            KeychainHelper.save(data, forKey: sessionKey)
        }
    }
    
    private func loadStoredSession() {
        guard let data = KeychainHelper.load(forKey: sessionKey),
              let session = try? JSONDecoder().decode(StoredSession.self, from: data) else {
            return
        }
        
        self.tokens = session.tokens
        self.currentUser = session.user
        logDebugAuthContext(reason: "Loaded stored session")
    }
    
    private func clearSession() {
        KeychainHelper.delete(forKey: sessionKey)
    }
    
    private struct StoredSession: Codable {
        let tokens: AuthTokens
        let user: User
    }

    // MARK: - Debug Helpers

    private func logDebugAuthContext(reason: String) {
        #if DEBUG
        guard let tokens else { return }
        let header = "⚠️ DEBUG AUTH DUMP (\(reason))"
        let config = """
        apiEndpoint=\(AWSConfig.apiEndpoint)
        iotEndpoint=\(AWSConfig.iotEndpoint)
        identityPoolId=\(AWSConfig.identityPoolId)
        userPoolId=\(AWSConfig.userPoolId)
        region=\(AWSConfig.region)
        """
        print(header)
        print(config)
        print("idToken=\(tokens.idToken)")
        if let refreshToken = tokens.refreshToken {
            print("refreshToken=\(refreshToken)")
        } else {
            print("refreshToken=nil")
        }
        AppLogger.auth.warning("\(header) - see Xcode console for tokens/config")
        #endif
    }
}

// MARK: - ASWebAuthenticationPresentationContextProviding

extension AuthService: ASWebAuthenticationPresentationContextProviding {
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        ASPresentationAnchor()
    }
}

// MARK: - Apple Sign In Delegate

private class AppleSignInDelegate: NSObject, ASAuthorizationControllerDelegate {
    let continuation: CheckedContinuation<ASAuthorization, Error>
    
    init(continuation: CheckedContinuation<ASAuthorization, Error>) {
        self.continuation = continuation
    }
    
    func authorizationController(controller: ASAuthorizationController, didCompleteWithAuthorization authorization: ASAuthorization) {
        continuation.resume(returning: authorization)
    }
    
    func authorizationController(controller: ASAuthorizationController, didCompleteWithError error: Error) {
        continuation.resume(throwing: error)
    }
}

// MARK: - Auth Errors

enum AuthError: LocalizedError {
    case notAuthenticated
    case signInFailed(String)
    case tokenRefreshFailed
    case networkError
    
    var errorDescription: String? {
        switch self {
        case .notAuthenticated:
            return "Not authenticated"
        case .signInFailed(let message):
            return "Sign in failed: \(message)"
        case .tokenRefreshFailed:
            return "Session expired. Please sign in again."
        case .networkError:
            return "Network error"
        }
    }
}

// MARK: - Keychain Helper

enum KeychainHelper {
    static func save(_ data: Data, forKey key: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: key,
            kSecValueData as String: data
        ]
        
        SecItemDelete(query as CFDictionary)
        SecItemAdd(query as CFDictionary, nil)
    }
    
    static func load(forKey key: String) -> Data? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true
        ]
        
        var result: AnyObject?
        SecItemCopyMatching(query as CFDictionary, &result)
        return result as? Data
    }
    
    static func delete(forKey key: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: key
        ]
        SecItemDelete(query as CFDictionary)
    }
}

// MARK: - String Extension

extension String {
    var nilIfEmpty: String? {
        isEmpty ? nil : self
    }
}
