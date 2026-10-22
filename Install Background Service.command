#!/bin/bash
# Keeps Trinnov Functions running in the background from every login (no password needed):
# MIDI, the `trinnov` command and the Stream Deck launchers then work without a browser or Terminal window.
cd "$(dirname "$0")" || exit 1
if ! /usr/bin/python3 --version >/dev/null 2>&1; then
  echo "macOS needs to install its developer tools (Python) first. Click Install, then run this again."
  exit 1
fi
/usr/bin/python3 server.py --install-service
