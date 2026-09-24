import SwiftUI
import MapKit

struct DroneDetailView: View {
    let drone: Drone
    var onDelete: (() -> Void)?
    
    @Environment(APIClient.self) private var apiClient
    @Environment(MQTTService.self) private var mqttService
    @Environment(\.dismiss) private var dismiss
    
    @State private var selectedTab = 0 // General tab by default
    @State private var droneState: Drone
    @State private var showingDeleteConfirmation = false
    @State private var isDeleting = false
    @State private var error: Error?
    @State private var statusPollTask: Task<Void, Never>?
    @State private var showingStopMenu = false
    @State private var showingStopConfirm = false
    @State private var pendingStopCommand: StopCommand?
    @State private var pendingSystemMessage: String?
    
    private var statusTopic: String { "drone/\(drone.droneId)/status" }
    
    /// Use shared status from MQTTService (single source of truth)
    private var status: DroneStatus? {
        mqttService.droneStatuses[drone.droneId]
    }
    
    /// Use shared online check from MQTTService
    private var isOnline: Bool {
        mqttService.isDroneOnline(droneId: drone.droneId)
    }

    init(drone: Drone, onDelete: (() -> Void)? = nil) {
        self.drone = drone
        self.onDelete = onDelete
        _droneState = State(initialValue: drone)
    }
    
    var body: some View {
        VStack(spacing: 0) {
            // Header
            headerView
            
            // Tab bar
            tabBar
            
            // Tab content
            TabView(selection: $selectedTab) {
                GeneralTabView(
                    drone: droneState,
                    status: status,
                    isOnline: isOnline,
                    onDeleteTapped: { showingDeleteConfirmation = true }
                )
                    .id("general-\(droneState.droneId)")
                    .tag(0)
                
                ChatTabView(
                    drone: droneState,
                    isOnline: isOnline,
                    isActive: selectedTab == 1,
                    systemMessage: $pendingSystemMessage
                )
                    .id("chat-\(droneState.droneId)")
                    .tag(1)
                
                MapTabView(drone: droneState, status: status)
                    .id("map-\(droneState.droneId)")
                    .tag(2)
                
                LogsTabView(drone: droneState, isActive: selectedTab == 3)
                    .id("logs-\(droneState.droneId)")
                    .tag(3)
                
                ConfigureTabView(drone: $droneState, isOnline: isOnline)
                    .id("configure-\(droneState.droneId)")
                    .tag(4)
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
        }
        .background(
            // WhatsApp-style background pattern
            ZStack {
                Color(red: 0.9, green: 0.88, blue: 0.85)
                Image(systemName: "bubble.left.and.bubble.right")
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .opacity(0.03)
            }
            .ignoresSafeArea()
        )
        .navigationBarBackButtonHidden(true)
        .toolbar {
            ToolbarItem(placement: .navigationBarLeading) {
                Button {
                    dismiss()
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: "chevron.left")
                        Text("Back")
                    }
                    .foregroundColor(.primary)
                }
            }
        }
        .alert("Delete Drone", isPresented: $showingDeleteConfirmation) {
            Button("Cancel", role: .cancel) { }
            Button("Delete", role: .destructive) {
                deleteDrone()
            }
        } message: {
            Text("Are you sure you want to delete \(droneState.name)?")
        }
        .alert("Error", isPresented: .constant(error != nil)) {
            Button("OK") { error = nil }
        } message: {
            if let error {
                Text(error.localizedDescription)
            }
        }
        .confirmationDialog("Emergency Actions", isPresented: $showingStopMenu, titleVisibility: .visible) {
            ForEach(StopCommand.allCases, id: \.self) { command in
                Button(command.displayName) {
                    pendingStopCommand = command
                    showingStopConfirm = true
                }
            }
            Button("Cancel", role: .cancel) { }
        }
        .alert("Confirm Command", isPresented: $showingStopConfirm) {
            Button("Cancel", role: .cancel) {
                pendingStopCommand = nil
            }
            Button("Send", role: .destructive) {
                if let command = pendingStopCommand {
                    sendStopCommand(command)
                }
                pendingStopCommand = nil
            }
        } message: {
            if let command = pendingStopCommand {
                Text("Are you sure you want to \(command.displayName)?")
            }
        }
        .task {
            await setupStatusSubscription()
        }
        .onDisappear {
            mqttService.unsubscribe(from: statusTopic)
            statusPollTask?.cancel()
        }
    }
    
    // MARK: - Header
    
    private var headerView: some View {
        HStack(spacing: 12) {
            // Drone avatar
            Image("DroneIcon")
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: 44, height: 44)
                .clipShape(RoundedRectangle(cornerRadius: 10))
            
            VStack(alignment: .leading, spacing: 2) {
                Text(droneState.name)
                    .font(.headline)
                    .foregroundColor(.primary)
                
                HStack(spacing: 4) {
                    Circle()
                        .fill(isOnline ? Color.green : Color.gray)
                        .frame(width: 8, height: 8)
                    
                    Text(isOnline ? "ON" : "OFF")
                        .font(.caption)
                        .foregroundColor(isOnline ? .green : .gray)
                }
            }
            
            Spacer()
            
            // Emergency action menu
            Button {
                showingStopMenu = true
            } label: {
                Text("🛑")
                    .font(.system(size: 33))
            }
        }
        .padding()
        .background(Color.white)
    }
    
    // MARK: - Tab Bar
    
    private var tabBar: some View {
        HStack(spacing: 0) {
            TabButton(title: "General", isSelected: selectedTab == 0) {
                withAnimation { selectedTab = 0 }
            }
            
            TabButton(title: "Chat", isSelected: selectedTab == 1, badge: nil) {
                withAnimation { selectedTab = 1 }
            }
            .accessibilityIdentifier("tab_chat")
            
            TabButton(title: "Map", isSelected: selectedTab == 2) {
                withAnimation { selectedTab = 2 }
            }
            
            TabButton(title: "Logs", isSelected: selectedTab == 3) {
                withAnimation { selectedTab = 3 }
            }
            
            TabButton(title: "Configure", isSelected: selectedTab == 4) {
                withAnimation { selectedTab = 4 }
            }
        }
        .background(Color.white)
    }
    
    // MARK: - Actions
    
    private func setupStatusSubscription() async {
        // Fetch initial status from API
        await fetchStatus()
        
        // Subscribe to real-time status updates via MQTT
        mqttService.subscribe(to: statusTopic) { [self] payload in
            handleStatusUpdate(payload)
        }
        AppLogger.mqtt.debug("Subscribed to status topic: \(statusTopic)")
        
        // Also start polling as fallback (MQTT WebSocket may not work)
        startStatusPolling()
    }
    
    private func startStatusPolling() {
        statusPollTask?.cancel()
        statusPollTask = Task {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 5_000_000_000) // Poll every 5 seconds
                await fetchStatus()
            }
        }
    }
    
    private func fetchStatus() async {
        do {
            let response = try await apiClient.getDroneStatus(droneId: drone.droneId)
            await MainActor.run {
                // Update shared status (single source of truth)
                mqttService.updateDroneStatus(droneId: drone.droneId, status: response.status)
            }
        } catch {
            AppLogger.api.debug("Status fetch failed: \(error.localizedDescription)")
        }
    }
    
    private func handleStatusUpdate(_ payload: Data) {
        // Parse the status update from MQTT (cancels polling if MQTT works)
        do {
            let decoder = JSONDecoder()
            let newStatus = try decoder.decode(DroneStatus.self, from: payload)
            
            Task { @MainActor in
                // Update shared status (single source of truth)
                mqttService.updateDroneStatus(droneId: drone.droneId, status: newStatus)
            }
        } catch {
            AppLogger.mqtt.warning("Failed to decode MQTT status: \(error.localizedDescription)")
        }
    }
    
    private func deleteDrone() {
        isDeleting = true
        Task {
            do {
                try await apiClient.deleteDrone(droneId: drone.droneId)
                await MainActor.run {
                    onDelete?()
                    dismiss()
                }
            } catch {
                await MainActor.run {
                    self.error = error
                    isDeleting = false
                }
            }
        }
    }

    private func sendStopCommand(_ command: StopCommand) {
        pendingSystemMessage = "Telling the drone to \(command.displayName)"
        Task {
            do {
                _ = try await apiClient.sendCommand(droneId: drone.droneId, command: command.commandText)
            } catch {
                await MainActor.run {
                    self.error = error
                }
            }
        }
    }
}

enum StopCommand: CaseIterable {
    case hover
    case land
    case returnToBase
    case killSwitch

    var displayName: String {
        switch self {
        case .hover:
            return "Hover"
        case .land:
            return "Land"
        case .returnToBase:
            return "Return to base"
        case .killSwitch:
            return "Kill Switch"
        }
    }

    var commandText: String {
        switch self {
        case .hover:
            return "hover"
        case .land:
            return "land"
        case .returnToBase:
            return "return to base"
        case .killSwitch:
            return "kill switch"
        }
    }
}

// MARK: - Tab Button

struct TabButton: View {
    let title: String
    let isSelected: Bool
    var badge: Int? = nil
    let action: () -> Void
    
    var body: some View {
        Button(action: action) {
            VStack(spacing: 8) {
                HStack(spacing: 4) {
                    Text(title)
                        .font(.subheadline)
                        .fontWeight(isSelected ? .semibold : .regular)
                        .foregroundColor(isSelected ? .primary : .gray)
                    
                    if let badge, badge > 0 {
                        Text("\(badge)")
                            .font(.caption2.bold())
                            .foregroundColor(.white)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.green)
                            .clipShape(Capsule())
                    }
                }
                
                Rectangle()
                    .fill(isSelected ? Color.green : Color.clear)
                    .frame(height: 3)
            }
        }
        .frame(maxWidth: .infinity)
    }
}

// MARK: - General Tab

struct GeneralTabView: View {
    let drone: Drone
    let status: DroneStatus?
    let isOnline: Bool
    let onDeleteTapped: () -> Void
    
    // Check if we have any telemetry data
    private func hasTelemetry(_ status: DroneStatus) -> Bool {
        let hasValidBattery = (status.battery ?? -1) >= 0
        let hasPosition = status.position != nil
        let hasAttitude = status.attitude != nil
        return hasValidBattery || hasPosition || hasAttitude
    }
    
    // Battery icon based on level
    private func batteryIcon(for level: Double) -> String {
        switch level {
        case 75...: return "battery.100percent"
        case 50..<75: return "battery.75percent"
        case 25..<50: return "battery.50percent"
        case 10..<25: return "battery.25percent"
        default: return "battery.0percent"
        }
    }
    
    // Battery color based on level
    private func batteryColor(for level: Double) -> Color {
        switch level {
        case 50...: return .green
        case 20..<50: return .orange
        default: return .red
        }
    }
    
    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                // Live Video Stream
                VideoStreamView(drone: drone)
                
                // Status Card
                VStack(alignment: .leading, spacing: 12) {
                    Text("Status")
                        .font(.headline)
                    
                    HStack {
                        Label(isOnline ? "Connected" : "Offline", systemImage: isOnline ? "wifi" : "wifi.slash")
                            .foregroundColor(isOnline ? .green : .gray)
                        
                        Spacer()
                        
                        if let armed = status?.armed {
                            Text(armed ? "ARMED" : "DISARMED")
                                .font(.caption.bold())
                                .foregroundColor(armed ? .orange : .green)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                                .background((armed ? Color.orange : Color.green).opacity(0.2))
                                .clipShape(Capsule())
                        }
                    }
                }
                .padding()
                .background(Color.white)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                
                // Telemetry
                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        Text("Telemetry")
                            .font(.headline)
                        Spacer()
                        if let mode = status?.mode {
                            Text(mode)
                                .font(.caption.bold())
                                .foregroundColor(.blue)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                                .background(Color.blue.opacity(0.15))
                                .clipShape(Capsule())
                        }
                    }
                    
                    if let status, hasTelemetry(status) {
                        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                            if let battery = status.battery, battery >= 0 {
                                TelemetryCard(
                                    icon: batteryIcon(for: battery),
                                    label: "Battery",
                                    value: "\(Int(battery))%",
                                    valueColor: batteryColor(for: battery)
                                )
                            }
                            if let pos = status.position {
                                TelemetryCard(icon: "arrow.up", label: "Altitude", value: String(format: "%.1fm", pos.altitude))
                            }
                            if let att = status.attitude {
                                TelemetryCard(icon: "location.north", label: "Heading", value: "\(Int(att.yaw))°")
                            }
                        }
                        
                        BatteryReadinessView(status: status)

                        // Position coordinates (if available)
                        if let pos = status.position {
                            HStack {
                                Image(systemName: "location")
                                    .foregroundColor(.gray)
                                Text(String(format: "%.6f, %.6f", pos.latitude, pos.longitude))
                                    .font(.caption)
                                    .foregroundColor(.gray)
                            }
                            .padding(.top, 4)
                        }
                    } else {
                        // No telemetry available
                        VStack(spacing: 8) {
                            Image(systemName: "antenna.radiowaves.left.and.right.slash")
                                .font(.title)
                                .foregroundColor(.gray.opacity(0.5))
                            Text("No telemetry data")
                                .font(.subheadline)
                                .foregroundColor(.gray)
                            Text("Connect a PX4 flight controller to see live data")
                                .font(.caption)
                                .foregroundColor(.gray.opacity(0.7))
                                .multilineTextAlignment(.center)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 20)
                    }
                }
                .padding()
                .background(Color.white)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                
                // Drone Info
                VStack(alignment: .leading, spacing: 12) {
                    Text("Information")
                        .font(.headline)
                    
                    DroneInfoRow(label: "Drone ID", value: drone.droneId)
                    DroneInfoRow(label: "Registered", value: drone.registeredAt)
                }
                .padding()
                .background(Color.white)
                .clipShape(RoundedRectangle(cornerRadius: 12))

                // Delete action
                Button(role: .destructive) {
                    onDeleteTapped()
                } label: {
                    HStack {
                        Image(systemName: "trash")
                        Text("Delete this drone")
                            .fontWeight(.semibold)
                    }
                    .frame(maxWidth: .infinity)
                    .padding()
                    .foregroundColor(.red)
                    .background(Color.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                }
            }
            .padding()
        }
    }
}

struct TelemetryCard: View {
    let icon: String
    let label: String
    let value: String
    var valueColor: Color = .primary
    
    var body: some View {
        VStack(spacing: 6) {
            Image(systemName: icon)
                .font(.title2)
                .foregroundColor(.cyan)
            Text(value)
                .font(.headline)
                .foregroundColor(valueColor)
            Text(label)
                .font(.caption)
                .foregroundColor(.gray)
        }
        .frame(maxWidth: .infinity)
        .padding()
        .background(Color.gray.opacity(0.1))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct DroneInfoRow: View {
    let label: String
    let value: String
    
    var body: some View {
        HStack {
            Text(label)
                .foregroundColor(.gray)
            Spacer()
            Text(value)
                .font(.system(.body, design: .monospaced))
        }
    }
}

// MARK: - Chat Tab

struct ChatTabView: View {
    let drone: Drone
    let isOnline: Bool
    let isActive: Bool
    @Binding var systemMessage: String?
    
    @Environment(APIClient.self) private var apiClient
    @Environment(MQTTService.self) private var mqttService
    
    @State private var displayMessages: [DisplayMessage] = []
    @State private var messageText = ""
    @State private var isLoading = false
    @State private var conversationId: String?
    @State private var scrollProxy: ScrollViewProxy?
    @State private var receivedMQTTAck = false
    @State private var receivedMQTTResponse = false
    
    var body: some View {
        VStack(spacing: 0) {
            // Messages
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 8) {
                        ForEach(displayMessages) { message in
                            MessageBubble(message: message)
                                .id(message.id)
                        }
                        
                        if isLoading {
                            HStack {
                                ProgressIndicatorView()
                                Spacer()
                            }
                            .padding(.horizontal)
                        }
                    }
                    .padding()
                }
                .onAppear { scrollProxy = proxy }
                .onChange(of: displayMessages.count) { _, _ in
                    withAnimation {
                        if let lastId = displayMessages.last?.id {
                            proxy.scrollTo(lastId, anchor: .bottom)
                        }
                    }
                }
            }
            
            // Input bar
            ChatInputBar(
                text: $messageText,
                isLoading: isLoading,
                onSend: sendMessage
            )
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { await startNewConversation() }
                } label: {
                    Image(systemName: "plus.message")
                }
                .disabled(isLoading)
            }
        }
        .task(id: isActive) {
            guard isActive else { return }
            await startConversation()
        }
        .onChange(of: systemMessage) { _, newValue in
            guard let newValue else { return }
            appendSystemMessage(newValue)
            systemMessage = nil
        }
        .onDisappear {
            // Unsubscribe from MQTT when leaving
            if let convId = conversationId {
                mqttService.unsubscribe(from: "drone/\(drone.droneId)/chat/\(convId)")
            }
        }
    }
    
    private var conversationKey: String {
        "conversation_\(drone.droneId)"
    }
    
    private func startNewConversation() async {
        // Unsubscribe from old conversation
        if let oldConvId = conversationId {
            mqttService.unsubscribe(from: "drone/\(drone.droneId)/chat/\(oldConvId)")
        }
        
        // Clear stored conversation
        UserDefaults.standard.removeObject(forKey: conversationKey)
        
        await MainActor.run {
            conversationId = nil
            displayMessages = []
        }
        
        // Create new conversation
        await createNewConversation()
    }
    
    private func startConversation() async {
        // Try to load existing conversation first
        if let storedConvId = UserDefaults.standard.string(forKey: conversationKey) {
            do {
                print("🗂️ Chat: Loading stored conversation \(storedConvId)")
                let messages = try await apiClient.getConversationHistory(droneId: drone.droneId, conversationId: storedConvId)
                
                // If conversation is empty or expired, create a new one
                if messages.isEmpty {
                    AppLogger.api.debug("Stored conversation is empty, creating new one")
                    await createNewConversation()
                    return
                }
                
                await MainActor.run {
                    self.conversationId = storedConvId
                    self.displayMessages = messages.map { msg in
                        DisplayMessage.from(chatMessage: msg)
                    }
                    // Subscribe to MQTT for real-time updates
                    setupMQTTSubscription(conversationId: storedConvId)
                }
                return // Successfully loaded existing conversation
            } catch {
                // Stored conversation invalid/expired, will create new one
                print("⚠️ Chat: Stored conversation load failed: \(error.localizedDescription)")
                AppLogger.api.debug("Stored conversation expired, creating new: \(error.localizedDescription)")
            }
        }
        
        // Create new conversation
        await createNewConversation()
    }
    
    private func createNewConversation() async {
        do {
            let response = try await apiClient.startConversation(droneId: drone.droneId)
            await MainActor.run {
                conversationId = response.conversationId
                // Persist for future sessions
                UserDefaults.standard.set(response.conversationId, forKey: conversationKey)
                // Subscribe to MQTT for real-time updates
                setupMQTTSubscription(conversationId: response.conversationId)
                AppLogger.api.debug("Created new conversation: \(response.conversationId)")
                print("🆕 Chat: Created conversation \(response.conversationId)")
            }
        } catch {
            AppLogger.api.error("Failed to create conversation: \(error)")
            print("❌ Chat: Failed to create conversation: \(error.localizedDescription)")
        }
    }
    
    private func setupMQTTSubscription(conversationId: String) {
        let topic = "drone/\(drone.droneId)/chat/\(conversationId)"
        print("📡 Subscribing to MQTT topic: \(topic)")
        
        mqttService.subscribe(to: topic) { [self] payload in
            handleMQTTMessage(payload)
        }
    }
    
    /// Parse the `media` array out of a loosely-decoded MQTT payload.
    ///
    /// This path uses JSONSerialization rather than Codable (the payloads are
    /// heterogeneous and partially untyped), so the strict decoding in
    /// MessageContent.MediaItem doesn't apply here and the leniency has to be
    /// repeated: an item missing a usable URL is dropped, and an unknown kind
    /// falls back to .photo rather than discarding the whole message.
    static func parseMedia(_ raw: Any?) -> [MessageContent.MediaItem]? {
        guard let array = raw as? [[String: Any]] else { return nil }
        let items = array.compactMap { entry -> MessageContent.MediaItem? in
            guard let urlString = entry["url"] as? String,
                  let url = URL(string: urlString) else { return nil }
            let kind = MessageContent.MediaItem.Kind(
                rawValue: entry["kind"] as? String ?? ""
            ) ?? .photo
            return MessageContent.MediaItem(url: url, kind: kind)
        }
        return items.isEmpty ? nil : items
    }

    private func handleMQTTMessage(_ payload: Data) {
        if let jsonString = String(data: payload, encoding: .utf8) {
            print("📨 Chat MQTT payload: \(jsonString)")
        } else {
            print("📨 Chat MQTT payload: \(payload.count) bytes (non-utf8)")
        }
        guard let json = try? JSONSerialization.jsonObject(with: payload) as? [String: Any] else {
            return
        }
        
        let messageType = json["message_type"] as? String ?? "text"
        let text = json["text"] as? String ?? ""
        let imageUrls = json["image_urls"] as? [String]
        let imageOptions = json["image_options"] as? [[String: Any]]
        let media = Self.parseMedia(json["media"])
        
        Task { @MainActor in
            // Handle "ack" - cloud received request and forwarded to drone
            if messageType == "ack" {
                print("✅ Chat MQTT ack: \(text)")
                // Update in-progress message with the ack text if provided
                if let idx = displayMessages.lastIndex(where: { !$0.isUser && $0.status == .inProgress }) {
                    displayMessages[idx].content = text.isEmpty ? "Drone is processing..." : text
                }
                receivedMQTTAck = true
                return
            }
            
            // Handle "error" - something went wrong (drone offline, timeout, etc.)
            if messageType == "error" {
                print("❌ Chat MQTT error: \(text)")
                // Remove loading message and show error
                displayMessages.removeAll { msg in
                    msg.status == .inProgress || msg.content.contains("Processing")
                }
                displayMessages.append(DisplayMessage(
                    id: UUID().uuidString,
                    content: text,
                    isUser: false,
                    timestamp: Date(),
                    status: .failed
                ))
                isLoading = false
                receivedMQTTResponse = true
                return
            }

            // De-dupe: if we already show the same in-progress text, just mark delivered
            if messageType == "text",
               let idx = displayMessages.lastIndex(where: { !$0.isUser && $0.content == text }),
               displayMessages[idx].status == .inProgress {
                print("ℹ️ Chat MQTT de-dupe: updating in-progress message")
                displayMessages[idx].status = .delivered
                isLoading = false
                receivedMQTTResponse = true
                return
            }
            
            // Handle actual response (text, image, image_choice)
            print("💬 Chat MQTT message_type=\(messageType), text=\(text.prefix(80))")
            // Remove any "loading" or "in progress" messages
            displayMessages.removeAll { msg in
                msg.status == .inProgress || msg.content.contains("Processing")
            }
            
            // Create the new message
            var newMessage = DisplayMessage(
                id: UUID().uuidString,
                content: text,
                isUser: false,
                timestamp: Date(),
                status: .delivered
            )
            
            // Captured media. Set before the imageURLs fallbacks and
            // checked first when rendering, because the server sends
            // image_urls alongside media for older builds — taking both here
            // would show the same photos twice.
            if let media, !media.isEmpty {
                newMessage.media = media
            } else if let urls = imageUrls, !urls.isEmpty {
                newMessage.imageURLs = urls
            }

            // Handle image choice options
            if let options = imageOptions, !options.isEmpty {
                newMessage.imageURLs = options.compactMap { $0["url"] as? String }
            }
            
            displayMessages.append(newMessage)
            isLoading = false
            receivedMQTTResponse = true
        }
    }
    
    private func sendMessage() {
        let text = messageText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        
        // Clear input immediately for responsive UI
        messageText = ""
        
        guard let convId = conversationId else {
            print("⚠️ No conversation ID, cannot send message")
            return
        }
        print("📤 Chat send: conv=\(convId) text=\(text)")
        
        // Reset MQTT tracking flags
        receivedMQTTAck = false
        receivedMQTTResponse = false
        
        // Add user message
        let userMessage = DisplayMessage(
            id: UUID().uuidString,
            content: text,
            isUser: true,
            timestamp: Date(),
            status: .sending
        )
        displayMessages.append(userMessage)
        isLoading = true
        
        Task {
            do {
                let response = try await apiClient.sendChatMessage(
                    droneId: drone.droneId,
                    conversationId: convId,
                    message: text
                )
                // Check if we need to wait for drone response
                var needsWait = false
                if let immediate = response.immediateResponse {
                    if case .loading = immediate.content {
                        needsWait = true
                    }
                } else {
                    needsWait = true
                }
                
                await MainActor.run {
                    // Update user message status
                    if let idx = displayMessages.firstIndex(where: { $0.id == userMessage.id }) {
                        displayMessages[idx].status = .delivered
                    }
                    
                    // Add assistant response if immediate
                    if let immediate = response.immediateResponse {
                        print("⚡️ Chat immediate response: \(immediate.content)")
                        displayMessages.append(DisplayMessage.from(chatMessage: immediate))
                    } else {
                        // Add "in progress" message
                        let inProgressMsg = DisplayMessage(
                            id: UUID().uuidString,
                            content: "Processing your request...",
                            isUser: false,
                            timestamp: Date(),
                            status: .inProgress
                        )
                        displayMessages.append(inProgressMsg)
                    }
                    isLoading = false
                }
                
                // Wait for MQTT response - no need to poll if cloud sends ack/error via MQTT
                if needsWait {
                    // Wait up to 60 seconds for MQTT to deliver the response
                    for _ in 0..<60 {
                        try? await Task.sleep(nanoseconds: 1_000_000_000)
                        
                        let gotResponse = await MainActor.run { receivedMQTTResponse }
                        if gotResponse { return }
                    }
                    
                    // Only fall back to polling if we never got an MQTT response
                    let gotResponse = await MainActor.run { receivedMQTTResponse }
                    if !gotResponse {
                        print("⏳ Chat MQTT timeout; polling for result")
                        await pollForResult(convId: convId)
                    }
                }
            } catch {
                await MainActor.run {
                    isLoading = false
                    print("❌ Chat send failed: \(error.localizedDescription)")
                    // Add error message
                    displayMessages.append(DisplayMessage(
                        id: UUID().uuidString,
                        content: "Failed to send message: \(error.localizedDescription)",
                        isUser: false,
                        timestamp: Date(),
                        status: .failed
                    ))
                }
            }
        }
    }

    private func appendSystemMessage(_ text: String) {
        displayMessages.append(DisplayMessage(
            id: UUID().uuidString,
            content: text,
            isUser: false,
            timestamp: Date(),
            status: .delivered
        ))
    }
    
    private func pollForResult(convId: String) async {
        // Poll for updated conversation (up to 3 minutes for photo capture)
        for _ in 0..<180 {
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            
            do {
                let messages = try await apiClient.getConversationHistory(droneId: drone.droneId, conversationId: convId)
                
                // Check for new assistant messages
                if let lastMsg = messages.last, lastMsg.sender == .drone {
                    await MainActor.run {
                        let newMessage = DisplayMessage.from(chatMessage: lastMsg)
                        
                        // Replace the "in progress" message if found
                        if let idx = displayMessages.lastIndex(where: { !$0.isUser && $0.status == .inProgress }) {
                            displayMessages[idx] = newMessage
                        } else if !displayMessages.contains(where: { $0.id == newMessage.id }) {
                            displayMessages.append(newMessage)
                        }
                    }
                    
                    // If we got content (not loading), stop polling
                    if case .loading = lastMsg.content {
                        continue
                    } else {
                        break
                    }
                }
            } catch {
                AppLogger.api.error("Poll failed: \(error)")
            }
        }
    }
}

// MARK: - Display Message (UI Model)

struct DisplayMessage: Identifiable {
    let id: String
    var content: String
    let isUser: Bool
    let timestamp: Date
    var imageURLs: [String]?
    var media: [MessageContent.MediaItem]?
    var status: MessageStatus
    
    enum MessageStatus {
        case sending
        case delivered
        case inProgress
        case failed
    }
    
    static func from(chatMessage: ChatMessage) -> DisplayMessage {
        var content: String = ""
        var imageURLs: [String]? = nil
        var media: [MessageContent.MediaItem]? = nil
        var status: MessageStatus = .delivered
        
        switch chatMessage.content {
        case .text(let text):
            content = text
        case .image(let url):
            imageURLs = [url.absoluteString]
        case .imageChoice(let options):
            imageURLs = options.map { $0.url.absoluteString }
            content = "Here's what I see:"
        case .media(let items):
            media = items
        case .loading(let text):
            content = text
            status = .inProgress
        case .error(let text):
            content = text
            status = .failed
        case .missionProgress(let progress):
            content = progress.message ?? progress.progressText
            status = progress.status == "completed" ? .delivered :
                     progress.status == "failed" ? .failed : .inProgress
        }
        
        return DisplayMessage(
            id: chatMessage.id,
            content: content,
            isUser: chatMessage.sender == .user,
            timestamp: chatMessage.timestamp,
            imageURLs: imageURLs,
            media: media,
            status: status
        )
    }
}

// MARK: - Chat Bubble

struct MessageBubble: View {
    let message: DisplayMessage
    
    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            if message.isUser {
                Spacer(minLength: 60)
            } else {
                // Avatar for drone
                ZStack {
                    Circle()
                        .fill(Color.gray.opacity(0.2))
                        .frame(width: 32, height: 32)
                    
                    Image(systemName: "sparkles")
                        .font(.caption)
                        .foregroundColor(.gray)
                }
            }
            
            VStack(alignment: message.isUser ? .trailing : .leading, spacing: 4) {
                // Message content
                VStack(alignment: .leading, spacing: 8) {
                    if !message.content.isEmpty {
                        Text(message.content)
                            .font(.body)
                            .foregroundColor(message.isUser ? .white : .primary)
                    }

                    // Captured media: one file inline, several as an album
                    // card. Checked before imageURLs so a `media` message
                    // never renders twice — the server populates image_urls
                    // alongside media for older builds' benefit.
                    if let media = message.media, !media.isEmpty {
                        MediaAttachmentView(items: media)
                    } else if let imageURLs = message.imageURLs, !imageURLs.isEmpty {
                        ForEach(Array(imageURLs.enumerated()), id: \.offset) { _, urlString in
                            AsyncImage(url: URL(string: urlString)) { image in
                                image
                                    .resizable()
                                    .aspectRatio(contentMode: .fill)
                                    .frame(maxWidth: 250, maxHeight: 200)
                                    .clipShape(RoundedRectangle(cornerRadius: 8))
                            } placeholder: {
                                RoundedRectangle(cornerRadius: 8)
                                    .fill(Color.gray.opacity(0.2))
                                    .frame(height: 150)
                                    .overlay(ProgressView())
                            }
                        }
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(message.isUser ? Color(red: 0.2, green: 0.3, blue: 0.25) : Color.white)
                .clipShape(ChatBubbleShape(isUser: message.isUser))
                .shadow(color: .black.opacity(0.05), radius: 2, y: 1)
                
                // Timestamp and status
                HStack(spacing: 4) {
                    Text(message.timestamp, style: .time)
                        .font(.caption2)
                        .foregroundColor(.gray)
                    
                    if message.isUser {
                        switch message.status {
                        case .sending:
                            Image(systemName: "clock")
                                .font(.caption2)
                                .foregroundColor(.gray)
                        case .delivered:
                            Image(systemName: "checkmark")
                                .font(.caption2)
                                .foregroundColor(.cyan)
                        case .inProgress:
                            ProgressView()
                                .scaleEffect(0.5)
                        case .failed:
                            Image(systemName: "exclamationmark.circle")
                                .font(.caption2)
                                .foregroundColor(.red)
                        }
                    } else if message.status == .inProgress {
                        Text("IN PROGRESS")
                            .font(.caption2.bold())
                            .foregroundColor(.orange)
                    }
                }
            }
            
            if !message.isUser {
                Spacer(minLength: 60)
            } else {
                // User avatar
                Circle()
                    .fill(Color.cyan)
                    .frame(width: 32, height: 32)
                    .overlay(
                        Text(AuthService.shared.currentUser?.initials ?? "U")
                            .font(.caption2.bold())
                            .foregroundColor(.white)
                    )
            }
        }
    }
}

struct ChatBubbleShape: Shape {
    let isUser: Bool
    
    func path(in rect: CGRect) -> Path {
        let radius: CGFloat = 16
        var path = Path()
        
        if isUser {
            path.addRoundedRect(in: rect, cornerRadii: RectangleCornerRadii(
                topLeading: radius,
                bottomLeading: radius,
                bottomTrailing: 4,
                topTrailing: radius
            ))
        } else {
            path.addRoundedRect(in: rect, cornerRadii: RectangleCornerRadii(
                topLeading: 4,
                bottomLeading: radius,
                bottomTrailing: radius,
                topTrailing: radius
            ))
        }
        
        return path
    }
}

// MARK: - Chat Input Bar

struct ChatInputBar: View {
    @Binding var text: String
    let isLoading: Bool
    let onSend: () -> Void
    
    var body: some View {
        HStack(spacing: 12) {
            // Text field
            TextField("Type your message...", text: $text, axis: .vertical)
                .textFieldStyle(.plain)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(Color.white)
                .clipShape(RoundedRectangle(cornerRadius: 20))
                .lineLimit(1...5)
                .accessibilityIdentifier("e2e_chat_input")

            // Send button
            Button(action: onSend) {
                Image(systemName: "paperplane.fill")
                    .font(.title3)
                    .foregroundColor(.white)
                    .frame(width: 44, height: 44)
                    .background(text.isEmpty ? Color.gray : Color.green)
                    .clipShape(Circle())
            }
            .disabled(text.isEmpty || isLoading)
            .accessibilityIdentifier("e2e_chat_send")
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(Color(red: 0.95, green: 0.93, blue: 0.9))
    }
}

// MARK: - Progress Indicator

struct ProgressIndicatorView: View {
    var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<3) { i in
                Circle()
                    .fill(Color.gray)
                    .frame(width: 8, height: 8)
                    .opacity(0.5)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(Color.white)
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }
}

// MARK: - Map Tab

struct MapTabView: View {
    let drone: Drone
    let status: DroneStatus?
    
    @State private var cameraPosition: MapCameraPosition = .automatic
    
    private var droneCoordinate: CLLocationCoordinate2D? {
        guard let pos = status?.position else { return nil }
        return CLLocationCoordinate2D(latitude: pos.latitude, longitude: pos.longitude)
    }
    
    var body: some View {
        ZStack {
            if let coordinate = droneCoordinate {
                Map(position: $cameraPosition) {
                    // Drone marker
                    Annotation(drone.name, coordinate: coordinate) {
                        ZStack {
                            Circle()
                                .fill(Color.white)
                                .frame(width: 44, height: 44)
                                .shadow(color: .black.opacity(0.2), radius: 4, y: 2)
                            
                            Image("DroneIcon")
                                .resizable()
                                .aspectRatio(contentMode: .fit)
                                .frame(width: 36, height: 36)
                                .clipShape(Circle())
                        }
                    }
                }
                .mapStyle(.standard(elevation: .realistic, pointsOfInterest: .excludingAll))
                .mapControls {
                    MapCompass()
                    MapScaleView()
                    MapUserLocationButton()
                }
                .onAppear {
                    // Center on drone with appropriate zoom
                    cameraPosition = .region(MKCoordinateRegion(
                        center: coordinate,
                        span: MKCoordinateSpan(latitudeDelta: 0.005, longitudeDelta: 0.005)
                    ))
                }
                .onChange(of: status?.position?.latitude) { _, _ in
                    // Update camera when position changes
                    if let newCoord = droneCoordinate {
                        withAnimation(.easeInOut(duration: 0.5)) {
                            cameraPosition = .region(MKCoordinateRegion(
                                center: newCoord,
                                span: MKCoordinateSpan(latitudeDelta: 0.005, longitudeDelta: 0.005)
                            ))
                        }
                    }
                }
                
                // Coordinates overlay
                VStack {
                    Spacer()
                    
                    HStack {
                        VStack(alignment: .leading, spacing: 4) {
                            if let pos = status?.position {
                                HStack(spacing: 6) {
                                    Image(systemName: "location.fill")
                                        .font(.caption)
                                        .foregroundColor(.cyan)
                                    Text(String(format: "%.6f, %.6f", pos.latitude, pos.longitude))
                                        .font(.system(.caption, design: .monospaced))
                                        .foregroundColor(.primary)
                                }
                                
                                HStack(spacing: 6) {
                                    Image(systemName: "arrow.up")
                                        .font(.caption)
                                        .foregroundColor(.cyan)
                                    Text(String(format: "%.1f m altitude", pos.altitude))
                                        .font(.system(.caption, design: .monospaced))
                                        .foregroundColor(.primary)
                                }
                            }
                        }
                        .padding(12)
                        .background(.ultraThinMaterial)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        
                        Spacer()
                        
                        // Re-center button
                        Button {
                            if let coord = droneCoordinate {
                                withAnimation(.easeInOut(duration: 0.3)) {
                                    cameraPosition = .region(MKCoordinateRegion(
                                        center: coord,
                                        span: MKCoordinateSpan(latitudeDelta: 0.005, longitudeDelta: 0.005)
                                    ))
                                }
                            }
                        } label: {
                            Image(systemName: "airplane.circle.fill")
                                .font(.title)
                                .foregroundColor(.cyan)
                                .background(
                                    Circle()
                                        .fill(.ultraThinMaterial)
                                        .frame(width: 44, height: 44)
                                )
                        }
                    }
                    .padding()
                }
            } else {
                // No GPS data available
                VStack(spacing: 16) {
                    ZStack {
                        Circle()
                            .fill(Color.gray.opacity(0.1))
                            .frame(width: 100, height: 100)
                        
                        Image(systemName: "location.slash")
                            .font(.system(size: 40))
                            .foregroundColor(.gray)
                    }
                    
                    Text("No GPS Data")
                        .font(.headline)
                        .foregroundColor(.primary)
                    
                    Text("Waiting for drone position...")
                        .font(.subheadline)
                        .foregroundColor(.gray)
                    
                    if status == nil {
                        Text("Connect to the drone to see its location")
                            .font(.caption)
                            .foregroundColor(.gray.opacity(0.7))
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 40)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Color.white)
            }
        }
    }
}

// MARK: - Logs Tab

struct LogsTabView: View {
    let drone: Drone
    let isActive: Bool
    
    @Environment(APIClient.self) private var apiClient
    @Environment(MQTTService.self) private var mqttService
    @State private var logs: [DroneLogEntry] = []
    @State private var isAutoScroll = true
    @State private var filterLevel: String? = nil
    @State private var filterSource: String? = nil
    @State private var isLoading = false
    @State private var pollTask: Task<Void, Never>?
    @State private var lastTimestamp: String? = nil
    @State private var isMqttActive = false
    @State private var lastMqttMessage: Date? = nil
    
    private var logsTopic: String { "drone/\(drone.droneId)/logs" }
    
    private var filteredLogs: [DroneLogEntry] {
        logs.filter { log in
            let levelMatch = filterLevel == nil || log.level == filterLevel
            let sourceMatch = filterSource == nil || log.source == filterSource
            return levelMatch && sourceMatch
        }
    }
    
    private var uniqueSources: [String] {
        Array(Set(logs.map { $0.source })).sorted()
    }
    
    var body: some View {
        VStack(spacing: 0) {
            // Filter bar
            filterBar
            
            // Logs list
            if logs.isEmpty && !isLoading {
                emptyState
            } else {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 4) {
                            ForEach(filteredLogs) { log in
                                LogEntryView(log: log)
                                    .id(log.id)
                            }
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                    }
                    .onChange(of: logs.count) { _, _ in
                        if isAutoScroll, let lastLog = filteredLogs.last {
                            withAnimation(.easeOut(duration: 0.2)) {
                                proxy.scrollTo(lastLog.id, anchor: .bottom)
                            }
                        }
                    }
                }
            }
        }
        .background(Color(red: 0.1, green: 0.1, blue: 0.12))
        .task(id: isActive) {
            guard isActive else { return }
            // Subscribe to MQTT for real-time logs
            setupMqttSubscription()
            
            // Fetch initial logs from API
            await fetchLogs()
            
            // Start polling as fallback (will check if MQTT is working)
            startPollingFallback()
        }
        .onDisappear {
            mqttService.unsubscribe(from: logsTopic)
            pollTask?.cancel()
        }
    }
    
    private var filterBar: some View {
        HStack(spacing: 12) {
            // Source filter
            Menu {
                Button("All Sources") { filterSource = nil }
                Divider()
                ForEach(uniqueSources, id: \.self) { source in
                    Button(source) { filterSource = source }
                }
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: "line.3.horizontal.decrease.circle")
                    Text(filterSource ?? "Source")
                        .lineLimit(1)
                }
                .font(.caption)
                .foregroundColor(filterSource != nil ? .cyan : .gray)
            }
            
            // Level filter
            Menu {
                Button("All Levels") { filterLevel = nil }
                Divider()
                Button("INFO") { filterLevel = "INFO" }
                Button("WARNING") { filterLevel = "WARNING" }
                Button("ERROR") { filterLevel = "ERROR" }
                Button("DEBUG") { filterLevel = "DEBUG" }
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: "exclamationmark.triangle")
                    Text(filterLevel ?? "Level")
                }
                .font(.caption)
                .foregroundColor(filterLevel != nil ? .cyan : .gray)
            }
            
            Spacer()
            
            // Connection status indicator
            HStack(spacing: 4) {
                Circle()
                    .fill(isMqttActive ? Color.green : Color.orange)
                    .frame(width: 6, height: 6)
                Text(isMqttActive ? "Live" : "Polling")
                    .font(.caption2)
                    .foregroundColor(.gray)
            }
            
            // Loading indicator
            if isLoading {
                ProgressView()
                    .scaleEffect(0.7)
            }
            
            // Auto-scroll toggle
            Button {
                isAutoScroll.toggle()
            } label: {
                Image(systemName: isAutoScroll ? "arrow.down.circle.fill" : "arrow.down.circle")
                    .foregroundColor(isAutoScroll ? .cyan : .gray)
            }
            
            // Clear logs
            Button {
                logs.removeAll()
                lastTimestamp = nil
            } label: {
                Image(systemName: "trash")
                    .foregroundColor(.gray)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Color(red: 0.15, green: 0.15, blue: 0.17))
    }
    
    private var emptyState: some View {
        VStack(spacing: 16) {
            Image(systemName: "text.alignleft")
                .font(.system(size: 50))
                .foregroundColor(.gray.opacity(0.4))
            
            Text("No Logs Yet")
                .font(.headline)
                .foregroundColor(.gray)
            
            Text("Logs from drone daemon, ArduPilot,\nand LLM-generated code will appear here")
                .font(.caption)
                .foregroundColor(.gray.opacity(0.7))
                .multilineTextAlignment(.center)
            
            HStack(spacing: 6) {
                ProgressView()
                    .scaleEffect(0.6)
                Text(isMqttActive ? "Connected via MQTT" : "Polling for logs...")
                    .font(.caption2)
                    .foregroundColor(.gray.opacity(0.5))
            }
            .padding(.top, 8)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
    
    // MARK: - MQTT (Primary)
    
    private func setupMqttSubscription() {
        mqttService.subscribe(to: logsTopic) { payload in
            handleMqttLogMessage(payload)
        }
        AppLogger.mqtt.debug("Subscribed to logs topic: \(logsTopic)")
    }
    
    private func handleMqttLogMessage(_ payload: Data) {
        guard let json = try? JSONSerialization.jsonObject(with: payload) as? [String: Any] else {
            return
        }
        
        let timestamp = json["timestamp"] as? String ?? ISO8601DateFormatter().string(from: Date())
        let level = json["level"] as? String ?? "INFO"
        let source = json["source"] as? String ?? "unknown"
        let message = json["message"] as? String ?? ""
        let code = json["code"] as? String
        
        let logEntry = DroneLogEntry(
            id: UUID().uuidString,
            timestamp: timestamp,
            level: level,
            source: source,
            message: message,
            code: code
        )
        
        Task { @MainActor in
            // Mark MQTT as active
            isMqttActive = true
            lastMqttMessage = Date()
            
            // Avoid duplicates
            if !logs.contains(where: { $0.timestamp == timestamp && $0.message == message }) {
                // Keep max 500 logs
                if logs.count > 500 {
                    logs.removeFirst(100)
                }
                logs.append(logEntry)
            }
        }
    }
    
    // MARK: - API Polling (Fallback)
    
    private func fetchLogs() async {
        isLoading = true
        defer { isLoading = false }
        
        do {
            let response = try await apiClient.getLogs(droneId: drone.droneId, since: lastTimestamp, limit: 100)
            
            await MainActor.run {
                for logEntry in response.logs {
                    // Avoid duplicates
                    if !logs.contains(where: { $0.timestamp == logEntry.timestamp && $0.message == logEntry.message }) {
                        logs.append(DroneLogEntry(
                            id: UUID().uuidString,
                            timestamp: logEntry.timestamp,
                            level: logEntry.level,
                            source: logEntry.source,
                            message: logEntry.message,
                            code: logEntry.code
                        ))
                    }
                }
                
                // Update last timestamp for incremental fetching
                if let lastLog = response.logs.last {
                    lastTimestamp = lastLog.timestamp
                }
                
                // Keep max 500 logs
                if logs.count > 500 {
                    logs.removeFirst(logs.count - 500)
                }
            }
        } catch {
            AppLogger.api.debug("Failed to fetch logs: \(error.localizedDescription)")
        }
    }
    
    private func startPollingFallback() {
        pollTask?.cancel()
        pollTask = Task {
            // Wait a bit to see if MQTT delivers messages
            try? await Task.sleep(nanoseconds: 5_000_000_000) // 5 seconds
            
            while !Task.isCancelled {
                // Check if MQTT is active (received message in last 10 seconds)
                let mqttWorking = await MainActor.run {
                    if let lastMsg = lastMqttMessage {
                        return Date().timeIntervalSince(lastMsg) < 10
                    }
                    return false
                }
                
                if mqttWorking {
                    // MQTT is working, poll less frequently (every 30s just to catch any missed)
                    await MainActor.run { isMqttActive = true }
                    try? await Task.sleep(nanoseconds: 30_000_000_000)
                } else {
                    // MQTT not working, poll more frequently
                    await MainActor.run { isMqttActive = false }
                    await fetchLogs()
                    try? await Task.sleep(nanoseconds: 2_000_000_000) // 2 seconds
                }
            }
        }
    }
}

// MARK: - Log Entry Model

struct DroneLogEntry: Identifiable {
    let id: String
    let timestamp: String
    let level: String
    let source: String
    let message: String
    let code: String?
    
    var date: Date {
        ISO8601DateFormatter().date(from: timestamp) ?? Date()
    }
    
    var levelColor: Color {
        switch level.uppercased() {
        case "ERROR": return .red
        case "WARNING": return .orange
        case "DEBUG": return .gray
        default: return .green
        }
    }
    
    var sourceIcon: String {
        switch source.lowercased() {
        case "daemon": return "gear"
        case "llm_code": return "sparkles"
        case "mavlink", "ardupilot", "px4": return "airplane"
        default: return "square.fill"
        }
    }
}

// MARK: - Log Entry View

struct LogEntryView: View {
    let log: DroneLogEntry
    @State private var isExpanded = false
    
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .top, spacing: 8) {
                // Timestamp
                Text(formattedTime)
                    .font(.system(size: 10, weight: .medium, design: .monospaced))
                    .foregroundColor(.gray)
                    .frame(width: 70, alignment: .leading)
                
                // Level indicator
                Circle()
                    .fill(log.levelColor)
                    .frame(width: 6, height: 6)
                    .padding(.top, 4)
                
                // Source icon
                Image(systemName: log.sourceIcon)
                    .font(.system(size: 10))
                    .foregroundColor(.cyan.opacity(0.8))
                    .frame(width: 14)
                
                // Message
                VStack(alignment: .leading, spacing: 2) {
                    Text(log.message)
                        .font(.system(size: 12, design: .monospaced))
                        .foregroundColor(.white.opacity(0.9))
                        .lineLimit(isExpanded ? nil : 3)
                    
                    // Source label
                    Text(log.source)
                        .font(.system(size: 9, weight: .medium))
                        .foregroundColor(.cyan.opacity(0.5))
                }
                
                Spacer()
            }
            
            // Code block if present
            if let code = log.code, isExpanded {
                Text(code)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundColor(.green.opacity(0.8))
                    .padding(8)
                    .background(Color.black.opacity(0.3))
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                    .padding(.leading, 84)
            }
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
        .onTapGesture {
            withAnimation(.easeInOut(duration: 0.2)) {
                isExpanded.toggle()
            }
        }
    }
    
    private var formattedTime: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss"
        return formatter.string(from: log.date)
    }
}

#Preview {
    NavigationStack {
        DroneDetailView(drone: Drone(
            userId: "user-123",
            droneId: "drone-001",
            name: "Scout",
            registeredAt: ISO8601DateFormatter().string(from: Date()),
            status: "active"
        ))
        .environment(APIClient.shared)
        .environment(MQTTService.shared)
    }
}


/// Pre-takeoff battery status: ready or not (and why), the pack voltage, any
/// battery-sensor faults the drone detected, and in flight the action budget.
struct BatteryReadinessView: View {
    let status: DroneStatus

    var body: some View {
        if status.preflight != nil || !(status.batteryWarnings ?? []).isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                if status.armed == true, let budget = status.batteryBudget {
                    row(icon: budget.verdict == "ok" ? "gauge.with.dots.needle.67percent" : "exclamationmark.triangle.fill",
                        color: budget.verdict == "ok" ? .green : .orange,
                        text: budget.verdict == "ok"
                            ? "About \(budget.actionsLeft ?? 0) more actions before returning home"
                            : (budget.reason ?? "Returning home for battery"))
                } else if let pre = status.preflight {
                    row(icon: pre.canTakeoff ? "checkmark.seal.fill" : "xmark.octagon.fill",
                        color: pre.canTakeoff ? .green : .red,
                        text: pre.canTakeoff
                            ? "Battery OK for takeoff"
                            : (pre.reason ?? "Not safe to take off"))
                }
                if let v = status.voltage {
                    Text(String(format: "Pack %.2f V", v) + (status.batterySource.map { " · from \($0.replacingOccurrences(of: "_", with: " "))" } ?? ""))
                        .font(.caption)
                        .foregroundColor(.gray)
                }
                ForEach(status.batteryWarnings ?? [], id: \.self) { warning in
                    Text(warning)
                        .font(.caption)
                        .foregroundColor(.orange)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 4)
        }
    }

    private func row(icon: String, color: Color, text: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: icon).foregroundColor(color)
            Text(text).font(.subheadline)
        }
    }
}
