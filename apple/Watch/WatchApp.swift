// The watch: safe to spend, payday, the one thing and the next bill, read with a sign-in of the watch's own that
// the phone sent once. Notifications from the web app reach the watch through the phone; the complications are
// the glance widget.
import SwiftUI
import WatchConnectivity
import WidgetKit

@main
struct FinancialAuthorityWatchApp: App {
    init() {
        WatchSession.shared.activate()
    }

    var body: some Scene {
        WindowGroup { WatchHome() }
    }
}

struct WatchHome: View {
    @State private var glance: Glance?
    @State private var note: String?

    var body: some View {
        NavigationStack {
            List {
                if let g = glance {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Safe to spend").font(.caption2).foregroundStyle(.secondary)
                        Text(Format.safe(g.safe_to_spend)).font(.system(.title2, design: .rounded).weight(.heavy)).foregroundStyle((g.safe_to_spend ?? 0) < 0 ? .red : .primary)
                            .lineLimit(1).minimumScaleFactor(0.5)
                        Text("through \(Format.shortDay(g.through))").font(.caption2)
                    }
                    Label("Payday \(Format.paydayWords(g.payday.days))", systemImage: "calendar")
                    VStack(alignment: .leading, spacing: 2) {
                        StateBadge(status: g.status)
                        Text(g.one_thing).font(.footnote)
                    }
                    if let b = g.next_bill {
                        LabeledContent(b.name, value: Format.money(b.amount))
                    }
                    Button("Sign out", role: .destructive) {
                        Task {
                            await APIClient.signOut()
                            glance = nil
                            WidgetCenter.shared.reloadAllTimelines()
                        }
                    }
                } else {
                    Text(note ?? "Not signed in. Keep this app open, and on the iPhone open Authority, then Settings › Sign in the watch.").font(.footnote)
                }
            }
            .navigationTitle("Authority")
            .task { await load() }
            .refreshable { await load() }
            .onReceive(NotificationCenter.default.publisher(for: WatchSession.signedIn)) { _ in
                Task { await load() }
            }
        }
    }

    private func load() async {
        do {
            glance = try await APIClient.shared.glance()
            note = nil
            WidgetCenter.shared.reloadAllTimelines()
        } catch APIError.signedOut {
            glance = nil
            note = nil  // the signed-out words below say what to do
        } catch {
            note = error.localizedDescription
        }
    }
}

/// No stored state: every call reads `WCSession.default`, so sharing it across threads is safe.
final class WatchSession: NSObject, WCSessionDelegate, @unchecked Sendable {
    static let shared = WatchSession()
    static let signedIn = Notification.Name("FAWatchSignedIn")

    func activate() {
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {}

    /// The watch's own sign-in, sent once by the phone: kept in the watch's Keychain and confirmed, so the phone
    /// can tell the owner it arrived. The watch renews it by itself from here on.
    func session(_ session: WCSession, didReceiveMessage message: [String: Any], replyHandler: @escaping ([String: Any]) -> Void) {
        guard let data = message["credentials"] as? Data, let creds = try? JSONDecoder().decode(Credentials.self, from: data) else {
            replyHandler(["kept": false])
            return
        }
        Keychain.save(creds)
        replyHandler(["kept": true])
        WidgetCenter.shared.reloadAllTimelines()
        NotificationCenter.default.post(name: Self.signedIn, object: nil)
    }
}
