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
- [x] `python -m wincast --headless` (port of `play.py`), live or `--replay`
- [x] SR gate: map 11, CLASSIC, 10 players, no bots (ARAM fixture refused in tests)
- [x] Golden-game test against the model repo's own output
- [ ] Game state machine as a Qt-free class: no game → loading → in game → ended
- [ ] Poll off the UI thread (QThread / worker), emit (game_time, p_mine, team)
- [ ] Smoothing of the displayed number (B-D7): EMA with a short time constant, reset on game start
- [ ] Verify TLS with Riot's `riotgames.pem` instead of `verify=False` (do it in Wincast's own fetch, not the vendored file)
- [ ] Model resolved once at game start, never swapped mid-game

## Phase 6 — Overlay

- [ ] Frameless, always-on-top, click-through pill (`WS_EX_LAYERED | WS_EX_TRANSPARENT`)
- [ ] Number coloured for your side; optional tiny trend line (last few minutes)
- [ ] Global hotkey `Ctrl+Shift+O` lock/unlock; drag when unlocked; position saved per monitor
- [ ] Hidden / "—" outside a supported game
- [ ] Test in Borderless; document that exclusive fullscreen hides it
- [ ] Plain separate window only. No hooks, no injection (Vanguard)

## Phase 7 — Tray + main window

- [ ] Tray icon: show/hide overlay, open window, quit; optional start with Windows
- [ ] **Live** tab: full curve for a second monitor
- [ ] **History** tab: every game's live curve saved locally (no key needed), browse past games
- [ ] **Settings & Model** tab: hotkey, opacity, smoothing, loaded model + patch + scores, import model file, auto-update toggle (off)

## Phase 8 — Models & updates

- [ ] Model folder in `%LOCALAPPDATA%\Wincast\models`; pick the one matching the live patch, else newest, else the bundled fallback
- [ ] Refuse wrong feature-set version with a plain message
- [ ] GitHub Releases check at launch only, SHA-256 verified, previous model kept for rollback; with auto-update off, show a line (not a popup) that a newer model exists

## Phase 9 — Packaging & release

- [ ] PyInstaller or Nuitka single-folder build; installer later
- [ ] Choose a licence (MIT / Apache-2.0 / GPL) before the repo goes public
- [ ] Register the product with Riot's developer portal before publishing a binary
- [ ] README screenshots, privacy statement, "not endorsed by Riot" line (done in README)

## Risks

1. **Riot policy** wording on game-session information is vague; the position is that a local, keyless
   tool showing a number derived from scoreboard-visible data is acceptable. Register the product anyway.
2. **Exclusive fullscreen** can hide normal windows: require Borderless.
3. **Patch drift**: the fallback model ages each patch; Phase 8 is what keeps it current.
4. **Live API changes** (e.g. the 2026-09 structure-name change): fix in the model repo, re-sync.
