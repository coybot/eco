// ─────────────────────────────────────────────
// AWS / API configuration for the web operator
// Values are read from environment variables at
// runtime (NEXT_PUBLIC_* are inlined at build).
// ─────────────────────────────────────────────

export const OPERATOR_CONFIG = {
  apiEndpoint:
    process.env.NEXT_PUBLIC_OPERATOR_API_ENDPOINT ??
    "https://w3zf9y7b29.execute-api.us-east-2.amazonaws.com/prod",

  region: process.env.NEXT_PUBLIC_OPERATOR_REGION ?? "us-east-2",

  // Cognito Identity Pool – used to exchange social tokens for AWS credentials
  // (needed for IoT WebSocket SigV4 signing)
  identityPoolId:
    process.env.NEXT_PUBLIC_OPERATOR_IDENTITY_POOL_ID ?? "us-east-2:6da3c153-6ccf-487c-833c-02e81d728135",

  // IoT Core ATS endpoint (wss://<endpoint>/mqtt)
  iotEndpoint:
    process.env.NEXT_PUBLIC_OPERATOR_IOT_ENDPOINT ?? "a8n91fgngz353-ats.iot.us-east-2.amazonaws.com",

  // Cognito User Pool configuration
  cognitoUserPoolId:
    process.env.NEXT_PUBLIC_OPERATOR_COGNITO_USER_POOL_ID ?? "us-east-2_K16WBUtfm",

  cognitoAppClientId:
    process.env.NEXT_PUBLIC_OPERATOR_COGNITO_APP_CLIENT_ID ?? "5fvrfsuln61as13mtu4um6oquu",

  cognitoRegion:
    process.env.NEXT_PUBLIC_OPERATOR_COGNITO_REGION ?? "us-east-2",

  // Cognito Hosted UI URL (e.g., https://<domain>.auth.us-east-2.amazoncognito.com)
  cognitoHostedUiUrl:
    process.env.NEXT_PUBLIC_OPERATOR_COGNITO_HOSTED_UI_URL ?? "https://coybot-drone-auth-dev.auth.us-east-2.amazoncognito.com",

  // Cognito OAuth callback URL
  cognitoCallbackUrl:
    process.env.NEXT_PUBLIC_OPERATOR_COGNITO_CALLBACK_URL ?? "",
} as const
