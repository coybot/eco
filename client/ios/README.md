# Drone Operator iOS App

SwiftUI app for controlling drones with natural language commands.

## Features

- 🔐 **Authentication** - Sign in with Apple or Google
- 🚁 **Drone Management** - Register, list, and manage your drones
- 💬 **Natural Language Commands** - "Take off to 5 meters", "Spin motor 1"
- 📡 **Status Polling** - Automatic status updates via API
- 🎨 **Modern UI** - Dark theme with smooth animations

## Requirements

- iOS 17.0+
- Xcode 15.0+
- AWS SAM stack deployed (see main README)

## Project Setup

### 1. Create Xcode Project

Since Xcode projects are complex binary files, create a new project:

1. Open Xcode → File → New → Project
2. Choose **App** under iOS
3. Settings:
   - Product Name: `DroneOperator`
   - Team: Your team
   - Organization Identifier: `com.yourcompany`
   - Interface: **SwiftUI**
   - Language: **Swift**
   - Storage: None
   - Uncheck Include Tests

4. Save to `client/ios/DroneOperator/`

### 2. Add Source Files

Copy the Swift files from this folder structure into your Xcode project:

```
DroneOperator/
├── App/
│   └── DroneOperatorApp.swift
├── Config/
│   └── AWSConfig.swift
├── Models/
│   ├── Drone.swift
│   └── User.swift
├── Services/
│   ├── AuthService.swift
│   ├── APIClient.swift
│   ├── DroneSetupService.swift
│   └── Logger.swift
└── Views/
    ├── Auth/
    │   └── AuthView.swift
    └── Drones/
        ├── DroneListView.swift
        ├── DroneDetailView.swift
        ├── AddDroneView.swift
        └── SetupDroneView.swift
```

### 3. Configure AWS

After deploying the SAM stack, get your configuration values:

```bash
sam list stack-outputs --stack-name drone-api --region us-west-2
```

Update `Config/AWSConfig.swift`:

```swift
enum AWSConfig {
    static let region = "us-west-2"
    static let apiEndpoint = "https://XXXXXXXXXX.execute-api.us-west-2.amazonaws.com/prod"
    static let googleClientId = "YOUR_CLIENT_ID.apps.googleusercontent.com"
}
```

### 4. Configure Sign in with Apple

1. In Xcode → Signing & Capabilities → + Capability → Sign in with Apple
2. In Apple Developer Portal:
   - Enable Sign in with Apple for your App ID

### 5. Configure Google Sign-In (Optional)

1. Create OAuth 2.0 credentials in Google Cloud Console
2. Set the iOS URL scheme: `com.googleusercontent.apps.YOUR_CLIENT_ID`
3. Add to `AWSConfig.swift`:
   ```swift
   static let googleClientId = "YOUR_CLIENT_ID.apps.googleusercontent.com"
   ```
4. Add URL Scheme in Info → URL Types

### 6. Configure URL Schemes

Add to Info.plist (or via Xcode UI):

```xml
<key>CFBundleURLTypes</key>
<array>
    <dict>
        <key>CFBundleURLSchemes</key>
        <array>
            <string>droneoperator</string>
        </array>
    </dict>
</array>
```

### 7. Add Local Network Permission

Required for drone WiFi provisioning (talking to `192.168.4.1` on drone's hotspot):

```xml
<key>NSLocalNetworkUsageDescription</key>
<string>Drone Operator needs local network access to communicate with your drone during setup.</string>
```

### 8. Add Hotspot Configuration Capability

Enable **Hotspot Configuration** in Signing & Capabilities so the app can
join the drone access point using `NEHotspotConfigurationManager`.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         iOS App                             │
│                                                             │
│  ┌────────────┐  ┌────────────┐  ┌───────────────────────┐  │
│  │AuthService │  │ APIClient  │  │  DroneSetupService    │  │
│  │(OAuth)     │  │(REST+poll) │  │  (local HTTP)         │  │
│  └─────┬──────┘  └─────┬──────┘  └───────────┬───────────┘  │
│        │               │                     │              │
└────────┼───────────────┼─────────────────────┼──────────────┘
         │               │                     │
         │               ▼                     ▼
         │      ┌───────────────┐      ┌─────────────┐
         └─────▶│  API Gateway  │      │ Drone (AP)  │
   (Bearer)     │  + Authorizer │      │ 192.168.4.1:80 │
                └───────┬───────┘      └─────────────┘
                        │
                        ▼
                  ┌───────────┐
                  │  Lambda   │──────▶ DynamoDB
                  └─────┬─────┘
                        │
                        ▼
                 ┌────────────┐
                 │   Drone    │
                 └────────────┘
```

**Status Flow:** Drone → MQTT → DynamoDB → API (polled every 3s) → iOS  
**Setup Flow:** iOS → Drone Hotspot (192.168.4.1) → WiFi credentials → Drone connects → Cloud registration

## Key Components

### AuthService

Handles social authentication:
- Sign in with Apple
- Sign in with Google (OAuth PKCE)
- Token persistence in Keychain

### APIClient

REST client for API Gateway:
- List registered drones
- Register/unregister drones
- Send natural language commands
- Poll drone status (every 3 seconds)

### DroneSetupService

Handles WiFi provisioning flow:
- Detects when connected to drone's hotspot
- Fetches drone info from `http://192.168.4.1/info`
- Sends WiFi credentials to `http://192.168.4.1/configure`
- Monitors connection status during setup

### Logger

Centralized logging with categories:
- Auth, API, UI, Setup events
- Automatic error context capture

## UI Components

| View | Description |
|------|-------------|
| `AuthView` | Sign in with Apple or Google |
| `DroneListView` | Grid of registered drones with live status |
| `DroneDetailView` | Full telemetry, command input, history |
| `AddDroneView` | Register existing drone or start setup wizard |
| `SetupDroneView` | Multi-step wizard for hotspot-based drone setup |
| `SettingsView` | User profile and sign out |

## Customization

### Theming

Colors are defined inline with a dark theme. Key colors:
- Background: `rgb(13, 13, 25)` to `rgb(25, 25, 51)`
- Accent: Cyan (`#00E5FF`)
- Success: Green
- Warning: Orange
- Error: Red

### Adding Quick Commands

In `DroneDetailView.swift`, add to the `commandSection`:

```swift
QuickCommandButton(text: "Your Command") {
    commandText = "your natural language command"
    sendCommand()
}
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Sign in failed" | Check Google Client ID in `AWSConfig.swift` |
| No drones appearing | Ensure AWS stack is deployed and API endpoint is correct |
| Status not updating | Check drone is online and publishing status |
| Apple Sign-In fails | Check capability is added and App ID configured |

## Development

### Adding a New Feature

1. Models go in `Models/`
2. API/Service logic in `Services/`
3. UI components in `Views/`
4. Use `@Observable` for state management
5. Follow existing patterns for consistency

### Testing Commands

Before testing on a real drone:
1. Check the generated code in command history
2. Use motor test commands with propellers removed
3. Have emergency disconnect ready

