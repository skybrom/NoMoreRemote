#!/usr/bin/env python3
"""trinnov: run any La Remote function on your Trinnov Nova, by name, from a script or a key.

    trinnov list                          every function your Nova offers
    trinnov status [WORDS]                current state (lit / not lit)
    trinnov press   [SECTION] NAME        what a tap on La Remote does (flip / select)
    trinnov on|off|toggle [SECTION] NAME  explicit, safe to repeat (does nothing if already so)
    trinnov preset NAME | #ID | toggle    shortcuts: preset, source, speakerset, downmix, snapshot
    trinnov volume up 2 | down 2 | set -30   nudge, or go to a level (max 10 dB per call, --force to go further)
                                          (a bare -30 is a LEVEL; +2 nudges up)
    trinnov mute [on|off|toggle]

NAME can be any part of the label, in any case, or #N for a numbered item (see `trinnov list`).
Options: --ip ADDRESS, --json, --dry-run (show what would be sent), --yes (needed for Power), --no-service,
         --refresh (re-read the function list from the Nova).

Exit code: 0 done, 1 failed, 2 wrong usage, 3 name not found or ambiguous.
The Nova is found on your network automatically; the menu is read from that Nova.
"""
import argparse, contextlib, io, json, os, re, sys, time, unicodedata, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402  (same folder: the engine behind the app)

SUPPORT = os.environ.get("TRINNOV_SUPPORT_DIR") or server.SUPPORT
TREE_CACHE = os.path.join(SUPPORT, "tree_cache.json")
VOLUME_STEP_LIMIT = 10.0

SECTION_WORDS = {"preset": "presets", "presets": "presets", "source": "sources", "sources": "sources",
                 "speakerset": "speaker sets", "speakersets": "speaker sets", "speaker": "speaker sets",
                 "downmix": "downmix", "downmixes": "downmix", "snapshot": "snapshots", "snapshots": "snapshots",
                 "tools": "speaker tools", "speakertools": "speaker tools", "processing": "processing",
                 "runtime": "processing", "monitoring": "monitoring", "common": "common", "power": "power",
                 "network": "network", "sendsources": "send sources"}


LAUNCH_LOG = os.path.expanduser("~/Library/Logs/TrinnovLaunchers.log")


def log(message):
    """Every run leaves a line, so a key press that 'does nothing' can be diagnosed."""
    try:
        os.makedirs(os.path.dirname(LAUNCH_LOG), exist_ok=True)
        with open(LAUNCH_LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
    except OSError:
        pass


class Fail(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9#]+", "", s)


# ------------------------------------------------------------------ connecting
def cached_tree(ip, refresh):
    """The function list is read from the Nova (about 3 MB); keep it for a day so a key press is quick."""
    if not refresh:
        try:
            if time.time() - os.path.getmtime(TREE_CACHE) < 86400:
                c = json.load(open(TREE_CACHE))
                return c["tree"], c["source"]
        except (OSError, ValueError, KeyError):
            pass
    tree, how = server.load_tree(ip)
    try:
        os.makedirs(SUPPORT, exist_ok=True)
        json.dump({"tree": tree, "source": how}, open(TREE_CACHE, "w"))
    except OSError:
        pass
    return tree, how


def connect(args):
    ip = server.find_nova(args.ip or os.environ.get("TRINNOV_IP"))
    if not ip:
        raise Fail("No Trinnov Nova found on the network")
    try:
        link = server.NovaLink(ip)
        link.connect()
        tree, how = cached_tree(ip, args.refresh)
        model = server.Model(link, tree, how)
        model.build()
    except OSError as e:
        raise Fail(f"Cannot reach the Nova at {ip} ({e})")
    return link, model


# ------------------------------------------------------------------ finding a function by name
def section_of(model, word):
    w = norm(word)
    wanted = SECTION_WORDS.get(w)
    for s in model.sections:
        if norm(s["title"]) == w or norm(s["key"]) == w or (wanted and norm(s["title"]) == norm(wanted)):
            return s
    return None


def describe(model, s, it):
    return f"{s['title']}/{it['label']}" + (f" ({it['sub']})" if it.get("sub", "") and it["sub"].startswith("#") else "")


def find(model, words):
    """words: [SECTION] NAME. Returns (section, item)."""
    if not words:
        raise Fail("Name the function, e.g. `trinnov press Presets \"Preset A\"`", 2)
    sec = section_of(model, words[0]) if len(words) > 1 else None
    query = " ".join(words[1:] if sec else words)
    pool = [(s, i) for s in ([sec] if sec else model.sections) for i in s["items"]]
    q = norm(query)
    tiers = [lambda i: norm(i["label"]) == q or (str(i.get("sub") or "").startswith("#") and norm(i["sub"]) == q),
             lambda i: norm(i["label"]).startswith(q),
             lambda i: q in norm(i["label"])]
    for test in tiers:
        hits = [(s, i) for s, i in pool if test(i)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise Fail("More than one match, be more specific (or use #N): " +
                       "; ".join(describe(model, s, i) for s, i in hits), 3)
    raise Fail(f"No function called '{query}'" + (f" in {sec['title']}" if sec else "") +
               ". Run `trinnov list` to see what your Nova offers.", 3)


# ------------------------------------------------------------------ doing things
def lit(model, iid):
    return model.poll()["items"][iid]


def act(args, link, model, sec, it, verb):
    iid = it["id"]
    if it.get("view_only"):
        raise Fail(f"{it['label']} is view only", 1)
    if sec["key"] == "power" and not (args.yes or args.dry_run):
        raise Fail(f"{it['label']} switches the Nova off or restarts it: add --yes to confirm", 1)
    st = lit(model, iid)
    if st.get("unavailable"):
        raise Fail(f"This Nova does not have '{it['label']}'", 1)
    is_toggle = it["type"] == "boolean" and it["value"] is True and not it.get("momentary")
    sum_mode = sec["key"] == "sources" and server.sym(link.read("/monitoring/control/source_select_mode")) == "sum"
    if verb in ("on", "off"):
        if it.get("momentary") or it["type"] == "toggle-preset":
            raise Fail(f"{it['label']} is a one-shot action: use `press`", 2)
        if not is_toggle and not sum_mode and verb == "off":
            raise Fail(f"{it['label']} is a choice: switch another one on instead of this one off", 2)
        if st["on"] is (verb == "on"):
            return f"{describe(model, sec, it)}: already {verb}"
    try:
        path, payload, what = model.press(iid)
    except ValueError as e:
        raise Fail(str(e), 1)
    if args.dry_run:
        return f"[dry run] would send {path} <- {json.dumps(payload)}"
    link.write(path, payload)
    time.sleep(0.5)
    if it.get("momentary") or it["type"] == "toggle-preset":
        return f"{describe(model, sec, it)}: done"
    after = lit(model, iid)["on"]
    return f"{describe(model, sec, it)}: {'on' if after else 'off'}"


def cmd_list(args, link, model):
    if args.json:
        return json.dumps({"nova": model.info, "menu": model.tree_source, "sections": [
            {"section": s["title"], "note": s["note"], "items": [
                {"label": i["label"], "ref": i.get("sub"), "type": i["type"],
                 "view_only": bool(i.get("view_only")), "action": bool(i.get("momentary"))} for i in s["items"]]}
            for s in model.sections]}, indent=1)
    lines = [f"{model.info['product'].upper()} #{model.info['serial']}  ({model.tree_source})"]
    for s in model.sections:
        lines.append(f"\n{s['title']}" + (f"   - {s['note']}" if s["note"] else ""))
        for i in s["items"]:
            flags = " [view only]" if i.get("view_only") else " [action]" if i.get("momentary") else ""
            lines.append(f"   {i['label']}" + (f"  {i['sub']}" if i.get("sub") else "") + flags)
    return "\n".join(lines)


def cmd_status(args, link, model, words):
    st = model.poll()
    rows = []
    for s in model.sections:
        for i in s["items"]:
            if words and not all(norm(w) in norm(s["title"] + i["label"]) for w in words):
                continue
            x = st["items"][i["id"]]
            rows.append({"section": s["title"], "label": i["label"], "ref": i.get("sub"),
                         "on": x.get("on"), "info": x.get("info"), "unavailable": bool(x.get("unavailable"))})
    extra = {"volume_db": st.get("volume"), "mute": st.get("mute"), "source_mode": st.get("mode")}
    if args.json:
        return json.dumps({"state": extra, "items": rows}, indent=1)
    lines = [f"volume {extra['volume_db']} dB, mute {'on' if extra['mute'] else 'off'}, source mode {extra['source_mode']}"]
    lines += [f"{r['section']:<14} {r['label']:<24} {'ON' if r['on'] else '-' if r['on'] is False else '?'}"
              + (f"  {r['info']}" if r["info"] else "") for r in rows]
    return "\n".join(lines)


def parse_volume(words):
    """('up'|'down', n) / ('set', level) / +n / bare level. Returns ('delta', n) or ('level', n)."""
    w = [x.lower() for x in words]
    num = r"\d+(\.\d+)?"
    if len(w) == 2 and w[0] in ("up", "down") and re.fullmatch(num, w[1]):
        return "delta", float(w[1]) * (1 if w[0] == "up" else -1)
    if len(w) == 2 and w[0] == "set" and re.fullmatch(r"-?" + num, w[1]):
        return "level", float(w[1])
    if len(w) == 1 and re.fullmatch(r"\+" + num, w[0]):
        return "delta", float(w[0])
    if len(w) == 1 and re.fullmatch(r"-?" + num, w[0]):
        return "level", float(w[0])
    raise Fail("Usage: trinnov volume up 2 | down 2 | set -30", 2)


def cmd_volume(args, link, model, words):
    kind, number = parse_volume(words)
    cur = link.read("/optimizer/volume/volume")
    if not isinstance(cur, (int, float)):
        raise Fail("The Nova did not report its volume")
    delta = number if kind == "delta" else number - cur
    if abs(delta) > VOLUME_STEP_LIMIT and not args.force:
        raise Fail(f"That is a change of {delta:+.1f} dB (now {cur:.1f} dB). Limit is {VOLUME_STEP_LIMIT:g} dB per call: add --force", 1)
    if args.dry_run:
        return f"[dry run] would send /optimizer/volume <- {{\"dvolume\": {delta:g}}} (now {cur:.1f} dB)"
    link.write("/optimizer/volume", {"dvolume": delta})
    time.sleep(0.5)
    return f"Volume: {link.read('/optimizer/volume/volume')} dB"


def cmd_mute(args, link, model, mode):
    cur = bool(link.read("/optimizer/volume/mute"))
    want = (not cur) if mode == "toggle" else (mode == "on")
    if want is cur:
        return f"Mute: already {'on' if cur else 'off'}"
    if args.dry_run:
        return f"[dry run] would send /optimizer/volume <- {{\"mute\": {json.dumps(want)}}}"
    link.write("/optimizer/volume", {"mute": want})
    time.sleep(0.4)
    return f"Mute: {'on' if link.read('/optimizer/volume/mute') else 'off'}"


def run(args, ctx=None):
    """Run one command. Inside the background service, ctx=(link, model) is its own live connection."""
    words, cmd = args.words, args.command
    own = ctx is None
    link, model = ctx if ctx else connect(args)
    try:
        if cmd == "list":
            return cmd_list(args, link, model)
        if cmd == "status":
            return cmd_status(args, link, model, words)
        if cmd == "volume":
            return cmd_volume(args, link, model, words)
        if cmd == "mute":
            mode = (words[0] if words else "toggle")
            if mode not in ("on", "off", "toggle"):
                raise Fail("Usage: trinnov mute [on|off|toggle]", 2)
            return cmd_mute(args, link, model, mode)
        if cmd in ("preset", "source", "speakerset", "downmix", "snapshot"):
            sec = section_of(model, cmd)
            if not sec:
                raise Fail(f"This Nova has no {cmd} functions", 3)
            if cmd == "preset" and words and norm(words[0]) == "toggle":
                it = next((i for i in sec["items"] if i["type"] == "toggle-preset"), None)
                if not it:
                    raise Fail("No previous preset to go back to", 3)
                return act(args, link, model, sec, it, "press")
            s, it = find(model, [sec["title"]] + words)
            return act(args, link, model, s, it, "press")
        if cmd in ("press", "on", "off", "toggle"):
            s, it = find(model, words)
            return act(args, link, model, s, it, "press" if cmd == "toggle" else cmd)
        raise Fail("Unknown command", 2)
    finally:
        if own:
            link.close()


COMMANDS = ["list", "status", "press", "on", "off", "toggle", "preset", "source", "speakerset", "downmix",
            "snapshot", "volume", "mute"]
BRIDGE_FILE = os.path.join(SUPPORT, "bridge.json")


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="trinnov", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=COMMANDS)
    ap.add_argument("words", nargs="*")
    ap.add_argument("--ip")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--no-service", action="store_true", help="talk to the Nova directly even if the background service runs")
    return ap.parse_args(argv)


def via_service(argv):
    """If the background service (or the open app) is running, let it do the work over loopback: it already
    holds the connection, so this works the same however this command was launched. Returns (code, output) or None."""
    try:
        info = json.load(open(BRIDGE_FILE))
        os.kill(info["pid"], 0)
        req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api/cli", data=json.dumps({"argv": argv}).encode(),
                                     headers={"X-Token": info["token"], "Content-Type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=10))
        if r.get("not_connected"):
            return None
        return r["code"], r["output"]
    except (OSError, ValueError, KeyError):
        return None


def main():
    argv = sys.argv[1:]
    args = parse_args(argv)
    what = f"trinnov {args.command} {' '.join(args.words)}".strip()
    log(f"{what}: started")
    if not args.no_service:
        done = via_service([a for a in argv if a != "--no-service"])
        if done:
            code, out = done
            log(f"{what}: via service: {out.splitlines()[0] if out else 'done'}" + ("" if code == 0 else f" (exit {code})"))
            print(out)
            sys.exit(code)
        log(f"{what}: service not available, talking to the Nova directly")
    try:
        result = run(args)
        log(f"{what}: {result.splitlines()[0] if result else 'done'}")
        print(result)
    except Fail as e:
        log(f"{what}: FAILED: {e}")
        print(f"Trinnov: {e}", file=sys.stderr if args.json else sys.stdout)
        sys.exit(e.code)
    except OSError as e:
        log(f"{what}: FAILED (connection): {e}")
        print(f"Trinnov: connection problem ({e})")
        sys.exit(1)
    except Exception as e:                                           # noqa: BLE001
        log(f"{what}: CRASHED: {type(e).__name__}: {e}")
        print(f"Trinnov: unexpected problem ({type(e).__name__}: {e})")
        sys.exit(1)


if __name__ == "__main__":
    main()
