import SwiftUI

/// Colours taken from the Nova's own web interface.
enum Theme {
    static let bg = Color(hex: 0x262728)
    static let panel = Color(hex: 0x29292D)
    static let tile = Color(hex: 0x2A3038)
    static let tileHover = Color(hex: 0x4D5154)
    static let edge = Color(hex: 0x353538)
    static let text = Color(hex: 0xD3D3D3)
    static let legend = Color(hex: 0xB0ADAD)
    static let off = Color(hex: 0x747474)
    static let accent = Color(hex: 0x2DE5FE)
    static let accentDim = Color(hex: 0x26A7B9)
    static let warn = Color(hex: 0xF2994A)
    static let danger = Color(hex: 0xFF575F)
    static let ok = Color(hex: 0x2CC8DD)
}

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255, blue: Double(hex & 0xFF) / 255)
    }
}

struct PillButton: View {
    let title: String
    var tint: Color = Theme.accent
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Text(title).font(.system(size: 10.5, weight: .semibold)).foregroundColor(tint)
                .padding(.horizontal, 8).padding(.vertical, 4)
                .background(Capsule().fill(Color(hex: 0x1D1F21)))
                .overlay(Capsule().stroke(Theme.edge, lineWidth: 1))
        }.buttonStyle(.plain)
    }
}

struct FlatButtonStyle: ButtonStyle {
    var prominent = false
    var danger = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: prominent ? .semibold : .regular))
            .foregroundColor(prominent ? Color(hex: 0x05262B) : Theme.text)
            .padding(.horizontal, 14).padding(.vertical, 7)
            .background(RoundedRectangle(cornerRadius: 8).fill(prominent ? (danger ? Theme.danger : Theme.accent) : Theme.tile))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.edge, lineWidth: 1))
            .opacity(configuration.isPressed ? 0.75 : 1)
    }
}

struct TileButtonStyle: ButtonStyle {
    let on: Bool
    let learning: Bool
    let hovering: Bool
    let disabled: Bool
    let viewOnly: Bool
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .frame(maxWidth: .infinity, minHeight: 88)
            .background(RoundedRectangle(cornerRadius: 12).fill(viewOnly ? Color.clear : (hovering && !disabled ? Theme.tileHover : Theme.tile)))
            .overlay(RoundedRectangle(cornerRadius: 12)
                .strokeBorder(learning ? Theme.warn : (on ? Theme.accent : Theme.edge),
                              style: StrokeStyle(lineWidth: 1, dash: viewOnly ? [4, 3] : [])))
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .opacity(disabled ? 0.35 : 1)
    }
}
