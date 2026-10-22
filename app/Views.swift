import SwiftUI
import ServiceManagement

// MARK: the window

enum Sheet: String, Identifiable {
    case midi, launchers, settings
    var id: String { rawValue }
}

struct MainWindow: View {
    @EnvironmentObject var engine: Engine
    @State private var selected: String = ""
    @State private var sheet: Sheet?
    @State private var confirming: MenuItem?

    private var current: MenuSection? {
        engine.pages.first { $0.key == selected } ?? engine.pages.first
    }

    var body: some View {
        VStack(spacing: 0) {
            HeaderBar(sheet: $sheet)
            titleBar
            banners
            if engine.connected, let section = current {
                HStack(alignment: .top, spacing: 14) {
                    Sidebar(selected: Binding(get: { current?.key ?? "" }, set: { selected = $0 }))
                    TilesPane(section: section, confirming: $confirming)
                    LogPane()
                }
                .padding(.horizontal, 16).padding(.vertical, 10)
                VolumeBar()
            } else {
                DisconnectedView()
            }
        }
        .background(Theme.bg)
        .overlay(alignment: .bottom) { ToastView() }
        .sheet(item: $sheet) { which in
            switch which {
            case .midi: MidiSheet()
            case .launchers: LaunchersSheet()
            case .settings: AppSettingsSheet()
            }
        }
        .alert("Are you sure?", isPresented: Binding(get: { confirming != nil }, set: { if !$0 { confirming = nil } })) {
            Button("Cancel", role: .cancel) { confirming = nil }
            Button(confirming?.label ?? "Do it", role: .destructive) {
                if let item = confirming { Task { _ = await engine.press(item, confirmed: true) } }
                confirming = nil
            }
        } message: {
            Text("\(confirming?.label ?? "This") will act on the Nova right away.")
        }
        .onChange(of: sheet) { newValue in
            engine.midiSheetOpen = (newValue == .midi)
            if newValue == .midi { Task { await engine.refreshMidi() } }
        }
        .task { engine.start() }
    }

    private var titleBar: some View {
        HStack(spacing: 18) {
            Text("FUNCTIONS").font(.system(size: 13, weight: .semibold)).tracking(1)
            Rectangle().fill(Theme.accent).frame(width: 70, height: 2)
            Text("Everything La Remote can do").foregroundColor(Theme.legend)
        }
        .padding(.horizontal, 24).padding(.vertical, 9)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel))
        .padding(.top, 12).padding(.bottom, 6)
    }

    @ViewBuilder private var banners: some View {
        if engine.readOnly {
            Banner(text: "Read-only mode: buttons show what they would send, but nothing is sent to the Nova.", tint: Theme.warn, fill: Color(hex: 0x3A2F22))
        }
        if let learning = engine.state?.midi?.learning {
            HStack {
                Text("Listening: press a key, pad or button on your MIDI controller to map it to \(learning.section) / \(learning.label)…")
                Button("Cancel") { Task { await engine.cancelLearn() } }.buttonStyle(FlatButtonStyle())
            }
            .foregroundColor(Color(hex: 0xCDE8A0)).padding(.horizontal, 16).padding(.vertical, 8)
            .background(RoundedRectangle(cornerRadius: 10).fill(Color(hex: 0x2C3A1F)))
            .padding(.bottom, 6)
        }
    }
}

struct Banner: View {
    let text: String
    let tint: Color
    let fill: Color
    var body: some View {
        Text(text).foregroundColor(tint).padding(.horizontal, 16).padding(.vertical, 8)
            .background(RoundedRectangle(cornerRadius: 10).fill(fill)).padding(.bottom, 6)
    }
}

// MARK: header

struct HeaderBar: View {
    @EnvironmentObject var engine: Engine
    @Binding var sheet: Sheet?

    var body: some View {
        HStack(spacing: 16) {
            Text("N O V A").font(.system(size: 22, weight: .ultraLight)).tracking(2)
            HStack(spacing: 8) {
                Circle().fill(engine.connected ? Theme.accent : Theme.danger).frame(width: 9, height: 9)
                    .shadow(color: engine.connected ? Theme.accent : .clear, radius: 4)
                Text(statusText).font(.system(size: 12)).foregroundColor(Theme.legend).lineLimit(1)
            }
            Spacer()
            Picker("", selection: Binding(get: { engine.readOnly }, set: { v in Task { await engine.setReadOnly(v) } })) {
                Text("Live").tag(false)
                Text("Read-only").tag(true)
            }
            .pickerStyle(.segmented).frame(width: 170).labelsHidden()
            .help("Read-only: look, never send")
            Button("MIDI…") { sheet = .midi }.buttonStyle(FlatButtonStyle()).accessibilityLabel("MIDI")
            Button("Stream Deck…") { sheet = .launchers }.buttonStyle(FlatButtonStyle()).accessibilityLabel("Stream Deck")
            Button("Reload from Nova") { Task { await engine.reload() } }.buttonStyle(FlatButtonStyle()).accessibilityLabel("Reload from Nova")
            Button { sheet = .settings } label: { Image(systemName: "gearshape") }.buttonStyle(FlatButtonStyle()).help("App settings").accessibilityLabel("App settings")
        }
        .padding(.horizontal, 22).padding(.vertical, 10)
        .background(LinearGradient(colors: [Color(hex: 0x2F3338), Color(hex: 0x232528)], startPoint: .top, endPoint: .bottom))
    }

    private var statusText: String {
        if let info = engine.menu?.info, engine.connected {
            return "\((info.product ?? "Trinnov").uppercased()) #\(info.serial.map(String.init) ?? "?") · \(info.firmware ?? "firmware ?") · \(engine.menu?.nova_ip ?? "")"
        }
        return engine.state?.error ?? engine.menu?.error ?? engine.engineText
    }
}

// MARK: sidebar, tiles, log

struct Sidebar: View {
    @EnvironmentObject var engine: Engine
    @Binding var selected: String

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 3) {
                Text("FUNCTIONS").font(.system(size: 11, weight: .semibold)).tracking(1.2).foregroundColor(Theme.legend)
                    .padding(.horizontal, 10).padding(.top, 6).padding(.bottom, 8)
                ForEach(engine.pages) { section in
                    Button { selected = section.key } label: {
                        HStack {
                            if section.key == Engine.favouritesKey { Image(systemName: "star.fill").font(.system(size: 11)) }
                            Text(section.title)
                            Spacer()
                            if !section.items.isEmpty { Text("\(section.items.count)").font(.system(size: 11)).foregroundColor(Theme.off) }
                        }
                        .padding(.horizontal, 12).padding(.vertical, 9)
                        .foregroundColor(selected == section.key ? Theme.accent : Theme.text)
                        .background(RoundedRectangle(cornerRadius: 9).fill(selected == section.key ? Theme.tile : Color.clear))
                        .overlay(alignment: .leading) {
                            if selected == section.key { Rectangle().fill(Theme.accent).frame(width: 3).padding(.vertical, 4) }
                        }
                        .contentShape(Rectangle())
                    }.buttonStyle(.plain).accessibilityLabel(section.title)
                }
            }.padding(8)
        }
        .frame(width: 210)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel))
    }
}

struct TilesPane: View {
    @EnvironmentObject var engine: Engine
    let section: MenuSection
    @Binding var confirming: MenuItem?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 4) {
                Text(section.title).font(.system(size: 20, weight: .semibold))
                Text(section.note ?? " ").font(.system(size: 13)).foregroundColor(Theme.legend).padding(.bottom, 10)
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 160), spacing: 12)], spacing: 12) {
                    ForEach(section.items) { item in
                        TileView(item: item, state: engine.state?.items?[item.id],
                                 learning: engine.state?.midi?.learning?.label == item.label && engine.state?.midi?.learning?.section == (engine.sections.first { $0.items.contains(item) }?.title ?? section.title)) {
                            if item.needsConfirm { confirming = item }
                            else { Task { _ = await engine.press(item) } }
                        }
                    }
                }
            }.padding(16)
        }
        .frame(maxWidth: .infinity)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel))
    }
}

struct TileView: View {
    @EnvironmentObject var engine: Engine
    let item: MenuItem
    let state: ItemState?
    let learning: Bool
    let press: () -> Void
    @State private var hovering = false

    private var on: Bool { state?.on == true }
    private var unavailable: Bool { state?.unavailable == true }
    private var favourite: Bool { engine.isFavourite(item) }

    var body: some View {
        ZStack(alignment: .topLeading) {
            Button(action: press) {
                VStack(spacing: 4) {
                    Text(item.label).font(.system(size: 15, weight: .semibold)).foregroundColor(on ? Theme.accent : Theme.text).lineLimit(1)
                    Text(subText).font(.system(size: 12)).foregroundColor(Theme.legend).lineLimit(1)
                }
                .padding(.horizontal, 10).padding(.vertical, 12)
            }
            .buttonStyle(TileButtonStyle(on: on, learning: learning, hovering: hovering, disabled: unavailable, viewOnly: item.viewOnly))
            .disabled(item.viewOnly || unavailable)
            .help(unavailable ? "This Nova does not have this function" : "")
            .allowsHitTesting(false)
            .accessibilityHidden(true)

            // takes the mouse: a click presses the tile, a drag hands the function's launcher app to whatever it is dropped on
            DragSource(label: item.label, value: on ? "on" : "off", enabled: !(item.viewOnly || unavailable), onClick: press, file: { engine.launcherFile(item) })

            // the LED
            Circle().fill(on ? Theme.accent : Color(hex: 0x3B3F44)).frame(width: 9, height: 9)
                .shadow(color: on ? Theme.accent : .clear, radius: 4)
                .opacity(item.momentary == true ? 0 : 1)
                .frame(maxWidth: .infinity, alignment: .topTrailing).padding(9).allowsHitTesting(false)

            if let badge = item.midi {
                Text(badge).font(.system(size: 10, weight: .semibold, design: .monospaced)).foregroundColor(Theme.accentDim)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing).padding(8).allowsHitTesting(false)
            }
            if hovering && !item.viewOnly {
                HStack(spacing: 6) {
                    PillButton(title: "+ Deck") { Task { await engine.makeLauncher(item) } }
                    if !item.needsConfirm { PillButton(title: "MIDI", tint: Theme.ok) { Task { await engine.learn(item) } } }
                }
                .padding(8).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
            }
            if (hovering || favourite) && !item.viewOnly {
                Button { engine.toggleFavourite(item) } label: {
                    Image(systemName: favourite ? "star.fill" : "star").font(.system(size: 12))
                        .foregroundColor(favourite ? Theme.warn : Theme.legend).padding(9).contentShape(Rectangle())
                }
                .buttonStyle(.plain).help(favourite ? "Remove from Favourites" : "Add to Favourites")
                .accessibilityLabel(favourite ? "Remove \(item.label) from Favourites" : "Add \(item.label) to Favourites")
            }
        }
        .contextMenu {
            if !item.viewOnly {
                Button(favourite ? "Remove from Favourites" : "Add to Favourites") { engine.toggleFavourite(item) }
                Button("Show launcher in Finder") { Task { await engine.makeLauncher(item) } }
                if !item.needsConfirm { Button("Map to MIDI…") { Task { await engine.learn(item) } } }
                if item.midi != nil { Button("Delete MIDI mapping") { Task { await engine.unlearn(item) } } }
            }
        }
        .onHover { inside in
            hovering = inside
            if inside && !item.viewOnly && !unavailable { engine.prepareLauncher(item) }
        }
    }

    private var subText: String {
        if item.viewOnly, let info = state?.info, !info.isEmpty { return info }
        if item.type == "toggle-preset", let info = state?.info, !info.isEmpty { return "back to: \(info)" }
        return item.sub ?? " "
    }
}

struct LogPane: View {
    @EnvironmentObject var engine: Engine
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("SENT TO THE NOVA").font(.system(size: 11, weight: .semibold)).tracking(1.2).foregroundColor(Theme.legend)
            ScrollView {
                VStack(alignment: .leading, spacing: 5) {
                    ForEach(Array((engine.state?.log ?? []).reversed().enumerated()), id: \.offset) { _, entry in
                        HStack(alignment: .top, spacing: 0) {
                            Rectangle().fill(color(entry.kind)).frame(width: 3)
                            VStack(alignment: .leading, spacing: 3) {
                                HStack(alignment: .firstTextBaseline, spacing: 6) {
                                    Text(entry.t).font(.system(size: 10, design: .monospaced)).foregroundColor(Theme.legend)
                                    Text(entry.text).font(.system(size: 12, weight: .medium))
                                        .foregroundColor(entry.kind == "error" ? Color(hex: 0xFFB3B6) : (entry.kind == "blocked" ? Theme.warn : Theme.text))
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                                if engine.showTechnicalLog, let detail = entry.detail, !detail.isEmpty {
                                    Text(detail).font(.system(size: 10, design: .monospaced)).foregroundColor(Theme.legend)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                            .padding(6).frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .background(RoundedRectangle(cornerRadius: 7).fill(Color(hex: 0x232528)))
                    }
                }
            }
            Button(engine.showTechnicalLog ? "Hide log" : "Show log") { engine.showTechnicalLog.toggle() }
                .buttonStyle(.plain).font(.system(size: 11)).foregroundColor(Theme.legend)
                .help("Show the exact message sent to the Nova under each line")
        }
        .padding(14).frame(width: 290)
        .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel))
    }
    private func color(_ kind: String) -> Color {
        switch kind { case "sent": return Theme.accent; case "error": return Theme.danger; case "blocked": return Theme.warn; default: return Theme.off }
    }
}

// MARK: footer, toast, disconnected

struct VolumeBar: View {
    @EnvironmentObject var engine: Engine
    var body: some View {
        HStack(spacing: 14) {
            roundButton("minus") { Task { await engine.volume(-(engine.state?.volume_step ?? 1)) } }.accessibilityLabel("Volume down")
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(volumeText).font(.system(size: 26, weight: .light)).monospacedDigit()
                Text("dB").font(.system(size: 13)).foregroundColor(Theme.legend)
            }.frame(minWidth: 120)
            roundButton("plus") { Task { await engine.volume(engine.state?.volume_step ?? 1) } }.accessibilityLabel("Volume up")
            Button(engine.state?.mute == true ? "Muted" : "Mute") { Task { await engine.toggleMute() } }
                .buttonStyle(FlatButtonStyle(prominent: engine.state?.mute == true, danger: true)).accessibilityLabel("Mute")
        }
        .padding(.vertical, 12).frame(maxWidth: .infinity)
        .background(LinearGradient(colors: [Color(hex: 0x232528), Color(hex: 0x1F2022)], startPoint: .top, endPoint: .bottom))
    }
    private var volumeText: String {
        guard let v = engine.state?.volume else { return "--" }
        return v <= -96 ? "−∞" : String(format: "%.1f", v)
    }
    private func roundButton(_ symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 17, weight: .medium)).frame(width: 46, height: 46)
                .background(Circle().fill(Theme.tile)).overlay(Circle().stroke(Theme.edge))
        }.buttonStyle(.plain)
    }
}

struct ToastView: View {
    @EnvironmentObject var engine: Engine
    var body: some View {
        if let text = engine.toast {
            Text(text).font(.system(size: 13)).padding(.horizontal, 16).padding(.vertical, 9)
                .background(RoundedRectangle(cornerRadius: 10).fill(Color(hex: 0x1D1F21)))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.edge)).padding(.bottom, 80)
                .transition(.opacity)
        }
    }
}

struct DisconnectedView: View {
    @EnvironmentObject var engine: Engine
    var body: some View {
        VStack(spacing: 12) {
            Spacer()
            Text("Can’t reach your Nova").font(.system(size: 22, weight: .semibold))
            Text(engine.state?.error ?? engine.menu?.error ?? engine.engineText).foregroundColor(Theme.legend)
            Text("Check that the Nova is on and on the same network as this Mac. The app keeps looking by itself.").foregroundColor(Theme.legend)
            Button("Try again") { Task { await engine.reload() } }.buttonStyle(FlatButtonStyle())
            Spacer()
        }.frame(maxWidth: .infinity)
    }
}

// MARK: sheets

struct SheetFrame<Content: View>: View {
    @Environment(\.dismiss) private var dismiss
    let title: String
    @ViewBuilder let content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack { Text(title).font(.system(size: 18, weight: .semibold)); Spacer(); Button("Close") { dismiss() }.buttonStyle(FlatButtonStyle()) }
            content
        }
        .padding(22).frame(minWidth: 640, minHeight: 360).background(Theme.panel)
    }
}

struct LaunchersSheet: View {
    @EnvironmentObject var engine: Engine
    var body: some View {
        SheetFrame(title: "Stream Deck launchers") {
            Text("Each launcher is a small app that runs one function on your Nova. Drag it from Finder onto a Stream Deck key (Stream Deck makes an Open action from it). Hover a tile and click + Deck to make just that one.")
            Text("Power Off and Reboot launchers always ask first. Network entries have none. A launcher plays a soft sound on success and a different one on failure; every run is recorded in ~/Library/Logs/TrinnovLaunchers.log.")
                .font(.system(size: 12)).foregroundColor(Theme.legend)
            HStack {
                Spacer()
                Button("Open folder") { Task { await engine.openLauncherFolder() } }.buttonStyle(FlatButtonStyle())
                Button("Make all launchers") { Task { await engine.makeAllLaunchers() } }.buttonStyle(FlatButtonStyle(prominent: true))
            }
        }
    }
}

struct MidiSheet: View {
    @EnvironmentObject var engine: Engine
    var body: some View {
        SheetFrame(title: "MIDI") {
            Text("Any controller, or a Stream Deck MIDI plugin, can run every function. Send MIDI to the port “Trinnov Functions”, or from any controller already connected to this Mac. A press is a note on, a program change, or a CC of 64 or more.")
                .font(.system(size: 13))
            if let m = engine.midi {
                Text("MIDI helper: \(m.status). Ports heard: \(m.ports.isEmpty ? "none yet" : m.ports.joined(separator: ", ")).")
                    .font(.system(size: 12)).foregroundColor(Theme.legend)
                Text("Messages heard (live)").font(.system(size: 13, weight: .semibold))
                ScrollView {
                    VStack(alignment: .leading, spacing: 3) {
                        if m.history.isEmpty { Text("Nothing heard yet. Press a key on your controller.").foregroundColor(Theme.legend) }
                        ForEach(Array(m.history.enumerated()), id: \.offset) { _, e in
                            Text("\(e.at)  \(e.text)  value \(e.value)  [\(e.raw ?? "")]  \(e.port) → \(e.result ?? "")")
                                .font(.system(size: 11, design: .monospaced)).frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }.frame(height: 110)
                Text("Mapped functions").font(.system(size: 13, weight: .semibold))
                ScrollView {
                    VStack(alignment: .leading, spacing: 6) {
                        if m.bindings.isEmpty { Text("No function is mapped yet. Use “Map every function automatically”, or hover a tile and click MIDI.").foregroundColor(Theme.legend) }
                        ForEach(m.bindings) { b in
                            HStack {
                                Text("\(b.section) / \(b.label)")
                                Spacer()
                                Text(b.text).foregroundColor(Theme.legend)
                                if b.msg.type == "cc" {
                                    Picker("", selection: Binding(get: { b.trigger ?? "press" }, set: { v in Task { await engine.setTrigger(b, v) } })) {
                                        Text("press (64 and up)").tag("press")
                                        Text("every value (toggle button)").tag("any")
                                    }.labelsHidden().frame(width: 210)
                                }
                            }
                        }
                    }
                }.frame(minHeight: 100)
            } else {
                Text("Reading MIDI status…").foregroundColor(Theme.legend)
            }
            Text("Power Off and Reboot cannot be mapped (a stray note would switch your Nova off). A Stream Deck MIDI button must have its note and velocity typed into its settings, so they are saved.")
                .font(.system(size: 12)).foregroundColor(Theme.legend)
            HStack {
                Spacer()
                Button("Clear all") { Task { await engine.clearMidi() } }.buttonStyle(FlatButtonStyle())
                Button("Map every function automatically") { Task { await engine.mapEverything() } }.buttonStyle(FlatButtonStyle(prominent: true))
            }
        }
        .task {
            await engine.refreshMidi()
        }
        .onReceive(Timer.publish(every: 1, on: .main, in: .common).autoconnect()) { _ in Task { await engine.refreshMidi() } }
    }
}

struct AppSettingsSheet: View {
    @EnvironmentObject var engine: Engine
    @AppStorage("showWindowAtLaunch") private var showWindowAtLaunch = true
    @State private var loginEnabled = SMAppService.mainApp.status == .enabled
    @State private var loginError: String?
    @StateObject private var autoOff = AutoOff.shared

    var body: some View {
        SheetFrame(title: "App settings") {
            Text("Trinnov shutdown").font(.system(size: 13, weight: .semibold))
            HStack(spacing: 8) {
                Toggle("Automatically shut down the Trinnov when this Mac turns off", isOn: Binding(get: { autoOff.installed }, set: { autoOff.setEnabled($0) }))
                    .disabled(autoOff.busy)
                if autoOff.busy { ProgressView().controlSize(.small) }
            }
            Text("Also when it restarts or logs out. Turning it on asks for your Mac password once, to install a small system helper (macOS cuts programs off from the network early in a restart).")
                .font(.system(size: 12)).foregroundColor(Theme.legend)
            if autoOff.installed && !autoOff.upToDate {
                HStack {
                    Text("A newer version of the helper is available.").font(.system(size: 12)).foregroundColor(Theme.warn)
                    Button("Update") { autoOff.update() }.buttonStyle(FlatButtonStyle()).disabled(autoOff.busy)
                }
            }
            if let m = autoOff.message { Text(m).font(.system(size: 12)).foregroundColor(Theme.warn) }
            if autoOff.installed, let last = autoOff.lastEvent { Text("Last: \(last)").font(.system(size: 11, design: .monospaced)).foregroundColor(Theme.legend) }
            Divider()
            Toggle("Open this app at login (so the menu bar icon is always there)", isOn: Binding(get: { loginEnabled }, set: { setLogin($0) }))
            if let e = loginError { Text(e).font(.system(size: 12)).foregroundColor(Theme.warn) }
            Toggle("Show this window when the app starts", isOn: $showWindowAtLaunch)
            Divider()
            Text("Background engine").font(.system(size: 13, weight: .semibold))
            Text("The engine keeps the connection to your Nova, listens to MIDI and runs the Stream Deck launchers, even when this app is closed. \(engine.engineText)")
                .font(.system(size: 12)).foregroundColor(Theme.legend)
            HStack {
                Button("Restart the engine") { Task { await engine.restartEngine() } }.buttonStyle(FlatButtonStyle())
                Spacer()
            }
            Text("Logs: ~/Library/Logs/TrinnovFunctionsService.log (engine) and ~/Library/Logs/TrinnovLaunchers.log (launchers and commands).")
                .font(.system(size: 12)).foregroundColor(Theme.legend)
        }
    }

    private func setLogin(_ on: Bool) {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            loginError = nil
        } catch {
            loginError = "macOS did not allow it: \(error.localizedDescription). You can also add the app in System Settings → General → Login Items."
        }
        loginEnabled = SMAppService.mainApp.status == .enabled
    }
}

/// Click = press; drag = drag the launcher app file (the Stream Deck app turns a dropped app into an "Open" key).
struct DragSource: NSViewRepresentable {
    let label: String
    let value: String
    let enabled: Bool
    let onClick: () -> Void
    let file: () -> URL?

    func makeNSView(context: Context) -> NSView { Source() }
    func updateNSView(_ view: NSView, context: Context) {
        guard let v = view as? Source else { return }
        v.enabled = enabled; v.onClick = onClick; v.file = file
        v.setAccessibilityLabel(label); v.setAccessibilityValue(value)
    }

    final class Source: NSView, NSDraggingSource {
        var enabled = true
        var onClick: () -> Void = {}
        var file: () -> URL? = { nil }
        private var down: NSPoint?
        private var dragged = false

        override var acceptsFirstResponder: Bool { false }
        override func isAccessibilityElement() -> Bool { true }
        override func accessibilityRole() -> NSAccessibility.Role? { .button }
        override func accessibilityPerformPress() -> Bool { guard enabled else { return false }; onClick(); return true }
        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
        override func mouseDown(with event: NSEvent) { down = event.locationInWindow; dragged = false }

        override func mouseDragged(with event: NSEvent) {
            guard enabled, !dragged, let start = down, let url = file() else { return }
            let p = event.locationInWindow
            guard hypot(p.x - start.x, p.y - start.y) > 4 else { return }
            dragged = true
            let item = NSDraggingItem(pasteboardWriter: url as NSURL)
            let icon = NSWorkspace.shared.icon(forFile: url.path)
            icon.size = NSSize(width: 48, height: 48)
            let at = convert(p, from: nil)
            item.setDraggingFrame(NSRect(x: at.x - 24, y: at.y - 24, width: 48, height: 48), contents: icon)
            beginDraggingSession(with: [item], event: event, source: self)
        }

        override func mouseUp(with event: NSEvent) {
            defer { down = nil }
            guard enabled, !dragged, bounds.contains(convert(event.locationInWindow, from: nil)) else { return }
            onClick()
        }

        func draggingSession(_ session: NSDraggingSession, sourceOperationMaskFor context: NSDraggingContext) -> NSDragOperation { .copy }
    }
}
