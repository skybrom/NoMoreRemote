#!/usr/bin/env python3
"""The Trinnov Bypass tool through Trinnov Functions' service, and directly. Fake Nova only.

    python3 tests/test_bypass_service.py
"""
import json, os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BYPASS = os.path.join(os.path.dirname(ROOT), "TrinnovBypass", "src", "trinnov_bypass.py")
if not os.path.exists(BYPASS):
    print("TrinnovBypass/src/trinnov_bypass.py not found next to this project: skipped")
    sys.exit(0)
HOME = tempfile.mkdtemp(prefix="trinnovbypass-")
PORT = 8103
ENV = {**os.environ, "HOME": HOME, "TRINNOV_IP": "127.0.0.1", "TRINNOV_PORT": str(PORT), "TRINNOV_PREFIXES": "127.0.0",
       "TRINNOV_SUPPORT_DIR": os.path.join(HOME, "support"), "TRINNOV_NO_REVEAL": "1"}
fake_log = os.path.join(HOME, "fake.log")
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    ok = bool(ok)
    passed, failed = passed + ok, failed + (not ok)
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"   -> {detail}"))


def writes():
    return [l.split(" ", 1)[1] for l in open(fake_log).read().splitlines() if l.startswith("WRITE")]


def bypass(cmd):
    r = subprocess.run([sys.executable, BYPASS, cmd], env=ENV, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout.strip()


fake = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_nova.py"), str(PORT)], stdout=open(fake_log, "w"), stderr=subprocess.STDOUT)
time.sleep(1)
app = None
try:
    code, out = bypass("status")
    check("direct: status", code == 0 and "ACTIVE" in out, out)
    code, out = bypass("bypass")
    check("direct: bypass", code == 0 and "BYPASSED" in out and writes()[-1] == '/optimizer/volume {"bypass": true}', (out, writes()[-1:]))
    code, out = bypass("engage")
    check("direct: engage", code == 0 and "ACTIVE" in out and writes()[-1] == '/optimizer/volume {"bypass": false}', (out, writes()[-1:]))

    app = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), "--no-open", "--port", "8803"], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(4)
    n = len(writes())
    code, out = bypass("toggle")
    check("service: toggle works and reports the new state", code == 0 and "BYPASSED" in out and writes()[n:] == ['/optimizer/volume {"bypass": true}'], (out, writes()[n:]))
    code, out = bypass("bypass")
    check("service: bypass when already bypassed says so and sends nothing", code == 0 and "already" in out and len(writes()) == n + 1, (out, writes()[n:]))
    code, out = bypass("engage")
    check("service: engage", code == 0 and "ACTIVE" in out and writes()[-1] == '/optimizer/volume {"bypass": false}', (out, writes()[-1:]))
    code, out = bypass("status")
    check("service: status", code == 0 and "ACTIVE" in out, out)
    log = open(os.path.join(HOME, "Library", "Logs", "TrinnovLaunchers.log")).read()
    check("the log says these ran through the service", log.count("via the Trinnov Functions service") >= 3, log[-300:])
    app.terminate(); app.wait(timeout=10); app = None
    n = len(writes())
    code, out = bypass("toggle")
    check("service gone: falls back to the Nova directly", code == 0 and "BYPASSED" in out and writes()[n:] == ['/optimizer/volume {"bypass": true}'], (out, writes()[n:]))
finally:
    for p in (app, fake):
        if p:
            p.terminate()
    time.sleep(0.5)
    shutil.rmtree(HOME, ignore_errors=True)
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
