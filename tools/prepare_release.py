#!/usr/bin/env python3
"""
Get the source tree ready to build Wincast <patch> (used by the release workflow).

    python tools/prepare_release.py --version 16.20 [--model build/model.json]

  * sets wincast.__version__ to the patch ("16.20")
  * with --model: validates it like the app does, makes it the built-in model
    (resources/models/fallback.json), bundles Data Dragon item prices for its
    patch (downloaded if not already there), and updates CORE_MANIFEST.json
  * writes build/release-notes.md

Touches only the working tree of the build machine; nothing is committed.
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DDRAGON = "https://ddragon.leagueoflegends.com"


def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def get_json(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def ensure_items(res_dd: Path, patch: str) -> str:
    """Item prices for `patch` in resources/ddragon; returns the Data Dragon version."""
    have = sorted(res_dd.glob(f"item-{patch}.*-en_US.json"))
    if have:
        return have[-1].name[len("item-"):-len("-en_US.json")]
    versions = get_json(f"{DDRAGON}/api/versions.json")
    version = next((v for v in versions if v.startswith(patch + ".")), None)
    if version is None:
        raise SystemExit(f"Data Dragon has no release for patch {patch} yet")
    items = get_json(f"{DDRAGON}/cdn/{version}/data/en_US/item.json")
    (res_dd / f"item-{version}-en_US.json").write_text(json.dumps(items), encoding="utf-8")
    (res_dd / "versions.json").write_text(json.dumps(versions), encoding="utf-8")
    return version


def main(argv=None, root=ROOT):
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True, help="the patch being released, e.g. 16.20")
    ap.add_argument("--model", help="model file to build in (default: keep the current one)")
    args = ap.parse_args(argv)
    if not re.fullmatch(r"\d+\.\d+", args.version):
        raise SystemExit(f"version should look like 16.20, not {args.version!r}")

    src = root / "src"
    res = src / "wincast" / "resources"
    manifest_path = src / "lolwp" / "CORE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))

    init = src / "wincast" / "__init__.py"
    text = init.read_text("utf-8")
    text = re.sub(r'__version__ = "[^"]*"', f'__version__ = "{args.version}"', text)
    init.write_text(text, encoding="utf-8")

    fallback = res / "models" / "fallback.json"
    if args.model:
        sys.path.insert(0, str(src))
        from lolwp.model.bundle import BundleError, Model
        raw = Path(args.model).read_bytes()
        bundle = json.loads(raw)
        try:
            if bundle.get("kind") != "live":
                raise BundleError("not a live model")
            Model(bundle)
        except (BundleError, KeyError) as exc:
            raise SystemExit(f"{args.model} can't be built in: {exc}")
        fallback.write_bytes(raw)
        version = ensure_items(res / "ddragon", bundle["patch"])
        for old in (res / "ddragon").glob("item-*-en_US.json"):
            if old.name != f"item-{version}-en_US.json":
                try:
                    old.unlink()
                except OSError:
                    pass
        manifest["ddragon_items"] = version
    bundle = json.loads(fallback.read_text("utf-8"))
    manifest["fallback_model"] = {"name": bundle.get("name"), "patch": bundle.get("patch"),
                                  "sha256": sha256(fallback)}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    hold = (bundle.get("metrics") or {}).get("holdout") or {}
    games = (bundle.get("games") or {}).get("train") or 0
    notes = (f"Wincast for League of Legends patch **{args.version}**.\n\n"
             f"Built-in model: `{bundle.get('name')}`, trained on patch {bundle.get('patch')} "
             f"({games:,} games; {hold.get('acc', 0):.1%} accuracy on games it never saw). "
             f"Newer models for the current patch are published daily: Models tab > Check Now.\n\n"
             f"**Download:** most people want `Wincast-{args.version}-setup.exe`: run it and "
             f"follow the steps. Rather not install anything? Get `Wincast-{args.version}-win64.zip`, "
             f"unzip it anywhere and run `Wincast.exe`. Either way your history and settings carry "
             f"over.\n\n"
             f"**\"Windows protected your PC\"?** Wincast isn't code-signed, so Windows warns about "
             f"it like any new unsigned app. Click **More info**, then **Run anyway**. You'll see "
             f"this once for each download.\n\n"
             f"The game must be in Borderless or Windowed mode. Checksums: `SHA256SUMS`.\n\n"
             f"Wincast isn't endorsed by Riot Games and doesn't reflect the views or opinions of "
             f"Riot Games or anyone officially involved in producing or managing Riot Games "
             f"properties.\n")
    (root / "build").mkdir(exist_ok=True)
    (root / "build" / "release-notes.md").write_text(notes, encoding="utf-8")
    print(f"version {args.version}; built-in model {bundle.get('name')} "
          f"(patch {bundle.get('patch')}); items {manifest.get('ddragon_items')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
