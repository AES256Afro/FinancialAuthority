// The iPhone and iPad app: Today, the paychecks, Cover and Settings, all read from your own server. Widgets on
// the Home Screen and the Lock Screen show the glance; the watch's own sign-in is started from Settings here.
import SwiftUI
import WidgetKit

@main
struct FinancialAuthorityApp: App {
    @State private var signedIn = Keychain.load() != nil

    init() {
        PhoneSession.shared.activate()
    }

    var body: some Scene {
        WindowGroup {
            if signedIn {
                MainTabs(onSignOut: {
                    Task {
                        await APIClient.signOut()
                        WidgetCenter.shared.reloadAllTimelines()
                        signedIn = false
                    }
                })
                .onReceive(NotificationCenter.default.publisher(for: .signedOut)) { _ in
                    if Keychain.load() == nil {
                        WidgetCenter.shared.reloadAllTimelines()
                        signedIn = false
                    }
                }
            } else {
                SignInView(onSignedIn: {
                    WidgetCenter.shared.reloadAllTimelines()
                    signedIn = true
                })
            }
        }
    }
}

struct MainTabs: View {
    var onSignOut: () -> Void

    var body: some View {
        TabView {
            NavigationStack { TodayView() }
                .tabItem { Label("Today", systemImage: "sun.max") }
            NavigationStack { PaychecksView() }
                .tabItem { Label("Paychecks", systemImage: "chart.bar.xaxis") }
            NavigationStack { CoverView() }
                .tabItem { Label("Cover", systemImage: "gauge.with.dots.needle.33percent") }
            NavigationStack { SettingsView(onSignOut: onSignOut) }
                .tabItem { Label("Settings", systemImage: "gearshape") }
        }
    }
}
