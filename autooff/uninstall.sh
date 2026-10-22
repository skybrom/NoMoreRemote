#!/bin/bash
# Removes Trinnov Auto Shutdown (user listener and system helper).
# Copy of the Trinnov Auto Shutdown uninstaller, run by the Trinnov Functions app (Settings).
# Runs the script on stdin as root. From the app (TRINNOV_NATIVE_AUTH set) macOS shows its own standard
# administrator prompt (with Touch ID); from the Terminal it is a normal sudo.
run_root() {
  if [ -n "$TRINNOV_NATIVE_AUTH" ]; then
    local tmp rc; tmp="$(mktemp -t trinnovroot)"; cat > "$tmp"
    /usr/bin/osascript - "$tmp" "$@" <<'OSAEOF'
on run argv
	set cmd to "/bin/bash " & quoted form of (item 1 of argv)
	repeat with i from 2 to count of argv
		set cmd to cmd & " " & quoted form of (item i of argv)
	end repeat
	do shell script cmd with prompt "Trinnov Functions wants to change the Trinnov shutdown helper." with administrator privileges
end run
OSAEOF
    rc=$?; rm -f "$tmp"; return $rc
  fi
  sudo /bin/bash -s "$@"
}
LABEL="com.trinnov.autoshutdown"
# The system helper goes first: if the password is cancelled nothing is half removed.
echo "Removing the system helper (needs your password)..."
if run_root <<'ROOTEOF'
DL=com.trinnov.autoshutdown.daemon
launchctl kill SIGKILL system/$DL 2>/dev/null; launchctl bootout system/$DL 2>/dev/null
rm -f /Library/LaunchDaemons/$DL.plist; rm -rf "/Library/Application Support/TrinnovAutoShutdown"
ROOTEOF
then
  # SIGKILL first, so that stopping the programs does not send a shutdown to the Nova
  launchctl kill SIGKILL "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
  rm -rf "$HOME/Library/Application Support/TrinnovAutoShutdown"
  echo "Trinnov Auto Shutdown removed."
else
  echo "The system helper was NOT removed."
  exit 1
fi
