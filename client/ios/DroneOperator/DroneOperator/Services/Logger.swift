import Foundation
import OSLog

/// Centralized logging with OSLog for debugging
enum AppLogger {
    
    private static let subsystem = Bundle.main.bundleIdentifier ?? "us.presidio.drone"
    
    // Category-specific loggers
    static let auth = Logger(subsystem: subsystem, category: "Auth")
    static let api = Logger(subsystem: subsystem, category: "API")
    static let ui = Logger(subsystem: subsystem, category: "UI")
    static let setup = Logger(subsystem: subsystem, category: "DroneSetup")
    static let mqtt = Logger(subsystem: subsystem, category: "MQTT")
    static let chat = Logger(subsystem: subsystem, category: "Chat")
    
    /// Log an error that's being displayed to the user
    static func logUIError(_ error: Error, context: String, file: String = #file, function: String = #function, line: Int = #line) {
        let fileName = (file as NSString).lastPathComponent
        let location = "\(fileName):\(line) \(function)"
        
        ui.error("""
            ❌ UI Error Displayed
            Context: \(context)
            Error: \(error.localizedDescription)
            Type: \(String(describing: type(of: error)))
            Location: \(location)
            Details: \(String(describing: error))
            """)
    }
    
    /// Log an API error with request details
    static func logAPIError(_ error: Error, endpoint: String, method: String = "GET") {
        api.error("""
            ❌ API Error
            Endpoint: \(method) \(endpoint)
            Error: \(error.localizedDescription)
            Type: \(String(describing: type(of: error)))
            Details: \(String(describing: error))
            """)
    }
    
    /// Log authentication events
    static func logAuthEvent(_ message: String, error: Error? = nil) {
        if let error = error {
            auth.error("🔐 Auth: \(message) - Error: \(error.localizedDescription)")
        } else {
            auth.info("🔐 Auth: \(message)")
        }
    }
    
    /// Log drone setup events
    static func logSetup(_ message: String, droneId: String? = nil, error: Error? = nil) {
        let droneInfo = droneId.map { " [Drone: \($0)]" } ?? ""
        if let error = error {
            setup.error("🚁 Setup\(droneInfo): \(message) - Error: \(error.localizedDescription)")
        } else {
            setup.info("🚁 Setup\(droneInfo): \(message)")
        }
    }
}

// MARK: - Convenience Extensions

extension Error {
    /// Log this error as a UI error and return self (for chaining)
    @discardableResult
    func logAsUIError(context: String, file: String = #file, function: String = #function, line: Int = #line) -> Error {
        AppLogger.logUIError(self, context: context, file: file, function: function, line: line)
        return self
    }
}

