// Headless check of the app's data layer: reads the engine through the SAME models the app uses.
//   selftest                       decode state, menu and MIDI info and print a summary
//   selftest --press "Preset B"    also press that function and check the Nova state followed
// Uses TRINNOV_SUPPORT_DIR to find the engine (bridge.json). Exit code 0 = all good.
import Foundation

func fail(_ message: String) -> Never { print("FAIL: \(message)"); exit(1) }

let support = ProcessInfo.processInfo.environment["TRINNOV_SUPPORT_DIR"]
    ?? NSHomeDirectory() + "/Library/Application Support/TrinnovFunctions"
guard let data = FileManager.default.contents(atPath: support + "/bridge.json"),
      let bridge = try? JSONDecoder().decode(BridgeInfo.self, from: data) else { fail("no engine registered in \(support)/bridge.json") }

func call<T: Decodable>(_ path: String, body: [String: Any]? = nil, as: T.Type) -> T {
    var request = URLRequest(url: URL(string: "http://127.0.0.1:\(bridge.port)\(path)")!, timeoutInterval: 30)
    request.setValue(bridge.token, forHTTPHeaderField: "X-Token")
    if let body = body {
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try! JSONSerialization.data(withJSONObject: body)
    }
    var result: T?
    var problem = ""
    let done = DispatchSemaphore(value: 0)
    URLSession.shared.dataTask(with: request) { data, _, error in
        if let data = data {
            do { result = try JSONDecoder().decode(T.self, from: data) } catch { problem = "decoding \(path): \(error)" }
        } else { problem = "\(path): \(error?.localizedDescription ?? "no data")" }
        done.signal()
    }.resume()
    done.wait()
    guard let r = result else { fail(problem) }
    return r
}

let menu = call("/api/menu", as: MenuResponse.self)
guard menu.connected, let sections = menu.sections, !sections.isEmpty else { fail("menu not connected or empty: \(menu.error ?? "")") }
let state = call("/api/state", as: StateResponse.self)
guard state.connected, let items = state.items else { fail("state not connected") }
let midi = call("/api/midi", as: MidiInfo.self)
let count = sections.reduce(0) { $0 + $1.items.count }
print("OK: Nova \(menu.info?.product ?? "?") #\(menu.info?.serial ?? 0) at \(menu.nova_ip ?? "?")")
print("OK: \(sections.count) sections, \(count) functions, \(items.count) states, volume \(state.volume.map { String($0) } ?? "?") dB")
print("OK: MIDI helper '\(midi.status)', \(midi.ports.count) ports, \(midi.bindings.count) mappings")
if sections.contains(where: { $0.title == "Presets" }) { print("OK: sections: " + sections.map { $0.title }.joined(separator: ", ")) }

if let at = CommandLine.arguments.firstIndex(of: "--press"), at + 1 < CommandLine.arguments.count {
    let wanted = CommandLine.arguments[at + 1]
    guard let item = sections.flatMap({ $0.items }).first(where: { $0.label == wanted }) else { fail("no function named \(wanted)") }
    let r = call("/api/press", body: ["id": item.id, "confirm": false], as: ActionResult.self)
    guard r.ok == true else { fail("press \(wanted): \(r.error ?? "no ok")") }
    Thread.sleep(forTimeInterval: 0.6)
    let after = call("/api/state", as: StateResponse.self)
    print("OK: pressed \(wanted); its tile is now \(after.items?[item.id]?.on == true ? "lit" : "not lit")")
}
exit(0)
