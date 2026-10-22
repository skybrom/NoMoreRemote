// trinnov-midi: listens to MIDI and prints what it hears as one JSON line per message.
//
//  - connects to EVERY MIDI source on the Mac (controllers, IAC Driver buses, Stream Deck MIDI plugins...),
//    including ones plugged in later;
//  - creates a virtual MIDI input called "Trinnov Functions" that other apps can send to.
//
// Lines look like:  {"type":"note","channel":1,"number":36,"value":100,"port":"IAC Driver Bus 1"}
// types: note (value 0 = note off), cc, program. `trinnov-midi --list` prints the ports and exits.
import Foundation
import CoreMIDI

setvbuf(stdout, nil, _IOLBF, 0)

func displayName(_ obj: MIDIObjectRef) -> String {
    var name: Unmanaged<CFString>?
    if MIDIObjectGetStringProperty(obj, kMIDIPropertyDisplayName, &name) == noErr, let value = name {
        return value.takeRetainedValue() as String
    }
    return "unknown"
}

func emit(_ fields: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys]),
       let text = String(data: data, encoding: .utf8) {
        print(text)
    }
}

if CommandLine.arguments.contains("--list") {
    let sources = (0..<MIDIGetNumberOfSources()).map { displayName(MIDIGetSource($0)) }
    let destinations = (0..<MIDIGetNumberOfDestinations()).map { displayName(MIDIGetDestination($0)) }
    emit(["sources": sources, "destinations": destinations])
    exit(0)
}

var client = MIDIClientRef()
var inPort = MIDIPortRef()
var virtualInput = MIDIEndpointRef()
var connected = Set<MIDIEndpointRef>()
var sourceNames: [MIDIEndpointRef: String] = [:]
let env = ProcessInfo.processInfo.environment
let portName = env["TRINNOV_MIDI_PORT_NAME"] ?? "Trinnov Functions"         // the test suite uses another name
let allowTestPorts = env["TRINNOV_MIDI_ALLOW_TEST"] != nil

func uniqueNames() -> [String] { Array(Set(sourceNames.values)).sorted() }

/// Reads the MIDI 1.0 messages inside a packet list and prints them.
/// The packets are walked by hand, straight in the list's memory (copying a packet and stepping on from the
/// copy reads garbage for every packet after the first). Running status is handled.
func handle(_ list: UnsafePointer<MIDIPacketList>, port: String) {
    let raw = UnsafeRawPointer(list)
    var offset = MemoryLayout<UInt32>.size                              // after numPackets
    for _ in 0..<Int(list.pointee.numPackets) {
        let length = Int(raw.loadUnaligned(fromByteOffset: offset + 8, as: UInt16.self))
        let data = raw.advanced(by: offset + 10).assumingMemoryBound(to: UInt8.self)
        let bytes = UnsafeBufferPointer(start: data, count: length)
        parse(bytes, port: port)
        offset += 10 + length
        #if arch(arm64) || arch(arm)
        offset = (offset + 3) & ~3                                      // packets are 4-byte aligned on ARM
        #endif
    }
}

func parse(_ bytes: UnsafeBufferPointer<UInt8>, port: String) {
    let hex = bytes.map { String(format: "%02X", $0) }.joined()
    var i = 0
    var running: UInt8 = 0
    while i < bytes.count {
        var status = bytes[i]
        if status < 0x80 {                                              // data byte: running status
            if running == 0 { i += 1; continue }
            status = running
        } else {
            i += 1
            if status < 0xF0 { running = status }
        }
        let channel = Int(status & 0x0F) + 1
        switch status & 0xF0 {
        case 0x80, 0x90:
            if i + 1 < bytes.count {
                let velocity = (status & 0xF0) == 0x80 ? 0 : Int(bytes[i + 1])
                emit(["type": "note", "channel": channel, "number": Int(bytes[i]), "value": velocity, "port": port, "raw": hex])
            }
            i += 2
        case 0xB0:
            if i + 1 < bytes.count {
                emit(["type": "cc", "channel": channel, "number": Int(bytes[i]), "value": Int(bytes[i + 1]), "port": port, "raw": hex])
            }
            i += 2
        case 0xC0:
            if i < bytes.count {
                emit(["type": "program", "channel": channel, "number": Int(bytes[i]), "value": 127, "port": port, "raw": hex])
            }
            i += 1
        case 0xA0, 0xE0: i += 2
        case 0xD0: i += 1
        default:                                                        // system / realtime: skip to the next status byte
            while i < bytes.count && bytes[i] < 0x80 { i += 1 }
        }
    }
}

func connectAllSources() {
    let present = Set((0..<MIDIGetNumberOfSources()).map { MIDIGetSource($0) })
    for gone in connected.subtracting(present) {                        // a controller or plugin went away
        connected.remove(gone)
        sourceNames[gone] = nil
    }
    for source in present where !connected.contains(source) {
        let name = displayName(source)
        // ports named trinnov-test-* belong to this project's test suite: a real run never listens to them,
        // so tests can never trigger anything on a real Nova
        if name.hasPrefix("trinnov-test-") && !allowTestPorts { continue }
        sourceNames[source] = name
        let ref = UnsafeMutableRawPointer(bitPattern: Int(source))
        if MIDIPortConnectSource(inPort, source, ref) == noErr {
            connected.insert(source)
        }
    }
}

MIDIClientCreateWithBlock("Trinnov Functions" as CFString, &client) { notification in
    if notification.pointee.messageID == .msgSetupChanged {
        connectAllSources()                                         // a controller or plugin appeared
        emit(["type": "ports", "sources": uniqueNames()])
    }
}
MIDIInputPortCreateWithBlock(client, "Trinnov Functions in" as CFString, &inPort) { list, refCon in
    let source = MIDIEndpointRef(Int(bitPattern: refCon))
    handle(list, port: sourceNames[source] ?? "unknown")
}
MIDIDestinationCreateWithBlock(client, portName as CFString, &virtualInput) { list, _ in
    handle(list, port: "\(portName) (virtual port)")
}
connectAllSources()
emit(["type": "ready", "sources": uniqueNames()])
RunLoop.main.run()
