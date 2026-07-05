import Foundation

/// AWS and auth endpoints for the RoverOperator app — forked from DroneOperator's
/// AWSConfig.swift. Same `drone-api` SAM stack (us-west-2); the rover reuses eco's
/// existing Cognito user pool and API Gateway rather than standing up new cloud infra.
enum AWSConfig {
    // MARK: - AWS
    static let region = "us-west-2"

    // MARK: - API Gateway
    static let apiEndpoint = "https://03bnj3wwef.execute-api.us-west-2.amazonaws.com/prod"

    // MARK: - Cognito
    static let identityPoolId = "us-west-2:4f98bb63-512b-4772-861f-8a9bed4e8727"
    static let userPoolId = "us-west-2_MkixOuF3S"

    // MARK: - IoT Core (for MQTT — not yet used by RoverOperator; see Phase 3 in
    // eco/rover/docs/architecture.md)
    static let iotEndpoint = "a3c6a8oie6d6k5-ats.iot.us-west-2.amazonaws.com"

    // MARK: - Cognito App Client
    static let cognitoClientId = "4j965u17ohomik14cte9ni276h"
}
