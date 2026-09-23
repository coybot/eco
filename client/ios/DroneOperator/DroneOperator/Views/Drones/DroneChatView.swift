import SwiftUI

/// Chat interface for conversing with a drone
struct DroneChatView: View {
    let drone: Drone
    let conversationId: String
    
    @Environment(APIClient.self) private var apiClient
    @Environment(MQTTService.self) private var mqttService
    @Environment(\.dismiss) private var dismiss
    
    @State private var messages: [ChatMessage] = []
    @State private var inputText = ""
    @State private var isLoading = false
    @State private var error: Error?
    @State private var scrollProxy: ScrollViewProxy?
    
    var body: some View {
        VStack(spacing: 0) {
            // Messages list
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 12) {
                        ForEach(messages) { message in
                            ChatBubble(message: message) { selectedOption in
                                handleImageSelection(option: selectedOption)
                            }
                            .id(message.id)
                        }
                        
                        // Loading indicator when drone is processing
                        if isLoading {
                            DroneTypingIndicator()
                                .id("typing")
                        }
                    }
                    .padding()
                }
                .onAppear {
                    scrollProxy = proxy
                }
                .onChange(of: messages.count) { _, _ in
                    scrollToBottom()
                }
            }
            
            Divider()
                .background(Color.white.opacity(0.1))
            
            // Input area
            inputArea
        }
        .background(Color(red: 0.05, green: 0.05, blue: 0.1))
        .navigationTitle("Chat")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Color(red: 0.05, green: 0.05, blue: 0.1), for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .alert("Error", isPresented: .constant(error != nil)) {
            Button("OK") { error = nil }
        } message: {
            if let error {
                Text(error.localizedDescription)
            }
        }
        .task {
            await setupMQTTSubscription()
            await loadConversationHistory()
        }
    }
    
    // MARK: - Input Area
    
    private var inputArea: some View {
        HStack(spacing: 12) {
            TextField("Message your drone...", text: $inputText, axis: .vertical)
                .textFieldStyle(.plain)
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
                .background(Color.white.opacity(0.1))
                .foregroundColor(.white)
                .clipShape(RoundedRectangle(cornerRadius: 20))
                .lineLimit(1...5)
            
            Button {
                sendMessage()
            } label: {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 36))
                    .foregroundStyle(inputText.isEmpty ? .gray : .cyan)
            }
            .disabled(inputText.isEmpty || isLoading)
        }
        .padding(.horizontal)
        .padding(.vertical, 12)
        .background(Color(red: 0.08, green: 0.08, blue: 0.12))
    }
    
    // MARK: - Actions
    
    private func setupMQTTSubscription() async {
        // Subscribe to drone responses for this conversation
        let responseTopic = "drone/\(drone.droneId)/chat/\(conversationId)/response"
        AppLogger.chat.info("🔔 Subscribing to MQTT topic: \(responseTopic)")
        mqttService.subscribe(to: responseTopic) { payload in
            handleDroneMessage(payload)
        }
        
        // Subscribe to mission progress updates for this conversation
        let progressTopic = "drone/\(drone.droneId)/chat/\(conversationId)/progress"
        AppLogger.chat.info("🔔 Subscribing to MQTT topic: \(progressTopic)")
        mqttService.subscribe(to: progressTopic) { payload in
            handleDroneMessage(payload)
        }
        
        // Also subscribe to drone logs for this conversation (for real-time output)
        let logsTopic = "drone/\(drone.droneId)/logs"
        mqttService.subscribe(to: logsTopic) { payload in
            handleDroneLogs(payload)
        }
        
        // Also subscribe to general drone status
        let statusTopic = "drone/\(drone.droneId)/status"
        mqttService.subscribe(to: statusTopic) { payload in
            handleDroneStatus(payload)
        }
    }
    
    private func loadConversationHistory() async {
        do {
            let history = try await apiClient.getConversationHistory(
                droneId: drone.droneId,
                conversationId: conversationId
            )
            await MainActor.run {
                self.messages = history
                scrollToBottom()
            }
        } catch {
            // New conversation, no history
            AppLogger.api.debug("No conversation history: \(error.localizedDescription)")
        }
    }
    
    private func sendMessage() {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        
        inputText = ""
        
        // Add user message immediately
        let userMessage = ChatMessage(
            conversationId: conversationId,
            sender: .user,
            content: .text(text)
        )
        messages.append(userMessage)
        scrollToBottom()
        
        isLoading = true
        
        Task {
            do {
                // Send to API - it will relay to drone via MQTT
                let response = try await apiClient.sendChatMessage(
                    droneId: drone.droneId,
                    conversationId: conversationId,
                    message: text
                )
                
                // If we get an immediate response (like a clarifying question), show it
                if let immediateResponse = response.immediateResponse {
                    await MainActor.run {
                        messages.append(immediateResponse)
                        isLoading = false
                    }
                }
                // Otherwise wait for MQTT message from drone
                
            } catch {
                await MainActor.run {
                    self.error = error
                    isLoading = false
                    
                    // Add error message to chat
                    let errorMessage = ChatMessage(
                        conversationId: conversationId,
                        sender: .drone,
                        content: .error("Failed to send: \(error.localizedDescription)")
                    )
                    messages.append(errorMessage)
                }
            }
        }
    }
    
    private func handleImageSelection(option: MessageContent.ImageOption) {
        // Add user selection as a message
        let selectionMessage = ChatMessage(
            conversationId: conversationId,
            sender: .user,
            content: .text("Option \(option.id + 1)")
        )
        messages.append(selectionMessage)
        
        isLoading = true
        
        Task {
            do {
                let response = try await apiClient.sendImageSelection(
                    droneId: drone.droneId,
                    conversationId: conversationId,
                    optionId: option.id
                )
                
                if let immediateResponse = response.immediateResponse {
                    await MainActor.run {
                        messages.append(immediateResponse)
                        isLoading = false
                    }
                }
            } catch {
                await MainActor.run {
                    self.error = error
                    isLoading = false
                }
            }
        }
    }
    
    private func handleDroneMessage(_ payload: Data) {
        // Log raw payload for debugging (also print to console)
        if let jsonString = String(data: payload, encoding: .utf8) {
            AppLogger.chat.debug("📨 Drone response: \(jsonString)")
            print("📨 Drone response: \(jsonString)")
        }
        
        // Try to decode as DroneMessagePayload first (formatted message including progress)
        if let messagePayload = try? JSONDecoder().decode(DroneMessagePayload.self, from: payload),
           messagePayload.conversationId == conversationId,
           let message = messagePayload.toChatMessage() {
            
            Task { @MainActor in
                // For mission progress, update existing progress message instead of appending
                if case .missionProgress = message.content {
                    // Find existing progress message for this mission and update it
                    if let index = messages.lastIndex(where: { msg in
                        if case .missionProgress = msg.content {
                            return true
                        }
                        return false
                    }) {
                        messages[index] = message
                    } else {
                        messages.append(message)
                    }
                } else {
                    messages.append(message)
                }
                scrollToBottom()
                
                // Don't clear loading for progress updates
                if case .missionProgress(let progress) = message.content,
                   progress.status == "in_progress" {
                    // Keep loading
                } else {
                    isLoading = false
                }
            }
            return
        }
        
        // Try to decode as DroneExecutionResponse (execution result)
        guard let response = try? JSONDecoder().decode(DroneExecutionResponse.self, from: payload) else {
            AppLogger.chat.error("❌ Failed to decode drone response")
            print("❌ Failed to decode drone response")
            return
        }
        
        guard response.conversationId == conversationId else {
            return
        }
        
        // Log execution result (line-by-line to match chat output)
        if response.result.success {
            if let stdout = response.result.stdout, !stdout.isEmpty {
                let lines = stdout
                    .split(whereSeparator: \.isNewline)
                    .map { String($0) }
                for line in lines where !line.trimmingCharacters(in: .whitespaces).isEmpty {
                    let lower = line.lowercased()
                    if lower.contains("error") || lower.contains("failed") || lower.contains("exception") {
                        AppLogger.chat.error("❌ Drone: \(line)")
                        print("❌ Drone: \(line)")
                    } else {
                        AppLogger.chat.info("✅ Drone: \(line)")
                        print("✅ Drone: \(line)")
                    }
                }
            }
            if let imageUrls = response.imageUrls ?? response.result.imageUrls, !imageUrls.isEmpty {
                AppLogger.chat.info("📷 Images: \(imageUrls.joined(separator: ", "))")
                print("📷 Images: \(imageUrls.joined(separator: ", "))")
            }
            if let summary = response.result.summary {
                AppLogger.chat.info("📋 Mission summary: \(summary)")
                print("📋 Mission summary: \(summary)")
            }
        } else {
            let errorMsg = response.result.failureReason ?? response.result.error ?? response.result.stderr ?? "Unknown error"
            AppLogger.chat.error("❌ Drone failed: \(errorMsg)")
            print("❌ Drone failed: \(errorMsg)")
        }
        
        guard let message = response.toChatMessage() else {
            AppLogger.chat.error("❌ Failed to convert response to chat message")
            print("❌ Failed to convert response to chat message")
            return
        }
        
        Task { @MainActor in
            isLoading = false
            messages.append(message)
            scrollToBottom()
        }
    }
    
    private func handleDroneStatus(_ payload: Data) {
        // Handle status updates - could update a drone status indicator
        // For now, just log
        AppLogger.mqtt.debug("Received drone status update")
    }
    
    private func handleDroneLogs(_ payload: Data) {
        // Parse and log drone execution output in real-time (also print)
        guard let json = try? JSONSerialization.jsonObject(with: payload) as? [String: Any],
              let message = json["message"] as? String else {
            return
        }
        
        let level = json["level"] as? String ?? "INFO"
        let source = json["source"] as? String ?? "drone"
        
        // Log to Xcode console with appropriate level
        switch level.uppercased() {
        case "ERROR":
            AppLogger.chat.error("🚁 [\(source)] \(message)")
            print("🚁 [\(source)] ERROR: \(message)")
        case "WARNING":
            AppLogger.chat.warning("🚁 [\(source)] \(message)")
            print("🚁 [\(source)] WARNING: \(message)")
        default:
            AppLogger.chat.info("🚁 [\(source)] \(message)")
            print("🚁 [\(source)] \(message)")
        }
    }
    
    private func scrollToBottom() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
            withAnimation(.easeOut(duration: 0.2)) {
                if let last = messages.last {
                    scrollProxy?.scrollTo(last.id, anchor: .bottom)
                } else if isLoading {
                    scrollProxy?.scrollTo("typing", anchor: .bottom)
                }
            }
        }
    }
}

// MARK: - Chat Bubble

struct ChatBubble: View {
    let message: ChatMessage
    var onImageSelected: ((MessageContent.ImageOption) -> Void)?
    
    var body: some View {
        HStack {
            if message.sender == .user {
                Spacer(minLength: 60)
            }
            
            VStack(alignment: message.sender == .user ? .trailing : .leading, spacing: 4) {
                bubbleContent
                
                Text(message.timestamp, style: .time)
                    .font(.caption2)
                    .foregroundColor(.white.opacity(0.4))
            }
            
            if message.sender == .drone {
                Spacer(minLength: 60)
            }
        }
    }
    
    @ViewBuilder
    private var bubbleContent: some View {
        switch message.content {
        case .text(let text):
            Text(text)
                .font(.body)
                .foregroundColor(.white)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(bubbleBackground)
                .clipShape(RoundedRectangle(cornerRadius: 18))
            
        case .image(let url):
            AsyncImage(url: url) { phase in
                switch phase {
                case .empty:
                    ProgressView()
                        .frame(width: 200, height: 150)
                case .success(let image):
                    image
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                        .frame(maxWidth: 250, maxHeight: 200)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                case .failure:
                    Image(systemName: "photo")
                        .foregroundStyle(.gray)
                        .frame(width: 200, height: 150)
                @unknown default:
                    EmptyView()
                }
            }
            .padding(4)
            .background(bubbleBackground)
            .clipShape(RoundedRectangle(cornerRadius: 16))
            
        case .media(let items):
            // Group chat reaches this bubble too, so a fleet mission's
            // recordings land here rather than only in the per-drone chat.
            MediaAttachmentView(items: items)
                .padding(8)
                .background(bubbleBackground)
                .clipShape(RoundedRectangle(cornerRadius: 16))

        case .imageChoice(let options):
            VStack(alignment: .leading, spacing: 12) {
                Text("Which one?")
                    .font(.subheadline.bold())
                    .foregroundColor(.white)
                
                LazyVGrid(columns: [
                    GridItem(.flexible()),
                    GridItem(.flexible())
                ], spacing: 8) {
                    ForEach(options) { option in
                        Button {
                            onImageSelected?(option)
                        } label: {
                            VStack(spacing: 4) {
                                AsyncImage(url: option.url) { phase in
                                    switch phase {
                                    case .success(let image):
                                        image
                                            .resizable()
                                            .aspectRatio(contentMode: .fill)
                                            .frame(height: 100)
                                            .clipped()
                                    default:
                                        Rectangle()
                                            .fill(Color.gray.opacity(0.3))
                                            .frame(height: 100)
                                    }
                                }
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                                
                                Text("\(option.id + 1)")
                                    .font(.caption.bold())
                                    .foregroundColor(.cyan)
                            }
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .padding(12)
            .background(bubbleBackground)
            .clipShape(RoundedRectangle(cornerRadius: 16))
            
        case .loading(let text):
            HStack(spacing: 8) {
                ProgressView()
                    .scaleEffect(0.8)
                    .tint(.cyan)
                Text(text)
                    .font(.subheadline)
                    .foregroundColor(.white.opacity(0.7))
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(bubbleBackground)
            .clipShape(RoundedRectangle(cornerRadius: 18))
            
        case .error(let text):
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
                Text(text)
                    .font(.subheadline)
                    .foregroundColor(.red.opacity(0.8))
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(Color.red.opacity(0.15))
            .clipShape(RoundedRectangle(cornerRadius: 18))
            
        case .missionProgress(let progress):
            VStack(alignment: .leading, spacing: 8) {
                // Progress header
                HStack {
                    Image(systemName: progress.status == "completed" ? "checkmark.circle.fill" : 
                          progress.status == "failed" ? "xmark.circle.fill" : "location.circle.fill")
                        .foregroundStyle(progress.status == "completed" ? .green : 
                                        progress.status == "failed" ? .red : .cyan)
                    Text("Mission Progress")
                        .font(.caption.bold())
                        .foregroundColor(.white.opacity(0.7))
                }
                
                // Progress bar
                GeometryReader { geometry in
                    ZStack(alignment: .leading) {
                        Rectangle()
                            .fill(Color.white.opacity(0.2))
                            .frame(height: 6)
                            .clipShape(RoundedRectangle(cornerRadius: 3))
                        
                        Rectangle()
                            .fill(progress.status == "completed" ? Color.green : 
                                  progress.status == "failed" ? Color.red : Color.cyan)
                            .frame(width: geometry.size.width * progress.percentComplete, height: 6)
                            .clipShape(RoundedRectangle(cornerRadius: 3))
                    }
                }
                .frame(height: 6)
                
                // Current phase info
                Text(progress.progressText)
                    .font(.subheadline)
                    .foregroundColor(.white)
                
                // Optional message
                if let message = progress.message, !message.isEmpty {
                    Text(message)
                        .font(.caption)
                        .foregroundColor(.white.opacity(0.6))
                }
            }
            .padding(12)
            .background(bubbleBackground)
            .clipShape(RoundedRectangle(cornerRadius: 16))
        }
    }
    
    private var bubbleBackground: Color {
        message.sender == .user
            ? Color.cyan.opacity(0.3)
            : Color.white.opacity(0.1)
    }
}

// MARK: - Typing Indicator

struct DroneTypingIndicator: View {
    @State private var animationPhase = 0
    
    var body: some View {
        HStack {
            HStack(spacing: 4) {
                ForEach(0..<3) { index in
                    Circle()
                        .fill(Color.white.opacity(0.6))
                        .frame(width: 8, height: 8)
                        .scaleEffect(animationPhase == index ? 1.3 : 1.0)
                        .animation(
                            .easeInOut(duration: 0.4)
                            .repeatForever(autoreverses: true)
                            .delay(Double(index) * 0.15),
                            value: animationPhase
                        )
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(Color.white.opacity(0.1))
            .clipShape(RoundedRectangle(cornerRadius: 18))
            
            Spacer()
        }
        .onAppear {
            animationPhase = 1
        }
    }
}

// MARK: - Preview

#Preview {
    NavigationStack {
        DroneChatView(
            drone: Drone(
                userId: "user-123",
                droneId: "drone-001",
                name: "Test Drone",
                registeredAt: ISO8601DateFormatter().string(from: Date()),
                status: "active"
            ),
            conversationId: "conv-123"
        )
        .environment(APIClient.shared)
        .environment(MQTTService.shared)
    }
}

