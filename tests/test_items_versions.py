"""A new patch must not crash the retrains (2026-09-27, patch 16.19).

The Data Dragon version list is cached on first use. A patch missing from the
cached list must trigger one re-download; a patch Data Dragon really hasn't
published yet must raise NoReleaseYet, which both retrains turn into a skipped run.
"""

import json
import os
import tempfile
import unittest

from lolwp.store import items_db


class TestNewPatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._dir, self._get = items_db.CACHE_DIR, items_db._get_json
        items_db.CACHE_DIR = self.tmp.name
        with open(os.path.join(self.tmp.name, "versions.json"), "w") as fh:
            json.dump(["16.18.1", "16.17.1"], fh)          # cached before 16.19 existed
        self.fetches = 0

    def tearDown(self):
        items_db.CACHE_DIR, items_db._get_json = self._dir, self._get
        self.tmp.cleanup()

    def online(self, versions):
        def get(url, timeout=30):
            self.fetches += 1
            return versions
        items_db._get_json = get

    def test_known_patch_uses_the_cache(self):
        self.online(["16.19.1", "16.18.1"])
        self.assertEqual(items_db.resolve_version("16.18.702.1"), "16.18.1")
        self.assertEqual(self.fetches, 0)

    def test_new_patch_refreshes_the_list_once(self):
        self.online(["16.19.1", "16.18.1", "16.17.1"])
        self.assertEqual(items_db.resolve_version("16.19.1"), "16.19.1")
        self.assertEqual(self.fetches, 1)
        self.assertEqual(items_db.resolve_version("16.19.1"), "16.19.1")   # now cached
        self.assertEqual(self.fetches, 1)

    def test_not_published_yet_raises_the_skippable_error(self):
        self.online(["16.18.1", "16.17.1"])
        with self.assertRaises(items_db.NoReleaseYet):
            items_db.resolve_version("16.19.1")
        self.assertTrue(issubclass(items_db.NoReleaseYet, LookupError))   # old callers still work

    def test_offline_new_patch_raises_instead_of_hanging(self):
        def offline(url, timeout=30):
            raise OSError("no network")
        items_db._get_json = offline
        with self.assertRaises(items_db.NoReleaseYet):
            items_db.resolve_version("16.19.1")


if __name__ == "__main__":
    unittest.main()
