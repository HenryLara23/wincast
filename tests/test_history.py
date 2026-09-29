"""Saving finished games: when the engine hands one over, what the record holds
(and doesn't: no names), and the store on disk."""

import json
import tempfile
import unittest
from pathlib import Path

from tests import ROOT
from tests.test_session import renamed, with_game_end

from wincast.client import OFFLINE, Poll
from wincast.history import HistoryStore, events_for_history, record_from_game
from wincast.resolver import FixedResolver
from wincast.session import Engine

GOLDEN = json.loads((ROOT / "tests" / "fixtures" / "golden_game.json").read_text("utf-8"))
SNAPS = GOLDEN["snapshots"]
NAMES = {p["riotId"] for p in SNAPS[0]["allPlayers"]} | \
        {p.get("riotIdGameName") for p in SNAPS[0]["allPlayers"]}


class TestEngineHandsOverGames(unittest.TestCase):
    def setUp(self):
        self.eng = Engine(FixedResolver(), tau_s=0)

    def feed(self, *snaps):
        for s in snaps:
            self.eng.feed(Poll.of(s))

    def test_ended_game_once(self):
        self.feed(*SNAPS[:6], with_game_end(SNAPS[5], "Win"))
        done = self.eng.pop_finished()
        self.assertEqual([g.result for g in done], ["Win"])
        self.feed(with_game_end(SNAPS[5], "Win"))           # API lingers
        self.eng.feed(Poll(OFFLINE))
        self.assertEqual(self.eng.pop_finished(), [])
        self.assertEqual(self.eng.flush(), [])

    def test_short_abandoned_game_is_dropped_long_one_kept(self):
        self.feed(SNAPS[0])                                   # t ~ 0 s
        self.feed(renamed(SNAPS[0], "x"))                    # a different game starts
        self.assertEqual(self.eng.pop_finished(), [])
        self.feed(*SNAPS[1:4])                               # this one reaches ~4.5 min
        self.feed(renamed(SNAPS[0], "y"))
        done = self.eng.pop_finished()
        self.assertEqual(len(done), 1)
        self.assertIsNone(done[0].result)

    def test_flush_hands_over_the_game_in_progress(self):
        self.feed(*SNAPS[:5])
        done = self.eng.flush()
        self.assertEqual(len(done), 1)
        self.assertEqual(self.eng.flush(), [])


class TestRecord(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        eng = Engine(FixedResolver(), tau_s=5)
        for s in SNAPS:
            eng.feed(Poll.of(s))
        eng.feed(Poll.of(with_game_end(SNAPS[-1], "Win")))
        cls.game = eng.pop_finished()[0]
        cls.rec = record_from_game(cls.game)

    def test_fields(self):
        r = self.rec
        self.assertEqual(r["result"], "Win")
        self.assertIn(r["team"], ("ORDER", "CHAOS"))
        self.assertTrue(r["champion"])
        self.assertEqual(len(r["curve"]), len(SNAPS) + 1)
        self.assertAlmostEqual(r["final"], r["curve"][-1][2])
        self.assertLessEqual(r["low"], r["final"])
        self.assertGreater(r["duration_s"], 1000)
        self.assertEqual(r["model"], self.game.model.name)

    def test_no_names_anywhere(self):
        text = json.dumps(self.rec)
        for name in NAMES:
            if name:
                self.assertNotIn(name, text)

    def test_events_have_teams(self):
        kinds = {e["kind"] for e in self.rec["events"]}
        self.assertIn("Kill", kinds)
        self.assertIn("Tower", kinds)
        with_team = [e for e in self.rec["events"] if e["team"] in ("ORDER", "CHAOS")]
        self.assertGreater(len(with_team), 0.9 * len(self.rec["events"]))

    def test_structure_owner_both_spellings(self):
        ev = [{"EventName": "TurretKilled", "EventTime": 600, "TurretKilled": "Turret_TOrder_L1_P3_x"},
              {"EventName": "TurretKilled", "EventTime": 700, "TurretKilled": "Turret_T2_R_03_A"},
              {"EventName": "InhibKilled", "EventTime": 900, "InhibKilled": "Barracks_T1_C1"}]
        out = events_for_history(ev, {})
        self.assertEqual([e["team"] for e in out], ["CHAOS", "ORDER", "CHAOS"])


class TestStore(unittest.TestCase):
    def test_roundtrip_listing_and_delete(self):
        with tempfile.TemporaryDirectory() as d:
            store = HistoryStore(Path(d) / "history")
            self.assertEqual(store.entries(), [])
            rec = {"schema": 1, "started_utc": "2026-09-28T14:03:00Z", "champion": "Kai'Sa",
                   "result": "Win", "curve": [[0, 0.5, 0.5]], "events": []}
            p1 = store.save(rec)
            p2 = store.save(dict(rec, started_utc="2026-09-28T15:00:00Z", result="Lose"))
            p3 = store.save(rec)                          # same start: no overwrite
            self.assertEqual(len({p1, p2, p3}), 3)
            self.assertIn("KaiSa", p1.name)
            (Path(d) / "history" / "junk.json").write_text("{not json")
            entries = store.entries()
            self.assertEqual(len(entries), 3)
            self.assertEqual(entries[0]["result"], "Lose")          # newest first
            self.assertNotIn("curve", entries[0])
            self.assertEqual(store.load(p1)["curve"], [[0, 0.5, 0.5]])
            store.delete(p1)
            self.assertEqual(len(store.entries()), 2)


if __name__ == "__main__":
    unittest.main()


class TestGameEndsUnseen(unittest.TestCase):
    """2026-09-28 bug: a game that ended without its GameEnd snapshot being scored
    was never saved while the app kept running."""

    def test_result_seen_even_if_the_final_snapshot_fails_to_score(self):
        import copy
        eng = Engine(FixedResolver(), tau_s=0)
        for s in SNAPS[:5]:
            eng.feed(Poll.of(s))
        end = with_game_end(SNAPS[4], "Win")
        end = copy.deepcopy(end)
        end["allPlayers"][0]["level"] = "?"                  # scoring blows up
        end["allPlayers"][0]["items"] = [{"itemID": "x", "count": None}]
        up = eng.feed(Poll.of(end))
        self.assertEqual((up.state, up.result), ("ended", "Win"))
        self.assertEqual([g.result for g in eng.pop_finished()], ["Win"])

    def test_client_gone_for_three_minutes_saves_the_game(self):
        now = [1000.0]
        eng = Engine(FixedResolver(), tau_s=0, clock=lambda: now[0])
        for s in SNAPS[:5]:
            eng.feed(Poll.of(s))
        eng.feed(Poll(OFFLINE))
        now[0] += 120
        eng.feed(Poll(OFFLINE))
        self.assertEqual(eng.pop_finished(), [])              # could still reconnect
        now[0] += 61
        eng.feed(Poll(OFFLINE))
        done = eng.pop_finished()
        self.assertEqual(len(done), 1)
        self.assertIsNone(done[0].result)
        self.assertIsNone(eng.game)

    def test_reconnect_inside_the_grace_keeps_the_game(self):
        now = [1000.0]
        eng = Engine(FixedResolver(), tau_s=0, clock=lambda: now[0])
        for s in SNAPS[:5]:
            eng.feed(Poll.of(s))
        gid = eng.game.id
        eng.feed(Poll(OFFLINE))
        now[0] += 150
        eng.feed(Poll.of(SNAPS[6]))
        now[0] += 150                                        # a later drop starts a new clock
        eng.feed(Poll(OFFLINE))
        self.assertEqual(eng.game.id, gid)
        self.assertEqual(eng.pop_finished(), [])


class TestExplorerPath(unittest.TestCase):
    def test_store_python_redirect(self):
        from wincast.paths import explorer_path
        with tempfile.TemporaryDirectory() as la:
            real = Path(la) / "Wincast" / "history"
            exe = r"C:\\Users\\H\\AppData\\Local\\Microsoft\\WindowsApps\\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\\python.exe"
            self.assertEqual(explorer_path(real, exe, la), real)       # redirect not there
            red = (Path(la) / "Packages" / "PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0"
                   / "LocalCache" / "Local" / "Wincast" / "history")
            red.mkdir(parents=True)
            self.assertEqual(explorer_path(real, exe, la), red)
            exe2 = r"C:\\Program Files\\WindowsApps\\PythonSoftwareFoundation.Python.3.11_3.11.2544.0_x64__qbz5n2kfra8p0\\python3.11.exe"
            self.assertEqual(explorer_path(real, exe2, la), red)
            self.assertEqual(explorer_path(real, r"C:\\Python311\\python.exe", la), real)


class TestEventsFallback(unittest.TestCase):
    """2026-09-28: the game stopped answering /allgamedata while the nexus blew up,
    then closed; GameEnd was never seen. /eventdata is the fallback."""

    def test_feed_events_ends_the_game(self):
        eng = Engine(FixedResolver(), tau_s=0)
        for s in SNAPS[:5]:
            eng.feed(Poll.of(s))
        self.assertIsNone(eng.feed_events([{"EventName": "ChampionKill", "EventTime": 900}]))
        up = eng.feed_events(SNAPS[4]["events"]["Events"] +
                             [{"EventName": "GameEnd", "EventTime": 1000, "Result": "Lose"}])
        self.assertEqual((up.state, up.result), ("ended", "Lose"))
        self.assertEqual([g.result for g in eng.pop_finished()], ["Lose"])
        self.assertIsNone(eng.feed_events([{"EventName": "GameEnd", "Result": "Lose"}]))  # once
