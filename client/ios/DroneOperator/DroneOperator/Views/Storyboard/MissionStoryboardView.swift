import SwiftUI
import MapKit
import CoreLocation

/// Natural-language mission, as a chat. The operator types a mission; plans,
/// clarifying questions, live status and the summary all stream back into the
/// same thread. Reuses APIClient + MQTTService through StoryboardController.
struct MissionStoryboardView: View {
    @State private var controller = StoryboardController()

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                transcript
                Divider()
                inputBar
            }
            .navigationTitle("Mission")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { controller.reset() } label: {
                        Image(systemName: "square.and.pencil")
                    }
                }
            }
            .sheet(isPresented: Binding(get: { controller.showMap },
                                        set: { controller.showMap = $0 })) {
                AreaMapSheet(controller: controller)
            }
        }
        .task { await controller.begin() }
        .onDisappear { controller.teardown() }
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    ForEach(controller.chat) { item in
                        MissionChatRow(item: item, controller: controller).id(item.id)
                    }
                }
                .padding()
            }
            .onChange(of: controller.chat.count) { _, _ in
                if let last = controller.chat.last {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
        }
    }

    private var inputBar: some View {
        HStack(spacing: 8) {
            Image(systemName: "mic.slash").foregroundStyle(.secondary)
            TextField(placeholder, text: Binding(get: { controller.input },
                                                 set: { controller.input = $0 }),
                      axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(1...4)
                .onSubmit { controller.submit() }
            Button { controller.submit() } label: {
                Image(systemName: "arrow.up.circle.fill").font(.title2)
            }
            .disabled(controller.input.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .padding(10)
        .background(.thinMaterial)
    }

    private var placeholder: String {
        switch controller.phase {
        case .awaitingMission: return "Describe your mission\u{2026}"
        case .awaitingClarification: return "Answer\u{2026}"
        case .awaitingSelection: return "Say \u{201C}go\u{201D}, or tap a plan"
        default: return "Message"
        }
    }
}

// MARK: - Chat rows

private struct MissionChatRow: View {
    let item: StoryboardController.Item
    let controller: StoryboardController

    var body: some View {
        switch item {
        case .user(let t):
            Bubble(text: t, mine: true)
        case .drone(let t):
            Bubble(text: t, mine: false)
        case .mapPrompt:
            Button {
                controller.drawMode = .operatingArea
                controller.showMap = true
            } label: {
                Label("Mark area", systemImage: "map")
                    .padding(.vertical, 8).padding(.horizontal, 14)
            }
            .buttonStyle(.borderedProminent)
        case .plans(let plans, let rec):
            VStack(spacing: 10) {
                ForEach(plans) { p in
                    PlanCard(plan: p, isRecommended: p.planId == rec)
                        .onTapGesture { Task { await controller.select(p) } }
                }
            }
        case .progress(let drone, let p):
            DroneStatusCard(droneId: drone, progress: p)
        case .target(let fix, let url):
            TargetFoundCard(target: fix, imageURL: url)
        case .summary(let s):
            VStack(alignment: .leading, spacing: 10) {
                Label("Mission summary", systemImage: "checkmark.seal.fill")
                    .font(.headline).foregroundStyle(.green)
                Text(s)
                Button("New mission") { controller.reset() }.buttonStyle(.bordered)
            }
            .padding()
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 14).fill(Color.green.opacity(0.10)))
        }
    }
}

private struct Bubble: View {
    let text: String
    let mine: Bool
    var body: some View {
        HStack {
            if mine { Spacer(minLength: 40) }
            Text(text)
                .padding(.vertical, 8).padding(.horizontal, 12)
                .background(mine ? Color.accentColor : Color(.secondarySystemBackground),
                            in: RoundedRectangle(cornerRadius: 16))
                .foregroundStyle(mine ? .white : .primary)
            if !mine { Spacer(minLength: 40) }
        }
    }
}

// MARK: - Map sheet

private struct AreaMapSheet: View {
    let controller: StoryboardController
    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                AreaDrawMap(controller: controller)
                controls
            }
            .navigationTitle("Operating area")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { controller.finishArea() }
                        .disabled(!controller.canSubmitArea)
                }
            }
        }
    }

    private var controls: some View {
        VStack(spacing: 10) {
            Picker("Draw", selection: Binding(get: { controller.drawMode },
                                              set: { controller.drawMode = $0 })) {
                ForEach(StoryboardController.DrawMode.allCases, id: \.self) { Text($0.rawValue).tag($0) }
            }.pickerStyle(.segmented)
            Text(controller.drawMode == .operatingArea
                 ? "Tap around the blue target area to outline the operating area, then Finish shape — or use Suggested area."
                 : "Tap to outline a no-fly zone, then Finish shape.")
                .font(.footnote).foregroundStyle(.secondary)
            HStack {
                Button("Undo point") { if !controller.draftPolygon.isEmpty { controller.draftPolygon.removeLast() } }
                    .disabled(controller.draftPolygon.isEmpty)
                Spacer()
                Button("Finish shape") { controller.completeDraftPolygon() }
                    .disabled(controller.draftPolygon.count < 3)
            }.buttonStyle(.bordered)
            Button("Use suggested area") { controller.useSuggestedArea() }
                .buttonStyle(.borderedProminent)
        }
        .padding()
        .background(.thinMaterial)
    }
}

private struct AreaDrawMap: View {
    let controller: StoryboardController
    @State private var camera: MapCameraPosition = .automatic
    var body: some View {
        MapReader { proxy in
            Map(position: $camera) {
                // The sim's target area, so you draw over the right place.
                MapCircle(center: controller.searchCenter.clCoordinate,
                          radius: StoryboardController.simSearchRadiusM)
                    .foregroundStyle(.blue.opacity(0.12)).stroke(.blue, lineWidth: 1.5)
                Marker("Target area", systemImage: "scope",
                       coordinate: controller.searchCenter.clCoordinate).tint(.blue)
                if controller.operatingArea.count >= 3 {
                    MapPolygon(coordinates: controller.operatingArea.map(\.clCoordinate))
                        .foregroundStyle(.green.opacity(0.15)).stroke(.green, lineWidth: 2)
                }
                ForEach(Array(controller.noFlyZones.enumerated()), id: \.offset) { _, z in
                    MapPolygon(coordinates: z.map(\.clCoordinate))
                        .foregroundStyle(.red.opacity(0.2)).stroke(.red, lineWidth: 2)
                }
                if !controller.draftPolygon.isEmpty {
                    MapPolyline(coordinates: controller.draftPolygon.map(\.clCoordinate))
                        .stroke(controller.drawMode == .noFlyZone ? .red : .green,
                                style: StrokeStyle(lineWidth: 2, dash: [6, 4]))
                    ForEach(Array(controller.draftPolygon.enumerated()), id: \.offset) { _, c in
                        Marker("", systemImage: "circle.fill", coordinate: c.clCoordinate)
                            .tint(controller.drawMode == .noFlyZone ? .red : .green)
                    }
                }
            }
            .mapStyle(.standard(elevation: .flat, pointsOfInterest: .excludingAll))
            .onTapGesture(coordinateSpace: .local) { loc in
                if let c = proxy.convert(loc, from: .local) {
                    controller.addVertex(GeoCoordinate(lat: c.latitude, lon: c.longitude))
                }
            }
            .onAppear {
                // Centre on the sim's target area (not the whole map).
                camera = .region(MKCoordinateRegion(
                    center: controller.searchCenter.clCoordinate,
                    latitudinalMeters: 1400, longitudinalMeters: 1400))
            }
        }
    }
}

// MARK: - Cards

private struct PlanCard: View {
    let plan: MissionPlanOption
    let isRecommended: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(plan.label).font(.headline)
                if isRecommended {
                    Text("Recommended").font(.caption.bold())
                        .padding(.horizontal, 8).padding(.vertical, 2)
                        .background(.tint, in: Capsule()).foregroundStyle(.white)
                }
                Spacer()
                Label("\(plan.estMinutes, specifier: "%.0f") min", systemImage: "clock")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            Text(plan.rationale).font(.subheadline).foregroundStyle(.secondary)
            if !plan.nfzClear {
                Label("Clips a no-fly zone", systemImage: "exclamationmark.triangle.fill")
                    .font(.caption).foregroundStyle(.orange)
            }
            ForEach(plan.perDrone) { d in
                Text("\u{2022} \(d.droneId): " + d.phases.map(\.label).joined(separator: " \u{2192} "))
                    .font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 14).fill(Color(.secondarySystemBackground)))
        .overlay(RoundedRectangle(cornerRadius: 14)
            .strokeBorder(isRecommended ? Color.accentColor : .clear, lineWidth: 2))
    }
}

private struct DroneStatusCard: View {
    let droneId: String
    let progress: MessageContent.MissionProgress
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(droneId).font(.headline)
            Text(progress.objective).font(.subheadline)
            ProgressView(value: progress.percentComplete)
            Text("Phase \(progress.phase) of \(progress.totalPhases) \u{00B7} \(progress.status)")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 14).fill(Color(.secondarySystemBackground)))
    }
}

private struct TargetFoundCard: View {
    let target: StoryboardController.TargetFix
    let imageURL: URL?
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Target localized", systemImage: "scope").font(.headline).foregroundStyle(.green)
            Text(target.label.capitalized).font(.subheadline)
            if let lat = target.lat, let lon = target.lon {
                Text(String(format: "%.6f, %.6f", lat, lon)).font(.caption.monospaced())
            }
            if let e = target.eastM, let n = target.northM {
                Text(String(format: "local ENU: east %.0f m, north %.0f m", e, n))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let url = imageURL {
                AsyncImage(url: url) { $0.resizable().scaledToFit() } placeholder: { ProgressView() }
                    .frame(maxHeight: 220).clipShape(RoundedRectangle(cornerRadius: 10))
            }
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 14).fill(Color.green.opacity(0.10)))
    }
}

extension GeoCoordinate {
    var clCoordinate: CLLocationCoordinate2D {
        CLLocationCoordinate2D(latitude: lat, longitude: lon)
    }
}
