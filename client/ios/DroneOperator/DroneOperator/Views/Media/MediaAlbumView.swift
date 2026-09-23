import SwiftUI
import AVKit

/// Full-screen, paged viewer for the media a mission captured.
///
/// Reached from a chat bubble: one captured file renders inline in the bubble
/// and opens here on tap; several render as an album card and open here at the
/// tapped item. Photos and clips share one pager deliberately — a mission that
/// took stills and a video produced one result, and splitting them into two
/// surfaces would make the operator reassemble it mentally.
struct MediaAlbumView: View {
    let items: [MessageContent.MediaItem]
    @State var selection: Int
    @Environment(\.dismiss) private var dismiss

    init(items: [MessageContent.MediaItem], initialIndex: Int = 0) {
        self.items = items
        _selection = State(initialValue: min(max(initialIndex, 0), max(items.count - 1, 0)))
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()

                TabView(selection: $selection) {
                    ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                        MediaPage(item: item)
                            .tag(index)
                    }
                }
                .tabViewStyle(.page(indexDisplayMode: items.count > 1 ? .automatic : .never))
                .indexViewStyle(.page(backgroundDisplayMode: .interactive))
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
                ToolbarItem(placement: .primaryAction) {
                    if items.indices.contains(selection) {
                        ShareLink(item: items[selection].url) {
                            Image(systemName: "square.and.arrow.up")
                        }
                    }
                }
            }
        }
        .accessibilityIdentifier("e2e_media_album_view")
    }

    private var title: String {
        guard items.count > 1 else {
            return items.first?.kind == .video ? "Recording" : "Photo"
        }
        return "\(selection + 1) of \(items.count)"
    }
}

/// One page: a zoomable still, or a player for a clip.
private struct MediaPage: View {
    let item: MessageContent.MediaItem

    var body: some View {
        switch item.kind {
        case .photo:
            AsyncImage(url: item.url) { phase in
                switch phase {
                case .success(let image):
                    image.resizable().aspectRatio(contentMode: .fit)
                case .failure:
                    // Presigned URLs expire; history re-signs on read, but a
                    // bubble left on screen past the expiry will land here.
                    // Say so rather than showing an empty frame.
                    MediaPlaceholder(systemImage: "photo", caption: "Couldn't load this photo")
                default:
                    ProgressView().tint(.white)
                }
            }
        case .video:
            // A fresh player per page; SwiftUI tears it down when the page
            // leaves the pager, which stops playback with it.
            VideoPlayer(player: AVPlayer(url: item.url))
        }
    }
}

private struct MediaPlaceholder: View {
    let systemImage: String
    let caption: String

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: systemImage).font(.largeTitle)
            Text(caption).font(.footnote)
        }
        .foregroundStyle(.secondary)
    }
}

/// The in-bubble presentation: one item inline, several as an album card.
///
/// This is the "a file, or a folder" split the operator asked for, and it is
/// decided by count alone — the server sends one ordered list precisely so the
/// client doesn't have to reconcile separate photo and video collections.
struct MediaAttachmentView: View {
    let items: [MessageContent.MediaItem]
    @State private var presented: MediaSelection?

    private let thumbSize: CGFloat = 78

    var body: some View {
        Group {
            if items.count == 1 {
                single(items[0])
            } else if items.count > 1 {
                album
            }
        }
        .sheet(item: $presented) { selection in
            MediaAlbumView(items: items, initialIndex: selection.id)
        }
    }

    @ViewBuilder
    private func single(_ item: MessageContent.MediaItem) -> some View {
        Button {
            presented = MediaSelection(id: 0)
        } label: {
            MediaThumbnail(item: item)
                .frame(maxWidth: 250, maxHeight: 200)
                .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("e2e_media_single")
        .accessibilityLabel(item.kind == .video ? "Recorded video" : "Photo")
    }

    private var album: some View {
        VStack(alignment: .leading, spacing: 6) {
            LazyVGrid(columns: [GridItem(.fixed(thumbSize), spacing: 4),
                                GridItem(.fixed(thumbSize), spacing: 4)],
                      spacing: 4) {
                ForEach(Array(items.prefix(4).enumerated()), id: \.offset) { index, item in
                    Button {
                        presented = MediaSelection(id: index)
                    } label: {
                        ZStack(alignment: .bottomTrailing) {
                            MediaThumbnail(item: item)
                                .frame(width: thumbSize, height: thumbSize)
                                .clipShape(RoundedRectangle(cornerRadius: 6))
                            // The fourth tile stands in for everything beyond
                            // it, so a 20-file mission still reads as one card.
                            if index == 3 && items.count > 4 {
                                RoundedRectangle(cornerRadius: 6)
                                    .fill(.black.opacity(0.55))
                                    .frame(width: thumbSize, height: thumbSize)
                                    .overlay(
                                        Text("+\(items.count - 3)")
                                            .font(.headline)
                                            .foregroundStyle(.white)
                                    )
                            }
                        }
                    }
                    .buttonStyle(.plain)
                }
            }

            Text(summary)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .accessibilityIdentifier("e2e_media_album")
        .accessibilityLabel(summary)
    }

    private var summary: String {
        let photos = items.filter { $0.kind == .photo }.count
        let videos = items.filter { $0.kind == .video }.count
        var parts: [String] = []
        if photos > 0 { parts.append("\(photos) photo\(photos == 1 ? "" : "s")") }
        if videos > 0 { parts.append("\(videos) video\(videos == 1 ? "" : "s")") }
        return parts.joined(separator: " · ")
    }
}

/// Thumbnail for either kind. Videos get the poster frame AVFoundation can
/// pull from the remote asset, with a play badge so a clip never reads as a
/// still that failed to load.
private struct MediaThumbnail: View {
    let item: MessageContent.MediaItem

    var body: some View {
        ZStack {
            switch item.kind {
            case .photo:
                AsyncImage(url: item.url) { image in
                    image.resizable().aspectRatio(contentMode: .fill)
                } placeholder: {
                    Rectangle().fill(Color.gray.opacity(0.2)).overlay(ProgressView())
                }
            case .video:
                VideoPosterView(url: item.url)
                Image(systemName: "play.circle.fill")
                    .font(.title2)
                    .foregroundStyle(.white)
                    .shadow(radius: 3)
            }
        }
    }
}

/// First-frame poster for a remote clip.
private struct VideoPosterView: View {
    let url: URL
    @State private var poster: CGImage?
    @State private var failed = false

    var body: some View {
        Group {
            if let poster {
                Image(decorative: poster, scale: 1.0)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
            } else {
                Rectangle()
                    .fill(Color.black.opacity(failed ? 0.6 : 0.25))
                    .overlay(failed ? nil : ProgressView().tint(.white))
            }
        }
        .task(id: url) { await loadPoster() }
    }

    private func loadPoster() async {
        let generator = AVAssetImageGenerator(asset: AVURLAsset(url: url))
        generator.appliesPreferredTrackTransform = true
        // Cap the decode: these are thumbnails in a chat bubble, and pulling a
        // 4K frame over the network for a 78pt tile is pure waste.
        generator.maximumSize = CGSize(width: 320, height: 320)
        do {
            let (image, _) = try await generator.image(at: .zero)
            poster = image
        } catch {
            failed = true
        }
    }
}

/// `.sheet(item:)` needs an Identifiable payload and Int isn't one. A wrapper
/// rather than `extension Int: Identifiable`: a retroactive conformance on a
/// stdlib type is visible to the whole module, not just this file, and would
/// silently change what `.sheet(item:)` accepts everywhere else in the app.
private struct MediaSelection: Identifiable {
    let id: Int
}
