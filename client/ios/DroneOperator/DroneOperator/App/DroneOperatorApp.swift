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
                    // Connect MQTT when user is authenticated
                    await setupMQTT()
                }
                .onChange(of: authService.isAuthenticated) { _, isAuthenticated in
                    if isAuthenticated {
                        Task { await setupMQTT() }
                    } else {
                        mqttService.disconnect()
                    }
                }
        }
    }
    
    private func setupMQTT() async {
        print("🔌 setupMQTT called - isAuthenticated: \(authService.isAuthenticated), hasToken: \(authService.idToken != nil)")
        
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
            if authService.isAuthenticated {
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
            DroneListView()
                .tabItem {
                    Label("Drones", systemImage: "location.viewfinder")
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
                Section {
                    if let user = authService.currentUser {
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
                    Button(role: .destructive) {
                        authService.signOut()
                    } label: {
                        Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
                    }
                }
            }
            .navigationTitle("Settings")
        }
    }
}

