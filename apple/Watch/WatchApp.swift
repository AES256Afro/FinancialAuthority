// The watch: safe to spend, payday, the one thing and the next bill, read with the sign-in the phone sent.
// Notifications from the web app reach the watch through the phone; the complications are the glance widget.
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
                        Text(Format.money(g.safe_to_spend)).font(.system(.title2, design: .rounded).weight(.heavy)).foregroundStyle(g.safe_to_spend < 0 ? .red : .primary)
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
                } else {
                    Text(note ?? "Open the app on your iPhone and sign in; it sends the sign-in here.").font(.footnote)
                }
            }
            .navigationTitle("Authority")
            .task { await load() }
            .refreshable { await load() }
        }
    }

    private func load() async {
        do {
            glance = try await APIClient.shared.glance()
            note = nil
            WidgetCenter.shared.reloadAllTimelines()
        } catch {
            note = error.localizedDescription
        }
    }
}

final class WatchSession: NSObject, WCSessionDelegate {
    static let shared = WatchSession()

    func activate() {
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        receive(session.receivedApplicationContext)
    }

    func session(_ session: WCSession, didReceiveApplicationContext applicationContext: [String: Any]) {
        receive(applicationContext)
    }

    private func receive(_ context: [String: Any]) {
        guard let data = context["credentials"] as? Data, let creds = try? JSONDecoder().decode(Credentials.self, from: data) else { return }
        Keychain.save(creds)
        WidgetCenter.shared.reloadAllTimelines()
    }
}
