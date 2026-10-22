import SwiftUI
import AppKit

/// Trinnov Functions: a menu bar app. The icon's menu has Settings and Quit; Settings opens the window.
@main
struct TrinnovFunctionsApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    private let engine = Engine.shared

    var body: some Scene {
        Window("Trinnov Functions", id: "settings") {
            MainWindow()
                .environmentObject(engine)
                .frame(minWidth: 1000, minHeight: 640)
                .preferredColorScheme(.dark)
        }
        .defaultSize(width: 1120, height: 720)

        MenuBarExtra {
            MenuContent()
        } label: {
            StatusIcon().environmentObject(engine)
        }
        .menuBarExtraStyle(.menu)
    }
}

struct MenuContent: View {
    @Environment(\.openWindow) private var openWindow
    var body: some View {
        Button("Settings…") {
            openWindow(id: "settings")
            NSApp.activate(ignoringOtherApps: true)
        }.keyboardShortcut(",")
        Divider()
        Button("Quit") { NSApp.terminate(nil) }.keyboardShortcut("q")
    }
}

/// The menu bar icon: solid when the Nova is connected, an outline otherwise.
struct StatusIcon: View {
    @EnvironmentObject var engine: Engine
    var body: some View {
        Image(systemName: engine.connected ? "slider.horizontal.3" : "exclamationmark.triangle")
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        Task { @MainActor in Engine.shared.start() }              // keep the icon's state live even with no window
        if !UserDefaults.standard.bool(forKey: "showWindowAtLaunchSet") {
            UserDefaults.standard.set(true, forKey: "showWindowAtLaunch")      // first run: show the window
            UserDefaults.standard.set(true, forKey: "showWindowAtLaunchSet")
        }
        if !UserDefaults.standard.bool(forKey: "showWindowAtLaunch") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { NSApp.windows.filter { $0.title == "Trinnov Functions" }.forEach { $0.close() } }
        } else {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { NSApp.activate(ignoringOtherApps: true) }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}
