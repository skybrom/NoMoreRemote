#!/usr/bin/env python3
"""Tests the `trinnov` command and the launcher builder against the FAKE Nova only.

    python3 tests/test_cli.py

Starts tests/fake_nova.py on a spare port, points everything at it (TRINNOV_IP / TRINNOV_PORT, an empty
HOME, loopback-only scanning) so nothing can reach a real Nova, then checks outputs and the exact
messages the fake received.
"""
import json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PORT = 8095
HOME = tempfile.mkdtemp(prefix="trinnovtest-")
ENV = {**os.environ, "HOME": HOME, "TRINNOV_IP": "127.0.0.1", "TRINNOV_PORT": str(PORT), "TRINNOV_PREFIXES": "127.0.0",
       "TRINNOV_SUPPORT_DIR": os.path.join(HOME, "support"), "TRINNOV_LAUNCHER_DIR": os.path.join(HOME, "launchers"), "TRINNOV_NO_REVEAL": "1"}
fake_log = os.path.join(HOME, "fake.log")
fake = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_nova.py"), str(PORT)],
                        stdout=open(fake_log, "w"), stderr=subprocess.STDOUT)
time.sleep(1.2)
passed = failed = 0


def trinnov(*args):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "trinnov.py"), *args], env=ENV, capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout + r.stderr).strip()


def writes():
    return [l.split(" ", 1)[1] for l in open(fake_log).read().splitlines() if l.startswith("WRITE")]


def check(name, ok, detail=""):
    global passed, failed
    ok = bool(ok)
    passed, failed = passed + ok, failed + (not ok)
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"   -> {detail}"))


try:
    code, out = trinnov("list")
    check("list shows the Nova's own presets, sources, speaker set", code == 0 and "Preset A" in out and "Computer" in out and "Monitors" in out, out[:300])
    check("list hides what the Nova does not have (factory preset, hidden source)", "Builtin" not in out and "Talkback" not in out, out[:300])
    code, out = trinnov("list", "--json")
    check("list --json is valid JSON", code == 0 and json.loads(out)["sections"], out[:200])

    n = len(writes())
    code, out = trinnov("on", "Bypass")
    check("on Bypass switches it on", code == 0 and out.endswith("on") and writes()[n:] == ['/optimizer/volume {"bypass": true}'], (out, writes()[n:]))
    code, out = trinnov("on", "Bypass")
    check("on Bypass again does nothing (already on)", code == 0 and "already on" in out and len(writes()) == n + 1, out)
    code, out = trinnov("off", "Bypass")
    check("off Bypass switches it off", code == 0 and out.endswith("off") and writes()[-1] == '/optimizer/volume {"bypass": false}', out)
    code, out = trinnov("toggle", "Common", "Dim")
    check("toggle with a section name", code == 0 and writes()[-1] == '/optimizer/volume {"dim": true}', out)

    code, out = trinnov("preset", "Preset B")
    check("preset by name", code == 0 and writes()[-1] == '/optimizer/preset/current {"id": 2}', out)
    code, out = trinnov("preset", "#3")
    check("preset by number (#3)", code == 0 and writes()[-1] == '/optimizer/preset/current {"id": 3}', out)
    code, out = trinnov("preset", "toggle")
    check("preset toggle goes back to the previous preset", code == 0 and writes()[-1] == '/optimizer/preset/current {"id": 2}', (out, writes()[-3:]))
    code, out = trinnov("preset", "Preset")
    check("an ambiguous name is refused (exit 3) and lists the candidates", code == 3 and "Preset A" in out and "Preset C" in out, (code, out))
    n = len(writes())
    code, out = trinnov("preset", "Nonexistent")
    check("an unknown name is refused (exit 3), nothing sent", code == 3 and len(writes()) == n, (code, out))

    code, out = trinnov("press", "Sum")
    check("press Sum sets Sum mode", code == 0 and writes()[-1] == '/monitoring/control {"source_select_mode": "SYM(\'sum\')"}', out)
    trinnov("on", "Turntable")
    check("sum mode: on Turntable sends just the source number", writes()[-1] == '/monitoring/control {"main_selected_sources": 2}', writes()[-2:])
    code, out = trinnov("status", "Sources")
    check("both sources lit in sum mode", out.count("ON") == 2, out)
    code, out = trinnov("off", "Computer")
    check("sum mode: off Computer removes just that source", code == 0 and writes()[-1] == '/monitoring/control {"main_selected_sources": 1}', out)
    code, out = trinnov("off", "Computer")
    check("sum mode: off again does nothing", code == 0 and "already off" in out, out)
    trinnov("press", "Toggle")
    code, out = trinnov("off", "Turntable")
    check("toggle mode: off on a choice is refused", code == 2, (code, out))

    n = len(writes())
    code, out = trinnov("press", "Power Off")
    check("Power Off without --yes is refused, nothing sent", code == 1 and "--yes" in out and len(writes()) == n, out)
    code, out = trinnov("press", "Power Off", "--dry-run")
    check("--dry-run shows what would be sent, sends nothing", code == 0 and "dry run" in out and len(writes()) == n, out)
    code, out = trinnov("press", "Power Off", "--yes")
    check("Power Off --yes sends the shutdown", code == 0 and writes()[-1] == '/system/power_supply {"shutdown": true}', out)
    code, out = trinnov("press", "DHCP")
    check("Network is view only", code == 1 and "view only" in out, out)

    code, out = trinnov("volume", "up", "2")
    check("volume up 2", code == 0 and writes()[-1] == '/optimizer/volume {"dvolume": 2.0}', out)
    code, out = trinnov("volume", "set", "-26")
    check("volume set -26 goes to the level", code == 0 and "dvolume" in writes()[-1], (out, writes()[-1]))
    n = len(writes())
    code, out = trinnov("volume", "set", "0")
    check("a jump over 10 dB is refused", code == 1 and "--force" in out and len(writes()) == n, out)
    code, out = trinnov("mute", "on")
    check("mute on", code == 0 and writes()[-1] == '/optimizer/volume {"mute": true}', out)
    code, out = trinnov("mute", "on")
    check("mute on again does nothing", "already on" in out, out)

    # launchers (the command line each launcher runs is executed here, against the fake)
    sys.path.insert(0, ROOT)
    srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), "--no-open", "--port", "8796"], env=ENV,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(4)
    try:
        base = "http://127.0.0.1:8796"
        tok = re.search(r'const TOKEN = "([^"]+)"', urllib.request.urlopen(base + "/").read().decode()).group(1)

        def api(path, body):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"X-Token": tok, "Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=120))

        menu = json.load(urllib.request.urlopen(urllib.request.Request(base + "/api/menu", headers={"X-Token": tok})))
        ids = {i["label"]: i["id"] for s in menu["sections"] for i in s["items"]}
        r = api("/api/launcher", {"id": ids["Preset C"], "reveal": False})
        launchers = os.path.join(HOME, "launchers")
        check("a tile makes a launcher app", r.get("ok") and os.path.isdir(os.path.join(launchers, "Presets - Preset C.app")), r)
        check("the command line is installed outside the download folder", os.path.isfile(os.path.join(HOME, "support", "cli", "trinnov.py")), os.listdir(HOME))
        script = subprocess.run(["osadecompile", os.path.join(launchers, "Presets - Preset C.app")], capture_output=True, text=True).stdout
        check("the launcher calls the installed command by name", "trinnov.py" in script and "press" in script and "Presets" in script and "Preset C" in script, script)
        r = api("/api/launcher", {"id": ids["Power Off"], "reveal": False})
        script = subprocess.run(["osadecompile", os.path.join(launchers, "Power - Power Off.app")], capture_output=True, text=True).stdout
        check("the Power launcher asks first and passes --yes only after the dialog", "display dialog" in script and "--yes" in script, script)
        r = api("/api/launcher", {"id": ids["DHCP"], "reveal": False})
        check("no launcher for view-only Network tiles", not r.get("ok"), r)
        r = api("/api/launcher", {"all": True, "reveal": False})
        count = len([f for f in os.listdir(launchers) if f.endswith(".app")])
        check("create all builds one launcher per usable tile", r.get("ok") and count >= 20, (r, count))
        # run the exact command a launcher contains, against the fake
        script = subprocess.run(["osadecompile", os.path.join(launchers, "Presets - Preset C.app")], capture_output=True, text=True).stdout
        cmd = re.search(r'do shell script "(.*)"', script).group(1).replace('\\"', '"').replace("\\\\", "\\")
        n = len(writes())
        out = subprocess.run(["bash", "-c", cmd], env=ENV, capture_output=True, text=True).stdout
        check("the launcher's command line works against the Nova", writes()[n:] == ['/optimizer/preset/current {"id": 3}'], (cmd, out, writes()[n:]))
        check("launcher apps are signed and run as agents", subprocess.run(["codesign", "-v", os.path.join(launchers, "Presets - Preset C.app")],
                                                                          capture_output=True).returncode == 0)
    finally:
        srv.terminate()

finally:
    fake.terminate()
    shutil.rmtree(HOME, ignore_errors=True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
