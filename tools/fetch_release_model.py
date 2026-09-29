#!/usr/bin/env python3
"""
Download the last published model of the patch BEFORE a given one (used by the
release workflow: Wincast 16.20 ships the best 16.19 model).

    python tools/fetch_release_model.py --before 16.20 --out build/model.json

Checksum-verified and validated like an in-app download. If no earlier patch has
a published model (the very first release), writes nothing and exits 0: the
build then keeps the model already in the repo.
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from wincast import updates                    # noqa: E402
from wincast.models import patch_key           # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True, help="the patch being released, e.g. 16.20")
    ap.add_argument("--out", required=True)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", updates.REPO))
    ap.add_argument("--api", default=os.environ.get("GITHUB_API_URL", updates.API))
    args = ap.parse_args(argv)

    session = None
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:                                   # higher rate limit inside Actions
        import requests
        session = requests.Session()
        session.headers.update({"Accept": "application/vnd.github+json",
                                "User-Agent": "Wincast-release", "Authorization": f"Bearer {token}"})
    older = [m for m in updates.list_models(args.repo, args.api, session)
             if patch_key(m.patch) < patch_key(args.before)]
    if not older:
        print(f"no published model before {args.before}: keeping the repo's built-in model")
        return 0
    pick = older[-1]                            # the newest day of the newest earlier patch
    data = updates.fetch_verified(pick, session)
    json.loads(data)                            # sanity; full validation in prepare_release
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"{pick.tag}/{pick.name} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
