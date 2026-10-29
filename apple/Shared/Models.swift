// The shapes the app reads from the server. Every figure is the engine's; the app only draws it.
import Foundation

struct Glance: Codable, Equatable {
    struct Payday: Codable, Equatable { let date: String; let days: Int; let amount: Double }
    struct Bill: Codable, Equatable { let name: String; let amount: Double; let date: String }
    struct CoverCounts: Codable, Equatable { let good: Int; let watch: Int; let act: Int }
    let as_of: String
    /// Null until a bank or the owner reports a balance: unknown, never $0.
    let safe_to_spend: Double?
    let through: String
    let payday: Payday
    let next_bill: Bill?
    let one_thing: String
    let status: String
    let cover: CoverCounts
    let text: String
    let not_advice: String

    static let placeholder = Glance(as_of: "2026-10-15", safe_to_spend: 812, through: "2026-10-29",
                                    payday: .init(date: "2026-10-30", days: 15, amount: 2240), next_bill: .init(name: "Phone", amount: 96, date: "2026-10-22"),
                                    one_thing: "Nothing needs you today.", status: "good", cover: .init(good: 3, watch: 0, act: 0),
                                    text: "Safe to spend $812 through Oct 29.", not_advice: "Not financial advice.")
}

struct TodayThing: Codable {
    struct Thing: Codable { let text: String; let why: String; let link: String }
    struct Streak: Codable, Identifiable { let key: String; let label: String; let good: Bool; var id: String { key } }
    let thing: Thing
    let status: String
    let others: [Thing]
    let streaks: [Streak]
}

struct Cover: Codable {
    struct Account: Codable, Identifiable {
        let id: Int
        let name: String
        let role: String
        let status: String
        let headline: String
        let balance: Double?
    }
    struct Row: Codable, Identifiable {
        let id: String
        let kind: String
        let date: String
        let action: String
        let amount: Double
        let so_that: String
        let done: Bool
    }
    let paydays: [String]
    let accounts: [Account]
    let sheet: [Row]
}

struct PaycheckViews: Codable {
    struct PartName: Codable { let key: String; let label: String }
    struct Part: Codable, Identifiable { let key: String; let label: String; let amount: Double; var id: String { key } }
    struct Check: Codable, Identifiable {
        let start: String
        let paycheck: Double?
        /// Null in the first window when no balance is reported yet: what it brings in and leaves is unknown.
        let inflow: Double?
        let short: Double?
        let actual: Bool
        let parts: [Part]
        var id: String { start }
    }
    let parts: [PartName]
    let windows: [Check]
    let history: [Check]
    let not_advice: String
}

/// What the app keeps to reach the server: never a password, only the tokens the server issued.
struct Credentials: Codable, Equatable {
    var baseURL: URL
    var clientID: String
    var tokenEndpoint: URL
    var accessToken: String
    var refreshToken: String?
}
