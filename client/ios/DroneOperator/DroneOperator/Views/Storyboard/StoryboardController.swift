import Foundation
import Observation

/// Drives the natural-language mission as a CHAT: the operator types a mission,
/// the system replies in the thread (clarifying questions, plan options, acks),
/// and the drones stream live updates back as messages. All networking reuses
/// APIClient (the /plan + /plan/select routes); live status reuses MQTTService.
///
/// The map (operating area + no-fly zones) is the one non-text step — it opens
/// as a sheet from an inline chat prompt, then the conversation continues.
@Observable
@MainActor
final class StoryboardController {

    // Where we are in the conversation, so a typed line is interpreted right.
    enum Phase { case awaitingMission, awaitingArea, awaitingSelection,
                 awaitingClarification, executing, done }

    // One entry in the chat transcript.
    enum Item: Identifiable {
        case user(String)
        case drone(String)                                   // system/drone text
        case mapPrompt                                       // "mark the area" button
        case plans([MissionPlanOption], recommended: String?)
        case progress(drone: String, MessageContent.MissionProgress)
        case target(TargetFix, imageURL: URL?)
        case summary(String)
        var id: String {
            switch self {
            case .user(let t): return "u-\(t.hashValue)-\(abs(t.count))"
            case .drone(let t): return "d-\(t.hashValue)"
            case .mapPrompt: return "map"
            case .plans(let p, _): return "plans-" + p.map(\.planId).joined()
            case .progress(let d, _): return "prog-\(d)"       // one live row per drone
            case .target(let t, _): return "target-\(t.label)"
            case .summary(let s): return "sum-\(s.hashValue)"
            }
        }
    }

    var phase: Phase = .awaitingMission
    var chat: [Item] = []
    var input: String = ""
    var showMap = false
    var isBusy = false

    // Mission tasking (accumulates operator clarifications).
    var missionText: String = ""

    // Drawn geometry.
    var operatingArea: [GeoCoordinate] = []
    var noFlyZones: [[GeoCoordinate]] = []
    var draftPolygon: [GeoCoordinate] = []
    enum DrawMode: String, CaseIterable { case operatingArea = "Operating area"
                                          case noFlyZone = "No-fly zone" }
    var drawMode: DrawMode = .operatingArea

    // Fleet / conversation.
    var droneIds: [String] = []
    var conversationId: String = ""
    var leadDroneId: String { droneIds.first ?? "" }

    var targetLocation: TargetFix?

    // MARK: - Sim-scene mapping
    //
    // The Godot world is a local ENU sandbox (metres from an origin); the map is
    // real-world lat/lon. This datum ties the sim origin to a real lat/lon so the
    // map is coherent — it MUST match the daemon's --datum-lat/--datum-lon and the
    // sim's search-area centre (surveil_truck: ENU east=400, north=0, r=120). The
    // map centres here and shows the target area, so what you draw lines up with
    // where the drones actually fly.
    static let simDatum = GeoCoordinate(lat: 37.405, lon: -122.100)
    static let simSearchEast = 400.0
    static let simSearchNorth = 0.0
    static let simSearchRadiusM = 120.0

    /// lat/lon of a local ENU point, about the datum (inverse of the daemon's
    /// enu_to_latlon).
    static func coord(east: Double, north: Double) -> GeoCoordinate {
        let er = 6_371_000.0
        let lat = simDatum.lat + north / er * 180.0 / .pi
        let lon = simDatum.lon + east / (er * cos(simDatum.lat * .pi / 180.0)) * 180.0 / .pi
        return GeoCoordinate(lat: lat, lon: lon)
    }
    var searchCenter: GeoCoordinate {
        Self.coord(east: Self.simSearchEast, north: Self.simSearchNorth)
    }

    /// Fill the operating area with a sensible box around the sim's target area,
    /// so the operator can proceed with one tap instead of drawing by hand.
    func useSuggestedArea() {
        let c = searchCenter
        let dLat = 0.0045, dLon = 0.0060   // ~500 m box
        operatingArea = [
            GeoCoordinate(lat: c.lat - dLat, lon: c.lon - dLon),
            GeoCoordinate(lat: c.lat - dLat, lon: c.lon + dLon),
            GeoCoordinate(lat: c.lat + dLat, lon: c.lon + dLon),
            GeoCoordinate(lat: c.lat + dLat, lon: c.lon - dLon),
        ]
        draftPolygon = []
    }

    private let api = APIClient.shared
    private let mqtt = MQTTService.shared
    private var subscribedTopics: [String] = []

    struct TargetFix: Equatable {
        let label: String
        let lat: Double?; let lon: Double?
        let eastM: Double?; let northM: Double?
    }

    // MARK: - Lifecycle

    func begin() async {
        guard chat.isEmpty else { return }
        say("Hi — I'm your mission assistant. Describe a surveillance mission in "
            + "plain language and I'll plan it with your drones. For example: "
            + "\u{201C}Fly two drones to the target area, find and localize the "
            + "pickup truck, and give me a summary.\u{201D}")
        do {
            let drones = try await api.listDrones()
            droneIds = drones.map { $0.droneId }.sorted()
            guard let lead = droneIds.first else {
                say("No drones are registered yet. Register your fleet, then come back.")
                return
            }
            conversationId = try await api.startConversation(droneId: lead).conversationId
        } catch {
            say("I couldn't reach the ground control station: \(error.localizedDescription)")
        }
    }

    // MARK: - Operator input (typed today; a mic layer can call submit() later)

    func submit() {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        input = ""
        guard !text.isEmpty else { return }
        chat.append(.user(text))
        switch phase {
        case .awaitingMission:
            missionText = text
            promptForArea()
        case .awaitingClarification:
            missionText += " " + text          // fold the answer into the tasking
            Task { await requestPlans() }
        case .awaitingSelection:
            handleSelectionUtterance(text)
        case .awaitingArea:
            say("Tap \u{201C}Mark area\u{201D} above to draw the operating area on the map.")
        case .executing, .done:
            say("The mission is already underway — I'll keep posting updates here.")
        }
    }

    private func promptForArea() {
        phase = .awaitingArea
        say("Got it. Now mark the operating area and any no-fly zones on the map.")
        chat.append(.mapPrompt)
    }

    // MARK: - Map

    func addVertex(_ c: GeoCoordinate) { draftPolygon.append(c) }
    func completeDraftPolygon() {
        guard draftPolygon.count >= 3 else { return }
        switch drawMode {
        case .operatingArea: operatingArea = draftPolygon
        case .noFlyZone: noFlyZones.append(draftPolygon)
        }
        draftPolygon = []
    }
    var canSubmitArea: Bool { operatingArea.count >= 3 }

    var areaCentroid: GeoCoordinate? {
        guard !operatingArea.isEmpty else { return nil }
        let lat = operatingArea.map(\.lat).reduce(0,+) / Double(operatingArea.count)
        let lon = operatingArea.map(\.lon).reduce(0,+) / Double(operatingArea.count)
        return GeoCoordinate(lat: lat, lon: lon)
    }

    /// Called when the operator finishes with the map sheet.
    func finishArea() {
        showMap = false
        chat.append(.user("Marked the operating area"
            + (noFlyZones.isEmpty ? "" : " and \(noFlyZones.count) no-fly zone(s)") + "."))
        Task { await requestPlans() }
    }

    // MARK: - Planning

    func requestPlans() async {
        guard !missionText.isEmpty, operatingArea.count >= 3, !conversationId.isEmpty
        else { return }
        isBusy = true; defer { isBusy = false }
        say("Planning\u{2026}")
        let req = MissionPlanRequest(
            message: missionText, operatingArea: operatingArea, noFlyZones: noFlyZones,
            droneIds: droneIds, home: areaCentroid, cruiseMps: 25.0)
        do {
            let resp = try await api.requestMissionPlans(
                droneId: leadDroneId, conversationId: conversationId, request: req)
            if resp.status == "ask", let q = resp.question {
                phase = .awaitingClarification
                say(q)
            } else if resp.plans.count == 1, let only = resp.plans.first {
                // Only one sensible plan — don't make the operator "choose" from
                // a list of one; show it and launch it.
                phase = .awaitingSelection
                say("Here's the plan: \(only.label). Launching it.")
                chat.append(.plans(resp.plans, recommended: nil))  // one option — no "recommended" badge
                await select(only)
            } else if !resp.plans.isEmpty {
                phase = .awaitingSelection
                say("Here are \(resp.plans.count) options — tap one, or say \u{201C}go\u{201D} "
                    + "for the recommended plan.")
                chat.append(.plans(resp.plans, recommended: resp.recommendedPlanId))
            } else {
                say("I couldn't plan that — try rephrasing the mission.")
            }
        } catch {
            say("Planning failed: \(error.localizedDescription)")
        }
    }

    // MARK: - Selection ("go", a number, or a tap)

    private var lastPlans: [MissionPlanOption] {
        for item in chat.reversed() { if case .plans(let p, _) = item { return p } }
        return []
    }
    private var recommendedId: String? {
        for item in chat.reversed() { if case .plans(_, let r) = item { return r } }
        return nil
    }

    private func handleSelectionUtterance(_ text: String) {
        let low = text.lowercased()
        let plans = lastPlans
        var chosen: MissionPlanOption?
        if low.contains("go") || low.contains("recommend") || low.contains("first") {
            chosen = plans.first(where: { $0.planId == recommendedId }) ?? plans.first
        } else if let n = Int(low.filter(\.isNumber)), n >= 1, n <= plans.count {
            chosen = plans[n - 1]
        }
        if let c = chosen { Task { await select(c) } }
        else { say("Tap a plan card, or say \u{201C}go\u{201D} for the recommended one.") }
    }

    func select(_ plan: MissionPlanOption) async {
        guard phase == .awaitingSelection else { return }
        isBusy = true; defer { isBusy = false }
        subscribeToStatus()
        do {
            _ = try await api.selectMissionPlan(
                droneId: leadDroneId, conversationId: conversationId, planId: plan.planId)
            phase = .executing
            say("Launching \(plan.label) on \(plan.perDrone.count) aircraft. I'll post "
                + "status here as they fly.")
        } catch {
            say("Couldn't start the mission: \(error.localizedDescription)")
        }
    }

    // MARK: - Live status via MQTT

    private func subscribeToStatus() {
        guard subscribedTopics.isEmpty else { return }
        for did in droneIds {
            let progress = "drone/\(did)/chat/\(conversationId)/progress"
            let response = "drone/\(did)/chat/\(conversationId)/response"
            mqtt.subscribe(to: progress) { [weak self] d in
                Task { @MainActor in self?.onProgress(d, drone: did) } }
            mqtt.subscribe(to: response) { [weak self] d in
                Task { @MainActor in self?.onResponse(d, drone: did) } }
            subscribedTopics += [progress, response]
        }
    }

    private func upsertProgress(drone: String, _ p: MessageContent.MissionProgress) {
        if let i = chat.firstIndex(where: {
            if case .progress(let d, _) = $0 { return d == drone }; return false }) {
            chat[i] = .progress(drone: drone, p)
        } else {
            chat.append(.progress(drone: drone, p))
        }
    }

    private func onProgress(_ data: Data, drone: String) {
        guard let payload = try? JSONDecoder().decode(DroneMessagePayload.self, from: data),
              let msg = payload.toChatMessage() else { return }
        if case .missionProgress(let p) = msg.content { upsertProgress(drone: drone, p) }
        else if case .text(let t) = msg.content { say("\(drone): \(t)") }
    }

    private func onResponse(_ data: Data, drone: String) {
        guard let payload = try? JSONDecoder().decode(MissionResponsePayload.self, from: data)
        else { return }
        let r = payload.result
        if let t = r.targetLocation {
            let fix = TargetFix(label: t.label ?? "target", lat: t.lat, lon: t.lon,
                                eastM: t.eastM, northM: t.northM)
            targetLocation = fix
            let url = payload.imageUrls?.first.flatMap { URL(string: $0) }
            if !chat.contains(where: { if case .target = $0 { return true }; return false }) {
                chat.append(.target(fix, imageURL: url))
            }
        }
        if let s = r.summary, !s.isEmpty { chat.append(.summary(s)) }
        phase = .done
    }

    // MARK: - New mission / teardown

    func reset() {
        teardown()
        phase = .awaitingMission
        chat = []; missionText = ""; input = ""
        operatingArea = []; noFlyZones = []; draftPolygon = []
        targetLocation = nil
        Task { await begin() }
    }

    func teardown() {
        for t in subscribedTopics { mqtt.unsubscribe(from: t) }
        subscribedTopics = []
    }

    private func say(_ text: String) { chat.append(.drone(text)) }
}

/// Minimal decode of the drone's /response payload — target + summary only.
struct MissionResponsePayload: Decodable {
    let droneId: String?
    let conversationId: String?
    let missionId: String?
    let imageUrls: [String]?
    let result: ResultBlock

    enum CodingKeys: String, CodingKey {
        case droneId, result
        case conversationId = "conversation_id"
        case missionId = "mission_id"
        case imageUrls = "image_urls"
    }
    struct ResultBlock: Decodable {
        let success: Bool?
        let summary: String?
        let targetLocation: TargetLocation?
        enum CodingKeys: String, CodingKey {
            case success, summary
            case targetLocation = "target_location"
        }
    }
    struct TargetLocation: Decodable {
        let label: String?; let lat: Double?; let lon: Double?
        let eastM: Double?; let northM: Double?
        enum CodingKeys: String, CodingKey {
            case label, lat, lon
            case eastM = "east_m"; case northM = "north_m"
        }
    }
}
