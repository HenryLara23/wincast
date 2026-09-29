"""Model store (choosing, importing, pinning) and the GitHub update check,
against a fake GitHub on localhost."""

import hashlib
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from tests import ROOT

from wincast.models import BUNDLED, ModelStore
from wincast.resolver import StoreResolver
from wincast import updates

FALLBACK = ROOT / "src" / "wincast" / "resources" / "models" / "fallback.json"
BASE = json.loads(FALLBACK.read_text("utf-8"))


def variant(name, patch, created, **extra):
    b = dict(BASE, name=name, patch=patch, created_utc=created)
    b.update(extra)
    return json.dumps(b).encode("utf-8")


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ModelStore(Path(self.tmp.name) / "models")

    def tearDown(self):
        self.tmp.cleanup()

    def test_bundled_only(self):
        ms = self.store.models()
        self.assertEqual([m.source for m in ms], [BUNDLED])
        self.assertTrue(ms[0].ok)
        self.assertEqual(self.store.choose().source, BUNDLED)

    def test_newest_compatible_wins_and_pin_overrides(self):
        self.store.install_bytes(variant("m_16.19_a", "16.19", "2026-10-01T00:00:00Z"))
        self.store.install_bytes(variant("m_16.19_b", "16.19", "2026-10-03T00:00:00Z"))
        self.store.install_bytes(variant("m_16.17", "16.17", "2026-09-01T00:00:00Z"))
        self.assertEqual(self.store.choose().name, "m_16.19_b")
        self.assertEqual(self.store.choose(pinned="m_16.19_a").name, "m_16.19_a")
        self.assertEqual(self.store.choose(pinned="gone").name, "m_16.19_b")   # pin missing
        self.assertEqual(self.store.choose(pinned=BASE["name"]).source, BUNDLED)

    def test_older_download_loses_to_bundled(self):
        self.store.install_bytes(variant("old", "16.10", "2026-01-01T00:00:00Z"))
        self.assertEqual(self.store.choose().source, BUNDLED)

    def test_refuses_bad_files_with_a_reason(self):
        with self.assertRaisesRegex(ValueError, "not JSON"):
            self.store.install_bytes(b"<html>")
        with self.assertRaisesRegex(ValueError, "feature set"):
            self.store.install_bytes(variant("x", "16.19", "z", feature_set_version="9.9.9"))
        with self.assertRaisesRegex(ValueError, "postgame"):
            self.store.install_bytes(variant("x", "16.19", "z", kind="postgame"))
        self.assertEqual(len(self.store.models()), 1)

    def test_broken_file_in_folder_is_listed_not_chosen(self):
        self.store.folder.mkdir(parents=True)
        (self.store.folder / "junk.json").write_text("{}")
        ms = self.store.models()
        self.assertEqual(len(ms), 2)
        self.assertFalse(ms[1].ok)
        self.assertTrue(ms[1].error)
        self.assertEqual(self.store.choose().source, BUNDLED)

    def test_remove(self):
        info = self.store.install_bytes(variant("m", "16.19", "2026-10-01T00:00:00Z"))
        self.store.remove(info)
        self.assertEqual(len(self.store.models()), 1)
        with self.assertRaises(ValueError):
            self.store.remove(self.store.models()[0])

    def test_resolver_picks_per_game_and_caches(self):
        pin = [""]
        res = StoreResolver(self.store, pinned=lambda: pin[0])
        m1, db1 = res()
        self.assertEqual(res.current.source, BUNDLED)
        self.store.install_bytes(variant("m_16.19", "16.19", "2026-10-01T00:00:00Z"))
        m2, db2 = res()                           # the next game sees the new model
        self.assertEqual(m2.name, "m_16.19")
        self.assertIs(res()[0], m2)               # cached
        pin[0] = BASE["name"]
        self.assertIs(res()[0], m1)


# --------------------------------------------------------------------------- fake GitHub

class FakeGitHub(BaseHTTPRequestHandler):
    routes = {}

    def do_GET(self):
        path = self.path.split("?")[0]
        status, body, ctype = self.routes.get(path, (404, b"{}", "application/json"))
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class TestUpdates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), FakeGitHub)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        from lolwp.features import spec
        fs = spec.FEATURE_SET_VERSION
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ModelStore(Path(self.tmp.name) / "models")
        self.day1 = variant("lolwp_16.19_20261002-0605", "16.19", "2026-10-02T06:05:00Z")
        self.day2 = variant("lolwp_16.19_20261003-0105", "16.19", "2026-10-03T01:05:00Z")
        self.f1 = f"model-16.19-20261002-0605.fs{fs}.json"
        self.f2 = f"model-16.19-20261003-0105.fs{fs}.json"
        sums = "".join(f"{hashlib.sha256(d).hexdigest()}  {n}\n"
                       for n, d in ((self.f1, self.day1), (self.f2, self.day2))).encode()

        def rel(tag, name, assets):
            return {"tag_name": tag, "name": name, "draft": False,
                    "html_url": f"{self.base}/r/{tag}",
                    "assets": [{"name": n, "browser_download_url": f"{self.base}/dl/{tag}/{n}"}
                               for n in assets]}
        releases = [
            rel("v16.19", "Wincast 16.19", ["Wincast-16.19-win64.zip"]),          # an app release
            rel("models-16.19", "Models for patch 16.19", [self.f1, self.f2, "SHA256SUMS"]),
            rel("models-16.18", "Models for patch 16.18",
                [f"model-16.18-20260927-0157.fs{fs}.json", "SHA256SUMS"]),
            rel("models-16.20", "Models for patch 16.20",                            # other app version
                ["model-16.20-20261010-0000.fs9.9.9.json", "SHA256SUMS"]),
        ]
        FakeGitHub.routes = {
            "/repos/me/wincast/releases": (200, json.dumps(releases).encode(), "application/json"),
            f"/dl/models-16.19/{self.f2}": (200, self.day2, "application/octet-stream"),
            "/dl/models-16.19/SHA256SUMS": (200, sums, "text/plain"),
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_check_picks_the_newest_day_of_the_newest_patch_for_this_feature_set(self):
        rel = updates.check("me/wincast", api=self.base)
        self.assertEqual((rel.tag, rel.name, rel.patch, rel.stamp),
                         ("models-16.19", self.f2, "16.19", "20261003-0105"))
        self.assertTrue(updates.is_newer(rel, self.store))

    def test_download_verifies_and_installs(self):
        rel = updates.check("me/wincast", api=self.base)
        info = updates.download(rel, self.store)
        self.assertEqual(info.name, "lolwp_16.19_20261003-0105")
        self.assertEqual(self.store.choose().name, info.name)
        self.assertFalse(updates.is_newer(rel, self.store))     # installed now

    def test_older_day_installed_by_hand_still_offers_the_newer_one(self):
        self.store.install_bytes(self.day1)
        rel = updates.check("me/wincast", api=self.base)
        self.assertTrue(updates.is_newer(rel, self.store))

    def test_checksum_mismatch_is_refused(self):
        rel = updates.check("me/wincast", api=self.base)
        FakeGitHub.routes[f"/dl/models-16.19/{self.f2}"] = (200, self.day2 + b" ", "x")
        with self.assertRaisesRegex(updates.UpdateError, "checksum mismatch"):
            updates.download(rel, self.store)
        self.assertEqual(len(self.store.models()), 1)

    def test_no_repo_and_offline(self):
        with self.assertRaisesRegex(updates.UpdateError, "no such repository"):
            updates.check("nobody/nothing", api=self.base)
        with self.assertRaisesRegex(updates.UpdateError, "couldn't reach"):
            updates.check("me/wincast", api="http://127.0.0.1:9")

    def test_nothing_for_this_feature_set(self):
        FakeGitHub.routes["/repos/me/wincast/releases"] = (200, b"[]", "application/json")
        self.assertIsNone(updates.check("me/wincast", api=self.base))


if __name__ == "__main__":
    unittest.main()
