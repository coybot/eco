import SwiftUI
import MapKit
import CoreLocation

/// Map screen (task 3): shows the fleet's live positions and every object
/// they've reported seeing, over the sim's operating area / search circle.
/// Tapping a drone or an object selects it, which opens the same
/// MissionSidebarView the chat screen uses — the two screens share one
/// selection model (StoryboardController.selection) so switching tabs never
/// loses what's open in the sidebar.
///
/// Read-only: unlike AreaDrawMap (the operating-area drawing sheet), this map
/// doesn't take taps as new vertices — it's for watching the mission, not
/// planning it.
struct MissionMapView: View {
    let controller: StoryboardController
    @State private var camera: MapCameraPosition = .automatic
    @State private var hasCentered = false
    // Forces a body re-evaluation every second. MapKit's `Map` content
    // builder does not reliably re-run its per-annotation closures purely
    // from Observation tracking on MQTTService.droneStatuses (this app's own
    // DroneDetailView.MapTabView has the same issue and works around it with
    // an explicit `.onChange` just to re-center the camera) — confirmed live:
    // positions kept flowing into the shared cache (verified via
    // GET /drones/{id}/status) while the map's drone markers sat still.
    // A cheap 1 Hz tick, matching the heartbeat's own ~2s cadence, is a
    // simpler and more robust fix than trying to coax MapContentBuilder into
    // observing a dictionary mutation.
    @State private var refreshTick = Date()
    private let refreshTimer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    /// Live position per drone, straight from the shared MQTT status cache —
    /// this view doesn't own or duplicate that state. Only drones the daemon
    /// has actually reported a fix for (heartbeat's `position`, added by
    /// fw_gcs_daemon.build_heartbeat) show a pin; the rest are simply absent
    /// rather than shown at a stale/default location.
    private var dronePositions: [(id: String, coordinate: CLLocationCoordinate2D, headingDeg: Double?)] {
        controller.droneIds.compactMap { did in
            guard let pos = MQTTService.shared.droneStatuses[did]?.position else { return nil }
            let heading = MQTTService.shared.droneStatuses[did]?.headingDeg
            return (did, CLLocationCoordinate2D(latitude: pos.latitude, longitude: pos.longitude), heading)
        }
    }

    var body: some View {
        // Reading refreshTick here (unused otherwise) is what makes this
        // body actually re-run every tick — see the property's comment.
        let _ = refreshTick
        Map(position: $camera) {
            // The sim's target area, same as AreaDrawMap, so the map reads
            // consistently between the planning sheet and this live view.
            MapCircle(center: controller.searchCenter.clCoordinate,
                      radius: StoryboardController.simSearchRadiusM)
                .foregroundStyle(.blue.opacity(0.10)).stroke(.blue, lineWidth: 1.5)

            if controller.operatingArea.count >= 3 {
                MapPolygon(coordinates: controller.operatingArea.map(\.clCoordinate))
                    .foregroundStyle(.green.opacity(0.12)).stroke(.green, lineWidth: 2)
            }
            ForEach(Array(controller.noFlyZones.enumerated()), id: \.offset) { _, z in
                MapPolygon(coordinates: z.map(\.clCoordinate))
                    .foregroundStyle(.red.opacity(0.18)).stroke(.red, lineWidth: 2)
            }

            ForEach(dronePositions, id: \.id) { drone in
                Annotation(drone.id.uppercased(), coordinate: drone.coordinate) {
                    DroneMapMarker(
                        isSelected: controller.selection == .drone(drone.id),
                        headingDeg: drone.headingDeg
                    )
                    .onTapGesture { controller.select(drone: drone.id) }
                }
            }

            ForEach(controller.objects) { obj in
                if let lat = obj.lat, let lon = obj.lon {
                    Annotation(obj.label.capitalized,
                              coordinate: CLLocationCoordinate2D(latitude: lat, longitude: lon)) {
                        ObjectMapMarker(isSelected: controller.selection == .object(obj.label))
                            .onTapGesture { controller.select(object: obj.label) }
                    }
                }
            }
        }
        .mapStyle(.standard(elevation: .flat, pointsOfInterest: .excludingAll))
        .mapControls {
            MapCompass()
            MapScaleView()
        }
        .onAppear { centerIfNeeded() }
        .onChange(of: dronePositions.count) { _, _ in centerIfNeeded() }
        .onReceive(refreshTimer) { refreshTick = $0 }
    }

    /// Center once, on first data (drones or the target area) — not on every
    /// position update, or the map would fight the operator's own pan/zoom
    /// every couple of seconds as the fleet moves.
    private func centerIfNeeded() {
        guard !hasCentered else { return }
        hasCentered = true
        camera = .region(MKCoordinateRegion(
            center: controller.searchCenter.clCoordinate,
            latitudinalMeters: 1400, longitudinalMeters: 1400))
    }
}

private struct DroneMapMarker: View {
    let isSelected: Bool
    let headingDeg: Double?

    var body: some View {
        Image(systemName: "location.north.circle.fill")
            .font(.title2)
            .foregroundStyle(isSelected ? Color.accentColor : Color.orange)
            .background(Circle().fill(.white))
            .rotationEffect(.degrees(headingDeg ?? 0))
            .shadow(radius: isSelected ? 3 : 1)
    }
}

private struct ObjectMapMarker: View {
    let isSelected: Bool

    var body: some View {
        Image(systemName: "scope")
            .font(.title3)
            .foregroundStyle(.white)
            .padding(6)
            .background(Circle().fill(isSelected ? Color.accentColor : Color.red))
            .shadow(radius: isSelected ? 3 : 1)
    }
}
