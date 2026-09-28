"""Wincast: a live win-chance overlay for Summoner's Rift.

Reads only the Live Client Data API the game serves on this PC
(https://127.0.0.1:2999). No Riot API key, no account, nothing injected into
the game. Live scoring lives in the vendored `lolwp` package (see
tools/sync_core.py); everything in `wincast` is the app around it.
"""

__version__ = "0.1.0.dev0"
APP_NAME = "Wincast"
