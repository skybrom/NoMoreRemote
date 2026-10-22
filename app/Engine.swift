import Foundation
import SwiftUI

/// Finds the background engine, starts it if it is not running, and talks to it over loopback.
/// The engine (Python) holds the connection to the Nova, listens to MIDI and runs the launchers;
/// this app is only its control panel, so quitting the app never stops your keys from working.
@MainActor
final class Engine: ObservableObject {
    static let shared = Engine()
    @Published var menu: MenuResponse?
    @Published var state: StateResponse?
    @Published var midi: MidiInfo?
    @Published var engineText = "Looking for the engine…"
    @Published var toast: String?
    @Published var midiSheetOpen = false
    @Published var showTechnicalLog = UserDefaults.standard.bool(forKey: "showTechnicalLog") {
        didSet { UserDefaults.standard.set(showTechnicalLog, forKey: "showTechnicalLog") }
    }
    /// Starred tiles, kept as "Section|Label" so they survive a reload of the menu.
    @Published var favouriteKeys: [String] = UserDefaults.standard.stringArray(forKey: "favourites") ?? []

    let supportDir: URL
    private var bridge: BridgeInfo?
    private var lastEpoch = -1
    private var lastMidiVersion = -1
    private var lastStartAttempt = Date.distantPast
    private var polling: Task<Void, Never>?
    private var toastTask: Task<Void, Never>?
    private var launcherFiles: [String: URL] = [:]
    private var launcherPending = Set<String>()
    private var launcherEpoch = -1

    init() {
        if let custom = ProcessInfo.processInfo.environment["TRINNOV_SUPPORT_DIR"] {
            supportDir = URL(fileURLWithPath: custom)
        } else {
            supportDir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/TrinnovFunctions")
        }
    }

    var connected: Bool { (state?.connected ?? false) && (menu?.connected ?? false) }
    var sections: [MenuSection] { menu?.sections ?? [] }

    static let favouritesKey = "favourites"
    /// The Favourites page first, then the Nova's own sections.
    var pages: [MenuSection] {
        let favourites = favouriteKeys.compactMap { key -> MenuItem? in
            for s in sections { for i in s.items where Engine.favouriteKey(s, i) == key { return i } }
            return nil
        }
        let note = favourites.isEmpty ? "Star a tile (the ☆ that appears when you point at it) and it shows up here." : "Your starred functions, from every section."
        return [MenuSection(key: Engine.favouritesKey, title: "Favourites", note: note, items: favourites)] + sections
    }
    static func favouriteKey(_ section: MenuSection, _ item: MenuItem) -> String { "\(section.title)|\(item.label)" }
    func isFavourite(_ item: MenuItem) -> Bool {
        guard let s = sections.first(where: { $0.items.contains(item) }) else { return false }
        return favouriteKeys.contains(Engine.favouriteKey(s, item))
    }
    func toggleFavourite(_ item: MenuItem) {
        guard let s = sections.first(where: { $0.items.contains(item) }) else { return }
        let key = Engine.favouriteKey(s, item)
        if let at = favouriteKeys.firstIndex(of: key) { favouriteKeys.remove(at: at) } else { favouriteKeys.append(key) }
        UserDefaults.standard.set(favouriteKeys, forKey: "favourites")
    }
    var readOnly: Bool { state?.read_only ?? menu?.read_only ?? false }

    // MARK: finding and starting the engine

    func loadBridge() -> BridgeInfo? {
        guard let data = try? Data(contentsOf: supportDir.appendingPathComponent("bridge.json")),
              let info = try? JSONDecoder().decode(BridgeInfo.self, from: data),
              kill(info.pid, 0) == 0 else { return nil }
        return info
    }

    /// The engine folder shipped inside this app.
    var bundledEngine: URL? { Bundle.main.resourceURL?.appendingPathComponent("engine") }

    /// True when the installed copy of the engine is the one in this app (otherwise it is reinstalled once).
    func installedEngineIsCurrent() -> Bool {
        guard let shipped = bundledEngine else { return true }
        let installed = supportDir.appendingPathComponent("cli")
        for name in ["server.py", "trinnov.py", "ui/index.html", "midi/trinnov-midi.swift", "data/functions_tree.json"] {
            guard let a = try? Data(contentsOf: shipped.appendingPathComponent(name)),
                  let b = try? Data(contentsOf: installed.appendingPathComponent(name)), a == b else { return false }
        }
        return true
    }

    private func runEngineInstaller() async -> Bool {
        guard let shipped = bundledEngine else { return false }
        let script = shipped.appendingPathComponent("server.py").path
        return await withCheckedContinuation { continuation in
            DispatchQueue.global().async {
                let p = Process()
                p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
                p.arguments = [script, "--install-service"]
                p.standardOutput = FileHandle.nullDevice
                p.standardError = FileHandle.nullDevice
                do { try p.run(); p.waitUntilExit(); continuation.resume(returning: p.terminationStatus == 0) }
                catch { continuation.resume(returning: false) }
            }
        }
    }

    func ensureEngine() async {
        if ProcessInfo.processInfo.environment["TRINNOV_NO_ENGINE_START"] != nil {
            bridge = loadBridge()
            engineText = bridge == nil ? "No engine is running." : "Engine running."
            return
        }
        if bridge == nil { bridge = loadBridge() }
        if bridge != nil, installedEngineIsCurrent() { engineText = "Engine running."; return }
        guard Date().timeIntervalSince(lastStartAttempt) > 20 else { return }
        lastStartAttempt = Date()
        engineText = bridge == nil ? "Starting the engine…" : "Updating the engine…"
        let ok = await runEngineInstaller()
        for _ in 0..<40 {                                    // up to ~20 s for it to come up
            if let info = loadBridge() { bridge = info; engineText = "Engine running."; return }
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
        engineText = ok ? "The engine did not start. See ~/Library/Logs/TrinnovFunctionsService.log" : "Could not install the engine (needs Python 3: macOS can install Apple's developer tools)."
    }

    // MARK: talking to it

    enum EngineError: Error { case notRunning, bad(String) }

    func request<T: Decodable>(_ path: String, body: [String: Any]? = nil, as type: T.Type = T.self) async throws -> T {
        guard let info = bridge else { throw EngineError.notRunning }
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(info.port)\(path)")!, timeoutInterval: 30)
        request.setValue(info.token, forHTTPHeaderField: "X-Token")
        if let body = body {
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, http.statusCode != 200 { throw EngineError.bad("HTTP \(http.statusCode)") }
        return try JSONDecoder().decode(T.self, from: data)
    }

    func start() {
        guard polling == nil else { return }
        polling = Task { [weak self] in
            while !Task.isCancelled {
                await self?.poll()
                try? await Task.sleep(nanoseconds: 1_500_000_000)
            }
        }
    }

    func poll() async {
        if bridge == nil || loadBridge()?.token != bridge?.token {
            bridge = nil
            await ensureEngine()
        }
        guard bridge != nil else { state = nil; menu = nil; return }
        do {
            let st: StateResponse = try await request("/api/state")
            state = st
            if !st.connected { return }
            if menu == nil || menu?.connected != true || menu?.epoch != st.epoch {
                menu = try await request("/api/menu")
            }
            if let m = st.midi, m.version != lastMidiVersion {
                let first = lastMidiVersion == -1
                lastMidiVersion = m.version
                if !first { menu = try await request("/api/menu") }       // badges
                if midiSheetOpen { midi = try await request("/api/midi") }
            }
        } catch {
            bridge = nil                                       // the engine went away: look for it again next time
            state = nil
        }
    }

    func refreshMidi() async {
        midi = try? await request("/api/midi")
    }

    func showToast(_ text: String) {
        toast = text
        toastTask?.cancel()
        toastTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 2_600_000_000)
            if !Task.isCancelled { self?.toast = nil }
        }
    }

    // MARK: actions

    func post(_ path: String, _ body: [String: Any] = [:]) async -> ActionResult? {
        try? await request(path, body: body)
    }

    func press(_ item: MenuItem, confirmed: Bool = false) async -> ActionResult? {
        let r = await post("/api/press", ["id": item.id, "confirm": confirmed])
        if let r = r, r.needs_confirm != true {
            showToast(r.ok == true ? "Sent: \(item.label)" : (r.error ?? "Failed"))
        }
        await poll()
        return r
    }

    func reload() async {
        menu = nil
        menu = try? await request("/api/reload", body: [:])
        await poll()
    }

    func setReadOnly(_ on: Bool) async {
        _ = await post("/api/readonly", ["on": on])
        await poll()
    }

    func volume(_ delta: Double) async {
        let r = await post("/api/volume", ["delta": delta])
        if r?.ok != true { showToast(r?.error ?? "Failed") }
        await poll()
    }

    func toggleMute() async {
        _ = await post("/api/mute")
        await poll()
    }

    func makeLauncher(_ item: MenuItem) async {
        let r = await post("/api/launcher", ["id": item.id])
        showToast(r?.ok == true ? "Launcher made: drag it from Finder onto a Stream Deck key" : (r?.error ?? "Failed"))
    }

    /// Makes (once) the launcher app of a tile quietly, so dragging the tile can hand it to the Stream Deck app.
    func prepareLauncher(_ item: MenuItem) {
        if launcherEpoch != menu?.epoch { launcherFiles = [:]; launcherEpoch = menu?.epoch ?? -1 }
        guard launcherFiles[item.id] == nil, !launcherPending.contains(item.id) else { return }
        launcherPending.insert(item.id)
        Task {
            let r = await post("/api/launcher", ["id": item.id, "reveal": false])
            launcherPending.remove(item.id)
            if let path = r?.paths?.first, FileManager.default.fileExists(atPath: path) { launcherFiles[item.id] = URL(fileURLWithPath: path) }
        }
    }

    func launcherFile(_ item: MenuItem) -> URL? { launcherFiles[item.id] }

    func makeAllLaunchers() async {
        let r = await post("/api/launcher", ["all": true])
        showToast(r?.ok == true ? "\(r?.count ?? 0) launchers made in \(r?.dir ?? "")" : (r?.error ?? "Failed"))
    }

    func openLauncherFolder() async { _ = await post("/api/launcher", ["open_folder": true]) }

    func learn(_ item: MenuItem) async {
        let r = await post("/api/midi/learn", ["id": item.id])
        if r?.ok != true { showToast(r?.error ?? "Failed") }
        await poll()
    }

    func unlearn(_ item: MenuItem) async {
        _ = await post("/api/midi/unlearn", ["id": item.id])
        showToast("MIDI mapping removed: \(item.label)")
        menu = try? await request("/api/menu")
        await refreshMidi()
    }

    func cancelLearn() async { _ = await post("/api/midi/learn", ["cancel": true]); await poll() }

    func mapEverything() async {
        let r = await post("/api/midi/default")
        showToast(r?.ok == true ? "\(r?.count ?? 0) functions mapped to notes starting at 36 (channel 1)" : (r?.error ?? "Failed"))
        await refreshMidi()
        menu = try? await request("/api/menu")
    }

    func clearMidi() async {
        _ = await post("/api/midi/clear")
        showToast("All MIDI mappings removed")
        await refreshMidi()
        menu = try? await request("/api/menu")
    }

    func setTrigger(_ binding: MidiBinding, _ trigger: String) async {
        _ = await post("/api/midi/trigger", ["section": binding.section, "selector": binding.selector, "trigger": trigger])
        await refreshMidi()
    }

    func restartEngine() async {
        lastStartAttempt = .distantPast
        bridge = nil
        state = nil
        menu = nil
        engineText = "Restarting the engine…"
        _ = await runEngineInstaller()
        await ensureEngine()
    }
}
