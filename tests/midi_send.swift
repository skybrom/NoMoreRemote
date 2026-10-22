// Test tool: pretends to be a MIDI controller. Not part of the product.
//
//   midi_send source "Test Controller" 2      create a virtual MIDI SOURCE with that name, wait 2 s for listeners
//                                             to connect, then send the messages that follow
//   midi_send dest                            send to the "Trinnov Functions" virtual destination instead
// messages: note CH NUM VEL | cc CH NUM VAL | program CH NUM
import Foundation
import CoreMIDI

var client = MIDIClientRef()
MIDIClientCreateWithBlock("midi_send" as CFString, &client) { _ in }

func bytes(_ words: [String]) -> [UInt8]? {
    guard words.count >= 3, let ch = Int(words[1]), let a = Int(words[2]) else { return nil }
    let c = UInt8((ch - 1) & 0x0F)
    switch words[0] {
    case "note": return [0x90 | c, UInt8(a), UInt8(words.count > 3 ? Int(words[3]) ?? 100 : 100)]
    case "cc": return [0xB0 | c, UInt8(a), UInt8(words.count > 3 ? Int(words[3]) ?? 127 : 127)]
    case "program": return [0xC0 | c, UInt8(a)]
    default: return nil
    }
}

func sendMulti(_ messages: [[UInt8]], via transmit: (UnsafePointer<MIDIPacketList>) -> Void) {
    var buffer = [UInt8](repeating: 0, count: 1024)
    buffer.withUnsafeMutableBytes { raw in
        let list = raw.baseAddress!.assumingMemoryBound(to: MIDIPacketList.self)
        var packet = MIDIPacketListInit(list)
        for m in messages { packet = MIDIPacketListAdd(list, 1024, packet, 0, m.count, m) }
        transmit(list)
    }
}

func send(_ message: [UInt8], via transmit: (UnsafePointer<MIDIPacketList>) -> Void) {
    var buffer = [UInt8](repeating: 0, count: 1024)
    buffer.withUnsafeMutableBytes { raw in
        let list = raw.baseAddress!.assumingMemoryBound(to: MIDIPacketList.self)
        let packet = MIDIPacketListInit(list)
        _ = MIDIPacketListAdd(list, 1024, packet, 0, message.count, message)
        transmit(list)
    }
}

var args = Array(CommandLine.arguments.dropFirst())
let mode = args.isEmpty ? "dest" : args.removeFirst()
var messages: [[UInt8]] = []
var current: [String] = []
var wait = 2.0
var name = "Test Controller"
if mode == "source" { name = args.removeFirst(); wait = Double(args.removeFirst()) ?? 2 }
for word in args {
    if ["note", "cc", "program"].contains(word) { if let m = bytes(current) { messages.append(m) }; current = [word] } else { current.append(word) }
}
if let m = bytes(current) { messages.append(m) }

if mode == "source" {
    var source = MIDIEndpointRef()
    MIDISourceCreate(client, name as CFString, &source)
    Thread.sleep(forTimeInterval: wait)                              // let the helper notice and connect
    if ProcessInfo.processInfo.environment["MIDI_SEND_MULTI"] != nil {   // ALL messages in ONE packet list (several packets)
        sendMulti(messages) { MIDIReceived(source, $0) }
    } else if ProcessInfo.processInfo.environment["MIDI_SEND_RUNNING"] != nil {   // one packet: status byte once, then data pairs
        let first = messages[0]
        let bytes = first + messages.dropFirst().flatMap { Array($0.dropFirst()) }
        send(bytes) { MIDIReceived(source, $0) }
    } else {
        for message in messages {
            send(message) { MIDIReceived(source, $0) }
            Thread.sleep(forTimeInterval: 0.3)
        }
    }
} else {
    var destination = MIDIEndpointRef()
    for i in 0..<MIDIGetNumberOfDestinations() {
        var n: Unmanaged<CFString>?
        MIDIObjectGetStringProperty(MIDIGetDestination(i), kMIDIPropertyDisplayName, &n)
        if (n?.takeRetainedValue() as String?) == (ProcessInfo.processInfo.environment["TRINNOV_MIDI_PORT_NAME"] ?? "Trinnov Functions") { destination = MIDIGetDestination(i) }
    }
    if destination == 0 { print("no matching destination found"); exit(1) }
    var out = MIDIPortRef()
    MIDIOutputPortCreate(client, "out" as CFString, &out)
    for message in messages {
        send(message) { MIDISend(out, destination, $0) }
        Thread.sleep(forTimeInterval: 0.3)
    }
}
Thread.sleep(forTimeInterval: 0.5)
