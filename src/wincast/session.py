"""The scoring engine: Live Client polls in, one display-ready Update out.

Qt-free on purpose, so it runs the same in the overlay, the headless mode and
the tests, and a whole game can be replayed through it in milliseconds.

States
  NO_GAME      no game client (or it closed)
  LOADING      the client is up but there is no game yet (loading screen)
  UNSUPPORTED  a game, but not one the model scores (ARAM, Practice Tool, bots)
  IN_GAME      scoring
  ENDED        the GameEnd event arrived; the result is known

Rules that matter
  * The model and item prices are resolved ONCE, when a game is first seen, and
    kept for that game even if a newer model appears (planning: never swap
    mid-game).
  * A game is identified by its roster (team, champion, name for all ten). If the
    client drops and comes back with the same roster -- a crash and reconnect --
    it is the SAME game: curve, smoothing and model carry on.
  * A transient error (timeout during a hitch) changes nothing.
  * One bad snapshot never ends a game: the last good number stays up.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Callable, List, Optional, Tuple

from .client import DATA, ERROR, LOADING, OFFLINE, Poll
from .smoothing import LogitEMA

from lolwp.features import live_features as live

NO_GAME, LOADING_STATE, UNSUPPORTED, IN_GAME, ENDED = (
    "no_game", "loading", "unsupported", "in_game", "ended")

ORDER, CHAOS = "ORDER", "CHAOS"
NEW_GAME_IF_CLOCK_BACK_S = 10.0


@dataclass(frozen=True)
class Update:
    state: str
    detail: str = ""
    game_id: int = 0                  # increments per new game; 0 = none yet
    game_time: Optional[float] = None
    team: Optional[str] = None        # ORDER / CHAOS for the player on this PC, None if spectating
    p_blue: Optional[float] = None    # raw model output, P(blue side wins)
    p_mine_raw: Optional[float] = None
    p_mine: Optional[float] = None    # smoothed; what the overlay shows ("mine" = blue when spectating)
    result: Optional[str] = None      # "Win" / "Lose" (from this PC's player) once ENDED
    model_name: Optional[str] = None


@dataclass
class Game:
    id: int
    roster: Tuple
    model: object
    db: object
    team: Optional[str]
    smoother: LogitEMA
    curve: List[Tuple[float, float, float]] = field(default_factory=list)  # (t, raw, smoothed)
    last_t: float = -1.0
    result: Optional[str] = None
    unknown_events: Counter = field(default_factory=Counter)
    bad_snapshots: int = 0


def my_team(data) -> Optional[str]:
    """'ORDER' / 'CHAOS' for the player on this PC, or None (spectating)."""
    ap = data.get("activePlayer") or {}
    me = ap.get("riotId") or ap.get("summonerName")
    if not me:
        return None
    for p in data.get("allPlayers") or []:
        if me in (p.get("riotId"), p.get("summonerName")):
            return p.get("team")
    return None


def roster(data) -> Tuple:
    return tuple(sorted((p.get("team") or "", p.get("championName") or "",
                         p.get("riotId") or p.get("summonerName") or "")
                        for p in data.get("allPlayers") or []))


def game_result(data) -> Optional[str]:
    for e in reversed((data.get("events") or {}).get("Events") or []):
        if e.get("EventName") == "GameEnd":
            return e.get("Result") or "Ended"
    return None


class Engine:
    """Feed it Polls; it returns the Update to display.

    `resolve` is called once per new game and returns (model, item_db). The
    default in `resolver.py` loads the bundled model; Phase 8 swaps in one that
    picks by patch."""

    def __init__(self, resolve: Callable[[], Tuple[object, object]], tau_s: float = 5.0):
        self.resolve = resolve
        self.tau_s = tau_s
        self.game: Optional[Game] = None
        self.last_game: Optional[Game] = None
        self._games = 0
        self.last = Update(NO_GAME, "waiting for the game client")

    # ------------------------------------------------------------------ public

    def feed(self, poll: Poll) -> Update:
        if poll.kind == ERROR:
            return self.last                      # hold steady through a hitch
        if poll.kind == OFFLINE:
            self._client_gone()
            up = Update(NO_GAME, "waiting for the game client")
            if self.game is not None:             # mid-game: maybe a reconnect
                up = self._from_game(NO_GAME, "game client closed -- will resume on reconnect")
        elif poll.kind == LOADING:
            up = Update(LOADING_STATE, poll.detail or "loading",
                        game_id=self.game.id if self.game else 0)
        elif poll.kind == DATA:
            up = self._data(poll.data)
        else:
            up = self.last
        self.last = up
        return up

    def set_smoothing(self, tau_s: float):
        self.tau_s = tau_s
        if self.game:
            self.game.smoother.tau_s = tau_s

    # ------------------------------------------------------------------ internals

    def _client_gone(self):
        if self.game is not None and self.game.result is not None:
            self.last_game, self.game = self.game, None

    def _data(self, data) -> Update:
        ok, why = live.game_check(data)
        if not ok:
            if self.game is not None:
                self.last_game, self.game = self.game, None
            return Update(UNSUPPORTED, why)

        t = float((data.get("gameData") or {}).get("gameTime") or 0.0)
        g = self.game
        if g is None or g.roster != roster(data) or t < g.last_t - NEW_GAME_IF_CLOCK_BACK_S:
            if g is not None:
                self.last_game = g
            g = self.game = self._new_game(data)

        if g.result is not None:                  # already over; the API lingers a moment
            return self._from_game(ENDED)

        try:
            x = live.extract(data, g.db, g.unknown_events)
            p_blue = float(g.model.predict_one(x))
        except Exception as exc:                  # keep the last good number up
            g.bad_snapshots += 1
            return replace(self.last, detail=f"skipped a snapshot ({type(exc).__name__})")

        raw = p_blue if g.team != CHAOS else 1.0 - p_blue
        shown = g.smoother.update(t, raw)
        g.curve.append((t, raw, shown))
        g.last_t = t

        result = game_result(data)
        if result is not None:
            g.result = result
            return self._from_game(ENDED, p_blue=p_blue)
        return self._from_game(IN_GAME, p_blue=p_blue)

    def _new_game(self, data) -> Game:
        model, db = self.resolve()
        self._games += 1
        return Game(id=self._games, roster=roster(data), model=model, db=db,
                    team=my_team(data), smoother=LogitEMA(self.tau_s))

    def _from_game(self, state, detail="", p_blue=None) -> Update:
        g = self.game
        t, raw, shown = g.curve[-1] if g.curve else (None, None, None)
        if p_blue is None and raw is not None:
            p_blue = raw if g.team != CHAOS else 1.0 - raw
        return Update(state, detail, game_id=g.id, game_time=t, team=g.team,
                      p_blue=p_blue, p_mine_raw=raw, p_mine=shown, result=g.result,
                      model_name=getattr(g.model, "name", None))
