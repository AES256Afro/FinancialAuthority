// The widget extension's entry point, kept apart from the glance so a test can build the glance on its own.
import SwiftUI
import WidgetKit

@main
struct GlanceWidgets: WidgetBundle {
    var body: some Widget {
        GlanceWidget()
    }
}
