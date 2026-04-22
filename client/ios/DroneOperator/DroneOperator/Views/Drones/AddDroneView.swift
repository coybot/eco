import SwiftUI

struct AddDroneView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(APIClient.self) private var apiClient
    
    @State private var droneId = ""
    @State private var droneName = ""
    @State private var isRegistering = false
    @State private var error: Error?
    @State private var showScanner = false
    @State private var showSetupFlow = false
    @State private var showManualEntry = false
    
    let onAdd: (Drone) -> Void
    
    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.05, green: 0.05, blue: 0.1)
                    .ignoresSafeArea()
                
                if showManualEntry {
                    manualEntryView
                } else {
                    choiceView
                }
            }
            .navigationTitle("Add Drone")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Color(red: 0.05, green: 0.05, blue: 0.1), for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                    .foregroundColor(.white.opacity(0.7))
                }
            }
            .alert("Error", isPresented: .constant(error != nil)) {
                Button("OK") { error = nil }
            } message: {
                if let error {
                    Text(error.localizedDescription)
                }
            }
            .fullScreenCover(isPresented: $showSetupFlow) {
                SetupDroneView { drone in
                    onAdd(drone)
                    dismiss()
                }
            }
        }
    }
    
    // MARK: - Choice View
    
    private var choiceView: some View {
        ScrollView {
            VStack(spacing: 24) {
                // Illustration
                illustration
                
                Text("How would you like to add your drone?")
                    .font(.title3.bold())
                    .foregroundColor(.white)
                    .multilineTextAlignment(.center)
                
                // New drone (setup flow)
                OptionCard(
                    icon: "wifi",
                    title: "Set up new drone",
                    description: "Connect drone to your WiFi for the first time",
                    accentColor: .cyan
                ) {
                    showSetupFlow = true
                }
                
                // Already configured (manual entry)
                OptionCard(
                    icon: "checkmark.circle",
                    title: "Already configured",
                    description: "Add a drone that's already on your network",
                    accentColor: .green
                ) {
                    showManualEntry = true
                }
            }
            .padding(24)
        }
    }
    
    // MARK: - Manual Entry View
    
    private var manualEntryView: some View {
        ScrollView {
            VStack(spacing: 32) {
                // Back button
                HStack {
                    Button {
                        showManualEntry = false
                    } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "chevron.left")
                            Text("Back")
                        }
                        .foregroundColor(.cyan)
                    }
                    Spacer()
                }
                
                // Form
                form
            }
            .padding(24)
        }
    }
    
    // MARK: - Illustration
    
    private var illustration: some View {
        ZStack {
            // Background glow
            Circle()
                .fill(
                    RadialGradient(
                        colors: [.cyan.opacity(0.2), .clear],
                        center: .center,
                        startRadius: 30,
                        endRadius: 100
                    )
                )
                .frame(width: 200, height: 200)
            
            // Drone icon
            Image("DroneIcon")
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: 120, height: 120)
                .clipShape(RoundedRectangle(cornerRadius: 24))
            
            // Plus badge
            Image(systemName: "plus.circle.fill")
                .font(.system(size: 32))
                .foregroundStyle(.green)
                .background(Color(red: 0.05, green: 0.05, blue: 0.1))
                .clipShape(Circle())
                .offset(x: 40, y: 40)
        }
    }
    
    // MARK: - Form
    
    private var form: some View {
        VStack(spacing: 24) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Drone ID")
                    .font(.subheadline)
                    .foregroundColor(.white.opacity(0.7))
                
                HStack(spacing: 12) {
                    TextField("e.g., drone-001", text: $droneId)
                        .textFieldStyle(.plain)
                        .padding()
                        .background(Color.white.opacity(0.1))
                        .foregroundColor(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                        .autocapitalization(.none)
                        .autocorrectionDisabled()
                    
                    Button {
                        showScanner = true
                    } label: {
                        Image(systemName: "qrcode.viewfinder")
                            .font(.title2)
                            .frame(width: 50, height: 50)
                            .background(Color.white.opacity(0.1))
                            .foregroundColor(.cyan)
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                    }
                }
                
                Text("Find this on your drone's label or scan its QR code")
                    .font(.caption)
                    .foregroundColor(.white.opacity(0.4))
            }
            
            VStack(alignment: .leading, spacing: 8) {
                Text("Name (optional)")
                    .font(.subheadline)
                    .foregroundColor(.white.opacity(0.7))
                
                TextField("e.g., Backyard Drone", text: $droneName)
                    .textFieldStyle(.plain)
                    .padding()
                    .background(Color.white.opacity(0.1))
                    .foregroundColor(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            
            Button {
                registerDrone()
            } label: {
                HStack {
                    if isRegistering {
                        ProgressView()
                            .tint(.black)
                    } else {
                        Image(systemName: "plus.circle.fill")
                        Text("Add Drone")
                    }
                }
                .frame(maxWidth: .infinity)
                .frame(height: 54)
                .background(
                    LinearGradient(
                        colors: droneId.isEmpty ? [.gray] : [.cyan, .blue],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                )
                .foregroundColor(droneId.isEmpty ? .white.opacity(0.5) : .white)
                .font(.headline)
                .clipShape(RoundedRectangle(cornerRadius: 14))
            }
            .disabled(droneId.isEmpty || isRegistering)
            
            // Info card
            infoCard
        }
    }
    
    // MARK: - Info Card
    
    private var infoCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Image(systemName: "info.circle.fill")
                    .foregroundStyle(.cyan)
                Text("Before adding a drone")
                    .font(.subheadline.bold())
                    .foregroundColor(.white)
            }
            
            VStack(alignment: .leading, spacing: 8) {
                InfoRow(number: "1", text: "Power on your drone")
                InfoRow(number: "2", text: "Ensure it has internet connectivity")
                InfoRow(number: "3", text: "Wait for the status LED to turn green")
            }
        }
        .padding()
        .background(Color.cyan.opacity(0.1))
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.cyan.opacity(0.3), lineWidth: 1)
        )
    }
    
    // MARK: - Actions
    
    private func registerDrone() {
        isRegistering = true
        
        let name = droneName.isEmpty ? droneId : droneName
        
        Task {
            do {
                let drone = try await apiClient.registerDrone(droneId: droneId, name: name)
                await MainActor.run {
                    onAdd(drone)
                    dismiss()
                }
            } catch {
                await MainActor.run {
                    self.error = error
                    error.logAsUIError(context: "Registering drone '\(droneId)'")
                    isRegistering = false
                }
            }
        }
    }
}

struct InfoRow: View {
    let number: String
    let text: String
    
    var body: some View {
        HStack(spacing: 12) {
            Text(number)
                .font(.caption.bold())
                .frame(width: 20, height: 20)
                .background(Color.cyan.opacity(0.3))
                .foregroundColor(.cyan)
                .clipShape(Circle())
            
            Text(text)
                .font(.subheadline)
                .foregroundColor(.white.opacity(0.8))
        }
    }
}

struct OptionCard: View {
    let icon: String
    let title: String
    let description: String
    let accentColor: Color
    let action: () -> Void
    
    var body: some View {
        Button(action: action) {
            HStack(spacing: 16) {
                // Icon
                ZStack {
                    Circle()
                        .fill(accentColor.opacity(0.1))
                        .frame(width: 56, height: 56)
                    
                    Image(systemName: icon)
                        .font(.system(size: 24))
                        .foregroundStyle(accentColor)
                }
                
                // Text
                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(.headline)
                        .foregroundColor(.white)
                    
                    Text(description)
                        .font(.subheadline)
                        .foregroundColor(.white.opacity(0.6))
                        .multilineTextAlignment(.leading)
                }
                
                Spacer()
                
                Image(systemName: "chevron.right")
                    .foregroundColor(.white.opacity(0.3))
            }
            .padding()
            .background(Color.white.opacity(0.05))
            .clipShape(RoundedRectangle(cornerRadius: 16))
            .overlay(
                RoundedRectangle(cornerRadius: 16)
                    .stroke(Color.white.opacity(0.1), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    AddDroneView { _ in }
        .environment(APIClient.shared)
}

