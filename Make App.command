#!/bin/bash
# Builds "Trinnov Functions.app" next to this file: a double-click app with no Terminal window.
# (Keep the app in this folder: it runs server.py from here.)
cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"
SRC="$(mktemp -t trinnovfn).applescript"
cat > "$SRC" <<ASEOF
try
	do shell script "cd " & quoted form of "$DIR" & " && /usr/bin/python3 server.py > /dev/null 2>&1 &"
on error e
	display dialog "Could not start Trinnov Functions: " & e buttons {"OK"}
end try
ASEOF
rm -rf "Trinnov Functions.app"
osacompile -o "Trinnov Functions.app" "$SRC" 2>/dev/null || { echo "osacompile failed"; exit 1; }
defaults write "$DIR/Trinnov Functions.app/Contents/Info" LSUIElement -bool true
codesign --force --sign - "Trinnov Functions.app" >/dev/null 2>&1 || true
rm -f "$SRC"
echo "Built: $DIR/Trinnov Functions.app  (double-click it; use the Quit button in the app to stop it)"
