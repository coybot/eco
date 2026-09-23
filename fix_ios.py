import pathlib

# ---------- DroneSetupService: send the setup token ----------
p = pathlib.Path('client/ios/DroneOperator/DroneOperator/Services/DroneSetupService.swift')
s = p.read_text()

old = """    /// Send WiFi credentials to the drone
    func configureWiFi(ssid: String, password: String, userId: String) async throws -> String {"""
new = """    /// Send WiFi credentials to the drone
    ///
    /// `setupToken` is the drone's hotspot passphrase: the daemon uses the same
    /// secret for the AP and for authorizing /configure, so joining the network
    /// is not by itself proof that you are allowed to reconfigure the drone.
    func configureWiFi(ssid: String, password: String, userId: String, setupToken: String) async throws -> String {"""
assert old in s
s = s.replace(old, new)

old = """        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 10"""
new = """        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \\(setupToken)", forHTTPHeaderField: "Authorization")
        request.timeoutInterval = 10"""
assert old in s
s = s.replace(old, new)

old = """        guard httpResponse.statusCode == 200 else {
            if let errorJson = try? JSONSerialization.jsonObject(with: data) as? [String: Any],"""
new = """        guard httpResponse.statusCode != 401 else {
            throw SetupError.credentialsFailed("The drone rejected the setup passphrase. Check it against the passphrase printed on the drone.")
        }
        
        guard httpResponse.statusCode == 200 else {
            if let errorJson = try? JSONSerialization.jsonObject(with: data) as? [String: Any],"""
assert old in s
s = s.replace(old, new)
p.write_text(s)

# ---------- SetupDroneView: collect the passphrase, join the AP ----------
p = pathlib.Path('client/ios/DroneOperator/DroneOperator/Views/Drones/SetupDroneView.swift')
s = p.read_text()

old = """    @State private var wifiSSID = ""
    @State private var wifiPassword = ""
"""
new = """    @State private var wifiSSID = ""
    @State private var wifiPassword = ""
    @State private var hotspotSSID = ""
    @State private var hotspotPassphrase = ""
    @State private var showManualJoin = false
"""
assert old in s
s = s.replace(old, new)

start = s.index('    private var connectHotspotStep: some View {')
end = s.index('    // MARK: - Step: Enter WiFi Credentials')
new_step = '''    private var connectHotspotStep: some View {
        VStack(spacing: 32) {
            // Connection status
            ZStack {
                Circle()
                    .fill(setupService.isConnectedToDrone ? Color.green.opacity(0.1) : Color.cyan.opacity(0.1))
                    .frame(width: 160, height: 160)
                
                if setupService.isConnectedToDrone {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.system(size: 80))
                        .foregroundStyle(.green)
                } else {
                    Image(systemName: "wifi")
                        .font(.system(size: 80, weight: .light))
                        .foregroundStyle(.cyan)
                        .symbolEffect(.pulse)
                }
            }
            
            VStack(spacing: 16) {
                if setupService.isConnectedToDrone {
                    Text("Connected to drone!")
                        .font(.title2.bold())
                        .foregroundColor(.green)
                    
                    if let info = setupService.droneInfo {
                        Text("Drone ID: \\(info.droneId)")
                            .font(.subheadline)
                            .foregroundColor(.white.opacity(0.7))
                    }
                } else {
                    Text("Connect to drone's WiFi")
                        .font(.title2.bold())
                        .foregroundColor(.white)
                    
                    Text("Enter the setup network printed on your drone and this app will join it for you.")
                        .font(.body)
                        .foregroundColor(.white.opacity(0.7))
                        .multilineTextAlignment(.center)
                }
            }
            
            // The drone's AP is WPA2/WPA3 and its passphrase is generated on the
            // device, so the app cannot discover it - the operator reads it off the
            // airframe (the installer prints it at the end of setup). The same
            // secret authorizes /configure, so it is needed even when the phone
            // joined the network by hand.
            if !setupService.isConnectedToDrone {
                VStack(spacing: 20) {
                    hotspotField
                    passphraseField
                }
                
                primaryButton(isProcessing ? "Joining..." : "Join Drone WiFi",
                              disabled: hotspotSSID.isEmpty || hotspotPassphrase.isEmpty || isProcessing) {
                    joinDroneWiFi()
                }
                
                Button {
                    showManualJoin.toggle()
                } label: {
                    Text(showManualJoin ? "Hide manual steps" : "Join manually instead")
                        .font(.subheadline)
                        .foregroundColor(.cyan)
                }
                
                if showManualJoin {
                    VStack(alignment: .leading, spacing: 16) {
                        InstructionRow(number: "1", text: "Open iPhone Settings")
                        InstructionRow(number: "2", text: "Tap WiFi")
                        InstructionRow(number: "3", text: "Connect to the network above")
                        InstructionRow(number: "4", text: "Enter the setup passphrase")
                        InstructionRow(number: "5", text: "Return to this app")
                    }
                    .padding()
                    .background(Color.white.opacity(0.05))
                    .clipShape(RoundedRectangle(cornerRadius: 16))
                    
                    Button {
                        if let url = URL(string: "App-prefs:WIFI") {
                            UIApplication.shared.open(url)
                        }
                    } label: {
                        HStack {
                            Image(systemName: "gear")
                            Text("Open WiFi Settings")
                        }
                        .foregroundColor(.cyan)
                    }
                    
                    secondaryButton("I'm connected") {
                        setupService.checkDroneConnection()
                    }
                }
            } else if hotspotPassphrase.isEmpty {
                VStack(spacing: 20) {
                    passphraseField
                    
                    Text("The drone needs its setup passphrase before it will accept new WiFi credentials.")
                        .font(.footnote)
                        .foregroundColor(.white.opacity(0.6))
                        .multilineTextAlignment(.center)
                }
            }
            
            Spacer()
            
            if setupService.isConnectedToDrone {
                primaryButton("Continue", disabled: hotspotPassphrase.isEmpty) {
                    currentStep = .enterWiFi
                }
            }
        }
        .onChange(of: setupService.isConnectedToDrone) { _, isConnected in
            // Do not skip past the passphrase field if the phone was joined by hand.
            if isConnected && !hotspotPassphrase.isEmpty {
                // Small delay for UI feedback
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                    currentStep = .enterWiFi
                }
            }
        }
    }
    
    private var hotspotField: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Drone Network (SSID)")
                .font(.subheadline)
                .foregroundColor(.white.opacity(0.7))
            
            TextField("Coybot-...", text: $hotspotSSID)
                .textFieldStyle(.plain)
                .padding()
                .background(Color.white.opacity(0.1))
                .foregroundColor(.white)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .autocapitalization(.none)
                .autocorrectionDisabled()
        }
    }
    
    private var passphraseField: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Setup Passphrase")
                .font(.subheadline)
                .foregroundColor(.white.opacity(0.7))
            
            SecureField("Printed on the drone", text: $hotspotPassphrase)
                .textFieldStyle(.plain)
                .padding()
                .background(Color.white.opacity(0.1))
                .foregroundColor(.white)
                .clipShape(RoundedRectangle(cornerRadius: 12))
        }
    }
    
'''
s = s[:start] + new_step + s[end:]

old = """                let droneId = try await setupService.configureWiFi(
                    ssid: wifiSSID,
                    password: wifiPassword,
                    userId: userId
                )"""
new = """                let droneId = try await setupService.configureWiFi(
                    ssid: wifiSSID,
                    password: wifiPassword,
                    userId: userId,
                    setupToken: hotspotPassphrase
                )"""
assert old in s
s = s.replace(old, new)

old = """    // MARK: - Buttons
"""
new = """    private func joinDroneWiFi() {
        isProcessing = true
        AppLogger.logSetup("Joining drone access point", droneId: nil)
        
        Task {
            do {
                try await setupService.joinDroneAccessPoint(
                    ssid: hotspotSSID,
                    password: hotspotPassphrase
                )
                
                await MainActor.run {
                    isProcessing = false
                    setupService.checkDroneConnection()
                }
            } catch {
                await MainActor.run {
                    isProcessing = false
                    self.error = error
                    error.logAsUIError(context: "Joining drone access point '\\(hotspotSSID)'")
                }
            }
        }
    }
    
    // MARK: - Buttons
"""
assert old in s
s = s.replace(old, new, 1)
p.write_text(s)
print("ios ok")
