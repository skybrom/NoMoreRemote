import Foundation

// What the background engine says, as the app reads it (the engine's JSON API on 127.0.0.1).

struct BridgeInfo: Codable {
    let pid: Int32
    let port: Int
    let token: String
}

struct NovaInfo: Codable {
    let product: String?
    let serial: Int?
    let firmware: String?
    let market: String?
}

struct MenuItem: Codable, Identifiable, Hashable {
    let id: String
    let label: String
    let sub: String?
    let type: String
    let danger: String?
    let view_only: Bool?
    let momentary: Bool?
    let midi: String?
    var viewOnly: Bool { view_only ?? false }
    var needsConfirm: Bool { danger == "confirm" }
}

struct MenuSection: Codable, Identifiable, Hashable {
    let key: String
    let title: String
    let note: String?
    let items: [MenuItem]
    var id: String { key }
}

struct MenuResponse: Codable {
    let connected: Bool
    let error: String?
    let read_only: Bool?
    let nova_ip: String?
    let info: NovaInfo?
    let menu_source: String?
    let sections: [MenuSection]?
    let epoch: Int?
    let service: Bool?
}

struct ItemState: Codable {
    let on: Bool?
    let info: String?
    let unavailable: Bool?
}

struct LogEntry: Codable {
    let t: String
    let kind: String
    let text: String
    let detail: String?
}

struct MidiEvent: Codable, Hashable {
    let text: String
    let value: Int
    let port: String
    let raw: String?
    let at: String
    let result: String?
}

struct Learning: Codable, Hashable {
    let section: String
    let selector: String
    let label: String
}

struct MidiBrief: Codable {
    let version: Int
    let last: MidiEvent?
    let learning: Learning?
    let count: Int
}

struct StateResponse: Codable {
    let connected: Bool
    let error: String?
    let items: [String: ItemState]?
    let volume: Double?
    let mute: Bool?
    let volume_step: Double?
    let mode: String?
    let log: [LogEntry]?
    let read_only: Bool?
    let epoch: Int?
    let midi: MidiBrief?
}

struct MidiMsg: Codable, Hashable {
    let type: String
    let channel: Int
    let number: Int
}

struct MidiBinding: Codable, Hashable, Identifiable {
    let msg: MidiMsg
    let section: String
    let selector: String
    let label: String
    let text: String
    let trigger: String?
    var id: String { "\(msg.type)-\(msg.channel)-\(msg.number)" }
}

struct MidiInfo: Codable {
    let version: Int
    let last: MidiEvent?
    let learning: Learning?
    let count: Int
    let status: String
    let ports: [String]
    let virtual_port: String
    let allow_power: Bool
    let history: [MidiEvent]
    let bindings: [MidiBinding]
}

struct ActionResult: Codable {
    let ok: Bool?
    let error: String?
    let needs_confirm: Bool?
    let message: String?
    let count: Int?
    let dir: String?
    let paths: [String]?
}
