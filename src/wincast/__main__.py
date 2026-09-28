"""python -m wincast [--headless ...]

The Qt overlay isn't built yet, so the headless scorer is the only mode for now.
"""

import sys


def main():
    argv = [a for a in sys.argv[1:] if a != "--headless"]
    from .headless import main as headless_main
    return headless_main(argv)


if __name__ == "__main__":
    sys.exit(main())
