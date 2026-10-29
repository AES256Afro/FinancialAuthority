// Money, dates and the colours of the parts and the states, the same as the web app's.
import SwiftUI

enum Format {
    /// Dollars as the web app writes them (frontend/src/format.ts): halves rounded away from zero, a true minus
    /// before the dollar sign, and a figure that rounds to nothing shown as "$0", never "−$0".
    static func money(_ v: Double, cents withCents: Bool = false) -> String {
        let text = abs(v).formatted(.number.precision(.fractionLength(withCents ? 2 : 0)).rounded(rule: .toNearestOrAwayFromZero).locale(Locale(identifier: "en_US")))
        return (v < 0 && text.contains(where: { "123456789".contains($0) }) ? "\u{2212}$" : "$") + text
    }

    /// Safe to spend, or the plain words when no balance is reported yet (unknown is not $0).
    static func safe(_ v: Double?) -> String {
        v.map { money($0) } ?? "No balance yet"
    }

    /// A server date (yyyy-MM-dd) as local midnight, so the day shown is the day sent in any time zone.
    private static func date(_ iso: String) -> Date? {
        let p = iso.split(separator: "-").compactMap { Int($0) }
        guard p.count == 3 else { return nil }
        return Calendar.current.date(from: DateComponents(year: p[0], month: p[1], day: p[2]))
    }

    static func day(_ isoDate: String) -> String {
        guard let d = date(isoDate) else { return isoDate }
        return d.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day())
    }

    static func shortDay(_ isoDate: String) -> String {
        guard let d = date(isoDate) else { return isoDate }
        return d.formatted(.dateTime.month(.abbreviated).day())
    }

    static func paydayWords(_ days: Int) -> String {
        days == 0 ? "today" : days == 1 ? "tomorrow" : "in \(days) days"
    }
}

/// The part colours, validated for colour-blind readers in both modes (the web app's palette).
enum PartColour {
    static func color(_ key: String) -> Color {
        switch key {
        case "housing": return Color(light: 0x2A78D6, dark: 0x3987E5)
        case "bills": return Color(light: 0xEB6834, dark: 0xD95926)
        case "card": return Color(light: 0x4A3AA7, dark: 0x9085E9)
        case "later": return Color(light: 0xE87BA4, dark: 0xD55181)
        case "aside": return Color(light: 0xC3C2B7, dark: 0x4A505B)
        case "saving": return Color(light: 0xEDA100, dark: 0xC98500)
        default: return Color(light: 0x1BAF7A, dark: 0x199E70)
        }
    }
}

/// A state always shows an icon and a word, never colour alone.
struct StateBadge: View {
    let status: String

    var body: some View {
        let (icon, word, tint): (String, String, Color) = {
            switch status {
            case "crit": return ("exclamationmark.octagon.fill", "Act", .red)
            case "warn": return ("exclamationmark.triangle.fill", "Watch", .orange)
            default: return ("checkmark.circle.fill", "On track", .green)
            }
        }()
        Label(word, systemImage: icon)
            .font(.caption.weight(.bold))
            .foregroundStyle(tint)
    }
}

extension Color {
    init(light: UInt32, dark: UInt32) {
        #if os(watchOS)
        self.init(hex: dark)
        #else
        self.init(UIColor { trait in
            let v = trait.userInterfaceStyle == .dark ? dark : light
            return UIColor(red: CGFloat((v >> 16) & 0xFF) / 255, green: CGFloat((v >> 8) & 0xFF) / 255, blue: CGFloat(v & 0xFF) / 255, alpha: 1)
        })
        #endif
    }

    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255, blue: Double(hex & 0xFF) / 255)
    }
}
