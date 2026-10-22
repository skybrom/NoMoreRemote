#!/usr/bin/env python3
"""A stateful fake Trinnov Nova for testing Trinnov Functions without touching real hardware.

    python3 tests/fake_nova.py [port]       # default 8083
    TRINNOV_IP=127.0.0.1 TRINNOV_PORT=8083 python3 server.py

It speaks the same websocket protocol (answers reads, applies writes, pings the client) and prints
every write it receives. Names are generic; change STORE to try other setups.
"""
import base64, hashlib, json, socket, struct, sys, threading, time

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8083
GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

SRC = lambda name, hidden=False: {"name": name, "is_hidden_main": hidden, "is_mixbus": False, "is_locked": hidden}
STORE = {
    "/config": {"class_name": "Nova", "product_class_str": "nova", "serial": 4242, "product_market_str": "pro",
                "features": {"monitoring": True, "codec": False, "dolby": False, "auro": False, "remapping": False,
                             "aes_insert": False, "mixbus": False}},
    "/system/info/optimizer/release": "SYM('9.9.9-fake')", "/system/info/system/system_release": "1.0",
    "/optimizer/preset/available": [
        {"id": 0, "name": "Builtin", "type": "SYM('factory')", "empty": False, "locked": True},
        {"id": 1, "name": "Preset A", "type": "SYM('user')", "empty": False},
        {"id": 2, "name": "Preset B", "type": "SYM('user')", "empty": False},
        {"id": 3, "name": "Preset C", "type": "SYM('user')", "empty": False},
        {"id": 4, "empty": True}],
    "/optimizer/preset/current/id": 1,
    "/monitoring/downmix/current/uid": "UID('m')",
    "/monitoring/downmix/available": [{"name": "Mono", "uid": "UID('m')", "active": True},
                                      {"name": "Stereo", "uid": "UID('s')", "active": True},
                                      {"name": "Hidden one", "uid": "UID('h')", "active": False}],
    "/monitoring/entities/source": [SRC("Talkback", True), SRC("Computer"), SRC("Turntable")],
    "/monitoring/entities/destination": [
        {"name": "Phones", "is_mixbus": False, "is_speaker_set": False, "is_headphone": True, "is_hidden_main": True},
        {"name": "Monitors", "is_mixbus": False, "is_speaker_set": True, "is_headphone": False, "is_hidden_main": False,
         "channels_name": ["L", "R"]}],
    "/monitoring/speaker_functions/channels": {"L": {"state": False}, "R": {"state": False}},
    "/monitoring/preset/available": [{"id": 0, "name": "Default"}],
    "/system/usb_remote/volume_step": 2.0,
    "/optimizer/volume/volume": -30.0, "/optimizer/volume/mute": False, "/optimizer/volume/bypass": False,
    "/optimizer/volume/dim": False, "/optimizer/volume/opt_acoustic": True, "/optimizer/volume/opt_levels": True,
    "/optimizer/volume/opt_delays": True, "/optimizer/volume/volume_recall": False,
    "/monitoring/control/source_select_mode": "SYM('toggle')", "/monitoring/control/main_selected_sources": [1],
    "/monitoring/control/main_destination": 1, "/monitoring/control/b_enable_bass_management": False,
    "/monitoring/control/b_source_select_mode_sum": False, "/monitoring/control/phone_mute": False,
    "/monitoring/control/b_phones_mute_main_monitor": False, "/monitoring/speaker_functions/mode": "SYM('solo')",
    "/monitoring/speaker_functions/b_front_solo": False, "/monitoring/speaker_functions/b_surround_solo": False,
    "/monitoring/speaker_functions/b_height_solo": False, "/monitoring/speaker_functions/b_subwoofer_solo": False,
    "/monitoring/speaker_functions/channels/L/state": False, "/monitoring/speaker_functions/channels/R/state": False,
    "/system/network/ethernet/carrier": True, "/system/network/ethernet/method": "SYM('manual')",
    "/system/admin/soundcard/utils/srpc/b_connected": True,
}
WRITES = []


def frame(payload, opcode=0x2):
    n = len(payload)
    return (bytes([0x80 | opcode, n]) if n < 126 else bytes([0x80 | opcode, 126]) + struct.pack(">H", n)) + payload


def message(path, value):
    """Scalars (and the empty 'no such path' answer) are wrapped as {"_": v}; lists and objects go bare."""
    p = path.encode()
    body = json.dumps(value if isinstance(value, (list, dict)) and value != {} else {"_": value}).encode()
    return frame(struct.pack(">ibi", 5 + len(p) + len(body), 0, len(p)) + p + body)


def apply(parent, payload):
    for key, value in payload.items():
        path = f"{parent}/{key}"
        if path == "/monitoring/control/main_selected_sources" and isinstance(value, int):
            # like the real Nova: Sum mode adds/removes the source, Toggle mode replaces the selection
            chosen = list(STORE[path])
            if STORE["/monitoring/control/source_select_mode"] == "SYM('sum')":
                value = [x for x in chosen if x != value] if value in chosen else sorted(chosen + [value])
            else:
                value = [value]
        STORE[path] = value
        if parent == "/optimizer/preset/current" and key == "id":
            STORE["/optimizer/preset/current/id"] = value
    if parent == "/optimizer/volume" and "dvolume" in payload:
        STORE["/optimizer/volume/volume"] += payload["dvolume"]
    WRITES.append((parent, payload))
    print("WRITE", parent, json.dumps(payload), flush=True)


def serve(conn):
    data = conn.recv(4096)
    if not data.startswith(b"GET /ws"):
        conn.sendall(b"HTTP/1.1 200 OK\r\nServer: TrinnovDisplayServer/1.0\r\nContent-Length: 0\r\n\r\n")
        conn.close()
        return
    key = [l.split(b": ")[1] for l in data.split(b"\r\n") if l.lower().startswith(b"sec-websocket-key")][0]
    accept = base64.b64encode(hashlib.sha1(key + GUID).digest())
    conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                 b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
    threading.Thread(target=lambda: [time.sleep(5) or conn.sendall(frame(b"", 0x9)) for _ in range(1000)], daemon=True).start()
    buf = b""
    try:
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
            while len(buf) >= 6:
                n, off = buf[1] & 0x7F, 2
                if n == 126:
                    n, off = struct.unpack(">H", buf[2:4])[0], 4
                if len(buf) < off + 4 + n:
                    break
                mask = buf[off:off + 4]
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(buf[off + 4:off + 4 + n]))
                opcode, buf = buf[0] & 0xF, buf[off + 4 + n:]
                if opcode != 0x2 or len(payload) < 9:
                    continue
                _, kind, plen = struct.unpack(">ibi", payload[:9])
                path, body = payload[9:9 + plen].decode(), payload[9 + plen:]
                if kind == 0:
                    conn.sendall(message(path, STORE.get(path, {})))
                elif kind == 3:
                    apply(path, json.loads(body))
    except OSError:
        pass
    conn.close()


srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", PORT))
srv.listen(8)
print(f"fake Nova on 127.0.0.1:{PORT}", flush=True)
while True:
    c, _ = srv.accept()
    threading.Thread(target=serve, args=(c,), daemon=True).start()
