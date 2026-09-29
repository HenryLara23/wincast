# Wincast — planning

The public app. Split out of the monorepo on 2026-09-27; the collector and the
model repo are private and frozen except for bugs or a major game change.

**Legend:** `[x]` done · `[~]` partly done · `[ ]` not started

## Decisions

| Date | Decision | Why |
|---|---|---|
| 2026-09-27 | Name: **Wincast** | Short, says what it does; no Riot trademark in the name |
| 2026-09-27 | **Three repos**: `wincast` (public), `wincast-model` and `wincast-collector` (private) | Only the app is published |
| 2026-09-27 | **No Riot API key** in the public app | Live Client API is unauthenticated and local; keeps the app clear of key/rate-limit policy |
| 2026-09-27 | Scoring code is **vendored** into `src/lolwp/` by `tools/sync_core.py`, byte-identical, hash-checked, plus a golden-game test | App is public, model repo is private, so it can't be a dependency. Keeps the "same file for training and serving" rule |
| 2026-09-27 | **PySide6** (not PyQt6) | LGPL: a published build doesn't force the app's licence. API nearly identical |
| 2026-09-27 | Qt **Widgets** + pyqtgraph for v1 | Simplest route to Win32 click-through flags; QML later if wanted |
| 2026-09-27 | Score every 10-human SR game, **no ranked/normal disclaimer** | The API has no queue ID; the number is shown as is |
| 2026-09-27 | **Post-game deep review pinned** for a private build | Needs a key; `lolmodel/postgame.py` is its engine when it happens |
| (from B-D6/B-D7) | Overlay: single number, minimal footprint; auto-update off by default | Carried over from the monorepo plan |

## Phase 5 — Scoring loop

- [x] Vendored core (6 files) + fallback model + bundled Data Dragon prices (16.18.1)
- [x] `python -m wincast --headless`, live or `--replay`, now on the same engine as the overlay
- [x] SR gate: map 11, CLASSIC, 10 players, no bots (ARAM fixture refused in tests)
- [x] Golden-game test against the model repo's own output
- [x] `client.py`: one `/allgamedata` call per poll, sorted into OFFLINE / LOADING / DATA / ERROR
- [x] `session.py`: Qt-free engine. States no game → loading → unsupported / in game → ended (with Win/Lose from `GameEnd`)
  - game identity = roster, so a crash + reconnect resumes the same game (curve, smoothing, model)
  - model + item prices resolved once per game, never swapped mid-game (`resolver.py`)
  - transient errors hold the last update; a bad snapshot keeps the last good number
  - the whole game's curve is kept (`Game.curve`) for the History tab later
- [x] `smoothing.py`: EMA over game time in log-odds, tau 5 s default (`--smoothing 0` = raw). Pauses freeze it.
- [x] `worker.py`: `ScoringRunner` runs the engine on a QThread (2 s in game, 5 s idle); signals `updated`, `stateChanged`, `gameStarted`, `gameEnded`, `failed`, all delivered on the UI thread; clean `stop()`
- [x] TLS: verifies with `src/wincast/resources/riotgames.pem` (bundled, packaged via pyproject); falls back to unverified and says so if the check ever fails
- [x] **First real game (2026-09-28):** TLS verified with riotgames.pem against 127.0.0.1; loading screen = HTTP 404 then data; side detected; smoothing looked right; a mid-game disconnect went to "will resume on reconnect". Game clock sits at 0:00 for ~30 s while players load (headless no longer repeats those lines).
- [ ] Still to see in a real game: `GameEnd` Win/Lose, and an actual reconnect resuming the same game
- [ ] Tune the smoothing constant by eye once the overlay exists

## Phase 6 — Overlay

- [x] `ui/overlay.py`: frameless, always-on-top, no taskbar entry, never takes focus; click-through when locked (`WindowTransparentForInput` = `WS_EX_LAYERED | WS_EX_TRANSPARENT`)
- [x] Number coloured by the chance itself (red → grey → green, saturating at 20/80%) + 4-minute trend line. Caption and bar removed after the first in-game test ("fluff"); pill is 150×46. "Victory"/"Defeat" replaces the trend at game end; the unlock hint is a tooltip
- [x] Shown only while scoring or just ended ("Victory"/"Defeat"); hidden otherwise; always shown while unlocked
- [x] `ui/hotkey.py`: global `Ctrl+Shift+P` via RegisterHotKey on a hidden helper window. Does not fire while League has focus (tested); accepted: unlock from the desktop or tray, position is remembered. (A GetAsyncKeyState poller was tried and dropped: not wanted.)
- [x] Drag when unlocked (native `startSystemMove`); position saved per screen in QSettings, pulled back on-screen if a monitor went away; first launch starts unlocked
- [x] Topmost re-asserted every 3 s on Windows (borderless games can push above)
- [x] Minimal tray: move/lock, quit, tooltip with the current chance (Phase 7 grows it)
- [x] `python -m wincast --replay capture --speed N` drives the overlay from a recording
- [x] Log file `%LOCALAPPDATA%\Wincast\wincast.log` (the .exe has no console)
- [x] **First .exe**: `tools/build_exe.py` (PyInstaller onedir, resources added explicitly and checked after the build). Frozen build verified end to end on Linux; **Windows build not yet run**
- **Hand test on Windows, 2026-09-28** (`python -m wincast`, Borderless):
  - [x] visible over the game; clicks go through it when locked
  - [x] never steals focus when the number updates
  - [x] looks right at Henry's display scaling (a resize option would be nice: Phase 7 setting)
  - [x] hotkey works from the desktop, not while the game has focus (accepted)
  - [ ] position remembered after restart
  - [x] `Wincast.exe` from `dist/` works (Henry's PC)
- [x] Trend window 10 min by default (was 4), and drawn on a log-odds scale (1%..99%) so it stays visible at 98%+ / 2%- (it flattened into the box edge before)
- [x] `.exe` build tested by Henry: works
- [ ] Settings for scale / opacity / trend on-off / trend minutes exist in QSettings (`overlay/scale`, `overlay/opacity`, `overlay/trend`, `overlay/trend_minutes`) but have no UI until Phase 7

## Phase 7 — Main window, history, settings

Look: Windows 7 Task Manager (Henry's pick): menu bar, tabs, sunken list, bottom-right buttons,
sectioned status bar, green-on-black Performance-tab graphs. Qt's `windowsvista` style is forced on
Windows (Qt 6.7+ would otherwise pick the rounded Windows 11 style).

- [x] **Game history** (`history.py`): one JSON per game in `%LOCALAPPDATA%\Wincast\history\` —
  curve, result, side, champion, length, model, objective/kill times with the TEAM that took them.
  **No names**: killers are resolved to a team in memory and dropped. Replays save to `history-replay\`.
- [x] Engine hands each game over exactly once: on GameEnd, or when abandoned after ≥ 60 s (new game,
  unsupported mode, app quit)
- [x] `ui/mainwindow.py`: File (history folder, exit) · Options (always on top, move overlay, open at
  startup) · View (refresh, F5) · Help (about)
  - [x] **Live**: segmented gauge + green graph of the whole game + "This game" details
  - [x] **History**: sortable list (date, champion, side, result, length, final/lowest/highest), graph of
    the selected game with objective letters and kill ticks per team, Delete, Open Folder
  - [x] **Settings**: overlay size / opacity / trend on-off / trend minutes / hotkey, smoothing, start with
    Windows (.exe only), open window at startup, model info; Apply + Restore Defaults, applied live
  - [x] Status bar: state · win chance · games recorded · overlay locked/moving
- [x] Tray: "Open Wincast" (also single/double click); closing the window hides it (tray message once)
- [x] `prefs.py`: every setting's key, default and limits in one place; bad values fall back
- [x] Worker timer destroyed in its own thread (fixes a "Timers cannot be stopped from another thread" warning at exit)
- [x] Graph fill/line green above 50 %, red below (Henry, 2026-09-28)
- [x] **Bug (first real test):** a finished game wasn't saved while the app kept running. Fixed twice over:
  the GameEnd result is read before scoring (a snapshot that fails to score can't hide it), and a game
  whose client has been gone/loading for 3 min (wall clock) is saved as "No result"
- [x] "Open Folder" under Microsoft Store Python opens the redirected
  `AppData\Local\Packages\PythonSoftwareFoundation.Python.3.xx_…\LocalCache\Local\Wincast\…` folder
  (Store apps' AppData writes are virtualised; the .exe isn't affected)
- [x] **GameEnd missed twice (2026-09-28 log):** data stopped at 34:46 right after the enemy took both
  nexus towers; ~10 s of no answers; client closed. Now: when a snapshot fails mid-game the worker asks
  the light `/eventdata` endpoint for GameEnd, and every change in poll outcome is logged (`poll: …`)
- [x] Saved game confirmed from the .exe log (`saved game to …` 3 min after the client closed); F5 before that showed nothing
- [x] History graph zoom (Henry): drag to zoom a time range, wheel zooms around the cursor, right-click / Back
  = previous zoom, double-click / Home = whole game; time axis switches to m:ss when zoomed in
- [x] Bigger kill ticks (2 px, ~4 % of the graph height) and objective letters (12 px)
- [x] History list and graph in a splitter (list ~150 px by default, divider remembered); list scrolls. (Reverted once, brought back: Henry missed it)
- [x] Real game with the .exe, 2026-09-28: result recorded (Victory)
- [x] Hand test (Henry, 2026-09-28): hotkey change in Settings applies live (tray shows it); start with Windows from the .exe works

**Phase 7 done.**

## Phase 8 — Models & updates

- [ ] Model folder in `%LOCALAPPDATA%\Wincast\models`; pick the one matching the live patch, else newest, else the bundled fallback
- [ ] Refuse wrong feature-set version with a plain message
- [ ] GitHub Releases check at launch only, SHA-256 verified, previous model kept for rollback; with auto-update off, show a line (not a popup) that a newer model exists

## Phase 9 — Packaging & release

- [x] PyInstaller single-folder build (`tools/build_exe.py`), works on Henry's PC
- [ ] Installer (or a zip) and an app icon file
- [x] Licence: MIT
- [ ] Register the product with Riot's developer portal before publishing a binary
- [ ] README screenshots, privacy statement, "not endorsed by Riot" line (done in README)

## Risks

1. **Riot policy** wording on game-session information is vague; the position is that a local, keyless
   tool showing a number derived from scoreboard-visible data is acceptable. Register the product anyway.
2. **Exclusive fullscreen** can hide normal windows: require Borderless.
3. **Patch drift**: the fallback model ages each patch; Phase 8 is what keeps it current.
4. **Live API changes** (e.g. the 2026-09 structure-name change): fix in the model repo, re-sync.
