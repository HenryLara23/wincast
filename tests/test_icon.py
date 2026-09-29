"""The app icon files ship with the app and the .ico carries every size."""

import struct
import unittest
from pathlib import Path

ICON = Path(__file__).resolve().parent.parent / "src" / "wincast" / "resources" / "icon"


class IconFiles(unittest.TestCase):
    def test_pngs_present(self):
        for n in (16, 24, 32, 48, 256):
            with self.subTest(size=n):
                data = (ICON / f"wincast-{n}.png").read_bytes()
                self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                w, h = struct.unpack(">II", data[16:24])
                self.assertEqual((w, h), (n, n))

    def test_ico_has_small_and_large(self):
        data = (ICON / "wincast.ico").read_bytes()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind), (0, 1))
        sizes = {data[6 + 16 * i] or 256 for i in range(count)}
        self.assertTrue({16, 32, 48, 256} <= sizes, sizes)
