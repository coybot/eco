import SwiftUI

/// The right sidebar (tasks 3/4): whichever ONE thing is currently selected —
/// a drone (live video + telemetry) or a sighted object (photo + location).
/// Shared by both the Chat and Map screens via StoryboardController.selection,
/// so tapping a drone chip, a chat bubble, or a map pin all land here the same
/// way. Replaces the old always-on floating PiP: video now streams only for
/// the one drone actually selected, and stops the instant the selection
/// changes or the sidebar closes.
struct MissionSidebarView: View {
    let controller: StoryboardController

    @State private var stream: DroneVideoStream?
    @State private var streamedDroneId: String?

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            ScrollView {
                content
                    .padding()
            }
        }
        .background(.regularMaterial)
        .onAppear { syncStream() }
        .onChange(of: controller.selection) { _, _ in syncStream() }
        .onDisappear { stopStream() }
    }

    private var header: some View {
        HStack {
            Text(title).font(.headline)
            Spacer()
            Button { controller.clearSelection() } label: {
                Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
        }
        .padding()
    }

    private var title: String {
        switch controller.selection {
        case .drone(let id): return id.uppercased()
        case .object(let label): return label.capitalized
        case nil: return ""
        }
    }

    @ViewBuilder private var content: some View {
        switch controller.selection {
        case .drone(let id):
            DroneSidebarContent(droneId: id, stream: stream)
        case .object(let label):
            ObjectSidebarContent(controller: controller, label: label)
        case nil:
            EmptyView()
        }
    }

    /// Starts a video stream for a newly-selected drone and stops the
    /// previous one — mirrors DroneVideoPiPView.syncStreams' lifecycle, just
    /// for at most one drone at a time instead of the whole fleet.
    private func syncStream() {
        guard case .drone(let id) = controller.selection else {
            stopStream()
            return
        }
        guard id != streamedDroneId else { return }
        stopStream()
        guard let base = GCSSettings.shared.videoBaseURLString,
              let s = DroneVideoStream(droneId: id, baseURL: base) else { return }
        s.start()
        stream = s
        streamedDroneId = id
    }

    private func stopStream() {
        stream?.stop()
        stream = nil
        streamedDroneId = nil
    }
}

// MARK: - Drone: video + telemetry

private struct DroneSidebarContent: View {
    let droneId: String
    let stream: DroneVideoStream?

    private var status: DroneStatus? { MQTTService.shared.droneStatuses[droneId] }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            videoPanel
            statusRow
            telemetryGrid
        }
    }

    private var videoPanel: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 12).fill(Color.black)
            if let img = stream?.frame {
                Image(uiImage: img)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            } else {
                VStack(spacing: 6) {
                    ProgressView().tint(.white)
                    Text(GCSSettings.shared.videoBaseURLString == nil
                         ? "No video bridge configured" : "Connecting\u{2026}")
                        .font(.caption).foregroundStyle(.gray)
                }
            }
        }
        .aspectRatio(16 / 9, contentMode: .fit)
    }

    private var statusRow: some View {
        HStack(spacing: 6) {
            Circle()
                .fill((stream?.isConnected ?? false) ? Color.green : Color.gray)
                .frame(width: 8, height: 8)
            Text((stream?.isConnected ?? false) ? "Live" : "Offline")
                .font(.caption).foregroundStyle(.secondary)
            Spacer()
            if let armed = status?.armed {
                Text(armed ? "ARMED" : "DISARMED")
                    .font(.caption2.bold())
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(armed ? Color.red.opacity(0.15) : Color(.tertiarySystemFill),
                                in: Capsule())
                    .foregroundStyle(armed ? .red : .secondary)
            }
        }
    }

    private var telemetryGrid: some View {
        VStack(alignment: .leading, spacing: 8) {
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                TelemetryTile(icon: "battery.100", label: "Battery",
                             value: status?.battery.map { "\(Int($0))%" } ?? "\u{2014}")
                TelemetryTile(icon: "arrow.up.right", label: "Altitude",
                             value: status?.position.map { String(format: "%.0f m", $0.altitude) }
                                    ?? "\u{2014}")
                TelemetryTile(icon: "location.north.line", label: "Heading",
                             value: status?.headingDeg.map { "\(Int($0))\u{00B0}" } ?? "\u{2014}")
                TelemetryTile(icon: "speedometer", label: "Airspeed",
                             value: status?.airspeedMps.map { String(format: "%.0f m/s", $0) }
                                    ?? "\u{2014}")
            }
            if let pos = status?.position {
                Text(String(format: "%.6f, %.6f", pos.latitude, pos.longitude))
                    .font(.caption.monospaced()).foregroundStyle(.secondary)
            }
            if let enu = status?.positionEnu {
                Text(String(format: "local ENU: east %.0f m, north %.0f m", enu.eastM, enu.northM))
                    .font(.caption2).foregroundStyle(.secondary)
            }
            if status?.position == nil {
                Text("No telemetry yet").font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

private struct TelemetryTile: View {
    let icon: String
    let label: String
    let value: String
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label(label, systemImage: icon).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.subheadline.bold())
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color(.secondarySystemBackground)))
    }
}

// MARK: - Object: photo + location

private struct ObjectSidebarContent: View {
    let controller: StoryboardController
    let label: String

    private var object: StoryboardController.SightedObject? {
        controller.objects.first(where: { $0.label == label })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            photo
            if let lat = object?.lat, let lon = object?.lon {
                Text(String(format: "%.6f, %.6f", lat, lon)).font(.caption.monospaced())
            }
            if let e = object?.eastM, let n = object?.northM {
                Text(String(format: "local ENU: east %.0f m, north %.0f m", e, n))
                    .font(.caption).foregroundStyle(.secondary)
            }
            HStack(spacing: 16) {
                if let score = object?.score {
                    Label(String(format: "%.0f%%", score * 100), systemImage: "checkmark.seal")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let hits = object?.hits {
                    Label("\(hits) sighting\(hits == 1 ? "" : "s")", systemImage: "eye")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            // Prefills the chat input rather than sending it outright, so the
            // operator can edit before it goes out — same principle as every
            // other typed tasking in this app.
            Button {
                controller.input = "Fly back to the \(label) and take more angle shots."
                controller.screen = .chat
            } label: {
                Label("More angle shots", systemImage: "arrow.uturn.backward.circle")
            }
            .buttonStyle(.borderedProminent)
        }
    }

    @ViewBuilder private var photo: some View {
        if let url = object?.imageURL {
            AsyncImage(url: url) { $0.resizable().scaledToFit() } placeholder: { ProgressView() }
                .frame(maxHeight: 220)
                .clipShape(RoundedRectangle(cornerRadius: 10))
        } else {
            RoundedRectangle(cornerRadius: 10)
                .fill(Color(.secondarySystemBackground))
                .frame(height: 140)
                .overlay(Text("No photo yet").font(.caption).foregroundStyle(.secondary))
        }
    }
}
