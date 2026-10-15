// The screens. Each leads with the picture that carries the answer and shows the engine's figures only.
import Charts
import SwiftUI
import WidgetKit

@MainActor
final class Loader<T>: ObservableObject {
    @Published var value: T?
    @Published var error: String?
    private let fetch: () async throws -> T

    init(_ fetch: @escaping () async throws -> T) {
        self.fetch = fetch
    }

    func load() async {
        do {
            value = try await fetch()
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct TodayView: View {
    @StateObject private var glance = Loader { try await APIClient.shared.glance() }
    @StateObject private var today = Loader { try await APIClient.shared.today() }

    var body: some View {
        List {
            if let g = glance.value {
                Section {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Safe to spend through \(Format.shortDay(g.through))").font(.subheadline).foregroundStyle(.secondary)
                        Text(Format.money(g.safe_to_spend))
                            .font(.system(size: 52, weight: .heavy, design: .rounded))
                            .foregroundStyle(g.safe_to_spend < 0 ? .red : .primary)
                            .minimumScaleFactor(0.5)
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
    @StateObject private var views = Loader { try await APIClient.shared.paychecks() }
    @AppStorage("paycheckMode") private var mode = Mode.strips.rawValue

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
                        .frame(height: 280)
                        Text("Solid from the ledger, faded as planned.").font(.caption).foregroundStyle(.secondary)
                    }
                case .rings:
                    ForEach(v.windows) { c in
                        Section("\(Format.day(c.start)) · \(Format.money(c.inflow))") {
                            HStack {
                                Chart(c.parts.filter { $0.amount > 0 }) { p in
                                    SectorMark(angle: .value("Amount", p.amount), innerRadius: .ratio(0.62), angularInset: 1.5)
                                        .foregroundStyle(PartColour.color(p.key))
                                }
                                .frame(width: 120, height: 120)
                                .overlay {
                                    VStack(spacing: 0) {
                                        Text(Format.money(c.parts.first { $0.key == "yours" }?.amount ?? 0)).font(.headline)
                                        Text("yours").font(.caption2).foregroundStyle(.secondary)
                                    }
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
                                }
                            }
                        }
                    }
                case .table:
                    ForEach(Array(checks)) { c in
                        Section("\(Format.day(c.start))\(c.actual ? "" : " · planned")") {
                            LabeledContent("Brings in", value: Format.money(c.inflow))
                            ForEach(c.parts.filter { $0.amount > 0 }) { LabeledContent($0.label, value: Format.money($0.amount)) }
                            if c.short > 0 { LabeledContent("Short", value: Format.money(c.short)).foregroundStyle(.red) }
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
    @StateObject private var cover = Loader { try await APIClient.shared.cover() }

    var body: some View {
        List {
            if let c = cover.value {
                Section("Each account") {
                    ForEach(c.accounts.filter { $0.role != "other" }) { a in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                Text(a.name).font(.headline)
                                Spacer()
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
    @State private var sent = false

    var body: some View {
        List {
            if let creds = Keychain.load() {
                Section("Server") {
                    LabeledContent("Address", value: creds.baseURL.host() ?? creds.baseURL.absoluteString)
                    LabeledContent("Access", value: "Read only")
                }
            }
            Section("Apple Watch") {
                Button(sent ? "Sent to the watch" : "Send the sign-in to the watch") {
                    PhoneSession.shared.sendCredentials()
                    sent = true
                }
                Text("The watch app and its complication read the same glance. Notifications from the web app reach the watch through the phone.").font(.caption).foregroundStyle(.secondary)
            }
            Section {
                Button("Sign out", role: .destructive, action: onSignOut)
            } footer: {
                Text("Signing out forgets the token on this phone. To cut every device off, revoke the app on the web app's Clients page.")
            }
        }
        .navigationTitle("Settings")
    }
}
