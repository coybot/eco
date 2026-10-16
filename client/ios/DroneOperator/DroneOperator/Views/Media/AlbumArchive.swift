import Foundation
import ImageIO
import UIKit
import SwiftUI

/// Everything an operator needs to take a survey out of the app: the original
/// photos (untouched, so the GPS/time EXIF the aircraft wrote survives) plus a
/// `manifest.csv` listing where and when each was taken, zipped into one file
/// that the share sheet can send to Files, AirDrop, Mail or a drive.
///
/// Why not just share the photos: `ShareLink(item: url)` shares one presigned
/// link, and presigned links expire (24 h). A survey of 50 images has to leave
/// the app as files, as a set, with the positions beside them - EXIF alone is
/// stripped by most chat and mail apps.

/// One photo's capture metadata, read back from the EXIF in the file itself
/// (not from the server), so the manifest describes exactly what is in the zip.
struct PhotoCapture: Equatable {
    var file: String
    var utc: String?        // ISO-8601, UTC, millisecond resolution
    var latitude: Double?   // signed decimal degrees, WGS-84
    var longitude: Double?
    var altitudeMSL: Double?
    var heading: Double?    // degrees true
}

enum AlbumManifest {
    static let header = "n,file,utc_time,lat,lon,alt_msl_m,heading_deg"

    /// Capture metadata from a JPEG's bytes. Missing pieces stay nil: a photo
    /// taken with no GPS fix has a time and no position, and says so.
    static func capture(from data: Data, file: String) -> PhotoCapture {
        var out = PhotoCapture(file: file)
        guard let src = CGImageSourceCreateWithData(data as CFData, nil),
              let props = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [String: Any] else {
            return out
        }
        let exif = props[kCGImagePropertyExifDictionary as String] as? [String: Any]
        let gps = props[kCGImagePropertyGPSDictionary as String] as? [String: Any]

        // GPS date+time is UTC by definition; prefer it, else the EXIF original
        // time with its stated offset (the aircraft writes +00:00).
        let subsec = (exif?[kCGImagePropertyExifSubsecTimeOriginal as String] as? String)
            ?? (exif?[kCGImagePropertyExifSubsecTimeOriginal as String] as? Int).map(String.init)
        if let date = gps?[kCGImagePropertyGPSDateStamp as String] as? String,
           let time = gps?[kCGImagePropertyGPSTimeStamp as String] as? String {
            out.utc = isoUTC(exifDate: "\(date) \(time)", subsec: subsec)
        } else if let original = exif?[kCGImagePropertyExifDateTimeOriginal as String] as? String {
            out.utc = isoUTC(exifDate: original, subsec: subsec)
        }

        if let lat = gps?[kCGImagePropertyGPSLatitude as String] as? Double,
           let lon = gps?[kCGImagePropertyGPSLongitude as String] as? Double {
            let latRef = gps?[kCGImagePropertyGPSLatitudeRef as String] as? String
            let lonRef = gps?[kCGImagePropertyGPSLongitudeRef as String] as? String
            out.latitude = latRef == "S" ? -abs(lat) : abs(lat)
            out.longitude = lonRef == "W" ? -abs(lon) : abs(lon)
            if let alt = gps?[kCGImagePropertyGPSAltitude as String] as? Double {
                let below = (gps?[kCGImagePropertyGPSAltitudeRef as String] as? Int) == 1
                out.altitudeMSL = below ? -abs(alt) : abs(alt)
            }
            out.heading = gps?[kCGImagePropertyGPSImgDirection as String] as? Double
        }
        return out
    }

    /// "2026:10:14 17:34:30" (+ "563") -> "2026-10-14T17:34:30.563Z"
    static func isoUTC(exifDate: String, subsec: String?) -> String? {
        let parser = DateFormatter()
        parser.locale = Locale(identifier: "en_US_POSIX")
        parser.timeZone = TimeZone(identifier: "UTC")
        parser.dateFormat = "yyyy:MM:dd HH:mm:ss"
        // GPS TimeStamp can carry a fraction ("17:34:30.563"); the EXIF date
        // string never does. Keep whole seconds here, sub-second comes separately.
        let whole = String(exifDate.prefix(19))
        guard var date = parser.date(from: whole) else { return nil }
        if let subsec, let ms = Double("0." + subsec.filter(\.isNumber)) {
            date.addTimeInterval(ms)
        }
        let out = DateFormatter()
        out.locale = Locale(identifier: "en_US_POSIX")
        out.timeZone = TimeZone(identifier: "UTC")
        out.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'"
        return out.string(from: date)
    }

    static func csv(_ rows: [PhotoCapture]) -> String {
        func num(_ v: Double?, _ places: Int) -> String {
            v.map { String(format: "%.\(places)f", $0) } ?? ""
        }
        var lines = [header]
        for (i, r) in rows.enumerated() {
            let file = r.file.contains(",") ? "\"\(r.file)\"" : r.file
            lines.append([String(i + 1), file, r.utc ?? "", num(r.latitude, 7), num(r.longitude, 7),
                          num(r.altitudeMSL, 2), num(r.heading, 1)].joined(separator: ","))
        }
        return lines.joined(separator: "\n") + "\n"
    }
}

enum AlbumArchiveError: LocalizedError {
    case downloadsFailed(failed: Int, total: Int)
    case zipFailed

    var errorDescription: String? {
        switch self {
        case .downloadsFailed(let failed, let total):
            return "\(failed) of \(total) photos couldn't be downloaded. Their links may have expired - reopen the conversation and try again."
        case .zipFailed:
            return "Couldn't create the archive."
        }
    }
}

enum AlbumArchive {
    /// A file name for a downloaded photo: the S3 object's own name (it starts
    /// with a timestamp, so the folder sorts in capture order), minus the
    /// presigned query string, prefixed with its position in the album so two
    /// photos can never collide.
    static func fileName(for url: URL, index: Int) -> String {
        let name = url.lastPathComponent.isEmpty ? "photo.jpg" : url.lastPathComponent
        return String(format: "%03d_%@", index + 1, name)
    }

    /// Download `photos` at original quality (a few at a time), write them and a
    /// manifest.csv into a fresh folder, and zip it. Returns the zip's URL.
    /// All-or-nothing: a survey missing some of its photos is worse than an
    /// error, because nobody would notice.
    static func build(photos: [URL], now: Date = Date(),
                      session: URLSession = .shared) async throws -> URL {
        let stamp = DateFormatter()
        stamp.locale = Locale(identifier: "en_US_POSIX")
        stamp.timeZone = TimeZone(identifier: "UTC")
        stamp.dateFormat = "yyyyMMdd-HHmmss"
        let folderName = "coybot-survey-\(stamp.string(from: now))"

        let fm = FileManager.default
        let root = fm.temporaryDirectory.appendingPathComponent("album-export-\(UUID().uuidString)")
        let folder = root.appendingPathComponent(folderName)
        try fm.createDirectory(at: folder, withIntermediateDirectories: true)

        // Download with bounded concurrency; results are placed by index so the
        // manifest keeps the album's order whatever finishes first.
        var captures = [PhotoCapture?](repeating: nil, count: photos.count)
        var failed = 0
        try await withThrowingTaskGroup(of: (Int, PhotoCapture?).self) { group in
            var next = 0
            func launch() {
                guard next < photos.count else { return }
                let index = next, url = photos[index]
                next += 1
                group.addTask {
                    let name = fileName(for: url, index: index)
                    guard let (data, response) = try? await session.data(from: url),
                          (response as? HTTPURLResponse)?.statusCode == 200, !data.isEmpty else {
                        return (index, nil)
                    }
                    try data.write(to: folder.appendingPathComponent(name))
                    return (index, AlbumManifest.capture(from: data, file: name))
                }
            }
            for _ in 0..<min(6, photos.count) { launch() }
            while let (index, capture) = try await group.next() {
                if let capture { captures[index] = capture } else { failed += 1 }
                launch()
            }
        }
        if failed > 0 {
            try? fm.removeItem(at: root)
            throw AlbumArchiveError.downloadsFailed(failed: failed, total: photos.count)
        }

        let manifest = AlbumManifest.csv(captures.compactMap { $0 })
        try manifest.write(to: folder.appendingPathComponent("manifest.csv"), atomically: true, encoding: .utf8)

        // .forUploading makes the coordinator hand back a zip of the directory;
        // that file only exists inside the callback, so copy it out.
        let zip = root.appendingPathComponent("\(folderName).zip")
        var coordError: NSError?
        var copyError: Error?
        NSFileCoordinator().coordinate(readingItemAt: folder, options: .forUploading,
                                       error: &coordError) { zipped in
            do { try fm.copyItem(at: zipped, to: zip) } catch { copyError = error }
        }
        if coordError != nil || copyError != nil || !fm.fileExists(atPath: zip.path) {
            throw AlbumArchiveError.zipFailed
        }
        try? fm.removeItem(at: folder)
        return zip
    }
}

/// The system share sheet, for a file that already exists or a web link.
struct ShareSheet: UIViewControllerRepresentable {
    let fileURL: URL

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [fileURL], applicationActivities: nil)
    }

    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
