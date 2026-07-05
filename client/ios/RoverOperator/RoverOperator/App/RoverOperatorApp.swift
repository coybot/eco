import SwiftUI

@main
struct RoverOperatorApp: App {
    // Core singletons for the session. NavigationController is shared across tabs:
    // DriveView, NavigateView, and ConversationView (via DialogAgent) must all act on
    // the same in-flight goal/path.
    @State private var auth = AuthService.shared
    @State private var mqtt = MQTTService.shared
    @State private var ar: ARSessionManager
    @State private var control: RoverControl
    @State private var nav: NavigationController
    @State private var telemetry: RoverTelemetryPublisher

    init() {
        let ar = ARSessionManager()
        let control = RoverControl()
        let nav = NavigationController(ar: ar, control: control)
        _ar = State(initialValue: ar)
        _control = State(initialValue: control)
        _nav = State(initialValue: nav)
        _telemetry = State(initialValue: RoverTelemetryPublisher(ar: ar, nav: nav))
    }

    var body: some Scene {
        WindowGroup {
            RootView(ar: ar, control: control, nav: nav)
                .environment(auth)
                .environment(mqtt)
                .task(id: auth.isAuthenticated) {
                    // ClaudeDialogClient only needs a token when escalating, but keep it
                    // current with auth state rather than reading it lazily per-request.
                    await ClaudeDialogClient.shared.setTokenProvider { await AuthService.shared.idToken }
                    await setupTelemetry()
                }
        }
    }

    private func setupTelemetry() async {
        guard auth.isAuthenticated else {
            mqtt.disconnect()
            telemetry.stop()
            return
        }
        guard let token = auth.idToken else { return }
        do {
            try await mqtt.connect(withToken: token)
            telemetry.start()
        } catch {
            AppLogger.nav.error("MQTT connect failed: \(error.localizedDescription)")
        }
    }
}

struct RootView: View {
    @Environment(AuthService.self) private var authService
    let ar: ARSessionManager
    let control: RoverControl
    let nav: NavigationController

    var body: some View {
        Group {
            if authService.isAuthenticated {
                TabView {
                    DriveView(ar: ar, control: control)
                        .tabItem { Label("Drive", systemImage: "gamecontroller") }
                    NavigateView(ar: ar, nav: nav)
                        .tabItem { Label("Navigate", systemImage: "map") }
                    ConversationView(nav: nav)
                        .tabItem { Label("Talk", systemImage: "bubble.left.and.bubble.right") }
                }
                .onAppear { ar.start() }
            } else {
                AuthView()
            }
        }
    }
}
