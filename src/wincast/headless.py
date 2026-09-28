"""
Print the live win chance in a terminal. No window: this is the scoring loop
the overlay will sit on, and a way to check a model or a capture quickly.

    python -m wincast --headless
    python -m wincast --headless --model path/to/model.json
    python -m wincast --headless --replay capture.jsonl.gz

Ported from lolmodel/play.py. It reads only the Live Client Data API on this PC,
and only scores a Summoner's Rift game with ten human players.
"""

import argparse
import glob
import gzip
import json
import time
from collections import Counter

from .paths import FALLBACK_MODEL, setup_ddragon_cache

setup_ddragon_cache()                     # before lolwp.store.items_db is imported

from lolwp.features import live_features as live          # noqa: E402
from lolwp.model.bundle import BundleError, Model         # noqa: E402
from lolwp.store.items_db import ItemDB                   # noqa: E402


def my_team(data):
    """'ORDER' / 'CHAOS' for the player on this PC, or None (spectating)."""
    me = (data.get("activePlayer") or {}).get("riotId") or \
         (data.get("activePlayer") or {}).get("summonerName")
    if not me:
        return None
    for p in data.get("allPlayers") or []:
        if me in (p.get("riotId"), p.get("summonerName")):
            return p.get("team")
    return None


def score(data, model, db, unknown=None):
    """-> (game_time_s, P(blue wins), my team or None). Raises on a bad snapshot."""
    t = float((data.get("gameData") or {}).get("gameTime") or 0.0)
    p_blue = model.predict_one(live.extract(data, db, unknown))
    return t, p_blue, my_team(data)


def bar(p, width=30):
    n = int(round(p * width))
    return "#" * n + "." * (width - n)


def line(t, p_blue, team):
    clock = f"{int(t // 60):2d}:{int(t % 60):02d}"
    if team in ("ORDER", "CHAOS"):
        mine = p_blue if team == "ORDER" else 1 - p_blue
        side = "blue" if team == "ORDER" else "red"
        return f"{clock}  you ({side}) {mine:6.1%}  [{bar(mine)}]"
    return f"{clock}  blue {p_blue:6.1%}  [{bar(p_blue)}]  red {1 - p_blue:6.1%}"


def load_model(path=None):
    return Model.load(str(path or FALLBACK_MODEL))


def load_items(model, ddragon=None):
    """Item prices for the model's patch; the newest cached/available otherwise."""
    tries = []
    if ddragon:
        tries.append({"version": ddragon})
    if model.patch:
        tries.append({"game_version": model.patch + ".1"})
    tries.append({})
    last = None
    for kw in tries:
        try:
            return ItemDB.load(**kw)
        except Exception as exc:          # offline, or no release for that patch
            last = exc
    raise SystemExit(f"could not load item prices: {last}")


def snapshots_live(interval):
    while True:
        try:
            yield live.all_game_data()
        except Exception:
            yield None                    # no game running (connection refused)
        time.sleep(interval)


def snapshots_replay(pattern, speed):
    files = sorted(glob.glob(pattern))
    if not files:
        raise SystemExit(f"no capture matches {pattern}")
    prev = None
    with gzip.open(files[-1], "rt", encoding="utf-8") as fh:
        for raw in fh:
            rec = json.loads(raw)
            if prev is not None and speed > 0:
                time.sleep(max(0.0, (rec["wall_ms"] - prev) / 1000 / speed))
            prev = rec["wall_ms"]
            yield rec.get("data")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wincast --headless", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="a live model .json (default: the one bundled with the app)")
    ap.add_argument("--interval", type=float, default=3.0, help="seconds between reads")
    ap.add_argument("--ddragon", help="force a Data Dragon version, e.g. 16.18.1")
    ap.add_argument("--replay", help="play back a saved capture instead of a live game")
    ap.add_argument("--speed", type=float, default=0,
                    help="replay speed (1 = real time, 0 = as fast as possible)")
    args = ap.parse_args(argv)

    try:
        model = load_model(args.model)
    except (OSError, BundleError) as exc:
        raise SystemExit(f"can't use {args.model or FALLBACK_MODEL}: {exc}")
    print(f"model {model.name}  (patch {model.patch})")
    db = load_items(model, args.ddragon)
    print(f"item prices: Data Dragon {db.version}")

    source = (snapshots_replay(args.replay, args.speed) if args.replay
              else snapshots_live(args.interval))
    unknown, waiting, last_t = Counter(), None, None
    try:
        for data in source:
            if not data:
                msg = "waiting for a game..."
            else:
                ok, why = live.game_check(data)
                msg = None if ok else f"not scoring: {why}"
            if msg:
                if msg != waiting:
                    print(msg, flush=True)
                    waiting = msg
                continue
            waiting = None
            t = (data.get("gameData") or {}).get("gameTime")
            if args.replay and last_t is not None and t is not None and t - last_t < args.interval:
                continue                  # thin the replay to the live cadence
            last_t = t
            try:
                print(line(*score(data, model, db, unknown)), flush=True)
            except Exception as exc:      # one odd snapshot shouldn't end the session
                print(f"  (skipped a snapshot: {type(exc).__name__}: {exc})")
    except KeyboardInterrupt:
        pass
    if unknown:
        print(f"unrecognised live event names: {dict(unknown)}")
    return 0
