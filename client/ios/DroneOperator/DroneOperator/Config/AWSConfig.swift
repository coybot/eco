import Foundation

/// AWS and auth endpoints for the DroneOperator app.
///
/// Replace placeholders with values from your deployed SAM stack and Google Cloud OAuth
/// (iOS client). The Google reversed client scheme in `Info.plist` must match
/// `googleIOSURLScheme` here.
enum AWSConfig {
    // MARK: - AWS
    static let region = "us-west-2"

    // MARK: - API Gateway
    static let apiEndpoint = "https://YOUR_API_ID.execute-api.us-west-2.amazonaws.com/prod"

    // MARK: - Cognito
    static let identityPoolId = "us-west-2:REPLACE_WITH_IDENTITY_POOL_ID"
    static let userPoolId = "us-west-2_REPLACE_WITH_USER_POOL_ID"

    // MARK: - IoT Core (for MQTT)
    static let iotEndpoint = "REPLACE.iot.us-west-2.amazonaws.com"

    // MARK: - S3 (for drone images)
    static let imagesBucket = "drone-images-dev-us-west-2-YOUR_ACCOUNT_ID"

    // MARK: - OAuth (for social sign-in)
    /// iOS OAuth client ID from Google Cloud Console (must match URL scheme in Info.plist).
    static let googleClientId = "yourclientid.apps.googleusercontent.com"
    static let appleServicesId = ""

    // MARK: - App
    static let callbackURLScheme = "droneoperator"

    /// `com.googleusercontent.apps.<prefix>` where `<prefix>` is the part before `.apps.googleusercontent.com`.
    static var googleIOSURLScheme: String {
        let suffix = ".apps.googleusercontent.com"
        guard googleClientId.hasSuffix(suffix) else {
            return "com.googleusercontent.apps.invalid"
        }
        let raw = String(googleClientId.dropLast(suffix.count))
        return "com.googleusercontent.apps.\(raw)"
    }

    static var googleOAuthRedirectURI: String {
        "\(googleIOSURLScheme):/oauth2redirect/google"
    }
}
