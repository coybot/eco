import SwiftUI

struct ConfigureTabView: View {
    @Binding var drone: Drone
    let isOnline: Bool
    
    @Environment(APIClient.self) private var apiClient
    
    @State private var pendingName: String = ""
    @State private var isSavingName = false
    @State private var nameError: String?
    
    @State private var wifiNetworks: [WifiNetwork] = []
    @State private var isLoadingWifi = false
    @State private var isSavingWifi = false
    @State private var wifiError: String?
    @State private var lastWifiNonce: String?
    
    @State private var showingAddWifi = false
    @State private var editingWifi: WifiNetwork?
    @State private var isEditingList = false
    
    // Battery config
    @State private var batteryConfig: BatteryConfig = .default
    @State private var isLoadingBattery = false
    @State private var isSavingBattery = false
    @State private var batteryError: String?
    @State private var lastBatteryNonce: String?
    
    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                renameCard
                wifiCard
                batteryCard
            }
            .padding()
        }
        .onAppear {
            if pendingName.isEmpty {
                pendingName = drone.name
            }
        }
        .task {
            await loadWifi()
        }
    }
    
    // MARK: - Rename
    
    private var renameCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Drone name")
                .font(.headline)
            
            HStack(spacing: 12) {
                TextField("Name", text: $pendingName)
                    .textFieldStyle(.roundedBorder)
                    .autocorrectionDisabled()
                
                Button {
                    saveName()
                } label: {
                    if isSavingName {
                        ProgressView()
                    } else {
                        Text("Save")
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(isSavingName || pendingName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || pendingName == drone.name)
            }
            
            if let nameError {
                Text(nameError)
                    .font(.caption)
                    .foregroundColor(.red)
            }
        }
        .padding()
        .background(Color.white)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
    
    private func saveName() {
        let trimmed = pendingName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        
        isSavingName = true
        nameError = nil
        
        Task {
            do {
                let updated = try await apiClient.patchDroneName(droneId: drone.droneId, name: trimmed)
                await MainActor.run {
                    drone = updated
                    pendingName = updated.name
                    isSavingName = false
                }
            } catch {
                await MainActor.run {
                    nameError = error.localizedDescription
                    isSavingName = false
                }
            }
        }
    }
    
    // MARK: - WiFi
    
    private var wifiCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Wi‑Fi networks")
                        .font(.headline)
                    Text(isOnline ? "Drone is online (updates should apply immediately)" : "Drone is offline (updates will apply when it’s next online)")
                        .font(.caption)
                        .foregroundColor(.gray)
                }
                Spacer()
                Button {
                    if isEditingList {
                        saveWifiAndExitEditMode()
                    } else {
                        isEditingList = true
                    }
                } label: {
                    if isSavingWifi {
                        ProgressView()
                    } else {
                        Text(isEditingList ? "Save" : "Edit")
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(isLoadingWifi || isSavingWifi)
            }
            
            if isLoadingWifi {
                HStack(spacing: 8) {
                    ProgressView()
                    Text("Loading Wi‑Fi…")
                        .font(.caption)
                        .foregroundColor(.gray)
                }
            }
            
            if !isLoadingWifi {
                wifiList
            }
            
            if let lastWifiNonce {
                Text("Pushed to drone (nonce \(lastWifiNonce.prefix(8))…).")
                    .font(.caption2)
                    .foregroundColor(.green)
            }
            
            if let wifiError {
                Text(wifiError)
                    .font(.caption)
                    .foregroundColor(.red)
            }
        }
        .padding()
        .background(Color.white)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .sheet(isPresented: $showingAddWifi) {
            WifiNetworkEditorView(
                title: "Add Wi‑Fi",
                initial: WifiNetwork(ssid: "", password: "", priority: wifiNetworks.count, enabled: true)
            ) { newNetwork in
                wifiNetworks.append(newNetwork)
                renumberPriorities()
            }
        }
        .sheet(item: $editingWifi) { net in
            WifiNetworkEditorView(title: "Edit Wi‑Fi", initial: net) { updated in
                if let idx = wifiNetworks.firstIndex(where: { $0.id == updated.id }) {
                    wifiNetworks[idx] = updated
                    renumberPriorities()
                }
            }
        }
    }
    
    private var wifiList: some View {
        // List inside a card; constrain height so it doesn't eat the whole ScrollView.
        List {
            if wifiNetworks.isEmpty && !isEditingList {
                Text("No networks configured yet.")
                    .font(.caption)
                    .foregroundColor(.gray)
            }

            ForEach(wifiNetworks.sorted(by: { $0.priority < $1.priority })) { net in
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(net.ssid.isEmpty ? "(no SSID)" : net.ssid)
                            .font(.body)
                        Text("Priority \(net.priority)")
                            .font(.caption2)
                            .foregroundColor(.gray)
                    }
                    Spacer()
                    Toggle("", isOn: Binding(
                        get: { net.enabled },
                        set: { newValue in
                            if let idx = wifiNetworks.firstIndex(where: { $0.id == net.id }) {
                                wifiNetworks[idx].enabled = newValue
                            }
                        }
                    ))
                    .labelsHidden()
                }
                .contentShape(Rectangle())
                .onTapGesture {
                    if isEditingList {
                        editingWifi = net
                    }
                }
            }
            .onDelete { offsets in
                guard isEditingList else { return }
                // Because we're displaying a sorted view, delete by mapping offsets to ids.
                let sorted = wifiNetworks.sorted(by: { $0.priority < $1.priority })
                let idsToDelete = offsets.map { sorted[$0].id }
                wifiNetworks.removeAll { idsToDelete.contains($0.id) }
                renumberPriorities()
            }
            .onMove { from, to in
                guard isEditingList else { return }
                // Move within the sorted list.
                var sorted = wifiNetworks.sorted(by: { $0.priority < $1.priority })
                sorted.move(fromOffsets: from, toOffset: to)
                for (i, item) in sorted.enumerated() {
                    if let idx = wifiNetworks.firstIndex(where: { $0.id == item.id }) {
                        wifiNetworks[idx].priority = i
                    }
                }
            }
            
            if isEditingList {
                Button {
                    showingAddWifi = true
                } label: {
                    Label("Add network", systemImage: "plus")
                }
            }
        }
        .frame(height: min(52 * CGFloat(max(wifiNetworks.count + (isEditingList ? 1 : 0), 1)) + 44, 360))
        .scrollContentBackground(.hidden)
        .environment(\.editMode, .constant(isEditingList ? .active : .inactive))
    }
    
    private func renumberPriorities() {
        let sorted = wifiNetworks.sorted(by: { $0.priority < $1.priority })
        for (i, item) in sorted.enumerated() {
            if let idx = wifiNetworks.firstIndex(where: { $0.id == item.id }) {
                wifiNetworks[idx].priority = i
            }
        }
    }
    
    private func loadWifi() async {
        isLoadingWifi = true
        wifiError = nil
        defer { isLoadingWifi = false }
        
        do {
            let response = try await apiClient.getDroneWifi(droneId: drone.droneId)
            await MainActor.run {
                wifiNetworks = response.networks
                renumberPriorities()
            }
        } catch {
            // If not implemented server-side yet / no config, treat as empty.
            await MainActor.run {
                wifiError = error.localizedDescription
                wifiNetworks = wifiNetworks // keep whatever user has locally
            }
        }
    }
    
    private func saveWifi() {
        isSavingWifi = true
        wifiError = nil
        lastWifiNonce = nil
        
        // Normalize priorities before sending.
        renumberPriorities()
        
        Task {
            do {
                let response = try await apiClient.putDroneWifi(droneId: drone.droneId, networks: wifiNetworks)
                await MainActor.run {
                    wifiNetworks = response.networks
                    renumberPriorities()
                    lastWifiNonce = response.nonce
                    isSavingWifi = false
                }
            } catch {
                await MainActor.run {
                    wifiError = error.localizedDescription
                    isSavingWifi = false
                }
            }
        }
    }
    
    private func saveWifiAndExitEditMode() {
        isSavingWifi = true
        wifiError = nil
        lastWifiNonce = nil
        
        renumberPriorities()
        
        Task {
            do {
                let response = try await apiClient.putDroneWifi(droneId: drone.droneId, networks: wifiNetworks)
                await MainActor.run {
                    wifiNetworks = response.networks
                    renumberPriorities()
                    lastWifiNonce = response.nonce
                    isSavingWifi = false
                    isEditingList = false
                }
            } catch {
                await MainActor.run {
                    wifiError = error.localizedDescription
                    isSavingWifi = false
                }
            }
        }
    }
    
    // MARK: - Battery Configuration
    
    private var batteryCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Battery")
                        .font(.headline)
                    Text("Configure battery parameters for accurate percentage display")
                        .font(.caption)
                        .foregroundColor(.gray)
                }
                Spacer()
            }
            
            if isLoadingBattery {
                HStack(spacing: 8) {
                    ProgressView()
                    Text("Loading battery config…")
                        .font(.caption)
                        .foregroundColor(.gray)
                }
            } else {
                VStack(spacing: 16) {
                    // Cell count
                    HStack {
                        Text("Cell count")
                        Spacer()
                        Picker("", selection: $batteryConfig.cellCount) {
                            ForEach(1...14, id: \.self) { count in
                                Text("\(count)S").tag(count)
                            }
                        }
                        .pickerStyle(.menu)
                    }
                    
                    // Capacity
                    HStack {
                        Text("Capacity (mAh)")
                        Spacer()
                        TextField("mAh", value: $batteryConfig.capacityMah, format: .number)
                            .textFieldStyle(.roundedBorder)
                            .keyboardType(.numberPad)
                            .frame(width: 100)
                            .multilineTextAlignment(.trailing)
                    }
                    
                    // Voltage range
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Cell voltage range")
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                        
                        HStack {
                            Text("Empty")
                            Spacer()
                            TextField("V", value: $batteryConfig.cellEmptyVoltage, format: .number.precision(.fractionLength(2)))
                                .textFieldStyle(.roundedBorder)
                                .keyboardType(.decimalPad)
                                .frame(width: 80)
                                .multilineTextAlignment(.trailing)
                            Text("V")
                                .foregroundColor(.secondary)
                        }
                        
                        HStack {
                            Text("Full")
                            Spacer()
                            TextField("V", value: $batteryConfig.cellFullVoltage, format: .number.precision(.fractionLength(2)))
                                .textFieldStyle(.roundedBorder)
                                .keyboardType(.decimalPad)
                                .frame(width: 80)
                                .multilineTextAlignment(.trailing)
                            Text("V")
                                .foregroundColor(.secondary)
                        }
                    }
                    
                    // Computed pack voltages
                    let emptyPack = batteryConfig.cellEmptyVoltage * Double(batteryConfig.cellCount)
                    let fullPack = batteryConfig.cellFullVoltage * Double(batteryConfig.cellCount)
                    Text("Pack range: \(emptyPack, specifier: "%.1f")V – \(fullPack, specifier: "%.1f")V")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    
                    // Save button
                    Button {
                        saveBatteryConfig()
                    } label: {
                        if isSavingBattery {
                            ProgressView()
                                .frame(maxWidth: .infinity)
                        } else {
                            Text("Save Battery Config")
                                .frame(maxWidth: .infinity)
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(isSavingBattery)
                }
            }
            
            if let lastBatteryNonce {
                Text("Pushed to drone (nonce \(lastBatteryNonce.prefix(8))…).")
                    .font(.caption2)
                    .foregroundColor(.green)
            }
            
            if let batteryError {
                Text(batteryError)
                    .font(.caption)
                    .foregroundColor(.red)
            }
        }
        .padding()
        .background(Color.white)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .task {
            await loadBatteryConfig()
        }
    }
    
    private func loadBatteryConfig() async {
        isLoadingBattery = true
        batteryError = nil
        defer { isLoadingBattery = false }
        
        do {
            let response = try await apiClient.getBatteryConfig(droneId: drone.droneId)
            await MainActor.run {
                batteryConfig = response.batteryConfig
            }
        } catch {
            await MainActor.run {
                // Use defaults if not configured
                batteryError = error.localizedDescription
            }
        }
    }
    
    private func saveBatteryConfig() {
        isSavingBattery = true
        batteryError = nil
        lastBatteryNonce = nil
        
        Task {
            do {
                let response = try await apiClient.putBatteryConfig(droneId: drone.droneId, config: batteryConfig)
                await MainActor.run {
                    batteryConfig = response.batteryConfig
                    lastBatteryNonce = response.nonce
                    isSavingBattery = false
                }
            } catch {
                await MainActor.run {
                    batteryError = error.localizedDescription
                    isSavingBattery = false
                }
            }
        }
    }
}

// MARK: - Editor

private struct WifiNetworkEditorView: View {
    let title: String
    let initial: WifiNetwork
    let onSave: (WifiNetwork) -> Void
    
    @Environment(\.dismiss) private var dismiss
    
    @State private var ssid: String
    @State private var password: String
    @State private var enabled: Bool
    @State private var priority: Int
    
    init(title: String, initial: WifiNetwork, onSave: @escaping (WifiNetwork) -> Void) {
        self.title = title
        self.initial = initial
        self.onSave = onSave
        _ssid = State(initialValue: initial.ssid)
        _password = State(initialValue: initial.password)
        _enabled = State(initialValue: initial.enabled)
        _priority = State(initialValue: initial.priority)
    }
    
    var body: some View {
        NavigationStack {
            Form {
                Section("Network") {
                    TextField("SSID", text: $ssid)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                    SecureField("Password", text: $password)
                    Toggle("Enabled", isOn: $enabled)
                }
                
                Section("Priority") {
                    Stepper("Priority: \(priority)", value: $priority, in: 0...50)
                    Text("Lower number = higher priority.")
                        .font(.caption)
                        .foregroundColor(.gray)
                }
            }
            .navigationTitle(title)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        let trimmedSsid = ssid.trimmingCharacters(in: .whitespacesAndNewlines)
                        let net = WifiNetwork(
                            id: initial.id,
                            ssid: trimmedSsid,
                            password: password,
                            priority: priority,
                            enabled: enabled
                        )
                        onSave(net)
                        dismiss()
                    }
                    .disabled(ssid.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
    }
}

