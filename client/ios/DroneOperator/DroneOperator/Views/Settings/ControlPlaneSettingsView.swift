import SwiftUI

/// Lets the operator switch between AWS (cloud, the default) and a local
/// Ground Control Station (see eco/gcs/README.md) - a PC, Mac, or NVIDIA
/// Thor on the same network. Reachable from the Settings tab.
struct ControlPlaneSettingsView: View {
    @Environment(MQTTService.self) private var mqttService
    @Environment(AuthService.self) private var authService

    @State private var controlPlane: ControlPlane = GCSSettings.shared.controlPlane
    @State private var host: String = GCSSettings.shared.host
    @State private var httpPort: String = String(GCSSettings.shared.httpPort)
    @State private var mqttPort: String = String(GCSSettings.shared.mqttPort)
    @State private var pairingToken: String = GCSSettings.shared.pairingToken

    @State private var isTestingConnection = false
    @State private var testResult: TestResult?

    private enum TestResult {
        case success
        case failure
    }

    var body: some View {
        Form {
            Section {
                Picker("Control Plane", selection: $controlPlane) {
                    Text("Cloud (AWS)").tag(ControlPlane.cloud)
                    Text("Ground Control Station").tag(ControlPlane.gcs)
                }
                .pickerStyle(.segmented)
            } footer: {
                Text(controlPlane == .cloud
                     ? "Missions are orchestrated in AWS using Bedrock, as usual."
                     : "Missions are orchestrated locally by a Ground Control Station on your network, using its own models instead of Bedrock. No AWS account or internet connection needed.")
            }

            if controlPlane == .gcs {
                Section("Ground Control Station") {
                    TextField("Host or IP (e.g. 192.168.1.50)", text: $host)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)

                    TextField("HTTP port", text: $httpPort)
                        .keyboardType(.numberPad)

                    TextField("MQTT port", text: $mqttPort)
                        .keyboardType(.numberPad)

                    SecureField("Pairing token", text: $pairingToken)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }

                Section {
                    Button {
                        Task { await testConnection() }
                    } label: {
                        HStack {
                            Text("Test Connection")
                            Spacer()
                            if isTestingConnection {
                                ProgressView()
                            } else if let testResult {
                                Image(systemName: testResult == .success ? "checkmark.circle.fill" : "xmark.circle.fill")
                                    .foregroundStyle(testResult == .success ? .green : .red)
                            }
                        }
                    }
                    .disabled(host.isEmpty || httpPort.isEmpty || isTestingConnection)
                } footer: {
                    Text("Checks that the Ground Control Station's HTTP API is reachable at this host and port. This doesn't require a pairing token.")
                }
            }

            Section {
                Button("Save") {
                    save()
                }
                .disabled(controlPlane == .gcs && (host.isEmpty || httpPort.isEmpty || mqttPort.isEmpty))
            } footer: {
                Text("Saving reconnects MQTT with the new settings. The Ground Control Station prints its pairing token to the console on first run.")
            }
        }
        .navigationTitle("Ground Control Station")
    }

    private func testConnection() async {
        isTestingConnection = true
        testResult = nil
        defer { isTestingConnection = false }

        let port = Int(httpPort) ?? GCSSettings.shared.httpPort
        let reachable = await APIClient.shared.testGCSConnection(host: host, port: port)
        testResult = reachable ? .success : .failure
    }

    private func save() {
        let settings = GCSSettings.shared
        settings.controlPlane = controlPlane
        settings.host = host
        settings.httpPort = Int(httpPort) ?? settings.httpPort
        settings.mqttPort = Int(mqttPort) ?? settings.mqttPort
        settings.pairingToken = pairingToken

        Task {
            mqttService.disconnect()
            if settings.isGCSMode {
                try? await mqttService.connect(withToken: "")
            } else if let idToken = authService.idToken {
                // Switching back to cloud - reconnect with the real Cognito
                // token, the same way DroneOperatorApp's setupMQTT() does.
                try? await mqttService.connect(withToken: idToken)
            }
        }
    }
}

#Preview {
    NavigationStack {
        ControlPlaneSettingsView()
            .environment(MQTTService.shared)
    }
}
