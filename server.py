#!/usr/bin/env python3
"""Trinnov Functions: every function of La Remote, from your Mac, without La Remote.

Runs a small local web app (http://127.0.0.1:<port>) that talks to your Trinnov Nova over the
network. Nothing is hard-coded: the Nova is found on your network, and the menu (sources, speaker
sets, presets, downmixes, features) is read from that Nova.

    python3 server.py                 # find the Nova, open the app in your browser
    python3 server.py --read-only     # look but never write
    python3 server.py --ip 192.168.1.50
"""
import argparse, base64, contextlib, io, json, os, re, secrets, select, shlex, shutil, signal, socket, struct, subprocess, sys, threading, time
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
NOVA_PORT = int(os.environ.get("TRINNOV_PORT", "80"))
SUPPORT = os.environ.get("TRINNOV_SUPPORT_DIR") or os.path.expanduser("~/Library/Application Support/TrinnovFunctions")
LAUNCHER_DIR = os.environ.get("TRINNOV_LAUNCHER_DIR") or os.path.expanduser("~/Documents/Trinnov Stream Deck")
CACHE = os.path.join(SUPPORT, "last_ip")


# ------------------------------------------------------------------ finding the Nova
def is_nova(ip, timeout):
    try:
        with socket.create_connection((ip, NOVA_PORT), timeout=timeout) as s:
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


def find_nova(forced_ip=None):
    if forced_ip:
        return forced_ip
    try:
        cached = open(CACHE).read().strip()
    except OSError:
        cached = ""
    if cached and is_nova(cached, 1.0):
        return cached
    candidates = [f"{p}.{i}" for p in local_prefixes() for i in range(1, 255)]
    with ThreadPoolExecutor(max_workers=128) as pool:
        hits = [ip for ip, ok in zip(candidates, pool.map(lambda ip: is_nova(ip, 0.7), candidates)) if ok]
    if not hits:
        return None
    try:
        os.makedirs(SUPPORT, exist_ok=True)
        open(CACHE, "w").write(hits[0])
    except OSError:
        pass
    return hits[0]


# ------------------------------------------------------------------ talking to the Nova
def mask_frame(payload, opcode=0x2):
    mask = os.urandom(4)
    n = len(payload)
    hdr = bytes([0x80 | opcode, 0x80 | n]) if n < 126 else bytes([0x80 | opcode, 0x80 | 126]) + struct.pack(">H", n)
    return hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


class NovaLink:
    """One websocket to the Nova: answers its pings, keeps the latest value of every path read."""
    GET, PUBREQ = 0, 3

    def __init__(self, ip):
        self.ip = ip
        self.values = {}
        self.stamps = {}
        self.cond = threading.Condition()
        self.send_lock = threading.Lock()
        self.stream = b""
        self.raw = b""
        self.alive = False
        self.sock = None

    def connect(self):
        s = socket.create_connection((self.ip, NOVA_PORT), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET /ws HTTP/1.1\r\nHost: {self.ip}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = s.recv(1024)
            if not chunk:
                raise OSError("connection closed during handshake")
            resp += chunk
        head, _, rest = resp.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise OSError("the Nova refused the websocket")
        s.settimeout(None)
        self.sock, self.raw, self.alive = s, rest, True
        threading.Thread(target=self._reader, daemon=True).start()

    # -- receiving
    def _reader(self):
        try:
            self._consume_frames()
            while self.alive:
                r, _, _ = select.select([self.sock], [], [], 1)
                if not r:
                    continue
                data = self.sock.recv(1 << 20)
                if not data:
                    break
                self.raw += data
                self._consume_frames()
        except (OSError, ValueError):
            pass
        self.alive = False
        with self.cond:
            self.cond.notify_all()

    def _consume_frames(self):
        buf = self.raw
        while len(buf) >= 2:
            opcode, n, off = buf[0] & 0xF, buf[1] & 0x7F, 2
            if n == 126:
                if len(buf) < 4:
                    break
                n, off = struct.unpack(">H", buf[2:4])[0], 4
            elif n == 127:
                if len(buf) < 10:
                    break
                n, off = struct.unpack(">Q", buf[2:10])[0], 10
            if len(buf) < off + n:
                break
            payload, buf = buf[off:off + n], buf[off + n:]
            if opcode == 0x9:                       # ping: the Nova drops clients that ignore it
                self._send_raw(mask_frame(payload[:125], 0xA))
            elif opcode == 0x8:
                self.alive = False
            elif opcode in (0x0, 0x1, 0x2):
                self.stream += payload
                self._consume_messages()
        self.raw = buf

    def _consume_messages(self):
        while len(self.stream) >= 4:
            n = struct.unpack(">i", self.stream[:4])[0]
            if len(self.stream) < 4 + n:
                return
            msg, self.stream = self.stream[4:4 + n], self.stream[4 + n:]
            if len(msg) < 5:
                continue
            plen = struct.unpack(">i", msg[1:5])[0]
            path = msg[5:5 + plen].decode(errors="replace")
            body = msg[5 + plen:]
            try:
                value = json.loads(body.decode()) if body else None
            except ValueError:
                continue
            if isinstance(value, dict) and set(value) == {"_"}:
                value = value["_"]
            with self.cond:
                self.values[path] = value
                self.stamps[path] = time.time()
                self.cond.notify_all()

    # -- sending
    def _send_raw(self, data):
        with self.send_lock:
            self.sock.sendall(data)

    def _message(self, kind, path, obj=None):
        p = path.encode()
        body = json.dumps(obj).encode() if obj is not None else b""
        return mask_frame(struct.pack(">ibi", 5 + len(p) + len(body), kind, len(p)) + p + body)

    def read_many(self, paths, timeout=3.0):
        """Read several paths at once; returns {path: value or None when the Nova did not answer}."""
        paths = list(dict.fromkeys(paths))
        started = time.time()
        for p in paths:
            self._send_raw(self._message(self.GET, p))
        end = started + timeout
        with self.cond:
            while time.time() < end and self.alive:
                if all(self.stamps.get(p, 0) >= started for p in paths):
                    break
                self.cond.wait(0.2)
            return {p: (self.values.get(p) if self.stamps.get(p, 0) >= started else None) for p in paths}

    def read(self, path, timeout=3.0):
        return self.read_many([path], timeout)[path]

    def write(self, path, obj):
        self._send_raw(self._message(self.PUBREQ, path, obj))

    def close(self):
        self.alive = False
        try:
            self.sock.close()
        except OSError:
            pass


# ------------------------------------------------------------------ the menu (read from this Nova)
def sym(v):
    """'SYM('solo')' -> 'solo'; other values unchanged."""
    if isinstance(v, str):
        m = re.fullmatch(r"SYM\('(.*)'\)", v)
        if m:
            return m.group(1)
    return v


def fetch_tree_from_nova(ip):
    """The Nova's own web interface contains the La Remote function list; use it so a different
    firmware gives its own menu. Returns (tree, source) or (None, reason)."""
    try:
        base = f"http://{ip}:{NOVA_PORT}" if NOVA_PORT != 80 else f"http://{ip}"
        html = urllib.request.urlopen(base + "/", timeout=5).read().decode(errors="replace")
        scripts = re.findall(r'src="(js/app[^"]+\.js)"', html)
        if not scripts:
            return None, "no app script found"
        js = urllib.request.urlopen(f"{base}/{scripts[0]}", timeout=20).read().decode(errors="replace")
        tree = None
        for m in re.finditer(r"JSON\.parse\('(\{\"[a-z_]+\":\[)", js):
            i = m.start(1)
            j = i
            while True:
                j = js.index("')", j + 1)
                if js[j - 1] != "\\":
                    break
            try:
                cand = json.loads(js[i:j].replace("\\'", "'"))
            except ValueError:
                continue
            entries = [e for v in cand.values() for e in flatten(v)] if isinstance(cand, dict) else []
            # the La Remote function list: groups of entries that carry an action_path
            if entries and "common" in cand and sum(1 for e in entries if isinstance(e, dict) and "action_path" in e) > 10:
                tree = cand
                break
        if not tree:
            return None, "function list not found in the Nova's interface"
        return tree, f"read from the Nova ({scripts[0]})"
    except Exception as e:                                           # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def load_tree(ip):
    tree, how = fetch_tree_from_nova(ip)
    if tree:
        return tree, how
    with open(os.path.join(HERE, "data", "functions_tree.json")) as f:
        return json.load(f), f"built-in copy (Nova list unavailable: {how})"


def flatten(group):
    """A group is a list of entries, or {'list': [...]} for the conditional ones."""
    if isinstance(group, dict):
        group = [x for v in group.values() for x in (v if isinstance(v, list) else [v])]
    return [e for e in (group or []) if isinstance(e, dict) and "action_path" in e]


def filter_entities(entries, **expected):
    out = []
    for i, e in enumerate(entries or []):
        if all(e.get(k, False) == v for k, v in expected.items()):
            out.append((i, e))
    return out


class Model:
    """The menu for one Nova. Built once from what the Nova reports; states refreshed by poll()."""

    def __init__(self, link, tree, tree_source):
        self.link, self.tree, self.tree_source = link, tree, tree_source
        self.features, self.info, self.sections, self.items = {}, {}, [], {}
        self.lists = {}
        self.last_preset = None
        self.prev_preset = None
        self._load_preset_memory()
        self.volume_step = 1.0
        self.state = {}

    # ---- remembering the previous preset (shared by the app and the `trinnov` command)
    MEMORY = "preset_memory.json"

    def _load_preset_memory(self):
        try:
            m = json.load(open(os.path.join(SUPPORT, self.MEMORY)))
            self.last_preset, self.prev_preset = m.get("current"), m.get("previous")
        except (OSError, ValueError):
            pass

    def _note_preset(self, cur):
        """The Nova's current preset changed (here or elsewhere): the one before it becomes 'previous'."""
        if not isinstance(cur, int) or cur == self.last_preset:
            return
        if self.last_preset is not None:
            self.prev_preset = self.last_preset
        self.last_preset = cur
        try:
            os.makedirs(SUPPORT, exist_ok=True)
            json.dump({"current": cur, "previous": self.prev_preset}, open(os.path.join(SUPPORT, self.MEMORY), "w"))
        except OSError:
            pass

    # ---- building
    def build(self):
        L = self.link
        cfg = L.read("/config") or {}
        self.features = cfg.get("features", {}) or {}
        self.info = {
            "product": cfg.get("product_class_str") or cfg.get("class_name") or "Trinnov",
            "serial": cfg.get("serial"),
            "market": cfg.get("product_market_str"),
        }
        fw = L.read_many(["/system/info/optimizer/release", "/system/info/system/system_release"])
        self.info["firmware"] = " / ".join(str(sym(v)) for v in fw.values() if v)
        got = L.read_many(["/optimizer/preset/available", "/monitoring/downmix/available",
                           "/monitoring/entities/source", "/monitoring/entities/destination",
                           "/monitoring/speaker_functions/channels", "/monitoring/preset/available",
                           "/system/usb_remote/volume_step", "/optimizer/preset/current/id"], timeout=5)
        self.lists = {k: v for k, v in got.items()}
        step = got.get("/system/usb_remote/volume_step")
        self.volume_step = float(step) if isinstance(step, (int, float)) and step > 0 else 1.0
        self._note_preset(got.get("/optimizer/preset/current/id"))

        f = self.features
        tree = self.tree
        entries = list(flatten(tree.get("common")))
        entries += flatten(tree.get("monitoring")) if f.get("monitoring") else flatten(tree.get("no_monitoring"))
        if f.get("codec"):
            entries += flatten(tree.get("listening_format"))
        if f.get("dolby"):
            entries += flatten(tree.get("dolby"))
        if f.get("auro"):
            entries += flatten(tree.get("auro"))
        if f.get("remapping"):
            entries += flatten(tree.get("remapping"))
        entries += flatten(tree.get("power"))

        groups = {}
        for e in entries:
            groups.setdefault(e.get("container", "Other"), []).append(e)

        sources = self.lists["/monitoring/entities/source"] or []
        dests = self.lists["/monitoring/entities/destination"] or []
        speaker_funcs = self.lists["/monitoring/speaker_functions/channels"] or {}
        sections, items = [], {}

        def add(section, item):
            iid = f"{section['key']}:{len(section['items'])}"
            item["id"] = iid
            section["items"].append(item)
            items[iid] = item

        def tile(e, label=None, value="__entry__", name=None, sub=None):
            return {"label": label or e.get("name"), "sub": sub, "path": e["action_path"],
                    "value": e.get("action_value") if value == "__entry__" else value,
                    "type": e.get("action_type"), "danger": None}

        titles = {"Runtime": "Processing"}
        for container, es in groups.items():
            sec = {"key": re.sub(r"\W+", "_", container).lower(), "title": titles.get(container, container),
                   "note": None, "items": []}
            if container == "Presets":
                e = es[0]
                for i, p in filter_entities(self.lists["/optimizer/preset/available"], empty=False):
                    if sym(p.get("type")) == "factory":
                        continue
                    add(sec, tile(e, label=p.get("name") or f"Preset {i}", value=p.get("id", i), sub=f"#{p.get('id', i)}"))
                if sec["items"]:
                    sec["items"].append({"id": "presets:toggle", "label": "Toggle preset", "sub": "back to the previous one",
                                         "path": "/optimizer/preset/current/id", "value": None, "type": "toggle-preset",
                                         "danger": None})
                    items["presets:toggle"] = sec["items"][-1]
                else:
                    sec["note"] = "This Nova has no user presets."
            elif container == "Downmix":
                e = es[0]
                for i, d in enumerate(self.lists["/monitoring/downmix/available"] or []):
                    if d.get("active") is True:
                        add(sec, tile(e, label=d.get("name"), value=d.get("uid")))
            elif container == "Sources":
                e = es[0]
                if e.get("action_path") == "/metapresets/current/id":
                    continue                                # Dolby/Atmos style source list (other products)
                for i, s in filter_entities(sources, is_hidden_main=False, is_mixbus=False, is_locked=False):
                    add(sec, tile(e, label=s.get("name", "").strip() or f"Source {i}", value=i, sub=f"#{i}"))
            elif container == "Speaker Sets":
                e = [x for x in es if x["action_path"].endswith("main_destination")][0]
                for i, d in filter_entities(dests, is_mixbus=False, is_speaker_set=True, is_hidden_main=False):
                    add(sec, tile(e, label=d.get("name", "").strip(), value=i, sub=f"#{i}"))
                add(sec, {"label": "Next Spk", "sub": "next speaker set", "path": "/monitoring/control/next_speaker_set",
                          "value": True, "type": "boolean", "danger": None, "momentary": True})
            elif container == "Send Sources":
                for e in es:
                    if e["name"] == "Mixbuses" and not f.get("mixbus"):
                        continue
                    if e["name"] == "Mixbuses":
                        for i, s in filter_entities(sources, is_mixbus=True):
                            add(sec, tile(e, label=s.get("name", "").strip(), value=i, sub=f"#{i}"))
                    elif e["name"] == "Output":
                        for i, d in filter_entities(dests, is_mixbus=False, is_speaker_set=False, is_headphone=False):
                            add(sec, tile(e, label=d.get("name", "").strip(), value=i, sub=f"#{i}"))
                if not sec["items"]:
                    sec["note"] = "This Nova has no send sources or outputs."
            elif container == "Snapshots":
                e = es[0]
                sec["title"] = "Snapshots"
                sec["note"] = "The Nova's own La Remote editor does not offer snapshots; shown because the Nova reports them."
                for p in (self.lists["/monitoring/preset/available"] or []):
                    add(sec, tile(e, label=p.get("name"), value=p.get("id"), sub=f"#{p.get('id')}"))
            elif container == "Speaker Tools":
                chan_names = {n for _, d in filter_entities(dests, is_speaker_set=True) for n in (d.get("channels_name") or [])}
                for e in es:
                    if e["name"] == "SpkFunc":
                        for ch in speaker_funcs:
                            if ch in chan_names:
                                add(sec, {"label": ch, "sub": "channel", "path": f"/monitoring/speaker_functions/channels/{ch}/state",
                                          "value": True, "type": "boolean", "danger": None})
                    else:
                        add(sec, tile(e))
            elif container == "Monitoring":
                for e in es:
                    if e["name"] == "AES Insert" and not f.get("aes_insert"):
                        continue
                    if e["name"] == "DRC" and not f.get("drc"):
                        continue
                    add(sec, tile(e))
            elif container == "Network":
                sec["note"] = "View only: changing network settings from here is disabled on purpose."
                for e in es:
                    t = tile(e)
                    t["view_only"] = True
                    add(sec, t)
            elif container == "Power":
                for e in es:
                    t = tile(e)
                    t["danger"] = "confirm"
                    t["momentary"] = True
                    add(sec, t)
            else:
                for e in es:
                    add(sec, tile(e))
            sections.append(sec)
        step = self.volume_step
        vol = {"key": "volume", "title": "Volume", "note": f"Volume moves in steps of {step:g} dB (the step La Remote's knob uses).", "items": []}
        for label, sub, delta in (("Volume up", f"+{step:g} dB", step), ("Volume down", f"-{step:g} dB", -step)):
            add(vol, {"label": label, "sub": sub, "path": "/optimizer/volume", "value": None, "type": "volume-step",
                      "delta": delta, "danger": None, "momentary": True})
        add(vol, {"label": "Mute", "sub": "mute the monitors", "path": "/optimizer/volume/mute", "value": True, "type": "boolean", "danger": None})
        at = next((n for n, s in enumerate(sections) if s["key"] == "power"), len(sections))
        sections.insert(at, vol)
        self.sections, self.items = sections, items
        self._adjust()

    ACTION_TAILS = ("volume_recall", "next_speaker_set", "shutdown", "reboot")

    def _adjust(self):
        by_path = {}
        for it in self.items.values():
            by_path.setdefault(it["path"], []).append(it)
        for it in self.items.values():
            tail = it["path"].rsplit("/", 1)[-1]
            if tail in self.ACTION_TAILS:
                it["momentary"] = True                    # one-shot actions have no state
            # a lone "= false" button (Phone mute) reads naturally as a toggle: lit when true
            if (it["type"] == "boolean" and it["value"] is False and not it.get("view_only")
                    and not any(o is not it and o["value"] is True for o in by_path[it["path"]])):
                it["value"] = True
            # this firmware names the network values differently from the generic function list
            if it["path"] == "/system/network/ethernet/connected":
                it["path"] = "/system/network/ethernet/carrier"
            elif it["path"] == "/system/network/ethernet/dhcp":
                it.update({"path": "/system/network/ethernet/method", "type": "symbol",
                           "value": "auto" if it["value"] is True else "manual"})

    def selector(self, sec, item):
        """How a function is named in a command or link: its label, or #N when labels repeat."""
        key = lambda i: re.sub(r"\W+", "", i["label"]).lower()
        dup = [i for i in sec["items"] if key(i) == key(item)]
        return item["sub"] if len(dup) > 1 and (item.get("sub") or "").startswith("#") else item["label"]

    def lookup(self, section, selector):
        """Find a function by section title and its label (or #N): how MIDI mappings and launchers name them."""
        norm = lambda s: re.sub(r"\W+", "", str(s)).lower()
        for s in self.sections:
            if norm(s["title"]) != norm(section):
                continue
            for i in s["items"]:
                if norm(self.selector(s, i)) == norm(selector) or norm(i["label"]) == norm(selector):
                    return s, i
        return None, None

    # ---- state
    def state_paths(self):
        paths = {i["path"] for i in self.items.values() if i["type"] not in ("toggle-preset", "volume-step")}
        paths |= {"/optimizer/volume/volume", "/optimizer/volume/mute", "/monitoring/control/source_select_mode",
                  "/monitoring/control/main_selected_sources", "/optimizer/preset/current/id"}
        return sorted(paths)

    def poll(self):
        vals = self.link.read_many(self.state_paths(), timeout=3)
        self._note_preset(vals.get("/optimizer/preset/current/id"))
        out = {}
        for iid, it in self.items.items():
            if it["type"] == "toggle-preset":
                out[iid] = {"on": False, "info": self.preset_name(self.prev_preset)}
                continue
            cur = vals.get(it["path"])
            want = it["value"]
            missing = cur == {}                          # the Nova answers {} for a path it does not have
            if missing:
                cur = None
            if it.get("view_only"):
                shown = None if cur is None else ("yes" if cur is True else "no" if cur is False else str(sym(cur)))
                out[iid] = {"on": cur is True if it["type"] == "string" else self.matches(cur, want, it),
                            "info": shown if it["type"] == "string" else None, "unavailable": missing}
            elif it.get("momentary"):
                out[iid] = {"on": None, "info": None, "unavailable": False}
            else:
                out[iid] = {"on": self.matches(cur, want, it), "info": None, "unavailable": missing,
                            "unreadable": cur is None and not missing}
        vol = vals.get("/optimizer/volume/volume")
        return {"items": out, "volume": vol, "mute": vals.get("/optimizer/volume/mute"),
                "volume_step": self.volume_step, "mode": sym(vals.get("/monitoring/control/source_select_mode"))}

    def preset_name(self, pid):
        for i, p in enumerate(self.lists.get("/optimizer/preset/available") or []):
            if p.get("id", i) == pid and not p.get("empty"):
                return p.get("name")
        return None

    @staticmethod
    def matches(cur, want, it):
        if cur is None:
            return None
        if isinstance(cur, list):
            return want in cur
        if it["type"] == "boolean":
            return bool(cur) is bool(want)
        return sym(cur) == sym(want)

    # ---- acting
    def press(self, iid):
        """Work out the write for one tile. Returns (path, payload, description) or raises ValueError."""
        it = self.items.get(iid)
        if not it:
            raise ValueError("unknown function")
        if it.get("view_only") or it["type"] == "string":
            raise ValueError("this entry is view only")
        if it["type"] == "toggle-preset":
            if self.prev_preset is None:
                raise ValueError("no previous preset known yet: pick a preset first")
            return "/optimizer/preset/current", {"id": self.prev_preset}, f"back to preset {self.prev_preset}"
        if it["type"] == "volume-step":
            return "/optimizer/volume", {"dvolume": it["delta"]}, f"{it['label']} ({it['delta']:+g} dB)"
        path, want = it["path"], it["value"]
        parent, key = path.rsplit("/", 1)
        cur = self.link.read(path)
        if cur == {} and not it.get("momentary"):
            raise ValueError("this Nova does not have this function")
        if cur == {}:
            cur = None
        if path == "/monitoring/control/main_selected_sources":
            # Like the Nova's own Sources page and La Remote: send just the source number. In Sum mode
            # the Nova itself adds it to / removes it from the selection; in Toggle mode it replaces it.
            return parent, {key: want}, f"source {want}"
        if it["type"] == "boolean":
            if it.get("momentary") or cur is None:
                new = want
            elif want is True:
                new = not bool(cur)
            else:
                new = want
            return parent, {key: new}, f"{it['label']} = {new}"
        if it["type"] == "symbol":
            return parent, {key: f"SYM('{sym(want)}')"}, f"{it['label']}"
        return parent, {key: want}, f"{it['label']}"


# ------------------------------------------------------------------ Stream Deck launchers
def install_cli():
    """Copy the command line next to the app's data so launchers keep working if this folder moves."""
    dest = os.path.join(SUPPORT, "cli")
    for sub in ("data", "ui", "midi"):
        os.makedirs(os.path.join(dest, sub), exist_ok=True)
    if os.path.realpath(HERE) != os.path.realpath(dest):
        for name in ("server.py", "trinnov.py", os.path.join("data", "functions_tree.json"),
                     os.path.join("ui", "index.html"), os.path.join("midi", "trinnov-midi.swift")):
            shutil.copy2(os.path.join(HERE, name), os.path.join(dest, name))
    return os.path.join(dest, "trinnov.py")


def applescript_string(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def make_launcher(cli, section, label, selector, confirm, folder, verb="press"):
    """A tiny app that runs `trinnov press <section> <selector>` and shows the result as a notification.
    Dropping it on a Stream Deck key (it creates an 'Open' action) gives a key for that function."""
    name = f"{section} - {label}"
    if selector.startswith("#"):
        name += f" ({selector})"
    name = re.sub(r"[/:\\]", "-", name).strip()
    args = [verb, section, selector] + (["--yes"] if confirm else [])
    cmd = "/usr/bin/python3 " + shlex.quote(cli) + " " + " ".join(shlex.quote(a) for a in args) + " 2>&1"
    guard = ""
    if confirm:
        guard = (f'\tdisplay dialog "{applescript_string(label)}: the Nova will act right away." buttons {{"Cancel", "{applescript_string(label)}"}} '
                 f'default button "Cancel" cancel button "Cancel" with icon caution\n')
    tag = re.sub(r"[^A-Za-z0-9 ._#()-]", "", name)
    logfile = '"$HOME/Library/Logs/TrinnovLaunchers.log"'
    stamp = "$(/bin/date '+%Y-%m-%d %H:%M:%S')"
    opened = f'/bin/echo "{stamp} app {tag}: opened" >> {logfile}'
    err_prefix = f'/bin/echo "{stamp} app {tag}: ERROR" '
    lines = ["try", f'\tdo shell script "{applescript_string(opened)}"', "end try", "try"]
    if guard:
        lines.append(guard.rstrip("\n"))
    ok_sound = "[ -e \"$HOME/Library/Application Support/TrinnovFunctions/sounds_off\" ] || /usr/bin/afplay /System/Library/Sounds/Tink.aiff >/dev/null 2>&1 &"
    bad_sound = ok_sound.replace("Tink", "Basso")
    lines += [f'\tset r to do shell script "{applescript_string(cmd)}"',
              f'\tdo shell script "{applescript_string(ok_sound)}"',
              '\tdisplay notification r with title "Trinnov"',
              "on error e number n", "\tif n is not -128 then", "\t\ttry",
              f'\t\t\tdo shell script "{applescript_string(err_prefix)}" & quoted form of e & " >> {applescript_string(logfile)}"',
              "\t\tend try", f'\t\tdo shell script "{applescript_string(bad_sound)}"',
              '\t\tdisplay notification e with title "Trinnov" subtitle "Failed"', "\tend if", "end try"]
    script = "\n".join(lines) + "\n"
    app = os.path.join(folder, name + ".app")
    src = os.path.join(SUPPORT, "launcher_tmp.applescript")
    os.makedirs(folder, exist_ok=True)
    os.makedirs(SUPPORT, exist_ok=True)
    open(src, "w").write(script)
    shutil.rmtree(app, ignore_errors=True)
    if subprocess.run(["osacompile", "-o", app, src], capture_output=True).returncode != 0:
        raise OSError(f"could not build {name}")
    subprocess.run(["defaults", "write", os.path.join(app, "Contents", "Info"), "LSUIElement", "-bool", "true"], capture_output=True)
    subprocess.run(["codesign", "--force", "--sign", "-", app], capture_output=True)
    return app


# ------------------------------------------------------------------ MIDI
MIDI_SOURCE = os.path.join(HERE, "midi", "trinnov-midi.swift")
MIDI_BIN = os.environ.get("TRINNOV_MIDI_BIN") or os.path.join(SUPPORT, "trinnov-midi")
MIDI_MAP = os.path.join(SUPPORT, "midi_map.json")
MIDI_FIRST_NOTE = 36                                    # automatic mapping starts at C2 (note 36)


def midi_text(msg):
    kind = {"note": "Note", "cc": "CC", "program": "Program"}.get(msg["type"], msg["type"])
    return f"{kind} {msg['number']} (ch {msg['channel']})"


class Midi:
    """Listens to MIDI (through the trinnov-midi helper) and turns messages into function presses.
    Every function can be bound: by learning (click MIDI on a tile, press a key) or automatically."""

    def __init__(self, app):
        self.app = app
        self.lock = threading.RLock()
        self.proc = None
        self.status = "starting"
        self.ports = []
        self.last = None
        self.history = []
        self.learning = None                             # {"section", "selector", "label"} while waiting for a message
        self.version = 0
        self.fired = {}
        self.allow_power = False
        self.bindings = []
        self.only_ports = [x for x in (os.environ.get("TRINNOV_MIDI_ONLY_PORT") or "").split(",") if x]   # tests only
        self.load()

    # -- the mapping file
    def load(self):
        try:
            data = json.load(open(MIDI_MAP))
            self.bindings = [b for b in data.get("bindings", []) if isinstance(b, dict) and "msg" in b]
            self.allow_power = bool(data.get("allow_power", False))
        except (OSError, ValueError):
            self.bindings = []

    def save(self):
        os.makedirs(SUPPORT, exist_ok=True)
        with open(MIDI_MAP, "w") as f:
            json.dump({"version": 1, "allow_power": self.allow_power, "bindings": self.bindings}, f, indent=1)
        self.version += 1

    # -- the helper process
    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _ensure_helper(self):
        if os.path.exists(MIDI_BIN) and (os.environ.get("TRINNOV_MIDI_BIN") or
                                         os.path.getmtime(MIDI_BIN) >= os.path.getmtime(MIDI_SOURCE)):
            return True
        if not os.path.exists(MIDI_SOURCE):
            self.status = "MIDI helper source missing"
            return False
        self.status = "building the MIDI helper (first start, a few seconds)"
        os.makedirs(SUPPORT, exist_ok=True)
        r = subprocess.run(["swiftc", "-O", "-o", MIDI_BIN, MIDI_SOURCE], capture_output=True, text=True)
        if r.returncode != 0:
            self.status = "MIDI needs Apple's developer tools (swiftc): " + (r.stderr.strip().splitlines() or ["build failed"])[-1][:120]
            return False
        return True

    def _run(self):
        while not self.app.stop.is_set():
            if not self._ensure_helper():
                time.sleep(30)
                continue
            try:
                self.proc = subprocess.Popen([MIDI_BIN], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
                self.status = "listening"
                for line in self.proc.stdout:
                    try:
                        self.on_event(json.loads(line))
                    except ValueError:
                        pass
                self.status = "the MIDI helper stopped, restarting"
            except OSError as e:
                self.status = f"could not start the MIDI helper: {e}"
            time.sleep(3)

    def stop(self):
        if self.proc:
            self.proc.terminate()

    # -- incoming messages
    def note_history(self, entry):
        self.history.insert(0, entry)
        del self.history[25:]
        self.last = entry
        self.version += 1

    def on_event(self, ev):
        if ev.get("type") in ("ready", "ports"):
            self.ports = ev.get("sources", [])
            self.version += 1
            return
        port = ev.get("port", "")
        if self.only_ports and port not in self.only_ports:
            return
        msg = {"type": ev["type"], "channel": ev["channel"], "number": ev["number"]}
        value = ev.get("value", 0)
        entry = {"text": midi_text(msg), "value": value, "port": port, "raw": ev.get("raw"),
                 "at": time.strftime("%H:%M:%S"), "result": None}
        with self.lock:
            self.note_history(entry)
            if self.learning:
                self._learn_from(msg, value, port, entry)
                return
            matches = [b for b in self.bindings if b["msg"] == msg]
            if not matches:
                entry["result"] = "not mapped"
                return
            todo = []
            for b in matches:
                if not self.is_press(msg["type"], value, b.get("trigger", "press")):
                    entry["result"] = "release, ignored." + self.zero_velocity_hint(msg, value, entry["raw"])
                    continue
                now = time.time()
                if now - self.fired.get(json.dumps(b["msg"]), 0) < 0.2:
                    entry["result"] = "double trigger, ignored"
                    continue
                self.fired[json.dumps(b["msg"])] = now
                todo.append(b)
        for b in todo:
            threading.Thread(target=self._fire, args=(b, entry), daemon=True).start()

    @staticmethod
    def zero_velocity_hint(msg, value, raw):
        """A Note On with velocity 0 IS a note off in MIDI. Some Stream Deck buttons send it because their panel
        shows default values it never saved."""
        if msg["type"] == "note" and value == 0 and (raw or "")[:1] == "9":
            return (" This is a Note On with velocity 0, which MIDI treats as a note off. If it was a button press, "
                    "open the button's settings and give it a velocity above 0.")
        return ""

    @staticmethod
    def is_press(kind, value, trigger):
        """A note on, a program change, or a CC of 64 or more is a button press (note off / low CC = release).
        Trigger 'any' counts every CC value: for buttons that alternate 0 and 127 (toggle buttons)."""
        if kind == "program":
            return True
        if kind == "note":
            return value > 0
        return True if trigger == "any" else value >= 64

    def _learn_from(self, msg, value, port, entry):
        if "daw" in port.lower():                           # a controller's DAW port chatters constantly; never learn from it
            entry["result"] = "ignored while learning (a DAW port)"
            return
        if msg["type"] == "note" and value == 0:
            entry["result"] = "note off, waiting for the press." + self.zero_velocity_hint(msg, value, entry["raw"])
            return
        # a CC with a low value as the first message means this button's presses can be low: count every value
        trigger = "any" if msg["type"] == "cc" and value < 64 else "press"
        entry["result"] = "learned"
        self._bind(msg, trigger)

    def _fire(self, b, entry):
        result = self.app.midi_press(b, self.allow_power)
        with self.lock:
            entry["result"] = f"{b['section']} / {b['label']}: {result}"
            self.version += 1

    # -- mapping
    def learn(self, section, selector, label):
        with self.lock:
            self.learning = {"section": section, "selector": selector, "label": label}
            self.version += 1

    def cancel_learn(self):
        with self.lock:
            self.learning = None
            self.version += 1

    def _bind(self, msg, trigger="press"):
        l, self.learning = self.learning, None
        self.bindings = [b for b in self.bindings if b["msg"] != msg]
        self.bindings.append({"msg": msg, "section": l["section"], "selector": l["selector"], "label": l["label"], "trigger": trigger})
        self.save()
        self.app.note("info", f"MIDI: {midi_text(msg)} now runs {l['section']}/{l['label']}" + (" (every value counts)" if trigger == "any" else ""))

    def set_trigger(self, section, selector, trigger):
        with self.lock:
            for b in self.bindings:
                if b["section"] == section and b["selector"] == selector:
                    b["trigger"] = "any" if trigger == "any" else "press"
            self.save()

    def unbind(self, section, selector):
        with self.lock:
            self.bindings = [b for b in self.bindings if not (b["section"] == section and b["selector"] == selector)]
            self.save()

    def clear(self):
        with self.lock:
            self.bindings = []
            self.save()

    def map_everything(self, model):
        """One note per function, in menu order, channel 1, starting at note 36. Power functions are left out."""
        with self.lock:
            self.bindings, note = [], MIDI_FIRST_NOTE
            for s in model.sections:
                for i in s["items"]:
                    if i.get("view_only") or i.get("danger") == "confirm" or note > 127:
                        continue
                    self.bindings.append({"msg": {"type": "note", "channel": 1, "number": note}, "section": s["title"],
                                          "selector": model.selector(s, i), "label": i["label"]})
                    note += 1
            self.save()
            return len(self.bindings)

    def brief(self):
        with self.lock:
            return {"version": self.version, "last": self.last, "learning": self.learning, "count": len(self.bindings)}

    def info(self):
        with self.lock:
            return {**self.brief(), "status": self.status, "ports": self.ports, "virtual_port": "Trinnov Functions",
                    "allow_power": self.allow_power, "history": self.history[:12],
                    "bindings": [{**b, "text": midi_text(b["msg"])} for b in self.bindings]}


# ------------------------------------------------------------------ background service
SERVICE_LABEL = "com.trinnov.functions"
SERVICE_PORT = 47633
SERVICE_PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{SERVICE_LABEL}.plist")


def install_service():
    """A login item (LaunchAgent, no password) that keeps this app running: MIDI, the command and the
    launchers then work without a browser or Terminal window. Everything it needs is copied next to the data."""
    import plistlib
    cli = install_cli()
    server_py = os.path.join(os.path.dirname(cli), "server.py")
    plist = {"Label": SERVICE_LABEL, "ProgramArguments": ["/usr/bin/python3", server_py, "--service"],
             "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
             "StandardOutPath": os.path.expanduser("~/Library/Logs/TrinnovFunctionsService.log"),
             "StandardErrorPath": os.path.expanduser("~/Library/Logs/TrinnovFunctionsService.log")}
    os.makedirs(os.path.dirname(SERVICE_PLIST), exist_ok=True)
    os.makedirs(os.path.expanduser("~/Library/Logs"), exist_ok=True)
    with open(SERVICE_PLIST, "wb") as f:
        plistlib.dump(plist, f)
    if not os.environ.get("TRINNOV_NO_LAUNCHCTL"):
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", f"{domain}/{SERVICE_LABEL}"], capture_output=True)
        for _ in range(40):                              # launchd tears the old one down asynchronously: wait until it is gone
            if subprocess.run(["launchctl", "print", f"{domain}/{SERVICE_LABEL}"], capture_output=True).returncode != 0:
                break
            time.sleep(0.5)
        err = ""
        for _ in range(6):                               # and retry: starting too soon gives 'Input/output error'
            r = subprocess.run(["launchctl", "bootstrap", domain, SERVICE_PLIST], capture_output=True, text=True)
            if r.returncode == 0:
                break
            err = r.stderr.strip()
            time.sleep(1.5)
        else:
            raise OSError("launchctl could not start the service: " + (err or "unknown error"))
    return f"http://127.0.0.1:{SERVICE_PORT}/"


def uninstall_service():
    if not os.environ.get("TRINNOV_NO_LAUNCHCTL"):
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{SERVICE_LABEL}"], capture_output=True)
    try:
        os.remove(SERVICE_PLIST)
    except OSError:
        pass


def existing_instance():
    """Another copy of the app (or the service) already running for this user, or None."""
    try:
        info = json.load(open(BRIDGE_FILE))
        if info["pid"] != os.getpid():
            os.kill(info["pid"], 0)
            return info
    except (OSError, ValueError, KeyError):
        pass
    return None


# ------------------------------------------------------------------ the local app
class ReadOnlyError(OSError):
    pass


class GuardedLink:
    """The service's connection as seen by the command and by MIDI: honours Read-only, logs every write."""

    def __init__(self, app):
        self.app = app

    def read(self, path, timeout=3.0):
        return self.app.link.read(path, timeout)

    def read_many(self, paths, timeout=3.0):
        return self.app.link.read_many(paths, timeout)

    def write(self, path, obj):
        if self.app.read_only:
            self.app.note("blocked", f"Read-only mode: not sent: {path} <- {json.dumps(obj)}")
            raise ReadOnlyError("Read-only mode is on: nothing was sent.")
        self.app.link.write(path, obj)
        self.app.note("sent", f"{path} <- {json.dumps(obj)}")

    def close(self):
        pass


class App:
    def __init__(self, forced_ip, read_only):
        self.forced_ip = forced_ip
        self.read_only = read_only
        self.token = secrets.token_urlsafe(16)
        self.lock = threading.RLock()
        self.link = None
        self.model = None
        self.error = None
        self.log = []
        self.nova_ip = None
        self.last_try = 0.0
        self.epoch = 0
        self.midi = Midi(self)
        self.service = False
        self.stop = threading.Event()

    def note(self, kind, text):
        self.log.append({"t": time.strftime("%H:%M:%S"), "kind": kind, "text": text})
        del self.log[:-60]

    def connect(self):
        """(Re)connect to the Nova and rebuild the menu from what it reports."""
        with self.lock:
            self.last_try = time.time()
            if self.link:
                self.link.close()
            self.link = self.model = None
            self.error = None
            ip = find_nova(self.forced_ip)
            if not ip:
                self.error = "No Trinnov Nova found on this network."
                self.note("error", self.error)
                return
            try:
                link = NovaLink(ip)
                link.connect()
                tree, how = load_tree(ip)
                model = Model(link, tree, how)
                model.build()
                self.link, self.model, self.nova_ip = link, model, ip
                self.epoch += 1
                self.note("info", f"Connected to the Nova at {ip}. Menu {how}.")
            except Exception as e:                                       # noqa: BLE001
                self.error = f"Could not talk to the Nova at {ip}: {e}"
                self.note("error", self.error)

    def ready(self):
        with self.lock:
            if self.link and not self.link.alive:
                self.note("info", "Connection lost, reconnecting...")
                self.connect()
            elif self.model is None and time.time() - self.last_try > 4:
                self.connect()                      # keep looking: the Nova may simply not be on yet
            return self.model is not None

    def menu(self):
        with self.lock:
            if not self.ready():
                return {"connected": False, "error": self.error, "read_only": self.read_only}
            m = self.model
            return {"connected": True, "epoch": self.epoch, "service": self.service, "read_only": self.read_only, "nova_ip": self.nova_ip, "info": m.info,
                    "menu_source": m.tree_source,
                    "sections": [{"key": s["key"], "title": s["title"], "note": s["note"],
                                  "items": [{**{k: i.get(k) for k in ("id", "label", "sub", "type", "danger", "view_only", "momentary")},
                                             "midi": self.midi_badge(m, s, i)}
                                            for i in s["items"]]} for s in m.sections]}

    def state(self):
        with self.lock:
            if not self.ready():
                return {"connected": False, "error": self.error, "log": self.log, "read_only": self.read_only}
            st = self.model.poll()
            st.update({"connected": True, "epoch": self.epoch, "log": self.log, "read_only": self.read_only, "midi": self.midi.brief()})
            return st

    def write(self, path, payload, what):
        if self.read_only:
            self.note("blocked", f"Read-only mode: not sent: {path} <- {json.dumps(payload)}")
            return {"ok": False, "error": "Read-only mode is on: nothing was sent."}
        self.link.write(path, payload)
        self.note("sent", f"{what}: {path} <- {json.dumps(payload)}")
        return {"ok": True, "sent": {"path": path, "payload": payload}}

    def press(self, iid, confirmed):
        with self.lock:
            if not self.ready():
                return {"ok": False, "error": self.error or "Not connected"}
            item = self.model.items.get(iid)
            if item and item.get("danger") == "confirm" and not confirmed:
                return {"ok": False, "needs_confirm": True, "message": f"{item['label']} will act on the Nova right away."}
            try:
                path, payload, what = self.model.press(iid)
            except ValueError as e:
                self.note("error", str(e))
                return {"ok": False, "error": str(e)}
            return self.write(path, payload, what)

    def launcher(self, iid, make_all, reveal):
        with self.lock:
            if not self.ready():
                return {"ok": False, "error": self.error or "Not connected"}
            jobs = []
            for s in self.model.sections:
                for i in s["items"]:
                    if i.get("view_only") or (iid and i["id"] != iid):
                        continue
                    jobs.append((s["title"], i["label"], self.model.selector(s, i), i.get("danger") == "confirm"))
        if not jobs:
            return {"ok": False, "error": "No launcher for this function (view-only entries have none)."}
        try:
            cli = install_cli()
            paths = [make_launcher(cli, *job, LAUNCHER_DIR) for job in jobs]
        except OSError as e:
            return {"ok": False, "error": str(e)}
        self.note("info", f"Made {len(paths)} Stream Deck launcher(s) in {LAUNCHER_DIR}")
        if reveal and not os.environ.get("TRINNOV_NO_REVEAL"):
            subprocess.run(["open", LAUNCHER_DIR] if make_all else ["open", "-R", paths[0]])
        return {"ok": True, "dir": LAUNCHER_DIR, "count": len(paths), "paths": paths}

    def run_cli(self, argv):
        """Run a `trinnov` command inside the service, on its live connection."""
        import trinnov
        with self.lock:
            if not self.ready():
                return {"code": 1, "output": "Trinnov: " + (self.error or "Not connected"), "not_connected": True}
            buf = io.StringIO()
            try:
                with contextlib.redirect_stderr(buf):
                    args = trinnov.parse_args(argv)
            except SystemExit:
                return {"code": 2, "output": "Trinnov: wrong arguments"}
            self.note("info", "command: trinnov " + " ".join(argv))
            try:
                return {"code": 0, "output": trinnov.run(args, ctx=(GuardedLink(self), self.model))}
            except trinnov.Fail as e:
                return {"code": e.code, "output": f"Trinnov: {e}"}
            except ReadOnlyError as e:
                return {"code": 1, "output": f"Trinnov: {e}"}
            except Exception as e:                                       # noqa: BLE001
                return {"code": 1, "output": f"Trinnov: unexpected problem ({type(e).__name__}: {e})"}

    def midi_api(self, path, body):
        m = self.midi
        with self.lock:
            if path == "/api/midi/learn":
                if body.get("cancel"):
                    m.cancel_learn()
                    return {"ok": True}
                if not self.ready():
                    return {"ok": False, "error": "Not connected"}
                sec, item = next(((s, i) for s in self.model.sections for i in s["items"] if i["id"] == body.get("id")), (None, None))
                if not item or item.get("view_only"):
                    return {"ok": False, "error": "This entry cannot be mapped."}
                if item.get("danger") == "confirm" and not m.allow_power:
                    return {"ok": False, "error": "Power functions cannot be mapped to MIDI: a stray note would switch your Nova off."}
                m.learn(sec["title"], self.model.selector(sec, item), item["label"])
                return {"ok": True}
            if path == "/api/midi/unlearn":
                sec, item = next(((s, i) for s in self.model.sections for i in s["items"] if i["id"] == body.get("id")), (None, None))
                if item:
                    m.unbind(sec["title"], self.model.selector(sec, item))
                return {"ok": True}
            if path == "/api/midi/default":
                if not self.ready():
                    return {"ok": False, "error": "Not connected"}
                return {"ok": True, "count": m.map_everything(self.model)}
            if path == "/api/midi/clear":
                m.clear()
                return {"ok": True}
            if path == "/api/midi/trigger":
                m.set_trigger(str(body.get("section")), str(body.get("selector")), str(body.get("trigger")))
                return {"ok": True}
        return {"ok": False, "error": "unknown MIDI request"}

    def midi_badge(self, model, sec, item):
        sel = model.selector(sec, item)
        texts = [("N" if b["msg"]["type"] == "note" else "CC" if b["msg"]["type"] == "cc" else "PC") + str(b["msg"]["number"])
                 for b in self.midi.bindings if b["section"] == sec["title"] and b["selector"] == sel]
        return " ".join(texts) or None

    def midi_press(self, binding, allow_power):
        """A MIDI message was bound to a function: press it, the same way a tap on the tile would."""
        with self.lock:
            if not self.ready():
                return "not connected to the Nova"
            sec, item = self.model.lookup(binding["section"], binding["selector"])
            if not item:
                self.note("error", f"MIDI: no function '{binding['section']}/{binding['selector']}' on this Nova")
                return "function not found"
            if item.get("danger") == "confirm" and not allow_power:
                self.note("blocked", f"MIDI: {sec['title']}/{item['label']} is a Power function: not allowed over MIDI")
                return "Power is not allowed over MIDI"
            r = self.press(item["id"], True)
            return "sent" if r.get("ok") else (r.get("error") or "failed")

    def volume(self, delta):
        with self.lock:
            if not self.ready():
                return {"ok": False, "error": "Not connected"}
            return self.write("/optimizer/volume", {"dvolume": float(delta)}, f"Volume {delta:+g} dB")

    def mute(self):
        with self.lock:
            if not self.ready():
                return {"ok": False, "error": "Not connected"}
            cur = self.link.read("/optimizer/volume/mute")
            return self.write("/optimizer/volume", {"mute": not bool(cur)}, "Mute")


class Handler(BaseHTTPRequestHandler):
    app = None
    server_version = "TrinnovFunctions"

    def log_message(self, *a):
        pass

    def _allowed(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin")
        if host not in ("127.0.0.1", "localhost"):
            return False
        if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
            return False
        return True

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        return self._allowed() and secrets.compare_digest(self.headers.get("X-Token", ""), self.app.token)

    def do_GET(self):
        path = urlparse(self.path).path
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        if path == "/":
            html = open(os.path.join(HERE, "ui", "index.html"), "rb").read().replace(b"__TOKEN__", self.app.token.encode())
            return self._send(200, html, "text/html; charset=utf-8")
        if not self._authed():
            return self._send(403, {"error": "forbidden"})
        if path == "/api/menu":
            return self._send(200, self.app.menu())
        if path == "/api/midi":
            return self._send(200, self.app.midi.info())
        if path == "/api/state":
            return self._send(200, self.app.state())
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authed():
            return self._send(403, {"error": "forbidden"})
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        a = self.app
        if path == "/api/press":
            return self._send(200, a.press(str(body.get("id")), bool(body.get("confirm"))))
        if path.startswith("/api/midi"):
            return self._send(200, a.midi_api(path, body))
        if path == "/api/cli":
            return self._send(200, a.run_cli([str(x) for x in body.get("argv", [])]))
        if path == "/api/launcher" and body.get("open_folder"):
            os.makedirs(LAUNCHER_DIR, exist_ok=True)
            if not os.environ.get("TRINNOV_NO_REVEAL"):
                subprocess.run(["open", LAUNCHER_DIR])
            return self._send(200, {"ok": True, "dir": LAUNCHER_DIR})
        if path == "/api/launcher":
            return self._send(200, a.launcher(str(body.get("id") or ""), bool(body.get("all")), body.get("reveal", True)))
        if path == "/api/volume":
            return self._send(200, a.volume(float(body.get("delta", 0))))
        if path == "/api/mute":
            return self._send(200, a.mute())
        if path == "/api/readonly":
            a.read_only = bool(body.get("on"))
            a.note("info", "Read-only mode " + ("ON: nothing will be sent." if a.read_only else "OFF: buttons act on the Nova."))
            return self._send(200, {"ok": True, "read_only": a.read_only})
        if path == "/api/reload":
            a.connect()
            return self._send(200, a.menu())
        if path == "/api/quit":
            self._send(200, {"ok": True})
            a.stop.set()
            return
        self._send(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ip", help="Nova address (default: find it automatically)")
    ap.add_argument("--read-only", action="store_true", help="never send anything to the Nova")
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    ap.add_argument("--port", type=int, default=0, help="local port for the app (default: any free port)")
    ap.add_argument("--service", action="store_true", help="run as the background service (fixed port, no browser)")
    ap.add_argument("--install-service", action="store_true", help="install the background service for this user (no password)")
    ap.add_argument("--uninstall-service", action="store_true", help="remove the background service")
    args = ap.parse_args()

    if args.install_service:
        try:
            print(f"Background service installed. Open {install_service()} any time; it starts at every login.")
        except OSError as e:
            print(f"Could not install the service: {e}")
            sys.exit(1)
        return
    if args.uninstall_service:
        uninstall_service()
        print("Background service removed.")
        return
    other = existing_instance()
    if other:                                            # never start a second copy (it would double every MIDI action)
        url = f"http://127.0.0.1:{other['port']}/"
        print(f"Trinnov Functions is already running: {url}", flush=True)
        if not args.no_open and not args.service:
            webbrowser.open(url)
        return
    if args.service:
        args.port, args.no_open = SERVICE_PORT, True

    app = App(args.ip or os.environ.get("TRINNOV_IP"), args.read_only)
    app.service = args.service
    Handler.app = app
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    print(f"Trinnov Functions running at {url}  (Ctrl-C or the Quit button to stop)", flush=True)
    threading.Thread(target=app.connect, daemon=True).start()
    register_instance(port, app.token)
    app.midi.start()

    def keep_alive():                                    # reconnect on its own when the Nova is switched off and on
        while not app.stop.wait(15 if app.model else 30):
            app.ready()
    threading.Thread(target=keep_alive, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: app.stop.set())
    if not args.no_open:
        webbrowser.open(url)
    try:
        while not app.stop.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    app.midi.stop()
    srv.shutdown()
    unregister_instance()


BRIDGE_FILE = os.path.join(SUPPORT, "bridge.json")


def register_instance(port, token):
    """Tell the `trinnov` command (and launchers) that this instance is running, so they use its connection."""
    try:
        os.makedirs(SUPPORT, exist_ok=True)
        fd = os.open(BRIDGE_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"pid": os.getpid(), "port": port, "token": token}, f)
    except OSError:
        pass


def unregister_instance():
    try:
        if json.load(open(BRIDGE_FILE)).get("pid") == os.getpid():
            os.remove(BRIDGE_FILE)
    except (OSError, ValueError):
        pass


if __name__ == "__main__":
    import server as _module            # run as the importable module so `trinnov` and the app share one copy
    _module.main()
