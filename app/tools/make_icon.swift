// Draws the app icon (dark rounded square, cyan faders) and writes an .iconset folder: make_icon <folder.iconset>
import AppKit

func png(_ px: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8, samplesPerPixel: 4,
                               hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = CGFloat(px)
    let body = NSRect(x: s * 0.06, y: s * 0.06, width: s * 0.88, height: s * 0.88)
    let bg = NSBezierPath(roundedRect: body, xRadius: s * 0.2, yRadius: s * 0.2)
    NSGradient(starting: NSColor(red: 0.13, green: 0.15, blue: 0.19, alpha: 1), ending: NSColor(red: 0.05, green: 0.06, blue: 0.08, alpha: 1))!.draw(in: bg, angle: -90)
    let cyan = NSColor(red: 0.13, green: 0.87, blue: 1.0, alpha: 1)
    let knobs: [CGFloat] = [0.62, 0.38, 0.5]
    for (i, k) in knobs.enumerated() {
        let x = s * (0.3 + 0.2 * CGFloat(i))
        let track = NSBezierPath(roundedRect: NSRect(x: x - s * 0.012, y: s * 0.24, width: s * 0.024, height: s * 0.52), xRadius: s * 0.012, yRadius: s * 0.012)
        NSColor(white: 1, alpha: 0.18).setFill(); track.fill()
        let y = s * 0.24 + s * 0.52 * k
        cyan.setFill()
        NSBezierPath(ovalIn: NSRect(x: x - s * 0.05, y: y - s * 0.05, width: s * 0.1, height: s * 0.1)).fill()
    }
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

let dir = CommandLine.arguments[1]
try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
for base in [16, 32, 128, 256, 512] {
    try! png(base).write(to: URL(fileURLWithPath: "\(dir)/icon_\(base)x\(base).png"))
    try! png(base * 2).write(to: URL(fileURLWithPath: "\(dir)/icon_\(base)x\(base)@2x.png"))
}
