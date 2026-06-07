import SwiftUI

struct GroupChatView: View {
    let group: DroneGroup
    let conversationId: String

    @Environment(APIClient.self) private var apiClient
    @Environment(MQTTService.self) private var mqttService
    @Environment(\.dismiss) private var dismiss

    @State private var messages: [GroupChatMessage] = []
    @State private var inputText = ""
    @State private var isLoading = false
    @State private var error: Error?
    @State private var scrollProxy: ScrollViewProxy?

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 12) {
                        ForEach(messages) { msg in
                            GroupChatBubble(message: msg)
                                .id(msg.id)
                        }
                        if isLoading {
                            DroneTypingIndicator()
                                .id("typing")
                        }
                    }
                    .padding()
                }
                .onAppear { scrollProxy = proxy }
                .onChange(of: messages.count) { _, _ in scrollToBottom() }
            }

            Divider().background(Color.white.opacity(0.1))
            inputArea
        }
        .background(Color(red: 0.05, green: 0.05, blue: 0.1))
        .navigationTitle(group.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Color(red: 0.05, green: 0.05, blue: 0.1), for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .alert("Error", isPresented: .constant(error != nil)) {
            Button("OK") { error = nil }
        } message: {
            if let error { Text(error.localizedDescription) }
        }
        .task {
            await setupMQTT()
            await loadHistory()
        }
    }

    // MARK: - Input

    private var inputArea: some View {
        HStack(spacing: 12) {
            TextField("Message your fleet...", text: $inputText, axis: .vertical)
                .textFieldStyle(.plain)
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
                .background(Color.white.opacity(0.1))
                .foregroundColor(.white)
                .clipShape(RoundedRectangle(cornerRadius: 20))
                .lineLimit(1...5)

            Button { sendMessage() } label: {
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

    // MARK: - MQTT

    private func setupMQTT() async {
        // Group ack topic (publish_to_app uses group_id as "drone_id" key)
        mqttService.subscribe(to: "drone/\(group.groupId)/chat/\(conversationId)") { payload in
            handleGroupAck(payload)
        }
        // Each member's reply and progress topics
        for memberId in group.members {
            mqttService.subscribe(to: "drone/\(memberId)/chat/\(conversationId)/response") { payload in
                handleMemberMessage(payload, droneId: memberId)
            }
            mqttService.subscribe(to: "drone/\(memberId)/chat/\(conversationId)/progress") { payload in
                handleMemberMessage(payload, droneId: memberId)
            }
        }
    }

    // MARK: - History

    private func loadHistory() async {
        do {
            let history = try await apiClient.getGroupHistory(groupId: group.groupId, conversationId: conversationId)
            await MainActor.run {
                self.messages = history.map { GroupChatMessage(chatMessage: $0, droneId: nil) }
                scrollToBottom()
            }
        } catch {
            AppLogger.api.debug("No group conversation history: \(error.localizedDescription)")
        }
    }

    // MARK: - Send

    private func sendMessage() {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        inputText = ""

        let userMsg = GroupChatMessage(
            chatMessage: ChatMessage(conversationId: conversationId, sender: .user, content: .text(text)),
            droneId: nil
        )
        messages.append(userMsg)
        scrollToBottom()
        isLoading = true

        Task {
            do {
                _ = try await apiClient.sendGroupMessage(
                    groupId: group.groupId,
                    conversationId: conversationId,
                    message: text
                )
                // Responses arrive over MQTT
            } catch {
                await MainActor.run {
                    self.error = error
                    isLoading = false
                    messages.append(GroupChatMessage(
                        chatMessage: ChatMessage(conversationId: conversationId, sender: .drone, content: .error(error.localizedDescription)),
                        droneId: nil
                    ))
                }
            }
        }
    }

    // MARK: - MQTT handlers

    private func handleGroupAck(_ payload: Data) {
        guard let json = try? JSONDecoder().decode(DroneMessagePayload.self, from: payload),
              json.conversationId == conversationId,
              let chatMsg = json.toChatMessage() else { return }
        Task { @MainActor in
            messages.append(GroupChatMessage(chatMessage: chatMsg, droneId: nil))
            scrollToBottom()
        }
    }

    private func handleMemberMessage(_ payload: Data, droneId: String) {
        if let msgPayload = try? JSONDecoder().decode(DroneMessagePayload.self, from: payload),
           msgPayload.conversationId == conversationId,
           let chatMsg = msgPayload.toChatMessage() {
            Task { @MainActor in
                if case .missionProgress = chatMsg.content {
                    if let idx = messages.lastIndex(where: { msg in
                        guard msg.droneId == droneId else { return false }
                        if case .missionProgress = msg.chatMessage.content { return true }
                        return false
                    }) {
                        messages[idx] = GroupChatMessage(chatMessage: chatMsg, droneId: droneId)
                    } else {
                        messages.append(GroupChatMessage(chatMessage: chatMsg, droneId: droneId))
                    }
                } else {
                    messages.append(GroupChatMessage(chatMessage: chatMsg, droneId: droneId))
                    if case .missionProgress = chatMsg.content {} else { isLoading = false }
                }
                scrollToBottom()
            }
            return
        }

        guard let resp = try? JSONDecoder().decode(DroneExecutionResponse.self, from: payload),
              resp.conversationId == conversationId,
              let chatMsg = resp.toChatMessage() else { return }
        Task { @MainActor in
            isLoading = false
            messages.append(GroupChatMessage(chatMessage: chatMsg, droneId: droneId))
            scrollToBottom()
        }
    }

    private func scrollToBottom() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
            withAnimation(.easeOut(duration: 0.2)) {
                if let last = messages.last {
                    scrollProxy?.scrollTo(last.id, anchor: .bottom)
                }
            }
        }
    }
}

// MARK: - GroupChatMessage wrapper

struct GroupChatMessage: Identifiable {
    let id: String
    let chatMessage: ChatMessage
    let droneId: String?

    init(chatMessage: ChatMessage, droneId: String?) {
        self.id = chatMessage.id
        self.chatMessage = chatMessage
        self.droneId = droneId
    }
}

// MARK: - GroupChatBubble

struct GroupChatBubble: View {
    let message: GroupChatMessage

    var body: some View {
        VStack(alignment: message.chatMessage.sender == .user ? .trailing : .leading, spacing: 2) {
            if message.chatMessage.sender == .drone, let droneId = message.droneId {
                Text(droneId)
                    .font(.caption2)
                    .foregroundColor(.cyan.opacity(0.8))
                    .padding(.leading, 4)
            }
            HStack {
                if message.chatMessage.sender == .user { Spacer(minLength: 60) }
                ChatBubble(message: message.chatMessage)
                if message.chatMessage.sender == .drone { Spacer(minLength: 60) }
            }
        }
    }
}
