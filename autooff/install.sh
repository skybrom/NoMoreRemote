#!/bin/bash
# Installs "Trinnov Auto Shutdown": when this Mac shuts down, restarts or logs out,
# it powers off the Trinnov Nova on the network. No configuration needed.
# Copy of the Trinnov Auto Shutdown installer, run by the Trinnov Functions app (Settings). The only change:
# the administrator step uses macOS's own prompt when TRINNOV_NATIVE_AUTH is set (the app sets it).
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
set -e
SUPPORT="$HOME/Library/Application Support/TrinnovAutoShutdown"
LABEL="com.trinnov.autoshutdown"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "Installing Trinnov Auto Shutdown..."
if ! /usr/bin/python3 --version >/dev/null 2>&1; then
  echo
  echo "macOS needs to install its developer tools (Python) first."
  echo "Click Install in the window that just appeared, then run this installer again."
  exit 1
fi

launchctl kill SIGKILL "gui/$(id -u)/$LABEL" 2>/dev/null || true   # SIGKILL: do not trigger a shutdown
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$SUPPORT/watcher.sh"
mkdir -p "$SUPPORT" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

cat > "$SUPPORT/poweroff.py" <<'PYEOF'
#!/usr/bin/env python3
"""Trinnov Nova auto shutdown.

shutdown (default): ask the system helper to power the Nova off (falls back to doing it directly).
serve:              the system helper (root LaunchDaemon): waits on 127.0.0.1 and powers the Nova off on request.
learn:              find the Nova and remember its address.
"""
import base64, json, os, re, signal, socket, struct, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.geteuid() == 0
SUPPORT = ("/Library/Application Support/TrinnovAutoShutdown" if ROOT
           else os.path.expanduser("~/Library/Application Support/TrinnovAutoShutdown"))
CACHE = os.path.join(SUPPORT, "last_ip")
LOG = "/Library/Logs/TrinnovAutoShutdown.log" if ROOT else os.path.expanduser("~/Library/Logs/TrinnovAutoShutdown.log")
PORT = int(os.environ.get("TRINNOV_PORT", "80"))
DAEMON_PORT = int(os.environ.get("TRINNOV_DAEMON_PORT", "47631"))


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


IP_BOUND_IF = 25  # macOS: force a socket out of a specific network interface


def active_interfaces():
    """Names of interfaces that have a real IPv4 address (e.g. Ethernet and Wi-Fi)."""
    out = subprocess.run(["/sbin/ifconfig"], capture_output=True, text=True).stdout
    names, current = [], None
    for line in out.splitlines():
        if line and not line[0].isspace():
            current = line.split(":")[0]
        m = re.match(r"\s+inet (\d+\.\d+\.\d+\.\d+) ", line)
        if m and current and not m.group(1).startswith(("127.", "169.254.")) and current not in names:
            names.append(current)
    return names


def open_socket(ip, timeout):
    """Connect to the Nova. A USB Ethernet adapter can disappear early in a shutdown while the
    route still points at it ('No route to host'), so if the default route fails, try each interface."""
    try:
        return socket.create_connection((ip, PORT), timeout=timeout)
    except OSError as first:
        for name in active_interfaces():
            s = socket.socket()
            s.settimeout(timeout)
            try:
                s.setsockopt(socket.IPPROTO_IP, IP_BOUND_IF, socket.if_nametoindex(name))
                s.connect((ip, PORT))
                log(f"default route failed ({first}); reached {ip} via {name}")
                return s
            except OSError:
                s.close()
        raise first


def is_nova(ip, timeout):
    try:
        with open_socket(ip, timeout) as s:
            s.settimeout(timeout + 1)
            s.sendall(b"GET / HTTP/1.0\r\nHost: " + ip.encode() + b"\r\n\r\n")
            return b"TrinnovDisplayServer" in s.recv(1024)
    except OSError:
        return False


def local_prefixes():
    forced = os.environ.get("TRINNOV_PREFIXES")
    if forced:
        return forced.split(",")
    out = subprocess.run(["/sbin/ifconfig"], capture_output=True, text=True).stdout
    found = []
    for m in re.finditer(r"inet (\d+\.\d+\.\d+)\.\d+ netmask", out):
        p = m.group(1)
        if not p.startswith(("127.", "169.254.")) and p not in found:
            found.append(p)
    return found


def find_novas():
    try:
        cached = open(CACHE).read().strip()
    except OSError:
        cached = ""
    if cached and is_nova(cached, 1.0):
        return [cached]
    candidates = [f"{p}.{i}" for p in local_prefixes() for i in range(1, 255)]
    with ThreadPoolExecutor(max_workers=128) as pool:
        hits = [ip for ip, ok in zip(candidates, pool.map(lambda ip: is_nova(ip, 0.7), candidates)) if ok]
    if hits:
        try:
            open(CACHE, "w").write(hits[0])
        except OSError:
            pass
    return hits


def mask_frame(payload, opcode=0x2):
    mask = os.urandom(4)
    n = len(payload)
    hdr = bytes([0x80 | opcode, 0x80 | n]) if n < 126 else bytes([0x80 | opcode, 0x80 | 126]) + struct.pack(">H", n)
    return hdr + mask + bytes(b ^ m for b, m in zip(payload, mask * (n // 4 + 1)))


def shutdown_message():
    path = b"/system/power_supply"
    body = json.dumps({"shutdown": True}).encode()
    return mask_frame(struct.pack(">ibi", 5 + len(path) + len(body), 3, len(path)) + path + body)


def connect_ws(ip):
    s = open_socket(ip, 4)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET /ws HTTP/1.1\r\nHost: {ip}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    resp = s.recv(1024)
    if b" 101 " not in resp.split(b"\r\n")[0]:
        raise OSError(f"websocket handshake failed: {resp[:60]!r}")
    return s


def diagnose(ip):
    """Record why the Nova could not be reached (shutdown-time failures are otherwise invisible)."""
    try:
        socket.create_connection((ip, PORT), timeout=1).close()
        err = "connect ok"
    except OSError as e:
        err = repr(e)
    ifc = subprocess.run(["/sbin/ifconfig"], capture_output=True, text=True).stdout
    state = " ".join(l.strip() for l in ifc.splitlines() if "status:" in l or l.strip().startswith("inet "))
    route = subprocess.run(["/sbin/route", "-n", "get", ip], capture_output=True, text=True)
    hop = " ".join((route.stdout + route.stderr).split())[:200]
    log(f"diagnosis for {ip}: {err} | {state} | route: {hop}")


def remembered_ip():
    try:
        return open(CACHE).read().strip()
    except OSError:
        return ""


def send_shutdown(ip):
    """The websocket handshake itself proves this is the Nova, so no web-page check is needed first."""
    s = connect_ws(ip)
    try:
        s.sendall(shutdown_message())
        time.sleep(0.5)
    finally:
        s.close()


def power_off_new_connection():
    for attempt in range(3):
        tried = remembered_ip()
        if tried:  # straight to the remembered address: the quickest and most reliable thing at shutdown
            try:
                send_shutdown(tried)
                log(f"shutdown sent to {tried}")
                return True
            except OSError as e:
                log(f"attempt {attempt + 1}: {tried}: {e}")
        for ip in find_novas():  # otherwise look for it on the network
            if ip == tried:
                continue
            try:
                send_shutdown(ip)
                log(f"shutdown sent to {ip}")
                return True
            except OSError as e:
                log(f"attempt {attempt + 1}: {ip}: {e}")
        log(f"attempt {attempt + 1}: no Trinnov found on the network (interfaces: {local_prefixes()})")
        if tried:
            diagnose(tried)
        time.sleep(1)
    return False


def serve():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", DAEMON_PORT))
    srv.listen(5)

    def on_term(*_):  # last-resort backup; the network is usually gone by now
        log("daemon stop signal: trying a last shutdown")
        power_off_new_connection()
        os._exit(0)

    signal.signal(signal.SIGTERM, on_term)
    log("daemon started")
    while True:
        conn, _ = srv.accept()
        try:
            if conn.recv(64).startswith(b"shutdown"):
                log("daemon: shutdown requested")
                conn.sendall(b"sent\n" if power_off_new_connection() else b"failed\n")
        except OSError as e:
            log(f"daemon: {e}")
        finally:
            conn.close()


def trigger():
    try:
        with socket.create_connection(("127.0.0.1", DAEMON_PORT), timeout=2) as c:
            c.sendall(b"shutdown\n")
            c.settimeout(15)
            reply = c.recv(64).strip()
        log(f"system helper replied: {reply.decode(errors='replace')}")
        if reply in (b"sent", b"failed"):  # the helper already tried hard; don't hold up the restart
            return
    except OSError as e:
        log(f"system helper not reachable ({e}); trying directly")
    power_off_new_connection()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "shutdown"
    if mode == "learn":
        novas = find_novas()
        log(f"found Nova at {novas[0]}" if novas else "learn: no Trinnov found on the network")
    elif mode == "serve":
        serve()
    else:
        trigger()


main()
PYEOF
chmod +x "$SUPPORT/poweroff.py"

cat > "$SUPPORT/listener.swift" <<'SWEOF'
import AppKit

// Runs all session. macOS tells apps "power off" at the very start of a shutdown, restart or
// logout, while the network is still up. That is when we switch the Nova off.
let home = NSHomeDirectory()
let script = home + "/Library/Application Support/TrinnovAutoShutdown/poweroff.py"
let logPath = home + "/Library/Logs/TrinnovAutoShutdown.log"
var fired = false

func log(_ message: String) {
    let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd HH:mm:ss "
    let line = f.string(from: Date()) + message + "\n"
    if let h = FileHandle(forWritingAtPath: logPath) {
        h.seekToEndOfFile(); h.write(line.data(using: .utf8)!); h.closeFile()
    } else {
        FileManager.default.createFile(atPath: logPath, contents: line.data(using: .utf8))
    }
}

func powerOffNova(_ reason: String) {
    if fired { return }
    fired = true
    log("\(reason): powering off the Nova")
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
    p.arguments = [script, "shutdown"]
    try? p.run()
    p.waitUntilExit()
    // allow another run if the logout was cancelled
    DispatchQueue.main.asyncAfter(deadline: .now() + 60) { fired = false }
}

NSWorkspace.shared.notificationCenter.addObserver(
    forName: NSWorkspace.willPowerOffNotification, object: nil, queue: .main
) { _ in powerOffNova("power-off notification") }

signal(SIGTERM, SIG_IGN)
let term = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
term.setEventHandler { powerOffNova("stop signal"); exit(0) }
term.resume()

log("listener started")
NSApplication.shared.setActivationPolicy(.accessory)
NSApplication.shared.run()
SWEOF

if ! swiftc -O -o "$SUPPORT/trinnov-listener" "$SUPPORT/listener.swift" 2>"$SUPPORT/compile.log"; then
  echo "Could not build the listener (Apple developer tools needed). Details: $SUPPORT/compile.log"
  exit 1
fi

cat > "$PLIST" <<PEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key><array><string>$SUPPORT/trinnov-listener</string></array>
    <key>LimitLoadToSessionType</key><string>Aqua</string>
    <key>RunAtLoad</key><true/>
    <key>ExitTimeOut</key><integer>10</integer>
</dict>
</plist>
PEOF

echo
echo "An administrator password is needed once, to install the system helper that"
echo "does the actual shutdown (macOS cuts user programs off from the network at restart)."
if run_root "$SUPPORT" <<'ROOTEOF'
SRC="$1"; DEST="/Library/Application Support/TrinnovAutoShutdown"; DL="com.trinnov.autoshutdown.daemon"
launchctl kill SIGKILL "system/$DL" 2>/dev/null || true
launchctl bootout "system/$DL" 2>/dev/null || true
mkdir -p "$DEST"
cp "$SRC/poweroff.py" "$DEST/poweroff.py"
chown -R root:wheel "$DEST"; chmod 755 "$DEST" "$DEST/poweroff.py"
cat > "/Library/LaunchDaemons/$DL.plist" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$DL</string>
    <key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>$DEST/poweroff.py</string><string>serve</string></array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
</dict>
</plist>
PLISTEOF
chown root:wheel "/Library/LaunchDaemons/$DL.plist"; chmod 644 "/Library/LaunchDaemons/$DL.plist"
/usr/bin/python3 "$DEST/poweroff.py" learn || true
launchctl bootstrap system "/Library/LaunchDaemons/$DL.plist"
ROOTEOF
then
  echo "System helper installed."
else
  echo "WARNING: the system helper was not installed; shutdown at restart may not work."
fi

/usr/bin/python3 "$SUPPORT/poweroff.py" learn || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo
echo "Done. Your Trinnov will now switch off when this Mac shuts down, restarts or logs out."
echo "(Log: ~/Library/Logs/TrinnovAutoShutdown.log)"
