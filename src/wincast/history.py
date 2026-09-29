"""Games Wincast watched, saved on this PC for the History tab.

One small JSON file per game in %LOCALAPPDATA%\\Wincast\\history\\. What's in it:
the win-chance curve, result, your side and champion, game length, which model
scored it, and objective/kill TIMES with the TEAM that took them.

What's not in it: anyone's name. Event killers are resolved to a team in memory
and the names are dropped, so a history file says "your team took Baron at
21:40", never who. Nothing here is ever sent anywhere.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

SCHEMA = 1
ORDER, CHAOS = "ORDER", "CHAOS"

OBJECTIVES = {"DragonKill": "Dragon", "BaronKill": "Baron", "HeraldKill": "Herald",
              "HordeKill": "Grubs", "TurretKilled": "Tower", "InhibKilled": "Inhibitor",
              "ChampionKill": "Kill"}


def _structure_owner(name: str) -> Optional[str]:
    """Which side a turret/inhibitor BELONGED to, from its name
    (Turret_TOrder_... / Turret_T1_... = ORDER, TChaos / T2 = CHAOS)."""
    if re.search(r"_T(Order|1)_", name or ""):
        return ORDER
    if re.search(r"_T(Chaos|2)_", name or ""):
        return CHAOS
    return None


def _other(team):
    return {ORDER: CHAOS, CHAOS: ORDER}.get(team)


def events_for_history(events, name_team) -> List[dict]:
    """Live API events -> [{t, kind, team, detail}], names dropped."""
    out = []
    for e in events or []:
        kind = OBJECTIVES.get(e.get("EventName"))
        if not kind:
            continue
        team = None
        if kind in ("Tower", "Inhibitor"):
            owner = _structure_owner(e.get("TurretKilled") or e.get("InhibKilled") or "")
            team = _other(owner)
        else:
            team = name_team.get(e.get("KillerName"))
            if team is None and kind == "Kill":
                victim = name_team.get(e.get("VictimName"))
                team = _other(victim)
        item = {"t": round(float(e.get("EventTime") or 0.0), 1), "kind": kind, "team": team}
        if kind == "Dragon" and e.get("DragonType"):
            item["detail"] = e["DragonType"]
        out.append(item)
    return out


def record_from_game(g, source="live") -> dict:
    """session.Game -> the JSON record saved for it."""
    curve = [[round(t, 1), round(raw, 4), round(shown, 4)] for t, raw, shown in g.curve]
    shown = [c[2] for c in curve] or [None]
    started = datetime.fromtimestamp(g.started_wall, tz=timezone.utc)
    return {
        "schema": SCHEMA,
        "source": source,
        "started_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_s": round(g.last_t, 1) if g.curve else 0.0,
        "result": g.result,                       # "Win" / "Lose" / None = unfinished
        "team": g.team,                           # ORDER (blue) / CHAOS (red) / None
        "champion": g.champion,
        "game_mode": g.game_mode,
        "model": getattr(g.model, "name", None),
        "model_patch": getattr(g.model, "patch", None),
        "final": shown[-1],
        "low": min(shown) if curve else None,
        "high": max(shown) if curve else None,
        "curve": curve,                           # [game_time_s, raw, smoothed], "mine" side
        "events": events_for_history(g.events, g.name_team),
    }


class HistoryStore:
    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def save(self, record: dict) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        stamp = record["started_utc"].replace(":", "").replace("-", "").replace("T", "_")[:15]
        champ = re.sub(r"[^A-Za-z0-9]+", "", record.get("champion") or "game") or "game"
        path = self.folder / f"{stamp}_{champ}.json"
        n = 2
        while path.exists():
            path = self.folder / f"{stamp}_{champ}_{n}.json"
            n += 1
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)                     # never leave a half-written file
        return path

    def entries(self) -> List[dict]:
        """Summaries (no curve), newest first. Unreadable files are skipped."""
        out = []
        if not self.folder.exists():
            return out
        for path in self.folder.glob("*.json"):
            try:
                rec = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            if rec.get("schema") != SCHEMA:
                continue
            summary = {k: v for k, v in rec.items() if k not in ("curve", "events")}
            summary["path"] = path
            out.append(summary)
        out.sort(key=lambda r: r.get("started_utc", ""), reverse=True)
        return out

    def load(self, path) -> dict:
        return json.loads(Path(path).read_text("utf-8"))

    def delete(self, path):
        Path(path).unlink(missing_ok=True)
