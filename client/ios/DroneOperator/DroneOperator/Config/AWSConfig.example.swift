import Foundation

/// AWS and auth endpoints for the DroneOperator app.
/// Fill these in from your own deployed `drone-api` SAM stack's outputs
/// (`sam deploy` prints them, or `aws cloudformation describe-stacks`) — see
/// Config/AWSConfig.example.swift for the same file with placeholders and
/// docs/quickstart-aws.md for where each value comes from. This file is
/// gitignored once filled in with real values; only the example ships.
///
/// If you don't need the cloud control plane at all, use GCS mode instead
/// (see gcs/README.md) - no AWS account or endpoints required, and none of
/// this file's values are read in that mode except by AWSIoTMQTTClient's
/// unused-in-GCS-mode code paths.
enum AWSConfig {
    // MARK: - AWS
    static let region = "YOUR_AWS_REGION"

    // MARK: - API Gateway
    static let apiEndpoint = "https://YOUR_API_ID.execute-api.YOUR_AWS_REGION.amazonaws.com/prod"

    // MARK: - Cognito
    static let identityPoolId = "YOUR_AWS_REGION:YOUR_IDENTITY_POOL_ID"
    static let userPoolId = "YOUR_AWS_REGION_YOUR_USER_POOL_ID"

    // MARK: - IoT Core (for MQTT)
    static let iotEndpoint = "YOUR_IOT_ENDPOINT.iot.YOUR_AWS_REGION.amazonaws.com"

    // MARK: - S3 (for drone images)
    static let imagesBucket = "YOUR_IMAGES_BUCKET"

    // MARK: - Cognito App Client
    static let cognitoClientId = "YOUR_COGNITO_CLIENT_ID"
    static let cognitoHostedUIDomain = "https://YOUR_COGNITO_DOMAIN.auth.YOUR_AWS_REGION.amazoncognito.com"
    static let callbackURLScheme = "droneoperator"
}
