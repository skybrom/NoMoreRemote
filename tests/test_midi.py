#!/usr/bin/env python3
"""MIDI tests: a fake Nova plus fake MIDI controllers (CoreMIDI virtual ports). Never touches a real Nova.

    python3 tests/test_midi.py

Needs the Swift compiler (it builds the MIDI helper and a small test sender). Your real MIDI devices are ignored.
"""
import json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOME = tempfile.mkdtemp(prefix="trinnovmidi-")
BUILD = os.path.join(HOME, "build")
os.makedirs(BUILD)
PORT = 8099
PORTS = "Test Controller,Test DAW Out,Trinnov Functions (virtual port)"
ENV = {**os.environ, "HOME": HOME, "TRINNOV_IP": "127.0.0.1", "TRINNOV_PORT": str(PORT), "TRINNOV_PREFIXES": "127.0.0",
       "TRINNOV_SUPPORT_DIR": os.path.join(HOME, "support"), "TRINNOV_LAUNCHER_DIR": os.path.join(HOME, "launchers"),
       "TRINNOV_NO_REVEAL": "1", "TRINNOV_MIDI_BIN": os.path.join(BUILD, "trinnov-midi"), "TRINNOV_MIDI_ONLY_PORT": PORTS}
passed = failed = 0
procs = []


def check(name, ok, detail=""):
    global passed, failed
    ok = bool(ok)
    passed, failed = passed + ok, failed + (not ok)
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"   -> {detail}"))


def build(src, out):
    r = subprocess.run(["swiftc", "-O", "-o", out, src], capture_output=True, text=True)
    if r.returncode:
        print("could not build", src, r.stderr[-400:])
        sys.exit(1)


fake_log = os.path.join(HOME, "fake.log")


def writes():
    return [l.split(" ", 1)[1] for l in open(fake_log).read().splitlines() if l.startswith("WRITE")]


def start_app():
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), "--no-open", "--port", "8797"], env=ENV,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(p)
    time.sleep(4)
    return p


def api(path, body=None):
    global TOKEN
    req = urllib.request.Request("http://127.0.0.1:8797" + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Token": TOKEN, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def token():
    return re.search(r'const TOKEN = "([^"]+)"', urllib.request.urlopen("http://127.0.0.1:8797/").read().decode()).group(1)


def send(*args, source="Test Controller"):
    subprocess.run([os.path.join(BUILD, "midi_send"), "source", source, "2", *map(str, args)], capture_output=True, timeout=30)
    time.sleep(0.8)


try:
    build(os.path.join(ROOT, "midi", "trinnov-midi.swift"), os.path.join(BUILD, "trinnov-midi"))
    build(os.path.join(HERE, "midi_send.swift"), os.path.join(BUILD, "midi_send"))
    fake = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_nova.py"), str(PORT)], stdout=open(fake_log, "w"), stderr=subprocess.STDOUT)
    procs.append(fake)
    time.sleep(1)
    app = start_app()
    TOKEN = token()
    menu = api("/api/menu")
    ids = {i["label"]: i["id"] for s in menu["sections"] for i in s["items"]}
    m = api("/api/midi")
    check("the MIDI helper is listening", m["status"] == "listening", m["status"])

    # automatic mapping
    r = api("/api/midi/default", {})
    usable = [i for s in menu["sections"] for i in s["items"] if not i.get("view_only") and i.get("danger") != "confirm"]
    check("map everything gives each usable function a note, Power and view-only left out", r["ok"] and r["count"] == len(usable), (r, len(usable)))
    m = api("/api/midi")
    first = m["bindings"][0]
    check("notes start at 36 on channel 1, in menu order", first["msg"] == {"type": "note", "channel": 1, "number": 36}, first)
    check("Power functions are not mapped", not any(b["section"] == "Power" for b in m["bindings"]))
    target = next(b for b in m["bindings"] if b["selector"] == "Preset C")
    n = len(writes())
    send("note", 1, target["msg"]["number"], 100)
    check("a note from a controller runs its function", writes()[n:] == ['/optimizer/preset/current {"id": 3}'], writes()[n:])
    n = len(writes())
    send("note", 1, target["msg"]["number"], 0)
    check("a note off (release) does nothing", writes()[n:] == [], writes()[n:])
    n = len(writes())
    send("note", 1, 99, 100)
    check("an unmapped note does nothing", writes()[n:] == [], writes()[n:])
    n = len(writes())
    subprocess.run([os.path.join(BUILD, "midi_send"), "dest", "note", "1", str(target["msg"]["number"]), "100"], capture_output=True, timeout=30)
    time.sleep(0.8)
    check("sending to the 'Trinnov Functions' virtual port works too", writes()[n:] == ['/optimizer/preset/current {"id": 3}'], writes()[n:])

    # learn
    api("/api/midi/clear", {})
    check("clear removes every mapping", api("/api/midi")["bindings"] == [])
    r = api("/api/midi/learn", {"id": ids["Preset B"]})
    check("learn starts", r["ok"] and api("/api/midi")["learning"]["label"] == "Preset B", r)
    send("cc", 1, 7, 30, source="Test Controller")
    check("a quiet CC (below 64) is ignored while learning", api("/api/midi")["learning"] is not None)
    send("note", 1, 50, 100, source="Test DAW Out")
    check("a controller's DAW port is never learned from", api("/api/midi")["learning"] is not None and not api("/api/midi")["bindings"])
    send("cc", 2, 20, 127, source="Test Controller")
    m = api("/api/midi")
    check("a CC press is learned", m["learning"] is None and m["bindings"] and m["bindings"][0]["msg"] == {"type": "cc", "channel": 2, "number": 20}, m)
    n = len(writes())
    send("cc", 2, 20, 127, "cc", 2, 20, 0, "cc", 2, 20, 127)
    check("a CC of 64 or more runs its function on every press; the release (low value) does nothing", writes()[n:] == ['/optimizer/preset/current {"id": 2}'] * 2, writes()[n:])
    menu2 = api("/api/menu")
    badge = next(i["midi"] for s in menu2["sections"] for i in s["items"] if i["label"] == "Preset B")
    check("the tile shows its MIDI badge", badge == "CC20", badge)

    api("/api/midi/learn", {"id": ids["Preset A"]})
    send("program", 3, 5)
    n = len(writes())
    send("program", 3, 5)
    check("a program change can be learned and runs its function", writes()[n:] == ['/optimizer/preset/current {"id": 1}'], writes()[n:])

    r = api("/api/midi/learn", {"id": ids["Power Off"]})
    check("Power cannot be learned", not r["ok"] and "Power" in r["error"], r)
    r = api("/api/midi/learn", {"id": ids["DHCP"]})
    check("view-only entries cannot be learned", not r["ok"], r)

    # a Power binding written by hand is still refused
    api("/api/midi/clear", {})
    mp = os.path.join(HOME, "support", "midi_map.json")
    json.dump({"version": 1, "bindings": [{"msg": {"type": "note", "channel": 1, "number": 90}, "section": "Power", "selector": "Power Off", "label": "Power Off"}]}, open(mp, "w"))
    app.terminate(); app.wait(timeout=10)
    app = start_app(); TOKEN = token()
    n = len(writes())
    send("note", 1, 90, 100)
    check("a Power binding in the file is refused over MIDI", writes()[n:] == [], writes()[n:])

    # persistence, read-only, unlearn
    api("/api/midi/default", {})
    app.terminate(); app.wait(timeout=10)
    app = start_app(); TOKEN = token()
    check("mappings survive a restart", len(api("/api/midi")["bindings"]) == len(usable))
    api("/api/readonly", {"on": True})
    n = len(writes())
    send("note", 1, 36, 100)
    check("Read-only mode blocks MIDI, nothing sent", writes()[n:] == [], writes()[n:])
    api("/api/readonly", {"on": False})
    api("/api/midi/unlearn", {"id": ids["Preset C"]})
    sel = [b for b in api("/api/midi")["bindings"] if b["selector"] == "Preset C"]
    check("unlearn removes a function's mapping", sel == [], sel)
    check("the last MIDI message is reported", api("/api/midi")["last"] is not None)
finally:
    for p in procs:
        p.terminate()
    time.sleep(0.5)
    shutil.rmtree(HOME, ignore_errors=True)
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
