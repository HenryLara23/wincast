"""Only one Wincast runs: a second launch finds the first and asks it to show itself."""

import os
import unittest

try:
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
    from wincast.ui.single import SingleInstance
    HAVE_QT = True
except ImportError:                                  # PySide6 not installed
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "needs PySide6")
class TestSingleInstance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_second_launch_reaches_the_first(self):
        name = f"wincast-test-{os.getpid()}"
        first = SingleInstance(name)
        self.assertFalse(first.already_running())
        self.assertTrue(first.listen())
        shown = []
        first.activated.connect(lambda: shown.append(1))
        try:
            second = SingleInstance(name)
            self.assertTrue(second.already_running())
            loop = QEventLoop()
            first.activated.connect(loop.quit)
            QTimer.singleShot(2000, loop.quit)
            if not shown:
                loop.exec()
            self.assertEqual(shown, [1])
        finally:
            first.close()
        self.assertFalse(SingleInstance(name).already_running())
