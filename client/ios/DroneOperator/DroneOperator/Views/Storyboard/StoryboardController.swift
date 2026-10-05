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
        case drone(String)                                   // system text, no particular drone
        case droneSays(drone: String, text: String)          // a specific drone's line — tappable
        case mapPrompt                                       // "mark the area" button
        case plans([MissionPlanOption], recommended: String?)
        case progress(drone: String, MessageContent.MissionProgress)
        case target(TargetFix, imageURL: URL?)
        case summary(String)
        var id: String {
            switch self {
            case .user(let t): return "u-\(t.hashValue)-\(abs(t.count))"
            case .drone(let t): return "d-\(t.hashValue)"
            case .droneSays(let d, let t): return "ds-\(d)-\(t.hashValue)"
            case .mapPrompt: return "map"
            case .plans(let p, _): return "plans-" + p.map(\.planId).joined()
            case .progress(let d, _): return "prog-\(d)"       // one live row per drone
            case .target(let t, _): return "target-\(t.label)"
            case .summary(let s): return "sum-\(s.hashValue)"
            }
        }
    }

    /// Chat | Map — the top-level screen switch (task 3/4). Both screens share
    /// the same drone chip bar and the same right sidebar selection.
    enum MissionScreen { case chat, map }
    var screen: MissionScreen = .chat

    /// What the right sidebar (task 3/4) is showing — a drone's live video +
    /// telemetry, or a sighted object's photo + location. Nothing selected
    /// means no sidebar at all (the PiP-replacement default).
    enum SidebarSelection: Hashable { case drone(String); case object(String) }
    var selection: SidebarSelection?

    /// One physical thing the fleet has reported seeing this session — the
    /// target AND any non-target landmarks (e.g. the decoy car), merged in
    /// from every mission response's `landmarks` (see
    /// control/conversations.py's landmarks_payload / _build_mission_record).
    /// Keyed by label: repeat sightings across missions update the same entry
    /// in place rather than appending duplicates, so the map/sidebar always
    /// shows the latest fix.
    struct SightedObject: Identifiable, Hashable {
        var id: String { label }
        let label: String
        var lat: Double?
        var lon: Double?
        var eastM: Double?
        var northM: Double?
        var score: Double?
        var hits: Int?
        var imageURL: URL?
    }
    var objects: [SightedObject] = []

    func select(drone id: String) { selection = .drone(id) }
    func select(object label: String) { selection = .object(label) }
    func clearSelection() { selection = nil }

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
    /// Drones that have posted a final /response for the current mission; the
    /// mission is only "done" once this covers every tasked aircraft.
    private var respondedDrones: Set<String> = []

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
    /// drone/{id}/status subscriptions — separate from subscribedTopics
    /// (progress/response) because they're set up once the fleet is known in
    /// begin(), not gated behind selecting a mission plan like
    /// subscribeToStatus()'s guard assumes. Feeds MQTTService.droneStatuses,
    /// the shared cache the map/sidebar telemetry reads from.
    private var telemetryTopics: [String] = []

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
            subscribeToTelemetry()
        } catch {
            say("I couldn't reach the ground control station: \(error.localizedDescription)")
        }
    }

    /// Live drone/{id}/status for every known drone, independent of mission
    /// state — the map/sidebar want a drone's position and battery whether or
    /// not a mission is currently flying. Same decode + shared-cache pattern
    /// as DroneListView.subscribeToStatusTopics; feeding the same
    /// MQTTService.droneStatuses cache means DroneDetailView and this screen
    /// never disagree about a drone's last-known state.
    private func subscribeToTelemetry() {
        guard telemetryTopics.isEmpty else { return }
        for did in droneIds {
            let topic = "drone/\(did)/status"
            mqtt.subscribe(to: topic) { [weak self] data in
                guard let status = try? JSONDecoder().decode(DroneStatus.self, from: data)
                else { return }
                Task { @MainActor in
                    self?.mqtt.updateDroneStatus(droneId: did, status: status)
                }
            }
            telemetryTopics.append(topic)
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
            // A follow-up query ("did you also see a car?", "fly back to the
            // truck and take more angle shots") once a mission has launched
            // or finished — see sendFollowUp.
            Task { await sendFollowUp(text) }
        }
    }

    /// Task 2: operator follow-ups after a mission has launched or completed.
    /// Routes through the same /messages endpoint every other chat turn uses
    /// — control/conversations.py's message_handler now injects the
    /// conversation's recent mission records into the model's context, so a
    /// sighting question gets answered from real data ("respond"), and a
    /// fly-back request comes back as a fresh action:"mission" dispatched to
    /// the same command topic the original plan used. Either way the
    /// existing progress/response MQTT subscriptions on this conversation
    /// just keep working — no new topics needed.
    private func sendFollowUp(_ text: String) async {
        guard !conversationId.isEmpty else { return }
        isBusy = true; defer { isBusy = false }
        do {
            let resp = try await api.sendChatMessage(
                droneId: leadDroneId, conversationId: conversationId, message: text)
            guard let immediate = resp.immediateResponse else { return }
            switch immediate.content {
            case .text(let t), .error(let t):
                say(t)
            case .loading(let t):
                // message_handler's 'mission' branch saves a 'loading'
                // message when it dispatches a fresh mission — reset
                // per-mission state so "mission complete" fires again once
                // every drone reports in, exactly like the first launch.
                respondedDrones = []
                phase = .executing
                say(t)
            default:
                break
            }
        } catch {
            say("Couldn't reach the ground control station: \(error.localizedDescription)")
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
        respondedDrones = []
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
        else if case .text(let t) = msg.content { sayFromDrone(drone, t) }
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
        // Everything the fleet has actually seen (target AND non-target
        // landmarks, e.g. the decoy car) — what backs the map screen and the
        // "did you also see a car?" follow-up query. See
        // control/conversations.py's landmarks_payload for the source shape.
        mergeLandmarks(r.landmarks)
        // Each aircraft reports as it finishes; label it by drone so the first
        // one home doesn't read as the whole mission completing.
        if let s = r.summary, !s.isEmpty { sayFromDrone(drone, s) }
        respondedDrones.insert(drone)

        // Only declare the mission complete once EVERY tasked aircraft has
        // reported in (and, per the daemon, returned to base). Otherwise the
        // first drone to finish ended the mission while the others were still
        // flying — which is what looked like a premature "mission completed".
        let expected = Set(droneIds)
        guard !expected.isEmpty, respondedDrones.isSuperset(of: expected) else { return }
        let n = expected.count
        chat.append(.summary(n > 1
            ? "All \(n) aircraft have finished and returned to base."
            : "Mission complete — the aircraft has returned to base."))
        phase = .done
    }

    // MARK: - New mission / teardown

    func reset() {
        teardown()
        phase = .awaitingMission
        chat = []; missionText = ""; input = ""
        operatingArea = []; noFlyZones = []; draftPolygon = []
        targetLocation = nil
        objects = []; selection = nil; screen = .chat
        Task { await begin() }
    }

    func teardown() {
        for t in subscribedTopics { mqtt.unsubscribe(from: t) }
        subscribedTopics = []
        for t in telemetryTopics { mqtt.unsubscribe(from: t) }
        telemetryTopics = []
    }

    private func say(_ text: String) { chat.append(.drone(text)) }
    private func sayFromDrone(_ drone: String, _ text: String) {
        chat.append(.droneSays(drone: drone, text: text))
    }

    /// Upsert this response's landmarks into `objects`, keyed by label — a
    /// second mission that re-sights "car" updates that entry's fix rather
    /// than appending a duplicate pin.
    private func mergeLandmarks(_ landmarks: [LandmarkPayload]?) {
        guard let landmarks, !landmarks.isEmpty else { return }
        for lm in landmarks {
            guard let label = lm.label else { continue }
            let obj = SightedObject(label: label, lat: lm.lat, lon: lm.lon,
                                    eastM: lm.eastM, northM: lm.northM,
                                    score: lm.score, hits: lm.hits,
                                    imageURL: lm.imageUrl.flatMap(URL.init(string:)))
            if let i = objects.firstIndex(where: { $0.label == label }) {
                objects[i] = obj
            } else {
                objects.append(obj)
            }
        }
    }
}

/// Decode of the drone's /response payload — target + summary + everything
/// else the mission sighted (task 2/3's landmarks, for follow-up queries and
/// the map screen).
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
        // All-optional and defaulted to [] on missing key: an older daemon
        // that hasn't been rebuilt with the landmarks field yet must decode
        // fine — this is additive, not a wire-format break.
        let landmarks: [LandmarkPayload]?
        enum CodingKeys: String, CodingKey {
            case success, summary, landmarks
            case targetLocation = "target_location"
        }
        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            success = try c.decodeIfPresent(Bool.self, forKey: .success)
            summary = try c.decodeIfPresent(String.self, forKey: .summary)
            targetLocation = try c.decodeIfPresent(TargetLocation.self, forKey: .targetLocation)
            landmarks = try c.decodeIfPresent([LandmarkPayload].self, forKey: .landmarks) ?? []
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

/// One object the fleet has sighted, from control/conversations.py's
/// landmarks_payload — the target AND any non-target objects (e.g. a decoy
/// car). Every field but `label` is optional: a hardware daemon that hasn't
/// picked up a lat/lon datum, or a landmark that never got a first-sighting
/// photo, still decodes cleanly.
struct LandmarkPayload: Decodable {
    let label: String?
    let eastM: Double?
    let northM: Double?
    let altM: Double?
    let score: Double?
    let hits: Int?
    let lat: Double?
    let lon: Double?
    let imageUrl: String?

    enum CodingKeys: String, CodingKey {
        case label, score, hits, lat, lon
        case eastM = "east_m"; case northM = "north_m"; case altM = "alt_m"
        case imageUrl = "image_url"
    }
}
