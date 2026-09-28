"""Entry point for the frozen .exe (PyInstaller runs this file as a plain script,
where the package's relative imports would fail)."""

import sys

from wincast.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
