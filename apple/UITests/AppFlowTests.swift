// Drives the app on a Simulator against a local dev server (backend/scripts/dev_signin.py): the server's own
// sign-in sheet, every screen, a relaunch that must keep the token, and signing out and in again. Each screen is
// attached as a screenshot and as its visible text, so the figures can be checked against the REST.
// Run it with the server address and the dev server's test password in the environment (see apple/README.md):
//   TEST_RUNNER_FA_DEV_SERVER=http://localhost:8001 TEST_RUNNER_FA_DEV_PASSWORD=... xcodebuild test ...
import XCTest

@MainActor
final class AppFlowTests: XCTestCase {
    private let app = XCUIApplication()
    private var env: [String: String] { ProcessInfo.processInfo.environment }

    func test1SignInAndEveryScreen() throws {
        launch()
        try signInIfNeeded()
        assertFigures()
        capture("today")
        app.tabBars.buttons["Paychecks"].tap()
        for mode in ["Strips", "Rings", "Table"] {
            app.buttons[mode].tap()
            capture("paychecks-\(mode.lowercased())")
            if mode != "Strips" { scrollCapture("paychecks-\(mode.lowercased())") }
        }
        app.tabBars.buttons["Cover"].tap()
        capture("cover")
        scrollCapture("cover")
        app.tabBars.buttons["Settings"].tap()
        capture("settings")
    }

    func test2RelaunchKeepsTheToken() {
        launch()
        XCTAssertTrue(app.navigationBars["Today"].waitForExistence(timeout: 20), "a relaunch asked to sign in again")
        assertFigures()
        capture("relaunched")
        app.terminate()
        launch()
        XCTAssertTrue(app.navigationBars["Today"].waitForExistence(timeout: 20))
        assertFigures()
    }

    func test3SignOutAndIn() throws {
        launch()
        try signInIfNeeded()
        app.tabBars.buttons["Settings"].tap()
        app.buttons["Sign out"].tap()
        XCTAssertTrue(app.buttons["Sign in"].waitForExistence(timeout: 10))
        capture("signed-out")
        try signInIfNeeded()
        capture("signed-in-again")
    }

    /// Only the screens, for a run at another size, appearance or text size.
    func test4ScreensOnly() throws {
        launch()
        try signInIfNeeded()
        capture("today")
        scrollCapture("today")
        app.tabBars.buttons["Paychecks"].tap()
        for mode in ["Strips", "Rings", "Table"] {
            if app.buttons[mode].waitForExistence(timeout: 5) { app.buttons[mode].tap() }
            capture("paychecks-\(mode.lowercased())")
        }
        app.tabBars.buttons["Cover"].tap()
        capture("cover")
        app.tabBars.buttons["Settings"].tap()
        capture("settings")
    }

    /// The watch's own sign-in: start the watch app first (`simctl launch` on the paired watch), then this taps
    /// Sign in the watch, signs in on the server's page once more, and waits for the watch to confirm.
    func test5SignInTheWatch() throws {
        launch()
        try signInIfNeeded()
        app.tabBars.buttons["Settings"].tap()
        app.buttons["Sign in the watch"].tap()
        try serverSheet()
        let done = app.staticTexts["Signed in on the watch."]
        let ok = done.waitForExistence(timeout: 30)
        capture("settings-watch")
        XCTAssertTrue(ok, "the watch did not confirm its sign-in")
    }

    // MARK: - Helpers

    private func launch() {
        continueAfterFailure = false
        app.launch()
    }

    /// Today shows the server's figures, not a refused sign-in.
    private func assertFigures() {
        let safe = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'Safe to spend'")).firstMatch
        XCTAssertTrue(safe.waitForExistence(timeout: 20), "Today did not show the glance")
        XCTAssertFalse(app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'Not signed in'")).firstMatch.exists)
    }

    private func signInIfNeeded() throws {
        if app.navigationBars["Today"].waitForExistence(timeout: 8) { return }
        let server = try XCTUnwrap(env["FA_DEV_SERVER"], "set TEST_RUNNER_FA_DEV_SERVER")
        let field = app.textFields.firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        field.tap()
        if let typed = field.value as? String, !typed.isEmpty, typed != field.placeholderValue {
            field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: typed.count))
        }
        field.typeText(server)
        app.buttons["Sign in"].tap()
        try serverSheet()
        XCTAssertTrue(app.navigationBars["Today"].waitForExistence(timeout: 30), "sign-in did not reach Today")
        sleep(2)  // let the first reads land before the screenshot
    }

    /// The system asks before the sign-in sheet opens; then the server's own page: password, Sign in, Allow.
    private func serverSheet() throws {
        let password = try XCTUnwrap(env["FA_DEV_PASSWORD"], "set TEST_RUNNER_FA_DEV_PASSWORD")
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let proceed = springboard.buttons["Continue"]
        if proceed.waitForExistence(timeout: 10) { proceed.tap() }
        let secure = app.secureTextFields.firstMatch
        XCTAssertTrue(secure.waitForExistence(timeout: 20), "the server's sign-in page did not open")
        capture("server-signin")
        secure.tap()
        secure.typeText(password + "\n")  // Return submits the form; a tap on the page's button is not delivered reliably
        let notNow = app.alerts.buttons["Not Now"]  // the system offers to save the password; it is not kept
        if notNow.waitForExistence(timeout: 5) { notNow.tap() }
        let allow = app.webViews.buttons["Allow"].firstMatch
        XCTAssertTrue(allow.waitForExistence(timeout: 15), "the consent page did not open")
        capture("server-consent")
        allow.tap()
    }

    private func capture(_ name: String) {
        sleep(1)
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
        let text = XCTAttachment(string: app.debugDescription)  // one snapshot of the tree, every label in it
        text.name = name + ".txt"
        text.lifetime = .keepAlways
        add(text)
    }

    private func scrollCapture(_ name: String) {
        app.swipeUp()
        capture(name + "-2")
        app.swipeUp()
        capture(name + "-3")
        app.swipeDown()
        app.swipeDown()
        app.swipeDown()
    }
}
