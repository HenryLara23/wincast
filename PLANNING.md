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
- [x] `GameEnd` Win/Lose seen in real games (Victory recorded from the .exe)
- [ ] Not yet seen in a real game: a reconnect resuming the same game; the `/eventdata` end-of-game fallback
- [x] Smoothing constant: 5 s default accepted in play; adjustable in Settings

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
- [x] Settings for scale / opacity / trend on-off / trend minutes: UI in Phase 7 (Settings tab)

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

## Phase 8 — Models, updates & automatic releases

Decided 2026-09-28 (Henry): everything automatic from the server; retrain every 6 h; publish the day's
best model per patch; the app itself released once per patch as **v<patch>** (e.g. `v16.20`, no
mid-patch releases) with the PREVIOUS patch's last model built in. App auto-update off by default.

**GitHub layout (public repo `HenryLara23/wincast`):** code in the repo, no weights committed. Releases:

| Release | Tag | Files | Made by |
|---|---|---|---|
| App, once per patch | `v16.20` (Latest) | `Wincast-16.20-win64.zip`, `SHA256SUMS` | GitHub Actions |
| Models of a patch | `models-16.19` | `model-16.19-<YYYYMMDD-HHMM>.fs1.2.0.json` one per day, `SHA256SUMS` | the server |

- [x] App: `models.py` (ModelStore: validate, choose newest-or-pinned, import), `StoreResolver` (per game,
  never mid-game), `updates.py` (newest `models-*` release → newest file for this feature set, SHA-256
  verified), **Models tab** (list, Import, Use Selected/Newest, Remove, Check Now → Download, startup
  check off by default)
- [x] Server: `lolmodel/scripts/publish_release.py` + `deploy/lolwp-publish.{service,timer}` (daily 23:30
  UTC): uploads the patch's best model into `models-<patch>` when it changed; creates a patch's release
  only once it has ≥ 20k training games; rewrites SHA256SUMS; never "latest". Retrain timer → every 6 h.
- [x] `.github/workflows/release-app.yml` (Windows runner): on a new `models-<patch>` release (or by hand)
  → fetch the previous patch's last model (`tools/fetch_release_model.py`) → `tools/prepare_release.py`
  (version = patch, built-in model + its item prices, manifest, notes) → tests → `tools/build_exe.py --zip`
  → `gh release create v<patch> --latest`. Skips if `v<patch>` exists. First ever run keeps the repo's model.
- [x] Tests score with a frozen model/items (`tests/fixtures/golden_model.json`, `fixtures/ddragon/`) so
  swapping the built-in model can't break them. Rehearsed prepare → test → build → zip on Linux.
- [x] 95 app tests; 9 publisher tests (lolmodel)
- [x] Server set up and first upload done (2026-09-29): `models-16.19` holds `model-16.19-20260929-0204.fs1.2.0.json`
- [x] First Actions run (manual, 16.19) failed after ~1 min; likely cause: Windows checkout turned LF into CRLF,
  so the byte-exact manifest test failed. Fix: `.gitattributes` (`-text` for vendored code, resources,
  fixtures) + `core.autocrlf false` on the runner; actions bumped to checkout@v5 / setup-python@v6 (Node 24)
- [x] Second run failed the same way: in `.gitattributes` the LAST matching line wins, and `* text=auto`
  was last, overriding `-text`. Reordered (catch-all first, `* text=auto eol=lf`). Verified by cloning with
  `core.autocrlf=true core.eol=crlf`: all hashes match after the fix, all six differed before.
- [x] **v16.19 published by GitHub Actions** (2026-09-29, 59 MB zip); the released .exe found and installed
  `lolwp_16.19_20260929-0204` via Check Now. Full loop proven: server → models release → app release → app update.
- [ ] Not yet proven: the AUTOMATIC trigger (server creates `models-16.20` → Actions builds v16.20). v16.19 was
  started by hand because the workflow wasn't pushed yet when `models-16.19` was created. Watch it at 16.20.
- [ ] ~~warn when the model's patch is behind Riot's current patch~~ dropped 2026-09-29: no model nagging

## Loose ends (audit 2026-09-29)

- [x] 2026-09-30: Neeko's disguise split one game into 37 (roster included champions); roster is now team + name
- [x] 2026-10-01: losses saved with no result. Cause (Viego game + its replay): after a defeat the client closes within ~1 s of GameEnd, between two 2 s snapshots; wins linger 6+ s. Fix: poll the small /eventdata every 250 ms between snapshots while in game
- [x] No model nagging (models update only via the Models tab / its opt-in startup check). Last-season app note on the Live tab from Jan 15 of the next season (offline, clock + version). Help: Check for Updates / Report a Problem / Open Log Folder; About links the repo
- [x] X quits (Settings: "Keep running in the tray" hides instead); no pop-up notifications (hotkey trouble shows in Settings); only one Wincast runs, a second launch shows the first's window
- [x] Commit the private repo's Phase 8 server changes (`lolmodel/scripts/publish_release.py`, its test,
  `deploy/lolwp-publish.{service,timer}`, `deploy/lolwp-train.timer`, `deploy/README.md`, root `.gitignore`).
  The server already runs them; the repo doesn't have them yet.
- [ ] Fine-grained token expiry: when it lapses, the daily upload fails quietly (`journalctl -u lolwp-publish`).
  Set a reminder a week before; renew and paste into `/srv/lol/release.env`.
- [x] Root `planning.md` (monorepo) header updated to point at wincast/PLANNING.md
- [ ] Dev-only: Microsoft Store Python keeps a separate AppData (history, log) from the .exe. Not a user issue.

## Phase 9 — Packaging & release

- [x] PyInstaller single-folder build (`tools/build_exe.py`), works on Henry's PC
- [x] Zip per release, built by GitHub Actions (Phase 8)
- [x] App icon: "W as a graph" (tools/make_icon.py -> resources/icon/; .exe, window, tray). Installer icon comes with the installer
- [x] **Installer + uninstaller** (`installer/wincast.iss`, Inno Setup; built by the release workflow next to
  the zip): per-user install by default (`%LOCALAPPDATA%\Programs\Wincast`, no admin), "all users" option,
  Start-menu shortcut, optional desktop icon, uninstaller removes the start-with-Windows value and asks whether
  to delete history/models/settings. **Not yet tried on a real PC**: run a test build (Actions > Release app >
  Test build only) and install/uninstall it once
- [x] README screenshots: Live, overlay (+ close-up), History, Models (docs/screenshots/)
- [x] Licence: MIT
- [ ] Register with Riot's developer portal as a **Production** product (the public app runs on models trained from
  Match-v5 data the collector gathers). Applied 2026-09-28; next: riot.txt on GitHub Pages, then Verify URL
- [x] Privacy statement and "not endorsed by Riot" line (README, release notes, About box)

## Risks

1. **Riot policy** wording on game-session information is vague; the position is that a local, keyless
   tool showing a number derived from scoreboard-visible data is acceptable. Register the product anyway.
2. **Exclusive fullscreen** can hide normal windows: require Borderless.
3. **Patch drift**: the fallback model ages each patch; Phase 8 is what keeps it current.
4. **Live API changes** (e.g. the 2026-09 structure-name change): fix in the model repo, re-sync.
