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
  * A game is identified by its roster (team and name for all ten). If the
    client drops and comes back with the same roster -- a crash and reconnect --
    it is the SAME game: curve, smoothing and model carry on. Champions are NOT
    part of it: Neeko's disguise makes the API report her as whoever she copies
    (an ally, a minion), which once split a game into 38.
  * A transient error (timeout during a hitch) changes nothing.
  * One bad snapshot never ends a game: the last good number stays up.
  * Spectated games and replays (no player on this PC) are scored for the overlay
    but never handed to the history.
"""

from __future__ import annotations

from collections import Counter
import logging
import time
from dataclasses import dataclass, field, replace
from typing import Callable, List, Optional, Tuple

from .client import DATA, ERROR, LOADING, OFFLINE, Poll
from .smoothing import LogitEMA

from lolwp.features import live_features as live

log = logging.getLogger(__name__)

NO_GAME, LOADING_STATE, UNSUPPORTED, IN_GAME, ENDED = (
    "no_game", "loading", "unsupported", "in_game", "ended")

ORDER, CHAOS = "ORDER", "CHAOS"
NEW_GAME_IF_CLOCK_BACK_S = 10.0
MIN_UNFINISHED_S = 60.0          # an unfinished game shorter than this isn't worth keeping
GONE_GRACE_S = 180.0             # client gone this long (wall clock) -> the game is over


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
    champion: Optional[str] = None    # the champion of the player on this PC


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
    champion: Optional[str] = None
    game_mode: str = ""
    started_wall: float = field(default_factory=time.time)
    events: list = field(default_factory=list)         # the live API's event list, latest
    last_data_clock: float = 0.0                        # engine clock at the last snapshot
    gone_logged: bool = False                           # "closed without a GameEnd" said once
    name_team: dict = field(default_factory=dict)      # in memory only: never saved
    finished: bool = False                              # handed to history already


def my_player(data) -> Optional[dict]:
    """The allPlayers entry for the player on this PC, or None (spectating)."""
    ap = data.get("activePlayer") or {}
    me = ap.get("riotId") or ap.get("summonerName")
    if not me:
        return None
    for p in data.get("allPlayers") or []:
        if me in (p.get("riotId"), p.get("summonerName")):
            return p
    return None


def my_team(data) -> Optional[str]:
    """'ORDER' / 'CHAOS' for the player on this PC, or None (spectating)."""
    p = my_player(data)
    return p.get("team") if p else None


def roster(data) -> Tuple:
    """Who is playing, on which side. Not champions: those can change mid-game
    (Neeko's disguise); a same-roster rematch is caught by the clock going back."""
    return tuple(sorted((p.get("team") or "", p.get("riotId") or p.get("summonerName") or "")
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

    def __init__(self, resolve: Callable[[], Tuple[object, object]], tau_s: float = 5.0,
                 clock=time.monotonic):
        self.resolve = resolve
        self.clock = clock
        self._gone_since = None           # when the client went away mid-game
        self.tau_s = tau_s
        self.game: Optional[Game] = None
        self.last_game: Optional[Game] = None
        self._games = 0
        self._finished: List[Game] = []
        self.last = Update(NO_GAME, "waiting for the game client")

    # ------------------------------------------------------------------ public

    def feed(self, poll: Poll) -> Update:
        if poll.kind == ERROR:
            return self.last                      # hold steady through a hitch
        if poll.kind in (OFFLINE, LOADING):
            self._check_gone()
        if poll.kind == OFFLINE:
            self._client_gone()
            up = Update(NO_GAME, "waiting for the game client")
            if self.game is not None:             # mid-game: maybe a reconnect
                up = self._from_game(NO_GAME, "game client closed -- will resume on reconnect")
        elif poll.kind == LOADING:
            up = Update(LOADING_STATE, poll.detail or "loading",
                        game_id=self.game.id if self.game else 0)
        elif poll.kind == DATA:
            self._gone_since = None
            up = self._data(poll.data)
        else:
            up = self.last
        self.last = up
        return up

    def feed_events(self, events) -> Optional[Update]:
        """A bare event list (client.events()) for the open game: catches GameEnd
        when full snapshots are failing. Returns the ENDED update if it found one."""
        g = self.game
        if g is None or g.result is not None or not events:
            return None
        result = game_result({"events": {"Events": events}})
        if result is None:
            return None
        g.events = events
        g.result = result
        self._retire(g)
        self.last = self._from_game(ENDED)
        return self.last

    def pop_finished(self) -> List[Game]:
        """Games that are over (ended, or abandoned after at least a minute), each
        returned exactly once, for the history."""
        out, self._finished = self._finished, []
        return out

    def flush(self) -> List[Game]:
        """App is quitting: hand over the game in progress too."""
        if self.game is not None:
            self._retire(self.game)
        return self.pop_finished()

    def _retire(self, g: Game):
        if g.finished or not g.curve:
            return
        if g.team is None:                        # spectating or a replay: not your game,
            g.finished = True                     # and its result is blue's, not yours
            return
        if g.result is not None or g.last_t >= MIN_UNFINISHED_S:
            g.finished = True
            self._finished.append(g)

    def set_smoothing(self, tau_s: float):
        self.tau_s = tau_s
        if self.game:
            self.game.smoother.tau_s = tau_s

    # ------------------------------------------------------------------ internals

    def _check_gone(self):
        """No data for GONE_GRACE_S while a game is open: it ended without us
        seeing GameEnd (the client closed fast), or a reconnect never came. Hand it
        to the history now instead of waiting for the next game or app exit."""
        if self.game is None or self.game.result is not None:
            self._gone_since = None
            return
        now = self.clock()
        if self._gone_since is None:
            self._gone_since = now
        elif now - self._gone_since >= GONE_GRACE_S:
            self._retire(self.game)
            self.last_game, self.game = self.game, None
            self._gone_since = None

    def _client_gone(self):
        g = self.game
        if g is not None and g.result is not None:
            self.last_game, self.game = g, None
        elif g is not None and not g.gone_logged:
            # Evidence for games that end with no result (seen twice on 2026-09-30):
            # did the client close right after the last snapshot, or were the final
            # snapshots failing to score?
            g.gone_logged = True
            tail = [f"{e.get('EventName')}@{float(e.get('EventTime') or 0):.0f}"
                    for e in g.events[-4:]]
            log.info("game %d: client closed without a GameEnd, %.1f s after the last snapshot "
                     "(game time %.0f s, %d skipped snapshots); last events: %s",
                     g.id, self.clock() - g.last_data_clock, g.last_t, g.bad_snapshots,
                     ", ".join(tail) or "none")

    def _data(self, data) -> Update:
        ok, why = live.game_check(data)
        if not ok:
            if self.game is not None:
                self._retire(self.game)
                self.last_game, self.game = self.game, None
            return Update(UNSUPPORTED, why)

        t = float((data.get("gameData") or {}).get("gameTime") or 0.0)
        g = self.game
        if g is None or g.roster != roster(data) or t < g.last_t - NEW_GAME_IF_CLOCK_BACK_S:
            if g is not None:
                self._retire(g)
                self.last_game = g
            g = self.game = self._new_game(data)

        if g.result is not None:                  # already over; the API lingers a moment
            return self._from_game(ENDED)

        g.events = (data.get("events") or {}).get("Events") or []
        g.last_data_clock = self.clock()
        g.gone_logged = False                     # it answered again (a reconnect)
        result = game_result(data)                # before scoring: a snapshot that fails
        try:                                      # to score must not hide the result
            x = live.extract(data, g.db, g.unknown_events)
            p_blue = float(g.model.predict_one(x))
        except Exception as exc:                  # keep the last good number up
            g.bad_snapshots += 1
            if g.bad_snapshots <= 5 or result is not None:
                log.warning("game %d: skipped a snapshot at game time %.0f s (%s: %s)%s",
                            g.id, t, type(exc).__name__, exc,
                            " -- it had the GameEnd" if result is not None else "")
            if result is not None:
                g.result = result
                self._retire(g)
                return self._from_game(ENDED)
            return replace(self.last, detail=f"skipped a snapshot ({type(exc).__name__})")

        raw = p_blue if g.team != CHAOS else 1.0 - p_blue
        shown = g.smoother.update(t, raw)
        g.curve.append((t, raw, shown))
        g.last_t = t

        if result is not None:
            g.result = result
            self._retire(g)
            return self._from_game(ENDED, p_blue=p_blue)
        return self._from_game(IN_GAME, p_blue=p_blue)

    def _new_game(self, data) -> Game:
        model, db = self.resolve()
        self._games += 1
        me = my_player(data) or {}
        name_team = {}
        for p in data.get("allPlayers") or []:
            for key in ("riotId", "summonerName", "riotIdGameName"):
                if p.get(key):
                    name_team[p[key]] = p.get("team")
        return Game(id=self._games, roster=roster(data), model=model, db=db,
                    team=me.get("team"), smoother=LogitEMA(self.tau_s),
                    champion=me.get("championName"),
                    game_mode=(data.get("gameData") or {}).get("gameMode") or "",
                    name_team=name_team)

    def _from_game(self, state, detail="", p_blue=None) -> Update:
        g = self.game
        t, raw, shown = g.curve[-1] if g.curve else (None, None, None)
        if p_blue is None and raw is not None:
            p_blue = raw if g.team != CHAOS else 1.0 - raw
        return Update(state, detail, game_id=g.id, game_time=t, team=g.team,
                      p_blue=p_blue, p_mine_raw=raw, p_mine=shown, result=g.result,
                      model_name=getattr(g.model, "name", None), champion=g.champion)
