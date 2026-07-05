import Foundation
import OSLog

/// Centralized logging with OSLog — forked from DroneOperator's Logger.swift, categories
/// trimmed to what RoverOperator actually has.
enum AppLogger {
    private static let subsystem = Bundle.main.bundleIdentifier ?? "us.astral.rover"

    static let auth = Logger(subsystem: subsystem, category: "Auth")
    static let nav = Logger(subsystem: subsystem, category: "Nav")
    static let voice = Logger(subsystem: subsystem, category: "Voice")
    static let ui = Logger(subsystem: subsystem, category: "UI")
}
