// The widget's timeline provider against a live server, run inside the app (so it reads the app's Keychain item,
// as the widget does): the entry must be the server's glance, figure for figure, and each family is drawn to an
// image attached to the result. Sign the app in on this Simulator first (FAUITests does).
import SwiftUI
import WidgetKit
import XCTest

@MainActor
final class GlanceTimelineTests: XCTestCase {
    func testEntryIsTheServersGlance() async throws {
        let creds = try XCTUnwrap(Keychain.load(), "sign the app in on this Simulator first")
        var request = URLRequest(url: creds.baseURL.appending(path: "/api/v1/glance"))
        let entry = await GlanceProvider.entry()
        let fresh = try XCTUnwrap(Keychain.load())  // the provider may have renewed the token
        request.setValue("Bearer \(fresh.accessToken)", forHTTPHeaderField: "Authorization")
        let (data, _) = try await URLSession.shared.data(for: request)
        let server = try JSONDecoder().decode(Glance.self, from: data)
        XCTAssertNil(entry.note)
        XCTAssertEqual(entry.glance, server)

        let sizes: [(WidgetFamily, CGSize)] = [
            (.systemSmall, CGSize(width: 170, height: 170)), (.systemMedium, CGSize(width: 364, height: 170)),
            (.accessoryRectangular, CGSize(width: 172, height: 76)), (.accessoryCircular, CGSize(width: 76, height: 76)),
            (.accessoryInline, CGSize(width: 257, height: 26)),
        ]
        for (family, size) in sizes {
            for scheme in [ColorScheme.light, .dark] {
                let view = GlanceWidgetView(entry: entry, drawnAs: family)
                    .padding(family.rawValue < WidgetFamily.accessoryCircular.rawValue ? 16 : 4)
                    .frame(width: size.width, height: size.height, alignment: .topLeading)
                    .background(scheme == .dark ? Color.black : Color.white)
                    .environment(\.colorScheme, scheme)
                let renderer = ImageRenderer(content: view)
                renderer.scale = 3
                let image = try XCTUnwrap(renderer.uiImage)
                let shot = XCTAttachment(image: image)
                shot.name = "widget-\(family)-\(scheme)"
                shot.lifetime = .keepAlways
                add(shot)
            }
        }
    }
}
