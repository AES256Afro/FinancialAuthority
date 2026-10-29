// The glance as widgets: the Home Screen (small and medium), the Lock Screen, and on Apple Watch the same code
// draws the complications. It refreshes about every half hour from your server.
import SwiftUI
import WidgetKit

struct GlanceEntry: TimelineEntry {
    let date: Date
    let glance: Glance?
    let note: String?
}

struct GlanceProvider: TimelineProvider {
    func placeholder(in context: Context) -> GlanceEntry {
        GlanceEntry(date: .now, glance: .placeholder, note: nil)
    }

    func getSnapshot(in context: Context, completion: @escaping @Sendable (GlanceEntry) -> Void) {
        if context.isPreview {
            completion(placeholder(in: context))
            return
        }
        Task { completion(await Self.entry()) }
    }

    func getTimeline(in context: Context, completion: @escaping @Sendable (Timeline<GlanceEntry>) -> Void) {
        Task {
            let e = await Self.entry()
            completion(Timeline(entries: [e], policy: .after(.now.addingTimeInterval(30 * 60))))
        }
    }

    /// What the widget shows now: the glance, or why there is none. Internal so a test can run it.
    static func entry() async -> GlanceEntry {
        do {
            return GlanceEntry(date: .now, glance: try await APIClient.shared.glance(), note: nil)
        } catch {
            return GlanceEntry(date: .now, glance: nil, note: error.localizedDescription)
        }
    }
}

struct GlanceWidgetView: View {
    @Environment(\.widgetFamily) private var widgetFamily
    let entry: GlanceEntry
    /// Set only by a test that draws each family outside WidgetKit.
    var drawnAs: WidgetFamily?
    private var family: WidgetFamily { drawnAs ?? widgetFamily }

    var body: some View {
        if let g = entry.glance {
            switch family {
            case .accessoryInline:
                Text(g.safe_to_spend.map { "\(Format.money($0)) safe · payday \(Format.paydayWords(g.payday.days))" } ?? "Payday \(Format.paydayWords(g.payday.days))")
            case .accessoryCircular:
                VStack(spacing: 0) {
                    Text(g.safe_to_spend.map { Format.money($0) } ?? "?").font(.system(.caption, design: .rounded).weight(.heavy)).minimumScaleFactor(0.5)
                        .accessibilityLabel(Format.safe(g.safe_to_spend))
                    Text("\(g.payday.days)d").font(.caption2)
                }
            case .accessoryRectangular:
                VStack(alignment: .leading, spacing: 1) {
                    Text("Safe to spend").font(.caption2)
                    Text(Format.safe(g.safe_to_spend)).font(.headline.weight(.heavy)).minimumScaleFactor(0.6)
                    Text("Payday \(Format.paydayWords(g.payday.days))").font(.caption2)
                }
            #if os(iOS)
            case .systemMedium:
                HStack(alignment: .top) {
                    big(g)
                    Spacer()
                    VStack(alignment: .leading, spacing: 4) {
                        StateBadge(status: g.status)
                        Text(g.one_thing).font(.caption).lineLimit(4)
                    }
                    .frame(maxWidth: 160, alignment: .leading)
                }
            #endif
            default:
                big(g)
            }
        } else {
            Text(entry.note ?? "Open the app to sign in.").font(.caption)
        }
    }

    @ViewBuilder private func big(_ g: Glance) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Safe to spend").font(.caption).foregroundStyle(.secondary)
            Text(Format.safe(g.safe_to_spend)).font(.system(.title, design: .rounded).weight(.heavy)).lineLimit(1).minimumScaleFactor(0.4)
                .foregroundStyle((g.safe_to_spend ?? 0) < 0 ? .red : .primary)
            Text("through \(Format.shortDay(g.through))").font(.caption2).foregroundStyle(.secondary)
            Spacer(minLength: 2)
            Text("Payday \(Format.paydayWords(g.payday.days))").font(.caption2.weight(.semibold)).foregroundStyle(.blue)
        }
    }
}

struct GlanceWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "FinancialAuthorityGlance", provider: GlanceProvider()) { entry in
            GlanceWidgetView(entry: entry)
                .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Safe to spend")
        .description("What is safe to spend, and when payday is.")
        #if os(iOS)
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryInline, .accessoryCircular, .accessoryRectangular])
        #else
        .supportedFamilies([.accessoryInline, .accessoryCircular, .accessoryRectangular, .accessoryCorner])
        #endif
    }
}

