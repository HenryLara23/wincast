"""python -m wincast [--headless] [options]

Without --headless: the overlay app (needs PySide6).
With --headless: the terminal scorer (see headless.py for its options).
"""

import argparse
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--headless" in argv:
        argv.remove("--headless")
        from .headless import main as headless_main
        return headless_main(argv)

    ap = argparse.ArgumentParser(prog="wincast", description="Live win-chance overlay.")
    ap.add_argument("--model", help="a live model .json (default: the one bundled with the app)")
    ap.add_argument("--smoothing", type=float, help="smoothing time constant, game seconds (0 = off)")
    ap.add_argument("--replay", help="play a saved capture instead of reading the live game")
    ap.add_argument("--speed", type=float, default=10.0, help="replay speed (default 10x)")
    ap.add_argument("--unlocked", action="store_true", help="start with the overlay movable")
    ap.add_argument("--window", action="store_true", help="open the main window at start")
    ap.add_argument("--quit-after", type=float, help=argparse.SUPPRESS)   # tests / smoke runs
    args = ap.parse_args(argv)
    from .ui.app import run
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
