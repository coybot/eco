import SwiftUI

@main
struct DroneOperatorApp: App {
    
    @State private var authService = AuthService.shared
    @State private var apiClient = APIClient.shared
    @State private var mqttService = MQTTService.shared
    
    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(authService)
                .environment(apiClient)
                .environment(mqttService)
                .task {
                    // Connect MQTT when authenticated (cloud) or already
                    // configured (GCS - no sign-in needed, see ContentView)
                    await setupMQTT()
                }
                .onChange(of: authService.isAuthenticated) { _, isAuthenticated in
                    if isAuthenticated || GCSSettings.shared.isGCSMode {
                        Task { await setupMQTT() }
                    } else {
                        mqttService.disconnect()
                    }
                }
        }
    }

    private func setupMQTT() async {
        let isGCSMode = GCSSettings.shared.isGCSMode
        print("🔌 setupMQTT called - isGCSMode: \(isGCSMode), isAuthenticated: \(authService.isAuthenticated), hasToken: \(authService.idToken != nil)")

        if isGCSMode {
            guard !GCSSettings.shared.pairingToken.isEmpty else {
                print("🔌 setupMQTT: GCS mode but no pairing token set yet, skipping MQTT")
                return
            }
            do {
                try await mqttService.connect(withToken: "")
                print("🔌 setupMQTT: GCS MQTT connected successfully!")
            } catch {
                print("❌ setupMQTT: GCS MQTT connection failed: \(error)")
                AppLogger.mqtt.error("Failed to connect GCS MQTT: \(error.localizedDescription)")
            }
            return
        }

        guard authService.isAuthenticated else {
            print("🔌 setupMQTT: Not authenticated, skipping MQTT")
            return
        }

        // Proactively refresh token if needed before connecting
        do {
            try await authService.refreshTokensIfNeeded()
        } catch {
            // If refresh fails, we might still have a valid token - continue
            print("⚠️ setupMQTT: Token refresh failed, continuing with existing token")
        }

        guard let token = authService.idToken else {
            print("🔌 setupMQTT: No token available, skipping MQTT")
            return
        }

        print("🔌 setupMQTT: Calling mqttService.connect...")
        do {
            try await mqttService.connect(withToken: token)
            print("🔌 setupMQTT: MQTT connected successfully!")
        } catch {
            print("❌ setupMQTT: MQTT connection failed: \(error)")
            AppLogger.mqtt.error("Failed to connect MQTT: \(error.localizedDescription)")
        }
    }
}

struct ContentView: View {
    @Environment(AuthService.self) private var authService

    var body: some View {
        Group {
            // GCS mode has no Cognito/Google/Apple sign-in - a pairing token
            // from Settings is all that's needed, so skip AuthView entirely.
            if authService.isAuthenticated || GCSSettings.shared.isGCSMode {
                MainTabView()
            } else {
                AuthView()
            }
        }
        .animation(.easeInOut(duration: 0.3), value: authService.isAuthenticated)
    }
}

struct MainTabView: View {
    var body: some View {
        TabView {
            ChatsView()
                .tabItem {
                    Label("Missions", systemImage: "paperplane")
                }

            SettingsView()
                .tabItem {
                    Label("Settings", systemImage: "gear")
                }
        }
        .tint(Color("AccentColor"))
    }
}

struct SettingsView: View {
    @Environment(AuthService.self) private var authService
    
    var body: some View {
        NavigationStack {
            List {
                if let user = authService.currentUser {
                    Section {
                        HStack {
                            Image(systemName: "person.circle.fill")
                                .font(.system(size: 50))
                                .foregroundStyle(.secondary)

                            VStack(alignment: .leading, spacing: 4) {
                                Text(user.displayName)
                                    .font(.headline)
                                if let email = user.email {
                                    Text(email)
                                        .font(.subheadline)
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                        .padding(.vertical, 8)
                    }
                }

                Section {
                    NavigationLink {
                        ControlPlaneSettingsView()
                    } label: {
                        Label("Ground Control Station", systemImage: "antenna.radiowaves.left.and.right")
                    }
                }

                if !GCSSettings.shared.isGCSMode {
                    Section {
                        Button(role: .destructive) {
                            authService.signOut()
                        } label: {
                            Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
                        }
                    }
                }
            }
            .navigationTitle("Settings")
        }
    }
}

