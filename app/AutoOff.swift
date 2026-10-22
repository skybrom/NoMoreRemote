import Foundation
import SwiftUI

/// "Switch the Trinnov off when this Mac turns off": installs / removes the Auto Shutdown helpers
/// (a login agent that hears the shutdown and a system helper that sends it; the second needs the Mac password once).
@MainActor
final class AutoOff: ObservableObject {
    static let shared = AutoOff()
    @Published var installed = false
    @Published var upToDate = true
    @Published var busy = false
    @Published var message: String?
    @Published var lastEvent: String?

    private let home = FileManager.default.homeDirectoryForCurrentUser
    private var agentPlist: URL { home.appendingPathComponent("Library/LaunchAgents/com.trinnov.autoshutdown.plist") }
    private var installedScript: URL { home.appendingPathComponent("Library/Application Support/TrinnovAutoShutdown/poweroff.py") }
    private let daemonPlist = URL(fileURLWithPath: "/Library/LaunchDaemons/com.trinnov.autoshutdown.daemon.plist")
    private var shipped: URL? { Bundle.main.resourceURL?.appendingPathComponent("autooff") }

    init() { refresh() }

    func refresh() {
        let fm = FileManager.default
        installed = fm.fileExists(atPath: agentPlist.path) && fm.fileExists(atPath: daemonPlist.path)
        upToDate = installed ? installedCopyMatches() : true
        let log = home.appendingPathComponent("Library/Logs/TrinnovAutoShutdown.log")
        if let text = try? String(contentsOf: log, encoding: .utf8) {
            lastEvent = text.split(separator: "\n").last(where: { $0.contains("shutdown sent") || $0.contains("no Trinnov") || $0.contains("failed") }).map(String.init)
        }
    }

    /// The installer carries the helper's program inside it; the installed copy should be identical.
    private func installedCopyMatches() -> Bool {
        guard let shipped = shipped,
              let script = try? String(contentsOf: shipped.appendingPathComponent("install.sh"), encoding: .utf8),
              let start = script.range(of: "<<'PYEOF'\n"), let end = script.range(of: "\nPYEOF\n", range: start.upperBound..<script.endIndex),
              let current = try? String(contentsOf: installedScript, encoding: .utf8) else { return true }
        let wanted = script[start.upperBound..<end.lowerBound].trimmingCharacters(in: .whitespacesAndNewlines)
        return wanted == current.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    func setEnabled(_ on: Bool) { run(on ? "install.sh" : "uninstall.sh") }
    func update() { run("install.sh") }

    private func run(_ name: String) {
        guard !busy, let dir = shipped else { return }
        busy = true
        message = nil
        let script = dir.appendingPathComponent(name).path
        Task.detached {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/bin/bash")
            p.arguments = [script]
            var env = ProcessInfo.processInfo.environment
            env["TRINNOV_NATIVE_AUTH"] = "1"
            p.environment = env
            let pipe = Pipe()
            p.standardOutput = pipe
            p.standardError = pipe
            var output = ""
            var code: Int32 = -1
            do {
                try p.run()
                output = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                p.waitUntilExit()
                code = p.terminationStatus
            } catch { output = error.localizedDescription }
            let (finalOutput, finalCode) = (output, code)
            await MainActor.run {
                self.busy = false
                self.refresh()
                let tail = finalOutput.split(separator: "\n").suffix(3).joined(separator: " ")
                if finalCode != 0 || (name == "install.sh" && !self.installed) || (name == "uninstall.sh" && self.installed) {
                    self.message = tail.contains("not installed") || tail.contains("NOT removed") || tail.contains("User canceled")
                        ? "It was not set up: the password was cancelled or refused. \(tail)" : "It did not finish. \(tail)"
                }
            }
        }
    }
}
