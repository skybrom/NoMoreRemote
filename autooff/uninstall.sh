#!/bin/bash
# Removes Trinnov Auto Shutdown (user listener and system helper).
# Copy of the Trinnov Auto Shutdown uninstaller, run by the Trinnov Functions app (Settings).
SUDO="sudo"; [ -n "$SUDO_ASKPASS" ] && SUDO="sudo -A"
LABEL="com.trinnov.autoshutdown"
# SIGKILL first, so that stopping the programs does not send a shutdown to the Nova
launchctl kill SIGKILL "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
rm -rf "$HOME/Library/Application Support/TrinnovAutoShutdown"

echo "Removing the system helper (needs your password)..."
if $SUDO /bin/bash -c 'DL=com.trinnov.autoshutdown.daemon
launchctl kill SIGKILL system/$DL 2>/dev/null; launchctl bootout system/$DL 2>/dev/null
rm -f /Library/LaunchDaemons/$DL.plist; rm -rf "/Library/Application Support/TrinnovAutoShutdown"'; then
  echo "Trinnov Auto Shutdown removed."
else
  echo "The system helper was NOT removed."
fi
