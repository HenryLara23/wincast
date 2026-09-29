"""New models from GitHub Releases.

The server keeps one release per patch on the public repo (default
HenryLara23/wincast), tagged `models-<patch>` and never marked "latest", with
one file per day:

    model-16.19-20261003-0105.fs1.2.0.json    patch, when it was trained, feature set
    SHA256SUMS                                checksums of every file

`check()` finds the newest patch that has a model for THIS app's feature set and
takes its newest file; `download()` fetches it, verifies the SHA-256, validates
it through the model store and installs it. Nothing here runs unless the user
asked: the startup check is off by default, and "Check Now" is a button.

No token, no account: GitHub's public API (60 requests an hour per IP; Wincast
makes one per check).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional

from lolwp.features import spec

REPO = "HenryLara23/wincast"
API = "https://api.github.com"
TAG_PREFIX = "models-"
TIMEOUT = 15
ASSET = re.compile(r"^model-(\d+\.\d+)-(\d{8}-\d{4})\.fs(.+)\.json$")
REPO_URL = f"https://github.com/{REPO}"
RELEASES_PAGE = f"{REPO_URL}/releases/latest"


@dataclass
class ModelRelease:
    tag: str                    # models-16.19
    name: str                   # the file, model-16.19-20261003-0105.fs1.2.0.json
    patch: str
    stamp: str                  # when the model was trained, YYYYMMDD-HHMM
    model_url: str
    sums_url: Optional[str]
    page_url: str = ""

    def sort_key(self):
        from .models import patch_key
        return (patch_key(self.patch), self.stamp)


class UpdateError(RuntimeError):
    pass


def _session(session=None):
    if session is not None:
        return session
    import requests
    s = requests.Session()
    s.headers.update({"Accept": "application/vnd.github+json", "User-Agent": "Wincast"})
    return s


def _fetch_releases(repo: str, api: str, session=None) -> list:
    s = _session(session)
    try:
        r = s.get(f"{api}/repos/{repo}/releases", params={"per_page": 100}, timeout=TIMEOUT)
    except Exception as exc:
        raise UpdateError(f"couldn't reach GitHub ({type(exc).__name__})") from None
    if r.status_code == 404:
        raise UpdateError(f"no such repository: {repo}")
    if r.status_code == 403:
        raise UpdateError("GitHub is rate-limiting this connection; try again later")
    if r.status_code != 200:
        raise UpdateError(f"GitHub answered HTTP {r.status_code}")
    return r.json() or []


def _models_in(releases) -> list:
    found = []
    for rel in releases:
        tag = rel.get("tag_name") or ""
        if not tag.startswith(TAG_PREFIX) or rel.get("draft"):
            continue
        assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets") or []}
        for name, url in assets.items():
            m = ASSET.match(name or "")
            if m and m.group(3) == spec.FEATURE_SET_VERSION:        # this app's feature set only
                found.append(ModelRelease(tag=tag, name=name, patch=m.group(1), stamp=m.group(2),
                                          model_url=url, sums_url=assets.get("SHA256SUMS"),
                                          page_url=rel.get("html_url") or ""))
    return sorted(found, key=lambda x: x.sort_key())


def list_models(repo: str = REPO, api: str = API, session=None):
    """Every published model file this app can use (newest patch/day last)."""
    return _models_in(_fetch_releases(repo, api, session))


def check(repo: str = REPO, api: str = API, session=None) -> Optional[ModelRelease]:
    """The newest published model this app can use, or None if there isn't one."""
    found = list_models(repo, api, session)
    return found[-1] if found else None


def is_newer(release: Optional[ModelRelease], store) -> bool:
    """Worth offering: not installed yet, and newer than the newest usable model."""
    if release is None or store.has_release(release.patch, release.stamp):
        return False
    from .models import patch_key
    best = store.newest()
    if best is None:
        return True
    return (patch_key(release.patch), release.stamp) > (patch_key(best.patch), best.stamp)


def fetch_verified(release: ModelRelease, session=None) -> bytes:
    """Download a published model and check it against SHA256SUMS. Returns the bytes."""
    s = _session(session)
    try:
        r = s.get(release.model_url, timeout=TIMEOUT)
    except Exception as exc:
        raise UpdateError(f"download failed ({type(exc).__name__})") from None
    if r.status_code != 200:
        raise UpdateError(f"download failed: HTTP {r.status_code}")
    data = r.content
    if not release.sums_url:
        raise UpdateError("the release has no checksum file; not installing it")
    try:
        sums = s.get(release.sums_url, timeout=TIMEOUT)
    except Exception as exc:
        raise UpdateError(f"checksum download failed ({type(exc).__name__})") from None
    if sums.status_code != 200:
        raise UpdateError(f"checksum download failed: HTTP {sums.status_code}")
    want = None
    for line in sums.text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == release.name:
            want = parts[0].lower()
    if want is None:
        raise UpdateError("the checksum file doesn't list the model")
    if hashlib.sha256(data).hexdigest() != want:
        raise UpdateError("checksum mismatch: the download is corrupt or was tampered with")
    return data


def download(release: ModelRelease, store, session=None):
    """Fetch, verify, validate, install. Returns the installed ModelInfo."""
    data = fetch_verified(release, session)
    try:
        return store.install_bytes(data)
    except ValueError as exc:
        raise UpdateError(str(exc)) from None


def issue_url(version: str, model: str = "", windows: str = "") -> str:
    """A new GitHub issue with the useful details already filled in."""
    from urllib.parse import quote
    body = ("**What happened?**\n\n\n**What did you expect instead?**\n\n\n---\n"
            f"Wincast {version} · model {model or 'unknown'} · Windows {windows or 'unknown'}\n\n"
            "Please attach `wincast.log` (Help > Open Log Folder). It holds no player names; "
            "error messages in it can include your Windows user name in file paths.\n")
    return f"https://github.com/{REPO}/issues/new?body={quote(body)}"


def season_year(version: str) -> Optional[int]:
    """'16.19' -> 2026 (League patch 16.x is the 2026 season). None for dev builds."""
    m = re.fullmatch(r"(\d+)\.\d+", str(version))
    return 2010 + int(m.group(1)) if m else None


def app_is_old(version: str, today=None) -> bool:
    """True once the NEXT season's first patch is likely out (from Jan 15), judged
    by the PC's clock alone. Development builds (0.1.0.dev0) never count as old."""
    import datetime
    year = season_year(version)
    if year is None:
        return False
    today = today or datetime.date.today()
    return today >= datetime.date(year + 1, 1, 15)
