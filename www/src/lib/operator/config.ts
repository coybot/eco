// ─────────────────────────────────────────────
// AWS / API configuration for the web operator
// Values are read from environment variables at
// runtime (NEXT_PUBLIC_* are inlined at build).
// ─────────────────────────────────────────────

export const OPERATOR_CONFIG = {
  apiEndpoint:
    process.env.NEXT_PUBLIC_OPERATOR_API_ENDPOINT ??
    "https://03bnj3wwef.execute-api.us-west-2.amazonaws.com/prod",

  region: process.env.NEXT_PUBLIC_OPERATOR_REGION ?? "us-west-2",

  // Cognito Identity Pool – used to exchange social tokens for AWS credentials
  // (needed for IoT WebSocket SigV4 signing)
  identityPoolId:
    process.env.NEXT_PUBLIC_OPERATOR_IDENTITY_POOL_ID ?? "",

  // IoT Core ATS endpoint (wss://<endpoint>/mqtt)
  iotEndpoint:
    process.env.NEXT_PUBLIC_OPERATOR_IOT_ENDPOINT ?? "",

  // Cognito User Pool configuration
  cognitoUserPoolId:
    process.env.NEXT_PUBLIC_OPERATOR_COGNITO_USER_POOL_ID ?? "",

  cognitoAppClientId:
    process.env.NEXT_PUBLIC_OPERATOR_COGNITO_APP_CLIENT_ID ?? "",

  cognitoRegion:
    process.env.NEXT_PUBLIC_OPERATOR_COGNITO_REGION ?? "us-west-2",

  // Cognito Hosted UI URL (e.g., https://<domain>.auth.us-west-2.amazoncognito.com)
  cognitoHostedUiUrl:
    process.env.NEXT_PUBLIC_OPERATOR_COGNITO_HOSTED_UI_URL ?? "",

  // Cognito OAuth callback URL
  cognitoCallbackUrl:
    process.env.NEXT_PUBLIC_OPERATOR_COGNITO_CALLBACK_URL ?? "",
} as const
