// The screens. Each leads with the picture that carries the answer and shows the engine's figures only.
import AuthenticationServices
import Charts
import SwiftUI
import WidgetKit

extension Notification.Name {
    /// A read found the sign-in gone (the server refused the token and its renewal), so the app goes back to the
    /// sign-in screen instead of leaving every tab saying it is not signed in.
    static let signedOut = Notification.Name("FASignedOut")
}

@MainActor
final class Loader<T: Sendable>: ObservableObject {
    @Published var value: T?
    @Published var error: String?
    private let fetch: @Sendable () async throws -> T

    init(_ fetch: @escaping @Sendable () async throws -> T) {
        self.fetch = fetch
    }

    func load() async {
        do {
            value = try await fetch()
            error = nil
        } catch APIError.signedOut {
            value = nil
            error = APIError.signedOut.localizedDescription
            NotificationCenter.default.post(name: .signedOut, object: nil)
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// The reads as plain functions, so a view's stored loader starts with no actor of its own (Swift 6 refuses a
/// stored default that closes over the API actor from a main-actor view).
enum Read {
    static func glance() async throws -> Glance { try await APIClient.shared.glance() }
    static func today() async throws -> TodayThing { try await APIClient.shared.today() }
    static func cover() async throws -> Cover { try await APIClient.shared.cover() }
    static func paychecks() async throws -> PaycheckViews { try await APIClient.shared.paychecks() }
}

struct TodayView: View {
    @ScaledMetric(relativeTo: .largeTitle) private var figureSize: CGFloat = 52
    @StateObject private var glance = Loader(Read.glance)
    @StateObject private var today = Loader(Read.today)

    var body: some View {
        List {
            if let g = glance.value {
                Section {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Safe to spend through \(Format.shortDay(g.through))").font(.subheadline).foregroundStyle(.secondary)
                        Text(Format.safe(g.safe_to_spend))
                            .font(.system(size: g.safe_to_spend == nil ? figureSize * 0.6 : figureSize, weight: .heavy, design: .rounded))
                            .foregroundStyle((g.safe_to_spend ?? 0) < 0 ? .red : .primary)
                            .lineLimit(1)
                            .minimumScaleFactor(0.4)
                        Label("Payday \(Format.paydayWords(g.payday.days)) · \(Format.money(g.payday.amount))", systemImage: "calendar")
                            .font(.subheadline.weight(.semibold)).foregroundStyle(.blue)
                    }
                    .padding(.vertical, 6)
                }
            }
            if let t = today.value {
                Section("The one thing today") {
                    VStack(alignment: .leading, spacing: 6) {
                        StateBadge(status: t.status)
                        Text(t.thing.text).font(.headline)
                        Text(t.thing.why).font(.footnote).foregroundStyle(.secondary)
                    }
                    ForEach(t.others, id: \.text) { Text($0.text).font(.subheadline) }
                }
                if !t.streaks.isEmpty {
                    Section("Streaks") {
                        ForEach(t.streaks) { s in
                            Label(s.label, systemImage: s.good ? "flame.fill" : "circle").foregroundStyle(s.good ? .green : .secondary)
                        }
                    }
                }
            }
            if let bill = glance.value?.next_bill {
                Section("Next") {
                    LabeledContent(bill.name, value: "\(Format.money(bill.amount, cents: true)) · \(Format.day(bill.date))")
                }
            }
            if let error = glance.error ?? today.error {
                Section { Text(error).foregroundStyle(.red).font(.footnote) }
            }
            if let g = glance.value {
                Section { Text(g.not_advice).font(.caption2).foregroundStyle(.secondary) }
            }
        }
        .navigationTitle("Today")
        .refreshable { await reload() }
        .task { await reload() }
    }

    private func reload() async {
        async let a: Void = glance.load()
        async let b: Void = today.load()
        _ = await (a, b)
        WidgetCenter.shared.reloadAllTimelines()
    }
}

struct PaychecksView: View {
    enum Mode: String, CaseIterable { case strips = "Strips", rings = "Rings", table = "Table" }
    @StateObject private var views = Loader(Read.paychecks)
    @AppStorage("paycheckMode") private var mode = Mode.strips.rawValue
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        List {
            Picker("Show as", selection: $mode) {
                ForEach(Mode.allCases, id: \.rawValue) { Text($0.rawValue).tag($0.rawValue) }
            }
            .pickerStyle(.segmented)
            if let v = views.value {
                let checks = v.history.suffix(4) + v.windows
                switch Mode(rawValue: mode) ?? .strips {
                case .strips:
                    Section("Where each check goes") {
                        Chart {
                            ForEach(Array(checks)) { c in
                                ForEach(c.parts.filter { $0.amount > 0 }) { p in
                                    BarMark(x: .value("Check", Format.shortDay(c.start)), y: .value("Amount", p.amount))
                                        .foregroundStyle(by: .value("Part", p.label))
                                        .opacity(c.actual ? 1 : 0.6)
                                }
                            }
                        }
                        .chartForegroundStyleScale(domain: v.parts.map(\.label), range: v.parts.map { PartColour.color($0.key) })
                        .chartXAxis {
                            // Month over day, so ten checks fit a phone without "Oct..." for every planned one.
                            AxisMarks { value in
                                AxisValueLabel(centered: true) {
                                    if let day = value.as(String.self) {
                                        let words = day.split(separator: " ")
                                        VStack(spacing: 0) { ForEach(words.indices, id: \.self) { Text(words[$0]) } }
                                    }
                                }
                            }
                        }
                        .chartLegend(.hidden)
                        // The axis words stop growing at the largest sizes, where eight dates would run together;
                        // the legend below and the table keep the full size.
                        .dynamicTypeSize(...DynamicTypeSize.xxLarge)
                        .frame(height: 280)
                        // The legend as a wrapping grid: the chart's own clips a long part name at the edge.
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: typeSize.isAccessibilitySize ? 260 : 140), alignment: .leading)], alignment: .leading, spacing: 4) {
                            ForEach(v.parts, id: \.key) { part in
                                HStack(spacing: 6) {
                                    RoundedRectangle(cornerRadius: 2).fill(PartColour.color(part.key)).frame(width: 10, height: 10)
                                    Text(part.label).font(.caption)
                                }
                            }
                        }
                        Text("Solid from the ledger, faded as planned.").font(.caption).foregroundStyle(.secondary)
                    }
                case .rings:
                    ForEach(v.windows) { c in
                        Section("\(Format.day(c.start)) · \(Format.money(c.inflow ?? c.paycheck ?? 0))") {
                            // Side by side, or stacked at the accessibility text sizes so the legend keeps its width.
                            let layout = typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading)) : AnyLayout(HStackLayout())
                            layout {
                                Chart(c.parts.filter { $0.amount > 0 }) { p in
                                    SectorMark(angle: .value("Amount", p.amount), innerRadius: .ratio(0.62), angularInset: 1.5)
                                        .foregroundStyle(PartColour.color(p.key))
                                }
                                .frame(width: 120, height: 120)
                                .overlay {
                                    // Capped so the figure stays inside the ring; the legend beside it says it in full.
                                    VStack(spacing: 0) {
                                        Text(Format.money(c.parts.first { $0.key == "yours" }?.amount ?? 0)).font(.headline).minimumScaleFactor(0.6)
                                        Text("yours").font(.caption2).foregroundStyle(.secondary)
                                    }
                                    .padding(.horizontal, 22)
                                    .dynamicTypeSize(...DynamicTypeSize.xLarge)
                                }
                                VStack(alignment: .leading, spacing: 3) {
                                    ForEach(c.parts.filter { $0.amount > 0 }) { p in
                                        HStack(spacing: 6) {
                                            RoundedRectangle(cornerRadius: 2).fill(PartColour.color(p.key)).frame(width: 10, height: 10)
                                            Text(p.label).font(.caption)
                                            Spacer()
                                            Text(Format.money(p.amount)).font(.caption.monospacedDigit())
                                        }
                                    }
                                    if let short = c.short, short > 0 {
                                        // A short check is said in words with an icon, never by colour alone.
                                        Label("Short \(Format.money(short))", systemImage: "exclamationmark.octagon.fill")
                                            .font(.caption.weight(.bold)).foregroundStyle(.red)
                                    }
                                }
                            }
                        }
                    }
                case .table:
                    ForEach(Array(checks)) { c in
                        Section("\(Format.day(c.start))\(c.actual ? "" : " · planned")") {
                            LabeledContent("Brings in", value: c.inflow.map { Format.money($0) } ?? "Unknown until a balance is reported")
                            ForEach(c.parts.filter { $0.amount > 0 }) { LabeledContent($0.label, value: Format.money($0.amount)) }
                            if let short = c.short, short > 0 {
                                LabeledContent { Text(Format.money(short)) } label: { Label("Short", systemImage: "exclamationmark.octagon.fill") }
                                    .foregroundStyle(.red)
                            }
                        }
                    }
                }
                Section { Text(v.not_advice).font(.caption2).foregroundStyle(.secondary) }
            } else if let e = views.error {
                Text(e).foregroundStyle(.red)
            } else {
                ProgressView()
            }
        }
        .navigationTitle("Paychecks")
        .refreshable { await views.load() }
        .task { await views.load() }
    }
}

struct CoverView: View {
    @StateObject private var cover = Loader(Read.cover)
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        List {
            if let c = cover.value {
                Section("Each account") {
                    ForEach(c.accounts.filter { $0.role != "other" }) { a in
                        VStack(alignment: .leading, spacing: 4) {
                            // The badge under the name at the accessibility sizes, so the name is not broken mid-word.
                            let layout = typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: 4)) : AnyLayout(HStackLayout())
                            layout {
                                Text(a.name).font(.headline)
                                if !typeSize.isAccessibilitySize { Spacer() }
                                StateBadge(status: a.status)
                            }
                            Text(a.headline).font(.subheadline).foregroundStyle(.secondary)
                        }
                    }
                }
                Section("The payday sheet") {
                    if c.sheet.isEmpty { Text("Nothing to move before the next two paydays.").foregroundStyle(.secondary) }
                    ForEach(c.sheet) { r in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Image(systemName: r.done ? "checkmark.circle.fill" : "circle").foregroundStyle(r.done ? .green : .secondary)
                                Text(r.action).font(.subheadline.weight(.semibold))
                                Spacer()
                                Text(Format.money(r.amount, cents: true)).font(.subheadline.monospacedDigit())
                            }
                            Text("\(Format.day(r.date)) · so that \(r.so_that)").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Text("Tick these off in the web app; this app reads only.").font(.caption2).foregroundStyle(.secondary)
                }
            } else if let e = cover.error {
                Text(e).foregroundStyle(.red)
            } else {
                ProgressView()
            }
        }
        .navigationTitle("Cover")
        .refreshable { await cover.load() }
        .task { await cover.load() }
    }
}

struct SettingsView: View {
    var onSignOut: () -> Void
    @Environment(\.webAuthenticationSession) private var webAuth
    @State private var busy = false
    @State private var watchNote: String?

    var body: some View {
        List {
            if let creds = Keychain.load() {
                Section("Server") {
                    LabeledContent("Address", value: creds.baseURL.host() ?? creds.baseURL.absoluteString)
                    LabeledContent("Access", value: "Read only")
                }
            }
            Section {
                Button {
                    Task { await signInWatch() }
                } label: {
                    if busy { ProgressView() } else { Text("Sign in the watch") }
                }
                .disabled(busy)
                if let watchNote { Text(watchNote).font(.footnote) }
            } header: {
                Text("Apple Watch")
            } footer: {
                Text("Open Authority on the watch first. The watch gets a sign-in of its own, so you sign in once more on the server's page, and the phone and the watch each renew their own token.")
            }
            Section {
                Button("Sign out", role: .destructive, action: onSignOut)
            } footer: {
                Text("Signing out ends this phone's token on the server and forgets it here; the watch keeps its own. To cut every device off, revoke the apps on the web app's Clients page.")
            }
        }
        .navigationTitle("Settings")
    }

    private func signInWatch() async {
        guard let phone = Keychain.load() else { return }
        busy = true
        defer { busy = false }
        do {
            try PhoneSession.shared.checkWatch()
            let watch = try await SignIn.authorize(base: phone.baseURL, clientName: "FinancialAuthority for Apple Watch", using: webAuth)
            try await PhoneSession.shared.handOff(watch)
            watchNote = "Signed in on the watch."
        } catch let error as ASWebAuthenticationSessionError where error.code == .canceledLogin {
            watchNote = nil
        } catch {
            watchNote = error.localizedDescription
        }
    }
}
