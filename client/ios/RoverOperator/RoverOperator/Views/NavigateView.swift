import SwiftUI
import RoverNav

/// Phase-1 autonomy UI: relocalize into a saved map, pick a goal (a saved place or a
/// point N meters ahead), and watch the rover drive there. Full tap-on-map goal picking
/// is a follow-up; this proves the closed autonomy loop end-to-end.
struct NavigateView: View {
    let ar: ARSessionManager
    let nav: NavigationController

    @State private var places: [String: Vec2] = WorldMapStore.places()

    var body: some View {
        NavigationStack {
            List {
                Section("Status") {
                    Text("Tracking: \(trackingLabel)")
                    Text("Nav: \(stateLabel)")
                    if let p = ar.pose {
                        Text(String(format: "x %.2f  y %.2f", p.position.x, p.position.y))
                            .font(.system(.footnote, design: .monospaced))
                    }
                }

                Section("Go to a saved place") {
                    if places.isEmpty {
                        Text("No places saved yet.").foregroundStyle(.secondary)
                    }
                    ForEach(places.keys.sorted(), id: \.self) { name in
                        Button(name) { nav.navigate(to: places[name]!) }
                    }
                }

                Section("Quick test") {
                    Button("Drive 3 m ahead") {
                        guard let p = ar.pose else { return }
                        let goal = p.position + p.forward * 3.0
                        nav.navigate(to: goal)
                    }
                    Button("Save current spot as place…") { saveHere() }
                }

                Section {
                    Button(role: .destructive) { nav.cancel() } label: {
                        Label("Stop", systemImage: "stop.circle")
                    }
                }
            }
            .navigationTitle("Navigate")
        }
    }

    private func saveHere() {
        guard let p = ar.pose else { return }
        let name = "place-\(places.count + 1)"
        try? WorldMapStore.setPlace(name, at: p.position)
        places = WorldMapStore.places()
    }

    private var trackingLabel: String {
        switch ar.trackingState {
        case .normal: return "normal"; case .limited: return "limited"
        case .notAvailable: return "none"; @unknown default: return "?"
        }
    }

    private var stateLabel: String {
        switch nav.state {
        case .idle: return "idle"
        case .planning: return "planning"
        case .driving: return "driving"
        case .arrived: return "arrived ✅"
        case .failed(let m): return "failed: \(m)"
        }
    }
}
