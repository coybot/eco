import SwiftUI

/// Multi-step view for setting up a new drone via WiFi provisioning
struct SetupDroneView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(APIClient.self) private var apiClient
    @Environment(AuthService.self) private var authService
    
    @State private var setupService = DroneSetupService()
    @State private var currentStep: SetupStep = .intro
    @State private var wifiSSID = ""
    @State private var wifiPassword = ""
    @State private var droneName = ""
    @State private var isProcessing = false
    @State private var error: Error?
    
    let onComplete: (Drone) -> Void
    
    enum SetupStep {
        case intro
        case powerOn
        case connectHotspot
        case enterWiFi
        case configuring
        case complete
    }
    
    var body: some View {
        NavigationStack {
            ZStack {
                // Background
                Color(red: 0.05, green: 0.05, blue: 0.1)
                    .ignoresSafeArea()
                
                VStack(spacing: 0) {
                    // Progress indicator
                    progressBar
                    
                    // Content
                    ScrollView {
                        stepContent
                            .padding(24)
                    }
                }
            }
            .navigationTitle("Setup Drone")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Color(red: 0.05, green: 0.05, blue: 0.1), for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        setupService.stopMonitoring()
                        dismiss()
                    }
                    .foregroundColor(.white.opacity(0.7))
                }
            }
            .alert("Setup Error", isPresented: .constant(error != nil)) {
                Button("OK") { error = nil }
            } message: {
                if let error {
                    Text(error.localizedDescription)
                }
            }
        }
        .onDisappear {
            setupService.stopMonitoring()
        }
    }
    
    // MARK: - Progress Bar
    
    private var progressBar: some View {
        HStack(spacing: 4) {
            ForEach(0..<5) { index in
                Rectangle()
                    .fill(index <= stepIndex ? Color.cyan : Color.white.opacity(0.2))
                    .frame(height: 3)
            }
        }
        .padding(.horizontal, 24)
        .padding(.top, 8)
    }
    
    private var stepIndex: Int {
        switch currentStep {
        case .intro: return 0
        case .powerOn: return 1
        case .connectHotspot: return 2
        case .enterWiFi: return 3
        case .configuring: return 4
        case .complete: return 5
        }
    }
    
    // MARK: - Step Content
    
    @ViewBuilder
    private var stepContent: some View {
        switch currentStep {
        case .intro:
            introStep
        case .powerOn:
            powerOnStep
        case .connectHotspot:
            connectHotspotStep
        case .enterWiFi:
            enterWiFiStep
        case .configuring:
            configuringStep
        case .complete:
            completeStep
        }
    }
    
    // MARK: - Step: Intro
    
    private var introStep: some View {
        VStack(spacing: 32) {
            // Illustration
            ZStack {
                Circle()
                    .fill(
                        RadialGradient(
                            colors: [.cyan.opacity(0.3), .clear],
                            center: .center,
                            startRadius: 30,
                            endRadius: 120
                        )
                    )
                    .frame(width: 240, height: 240)
                
                Image("DroneIcon")
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: 140, height: 140)
                    .clipShape(RoundedRectangle(cornerRadius: 28))
            }
            
            VStack(spacing: 16) {
                Text("Let's set up your drone")
                    .font(.title.bold())
                    .foregroundColor(.white)
                
                Text("This will take about 2 minutes. Make sure you're near your drone and have your home WiFi password ready.")
                    .font(.body)
                    .foregroundColor(.white.opacity(0.7))
                    .multilineTextAlignment(.center)
            }
            
            // What you'll need
            VStack(alignment: .leading, spacing: 16) {
                Text("What you'll need:")
                    .font(.headline)
                    .foregroundColor(.white)
                
                RequirementRow(icon: "bolt.fill", text: "Your drone powered on")
                RequirementRow(icon: "wifi", text: "Your home WiFi password")
                RequirementRow(icon: "location.fill", text: "To be near the drone")
            }
            .padding()
            .background(Color.white.opacity(0.05))
            .clipShape(RoundedRectangle(cornerRadius: 16))
            
            Spacer()
            
            primaryButton("Get Started") {
                currentStep = .powerOn
            }
        }
    }
    
    // MARK: - Step: Power On
    
    private var powerOnStep: some View {
        VStack(spacing: 32) {
            // Animation
            ZStack {
                Circle()
                    .fill(Color.green.opacity(0.1))
                    .frame(width: 160, height: 160)
                
                Image(systemName: "power")
                    .font(.system(size: 80, weight: .light))
                    .foregroundStyle(.green)
            }
            
            VStack(spacing: 16) {
                Text("Power on your drone")
                    .font(.title2.bold())
                    .foregroundColor(.white)
                
                Text("Press the power button on your drone. Wait for it to boot up - this usually takes 30-60 seconds.")
                    .font(.body)
                    .foregroundColor(.white.opacity(0.7))
                    .multilineTextAlignment(.center)
            }
            
            // Timer hint
            HStack(spacing: 12) {
                Image(systemName: "clock")
                    .foregroundStyle(.cyan)
                Text("The drone creates a WiFi hotspot for 60 seconds after boot")
                    .font(.caption)
                    .foregroundColor(.white.opacity(0.6))
            }
            .padding()
            .background(Color.cyan.opacity(0.1))
            .clipShape(RoundedRectangle(cornerRadius: 12))
            
            Spacer()
            
            primaryButton("My drone is on") {
                currentStep = .connectHotspot
                setupService.startMonitoring()
            }
        }
    }
    
    // MARK: - Step: Connect to Hotspot
    
    private var connectHotspotStep: some View {
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
                        Text("Drone ID: \(info.droneId)")
                            .font(.subheadline)
                            .foregroundColor(.white.opacity(0.7))
                    }
                } else {
                    Text("Connect to drone's WiFi")
                        .font(.title2.bold())
                        .foregroundColor(.white)
                    
                    Text("Open Settings → WiFi and connect to the network starting with \"DroneSetup-\"")
                        .font(.body)
                        .foregroundColor(.white.opacity(0.7))
                        .multilineTextAlignment(.center)
                }
            }
            
            // Instructions card
            if !setupService.isConnectedToDrone {
                VStack(alignment: .leading, spacing: 16) {
                    InstructionRow(number: "1", text: "Open iPhone Settings")
                    InstructionRow(number: "2", text: "Tap WiFi")
                    InstructionRow(number: "3", text: "Connect to \"DroneSetup-XXXX\"")
                    InstructionRow(number: "4", text: "Return to this app")
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
            }
            
            Spacer()
            
            if setupService.isConnectedToDrone {
                primaryButton("Continue") {
                    currentStep = .enterWiFi
                }
            } else {
                secondaryButton("I'm connected") {
                    setupService.checkDroneConnection()
                }
            }
        }
        .onChange(of: setupService.isConnectedToDrone) { _, isConnected in
            if isConnected {
                // Small delay for UI feedback
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                    currentStep = .enterWiFi
                }
            }
        }
    }
    
    // MARK: - Step: Enter WiFi Credentials
    
    private var enterWiFiStep: some View {
        VStack(spacing: 32) {
            // Icon
            ZStack {
                Circle()
                    .fill(Color.cyan.opacity(0.1))
                    .frame(width: 120, height: 120)
                
                Image(systemName: "wifi.router.fill")
                    .font(.system(size: 50))
                    .foregroundStyle(.cyan)
            }
            
            VStack(spacing: 8) {
                Text("Enter your home WiFi")
                    .font(.title2.bold())
                    .foregroundColor(.white)
                
                Text("The drone will use this network to connect to the internet")
                    .font(.subheadline)
                    .foregroundColor(.white.opacity(0.6))
                    .multilineTextAlignment(.center)
            }
            
            // Form
            VStack(spacing: 20) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("WiFi Name (SSID)")
                        .font(.subheadline)
                        .foregroundColor(.white.opacity(0.7))
                    
                    TextField("Enter WiFi name", text: $wifiSSID)
                        .textFieldStyle(.plain)
                        .padding()
                        .background(Color.white.opacity(0.1))
                        .foregroundColor(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                        .autocapitalization(.none)
                        .autocorrectionDisabled()
                }
                
                VStack(alignment: .leading, spacing: 8) {
                    Text("Password")
                        .font(.subheadline)
                        .foregroundColor(.white.opacity(0.7))
                    
                    SecureField("Enter WiFi password", text: $wifiPassword)
                        .textFieldStyle(.plain)
                        .padding()
                        .background(Color.white.opacity(0.1))
                        .foregroundColor(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                }
                
                VStack(alignment: .leading, spacing: 8) {
                    Text("Drone Name (optional)")
                        .font(.subheadline)
                        .foregroundColor(.white.opacity(0.7))
                    
                    TextField("e.g., Backyard Drone", text: $droneName)
                        .textFieldStyle(.plain)
                        .padding()
                        .background(Color.white.opacity(0.1))
                        .foregroundColor(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                }
            }
            
            Spacer()
            
            primaryButton("Configure Drone", disabled: wifiSSID.isEmpty || wifiPassword.isEmpty) {
                configureDrone()
            }
        }
    }
    
    // MARK: - Step: Configuring
    
    private var configuringStep: some View {
        VStack(spacing: 32) {
            // Progress indicator
            ZStack {
                Circle()
                    .stroke(Color.white.opacity(0.1), lineWidth: 4)
                    .frame(width: 160, height: 160)
                
                Circle()
                    .trim(from: 0, to: progressForStep)
                    .stroke(Color.cyan, style: StrokeStyle(lineWidth: 4, lineCap: .round))
                    .frame(width: 160, height: 160)
                    .rotationEffect(.degrees(-90))
                    .animation(.easeInOut, value: setupService.setupProgress)
                
                VStack {
                    Image(systemName: iconForProgress)
                        .font(.system(size: 50))
                        .foregroundStyle(.cyan)
                    
                    Text(progressPercentage)
                        .font(.caption)
                        .foregroundColor(.white.opacity(0.6))
                }
            }
            
            VStack(spacing: 16) {
                Text(titleForProgress)
                    .font(.title2.bold())
                    .foregroundColor(.white)
                
                Text(subtitleForProgress)
                    .font(.body)
                    .foregroundColor(.white.opacity(0.7))
                    .multilineTextAlignment(.center)
            }
            
            // Progress steps
            VStack(alignment: .leading, spacing: 12) {
                ProgressStep(text: "Sending WiFi credentials", isComplete: setupService.setupProgress != .sendingCredentials, isCurrent: setupService.setupProgress == .sendingCredentials)
                ProgressStep(text: "Drone connecting to WiFi", isComplete: setupService.setupProgress == .registeringWithCloud || setupService.setupProgress == .completed, isCurrent: setupService.setupProgress == .waitingForDroneConnection)
                ProgressStep(text: "Registering with cloud", isComplete: setupService.setupProgress == .completed, isCurrent: setupService.setupProgress == .registeringWithCloud)
            }
            .padding()
            .background(Color.white.opacity(0.05))
            .clipShape(RoundedRectangle(cornerRadius: 16))
            
            Spacer()
        }
    }
    
    private var progressForStep: CGFloat {
        switch setupService.setupProgress {
        case .sendingCredentials: return 0.33
        case .waitingForDroneConnection: return 0.66
        case .registeringWithCloud: return 0.9
        case .completed: return 1.0
        default: return 0
        }
    }
    
    private var progressPercentage: String {
        "\(Int(progressForStep * 100))%"
    }
    
    private var iconForProgress: String {
        switch setupService.setupProgress {
        case .sendingCredentials: return "arrow.up.circle"
        case .waitingForDroneConnection: return "wifi"
        case .registeringWithCloud: return "cloud"
        case .completed: return "checkmark.circle"
        default: return "gear"
        }
    }
    
    private var titleForProgress: String {
        switch setupService.setupProgress {
        case .sendingCredentials: return "Sending credentials..."
        case .waitingForDroneConnection: return "Drone connecting..."
        case .registeringWithCloud: return "Almost done..."
        case .completed: return "Setup complete!"
        default: return "Setting up..."
        }
    }
    
    private var subtitleForProgress: String {
        switch setupService.setupProgress {
        case .sendingCredentials: return "Transmitting WiFi credentials to the drone"
        case .waitingForDroneConnection: return "Drone is connecting to your WiFi network"
        case .registeringWithCloud: return "Registering drone with your account"
        case .completed: return "Your drone is ready to use!"
        default: return ""
        }
    }
    
    // MARK: - Step: Complete
    
    private var completeStep: some View {
        VStack(spacing: 32) {
            // Success animation
            ZStack {
                Circle()
                    .fill(Color.green.opacity(0.1))
                    .frame(width: 160, height: 160)
                
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 80))
                    .foregroundStyle(.green)
            }
            
            VStack(spacing: 16) {
                Text("Drone added!")
                    .font(.title.bold())
                    .foregroundColor(.white)
                
                Text("Your drone is now connected and ready to receive commands.")
                    .font(.body)
                    .foregroundColor(.white.opacity(0.7))
                    .multilineTextAlignment(.center)
            }
            
            // Tip
            HStack(spacing: 12) {
                Image(systemName: "lightbulb.fill")
                    .foregroundStyle(.yellow)
                Text("Tip: Reconnect to your home WiFi to control your drone")
                    .font(.caption)
                    .foregroundColor(.white.opacity(0.6))
            }
            .padding()
            .background(Color.yellow.opacity(0.1))
            .clipShape(RoundedRectangle(cornerRadius: 12))
            
            Spacer()
            
            primaryButton("Done") {
                dismiss()
            }
        }
    }
    
    // MARK: - Actions
    
    private func configureDrone() {
        guard let userId = authService.currentUser?.id else {
            let notLoggedInError = DroneSetupService.SetupError.credentialsFailed("Not logged in")
            error = notLoggedInError
            notLoggedInError.logAsUIError(context: "Drone setup - user not logged in")
            return
        }
        
        currentStep = .configuring
        AppLogger.logSetup("Starting WiFi configuration", droneId: nil)
        
        Task {
            do {
                // Send WiFi credentials to drone
                let droneId = try await setupService.configureWiFi(
                    ssid: wifiSSID,
                    password: wifiPassword,
                    userId: userId
                )
                
                AppLogger.logSetup("Credentials sent, completing setup", droneId: droneId)
                
                // Wait for drone to connect and register
                let name = droneName.isEmpty ? droneId : droneName
                let drone = try await setupService.completeSetup(
                    droneId: droneId,
                    droneName: name,
                    apiClient: apiClient
                )
                
                AppLogger.logSetup("Setup completed successfully", droneId: droneId)
                
                await MainActor.run {
                    currentStep = .complete
                    onComplete(drone)
                }
                
            } catch {
                await MainActor.run {
                    self.error = error
                    error.logAsUIError(context: "Drone WiFi setup for SSID '\(wifiSSID)'")
                    currentStep = .enterWiFi
                }
            }
        }
    }
    
    // MARK: - Buttons
    
    private func primaryButton(_ title: String, disabled: Bool = false, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.headline)
                .frame(maxWidth: .infinity)
                .frame(height: 54)
                .background(
                    LinearGradient(
                        colors: disabled ? [.gray] : [.cyan, .blue],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                )
                .foregroundColor(disabled ? .white.opacity(0.5) : .white)
                .clipShape(RoundedRectangle(cornerRadius: 14))
        }
        .disabled(disabled)
    }
    
    private func secondaryButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.headline)
                .frame(maxWidth: .infinity)
                .frame(height: 54)
                .background(Color.white.opacity(0.1))
                .foregroundColor(.white)
                .clipShape(RoundedRectangle(cornerRadius: 14))
                .overlay(
                    RoundedRectangle(cornerRadius: 14)
                        .stroke(Color.white.opacity(0.2), lineWidth: 1)
                )
        }
    }
}

// MARK: - Supporting Views

struct RequirementRow: View {
    let icon: String
    let text: String
    
    var body: some View {
        HStack(spacing: 16) {
            Image(systemName: icon)
                .frame(width: 24)
                .foregroundStyle(.cyan)
            
            Text(text)
                .foregroundColor(.white.opacity(0.8))
        }
    }
}

struct InstructionRow: View {
    let number: String
    let text: String
    
    var body: some View {
        HStack(spacing: 16) {
            Text(number)
                .font(.caption.bold())
                .frame(width: 24, height: 24)
                .background(Color.cyan.opacity(0.3))
                .foregroundColor(.cyan)
                .clipShape(Circle())
            
            Text(text)
                .foregroundColor(.white.opacity(0.8))
        }
    }
}

struct ProgressStep: View {
    let text: String
    let isComplete: Bool
    let isCurrent: Bool
    
    var body: some View {
        HStack(spacing: 12) {
            ZStack {
                if isComplete {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                } else if isCurrent {
                    ProgressView()
                        .tint(.cyan)
                } else {
                    Circle()
                        .stroke(Color.white.opacity(0.3), lineWidth: 2)
                        .frame(width: 20, height: 20)
                }
            }
            .frame(width: 24, height: 24)
            
            Text(text)
                .foregroundColor(isComplete ? .white.opacity(0.5) : .white)
        }
    }
}

#Preview {
    SetupDroneView { _ in }
        .environment(APIClient.shared)
        .environment(AuthService.shared)
}

