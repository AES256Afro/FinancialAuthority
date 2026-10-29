// The watch signs in as a client of its own: the phone runs the server's sign-in once more under the watch's
// name and hands the result over WatchConnectivity as a live message the watch confirms, then forgets it. The
// phone never keeps the watch's tokens and the watch never sees the phone's, so each renews its own refresh token
// and neither signs the other out when tokens rotate.
import Foundation
import WatchConnectivity

/// No stored state: every call reads `WCSession.default`, so sharing it across threads is safe.
final class PhoneSession: NSObject, WCSessionDelegate, @unchecked Sendable {
    static let shared = PhoneSession()

    enum HandOff: Error, LocalizedError {
        case noWatch, notInstalled, notOpen
        var errorDescription: String? {
            switch self {
            case .noWatch: return "No Apple Watch is paired with this iPhone."
            case .notInstalled: return "Install Authority on the watch first, from the Watch app on this iPhone."
            case .notOpen: return "Open Authority on the watch, then tap again."
            }
        }
    }

    func activate() {
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    /// Whether a watch with the app is there to sign in, checked before the sign-in sheet opens.
    func checkWatch() throws {
        let s = WCSession.default
        guard WCSession.isSupported(), s.activationState == .activated, s.isPaired else { throw HandOff.noWatch }
        guard s.isWatchAppInstalled else { throw HandOff.notInstalled }
        guard s.isReachable else { throw HandOff.notOpen }
    }

    /// Send the watch its own sign-in, once, and wait for the watch to say it is kept. Never queued: a sign-in the
    /// watch is not open to take is not left waiting anywhere.
    func handOff(_ credentials: Credentials) async throws {
        try checkWatch()
        let data = try JSONEncoder().encode(credentials)
        try await withCheckedThrowingContinuation { (done: CheckedContinuation<Void, Error>) in
            WCSession.default.sendMessage(["credentials": data], replyHandler: { _ in done.resume() }, errorHandler: { done.resume(throwing: $0) })
        }
    }

    func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {}

    func sessionDidBecomeInactive(_ session: WCSession) {}

    func sessionDidDeactivate(_ session: WCSession) {
        WCSession.default.activate()
    }
}
