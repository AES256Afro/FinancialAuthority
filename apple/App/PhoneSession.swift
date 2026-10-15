// The phone hands the server address and the tokens to the paired watch over WatchConnectivity, so the watch
// never asks for a password; the watch keeps them in its own Keychain.
import Foundation
import WatchConnectivity

final class PhoneSession: NSObject, WCSessionDelegate {
    static let shared = PhoneSession()

    func activate() {
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    func sendCredentials() {
        guard WCSession.isSupported(), WCSession.default.activationState == .activated, WCSession.default.isPaired, WCSession.default.isWatchAppInstalled,
              let creds = Keychain.load(), let data = try? JSONEncoder().encode(creds) else { return }
        try? WCSession.default.updateApplicationContext(["credentials": data])
    }

    func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        if activationState == .activated { sendCredentials() }
    }

    func sessionDidBecomeInactive(_ session: WCSession) {}

    func sessionDidDeactivate(_ session: WCSession) {
        WCSession.default.activate()
    }
}
