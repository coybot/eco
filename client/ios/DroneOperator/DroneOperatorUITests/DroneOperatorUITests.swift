import XCTest

/// UI tests for Drone Operator.
final class DroneOperatorUITests: XCTestCase {

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testLaunch_showsAuthOrFleet() throws {
        let app = XCUIApplication()
        app.launch()

        let authTitle = app.staticTexts["Drone Operator"]
        let fleetBar = app.navigationBars["My Drones"]

        let sawAuth = authTitle.waitForExistence(timeout: 15)
        let sawFleet = fleetBar.waitForExistence(timeout: 15)

        XCTAssertTrue(
            sawAuth || sawFleet,
            "Expected sign-in hero (Drone Operator) or authenticated tab (My Drones navigation title)."
        )
    }

    /// Gated integration path: after tapping Play, status should move away from OFF.
    func testLiveCamera_toggleLeavesOffState() throws {
        let (app, droneId) = try launchFleetOrSkip()
        XCTAssertTrue(waitForChangingLiveStatus(app: app, droneId: droneId, timeout: 45))
    }

    /// Gated integration path: send motor NL phrase and wait for motor-related response.
    func testChat_motorNl_repliesContainingMotorKeyword() throws {
        let (app, droneId) = try launchFleetOrSkip()
        openDroneDetail(app: app, droneId: droneId)

        let chatTab = app.buttons["tab_chat"]
        XCTAssertTrue(chatTab.waitForExistence(timeout: 15))
        chatTab.tap()

        let composer = app.descendants(matching: .any).matching(identifier: "e2e_chat_input").element
        XCTAssertTrue(composer.waitForExistence(timeout: 15), "Expected chat composer with e2e_chat_input.")

        let phrase = "Test motor 1 at 15 percent for 2 seconds"
        composer.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        composer.typeText(phrase)

        let sendBtn = app.buttons["e2e_chat_send"]
        XCTAssertTrue(sendBtn.waitForExistence(timeout: 5))
        sendBtn.tap()

        let replyPredicate = NSPredicate(format: "label CONTAINS[c] %@ AND label != %@", "motor", phrase)
        let labelMatch = app.staticTexts.matching(replyPredicate).firstMatch
        XCTAssertTrue(
            labelMatch.waitForExistence(timeout: 120),
            "Expected a non-user visible label mentioning \"motor\" after motor NL chat.",
        )
    }

    // MARK: - Helpers

    private func launchFleetOrSkip() throws -> (XCUIApplication, String) {
        guard ProcessInfo.processInfo.environment["RUN_HEZARFEN_E2E"] == "1" else {
            throw XCTSkip("Set RUN_HEZARFEN_E2E=1 to enable integration tests.")
        }
        guard let droneId = ProcessInfo.processInfo.environment["E2E_DRONE_ID"], !droneId.isEmpty else {
            throw XCTSkip("Set E2E_DRONE_ID to match a Fleet card.")
        }

        let app = XCUIApplication()
        app.launch()

        let fleetBar = app.navigationBars["My Drones"]
        let authHero = app.staticTexts["Drone Operator"]

        if fleetBar.waitForExistence(timeout: 25) {
            return (app, droneId)
        }

        if authHero.waitForExistence(timeout: 8) && !fleetBar.waitForExistence(timeout: 1) {
            throw XCTSkip("App is on Auth. Sign in once on this device, then re-run.")
        }

        throw XCTSkip("Fleet did not appear. Check simulator/device state and roster.")
    }

    private func waitForChangingLiveStatus(app: XCUIApplication, droneId: String, timeout: TimeInterval) -> Bool {
        openDroneDetail(app: app, droneId: droneId)

        let toggle = app.buttons["e2e_live_camera_toggle"]
        XCTAssertTrue(toggle.waitForExistence(timeout: 20))

        let offChip = app.staticTexts["OFF"]
        XCTAssertTrue(offChip.waitForExistence(timeout: 15), "Expected OFF status before tapping Play.")

        toggle.tap()

        let deadline = Date().addingTimeInterval(timeout)

        func hasInterestingStatus(_ app: XCUIApplication) -> Bool {
            ["CONNECTING", "WAITING", "LIVE"].contains { app.staticTexts[$0].exists }
        }

        func offStillVisible(_ app: XCUIApplication) -> Bool {
            app.staticTexts["OFF"].exists
        }

        while Date() < deadline {
            if hasInterestingStatus(app) { return true }
            if offStillVisible(app) == false
                && app.buttons["e2e_live_camera_toggle"].exists
                && app.progressIndicators.element(boundBy: 0).exists
            {
                return true
            }
            RunLoop.main.run(mode: .default, before: Date().addingTimeInterval(0.25))
        }

        return hasInterestingStatus(app) || !offStillVisible(app)
    }

    private func openDroneDetail(app: XCUIApplication, droneId: String) {
        let cell = app.descendants(matching: .any).matching(identifier: "drone_cell_\(droneId)").firstMatch
        XCTAssertTrue(cell.waitForExistence(timeout: 25), "No Fleet row drone_cell_\(droneId).")
        XCTAssertTrue(cell.isHittable)
        cell.tap()

        XCTAssertTrue(
            app.navigationBars.buttons["Back"].waitForExistence(timeout: 20)
                || app.buttons["e2e_live_camera_toggle"].waitForExistence(timeout: 20)
                || app.buttons["tab_chat"].waitForExistence(timeout: 20),
            "Drone detail chrome did not appear.",
        )
    }
}
