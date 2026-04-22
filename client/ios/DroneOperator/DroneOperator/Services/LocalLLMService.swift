import Foundation
import CoreML

final class LocalLLMService {
    struct ModelConfig {
        let bundledModelName: String
        let bundledSubdirectory: String?
        let bundledExtension: String
        let promptInputKey: String?
        let textOutputKey: String?
    }

    enum LLMError: LocalizedError {
        case modelNotLoaded
        case modelLoadFailed(String)
        case incompatibleModel(String)
        case inferenceFailed(String)
        case invalidJSON(String)
        
        var errorDescription: String? {
            switch self {
            case .modelNotLoaded:
                return "Local model not loaded."
            case .modelLoadFailed(let reason):
                return "Failed to load local model: \(reason)"
            case .incompatibleModel(let reason):
                return "Incompatible model: \(reason)"
            case .inferenceFailed(let reason):
                return "Inference failed: \(reason)"
            case .invalidJSON(let reason):
                return "Invalid mission JSON: \(reason)"
            }
        }
    }
    
    private var model: MLModel?
    private var config = ModelConfig(
        bundledModelName: "llama_3.1_coreml",
        bundledSubdirectory: "Models",
        bundledExtension: "mlmodelc",
        promptInputKey: nil,
        textOutputKey: nil
    )
    
    func loadBundledModel() throws {
        let url = Bundle.main.url(
            forResource: config.bundledModelName,
            withExtension: config.bundledExtension,
            subdirectory: config.bundledSubdirectory
        ) ?? Bundle.main.url(forResource: config.bundledModelName, withExtension: "mlmodelc")
        guard let url else {
            throw LLMError.modelNotLoaded
        }
        do {
            self.model = try MLModel(contentsOf: url)
        } catch {
            throw LLMError.modelLoadFailed(error.localizedDescription)
        }
    }
    
    /// Generate a mission from a user prompt and optional vision context.
    /// This does not execute or control the drone directly.
    func generateMission(from prompt: String, vision: VisionContext?) async throws -> Mission {
        guard let model else {
            throw LLMError.modelNotLoaded
        }

        let composedPrompt = buildPrompt(prompt: prompt, vision: vision)
        let (inputKey, outputKey) = try resolveIOKeys(for: model)

        let features = try MLDictionaryFeatureProvider(dictionary: [
            inputKey: MLFeatureValue(string: composedPrompt)
        ])

        let output = try await model.prediction(from: features)
        guard let result = output.featureValue(for: outputKey)?.stringValue else {
            throw LLMError.incompatibleModel("Output key '\(outputKey)' not a string")
        }

        return try parseMissionJSON(result)
    }
    
    /// Parse mission JSON produced by the model and enforce strict schema.
    func parseMissionJSON(_ json: String) throws -> Mission {
        guard let data = json.data(using: .utf8) else {
            throw LLMError.invalidJSON("UTF-8 decoding failed")
        }
        let object = try JSONSerialization.jsonObject(with: data, options: [])
        guard let dict = object as? [String: Any] else {
            throw LLMError.invalidJSON("Root must be an object")
        }
        let keys = Set(dict.keys)
        let required: Set<String> = ["goal", "target", "constraints", "failsafes"]
        guard keys == required else {
            throw LLMError.invalidJSON("Expected keys \(required.sorted())")
        }
        let mission = try JSONDecoder().decode(Mission.self, from: data)
        guard mission.isValid else {
            throw LLMError.invalidJSON("Goal is empty")
        }
        return mission
    }

    private func buildPrompt(prompt: String, vision: VisionContext?) -> String {
        var payload = "You must return strict JSON only.\n"
        payload += "Schema: {\"goal\":\"...\",\"target\":{...},\"constraints\":{...},\"failsafes\":{...}}\n"
        payload += "User:\n\(prompt)\n"
        if let vision {
            payload += "Vision:\n"
            payload += "\(vision.observations)\n"
        }
        return payload
    }

    private func resolveIOKeys(for model: MLModel) throws -> (String, String) {
        if let configuredInput = config.promptInputKey, let configuredOutput = config.textOutputKey {
            return (configuredInput, configuredOutput)
        }

        let description = model.modelDescription
        let inputCandidates = description.inputDescriptionsByName
            .filter { $0.value.type == .string }
            .map { $0.key }
        let outputCandidates = description.outputDescriptionsByName
            .filter { $0.value.type == .string }
            .map { $0.key }

        guard let inputKey = inputCandidates.first else {
            throw LLMError.incompatibleModel("No string input found")
        }
        guard let outputKey = outputCandidates.first else {
            throw LLMError.incompatibleModel("No string output found")
        }
        return (inputKey, outputKey)
    }
}
