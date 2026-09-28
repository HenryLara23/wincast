# Wincast

A live win-chance overlay for League of Legends, Summoner's Rift.

* **Local only.** Reads the Live Client Data API that the game itself serves on
  your PC (`https://127.0.0.1:2999`). No Riot API key, no account, no server.
* **Nothing touches the game.** The overlay is an ordinary separate window; it
  never hooks, injects into or reads the memory of the game process.
* **Shows only what you could already see.** The Live Client API exposes the
  same information as the in-game scoreboard; Wincast turns it into one number.

> Status: early development. The scoring loop runs in a terminal today; the Qt
> overlay is next. See [PLANNING.md](PLANNING.md).

## Run it (terminal version)

```bash
pip install -e .[ui,dev]          # or: pip install -r requirements.txt
python -m wincast --headless      # waits for a game, prints a line every 3 s
```

Works in any Summoner's Rift game with ten human players. The game must be in
**Borderless** or Windowed mode for the overlay to draw over it.

## Layout

```
src/wincast/            the app
  headless.py           terminal scoring loop (the overlay's engine)
  paths.py              user folders (%LOCALAPPDATA%\Wincast)
  resources/            fallback model + Data Dragon item prices, bundled
src/lolwp/              live scoring code, VENDORED from the model repo -- do not edit
  CORE_MANIFEST.json    source commit + SHA-256 of every vendored file
tools/sync_core.py      re-pulls src/lolwp, the fallback model and the golden game
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

Wincast stores nothing about other players and sends nothing anywhere. The only
network requests are Riot's public Data Dragon (item prices, once per patch)
and, if you turn it on, a check for newer models.

---

Wincast isn't endorsed by Riot Games and doesn't reflect the views or opinions
of Riot Games or anyone officially involved in producing or managing Riot Games
properties. Riot Games and all associated properties are trademarks or
registered trademarks of Riot Games, Inc.
