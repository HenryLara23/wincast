#!/usr/bin/env python3
"""
Pull the live-scoring code, the fallback model and a golden test game from the
model repo (`lolmodel/`) into Wincast.

    python tools/sync_core.py                 # sync from ../lolmodel
    python tools/sync_core.py --check         # report drift only, change nothing
    python tools/sync_core.py --src D:/wincast-model --model D:/current.json

Why a copy and not a dependency: Wincast is public and the model repo is not, so
the app can't install from it. The copy is kept honest three ways:

  1. The six files are copied byte for byte into src/lolwp/ and never edited
     here. CORE_MANIFEST.json records their SHA-256 and the source commit;
     tests/test_core_manifest.py fails if a vendored file is touched.
  2. tests/fixtures/golden_game.json holds an anonymised real game and the
     feature vectors + win chances that *lolmodel's* code produced for it at
     sync time. tests/test_golden.py re-scores it with the vendored copy.
  3. At runtime a model file built for another feature-set version is refused.

Nothing here writes to the source folder.
"""

import argparse
import copy
import glob
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent            # wincast/
VENDOR = ROOT / "src" / "lolwp"
RES = ROOT / "src" / "wincast" / "resources"
FIXTURES = ROOT / "tests" / "fixtures"

# The complete import closure of live scoring: play.py needs nothing else.
CORE_FILES = [
    "features/spec.py",
    "features/constants.py",
    "features/assemble.py",
    "features/live_features.py",
    "store/items_db.py",
    "model/bundle.py",
]

INIT_TEXT = {
    "__init__.py": '"""Vendored from the model repo by tools/sync_core.py. Do not edit here."""\n',
    "features/__init__.py": '"""Live feature extraction (vendored)."""\n',
    "store/__init__.py": '"""Data Dragon item prices (vendored)."""\n',
    "model/__init__.py": '"""JSON model bundle loader (vendored)."""\n',
}

GOLDEN_EVERY_S = 90.0      # one snapshot per 90 s of game time keeps the fixture ~0.5 MB


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_commit(src):
    # GIT_OPTIONAL_LOCKS=0: `git status` otherwise refreshes the index and takes
    # .git/index.lock in the MODEL repo, which a sandboxed or interrupted run can
    # leave behind -- and then every git command there fails.
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    try:
        rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True,
                             text=True, check=True, env=env).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=src,
                               capture_output=True, text=True, check=True, env=env).stdout.strip()
        return rev + ("+dirty" if dirty else "")
    except Exception:
        return "unknown"


def feature_set_version(spec_path):
    m = re.search(r'^FEATURE_SET_VERSION\s*=\s*"([^"]+)"', Path(spec_path).read_text("utf-8"), re.M)
    return m.group(1) if m else None


# --------------------------------------------------------------------------- check


def check(src):
    """-> list of problems (empty = in sync)."""
    problems = []
    man_path = VENDOR / "CORE_MANIFEST.json"
    if not man_path.exists():
        return ["no CORE_MANIFEST.json yet -- run a sync"]
    man = json.loads(man_path.read_text("utf-8"))
    for rel in CORE_FILES:
        want = man["files"].get(rel)
        here = VENDOR / rel
        there = src / "lolwp" / rel
        if not here.exists():
            problems.append(f"missing vendored file {rel}")
        elif sha256(here) != want:
            problems.append(f"{rel} was edited inside wincast (hash differs from the manifest)")
        if there.exists() and sha256(there) != want:
            problems.append(f"{rel} changed in the model repo since the last sync")
    return problems


# --------------------------------------------------------------------------- sync pieces


def copy_core(src):
    # Overwrite in place rather than wiping the folder: the set of files is fixed.
    for rel in CORE_FILES:
        dst = VENDOR / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / "lolwp" / rel, dst)
    for rel, text in INIT_TEXT.items():
        (VENDOR / rel).write_text(text, encoding="utf-8")


def copy_model(model_path):
    bundle = json.loads(Path(model_path).read_text("utf-8"))
    if bundle.get("kind") != "live":
        raise SystemExit(f"{model_path} is not a live model (kind={bundle.get('kind')!r})")
    dst = RES / "models" / "fallback.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(model_path, dst)
    return bundle, dst


def copy_items(src, patch):
    """Bundle the model patch's item prices so a fresh install scores offline."""
    cache = src / "data" / "ddragon"
    found = sorted(glob.glob(str(cache / f"item-{patch}.*-en_US.json")))
    if not found:
        raise SystemExit(f"no cached Data Dragon items for patch {patch} in {cache}.\n"
                         "Run play.py in lolmodel once online so they are cached.")
    path = Path(found[-1])
    version = path.name[len("item-"):-len("-en_US.json")]
    out = RES / "ddragon"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("item-*.json"):                  # only one patch is bundled
        if old.name != path.name:
            try:
                old.unlink()
            except OSError:
                print(f"  note: could not remove old {old.name}; delete it by hand")
    shutil.copy2(path, out / path.name)
    if (cache / "versions.json").exists():
        shutil.copy2(cache / "versions.json", out / "versions.json")
    return version


def newest_capture(src):
    caps = sorted(glob.glob(str(src / "data" / "captures" / "capture-*.jsonl.gz")))
    return Path(caps[-1]) if caps else None


def build_golden(src, capture, model_path, items_version):
    """Anonymise a thinned capture, then score it with lolmodel's own code."""
    sys.path.insert(0, str(src / "scripts"))
    import anonymize_capture as anon                     # lolmodel/scripts/
    sys.path.pop(0)

    snaps, next_t = [], 0.0
    with gzip.open(capture, "rt", encoding="utf-8") as fh:
        for raw in fh:
            data = json.loads(raw).get("data")
            if not data:
                continue
            g = data.get("gameData") or {}
            if g.get("mapNumber") != 11 or len(data.get("allPlayers") or []) != 10:
                continue
            t = float(g.get("gameTime") or 0.0)
            if t >= next_t:
                snaps.append(copy.deepcopy(data))
                next_t = t + GOLDEN_EVERY_S
    if len(snaps) < 5:
        raise SystemExit(f"{capture} has only {len(snaps)} usable snapshots")

    # One name mapping for the whole game. The live API does not keep allPlayers
    # in a fixed order between snapshots, so numbering each snapshot on its own
    # (what anonymize() does) would turn Red1 into a different player every 90 s.
    order = copy.deepcopy(snaps[0])
    order["allPlayers"].sort(key=lambda p: (p.get("team", ""), p.get("championName", "")))
    mapping = anon.build_mapping(order)
    for data in snaps:
        for p in data.get("allPlayers") or []:
            anon.scrub_player(p, mapping)
        if isinstance(data.get("activePlayer"), dict):
            anon.scrub_player(data["activePlayer"], mapping)
        for e in (data.get("events") or {}).get("Events") or []:
            for key in anon.EVENT_NAME_FIELDS:
                if isinstance(e.get(key), str) and e[key] in mapping:
                    e[key] = mapping[e[key]]
            if isinstance(e.get("Assisters"), list):
                e["Assisters"] = [mapping.get(n, "anonymous") for n in e["Assisters"]]

    fd, tmp = tempfile.mkstemp(suffix=".json")       # outside the repo
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(snaps, fh)
    tmp = Path(tmp)
    scorer = r"""
import json, os, sys
src, inp, model, ver = sys.argv[1:5]
sys.path.insert(0, src)
from lolwp.features import live_features as live
from lolwp.model.bundle import Model
from lolwp.store.items_db import ItemDB
m = Model.load(model)
db = ItemDB.load(version=ver)
out = []
for d in json.load(open(inp, encoding="utf-8")):
    ok, why = live.game_check(d)
    x = live.extract(d, db)
    out.append({"ok": ok, "x": [float(v) for v in x], "p_blue": m.predict_one(x)})
print(json.dumps(out))
"""
    env = dict(os.environ, DDRAGON_CACHE=str(src / "data" / "ddragon"))
    try:
        res = subprocess.run([sys.executable, "-c", scorer, str(src), str(tmp),
                              str(model_path), items_version],
                             capture_output=True, text=True, env=env, cwd=src)
    finally:
        tmp.unlink(missing_ok=True)
    if res.returncode != 0:
        raise SystemExit("scoring the golden game with lolmodel failed:\n" + res.stderr)
    scored = json.loads(res.stdout)

    golden = {
        "about": "Anonymised real SR game, one snapshot per ~90 s. `expected` was "
                 "produced by the model repo's code at sync time; tests/test_golden.py "
                 "re-scores it with the vendored copy. Regenerate with tools/sync_core.py.",
        "items_version": items_version,
        "model": "src/wincast/resources/models/fallback.json",
        "snapshots": snaps,
        "expected": scored,
    }
    dst = FIXTURES / "golden_game.json"
    dst.write_text(json.dumps(golden, separators=(",", ":")), encoding="utf-8")
    return dst, len(snaps)


# --------------------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=str(ROOT.parent / "lolmodel"),
                    help="the model repo (default: ../lolmodel)")
    ap.add_argument("--model", help="live model file to ship as the fallback "
                                    "(default: <src>/models/current.json)")
    ap.add_argument("--capture", help="capture for the golden test "
                                      "(default: newest in <src>/data/captures)")
    ap.add_argument("--check", action="store_true", help="report drift, change nothing")
    args = ap.parse_args()

    src = Path(args.src).resolve()
    if not (src / "lolwp" / "features" / "spec.py").exists():
        raise SystemExit(f"{src} doesn't look like the model repo (no lolwp/features/spec.py)")

    if args.check:
        problems = check(src)
        for p in problems:
            print("DRIFT:", p)
        print("in sync" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0

    model_path = Path(args.model) if args.model else src / "models" / "current.json"
    capture = Path(args.capture) if args.capture else newest_capture(src)

    copy_core(src)
    bundle, _ = copy_model(model_path)
    fsv = feature_set_version(VENDOR / "features" / "spec.py")
    if bundle.get("feature_set_version") != fsv:
        raise SystemExit(f"model is for feature set {bundle.get('feature_set_version')}, "
                         f"code is {fsv}: sync a matching model")
    items_version = copy_items(src, bundle.get("patch"))

    manifest = {
        "source_repo": "wincast-model (private)",
        "source_commit": git_commit(src),
        "synced_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "feature_set_version": fsv,
        "files": {rel: sha256(VENDOR / rel) for rel in CORE_FILES},
        "fallback_model": {"name": bundle.get("name"), "patch": bundle.get("patch"),
                           "sha256": sha256(RES / "models" / "fallback.json")},
        "ddragon_items": items_version,
    }
    (VENDOR / "CORE_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                               encoding="utf-8")
    print(f"core: {len(CORE_FILES)} files, feature set {fsv}, from {manifest['source_commit'][:12]}")
    print(f"fallback model: {bundle.get('name')}")
    print(f"item prices: Data Dragon {items_version}")

    if capture and capture.exists():
        dst, n = build_golden(src, capture, model_path, items_version)
        print(f"golden game: {n} snapshots -> {dst.relative_to(ROOT)} "
              f"({dst.stat().st_size // 1024} KB)")
    else:
        print("golden game: no capture found, kept the existing fixture")
    return 0


if __name__ == "__main__":
    sys.exit(main())
