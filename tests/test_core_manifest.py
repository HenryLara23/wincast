"""The vendored scoring code must be exactly what the model repo shipped."""

import hashlib
import json
import unittest

from tests import ROOT

VENDOR = ROOT / "src" / "lolwp"
RES = ROOT / "src" / "wincast" / "resources"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestCoreManifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.man = json.loads((VENDOR / "CORE_MANIFEST.json").read_text("utf-8"))

    def test_vendored_files_untouched(self):
        for rel, want in self.man["files"].items():
            with self.subTest(file=rel):
                self.assertEqual(sha256(VENDOR / rel), want,
                                 f"src/lolwp/{rel} was edited. Fix it in the model repo "
                                 "and re-run tools/sync_core.py instead.")

    def test_feature_set_version_matches_code(self):
        from lolwp.features import spec
        self.assertEqual(self.man["feature_set_version"], spec.FEATURE_SET_VERSION)

    def test_fallback_model_is_the_synced_one_and_loads(self):
        path = RES / "models" / "fallback.json"
        self.assertEqual(sha256(path), self.man["fallback_model"]["sha256"])
        from lolwp.model.bundle import Model
        m = Model.load(str(path))                 # refuses a feature-set mismatch
        self.assertEqual(m.name, self.man["fallback_model"]["name"])

    def test_item_prices_bundled_for_the_model_patch(self):
        v = self.man["ddragon_items"]
        self.assertTrue((RES / "ddragon" / f"item-{v}-en_US.json").exists())
        self.assertTrue(v.startswith(self.man["fallback_model"]["patch"] + "."))


if __name__ == "__main__":
    unittest.main()
