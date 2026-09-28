"""
Print the live win chance in a terminal. No window, but the same engine the
overlay uses (session.Engine), so this is also how to check it quickly.

    python -m wincast --headless
    python -m wincast --headless --model path/to/model.json
    python -m wincast --headless --replay capture.jsonl.gz [--speed 10]
    python -m wincast --headless --smoothing 0        # raw model output

It reads only the Live Client Data API on this PC, and only scores a
Summoner's Rift game with ten human players.
"""

import argparse
import glob
import gzip
import json
import time

from .client import OFFLINE, LiveClient, Poll
from .resolver import FixedResolver
from .session import ENDED, IN_GAME, Engine


def bar(p, width=30):
    n = int(round(p * width))
    return "#" * n + "." * (width - n)


def line(up):
    t = up.game_time or 0.0
    clock = f"{int(t // 60):2d}:{int(t % 60):02d}"
    if up.team in ("ORDER", "CHAOS"):
        side = "blue" if up.team == "ORDER" else "red"
        return (f"{clock}  you ({side}) {up.p_mine:6.1%}  [{bar(up.p_mine)}]"
                f"  raw {up.p_mine_raw:6.1%}")
    return (f"{clock}  blue {up.p_mine:6.1%}  [{bar(up.p_mine)}]  red {1 - up.p_mine:6.1%}"
            f"  raw {up.p_mine_raw:6.1%}")


def polls_live(client, interval_fast, interval_slow):
    while True:
        p = client.poll()
        yield p
        time.sleep(interval_fast if p.kind == "data" else interval_slow)


def polls_replay(pattern, speed):
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
            yield Poll.of(rec.get("data"))
    yield Poll(OFFLINE, detail="end of capture")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wincast --headless", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="a live model .json (default: the one bundled with the app)")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between reads in game")
    ap.add_argument("--smoothing", type=float, default=5.0,
                    help="smoothing time constant in game seconds (0 = off)")
    ap.add_argument("--ddragon", help="force a Data Dragon version, e.g. 16.18.1")
    ap.add_argument("--replay", help="play back a saved capture instead of a live game")
    ap.add_argument("--speed", type=float, default=0,
                    help="replay speed (1 = real time, 0 = as fast as possible)")
    args = ap.parse_args(argv)

    resolver = FixedResolver(args.model, args.ddragon)
    try:
        model, db = resolver()
    except Exception as exc:
        raise SystemExit(f"can't load the model: {exc}")
    print(f"model {model.name}  (patch {model.patch}),  item prices: Data Dragon {db.version}")

    engine = Engine(resolver, tau_s=args.smoothing)
    if args.replay:
        source = polls_replay(args.replay, args.speed)
    else:
        client = LiveClient()
        print(f"connection: {client.tls}")
        source = polls_live(client, args.interval, max(args.interval, 5.0))

    last_state, last_printed_t = None, None
    try:
        for poll in source:
            up = engine.feed(poll)
            if up.state != last_state:
                label = up.state.replace("_", " ")
                print(f"[{label}]" + (f" {up.detail}" if up.detail else ""), flush=True)
                last_state = up.state
            if up.state == IN_GAME and up.p_mine is not None:
                # nothing new while the clock stands still (everyone still loading,
                # a pause); a replay runs faster than real time, so thin it too
                if last_printed_t is not None and (
                        up.game_time <= last_printed_t
                        or (args.replay and up.game_time - last_printed_t < args.interval)):
                    continue
                last_printed_t = up.game_time
                print(line(up), flush=True)
            elif up.state == ENDED and up.p_mine is not None and last_printed_t != up.game_time:
                last_printed_t = up.game_time
                print(line(up) + f"   -> {up.result}", flush=True)
    except KeyboardInterrupt:
        pass
    g = engine.game or engine.last_game
    if g is not None and g.unknown_events:
        print(f"unrecognised live event names: {dict(g.unknown_events)}")
    return 0
