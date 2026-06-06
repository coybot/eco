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
}
