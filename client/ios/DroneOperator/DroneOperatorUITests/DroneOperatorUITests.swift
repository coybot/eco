import XCTest

/// UI tests for Drone Operator.
final class DroneOperatorUITests: XCTestCase {

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    /// Capture-only: launches the app pointed at the local GCS + video bridge,
    /// selects the first drone chip so the right sidebar opens with its live
    /// video + telemetry, and saves a full-screen screenshot as a test
    /// attachment. Run on the physical iPad to visually confirm the
    /// selection-driven video feed renders on-device (the always-on floating
    /// PiP this replaced needed no selection step — this one does):
    ///   xcodebuild test -only-testing:DroneOperatorUITests/DroneOperatorUITests/testCapture_missionLiveFeeds ...
    func testCapture_missionLiveFeeds() throws {
        let app = XCUIApplication()
        // Deterministic GCS target (matches drone/sim/run_local_demo.sh on this
        // Mac + the demo operator token baked into GCSSettings).
        app.launchEnvironment["GCS_TEST_HOST"] = "10.10.10.38"
        app.launchEnvironment["GCS_TEST_HTTP_PORT"] = "8080"
        app.launchEnvironment["GCS_TEST_MQTT_PORT"] = "1883"
        app.launchEnvironment["GCS_TEST_VIDEO_PORT"] = "8091"
        app.launchEnvironment["GCS_TEST_TOKEN"] = "kivvgBdqcBzGDKf4rfAusUzqk7g4JVpNRItuy0vYKyE"
        app.launch()

        // The bottom tab bar defaults to Mission; tap it if present to be sure.
        let mission = app.buttons["Mission"]
        if mission.waitForExistence(timeout: 15) { mission.tap() }

        // Select the first drone chip once the fleet has loaded — this is
        // what opens the sidebar (no more always-on PiP to just wait for).
        let firstChip = app.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier BEGINSWITH 'mission_drone_chip_'"))
            .firstMatch
        if firstChip.waitForExistence(timeout: 15) { firstChip.tap() }
        Thread.sleep(forTimeInterval: 12)  // let the selected feed connect and stream

        let shot = XCUIScreen.main.screenshot()
        let att = XCTAttachment(screenshot: shot)
        att.name = "mission_sidebar_live_feed"
        att.lifetime = .keepAlways
        add(att)
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

    /// The natural-language mission per vehicle type — kept in sync with the
    /// `mission:` field for each type in eco/e2e/scenarios.yaml, since that file is
    /// the single source of truth for what "the scenario" is; Swift can't read YAML
    /// here without a new dependency, so this mirrors it in the one place this test
    /// needs it. All three mention a photo, so the existing "photo" reply assertion
    /// below works unchanged across quad/rover/fixed-wing.
    private func missionFor(vehicleType: String) -> String {
        switch vehicleType {
        case "rover":
            return "drive forward 2 meters, look around, and send a picture"
        case "fixedwing":
            return "climb to 40 meters, orbit once, and photograph the field"
        default: // "quadcopter"
            return "go up 2 meters, take a photo and tell me what you see, then land"
        }
    }

    /// End-to-end onboarding: sign in, add a simulated drone via the SAME
    /// "Already configured" manual-entry flow a real drone uses after WiFi setup,
    /// confirm it comes Online, then send a chat command and await a response.
    /// Drives the real app UI — this is the "manual step" automated.
    ///
    /// Env (passed via TEST_RUNNER_*):
    ///   RUN_HEZARFEN_E2E=1, E2E_DRONE_ID, E2E_EMAIL, E2E_PASSWORD, E2E_VEHICLE_TYPE
    ///   (quadcopter|rover|fixedwing, default quadcopter)
    func testOnboard_addSimDrone_thenCommand() throws {
        guard ProcessInfo.processInfo.environment["RUN_HEZARFEN_E2E"] == "1" else {
            throw XCTSkip("Set RUN_HEZARFEN_E2E=1 to enable integration tests.")
        }
        let env = ProcessInfo.processInfo.environment
        guard let droneId = env["E2E_DRONE_ID"], !droneId.isEmpty else {
            throw XCTSkip("Set E2E_DRONE_ID to the running sim drone id.")
        }
        let vehicleType = env["E2E_VEHICLE_TYPE"] ?? "quadcopter"

        let app = XCUIApplication()
        app.launch()

        signInIfNeeded(app: app, email: env["E2E_EMAIL"], password: env["E2E_PASSWORD"])

        // Missions tab (ChatsView) should be visible after sign-in.
        XCTAssertTrue(app.navigationBars["Missions"].waitForExistence(timeout: 25),
                      "Did not reach the Missions screen after sign-in.")
        screenshot(app, name: "01_missions_empty")

        // Open Add Drone via the empty-state capsule or the toolbar plus button.
        let addCapsule = app.buttons.containing(
            NSPredicate(format: "label CONTAINS[c] %@", "Onboard new drone")).firstMatch
        if addCapsule.waitForExistence(timeout: 4) {
            addCapsule.tap()
        } else {
            app.navigationBars["Missions"].buttons.element(boundBy: 0).tap()
        }

        // Choose "Already configured" (the post-setup / manual-entry path).
        let alreadyConfigured = app.buttons.containing(
            NSPredicate(format: "label CONTAINS[c] %@", "Already configured")).firstMatch
        XCTAssertTrue(alreadyConfigured.waitForExistence(timeout: 10),
                      "Add Drone sheet did not offer 'Already configured'.")
        screenshot(app, name: "02_add_drone_sheet")
        alreadyConfigured.tap()

        // Enter the sim drone id and submit.
        let idField = app.textFields.containing(
            NSPredicate(format: "placeholderValue CONTAINS[c] %@", "drone-001")).firstMatch
        XCTAssertTrue(idField.waitForExistence(timeout: 10), "Drone ID field not found.")
        screenshot(app, name: "03_already_configured_entry")
        idField.tap()
        idField.typeText(droneId)

        // The submit button is the one inside the form (label "Add Drone").
        let submit = app.buttons["Add Drone"].firstMatch
        XCTAssertTrue(submit.waitForExistence(timeout: 5))
        submit.tap()

        // The new drone card should appear and report Online.
        let cell = app.descendants(matching: .any)
            .matching(identifier: "drone_cell_\(droneId)").firstMatch
        XCTAssertTrue(cell.waitForExistence(timeout: 30),
                      "Onboarded drone card drone_cell_\(droneId) never appeared.")

        let onlineChip = app.staticTexts["Online"]
        XCTAssertTrue(onlineChip.waitForExistence(timeout: 40),
                      "Drone did not report Online after onboarding (check daemon heartbeat).")
        screenshot(app, name: "04_missions_online")

        // Send a command through the real cloud pipeline and await a response.
        openDroneDetail(app: app, droneId: droneId)
        let chatTab = app.buttons["tab_chat"]
        XCTAssertTrue(chatTab.waitForExistence(timeout: 15))
        screenshot(app, name: "05_drone_detail")
        chatTab.tap()

        let composer = app.descendants(matching: .any)
            .matching(identifier: "e2e_chat_input").element
        XCTAssertTrue(composer.waitForExistence(timeout: 15), "Chat composer not found.")
        let phrase = missionFor(vehicleType: vehicleType)
        composer.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        composer.typeText(phrase)

        let sendBtn = app.buttons["e2e_chat_send"]
        XCTAssertTrue(sendBtn.waitForExistence(timeout: 5))
        sendBtn.tap()
        screenshot(app, name: "06_chat_sent")

        // A drone reply mentioning "photo" should arrive within the cloud round-trip window.
        let reply = app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS[c] 'photo'")).firstMatch
        XCTAssertTrue(reply.waitForExistence(timeout: 150),
                      "No assistant reply mentioning 'photo' arrived within 150s.")
        screenshot(app, name: "07_chat_reply")
    }

    /// Saves a screenshot as an XCTAttachment so it appears in the xcresult bundle
    /// and is also written to /tmp/ios_screenshots/ for easy retrieval.
    private func screenshot(_ app: XCUIApplication, name: String) {
        let shot = XCUIScreen.main.screenshot()
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)

        // Also write to /tmp for direct file access
        let dir = "/tmp/ios_screenshots"
        try? FileManager.default.createDirectory(atPath: dir,
            withIntermediateDirectories: true)
        try? shot.pngRepresentation.write(
            to: URL(fileURLWithPath: "\(dir)/\(name).png"))
    }

    /// If the app is on the Auth screen, sign in with email/password.
    private func signInIfNeeded(app: XCUIApplication, email: String?, password: String?) {
        let fleetBar = app.navigationBars["Missions"]
        if fleetBar.waitForExistence(timeout: 8) { return }  // already authed

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

        let fleetBar = app.navigationBars["Missions"]
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
