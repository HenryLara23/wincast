# Wincast

A live win-chance overlay for League of Legends, Summoner's Rift.

* **Local only.** Reads the Live Client Data API that the game itself serves on
  your PC (`https://127.0.0.1:2999`). No Riot API key, no account, no server.
* **Nothing touches the game.** The overlay is an ordinary separate window; it
  never hooks, injects into or reads the memory of the game process.
* **Shows only what you could already see.** The Live Client API exposes the
  same information as the in-game scoreboard; Wincast turns it into one number.

> Status: early development. Overlay, main window (Live / History / Settings)
> and the .exe work. See [PLANNING.md](PLANNING.md).

## Run it

```bash
pip install -e .[ui,dev]
python -m wincast                  # the overlay: sits in the tray, shows up in game
python -m wincast --replay capture.jsonl.gz --speed 20   # watch a recorded game on the overlay
python -m wincast --headless       # terminal version, same engine
```

**Ctrl+Shift+P** unlocks the overlay so you can drag it; press it again to lock
(click-through). The first launch starts unlocked so you can place it. The tray
icon has the same option and Quit. The game must be in **Borderless** or
Windowed mode. Log file: `%LOCALAPPDATA%\Wincast\wincast.log`.

Riot's certificate (`src/wincast/resources/riotgames.pem`, from
<https://static.developer.riotgames.com/docs/lol/riotgames.pem>) is bundled, so
the connection to the game is verified.

## Build the .exe (Windows)

```bash
pip install -e .[ui,build]
python tools/build_exe.py --zip    # -> dist/Wincast/Wincast.exe and a zip to share
```

No Python needed to run the result. Unsigned, so Windows SmartScreen will ask
"Run anyway" the first time.

## Layout

```
src/wincast/            the app
  client.py             reads the Live Client API (one call per poll)
  session.py            the engine: game detection, scoring, smoothing, curve (no Qt)
  smoothing.py          log-odds moving average over game time
  resolver.py           which model + item prices a new game gets
  worker.py             runs the engine on a background QThread for the UI
  replay.py             plays a saved capture back as if live, at any speed
  headless.py           terminal front end on the same engine
  ui/overlay.py         the click-through pill
  ui/hotkey.py          Ctrl+Shift+P, system-wide (Windows RegisterHotKey)
  ui/tray.py            tray icon: move/lock, quit
  ui/mainwindow.py      the window: Live, History, Settings (Windows 7 Task Manager style)
  ui/charts.py          green-on-black graph and gauge
  history.py            saved games (no names), %LOCALAPPDATA%\Wincast\history
  prefs.py              every setting, its default and limits
  autostart.py          start with Windows (.exe only)
  ui/app.py             wiring; `python -m wincast`
  paths.py              user folders (%LOCALAPPDATA%\Wincast)
  resources/            fallback model + Data Dragon item prices, bundled
src/lolwp/              live scoring code, VENDORED from the model repo -- do not edit
  CORE_MANIFEST.json    source commit + SHA-256 of every vendored file
tools/sync_core.py      re-pulls src/lolwp, the fallback model and the golden game
tools/build_exe.py      PyInstaller build of Wincast.exe
tests/                  incl. test_golden.py: a real anonymised game re-scored
scripts/check_no_player_data.py   pre-commit guard (git config core.hooksPath .githooks)
docs/                   Live Client API reference, original design review
```

## How the model gets here

The model is trained in a separate (private) repo on ranked Summoner's Rift
games. Model files are plain JSON: coefficients and a feature list, never
pickles, never player data. A model built for a different feature set is
refused.

`src/lolwp/` is a byte-for-byte copy of the model repo's live scoring code.
To update it:

```bash
python tools/sync_core.py --check   # has anything drifted?
python tools/sync_core.py           # pull code + current model + golden game
python -m pytest
```

## Privacy

Wincast sends nothing anywhere. Each game it watches is saved on your PC for the
History tab (win-chance curve, result, your champion, objective times by team);
no player names are stored. The only
network requests are Riot's public Data Dragon (item prices, once per patch)
and, if you turn it on, a check for newer models.

---

Wincast isn't endorsed by Riot Games and doesn't reflect the views or opinions
of Riot Games or anyone officially involved in producing or managing Riot Games
properties. Riot Games and all associated properties are trademarks or
registered trademarks of Riot Games, Inc.
