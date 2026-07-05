import Foundation
import ARKit
import RoverNav

/// Persists an `ARWorldMap` plus human-named destinations ("gate 14", "jet bridge",
/// "cart yard") so the ramp only has to be mapped once. On next launch the app
/// relocalizes into the saved map and can navigate to any named anchor — this is what
/// lets Phase-2 voice commands ("take this to gate 14") resolve to a goal point.
enum WorldMapStore {
    private static var dir: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        let d = base.appendingPathComponent("RoverMaps", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }
    private static var mapURL: URL { dir.appendingPathComponent("world.map") }
    private static var placesURL: URL { dir.appendingPathComponent("places.json") }

    // MARK: - World map

    static func save(_ session: ARSession) async throws {
        // Archive to Data *inside* the completion handler: ARWorldMap isn't Sendable,
        // so it can't safely cross the continuation boundary — Data can.
        let data = try await withCheckedThrowingContinuation { (c: CheckedContinuation<Data, Error>) in
            session.getCurrentWorldMap { map, err in
                guard let map else { c.resume(throwing: err ?? StoreError.noMap); return }
                do {
                    let data = try NSKeyedArchiver.archivedData(withRootObject: map, requiringSecureCoding: true)
                    c.resume(returning: data)
                } catch {
                    c.resume(throwing: error)
                }
            }
        }
        try data.write(to: mapURL, options: .atomic)
    }

    static func loadWorldMap() -> ARWorldMap? {
        guard let data = try? Data(contentsOf: mapURL) else { return nil }
        return try? NSKeyedUnarchiver.unarchivedObject(ofClass: ARWorldMap.self, from: data)
    }

    // MARK: - Named places (nav-plane coordinates within the saved map)

    static func places() -> [String: Vec2] {
        guard let data = try? Data(contentsOf: placesURL),
              let raw = try? JSONDecoder().decode([String: [Double]].self, from: data) else { return [:] }
        return raw.compactMapValues { $0.count == 2 ? Vec2($0[0], $0[1]) : nil }
    }

    static func setPlace(_ name: String, at p: Vec2) throws {
        var current = places()
        current[name] = p
        let raw = current.mapValues { [$0.x, $0.y] }
        try JSONEncoder().encode(raw).write(to: placesURL, options: .atomic)
    }

    enum StoreError: Error { case noMap }
}
