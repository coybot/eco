import Foundation
import Vision

struct VisionObservation: Codable, Equatable {
    let label: String
    let confidence: Float
    let boundingBox: CGRect
    let attributes: [String: String]
}

struct VisionContext: Codable, Equatable {
    let observations: [VisionObservation]
}

final class VisionService {
    func analyze(image: CGImage) async throws -> VisionContext {
        // Placeholder for on-device VLM/vision pipeline.
        // The vision model should output labels, bounding boxes, and attributes only.
        return VisionContext(observations: [])
    }
}
