#!/bin/bash
# Starts Trinnov Functions: finds your Nova, opens the app in your browser.
# Close this window (or click Quit in the app) to stop it. Extra options: --read-only, --ip <address>
cd "$(dirname "$0")" || exit 1
if ! /usr/bin/python3 --version >/dev/null 2>&1; then
  echo "macOS needs to install its developer tools (Python) first. Click Install, then run this again."
  exit 1
fi
exec /usr/bin/python3 server.py "$@"
