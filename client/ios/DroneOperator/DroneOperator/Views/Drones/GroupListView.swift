import SwiftUI

struct GroupListView: View {
    @Environment(APIClient.self) private var apiClient
    @Environment(MQTTService.self) private var mqttService

    @State private var groups: [DroneGroup] = []
    @State private var isLoading = false
    @State private var showingCreateGroup = false
    @State private var error: Error?

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.05, green: 0.05, blue: 0.1).ignoresSafeArea()

                if isLoading && groups.isEmpty {
                    ProgressView().tint(.cyan).scaleEffect(1.5)
                } else if groups.isEmpty {
                    emptyState
                } else {
                    groupList
                }
            }
            .navigationTitle("Fleets")
            .navigationBarTitleDisplayMode(.large)
            .toolbarBackground(Color(red: 0.05, green: 0.05, blue: 0.1), for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { showingCreateGroup = true } label: {
                        Image(systemName: "plus.circle.fill")
                            .font(.title2)
                            .foregroundStyle(.cyan)
                    }
                }
            }
            .sheet(isPresented: $showingCreateGroup) {
                CreateGroupView { newGroup in
                    groups.append(newGroup)
                }
            }
            .alert("Error", isPresented: .constant(error != nil)) {
                Button("OK") { error = nil }
            } message: {
                if let error { Text(error.localizedDescription) }
            }
            .task { await loadGroups() }
            .refreshable { await loadGroups() }
        }
    }

    // MARK: - Empty state

    private var emptyState: some View {
        VStack(spacing: 24) {
            ZStack {
                Circle()
                    .fill(Color.cyan.opacity(0.1))
                    .frame(width: 120, height: 120)
                Image(systemName: "rectangle.3.group")
                    .font(.system(size: 48))
                    .foregroundStyle(.cyan.opacity(0.8))
            }
            VStack(spacing: 8) {
                Text("No Fleets Yet")
                    .font(.title2.bold())
                    .foregroundColor(.white)
                Text("Create a fleet to command multiple drones at once")
                    .font(.subheadline)
                    .foregroundColor(.white.opacity(0.6))
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 40)
            }
            Button { showingCreateGroup = true } label: {
                Label("Create Fleet", systemImage: "plus")
                    .font(.headline)
                    .foregroundColor(.black)
                    .padding(.horizontal, 24)
                    .padding(.vertical, 12)
                    .background(.cyan)
                    .clipShape(Capsule())
            }
        }
    }

    // MARK: - Group list

    private var groupList: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ForEach(groups) { group in
                    NavigationLink(value: group) {
                        GroupCard(group: group)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding()
        }
        .navigationDestination(for: DroneGroup.self) { group in
            GroupChatView(group: group, conversationId: UUID().uuidString)
        }
    }

    // MARK: - Load

    private func loadGroups() async {
        isLoading = true
        defer { isLoading = false }
        do {
            groups = try await apiClient.listGroups()
        } catch {
            self.error = error
        }
    }
}

// MARK: - GroupCard

struct GroupCard: View {
    let group: DroneGroup

    var body: some View {
        HStack(spacing: 16) {
            ZStack {
                Circle()
                    .fill(Color.cyan.opacity(0.15))
                    .frame(width: 52, height: 52)
                Image(systemName: "rectangle.3.group")
                    .font(.system(size: 22))
                    .foregroundStyle(.cyan)
            }

            VStack(alignment: .leading, spacing: 4) {
                Text(group.name)
                    .font(.headline)
                    .foregroundColor(.white)
                Text("\(group.members.count) drone\(group.members.count == 1 ? "" : "s")")
                    .font(.caption)
                    .foregroundColor(.white.opacity(0.5))
            }

            Spacer()

            Image(systemName: "chevron.right")
                .font(.caption)
                .foregroundColor(.white.opacity(0.3))
        }
        .padding()
        .background(
            RoundedRectangle(cornerRadius: 16)
                .fill(Color.white.opacity(0.05))
                .overlay(
                    RoundedRectangle(cornerRadius: 16)
                        .stroke(Color.white.opacity(0.1), lineWidth: 1)
                )
        )
    }
}

// MARK: - CreateGroupView

struct CreateGroupView: View {
    let onCreate: (DroneGroup) -> Void

    @Environment(APIClient.self) private var apiClient
    @Environment(\.dismiss) private var dismiss

    @State private var groupName = ""
    @State private var availableDrones: [Drone] = []
    @State private var selectedDroneIds: Set<String> = []
    @State private var isLoading = false
    @State private var error: Error?

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.05, green: 0.05, blue: 0.1).ignoresSafeArea()

                Form {
                    Section("Fleet Name") {
                        TextField("e.g. Search Team Alpha", text: $groupName)
                            .foregroundColor(.white)
                    }
                    .listRowBackground(Color.white.opacity(0.07))

                    Section("Select Drones") {
                        if availableDrones.isEmpty && !isLoading {
                            Text("No drones available")
                                .foregroundColor(.white.opacity(0.5))
                        } else {
                            ForEach(availableDrones) { drone in
                                Button {
                                    if selectedDroneIds.contains(drone.droneId) {
                                        selectedDroneIds.remove(drone.droneId)
                                    } else {
                                        selectedDroneIds.insert(drone.droneId)
                                    }
                                } label: {
                                    HStack {
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(drone.name)
                                                .foregroundColor(.white)
                                            Text(drone.droneId)
                                                .font(.caption)
                                                .foregroundColor(.white.opacity(0.5))
                                        }
                                        Spacer()
                                        if selectedDroneIds.contains(drone.droneId) {
                                            Image(systemName: "checkmark.circle.fill")
                                                .foregroundStyle(.cyan)
                                        }
                                    }
                                }
                            }
                        }
                    }
                    .listRowBackground(Color.white.opacity(0.07))
                }
                .scrollContentBackground(.hidden)
            }
            .navigationTitle("New Fleet")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Color(red: 0.05, green: 0.05, blue: 0.1), for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }.foregroundStyle(.white.opacity(0.7))
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Create") { createGroup() }
                        .disabled(groupName.isEmpty || selectedDroneIds.isEmpty || isLoading)
                        .foregroundStyle(groupName.isEmpty || selectedDroneIds.isEmpty ? .gray : .cyan)
                }
            }
            .alert("Error", isPresented: .constant(error != nil)) {
                Button("OK") { error = nil }
            } message: {
                if let error { Text(error.localizedDescription) }
            }
            .task { await loadDrones() }
        }
    }

    private func loadDrones() async {
        do { availableDrones = try await apiClient.listDrones() }
        catch { self.error = error }
    }

    private func createGroup() {
        isLoading = true
        Task {
            do {
                let group = try await apiClient.createGroup(
                    name: groupName,
                    members: Array(selectedDroneIds)
                )
                await MainActor.run {
                    onCreate(group)
                    dismiss()
                }
            } catch {
                await MainActor.run {
                    self.error = error
                    isLoading = false
                }
            }
        }
    }
}
