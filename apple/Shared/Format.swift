// Money, dates and the colours of the parts and the states, the same as the web app's.
import SwiftUI

enum Format {
    private static let whole: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .currency
        f.currencyCode = "USD"
        f.maximumFractionDigits = 0
        return f
    }()

    private static let cents: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .currency
        f.currencyCode = "USD"
        f.minimumFractionDigits = 2
        f.maximumFractionDigits = 2
        return f
    }()

    static func money(_ v: Double, cents withCents: Bool = false) -> String {
        (withCents ? cents : whole).string(from: NSNumber(value: v)) ?? "$\(Int(v))"
    }

    private static let iso: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    static func day(_ isoDate: String) -> String {
        guard let d = iso.date(from: isoDate) else { return isoDate }
        return d.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day())
    }

    static func shortDay(_ isoDate: String) -> String {
        guard let d = iso.date(from: isoDate) else { return isoDate }
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
