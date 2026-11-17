import Foundation

/// AWS and auth endpoints for the DroneOperator app.
/// Values are sourced from the deployed `drone-api` SAM stack in us-east-2.
enum AWSConfig {
    // MARK: - AWS
    static let region = "us-east-2"

    // MARK: - API Gateway
    static let apiEndpoint = "https://w3zf9y7b29.execute-api.us-east-2.amazonaws.com/prod"

    // MARK: - Cognito
    static let identityPoolId = "us-east-2:6da3c153-6ccf-487c-833c-02e81d728135"
    static let userPoolId = "us-east-2_K16WBUtfm"

    // MARK: - IoT Core (for MQTT)
    static let iotEndpoint = "a8n91fgngz353-ats.iot.us-east-2.amazonaws.com"

    // MARK: - S3 (for drone images)
    static let imagesBucket = "coybot-drone-images-dev-us-east-2"

    // MARK: - Cognito App Client
    static let cognitoClientId = "5fvrfsuln61as13mtu4um6oquu"
    static let cognitoHostedUIDomain = "https://coybot-drone-auth-dev.auth.us-east-2.amazoncognito.com"
    static let callbackURLScheme = "droneoperator"
}
