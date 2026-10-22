#!/bin/bash
# Builds the native menu bar app "Trinnov Functions.app" (needs Apple's developer tools: Swift).
#   app/build_app.sh                 -> ./Trinnov Functions.app
#   app/build_app.sh /some/folder/X.app
set -e
cd "$(dirname "$0")/.."
OUT="${1:-$PWD/Trinnov Functions.app}"
rm -rf "${OUT:?}"
mkdir -p "$OUT/Contents/Resources/autooff" "$OUT/Contents/MacOS" "$OUT/Contents/Resources/engine/data" "$OUT/Contents/Resources/engine/ui" "$OUT/Contents/Resources/engine/midi"

echo "Compiling the app..."
swiftc -O -parse-as-library -swift-version 5 -target "$(uname -m)-apple-macos13.0" app/*.swift -o "$OUT/Contents/MacOS/TrinnovFunctions"

echo "Drawing the icon..."
ICONS="$(mktemp -d)"
swiftc -O app/tools/make_icon.swift -o "$ICONS/make_icon" 2>/dev/null && "$ICONS/make_icon" "$ICONS/AppIcon.iconset" && iconutil -c icns "$ICONS/AppIcon.iconset" -o "$OUT/Contents/Resources/AppIcon.icns" || echo "(no icon)"
rm -rf "${ICONS:?}"

echo "Adding the engine and the MIDI helper..."
for f in server.py trinnov.py data/functions_tree.json ui/index.html midi/trinnov-midi.swift; do
  cp "$f" "$OUT/Contents/Resources/engine/$f"
done

cp autooff/install.sh autooff/uninstall.sh autooff/askpass.sh "$OUT/Contents/Resources/autooff/"

cat > "$OUT/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Trinnov Functions</string>
    <key>CFBundleDisplayName</key><string>Trinnov Functions</string>
    <key>CFBundleIdentifier</key><string>app.trinnovfunctions.menubar</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundleExecutable</key><string>TrinnovFunctions</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSMinimumSystemVersion</key><string>13.0</string>
    <key>LSUIElement</key><true/>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSPrincipalClass</key><string>NSApplication</string>
</dict>
</plist>
PLIST

codesign --force --sign - "$OUT" >/dev/null 2>&1 || true
echo "Built: $OUT"
