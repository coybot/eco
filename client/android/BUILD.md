# Coybot Drone Android App

Natural-language drone control from your phone. Talks to the same AWS backend the iOS app uses.

## What it does

- **Sign in** with your Cognito account (email + password)
- **Register your drone** by entering its ID (from `config.yaml`)
- **Chat with the drone** — type natural-language commands; Claude translates them and sends code to the drone via IoT Core
- **Polling** — responses appear automatically every 3 seconds (no MQTT wiring needed on the phone)

## Flow

```
Phone → POST /drones/{id}/conversations/{conv}/messages
          → Lambda (conversations.py)
            → Bedrock (Claude Sonnet)
              → IoT Core → daemon.py on Jetson
                → MAVLink → flight controller
                  → result published back via IoT → DynamoDB
Phone ← GET /drones/{id}/conversations/{conv}  (polls every 3s)
```

## Build with Android Studio

1. Open Android Studio → **Open** → select `eco/client/android/`
2. Let Gradle sync finish (downloads ~500 MB on first run)
3. Connect your Android phone (API 28 = Android 9 minimum) or start an emulator
4. **Run** (▶)

## Build from command line

```bash
cd eco/client/android
# macOS / Linux
./gradlew assembleDebug
# Windows
gradlew.bat assembleDebug
```

APK will be at `app/build/outputs/apk/debug/app-debug.apk`.
Sideload it: `adb install app/build/outputs/apk/debug/app-debug.apk`

## Reproducible for a new device

1. Install the APK
2. Sign in with any Cognito account (create users in AWS Cognito console or via CLI)
3. Add your drone by ID — that's it, no PC, no Tailscale

## Config values (top of MainActivity.kt)

```kotlin
private const val API_BASE    = "https://03bnj3wwef.execute-api.us-west-2.amazonaws.com/prod"
private const val COGNITO_REGION    = "us-west-2"
private const val COGNITO_CLIENT_ID = "4j965u17ohomik14cte9ni276h"
```

These are read from the CloudFormation stack outputs. If you redeploy the stack, update these three constants.

## Creating a new user

```bash
aws cognito-idp admin-create-user \
  --user-pool-id us-west-2_MkixOuF3S \
  --username new@example.com \
  --temporary-password TempPass1 \
  --profile coybot --region us-west-2

aws cognito-idp admin-set-user-password \
  --user-pool-id us-west-2_MkixOuF3S \
  --username new@example.com \
  --password "$E2E_TEST_PASSWORD" --permanent \
  --profile coybot --region us-west-2
```
