import Foundation
import AuthenticationServices

/// Handles authentication via Cognito: email/password, sign-up, and Google federated login.
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

    var idToken: String? { tokens?.idToken }

    // MARK: - Email/Password Sign In

    @MainActor
    func signIn(email: String, password: String) async throws {
        isLoading = true; error = nil
        do {
            let result = try await cognitoInitiateAuth(
                flow: "USER_PASSWORD_AUTH",
                params: ["USERNAME": email, "PASSWORD": password]
            )
            apply(result: result, email: email)
            isLoading = false
            AppLogger.logAuthEvent("Sign-in successful", error: nil)
        } catch {
            isLoading = false
            let e = AuthError.signInFailed(error.localizedDescription)
            self.error = e
            AppLogger.logAuthEvent("Sign-in failed", error: error)
            throw e
        }
    }

    // MARK: - Sign Up

    /// Register a new Cognito user. After this, call confirmSignUp with the emailed code.
    @MainActor
    func signUp(email: String, password: String) async throws {
        isLoading = true; error = nil
        do {
            try await cognitoSignUp(email: email, password: password)
            isLoading = false
            AppLogger.logAuthEvent("Sign-up successful — confirmation required", error: nil)
        } catch {
            isLoading = false
            let e = AuthError.signInFailed(error.localizedDescription)
            self.error = e
            throw e
        }
    }

    /// Confirm a new account with the verification code sent to the user's email.
    @MainActor
    func confirmSignUp(email: String, code: String) async throws {
        isLoading = true; error = nil
        do {
            try await cognitoConfirmSignUp(email: email, code: code)
            isLoading = false
            AppLogger.logAuthEvent("Confirmation successful", error: nil)
        } catch {
            isLoading = false
            let e = AuthError.signInFailed(error.localizedDescription)
            self.error = e
            throw e
        }
    }

    // MARK: - Google Sign In (Cognito Hosted UI)

    @MainActor
    func signInWithGoogle() async throws {
        try await signInWithHostedUI(provider: "Google", callbackScheme: AWSConfig.callbackURLScheme)
    }

    // MARK: - Sign Out

    func signOut() {
        AppLogger.logAuthEvent("User signed out", error: nil)
        tokens = nil; currentUser = nil
        clearSession()
    }

    // MARK: - Token Refresh

    @discardableResult
    func refreshTokensIfNeeded(force: Bool = false) async throws -> Bool {
        guard let current = tokens else { throw AuthError.notAuthenticated }
        guard force || current.isExpiredOrExpiringSoon else { return false }
        guard let refreshToken = current.refreshToken else {
            if current.isExpired { throw AuthError.tokenRefreshFailed }
            return false
        }
        do {
            let result = try await cognitoInitiateAuth(
                flow: "REFRESH_TOKEN_AUTH",
                params: ["REFRESH_TOKEN": refreshToken]
            )
            apply(result: result, email: currentUser?.email ?? "", keepRefreshToken: refreshToken)
            AppLogger.logAuthEvent("Token refreshed", error: nil)
            return true
        } catch {
            AppLogger.logAuthEvent("Token refresh failed", error: error)
            throw AuthError.tokenRefreshFailed
        }
    }

    // MARK: - Cognito Hosted UI (OAuth code flow)

    @MainActor
    private func signInWithHostedUI(provider: String, callbackScheme: String) async throws {
        isLoading = true; error = nil

        let redirectURI = "\(callbackScheme)://callback"
        guard var components = URLComponents(string: "\(AWSConfig.cognitoHostedUIDomain)/oauth2/authorize") else {
            throw AuthError.signInFailed("Invalid Hosted UI URL")
        }
        components.queryItems = [
            URLQueryItem(name: "client_id",          value: AWSConfig.cognitoClientId),
            URLQueryItem(name: "response_type",      value: "code"),
            URLQueryItem(name: "scope",              value: "email openid profile"),
            URLQueryItem(name: "redirect_uri",       value: redirectURI),
            URLQueryItem(name: "identity_provider",  value: provider),
        ]
        guard let authURL = components.url else { throw AuthError.signInFailed("Invalid OAuth URL") }

        do {
            let callbackURL = try await withCheckedThrowingContinuation { (cont: CheckedContinuation<URL, Error>) in
                let session = ASWebAuthenticationSession(url: authURL, callbackURLScheme: callbackScheme) { url, err in
                    if let err { cont.resume(throwing: err) }
                    else if let url { cont.resume(returning: url) }
                    else { cont.resume(throwing: AuthError.signInFailed("No callback URL")) }
                }
                session.prefersEphemeralWebBrowserSession = false
                session.presentationContextProvider = self
                self.webAuthSession = session
                session.start()
            }

            guard let code = URLComponents(url: callbackURL, resolvingAgainstBaseURL: false)?
                .queryItems?.first(where: { $0.name == "code" })?.value else {
                throw AuthError.signInFailed("No authorization code in callback")
            }

            let result = try await exchangeHostedUICode(code: code, redirectURI: redirectURI)
            let email = extractEmail(from: result.idToken) ?? ""
            apply(result: result, email: email)
            isLoading = false
            AppLogger.logAuthEvent("\(provider) Hosted UI sign-in successful", error: nil)
        } catch {
            isLoading = false
            let e = AuthError.signInFailed(error.localizedDescription)
            self.error = e
            AppLogger.logAuthEvent("\(provider) sign-in failed", error: error)
            throw e
        }
    }

    private func exchangeHostedUICode(code: String, redirectURI: String) async throws -> CognitoAuthResult {
        guard let url = URL(string: "\(AWSConfig.cognitoHostedUIDomain)/oauth2/token") else {
            throw AuthError.signInFailed("Invalid token URL")
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        let body = [
            "grant_type":   "authorization_code",
            "client_id":    AWSConfig.cognitoClientId,
            "code":         code,
            "redirect_uri": redirectURI,
        ].map { "\($0.key)=\($0.value.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? $0.value)" }
         .joined(separator: "&")
        request.httpBody = body.data(using: .utf8)

        let (data, _) = try await URLSession.shared.data(for: request)
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let idToken = json["id_token"] as? String else {
            throw AuthError.signInFailed("Token exchange failed")
        }
        return CognitoAuthResult(
            idToken: idToken,
            refreshToken: json["refresh_token"] as? String,
            expiresIn: json["expires_in"] as? Int ?? 3600
        )
    }

    // MARK: - Cognito API calls

    private struct CognitoAuthResult {
        let idToken: String
        let refreshToken: String?
        let expiresIn: Int
    }

    private func cognitoInitiateAuth(flow: String, params: [String: String]) async throws -> CognitoAuthResult {
        guard let url = URL(string: "https://cognito-idp.\(AWSConfig.region).amazonaws.com/") else {
            throw AuthError.signInFailed("Invalid Cognito URL")
        }
        let body: [String: Any] = [
            "AuthFlow": flow,
            "ClientId": AWSConfig.cognitoClientId,
            "AuthParameters": params,
        ]
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/x-amz-json-1.1", forHTTPHeaderField: "Content-Type")
        request.setValue("AWSCognitoIdentityProviderService.InitiateAuth", forHTTPHeaderField: "X-Amz-Target")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, _) = try await URLSession.shared.data(for: request)
        return try decodeCognitoAuthResult(from: data)
    }

    private func cognitoSignUp(email: String, password: String) async throws {
        guard let url = URL(string: "https://cognito-idp.\(AWSConfig.region).amazonaws.com/") else {
            throw AuthError.signInFailed("Invalid Cognito URL")
        }
        let body: [String: Any] = [
            "ClientId": AWSConfig.cognitoClientId,
            "Username": email,
            "Password": password,
            "UserAttributes": [["Name": "email", "Value": email]],
        ]
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/x-amz-json-1.1", forHTTPHeaderField: "Content-Type")
        request.setValue("AWSCognitoIdentityProviderService.SignUp", forHTTPHeaderField: "X-Amz-Target")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, _) = try await URLSession.shared.data(for: request)
        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let message = json["message"] as? String ?? (json["__type"] as? String) {
            throw AuthError.signInFailed(message)
        }
    }

    private func cognitoConfirmSignUp(email: String, code: String) async throws {
        guard let url = URL(string: "https://cognito-idp.\(AWSConfig.region).amazonaws.com/") else {
            throw AuthError.signInFailed("Invalid Cognito URL")
        }
        let body: [String: Any] = [
            "ClientId":         AWSConfig.cognitoClientId,
            "Username":         email,
            "ConfirmationCode": code,
        ]
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/x-amz-json-1.1", forHTTPHeaderField: "Content-Type")
        request.setValue("AWSCognitoIdentityProviderService.ConfirmSignUp", forHTTPHeaderField: "X-Amz-Target")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, _) = try await URLSession.shared.data(for: request)
        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let message = json["message"] as? String ?? (json["__type"] as? String) {
            throw AuthError.signInFailed(message)
        }
    }

    private func decodeCognitoAuthResult(from data: Data) throws -> CognitoAuthResult {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw AuthError.signInFailed("Invalid response")
        }
        if let message = json["message"] as? String { throw AuthError.signInFailed(message) }
        if let type = json["__type"] as? String {
            throw AuthError.signInFailed(json["message"] as? String ?? type)
        }
        guard let auth = json["AuthenticationResult"] as? [String: Any],
              let idToken = auth["IdToken"] as? String else {
            throw AuthError.signInFailed("Missing tokens in response")
        }
        return CognitoAuthResult(
            idToken: idToken,
            refreshToken: auth["RefreshToken"] as? String,
            expiresIn: auth["ExpiresIn"] as? Int ?? 3600
        )
    }

    // MARK: - Helpers

    private func apply(result: CognitoAuthResult, email: String, keepRefreshToken: String? = nil) {
        let expiresAt = Date().addingTimeInterval(TimeInterval(result.expiresIn))
        tokens = AuthTokens(
            idToken: result.idToken,
            refreshToken: result.refreshToken ?? keepRefreshToken,
            expiresAt: expiresAt
        )
        if currentUser == nil || currentUser?.email != email {
            currentUser = User(id: email, email: email, name: nil, provider: .cognito)
        }
        saveSession()
    }

    private func extractEmail(from idToken: String) -> String? {
        let parts = idToken.split(separator: ".")
        guard parts.count >= 2 else { return nil }
        var b64 = String(parts[1])
        while b64.count % 4 != 0 { b64.append("=") }
        guard let data = Data(base64Encoded: b64),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return json["email"] as? String
    }

    // MARK: - Session Persistence

    private let sessionKey = "us.astral.drone.session"

    private func saveSession() {
        guard let tokens, let user = currentUser else { return }
        if let data = try? JSONEncoder().encode(StoredSession(tokens: tokens, user: user)) {
            KeychainHelper.save(data, forKey: sessionKey)
        }
    }

    private func loadStoredSession() {
        guard let data = KeychainHelper.load(forKey: sessionKey),
              let session = try? JSONDecoder().decode(StoredSession.self, from: data) else { return }
        tokens = session.tokens
        currentUser = session.user
    }

    private func clearSession() { KeychainHelper.delete(forKey: sessionKey) }

    private struct StoredSession: Codable {
        let tokens: AuthTokens
        let user: User
    }
}

// MARK: - ASWebAuthenticationPresentationContextProviding

extension AuthService: ASWebAuthenticationPresentationContextProviding {
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        ASPresentationAnchor()
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
        case .notAuthenticated:      return "Not authenticated"
        case .signInFailed(let msg): return msg
        case .tokenRefreshFailed:    return "Session expired. Please sign in again."
        case .networkError:          return "Network error"
        }
    }
}

// MARK: - Keychain Helper

enum KeychainHelper {
    static func save(_ data: Data, forKey key: String) {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrAccount as String: key, kSecValueData as String: data]
        SecItemDelete(query as CFDictionary)
        SecItemAdd(query as CFDictionary, nil)
    }
    static func load(forKey key: String) -> Data? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrAccount as String: key, kSecReturnData as String: true]
        var result: AnyObject?
        SecItemCopyMatching(query as CFDictionary, &result)
        return result as? Data
    }
    static func delete(forKey key: String) {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrAccount as String: key]
        SecItemDelete(query as CFDictionary)
    }
}

// MARK: - String Extension

extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}
