import SwiftUI
import WebRTC

/// Live video stream view using Kinesis Video Streams WebRTC
struct VideoStreamView: View {
    let drone: Drone
    
    @Environment(APIClient.self) private var apiClient
    @State private var isStreaming = false
    @State private var isConnecting = false
    @State private var error: String?
    @State private var webRTCClient: KVSWebRTCClient?
    @State private var remoteVideoTrack: RTCVideoTrack?
    @State private var heartbeatTimer: Timer?
    
    private let mqttService = MQTTService.shared
    private let heartbeatInterval: TimeInterval = 5.0  // Send heartbeat every 5 seconds (drone timeout is 10s)
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Header
            HStack {
                Text("Live Camera")
                    .font(.headline)
                
                Spacer()
                
                // Status indicator
                HStack(spacing: 6) {
                    Circle()
                        .fill(statusColor)
                        .frame(width: 8, height: 8)
                    Text(statusText)
                        .font(.caption.bold())
                        .foregroundColor(statusColor)
                }
                
                // Play/Stop button
                Button {
                    print("[VideoStream] Button tapped! isStreaming=\(isStreaming)")
                    if isStreaming {
                        stopStream()
                    } else {
                        startStream()
                    }
                } label: {
                    if isConnecting {
                        ProgressView()
                            .scaleEffect(0.8)
                            .frame(width: 32, height: 32)
                    } else {
                        Image(systemName: isStreaming ? "stop.circle.fill" : "play.circle.fill")
                            .font(.title)
                            .foregroundColor(isStreaming ? .red : .green)
                            .frame(width: 32, height: 32)
                    }
                }
                .buttonStyle(.plain)
                .contentShape(Rectangle())
                .disabled(isConnecting)
            }
            
            // Video display area
            ZStack {
                RoundedRectangle(cornerRadius: 12)
                    .fill(Color.black)
                    .aspectRatio(16/9, contentMode: .fit)
                
                if let track = remoteVideoTrack {
                    RTCVideoView(videoTrack: track)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                } else if isConnecting {
                    VStack(spacing: 12) {
                        ProgressView()
                            .progressViewStyle(CircularProgressViewStyle(tint: .white))
                        Text("Connecting to drone...")
                            .font(.caption)
                            .foregroundColor(.gray)
                    }
                } else if isStreaming {
                    VStack(spacing: 8) {
                        Image(systemName: "video.badge.waveform")
                            .font(.largeTitle)
                            .foregroundColor(.gray)
                        Text("Waiting for video...")
                            .font(.caption)
                            .foregroundColor(.gray)
                    }
                } else {
                    VStack(spacing: 8) {
                        Image(systemName: "video.slash")
                            .font(.largeTitle)
                            .foregroundColor(.gray)
                        Text("Tap play to start stream")
                            .font(.caption)
                            .foregroundColor(.gray)
                    }
                }
                
                // Error overlay
                if let error = error {
                    VStack {
                        Spacer()
                        Text(error)
                            .font(.caption)
                            .foregroundColor(.white)
                            .padding(8)
                            .background(Color.red.opacity(0.8))
                            .clipShape(Capsule())
                            .padding(8)
                    }
                }
            }
        }
        .padding()
        .background(Color.white)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .onDisappear {
            stopStream()
        }
    }
    
    private var statusColor: Color {
        if isStreaming && remoteVideoTrack != nil {
            return .red
        } else if isStreaming || isConnecting {
            return .orange
        } else {
            return .gray
        }
    }
    
    private var statusText: String {
        if isStreaming && remoteVideoTrack != nil {
            return "LIVE"
        } else if isStreaming {
            return "WAITING"
        } else if isConnecting {
            return "CONNECTING"
        } else {
            return "OFF"
        }
    }
    
    private func startStream() {
        print("[VideoStream] startStream() called")
        guard !isConnecting && !isStreaming else {
            print("[VideoStream] Already connecting or streaming, returning")
            return
        }
        
        isConnecting = true
        error = nil
        print("[VideoStream] Starting stream for drone: \(drone.droneId)")
        
        Task {
            do {
                if GCSSettings.shared.isGCSMode {
                    sendVideoCommand(action: "start")
                } else {
                    // The cloud endpoint creates the KVS signaling channel before
                    // publishing the start command, so the viewer request cannot
                    // race channel creation on first use.
                    try await apiClient.startVideo(droneId: drone.droneId)
                }

                // Small delay to let drone start the video producer
                try await Task.sleep(nanoseconds: 1_500_000_000) // 1.5 seconds
                
                // Get viewer credentials and ICE servers from API
                print("[VideoStream] Fetching viewer credentials...")
                let viewer = try await apiClient.getVideoViewer(droneId: drone.droneId)
                print("[VideoStream] Got viewer info - channel: \(viewer.channelName)")
                
                // Initialize WebRTC client with ICE servers
                print("[VideoStream] ICE servers count: \(viewer.iceServers.count)")
                for (i, ice) in viewer.iceServers.enumerated() {
                    print("[VideoStream] ICE server \(i): \(ice.urls.first ?? "none")")
                }
                
                let iceServers = viewer.iceServers.map { ice in
                    RTCIceServer(
                        urlStrings: ice.urls,
                        username: ice.username,
                        credential: ice.credential
                    )
                }
                
                let client = KVSWebRTCClient(
                    signedWssUrl: viewer.signedWssUrl,
                    clientId: viewer.clientId,
                    iceServers: iceServers
                )
                
                // Set callback to receive video track
                client.onRemoteVideoTrack = { track in
                    print("[VideoStream] ✓ Received video track in callback, updating UI")
                    Task { @MainActor in
                        self.remoteVideoTrack = track
                        print("[VideoStream] ✓ remoteVideoTrack is now set: \(track)")
                    }
                }
                
                client.onError = { errorMessage in
                    Task { @MainActor in
                        self.error = errorMessage
                    }
                }
                
                client.onDisconnect = {
                    Task { @MainActor in
                        self.stopStream()
                    }
                }
                
                // Connect as viewer (will create offer and send to drone)
                try await client.connect()
                
                await MainActor.run {
                    webRTCClient = client
                    isStreaming = true
                    isConnecting = false
                    startHeartbeat()
                }
                
            } catch {
                print("[VideoStream] Error: \(error)")
                await MainActor.run {
                    self.error = error.localizedDescription
                    isConnecting = false
                    // Tell drone to stop since we failed to connect
                    sendVideoCommand(action: "stop")
                }
            }
        }
    }
    
    private func stopStream() {
        // Only log and act if we were actually streaming or connecting
        guard isStreaming || isConnecting || webRTCClient != nil else { return }
        
        print("[VideoStream] stopStream() called")
        
        // Stop heartbeat timer
        stopHeartbeat()
        
        // Disconnect WebRTC
        webRTCClient?.disconnect()
        webRTCClient = nil
        remoteVideoTrack = nil
        
        // Send stop command to drone
        if isStreaming || isConnecting {
            sendVideoCommand(action: "stop")
        }
        
        isStreaming = false
        isConnecting = false
        error = nil
    }
    
    // MARK: - MQTT Video Control
    
    private func sendVideoCommand(action: String, silent: Bool = false) {
        let topic = "drone/\(drone.droneId)/video/command"
        let payload: [String: Any] = [
            "action": action,
            "timestamp": ISO8601DateFormatter().string(from: Date())
        ]
        if !silent {
            print("[VideoStream] Sending MQTT command: \(action)")
        }
        mqttService.publish(to: topic, payload: payload, silent: silent)
    }
    
    private func startHeartbeat() {
        print("[VideoStream] Starting heartbeat timer (every \(heartbeatInterval)s)")
        // Send first heartbeat immediately
        sendVideoCommand(action: "heartbeat", silent: true)
        
        heartbeatTimer = Timer.scheduledTimer(withTimeInterval: heartbeatInterval, repeats: true) { _ in
            self.sendVideoCommand(action: "heartbeat", silent: true)
        }
    }
    
    private func stopHeartbeat() {
        heartbeatTimer?.invalidate()
        heartbeatTimer = nil
    }
}

// MARK: - WebRTC Video View (UIViewRepresentable)

struct RTCVideoView: UIViewRepresentable {
    let videoTrack: RTCVideoTrack
    
    func makeUIView(context: Context) -> RTCMTLVideoView {
        let view = RTCMTLVideoView(frame: .zero)
        view.videoContentMode = .scaleAspectFit
        videoTrack.add(view)
        return view
    }
    
    func updateUIView(_ uiView: RTCMTLVideoView, context: Context) {
        // Video track updates are handled automatically
    }
    
    static func dismantleUIView(_ uiView: RTCMTLVideoView, coordinator: ()) {
        // Clean up when view is removed
    }
}

// MARK: - KVS WebRTC Client

/// WebRTC client for Kinesis Video Streams signaling (viewer role)
class KVSWebRTCClient: NSObject {
    private let signedWssUrl: String
    private let clientId: String
    private let iceServers: [RTCIceServer]
    
    private var peerConnection: RTCPeerConnection?
    private var peerConnectionFactory: RTCPeerConnectionFactory?
    private var webSocket: URLSessionWebSocketTask?
    private var urlSession: URLSession?
    
    var onRemoteVideoTrack: ((RTCVideoTrack) -> Void)?
    var onError: ((String) -> Void)?
    var onDisconnect: (() -> Void)?
    
    init(signedWssUrl: String, clientId: String, iceServers: [RTCIceServer]) {
        self.signedWssUrl = signedWssUrl
        self.clientId = clientId
        self.iceServers = iceServers
        super.init()
    }
    
    func connect() async throws {
        // Initialize WebRTC factory (video only - no audio to avoid simulator crashes)
        let encoderFactory = RTCDefaultVideoEncoderFactory()
        let decoderFactory = RTCDefaultVideoDecoderFactory()
        peerConnectionFactory = RTCPeerConnectionFactory(
            encoderFactory: encoderFactory,
            decoderFactory: decoderFactory
        )
        
        guard let factory = peerConnectionFactory else {
            throw VideoStreamError.initializationFailed
        }
        
        // Configure peer connection
        let config = RTCConfiguration()
        config.iceServers = iceServers
        config.sdpSemantics = .unifiedPlan
        config.continualGatheringPolicy = .gatherContinually
        config.bundlePolicy = .maxBundle
        config.rtcpMuxPolicy = .require
        
        let constraints = RTCMediaConstraints(
            mandatoryConstraints: nil,
            optionalConstraints: ["DtlsSrtpKeyAgreement": "true"]
        )
        
        peerConnection = factory.peerConnection(with: config, constraints: constraints, delegate: self)
        
        guard let pc = peerConnection else {
            throw VideoStreamError.peerConnectionFailed
        }
        
        // Add transceiver for receiving video only (no audio to avoid simulator issues)
        let videoTransceiver = pc.addTransceiver(of: .video)
        videoTransceiver?.setDirection(.recvOnly, error: nil)
        
        // Connect to signaling WebSocket
        guard let url = URL(string: signedWssUrl) else {
            throw VideoStreamError.invalidUrl
        }
        
        urlSession = URLSession(configuration: .default)
        webSocket = urlSession?.webSocketTask(with: url)
        webSocket?.resume()
        
        // Start receiving signaling messages
        receiveSignalingMessages()
        
        // Create and send SDP offer (video only - no audio)
        let offerConstraints = RTCMediaConstraints(
            mandatoryConstraints: [
                "OfferToReceiveVideo": "true",
                "OfferToReceiveAudio": "false"
            ],
            optionalConstraints: nil
        )
        
        let offer = try await pc.offer(for: offerConstraints)
        try await pc.setLocalDescription(offer)
        
        // Send offer to drone via signaling
        sendSignalingMessage(type: "SDP_OFFER", payload: offer.sdp)
        
        print("[VideoStream] Sent SDP offer, waiting for answer from drone...")
    }
    
    private func receiveSignalingMessages() {
        webSocket?.receive { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let message):
                self.handleSignalingMessage(message)
                self.receiveSignalingMessages() // Continue receiving
            case .failure(let error):
                // Only report error if we're still supposed to be connected
                if self.webSocket != nil {
                    print("[VideoStream] WebSocket error: \(error)")
                    // Don't show socket closed errors to user - ICE failure is the real issue
                }
            }
        }
    }
    
    private func handleSignalingMessage(_ message: URLSessionWebSocketTask.Message) {
        guard case .string(let text) = message,
              let data = text.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return
        }
        
        let messageType = json["messageType"] as? String
        let payloadBase64 = json["messagePayload"] as? String
        
        print("[VideoStream] Received signaling message: \(messageType ?? "unknown")")
        
        switch messageType {
        case "SDP_ANSWER":
            // Drone responded with an SDP answer
            if let base64 = payloadBase64,
               let sdpData = Data(base64Encoded: base64),
               let sdpString = String(data: sdpData, encoding: .utf8) {
                
                let answer = RTCSessionDescription(type: .answer, sdp: sdpString)
                
                Task {
                    do {
                        try await self.peerConnection?.setRemoteDescription(answer)
                        print("[VideoStream] Set remote description (answer)")
                    } catch {
                        print("[VideoStream] Error setting remote description: \(error)")
                        self.onError?("Failed to set remote description")
                    }
                }
            }
            
        case "ICE_CANDIDATE":
            // ICE candidate from drone
            if let base64 = payloadBase64,
               let candidateData = Data(base64Encoded: base64),
               let candidateJson = try? JSONSerialization.jsonObject(with: candidateData) as? [String: Any],
               let candidate = candidateJson["candidate"] as? String,
               let sdpMid = candidateJson["sdpMid"] as? String,
               let sdpMLineIndex = candidateJson["sdpMLineIndex"] as? Int32 {
                
                let iceCandidate = RTCIceCandidate(sdp: candidate, sdpMLineIndex: sdpMLineIndex, sdpMid: sdpMid)
                peerConnection?.add(iceCandidate) { error in
                    if let error = error {
                        print("[VideoStream] Error adding ICE candidate: \(error)")
                    }
                }
            }
            
        default:
            break
        }
    }
    
    private func sendSignalingMessage(type: String, payload: String) {
        let payloadBase64 = Data(payload.utf8).base64EncodedString()
        
        let message: [String: Any] = [
            "action": type,
            "messagePayload": payloadBase64,
            "recipientClientId": "drone-master"
        ]
        
        if let data = try? JSONSerialization.data(withJSONObject: message),
           let text = String(data: data, encoding: .utf8) {
            webSocket?.send(.string(text)) { error in
                if let error = error {
                    print("[VideoStream] Error sending \(type): \(error)")
                }
            }
        }
    }
    
    private func sendIceCandidate(_ candidate: RTCIceCandidate) {
        let candidateDict: [String: Any] = [
            "candidate": candidate.sdp,
            "sdpMid": candidate.sdpMid ?? "",
            "sdpMLineIndex": candidate.sdpMLineIndex
        ]
        
        if let candidateData = try? JSONSerialization.data(withJSONObject: candidateDict) {
            let payloadBase64 = candidateData.base64EncodedString()
            
            let message: [String: Any] = [
                "action": "ICE_CANDIDATE",
                "messagePayload": payloadBase64,
                "recipientClientId": "drone-master"
            ]
            
            if let data = try? JSONSerialization.data(withJSONObject: message),
               let text = String(data: data, encoding: .utf8) {
                webSocket?.send(.string(text)) { error in
                    if let error = error {
                        print("[VideoStream] Error sending ICE candidate: \(error)")
                    }
                }
            }
        }
    }
    
    func disconnect() {
        webSocket?.cancel(with: .goingAway, reason: nil)
        webSocket = nil
        
        peerConnection?.close()
        peerConnection = nil
        
        peerConnectionFactory = nil
    }
}

// MARK: - RTCPeerConnectionDelegate

extension KVSWebRTCClient: RTCPeerConnectionDelegate {
    func peerConnection(_ peerConnection: RTCPeerConnection, didChange stateChanged: RTCSignalingState) {
        print("[VideoStream] Signaling state: \(stateChanged.rawValue)")
    }
    
    func peerConnection(_ peerConnection: RTCPeerConnection, didAdd stream: RTCMediaStream) {
        print("[VideoStream] Added stream with \(stream.videoTracks.count) video tracks")
        if let videoTrack = stream.videoTracks.first {
            print("[VideoStream] ✓ Got video track from stream, calling onRemoteVideoTrack")
            DispatchQueue.main.async {
                self.onRemoteVideoTrack?(videoTrack)
            }
        }
    }
    
    func peerConnection(_ peerConnection: RTCPeerConnection, didRemove stream: RTCMediaStream) {
        print("[VideoStream] Removed stream")
    }
    
    func peerConnectionShouldNegotiate(_ peerConnection: RTCPeerConnection) {
        print("[VideoStream] Should negotiate")
    }
    
    func peerConnection(_ peerConnection: RTCPeerConnection, didChange newState: RTCIceConnectionState) {
        let stateNames = ["new", "checking", "connected", "completed", "failed", "disconnected", "closed"]
        let stateName = newState.rawValue < stateNames.count ? stateNames[Int(newState.rawValue)] : "unknown"
        print("[VideoStream] ICE connection state: \(stateName) (\(newState.rawValue))")
        
        switch newState {
        case .connected, .completed:
            print("[VideoStream] ✓ ICE connected! Video should be flowing.")
        case .failed:
            print("[VideoStream] ✗ ICE failed - TURN servers may not be working")
            DispatchQueue.main.async {
                self.onError?("Connection failed - check network")
                self.onDisconnect?()
            }
        case .disconnected:
            DispatchQueue.main.async {
                self.onDisconnect?()
            }
        default:
            break
        }
    }
    
    func peerConnection(_ peerConnection: RTCPeerConnection, didChange newState: RTCIceGatheringState) {
        print("[VideoStream] ICE gathering state: \(newState.rawValue)")
    }
    
    func peerConnection(_ peerConnection: RTCPeerConnection, didGenerate candidate: RTCIceCandidate) {
        // Log the candidate type (host, srflx, relay)
        let candidateStr = candidate.sdp
        var candidateType = "unknown"
        if candidateStr.contains("typ host") {
            candidateType = "host"
        } else if candidateStr.contains("typ srflx") {
            candidateType = "srflx (STUN)"
        } else if candidateStr.contains("typ relay") {
            candidateType = "relay (TURN)"
        }
        print("[VideoStream] Generated ICE candidate: \(candidateType)")
        sendIceCandidate(candidate)
    }
    
    func peerConnection(_ peerConnection: RTCPeerConnection, didRemove candidates: [RTCIceCandidate]) {
        print("[VideoStream] Removed \(candidates.count) ICE candidates")
    }
    
    func peerConnection(_ peerConnection: RTCPeerConnection, didOpen dataChannel: RTCDataChannel) {
        print("[VideoStream] Data channel opened")
    }
    
    func peerConnection(_ peerConnection: RTCPeerConnection, didAdd rtpReceiver: RTCRtpReceiver, streams mediaStreams: [RTCMediaStream]) {
        print("[VideoStream] Added RTP receiver, track kind: \(rtpReceiver.track?.kind ?? "unknown")")
        if let videoTrack = rtpReceiver.track as? RTCVideoTrack {
            DispatchQueue.main.async {
                self.onRemoteVideoTrack?(videoTrack)
            }
        }
    }
    
    func peerConnection(_ peerConnection: RTCPeerConnection, didChange newState: RTCPeerConnectionState) {
        print("[VideoStream] Peer connection state: \(newState.rawValue)")
    }
}

// MARK: - Errors

enum VideoStreamError: LocalizedError {
    case initializationFailed
    case peerConnectionFailed
    case invalidUrl
    case signalingFailed
    
    var errorDescription: String? {
        switch self {
        case .initializationFailed:
            return "Failed to initialize WebRTC"
        case .peerConnectionFailed:
            return "Failed to create peer connection"
        case .invalidUrl:
            return "Invalid signaling URL"
        case .signalingFailed:
            return "Signaling connection failed"
        }
    }
}

#Preview {
    VideoStreamView(drone: Drone(
        userId: "user-123",
        droneId: "drone-001",
        name: "Scout",
        registeredAt: ISO8601DateFormatter().string(from: Date()),
        status: "active"
    ))
    .environment(APIClient.shared)
    .padding()
    .background(Color.gray.opacity(0.1))
}
