"""Reading the Live Client Data API that League serves on this PC.

One call per poll: /liveclientdata/allgamedata. What comes back is sorted into
four kinds so the engine never has to look at HTTP details:

  OFFLINE  nothing listening on 127.0.0.1:2999 -- no game client running
  LOADING  the API answers but has no game yet (loading screen, or a
           half-formed payload right at game start)
  DATA     a full snapshot
  ERROR    something transient (a timeout while the game hitches). The engine
           keeps its last state rather than flapping.

TLS: the game serves a certificate signed by Riot's own root, published as
riotgames.pem (https://static.developer.riotgames.com/docs/lol/riotgames.pem).
If that file is in resources/, it is used to verify the connection; otherwise,
or if verification fails, the client falls back to an unverified connection to
localhost -- which is what every tool built on this API does -- and says so in
`tls`.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any, Optional

from .paths import RESOURCES

BASE = "https://127.0.0.1:2999"
ALL_GAME_DATA = "/liveclientdata/allgamedata"
EVENT_DATA = "/liveclientdata/eventdata"
RIOT_PEM = RESOURCES / "riotgames.pem"

OFFLINE, LOADING, DATA, ERROR = "offline", "loading", "data", "error"


@dataclass(frozen=True)
class Poll:
    kind: str
    data: Optional[dict] = None
    detail: str = ""

    @classmethod
    def of(cls, data: Any) -> "Poll":
        """Classify a decoded payload (also used by replays and tests)."""
        if isinstance(data, dict) and data.get("gameData") and data.get("allPlayers"):
            return cls(DATA, data)
        return cls(LOADING, detail="no game data yet")


class LiveClient:
    def __init__(self, base: str = BASE, timeout: float = 2.0, cert=None):
        import requests
        self._requests = requests
        self.url = base.rstrip("/") + ALL_GAME_DATA
        self.events_url = base.rstrip("/") + EVENT_DATA
        self.timeout = (1.0, timeout)                 # (connect, read)
        self.session = requests.Session()
        cert = cert if cert is not None else (RIOT_PEM if RIOT_PEM.exists() else None)
        if base.startswith("https") and cert:
            self.session.verify = str(cert)
            self.tls = "verified (riotgames.pem)"
        else:
            self._insecure("unverified (no riotgames.pem)" if base.startswith("https")
                           else "plain http")

    def _insecure(self, why):
        self.session.verify = False
        self.tls = why
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    def poll(self) -> Poll:
        rq = self._requests
        try:
            r = self.session.get(self.url, timeout=self.timeout)
        except rq.exceptions.SSLError as exc:
            if self.session.verify is not False:
                # The certificate didn't check out against riotgames.pem (e.g. a
                # hostname mismatch). Localhost only, so carry on unverified.
                self._insecure(f"unverified (riotgames.pem check failed: {type(exc).__name__})")
                return Poll(ERROR, detail="TLS verification failed; retrying unverified")
            return Poll(ERROR, detail=f"TLS error: {exc}")
        except rq.exceptions.ConnectTimeout:
            return Poll(OFFLINE, detail="connect timeout")
        except rq.exceptions.ConnectionError:
            return Poll(OFFLINE, detail="no game client")
        except rq.exceptions.Timeout:
            return Poll(ERROR, detail="read timeout")
        if r.status_code != 200:
            return Poll(LOADING, detail=f"HTTP {r.status_code}")
        try:
            return Poll.of(r.json())
        except ValueError:
            return Poll(LOADING, detail="not JSON yet")

    def events(self):
        """The event list alone (a much smaller reply than /allgamedata), or None.

        Used when a full snapshot fails mid-game: while the nexus explodes the
        game can stop answering the big request, and that is exactly when the
        GameEnd event appears (seen 2026-09-28: two games closed with no GameEnd
        ever received)."""
        try:
            r = self.session.get(self.events_url, timeout=(1.0, 1.5))
            if r.status_code != 200:
                return None
            data = r.json()
            return data.get("Events") if isinstance(data, dict) else None
        except Exception:
            return None

    def close(self):
        self.session.close()
