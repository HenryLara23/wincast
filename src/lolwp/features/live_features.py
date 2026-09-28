#!/usr/bin/env python3
"""
Serving-side feature extractor: Live Client Data API -> the canonical vector.

The training-side twin is timeline_features.py. Both read their own source into
per-team numbers and then hand off to assemble.py, which owns every difference,
sum and role term -- so the arithmetic cannot drift between them. Neither module
owns a feature list; spec.py does.

Only /allgamedata is used (which bundles /playerlist, /eventdata and /gamestats).
/activeplayer is deliberately untouched: it covers one player, so it could never
be computed for the other nine and would break parity by construction.

Where the two sides differ in KIND, not just in code:

  respawn timers   live gives `respawnTimer` exactly; the timeline has to derive
                   it from a patch constants table. The live number is the truth,
                   which is why constants.py gets FITTED from a capture.
  item gold        live sees the whole inventory, including items the game grants
                   and the timeline never attributes to a player. ItemDB drops
                   those on both sides -- see ItemDB.counts_toward_gold.
  creep score      NOT a feature. Live reports it rounded to the nearest ten and,
                   in the 2026-09-19 capture, two players' values disagreed with
                   match-v5's own totals by 37 and 86. Dropped in spec 1.2.0.
  position         live `position` is "" outside SR draft; match-v5 teamPosition
                   is an algorithm's guess. Agreement is measured, not assumed.

    python -m lolwp.features.live_features            # read a live game
    python -m lolwp.features.live_features --self-test
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from . import constants as C
from . import spec
from .assemble import assemble

BASE = "https://127.0.0.1:2999/liveclientdata"

ORDER, CHAOS = "ORDER", "CHAOS"
BLUE, RED = 100, 200
TEAM_ID = {ORDER: BLUE, CHAOS: RED}

# Structure names encode their OWNER, not the killer, and the spelling has
# changed. A 2026-09-19 capture on patch 16.18 gives
#   Turret_TOrder_L1_P3_1242677625_0     Inhib_TOrder_L1_P1_2786523670_0
# while older patches (and docs/live_api_schema.md as written) give
#   Turret_T1_C_05_A                     Barracks_T1_L1
# Both are accepted: matching only the old spelling silently skipped every
# structure in that game, leaving towers and inhibitors at zero all match.
STRUCT_OWNER_TOKENS = {"T1": BLUE, "TORDER": BLUE, "T2": RED, "TCHAOS": RED}


def structure_owner(name):
    """'Turret_TOrder_L1_...' or 'Turret_T1_C_05_A' -> the team that LOST it."""
    for token in (name or "").split("_")[1:]:
        owner = STRUCT_OWNER_TOKENS.get(token.upper())
        if owner is not None:
            return owner
    return None

SUPPORTED_MAP = 11
SUPPORTED_MODE = "CLASSIC"

# Confirmed from a live capture (2026-09-19): voidgrubs are "HordeKill", matching
# the timeline's HORDE monster type. The substring match is kept as a safety net
# in case Riot renames it, and unknown event names are still surfaced.
VOIDGRUB_EVENT = "HordeKill"
VOIDGRUB_HINTS = ("horde", "grub")

KNOWN_EVENTS = {
    "GameStart", "MinionsSpawning", "FirstBlood", "ChampionKill", "Multikill",
    "Ace", "TurretKilled", "InhibKilled", "InhibRespawningSoon", "InhibRespawned",
    "DragonKill", "HeraldKill", "BaronKill", "GameEnd", "FirstBrick",
    VOIDGRUB_EVENT,
}


def _fetch(path):
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    r = requests.get(f"{BASE}{path}", verify=False, timeout=5)
    r.raise_for_status()
    return r.json()


def all_game_data():
    return _fetch("/allgamedata")


# --------------------------------------------------------------------------- gates


def game_check(data):
    """-> (ok, reason). The overlay shows nothing unless this passes.

    mapNumber 11 and CLASSIC still admit Practice Tool and Co-op vs AI, which is
    what the ten-humans check is for. ARAM is map 12 and excludes itself."""
    g = data.get("gameData") or {}
    players = data.get("allPlayers") or []
    if g.get("mapNumber") != SUPPORTED_MAP:
        return False, f"map {g.get('mapNumber')} is not Summoner's Rift"
    if g.get("gameMode") != SUPPORTED_MODE:
        return False, f"game mode {g.get('gameMode')}"
    if len(players) != 10:
        return False, f"{len(players)} players"
    if any(p.get("isBot") for p in players):
        return False, "bots in the game"
    teams = Counter(p.get("team") for p in players)
    if teams.get(ORDER) != 5 or teams.get(CHAOS) != 5:
        return False, f"team sizes {dict(teams)}"
    return True, ""


# --------------------------------------------------------------------------- pieces


def player_item_gold(player, db):
    """Held item value, priced by itemID through Data Dragon.

    The live `price` field is the COMBINE cost (Lost Chapter reports 250 against a
    real 1200), so it is never used."""
    return db.inventory_value(
        (it.get("itemID"), it.get("count", 1)) for it in player.get("items") or [])


def _aliases(player):
    """Events name players by riotId on newer patches and summonerName on older."""
    return {v for v in (player.get("riotId"), player.get("riotIdGameName"),
                        player.get("summonerName")) if v}


def name_to_team(players):
    return {alias: TEAM_ID.get(p.get("team")) for p in players for alias in _aliases(p)}


def read_events(events, players, game_time_s, unknown=None):
    """-> per-team objective counts and the buff/inhibitor clocks.

    Structures are credited by the owner of what was destroyed; monsters by the
    killer's team -- the same rule the timeline side applies, verified there on
    737,809 real events."""
    owner = name_to_team(players)
    state = {t: {k: 0 for k in ("towers", "inhibs", "dragons", "elders",
                                "heralds", "barons", "voidgrubs")}
             for t in (BLUE, RED)}
    for t in (BLUE, RED):
        state[t].update(baron_at=None, elder_at=None, inhib_times=[])

    for e in events or []:
        name = e.get("EventName") or ""
        when = float(e.get("EventTime") or 0.0)

        if name in ("TurretKilled", "InhibKilled"):
            victim = structure_owner(e.get("TurretKilled") or e.get("InhibKilled"))
            if victim is None:
                if unknown is not None:
                    unknown[f"unparsed structure: "
                            f"{e.get('TurretKilled') or e.get('InhibKilled')}"] += 1
                continue
            scorer = RED if victim == BLUE else BLUE
            if name == "TurretKilled":
                state[scorer]["towers"] += 1
            else:
                state[scorer]["inhibs"] += 1
                state[scorer]["inhib_times"].append(when)
            continue

        lowered = name.lower()
        is_grub = any(h in lowered for h in VOIDGRUB_HINTS)
        if name in ("DragonKill", "HeraldKill", "BaronKill") or is_grub:
            team = owner.get(e.get("KillerName"))
            if team not in state:         # executed, or killed by a minion
                continue
            if is_grub:
                state[team]["voidgrubs"] += 1
            elif name == "DragonKill":
                if e.get("DragonType") == "Elder":
                    state[team]["elders"] += 1
                    state[team]["elder_at"] = when
                else:
                    state[team]["dragons"] += 1
            elif name == "HeraldKill":
                state[team]["heralds"] += 1
            else:
                state[team]["barons"] += 1
                state[team]["baron_at"] = when
            continue

        if unknown is not None and name and name not in KNOWN_EVENTS:
            unknown[name] += 1

    for t in (BLUE, RED):
        s = state[t]
        s["baron_buff_remaining_s"] = _remaining(s.pop("baron_at"), game_time_s,
                                                 C.BARON_BUFF_S)
        s["elder_buff_remaining_s"] = _remaining(s.pop("elder_at"), game_time_s,
                                                 C.ELDER_BUFF_S)
        s["has_soul"] = int(s["dragons"] >= C.SOUL_AT_DRAGONS)
        s["inhibs_down_now"] = sum(
            1 for at in s.pop("inhib_times")
            if game_time_s - at < C.INHIB_RESPAWN_S)
    return state


def _remaining(at, now, duration):
    return 0.0 if at is None else max(0.0, duration - (now - at))


def team_state(data, db, unknown=None):
    """-> {100: {...}, 200: {...}} with exactly assemble.TEAM_KEYS."""
    players = data.get("allPlayers") or []
    game_time = float((data.get("gameData") or {}).get("gameTime") or 0.0)
    events = (data.get("events") or {}).get("Events") or []
    objectives = read_events(events, players, game_time, unknown)

    out = {}
    for live_team, team_id in TEAM_ID.items():
        members = [p for p in players if p.get("team") == live_team]
        scores = [p.get("scores") or {} for p in members]
        gold = {id(p): player_item_gold(p, db) for p in members}
        dead = [p for p in members if p.get("isDead")]
        timers = [float(p.get("respawnTimer") or 0.0) for p in dead]
        out[team_id] = dict(
            objectives[team_id],
            item_gold=sum(gold.values()),
            level_sum=sum(p.get("level", 0) for p in members),
            cs_sum=sum(s.get("creepScore", 0) for s in scores),
            kills=sum(s.get("kills", 0) for s in scores),
            deaths=sum(s.get("deaths", 0) for s in scores),
            assists=sum(s.get("assists", 0) for s in scores),
            alive=sum(1 for p in members if not p.get("isDead")),
            respawn_seconds=sum(timers),
            max_respawn_seconds=max(timers, default=0.0),
            dead_item_gold=sum(gold[id(p)] for p in dead),
        )
    return out


def role_pairs(data, db):
    """{role: ((blue gold, level), (red ...))} for roles present on both sides."""
    sides = {r: {} for r in spec.ROLES}
    for p in data.get("allPlayers") or []:
        role = p.get("position")
        team = TEAM_ID.get(p.get("team"))
        if role in sides and team is not None:
            sides[role][team] = (player_item_gold(p, db), p.get("level", 0))
    return {r: (v[BLUE], v[RED]) for r, v in sides.items()
            if BLUE in v and RED in v}


# --------------------------------------------------------------------------- api


def features(data, db, unknown=None):
    game_time = float((data.get("gameData") or {}).get("gameTime") or 0.0)
    state = team_state(data, db, unknown)
    return assemble(game_time, state[BLUE], state[RED], role_pairs(data, db))


def extract(data, db, unknown=None):
    return spec.vector(features(data, db, unknown))


def as_dict(vec):
    return dict(zip(spec.FEATURE_NAMES, vec))


FEATURE_NAMES = spec.FEATURE_NAMES
mirror = spec.mirror


# --------------------------------------------------------------------------- cli


def _self_test():
    from ..store.items_db import ItemDB
    db = ItemDB("self-test", {
        "3078": {"name": "Trinity Force", "gold": {"total": 3333, "sell": 2333,
                                                   "purchasable": True}, "tags": ["Damage"]},
        "6672": {"name": "Kraken Slayer", "gold": {"total": 3100, "sell": 2170,
                                                   "purchasable": True}, "tags": ["Damage"]},
        "2003": {"name": "Health Potion", "gold": {"total": 50, "sell": 20,
                                                   "purchasable": True}, "tags": ["Consumable"]},
        "3340": {"name": "Stealth Ward", "gold": {"total": 0, "sell": 0,
                                                  "purchasable": True}, "tags": ["Trinket"]},
    })
    roles = list(spec.ROLES)
    players = []
    for i in range(5):
        players.append({
            "team": ORDER, "riotId": f"Blue{i}#NA1", "position": roles[i],
            "level": 11, "isDead": False, "isBot": False, "respawnTimer": 0.0,
            "scores": {"kills": 3, "deaths": 2, "assists": 4, "creepScore": 150},
            "items": [{"itemID": 3078, "count": 1}, {"itemID": 2003, "count": 3},
                      {"itemID": 3340, "count": 1}]})
        players.append({
            "team": CHAOS, "riotId": f"Red{i}#NA1", "position": roles[i],
            "level": 10, "isDead": i == 0, "isBot": False,
            "respawnTimer": 21.0 if i == 0 else 0.0,
            "scores": {"kills": 2, "deaths": 3, "assists": 3, "creepScore": 130},
            "items": [{"itemID": 6672, "count": 1}]})

    events = [
        {"EventName": "GameStart", "EventTime": 0.0},
        {"EventName": "TurretKilled", "TurretKilled": "Turret_T2_C_05_A",
         "KillerName": "Blue0#NA1", "EventTime": 600.0},
        {"EventName": "TurretKilled", "TurretKilled": "Turret_T1_C_05_A",
         "KillerName": "Red0#NA1", "EventTime": 700.0},
        {"EventName": "InhibKilled", "InhibKilled": "Barracks_T2_L1",
         "KillerName": "Blue1#NA1", "EventTime": 1100.0},
        {"EventName": "DragonKill", "DragonType": "Fire",
         "KillerName": "Blue2#NA1", "EventTime": 500.0},
        {"EventName": "DragonKill", "DragonType": "Elder",
         "KillerName": "Red2#NA1", "EventTime": 1150.0},
        {"EventName": "BaronKill", "KillerName": "Blue3#NA1", "EventTime": 1140.0},
        {"EventName": "HeraldKill", "KillerName": "Minion_T1", "EventTime": 800.0},
        {"EventName": "HordeKill", "KillerName": "Blue1#NA1", "EventTime": 400.0},
        {"EventName": "SomethingNew", "EventTime": 900.0},
    ]
    game = {"allPlayers": players, "events": {"Events": events},
            "gameData": {"gameTime": 1200.0, "mapNumber": 11, "gameMode": "CLASSIC"}}

    ok, why = game_check(game)
    assert ok, why
    for bad, expect in (({"mapNumber": 12}, "Summoner's Rift"),
                        ({"gameMode": "ARAM"}, "game mode")):
        spoiled = {**game, "gameData": {**game["gameData"], **bad}}
        ok2, why2 = game_check(spoiled)
        assert not ok2 and expect in why2, why2
    bots = {**game, "allPlayers": [{**players[0], "isBot": True}] + players[1:]}
    assert game_check(bots) == (False, "bots in the game")

    unknown = Counter()
    d = as_dict(extract(game, db, unknown))
    assert len(d) == spec.N_FEATURES

    assert d["game_time_s"] == 1200.0
    assert d["diff_item_gold"] == (3333 - 3100) * 5
    assert d["sum_item_gold"] == (3333 + 3100) * 5
    assert d["diff_towers"] == 0 and d["sum_towers"] == 2   # credited by owner
    assert d["diff_inhibs"] == 1 and d["diff_inhibs_down_now"] == 1
    assert d["diff_dragons"] == 1 and d["diff_elders"] == -1
    assert d["diff_heralds"] == 0                           # minion kill, nobody
    assert d["diff_voidgrubs"] == 1, d["diff_voidgrubs"]
    assert d["diff_barons"] == 1
    assert d["diff_baron_buff_remaining_s"] == C.BARON_BUFF_S - 60
    assert d["diff_elder_buff_remaining_s"] == -(C.ELDER_BUFF_S - 50)
    assert d["diff_alive"] == 1
    assert d["diff_respawn_seconds"] == -21.0
    assert d["diff_max_respawn_seconds"] == -21.0
    assert d["diff_dead_item_gold"] == -3100
    assert d["diff_kills"] == 5 and d["sum_kills"] == 25
    assert abs(sum(d[f"role_{r}_item_gold_rel"] for r in spec.ROLES)) < 1e-6
    assert "SomethingNew" in unknown and "HordeKill" not in unknown
    assert not db.misses, db.misses

    # the invariant the model is built on
    import numpy as np
    v = extract(game, db)
    swapped = {**game, "allPlayers": [
        {**p, "team": CHAOS if p["team"] == ORDER else ORDER} for p in players],
        "events": [dict(e) for e in events]}
    swapped["events"] = {"Events": [
        {**e, **({"TurretKilled": e["TurretKilled"].replace("_T1_", "_TX_")
                  .replace("_T2_", "_T1_").replace("_TX_", "_T2_")}
                 if e.get("TurretKilled") else {}),
         **({"InhibKilled": e["InhibKilled"].replace("_T2_", "_TX_")
             .replace("_T1_", "_T2_").replace("_TX_", "_T1_")}
            if e.get("InhibKilled") else {})}
        for e in events]}
    w = extract(swapped, db)
    assert np.allclose(w, spec.mirror(v), atol=1e-6), "swapping teams must mirror"

    print(f"live_features self-test passed: {spec.N_FEATURES} features, "
          f"spec {spec.FEATURE_SET_VERSION}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--file", help="a saved /allgamedata JSON instead of a live game")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()

    from ..store.items_db import ItemDB
    data = json.load(open(args.file)) if args.file else all_game_data()
    ok, why = game_check(data)
    print(f"game check: {'ok' if ok else 'SKIP - ' + why}")
    db = ItemDB.load()
    unknown = Counter()
    d = as_dict(extract(data, db, unknown))
    width = max(len(k) for k in d)
    for k, v in d.items():
        print(f"  {k:<{width}}  {v:>10.1f}")
    if unknown:
        print(f"\nunrecognised event names: {dict(unknown)}")


if __name__ == "__main__":
    main()
