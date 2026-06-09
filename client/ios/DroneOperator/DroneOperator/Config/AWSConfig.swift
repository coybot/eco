import Foundation

/// AWS and auth endpoints for the DroneOperator app.
/// Values are sourced from the deployed `drone-api` SAM stack in us-west-2.
enum AWSConfig {
    // MARK: - AWS
    static let region = "us-west-2"

    // MARK: - API Gateway
    static let apiEndpoint = "https://03bnj3wwef.execute-api.us-west-2.amazonaws.com/prod"

    // MARK: - Cognito
    static let identityPoolId = "us-west-2:4f98bb63-512b-4772-861f-8a9bed4e8727"
    static let userPoolId = "us-west-2_MkixOuF3S"

    // MARK: - IoT Core (for MQTT)
    static let iotEndpoint = "a3c6a8oie6d6k5-ats.iot.us-west-2.amazonaws.com"

    // MARK: - S3 (for drone images)
    static let imagesBucket = "drone-images-dev-us-west-2-041686205727"

    // MARK: - Cognito App Client
    static let cognitoClientId = "4j965u17ohomik14cte9ni276h"
    static let cognitoHostedUIDomain = "https://drone-auth-dev-041686205727.auth.us-west-2.amazoncognito.com"
    static let callbackURLScheme = "droneoperator"
}
