import XCTest

/// UI tests for Rover Operator (phrover). See eco/e2e/README.md for the full picture.
///
/// The manual-teleop Drive tab is the ARKit-independent path: DriveView's D-pad calls
/// RoverControl.send() straight from the drag gesture, without waiting on ar.pose, so
/// it works in the iOS Simulator (which has no real camera/motion for ARKit world
/// tracking) — unlike NavigateView's autonomy loop, which needs a real device.
@MainActor
final class RoverOperatorUITests: XCTestCase {

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testLaunch_showsAuthOrTabs() throws {
        let app = XCUIApplication()
        app.launch()

        let authHero = app.staticTexts["Rover Operator"]
        let driveTab = app.buttons["Drive"]

        XCTAssertTrue(
            authHero.waitForExistence(timeout: 15) || driveTab.waitForExistence(timeout: 15),
            "Expected sign-in hero (Rover Operator) or the Drive tab."
        )
    }

    /// Gated integration path: drive forward on the D-pad and confirm the command
    /// actually reached the (mocked) WAVE ROVER base — proves the phone->ESP32 control
    /// loop end-to-end with no chassis needed.
    ///
    /// Env: RUN_HEZARFEN_E2E=1, E2E_EMAIL, E2E_PASSWORD, E2E_ROVER_HOST (host:port of a
    /// running eco/e2e/harness/mock_esp32.py — started by eco/e2e/run_phone.sh).
    func testDrive_forwardCommand_reachesMockChassis() async throws {
        guard ProcessInfo.processInfo.environment["RUN_HEZARFEN_E2E"] == "1" else {
            throw XCTSkip("Set RUN_HEZARFEN_E2E=1 to enable integration tests.")
        }
        guard let mockHost = ProcessInfo.processInfo.environment["E2E_ROVER_HOST"],
              !mockHost.isEmpty else {
            throw XCTSkip("Set E2E_ROVER_HOST to a running mock_esp32.py (host:port).")
        }
        let env = ProcessInfo.processInfo.environment

        let app = XCUIApplication()
        app.launch()

        signInIfNeeded(app: app, email: env["E2E_EMAIL"], password: env["E2E_PASSWORD"])

        let driveTab = app.buttons["Drive"]
        XCTAssertTrue(driveTab.waitForExistence(timeout: 25), "Did not reach the Drive tab.")
        driveTab.tap()

        let forward = app.descendants(matching: .any).matching(identifier: "e2e_drive_forward").element
        XCTAssertTrue(forward.waitForExistence(timeout: 15), "Forward D-pad button not found.")
        screenshot(app, name: "00_drive_tab")

        forward.press(forDuration: 1.0)
        screenshot(app, name: "01_drive_forward_pressed")

        let received = try await pollReceivedDriveCommands(mockHost: mockHost, timeout: 15)
        XCTAssertTrue(received, "mock_esp32 never received a drive (T=1) command.")
    }

    // MARK: - Helpers

    private func signInIfNeeded(app: XCUIApplication, email: String?, password: String?) {
        let driveTab = app.buttons["Drive"]
        if driveTab.waitForExistence(timeout: 8) { return }  // already authed

        guard let email, let password, !email.isEmpty, !password.isEmpty else {
            XCTFail("On Auth screen but E2E_EMAIL/E2E_PASSWORD not provided.")
            return
        }
        screenshot(app, name: "00_sign_in")
        let emailField = app.textFields["Email"]
        XCTAssertTrue(emailField.waitForExistence(timeout: 15), "Email field not found.")
        emailField.tap(); emailField.typeText(email)

        let pwField = app.secureTextFields["Password"]
        XCTAssertTrue(pwField.waitForExistence(timeout: 5))
        pwField.tap(); pwField.typeText(password)

        app.buttons["Sign In"].tap()
    }

    private func pollReceivedDriveCommands(mockHost: String, timeout: TimeInterval) async throws -> Bool {
        guard let url = URL(string: "http://\(mockHost)/__received") else { return false }
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if let (data, _) = try? await URLSession.shared.data(from: url),
               let commands = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] {
                if commands.contains(where: { ($0["T"] as? Int) == 1 }) {
                    return true
                }
            }
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
        return false
    }

    private func screenshot(_ app: XCUIApplication, name: String) {
        let shot = XCUIScreen.main.screenshot()
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)

        let dir = "/tmp/ios_screenshots"
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        try? shot.pngRepresentation.write(to: URL(fileURLWithPath: "\(dir)/\(name).png"))
    }
}
