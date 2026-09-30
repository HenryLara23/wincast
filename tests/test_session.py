"""The engine, driven by the anonymised golden game plus hand-made edge cases."""

import copy
import json
import unittest

from tests import GOLDEN_MODEL, ROOT

from wincast.client import DATA, ERROR, LOADING, OFFLINE, Poll
from wincast.resolver import FixedResolver
from wincast.session import (ENDED, IN_GAME, LOADING_STATE, NO_GAME, UNSUPPORTED,
                             Engine, my_team)

FX = ROOT / "tests" / "fixtures"


def with_game_end(snap, result="Win", dt=5.0):
    s = copy.deepcopy(snap)
    t = s["gameData"]["gameTime"] + dt
    s["gameData"]["gameTime"] = t
    s["events"]["Events"].append({"EventID": 9999, "EventName": "GameEnd",
                                  "EventTime": t, "Result": result})
    return s


def renamed(snap, suffix):
    s = copy.deepcopy(snap)
    for p in s["allPlayers"]:
        p["riotId"] = p["riotId"].replace("#NA1", f"{suffix}#NA1")
        p["summonerName"] = p.get("summonerName", "").replace("#NA1", f"{suffix}#NA1")
    ap = s.get("activePlayer") or {}
    for k in ("riotId", "summonerName"):
        if ap.get(k):
            ap[k] = ap[k].replace("#NA1", f"{suffix}#NA1")
    return s


class CountingResolver:
    def __init__(self):
        self.inner = FixedResolver(GOLDEN_MODEL)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.inner()


class TestEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g = json.loads((FX / "golden_game.json").read_text("utf-8"))
        cls.snaps, cls.expected = g["snapshots"], g["expected"]
        cls.aram = json.loads((FX / "sample_howling_abyss.json").read_text("utf-8"))

    def setUp(self):
        self.resolver = CountingResolver()
        self.engine = Engine(self.resolver, tau_s=0)      # tau 0: shown == raw

    def feed(self, data):
        return self.engine.feed(Poll.of(data))

    def test_states_before_a_game(self):
        self.assertEqual(self.engine.feed(Poll(OFFLINE)).state, NO_GAME)
        self.assertEqual(self.engine.feed(Poll(LOADING, detail="HTTP 404")).state, LOADING_STATE)
        self.assertEqual(self.feed({"gameData": {}}).state, LOADING_STATE)   # half-formed payload
        up = self.feed(self.aram)
        self.assertEqual(up.state, UNSUPPORTED)
        self.assertIn("Summoner's Rift", up.detail)
        self.assertEqual(self.resolver.calls, 0)

    def test_scores_match_the_golden_numbers_from_the_players_side(self):
        team = my_team(self.snaps[0])
        for snap, exp in zip(self.snaps, self.expected):
            up = self.feed(snap)
            self.assertEqual(up.state, IN_GAME)
            want = exp["p_blue"] if team == "ORDER" else 1 - exp["p_blue"]
            self.assertAlmostEqual(up.p_mine_raw, want, places=9)
            self.assertAlmostEqual(up.p_mine, want, places=9)
            self.assertAlmostEqual(up.p_blue, exp["p_blue"], places=9)
        self.assertEqual(self.resolver.calls, 1)
        self.assertEqual(len(self.engine.game.curve), len(self.snaps))

    def test_game_end_then_client_closes(self):
        for s in self.snaps[:5]:
            self.feed(s)
        up = self.feed(with_game_end(self.snaps[4], "Lose"))
        self.assertEqual((up.state, up.result), (ENDED, "Lose"))
        self.assertEqual(self.feed(with_game_end(self.snaps[4], "Lose")).state, ENDED)  # lingering API
        up = self.engine.feed(Poll(OFFLINE))
        self.assertEqual(up.state, NO_GAME)
        self.assertIsNone(self.engine.game)
        self.assertEqual(self.engine.last_game.result, "Lose")

    def test_reconnect_resumes_the_same_game(self):
        for s in self.snaps[:4]:
            self.feed(s)
        gid = self.engine.game.id
        self.assertEqual(self.engine.feed(Poll(OFFLINE)).state, NO_GAME)
        self.assertEqual(self.engine.feed(Poll(LOADING)).state, LOADING_STATE)
        up = self.feed(self.snaps[6])
        self.assertEqual((up.state, up.game_id), (IN_GAME, gid))
        self.assertEqual(self.resolver.calls, 1)           # model not re-resolved
        self.assertEqual(len(self.engine.game.curve), 5)

    def test_new_roster_is_a_new_game_with_a_fresh_model_resolve(self):
        self.feed(self.snaps[3])
        first = self.engine.game.id
        up = self.feed(renamed(self.snaps[0], "x"))
        self.assertNotEqual(up.game_id, first)
        self.assertEqual(self.resolver.calls, 2)
        self.assertEqual(self.engine.last_game.id, first)

    def test_disguises_do_not_split_the_game(self):
        """Neeko's passive makes the API report her champion as whoever she's disguised
        as (an ally, a minion). Seen live 2026-09-30: one game saved as 38."""
        disguises = ["Ezreal", "Blue Caster Minion", "Neeko", "Blitzcrank", "Blue Melee Minion"]
        self.feed(self.snaps[0])
        gid, champ = self.engine.game.id, self.engine.game.champion
        me = self.snaps[0]["activePlayer"]["riotId"]
        for i, s in enumerate(self.snaps[1:6]):
            s = copy.deepcopy(s)
            for p in s["allPlayers"]:
                if p["riotId"] == me:                           # my champion "changes"
                    p["championName"] = disguises[i]
                elif p["team"] != s["allPlayers"][0]["team"]:   # so does an enemy's
                    p["championName"] = disguises[-1 - i]
            up = self.feed(s)
            self.assertEqual((up.state, up.game_id), (IN_GAME, gid))
        self.assertEqual(self.resolver.calls, 1)
        self.assertEqual(self.engine.pop_finished(), [])
        self.assertEqual(self.engine.game.champion, champ)      # history keeps the real one
        self.assertEqual(len(self.engine.game.curve), 6)

    def test_client_closing_without_game_end_is_logged_with_evidence(self):
        """Two losses on 2026-09-30 were saved with no result: the game went from
        answering to gone with no GameEnd. The log must say what the last seconds held."""
        now = [100.0]
        eng = Engine(self.resolver, tau_s=0, clock=lambda: now[0])
        for s in self.snaps[:3]:
            eng.feed(Poll.of(s))
        now[0] += 4.5
        with self.assertLogs("wincast.session", "INFO") as logs:
            eng.feed(Poll(OFFLINE, detail="no game client"))
            eng.feed(Poll(OFFLINE, detail="no game client"))         # said once, not per poll
        text = "\n".join(logs.output)
        self.assertEqual(text.count("without a GameEnd"), 1)
        self.assertIn("4.5 s after the last snapshot", text)
        self.assertIn("last events:", text)

    def test_skipped_snapshots_are_logged(self):
        bad = copy.deepcopy(self.snaps[2])
        bad["allPlayers"][0]["items"] = [{"itemID": "not-a-number", "count": None}]
        bad["allPlayers"][0]["level"] = "?"                           # can't be scored
        self.feed(self.snaps[1])
        with self.assertLogs("wincast.session", "WARNING") as logs:
            self.feed(bad)
        self.assertIn("skipped a snapshot", logs.output[0])

    def test_clock_going_back_is_a_new_game(self):
        self.feed(self.snaps[10])
        first = self.engine.game.id
        self.assertNotEqual(self.feed(self.snaps[0]).game_id, first)

    def test_model_is_never_swapped_mid_game(self):
        self.feed(self.snaps[0])
        model = self.engine.game.model
        self.engine.resolve = lambda: (_ for _ in ()).throw(AssertionError("resolved mid-game"))
        for s in self.snaps[1:6]:
            self.feed(s)
        self.assertIs(self.engine.game.model, model)

    def test_transient_error_holds_the_last_update(self):
        up = self.feed(self.snaps[5])
        self.assertIs(self.engine.feed(Poll(ERROR, detail="read timeout")), up)

    def test_bad_snapshot_keeps_the_last_number(self):
        good = self.feed(self.snaps[5])
        bad = copy.deepcopy(self.snaps[6])
        bad["allPlayers"][0]["items"] = [{"itemID": "not-a-number", "count": None}]
        bad["allPlayers"][0]["level"] = "?"
        up = self.feed(bad)
        self.assertEqual(up.state, IN_GAME)
        self.assertEqual(up.p_mine, good.p_mine)
        self.assertEqual(self.engine.game.bad_snapshots, 1)
        self.assertEqual(self.feed(self.snaps[7]).state, IN_GAME)

    def test_smoothing_lags_raw_but_follows(self):
        eng = Engine(FixedResolver(GOLDEN_MODEL), tau_s=60)
        ups = [eng.feed(Poll.of(s)) for s in self.snaps]
        self.assertTrue(any(abs(u.p_mine - u.p_mine_raw) > 1e-3 for u in ups[1:]))
        self.assertAlmostEqual(ups[0].p_mine, ups[0].p_mine_raw)

    def test_spectator_sees_blue_side(self):
        s = copy.deepcopy(self.snaps[8])
        s["activePlayer"] = {}
        up = self.feed(s)
        self.assertIsNone(up.team)
        self.assertAlmostEqual(up.p_mine_raw, self.expected[8]["p_blue"], places=9)


if __name__ == "__main__":
    unittest.main()
