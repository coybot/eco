import SwiftUI
import UIKit

/// Live MJPEG reader for one drone's camera feed from the local video bridge
/// (drone/sim/video_bridge.py). The bridge serves `multipart/x-mixed-replace`
/// with JPEG parts; we stream the body with URLSession and split it into frames
/// as they arrive, so there's no per-frame request overhead and the picture
/// updates as fast as the sim renders.
///
/// This is the GCS-mode (no-cloud) counterpart to VideoStreamView's KVS WebRTC
/// path — same idea (a live camera thumbnail), a transport the local demo can
/// actually serve.
@Observable
@MainActor
final class DroneVideoStream: NSObject, URLSessionDataDelegate {
    let droneId: String
    private let url: URL

    private(set) var frame: UIImage?
    private(set) var isConnected = false

    private var session: URLSession?
    private var task: URLSessionDataTask?
    private var buffer = Data()
    private var retryWork: DispatchWorkItem?
    private var stopped = false

    // JPEG SOI (FF D8) / EOI (FF D9) markers — we scan the multipart body for
    // whole JPEGs rather than parsing MIME boundaries, which is robust to
    // header-format differences between servers.
    private static let soi = Data([0xFF, 0xD8])
    private static let eoi = Data([0xFF, 0xD9])

    init?(droneId: String, baseURL: String) {
        guard let u = URL(string: "\(baseURL)/video/\(droneId)") else { return nil }
        self.droneId = droneId
        self.url = u
        super.init()
    }

    func start() {
        stopped = false
        connect()
    }

    func stop() {
        stopped = true
        retryWork?.cancel()
        task?.cancel()
        task = nil
        session?.invalidateAndCancel()
        session = nil
        isConnected = false
    }

    private func connect() {
        buffer.removeAll(keepingCapacity: true)
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 15
        config.timeoutIntervalForResource = .greatestFiniteMagnitude
        let session = URLSession(configuration: config, delegate: self,
                                 delegateQueue: nil)
        self.session = session
        let t = session.dataTask(with: url)
        self.task = t
        t.resume()
    }

    private func scheduleRetry() {
        guard !stopped else { return }
        isConnected = false
        let work = DispatchWorkItem { [weak self] in self?.connect() }
        retryWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 2, execute: work)
    }

    // MARK: URLSessionDataDelegate (called off the main actor)

    nonisolated func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                                didReceive data: Data) {
        // Parse on a background queue, hand finished frames to the main actor.
        Task { @MainActor [weak self] in self?.ingest(data) }
    }

    nonisolated func urlSession(_ session: URLSession, task: URLSessionTask,
                                didCompleteWithError error: Error?) {
        Task { @MainActor [weak self] in self?.scheduleRetry() }
    }

    private func ingest(_ data: Data) {
        isConnected = true
        buffer.append(data)
        // Extract every complete JPEG currently in the buffer; keep the tail.
        while let soi = buffer.range(of: Self.soi),
              let eoi = buffer.range(of: Self.eoi,
                                     in: soi.upperBound..<buffer.endIndex) {
            let jpeg = buffer.subdata(in: soi.lowerBound..<eoi.upperBound)
            if let img = UIImage(data: jpeg) {
                self.frame = img
            }
            buffer.removeSubrange(buffer.startIndex..<eoi.upperBound)
        }
        // Guard against unbounded growth if we somehow never find an EOI.
        if buffer.count > 4_000_000 {
            buffer.removeAll(keepingCapacity: true)
        }
    }
}
