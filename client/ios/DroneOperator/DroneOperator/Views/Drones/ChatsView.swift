import SwiftUI

// MARK: - Unified chat list item

enum ChatListItem: Identifiable {
    case drone(Drone)
    case group(DroneGroup)

    var id: String {
        switch self {
        case .drone(let d): return "drone-\(d.droneId)"
        case .group(let g): return "group-\(g.groupId)"
        }
    }

    var displayName: String {
        switch self {
        case .drone(let d): return d.name
        case .group(let g): return g.name
        }
    }

    var subtitle: String {
        switch self {
        case .drone(let d): return d.droneId
        case .group(let g): return "\(g.members.count) drone\(g.members.count == 1 ? "" : "s")"
        }
    }

    var isGroup: Bool {
        if case .group = self { return true }
        return false
    }
}

// MARK: - ChatsView

struct ChatsView: View {
    @Environment(APIClient.self) private var apiClient
    @Environment(MQTTService.self) private var mqttService

    @State private var drones: [Drone] = []
    @State private var groups: [DroneGroup] = []
    @State private var isLoading = false
    @State private var showingAddDrone = false
    @State private var showingCreateGroup = false
    @State private var error: Error?
    @State private var subscribedDroneIds: Set<String> = []
    // Group rename
    @State private var renameTarget: DroneGroup?
    @State private var renameText = ""

    private var items: [ChatListItem] {
        let d = drones.map { ChatListItem.drone($0) }
        let g = groups.map { ChatListItem.group($0) }
        return g + d   // groups first, like Slack channels before DMs
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.05, green: 0.05, blue: 0.1).ignoresSafeArea()

                if isLoading && items.isEmpty {
                    ProgressView().tint(.cyan).scaleEffect(1.5)
                } else if items.isEmpty {
                    emptyState
                } else {
                    chatList
                }
            }
            .navigationTitle("Missions")
            .navigationBarTitleDisplayMode(.large)
            .toolbarBackground(Color(red: 0.05, green: 0.05, blue: 0.1), for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar { toolbarItems }
            .sheet(isPresented: $showingAddDrone) {
                AddDroneView { drone in drones.append(drone) }
            }
            .sheet(isPresented: $showingCreateGroup) {
                CreateGroupView { newGroup in groups.append(newGroup) }
            }
            .alert("Rename fleet", isPresented: .constant(renameTarget != nil)) {
                TextField("Fleet name", text: $renameText)
                Button("Save") { saveRename() }
                Button("Cancel", role: .cancel) { renameTarget = nil }
            }
            .alert("Error", isPresented: .constant(error != nil)) {
                Button("OK") { error = nil }
            } message: {
                if let error { Text(error.localizedDescription) }
            }
            .task { await loadAll() }
            .refreshable { await loadAll() }
        }
    }

    // MARK: - Toolbar

    @ToolbarContentBuilder
    private var toolbarItems: some ToolbarContent {
        ToolbarItem(placement: .primaryAction) {
            Button {
                showingAddDrone = true
            } label: {
                Image(systemName: "plus.circle")
                    .font(.title2)
                    .foregroundStyle(.cyan)
            }
        }
    }

    // MARK: - Empty state

    private var emptyState: some View {
        VStack(spacing: 24) {
            ZStack {
                Circle()
                    .fill(Color.cyan.opacity(0.1))
                    .frame(width: 120, height: 120)
                Image(systemName: "message")
                    .font(.system(size: 48))
                    .foregroundStyle(.cyan.opacity(0.8))
            }
            VStack(spacing: 8) {
                Text("No missions yet")
                    .font(.title2.bold())
                    .foregroundColor(.white)
                Text("Onboard a drone to get started")
                    .font(.subheadline)
                    .foregroundColor(.white.opacity(0.6))
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 40)
            }
            Button { showingAddDrone = true } label: {
                Label("Onboard new drone", systemImage: "plus")
                    .font(.headline)
                    .foregroundColor(.black)
                    .padding(.horizontal, 20)
                    .padding(.vertical, 12)
                    .background(.cyan)
                    .clipShape(Capsule())
            }
        }
    }

    // MARK: - Chat list

    private var chatList: some View {
        List {
            ForEach(items) { item in
                chatRow(item)
                    .listRowBackground(Color.white.opacity(0.04))
                    .listRowSeparatorTint(Color.white.opacity(0.08))
                    .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                        swipeActions(for: item)
                    }
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .navigationDestination(for: Drone.self) { drone in
            DroneDetailView(drone: drone) {
                drones.removeAll { $0.droneId == drone.droneId }
            }
        }
        .navigationDestination(for: DroneGroup.self) { group in
            GroupChatView(group: group, conversationId: UUID().uuidString)
        }
    }

    @ViewBuilder
    private func chatRow(_ item: ChatListItem) -> some View {
        switch item {
        case .drone(let drone):
            NavigationLink(value: drone) {
                ChatRow(
                    icon: AnyView(DroneRowIcon(isOnline: mqttService.isDroneOnline(droneId: drone.droneId))),
                    name: drone.name,
                    subtitle: drone.droneId,
                    badge: mqttService.isDroneOnline(droneId: drone.droneId) ? "Online" : nil,
                    badgeColor: .green
                )
            }
            .buttonStyle(.plain)
        case .group(let group):
            NavigationLink(value: group) {
                ChatRow(
                    icon: AnyView(FleetRowIcon(memberCount: group.members.count)),
                    name: group.name,
                    subtitle: "\(group.members.count) drone\(group.members.count == 1 ? "" : "s")",
                    badge: nil,
                    badgeColor: .cyan
                )
            }
            .buttonStyle(.plain)
        }
    }

    @ViewBuilder
    private func swipeActions(for item: ChatListItem) -> some View {
        switch item {
        case .drone(let drone):
            Button(role: .destructive) {
                Task { try? await apiClient.deleteDrone(droneId: drone.droneId) }
                drones.removeAll { $0.droneId == drone.droneId }
            } label: {
                Label("Remove", systemImage: "trash")
            }
        case .group(let group):
            Button(role: .destructive) {
                Task { try? await apiClient.deleteGroup(groupId: group.groupId) }
                groups.removeAll { $0.groupId == group.groupId }
            } label: {
                Label("Delete", systemImage: "trash")
            }
            Button {
                renameText = group.name
                renameTarget = group
            } label: {
                Label("Rename", systemImage: "pencil")
            }
            .tint(.orange)
        }
    }

    // MARK: - Load

    private func loadAll() async {
        isLoading = true
        defer { isLoading = false }
        async let dronesResult = try? apiClient.listDrones()
        async let groupsResult = try? apiClient.listGroups()
        let (d, g) = await (dronesResult, groupsResult)
        drones = d ?? []
        groups = g ?? []
        subscribeStatusTopics(for: drones)
    }

    private func subscribeStatusTopics(for drones: [Drone]) {
        for drone in drones where !subscribedDroneIds.contains(drone.droneId) {
            subscribedDroneIds.insert(drone.droneId)
            mqttService.subscribe(to: "drone/\(drone.droneId)/status") { payload in
                guard let status = try? JSONDecoder().decode(DroneStatus.self, from: payload) else { return }
                Task { @MainActor in mqttService.updateDroneStatus(droneId: drone.droneId, status: status) }
            }
        }
    }

    // MARK: - Rename

    private func saveRename() {
        guard let group = renameTarget, !renameText.trimmingCharacters(in: .whitespaces).isEmpty else {
            renameTarget = nil; return
        }
        let newName = renameText.trimmingCharacters(in: .whitespaces)
        Task {
            // Optimistic update
            await MainActor.run {
                if let idx = groups.firstIndex(where: { $0.groupId == group.groupId }) {
                    groups[idx].name = newName
                }
                renameTarget = nil
            }
            _ = try? await apiClient.renameGroup(groupId: group.groupId, name: newName, members: group.members)
        }
    }
}

// MARK: - Row subviews

struct ChatRow: View {
    let icon: AnyView
    let name: String
    let subtitle: String
    let badge: String?
    let badgeColor: Color

    var body: some View {
        HStack(spacing: 14) {
            icon
            VStack(alignment: .leading, spacing: 3) {
                Text(name)
                    .font(.headline)
                    .foregroundColor(.white)
                    .lineLimit(1)
                Text(subtitle)
                    .font(.caption)
                    .foregroundColor(.white.opacity(0.45))
                    .lineLimit(1)
            }
            Spacer()
            if let badge {
                Text(badge)
                    .font(.caption2.weight(.medium))
                    .foregroundColor(badgeColor)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(badgeColor.opacity(0.15))
                    .clipShape(Capsule())
            }
        }
        .padding(.vertical, 6)
    }
}

struct DroneRowIcon: View {
    let isOnline: Bool
    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            RoundedRectangle(cornerRadius: 12)
                .fill(Color.white.opacity(0.08))
                .frame(width: 48, height: 48)
                .overlay(
                    Image("DroneIcon")
                        .resizable().scaledToFit().frame(width: 28, height: 28)
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                )
            Circle()
                .fill(isOnline ? Color.green : Color.gray)
                .frame(width: 10, height: 10)
                .overlay(Circle().stroke(Color(red: 0.05, green: 0.05, blue: 0.1), lineWidth: 2))
        }
    }
}

struct FleetRowIcon: View {
    let memberCount: Int
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 12)
                .fill(Color.cyan.opacity(0.12))
                .frame(width: 48, height: 48)
            Image(systemName: "rectangle.3.group.fill")
                .font(.system(size: 22))
                .foregroundStyle(.cyan)
        }
    }
}
