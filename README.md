# Trinnov Functions

Every function of **La Remote**, from your Mac, **without La Remote**: Common, Presets, Processing, Downmix, Monitoring, Sources, Speaker Sets, Speaker Tools, Power, in the same layout and colours as the Nova's own interface.

> **Unofficial.** Not affiliated with or endorsed by Trinnov Audio. It sends the same messages the Nova's own web interface sends. **Most buttons are untested on real hardware** (see [What is tested](#what-is-tested)). Use at your own risk, and start in **Read-only** mode if you like.

## Run it

1. Download this folder.
2. Double-click **`Trinnov Functions.command`** (if macOS blocks it: right-click → **Open** → **Open**).
   - Your browser opens the app. Close the Terminal window, or click **Quit** in the app, to stop it.
3. Optional: double-click **`Make App.command`** once to get **`Trinnov Functions.app`**, a double-click app without a Terminal window. Keep it in this folder.

Needs macOS with Python 3 (macOS offers to install Apple's developer tools the first time if it is missing). No installation, no password, nothing is written outside this folder except a remembered Nova address in `~/Library/Application Support/TrinnovFunctions`.

**Recommended: install the background service.** Double-click **`Install Background Service.command`** once (no password). It keeps Trinnov Functions running from every login, so MIDI, the `trinnov` command and the Stream Deck launchers work without opening anything. The app is then always at **http://127.0.0.1:47633/**. `Uninstall Background Service.command` removes it. If the service is running, `Trinnov Functions.command` just opens it instead of starting a second copy.

Options (add after the command in Terminal): `--read-only` (never send anything), `--ip 192.168.1.50` (skip the search), `--no-open`.

## It reads **your** Nova

Nothing about a particular Nova or Mac is built in. On start it:

1. **Finds your Nova** on your network.
2. **Reads its feature flags** (`/config`), so the menu only shows what your model and licence support (no listening formats on a Nova without a codec, no DRC without the DRC feature, and so on).
3. **Reads your own lists**: presets (empty and factory slots are skipped), sources (hidden and locked ones are skipped), speaker sets, downmixes, speaker channels, snapshots. **One tile per item.**
4. **Reads the function list from the Nova's own web interface**, so a different firmware gives its own menu; a built-in copy (from firmware 5.3.0pre3) is used if that fails.
5. Keeps the tiles **live**: a lit tile is the Nova's current state, refreshed every 1.5 seconds. Change something on the Nova or from another device and it follows.

Click **Reload from Nova** after you add a preset or rename a source. If the Nova goes off or is not on yet, the app keeps looking and reconnects by itself.

A function the Nova does not have on this firmware is greyed out instead of guessed.

## How the buttons behave

| Kind | Behaviour |
|---|---|
| **Toggle** (Bypass, Dim, Acoustic, …) | press flips it; lit = on |
| **Select** (a preset, a source, a speaker set, a downmix, Solo/Mute mode) | press chooses it; the chosen one is lit |
| **Sources** | press sends the source number, as La Remote and the Nova's own page do. In *Sum* mode (the **Sum** tile in Monitoring) the Nova adds or removes it, so several can be on at once; in *Toggle* mode it selects just that one |
| **Toggle preset** | goes back to the previous preset (press again to come back); needs one preset change first |
| **Actions** (Ref. Level, Next Spk, Power Off, Reboot) | one-shot, no state |
| **View only** (Network) | shown, never written: changing network settings from here is disabled on purpose |

**Power Off and Reboot ask for confirmation.** The **Live / Read-only** switch in the header decides whether anything is sent; in Read-only mode a press only appears in the log on the right as *not sent*. The right-hand panel lists every message actually sent to the Nova, so you can see exactly what each button does.

The footer has the volume (− / + in the step your remote uses) and Mute.

## Stream Deck: one launcher per function

Hover a tile and click **+ Deck**, or use **Stream Deck…** in the header → **Make all launchers**. Each launcher is a small app (for example `Presets - My Preset.app`, `Sources - AUX.app`, `Common - Bypass.app`) in `~/Documents/Trinnov Stream Deck`. **Drag it from Finder onto a Stream Deck key**; the Stream Deck app makes an *Open* action from it. Press the key and a macOS notification shows the result.

- **Power Off and Reboot** launchers always show a confirmation dialog first. Network entries have no launcher.
- Launchers look functions up **by name on your Nova**, so they keep working when presets or sources are added. If you rename a preset, make its launcher again.
- They run a copy of the command line kept in `~/Library/Application Support/TrinnovFunctions/cli`, so they keep working if you move this folder.
- **How they run:** if the background service (or the open app) is running, a launcher hands the work to it over your Mac's internal loopback, so it behaves the same however Stream Deck started it. Without it, a launcher talks to the Nova directly.
- **Feedback:** a soft *Tink* sound on success and a *Basso* on failure, plus a macOS notification (macOS may hide those for apps started by Stream Deck, so the sound is the reliable sign). Switch the sounds off by creating an empty file `~/Library/Application Support/TrinnovFunctions/sounds_off`.
- **If a key does nothing**, read `~/Library/Logs/TrinnovLaunchers.log`: it has one line when the launcher opens, one when the command starts, and one with the result or the error.
- Launchers act on the live Nova, with no Read-only switch. Click **Make all launchers** again after you change your setup.

### Why not drag a tile from the page?

The Stream Deck app does not accept a link dragged from a web page, so the dependable way is the one above: drag the launcher app from Finder. (`trinnov://` is also the Trinnov app's own link scheme, so this project deliberately registers no links of its own.)

## MIDI: control the Nova from any controller or from a Stream Deck

Every function can be mapped to a MIDI message, so a MIDI controller, a Stream Deck MIDI plugin, or any app that sends MIDI can run it.

- **Where to send:** to the virtual port **Trinnov Functions** (it appears in any MIDI app by itself), or from a controller already connected to this Mac (the app listens to every MIDI input, including ones plugged in later and IAC Driver buses).
- **What counts as a button press:** a **note on**, a **program change**, or a **CC of 64 or more**. A note off, or a CC below 64, is the release and is ignored. A CC that sends `127` on every press and nothing on release works too.
- **Map one function (learn):** hover its tile, click **MIDI**, then press the key on your controller. The tile then shows a badge like `N40`. Messages from a controller's *DAW* port are never learned (those ports chatter all the time), and a note off is ignored (it waits for the press).
- **Toggle buttons:** a button that alternates CC 0 and CC 127 (for example the Stream Deck plugin's *MIDI CC Toggle*) cannot be told apart by its value. If a CC is learned from a low value it is set to *every value counts* automatically, and you can change that per mapping in the MIDI window.
- **See what arrives:** the MIDI window lists every message heard, live, with its raw bytes and what became of it (*not mapped*, *release, ignored*, *learned*, or the function it ran). If a key does nothing, start there.
- **Map everything at once:** **MIDI…** in the header → **Map every function automatically** gives each function one note, in menu order, on channel 1, starting at note 36. The window lists the table, so you can set up a Stream Deck from it.
- **Volume and Mute** are functions too (the *Volume* section), so a button can nudge the level or mute.
- **Safety:** Power Off and Reboot **cannot be mapped** (a stray note would switch the Nova off). The Read-only switch blocks MIDI as well. Mappings are saved in `~/Library/Application Support/TrinnovFunctions/midi_map.json`.
- **Needs the Swift compiler** (part of Apple's developer tools, already needed for Python on most Macs): the first start builds a small helper (`midi/trinnov-midi.swift`) for a few seconds.
- MIDI works while the app or the background service is running, so the **service** is the way to use it day to day.

**A Stream Deck with MIDI:** the Stream Deck app has no MIDI output of its own, so use a MIDI plugin from Elgato's marketplace (for example *Midi Button*). Such plugins usually create their own MIDI port (the app hears it by itself, whatever its name) or send to a port you choose: pick **Trinnov Functions** to send straight here. Use the plugin's plain **MIDI Note** or **MIDI CC** action for a normal button; its *Toggle* actions alternate values and need the *every value* option above.

## The `trinnov` command

Everything the app does is also a command, for scripts, Shortcuts, Keyboard Maestro, Home Assistant and so on:

```bash
./trinnov list                          # every function your Nova offers (add --json)
./trinnov status                        # what is lit; status Bypass filters
./trinnov on Bypass                     # on | off | toggle: safe to repeat, does nothing if already so
./trinnov press Presets "Preset A"      # what a tap does (flip / choose); section is optional
./trinnov preset "Preset A"             # shortcuts: preset, source, speakerset, downmix, snapshot
./trinnov preset "#3"                   # by number, when names repeat
./trinnov preset toggle                 # back to the previous preset
./trinnov source Computer on            # (in Sum mode) switch one source on/off; off needs Sum mode
./trinnov volume up 2                   # up | down | set -30 (max 10 dB per call, --force to go further)
./trinnov mute toggle
./trinnov press "Power Off" --yes       # Power needs --yes
./trinnov press Bypass --dry-run        # show what would be sent, send nothing
```

Names match any part of the label, in any case. If more than one thing matches, it refuses and lists them. Exit code: `0` done, `1` failed, `2` wrong usage, `3` name not found or ambiguous. Options: `--ip`, `--json`, `--refresh`.

## What is tested

Tested on one Nova: **firmware 5.3.0pre3, macOS 26.** The menu building and the live state were checked against that real Nova **in read-only mode**. **Writes were tested only against a fake Nova** (`tests/fake_nova.py`), which confirms the app sends what it should, not that a real Nova reacts as expected. Other Trinnov products (Altitude, D-MON, …) use a different interface and are **untested**.

Run the tests (fake Nova and fake MIDI controllers only, nothing can reach a real Nova): `python3 tests/test_cli.py` (command, launchers, service) and `python3 tests/test_midi.py` (MIDI, needs the Swift compiler).

Try the fake Nova yourself, without hardware:

```bash
python3 tests/fake_nova.py 8083 &
TRINNOV_IP=127.0.0.1 TRINNOV_PORT=8083 python3 server.py
```

## Security

The app listens only on `127.0.0.1` with a random token that changes at every start, and refuses requests from other sites, so a web page you visit cannot make it act on your Nova. It sends only the actions listed in the menu it built (the page sends an item number, never a path). The `trinnov` command and the launchers have no token: like any script on your Mac, anything running as you can use them.

## License

MIT, see [LICENSE](LICENSE).
