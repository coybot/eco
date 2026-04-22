import SwiftUI

struct DroneListView: View {
    @Environment(APIClient.self) private var apiClient
    @Environment(MQTTService.self) private var mqttService
    
    @State private var drones: [Drone] = []
    @State private var isLoading = false
    @State private var showingAddDrone = false
    @State private var error: Error?
    @State private var statusPollTask: Task<Void, Never>?
    @State private var subscribedDroneIds: Set<String> = []
    
    var body: some View {
        NavigationStack {
            ZStack {
                // Background
                Color(red: 0.05, green: 0.05, blue: 0.1)
                    .ignoresSafeArea()
                
                if isLoading && drones.isEmpty {
                    ProgressView()
                        .tint(.cyan)
                        .scaleEffect(1.5)
                } else if drones.isEmpty {
                    emptyState
                } else {
                    droneGrid
                }
            }
            .navigationTitle("My Drones")
            .navigationBarTitleDisplayMode(.large)
            .toolbarBackground(Color(red: 0.05, green: 0.05, blue: 0.1), for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showingAddDrone = true
                    } label: {
                        Image(systemName: "plus.circle.fill")
                            .font(.title2)
                            .foregroundStyle(.cyan)
                    }
                }
            }
            .sheet(isPresented: $showingAddDrone) {
                AddDroneView { drone in
                    drones.append(drone)
                }
            }
            .alert("Error", isPresented: .constant(error != nil)) {
                Button("OK") { error = nil }
            } message: {
                if let error {
                    Text(error.localizedDescription)
                }
            }
            .task {
                await loadDrones()
            }
            .refreshable {
                await loadDrones()
            }
        }
    }
    
    // MARK: - Empty State
    
    private var emptyState: some View {
        VStack(spacing: 24) {
            ZStack {
                Circle()
                    .fill(Color.cyan.opacity(0.1))
                    .frame(width: 120, height: 120)
                
                Image("DroneIcon")
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: 60, height: 60)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            
            VStack(spacing: 8) {
                Text("No Drones Yet")
                    .font(.title2.bold())
                    .foregroundColor(.white)
                
                Text("Add your first drone to get started")
                    .font(.subheadline)
                    .foregroundColor(.white.opacity(0.6))
            }
            
            Button {
                showingAddDrone = true
            } label: {
                Label("Add Drone", systemImage: "plus")
                    .font(.headline)
                    .foregroundColor(.black)
                    .padding(.horizontal, 24)
                    .padding(.vertical, 12)
                    .background(.cyan)
                    .clipShape(Capsule())
            }
        }
    }
    
    // MARK: - Drone Grid
    
    private var droneGrid: some View {
        ScrollView {
            LazyVGrid(
                columns: [
                    GridItem(.flexible(), spacing: 16),
                    GridItem(.flexible(), spacing: 16)
                ],
                spacing: 16
            ) {
                ForEach(drones) { drone in
                    NavigationLink(value: drone) {
                        DroneCard(
                            drone: drone,
                            status: mqttService.droneStatuses[drone.droneId]
                        )
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding()
        }
        .navigationDestination(for: Drone.self) { drone in
            DroneDetailView(drone: drone) {
                // Remove from local list when deleted
                drones.removeAll { $0.droneId == drone.droneId }
            }
        }
    }
    
    // MARK: - Actions
    
    private func loadDrones() async {
        isLoading = true
        defer { isLoading = false }
        
        do {
            drones = try await apiClient.listDrones()
            
            // Start polling for status updates
            startStatusPolling()
            subscribeToStatusTopics(for: drones)
        } catch {
            self.error = error
            error.logAsUIError(context: "Loading drones list")
        }
    }
    
    private func startStatusPolling() {
        statusPollTask?.cancel()
        statusPollTask = Task {
            while !Task.isCancelled {
                await fetchAllStatuses()
                try? await Task.sleep(nanoseconds: 3_000_000_000) // Poll every 3 seconds
            }
        }
    }
    
    private func fetchAllStatuses() async {
        for drone in drones {
            if let response = try? await apiClient.getDroneStatus(droneId: drone.droneId) {
                await MainActor.run {
                    mqttService.updateDroneStatus(droneId: drone.droneId, status: response.status)
                }
            }
        }
    }

    private func subscribeToStatusTopics(for drones: [Drone]) {
        for drone in drones where !subscribedDroneIds.contains(drone.droneId) {
            subscribedDroneIds.insert(drone.droneId)
            let topic = "drone/\(drone.droneId)/status"
            mqttService.subscribe(to: topic) { payload in
                handleStatusUpdate(droneId: drone.droneId, payload: payload)
            }
        }
    }

    private func handleStatusUpdate(droneId: String, payload: Data) {
        do {
            let decoder = JSONDecoder()
            let newStatus = try decoder.decode(DroneStatus.self, from: payload)
            Task { @MainActor in
                mqttService.updateDroneStatus(droneId: droneId, status: newStatus)
            }
        } catch {
            AppLogger.mqtt.warning("Failed to decode MQTT status: \(error.localizedDescription)")
        }
    }
}

// MARK: - Drone Card

struct DroneCard: View {
    let drone: Drone
    let status: DroneStatus?
    
    @Environment(MQTTService.self) private var mqttService
    
    /// Use shared online check from MQTTService (single source of truth)
    private var isOnline: Bool {
        mqttService.isDroneOnline(droneId: drone.droneId)
    }
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Header
            HStack {
                Image("DroneIcon")
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: 32, height: 32)
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                
                Spacer()
                
                statusIndicator
            }
            
            // Name
            Text(drone.name)
                .font(.headline)
                .foregroundColor(.white)
                .lineLimit(1)
            
            // Drone ID
            Text(drone.droneId)
                .font(.caption)
                .foregroundColor(.white.opacity(0.5))
                .lineLimit(1)
            
            Spacer()
            
            // Telemetry preview
            if let status {
                HStack(spacing: 16) {
                    // Only show battery if it's a valid value (PX4 returns -1 when unknown)
                    if let battery = status.battery, battery >= 0 {
                        HStack(spacing: 4) {
                            Image(systemName: batteryIcon(battery))
                                .foregroundStyle(batteryColor(battery))
                            Text("\(Int(battery))%")
                                .font(.caption2)
                                .foregroundColor(.white.opacity(0.7))
                        }
                    }
                    
                    if let position = status.position {
                        HStack(spacing: 4) {
                            Image(systemName: "arrow.up")
                                .foregroundStyle(.white.opacity(0.5))
                            Text("\(Int(position.altitude))m")
                                .font(.caption2)
                                .foregroundColor(.white.opacity(0.7))
                        }
                    }
                }
            }
        }
        .padding()
        .frame(height: 160)
        .background(
            RoundedRectangle(cornerRadius: 16)
                .fill(Color.white.opacity(0.05))
                .overlay(
                    RoundedRectangle(cornerRadius: 16)
                        .stroke(Color.white.opacity(0.1), lineWidth: 1)
                )
        )
    }
    
    private var statusIndicator: some View {
        HStack(spacing: 4) {
            Circle()
                .fill(isOnline ? Color.green : Color.gray)
                .frame(width: 8, height: 8)
            
            Text(isOnline ? "Online" : "Offline")
                .font(.caption2)
                .foregroundColor(.white.opacity(0.6))
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(Color.white.opacity(0.1))
        .clipShape(Capsule())
    }
    
    private func batteryIcon(_ level: Double) -> String {
        switch level {
        case 75...: return "battery.100"
        case 50..<75: return "battery.75"
        case 25..<50: return "battery.50"
        default: return "battery.25"
        }
    }
    
    private func batteryColor(_ level: Double) -> Color {
        switch level {
        case 50...: return .green
        case 20..<50: return .yellow
        default: return .red
        }
    }
}

#Preview {
    DroneListView()
        .environment(APIClient.shared)
}

