import Foundation

/// Represents a message in a drone conversation
struct ChatMessage: Identifiable, Codable, Equatable {
    let id: String
    let conversationId: String
    let sender: MessageSender
    let content: MessageContent
    let timestamp: Date
    
    init(
        id: String = UUID().uuidString,
        conversationId: String,
        sender: MessageSender,
        content: MessageContent,
        timestamp: Date = Date()
    ) {
        self.id = id
        self.conversationId = conversationId
        self.sender = sender
        self.content = content
        self.timestamp = timestamp
    }
}

/// Who sent the message
enum MessageSender: String, Codable {
    case user
    case drone
}

/// Types of message content
enum MessageContent: Codable, Equatable {
    case text(String)
    case image(URL)
    case imageChoice([ImageOption])  // Multiple images for user selection
    case loading(String)             // Drone is processing, with status text
    case error(String)               // Error message
    case missionProgress(MissionProgress)  // Mission execution progress
    
    /// Image option for selection
    struct ImageOption: Codable, Equatable, Identifiable {
        let id: Int
        let url: URL
        let description: String?
    }
    
    /// Mission progress information
    struct MissionProgress: Codable, Equatable {
        let missionId: String
        let phase: Int
        let totalPhases: Int
        let objective: String
        let status: String  // "in_progress", "completed", "failed"
        let message: String?
        
        var progressText: String {
            "Phase \(phase)/\(totalPhases): \(objective)"
        }
        
        var percentComplete: Double {
            guard totalPhases > 0 else { return 0 }
            return Double(phase - 1) / Double(totalPhases)
        }
    }
    
    // Custom coding for the enum
    enum CodingKeys: String, CodingKey {
        case type
        case text
        case url
        case options
        case missionProgress = "mission_progress"
    }
    
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let type = try container.decode(String.self, forKey: .type)
        
        switch type {
        case "text":
            let text = try container.decode(String.self, forKey: .text)
            self = .text(text)
        case "image":
            let url = try container.decode(URL.self, forKey: .url)
            self = .image(url)
        case "image_choice":
            let options = try container.decode([ImageOption].self, forKey: .options)
            self = .imageChoice(options)
        case "loading":
            let text = try container.decode(String.self, forKey: .text)
            self = .loading(text)
        case "error":
            let text = try container.decode(String.self, forKey: .text)
            self = .error(text)
        case "mission_progress":
            let progress = try container.decode(MissionProgress.self, forKey: .missionProgress)
            self = .missionProgress(progress)
        default:
            throw DecodingError.dataCorruptedError(forKey: .type, in: container, debugDescription: "Unknown message type: \(type)")
        }
    }
    
    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        
        switch self {
        case .text(let text):
            try container.encode("text", forKey: .type)
            try container.encode(text, forKey: .text)
        case .image(let url):
            try container.encode("image", forKey: .type)
            try container.encode(url, forKey: .url)
        case .imageChoice(let options):
            try container.encode("image_choice", forKey: .type)
            try container.encode(options, forKey: .options)
        case .loading(let text):
            try container.encode("loading", forKey: .type)
            try container.encode(text, forKey: .text)
        case .error(let text):
            try container.encode("error", forKey: .type)
            try container.encode(text, forKey: .text)
        case .missionProgress(let progress):
            try container.encode("mission_progress", forKey: .type)
            try container.encode(progress, forKey: .missionProgress)
        }
    }
}

/// A conversation with a drone
struct Conversation: Identifiable, Codable {
    let id: String
    let droneId: String
    let createdAt: Date
    var messages: [ChatMessage]
    
    init(droneId: String, id: String = UUID().uuidString) {
        self.id = id
        self.droneId = droneId
        self.createdAt = Date()
        self.messages = []
    }
}

/// MQTT message payload from drone (formatted message)
struct DroneMessagePayload: Codable {
    let droneId: String
    let conversationId: String?
    let messageType: String
    let text: String?
    let imageUrls: [String]?
    let imageOptions: [MessageContent.ImageOption]?
    let timestamp: String?
    
    // Mission progress fields
    let missionId: String?
    let phase: Int?
    let totalPhases: Int?
    let objective: String?
    let status: String?
    
    enum CodingKeys: String, CodingKey {
        case droneId
        case conversationId = "conversation_id"
        case messageType = "message_type"
        case text
        case imageUrls = "image_urls"
        case imageOptions = "image_options"
        case timestamp
        case missionId = "mission_id"
        case phase
        case totalPhases = "total_phases"
        case objective
        case status
    }
    
    /// Convert to ChatMessage
    func toChatMessage() -> ChatMessage? {
        let content: MessageContent
        
        switch messageType {
        case "text":
            guard let text = text else { return nil }
            content = .text(text)
        case "image":
            guard let urls = imageUrls, let first = urls.first, let url = URL(string: first) else { return nil }
            content = .image(url)
        case "image_choice":
            if let options = imageOptions {
                content = .imageChoice(options)
            } else if let urls = imageUrls {
                let options = urls.enumerated().map { index, urlString in
                    MessageContent.ImageOption(
                        id: index,
                        url: URL(string: urlString)!,
                        description: nil
                    )
                }
                content = .imageChoice(options)
            } else {
                return nil
            }
        case "loading":
            content = .loading(text ?? "Processing...")
        case "error":
            content = .error(text ?? "Unknown error")
        case "mission_progress":
            // Parse mission progress from fields
            guard let missionId = missionId,
                  let phase = phase,
                  let totalPhases = totalPhases,
                  let objective = objective else {
                return nil
            }
            let progress = MessageContent.MissionProgress(
                missionId: missionId,
                phase: phase,
                totalPhases: totalPhases,
                objective: objective,
                status: status ?? "in_progress",
                message: text
            )
            content = .missionProgress(progress)
        default:
            guard let text = text else { return nil }
            content = .text(text)
        }
        
        return ChatMessage(
            id: UUID().uuidString,
            conversationId: conversationId ?? "",
            sender: .drone,
            content: content,
            timestamp: timestamp.flatMap { ISO8601DateFormatter().date(from: $0) } ?? Date()
        )
    }
}

/// MQTT execution response from drone (raw execution result)
struct DroneExecutionResponse: Codable {
    let droneId: String
    let conversationId: String?
    let originalMessage: String?
    let result: ExecutionResult
    let followUp: Bool?
    let imageUrls: [String]?
    let timestamp: String?
    let missionId: String?  // For mission responses
    
    struct ExecutionResult: Codable {
        let success: Bool
        let stdout: String?
        let stderr: String?
        let returncode: Int?
        let error: String?
        let imageUrls: [String]?
        
        // Mission-specific fields
        let summary: String?
        let findings: [String: String]?
        let phasesCompleted: Int?
        let totalPhases: Int?
        let failureReason: String?
        let durationSeconds: Double?
        let actionsTaken: Int?
        
        enum CodingKeys: String, CodingKey {
            case success, stdout, stderr, returncode, error
            case imageUrls = "image_urls"
            case summary, findings
            case phasesCompleted = "phases_completed"
            case totalPhases = "total_phases"
            case failureReason = "failure_reason"
            case durationSeconds = "duration_seconds"
            case actionsTaken = "actions_taken"
        }
    }
    
    enum CodingKeys: String, CodingKey {
        case droneId
        case conversationId = "conversation_id"
        case originalMessage = "original_message"
        case result
        case followUp = "follow_up"
        case imageUrls = "image_urls"
        case timestamp
        case missionId = "mission_id"
    }
    
    /// Convert to ChatMessage
    func toChatMessage() -> ChatMessage? {
        let content: MessageContent
        
        // Check for images first (from result or top-level)
        let allImageUrls = imageUrls ?? result.imageUrls ?? []
        if !allImageUrls.isEmpty {
            if allImageUrls.count == 1, let url = URL(string: allImageUrls[0]) {
                content = .image(url)
            } else {
                let options = allImageUrls.enumerated().compactMap { index, urlString -> MessageContent.ImageOption? in
                    guard let url = URL(string: urlString) else { return nil }
                    return MessageContent.ImageOption(id: index, url: url, description: nil)
                }
                if options.isEmpty {
                    return nil
                }
                content = .imageChoice(options)
            }
        } else if !result.success {
            // Error case
            let errorText = result.failureReason ?? result.error ?? result.stderr ?? "Command failed"
            content = .error(errorText)
        } else if let summary = result.summary, !summary.isEmpty {
            // Mission completed with summary
            var text = summary
            if let phases = result.phasesCompleted, let total = result.totalPhases {
                text += "\n\n✅ Completed \(phases)/\(total) phases"
            }
            if let duration = result.durationSeconds {
                text += " in \(Int(duration))s"
            }
            content = .text(text)
        } else if let stdout = result.stdout, !stdout.isEmpty {
            // Success with output
            content = .text(stdout)
        } else {
            // Success with no output
            content = .text("Done!")
        }
        
        return ChatMessage(
            id: UUID().uuidString,
            conversationId: conversationId ?? "",
            sender: .drone,
            content: content,
            timestamp: timestamp.flatMap { ISO8601DateFormatter().date(from: $0) } ?? Date()
        )
    }
}

