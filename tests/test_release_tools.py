"""The release workflow's two tools: fetch the previous patch's last model, and
prepare the source tree (version, built-in model, manifest, notes)."""

import hashlib
import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import HTTPServer
from pathlib import Path

from tests import ROOT
from tests.test_models import FakeGitHub, variant

sys.path.insert(0, str(ROOT / "tools"))
import fetch_release_model                      # noqa: E402
import prepare_release                          # noqa: E402

from lolwp.features import spec                 # noqa: E402


class TestPrepareRelease(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / "src", self.root / "src",
                        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_version_model_manifest_and_notes(self):
        m = self.root / "m.json"
        m.write_bytes(variant("lolwp_16.18_20261001-0000", "16.18", "2026-10-01T00:00:00Z"))
        prepare_release.main(["--version", "16.19", "--model", str(m)], root=self.root)
        init = (self.root / "src" / "wincast" / "__init__.py").read_text("utf-8")
        self.assertIn('__version__ = "16.19"', init)
        fb = self.root / "src" / "wincast" / "resources" / "models" / "fallback.json"
        self.assertEqual(fb.read_bytes(), m.read_bytes())
        man = json.loads((self.root / "src" / "lolwp" / "CORE_MANIFEST.json").read_text("utf-8"))
        self.assertEqual(man["fallback_model"]["name"], "lolwp_16.18_20261001-0000")
        self.assertEqual(man["fallback_model"]["sha256"], hashlib.sha256(fb.read_bytes()).hexdigest())
        self.assertTrue(man["ddragon_items"].startswith("16.18."))
        notes = (self.root / "build" / "release-notes.md").read_text("utf-8")
        self.assertIn("patch **16.19**", notes)
        self.assertIn("trained on patch 16.18", notes)
        self.assertIn("Wincast-16.19-win64.zip", notes)
        self.assertIn("Wincast-16.19-setup.exe", notes)
        self.assertIn("More info", notes)

    def test_refuses_a_bad_model_and_a_bad_version(self):
        m = self.root / "m.json"
        m.write_bytes(variant("x", "16.18", "z", feature_set_version="9.9.9"))
        with self.assertRaises(SystemExit):
            prepare_release.main(["--version", "16.19", "--model", str(m)], root=self.root)
        with self.assertRaises(SystemExit):
            prepare_release.main(["--version", "v16.19"], root=self.root)


class TestFetchReleaseModel(unittest.TestCase):
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
        fs = spec.FEATURE_SET_VERSION
        self.tmp = tempfile.TemporaryDirectory()
        self.old = variant("lolwp_16.18_20260930-1805", "16.18", "2026-09-30T18:05:00Z")
        f_early = f"model-16.18-20260929-0605.fs{fs}.json"
        f_last = f"model-16.18-20260930-1805.fs{fs}.json"
        f_new = f"model-16.19-20261002-0605.fs{fs}.json"
        sums = f"{hashlib.sha256(self.old).hexdigest()}  {f_last}\n".encode()
        rels = [
            {"tag_name": "models-16.19", "assets": [
                {"name": f_new, "browser_download_url": f"{self.base}/dl/new"}]},
            {"tag_name": "models-16.18", "assets": [
                {"name": f_early, "browser_download_url": f"{self.base}/dl/early"},
                {"name": f_last, "browser_download_url": f"{self.base}/dl/last"},
                {"name": "SHA256SUMS", "browser_download_url": f"{self.base}/dl/sums"}]},
        ]
        FakeGitHub.routes = {
            "/repos/me/wincast/releases": (200, json.dumps(rels).encode(), "application/json"),
            "/dl/last": (200, self.old, "application/json"),
            "/dl/sums": (200, sums, "text/plain"),
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_takes_the_last_model_of_the_previous_patch(self):
        out = Path(self.tmp.name) / "model.json"
        fetch_release_model.main(["--before", "16.19", "--out", str(out),
                                  "--repo", "me/wincast", "--api", self.base])
        self.assertEqual(out.read_bytes(), self.old)

    def test_first_release_ever_keeps_the_built_in_model(self):
        out = Path(self.tmp.name) / "model.json"
        fetch_release_model.main(["--before", "16.18", "--out", str(out),
                                  "--repo", "me/wincast", "--api", self.base])
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
