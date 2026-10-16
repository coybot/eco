import Foundation

struct DroneGroup: Identifiable, Codable, Hashable {
    let userId: String
    let groupId: String
    var name: String
    var members: [String]
    let createdAt: String

    var id: String { groupId }
}

struct GroupsResponse: Decodable {
    let groups: [DroneGroup]
}

struct GroupCreateResponse: Decodable {
    let group: DroneGroup
}

struct GroupMessageResponse: Decodable {
    let groupId: String
    let orders: [String: String]
    let status: String?
    let pendingId: String?
    let prompt: String?
    let steps: [String]?
    /// The cancelled / expired note, when the answer to a held order was not "go".
    let immediateResponse: ChatMessage?

    enum CodingKeys: String, CodingKey {
        case groupId, orders, status, prompt, steps
        case pendingId = "pending_id"
        case immediateResponse = "immediate_response"
    }

    /// Orders the backend is holding until the operator says OK.
    var pendingFlight: PendingFlight? {
        guard status == "needs_confirmation", let pendingId else { return nil }
        return PendingFlight(id: pendingId, prompt: prompt ?? "Send these orders to the fleet?",
                             steps: steps ?? [])
    }
}
