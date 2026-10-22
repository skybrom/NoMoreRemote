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

/// Reads the MIDI 1.0 messages inside a packet list and prints them.
func handle(_ list: UnsafePointer<MIDIPacketList>, port: String) {
    var packet = list.pointee.packet
    for _ in 0..<list.pointee.numPackets {
        let length = Int(packet.length)
        withUnsafeBytes(of: &packet.data) { raw in
            var i = 0
            while i < length {
                let status = raw[i]
                if status < 0x80 { i += 1; continue }               // stray data byte
                let channel = Int(status & 0x0F) + 1
                switch status & 0xF0 {
                case 0x80, 0x90:                                    // note off / note on
                    if i + 2 < length {
                        let velocity = (status & 0xF0) == 0x80 ? 0 : Int(raw[i + 2])
                        emit(["type": "note", "channel": channel, "number": Int(raw[i + 1]), "value": velocity, "port": port])
                    }
                    i += 3
                case 0xB0:                                          // control change
                    if i + 2 < length {
                        emit(["type": "cc", "channel": channel, "number": Int(raw[i + 1]), "value": Int(raw[i + 2]), "port": port])
                    }
                    i += 3
                case 0xC0:                                          // program change
                    if i + 1 < length {
                        emit(["type": "program", "channel": channel, "number": Int(raw[i + 1]), "value": 127, "port": port])
                    }
                    i += 2
                case 0xA0, 0xE0: i += 3                             // aftertouch, pitch bend
                case 0xD0: i += 2                                   // channel pressure
                default: i += 1                                     // system and realtime messages
                }
            }
        }
        packet = MIDIPacketNext(&packet).pointee
    }
}

func connectAllSources() {
    for index in 0..<MIDIGetNumberOfSources() {
        let source = MIDIGetSource(index)
        if connected.contains(source) { continue }
        sourceNames[source] = displayName(source)
        let ref = UnsafeMutableRawPointer(bitPattern: Int(source))
        if MIDIPortConnectSource(inPort, source, ref) == noErr {
            connected.insert(source)
        }
    }
}

MIDIClientCreateWithBlock("Trinnov Functions" as CFString, &client) { notification in
    if notification.pointee.messageID == .msgSetupChanged {
        connectAllSources()                                         // a controller was plugged in
    }
}
MIDIInputPortCreateWithBlock(client, "Trinnov Functions in" as CFString, &inPort) { list, refCon in
    let source = MIDIEndpointRef(Int(bitPattern: refCon))
    handle(list, port: sourceNames[source] ?? "unknown")
}
MIDIDestinationCreateWithBlock(client, "Trinnov Functions" as CFString, &virtualInput) { list, _ in
    handle(list, port: "Trinnov Functions (virtual port)")
}
connectAllSources()
emit(["type": "ready", "sources": sourceNames.values.sorted()])
RunLoop.main.run()
