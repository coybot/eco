import Foundation
import Vision
import CoreVideo

/// On-device object detector (person + generic obstacles) via a CoreML model wrapped
/// in Vision. Ships with a YOLO-family model converted to `.mlpackage` (see
/// `eco/rover/models/`); swap the model name to use a custom-trained one.
///
/// Detections feed two consumers: the ObstacleGuard (people directly ahead) and the
/// Phase-2 dialog/"who's there" behaviors. This is the on-device half of the hybrid
/// AI split — heavy vision-language reasoning is offloaded to the cloud.
final class Detector {
    struct Detection {
        let label: String
        let confidence: Float
        let boundingBox: CGRect // normalized, Vision coords
    }

    private var request: VNCoreMLRequest?

    init(modelName: String = "RoverYOLO") {
        guard let url = Bundle.main.url(forResource: modelName, withExtension: "mlmodelc"),
              let model = try? VNCoreMLModel(for: MLModel(contentsOf: url)) else {
            request = nil
            return
        }
        let req = VNCoreMLRequest(model: model)
        req.imageCropAndScaleOption = .scaleFill
        request = req
    }

    func detect(_ pixelBuffer: CVPixelBuffer) -> [Detection] {
        guard let request else { return [] }
        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, orientation: .right)
        try? handler.perform([request])
        guard let results = request.results as? [VNRecognizedObjectObservation] else { return [] }
        return results.map {
            Detection(label: $0.labels.first?.identifier ?? "object",
                      confidence: $0.labels.first?.confidence ?? 0,
                      boundingBox: $0.boundingBox)
        }
    }
}
