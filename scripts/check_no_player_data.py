#!/usr/bin/env python3
"""
Refuse to commit anything that identifies a player.

Riot's developer policies do not allow redistributing identifiable player data,
and this repo is meant to be public. `.gitignore` keeps the obvious places out;
this catches what slips past it -- a fixture copied from a real capture, a path
outside data/, a key pasted into a doc.

    python scripts/check_no_player_data.py            # scan every tracked file
    python scripts/check_no_player_data.py --staged   # scan what is about to be committed

Installed as a pre-commit hook with:
    git config core.hooksPath .githooks

What it looks for:
  * any file under a `data/` directory, or with a data-store extension
  * PUUIDs (78-character tokens)
  * Riot API keys (RGAPI-...)
  * Riot ID / summoner-name fields in JSON whose value is not a placeholder
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BLOCKED_EXT = {".sqlite", ".db", ".parquet", ".jsonl", ".gz", ".log"}
PUUID = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{78}(?![A-Za-z0-9_-])")
API_KEY = re.compile(r"RGAPI-[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}")
NAME_KEYS = {"riotId", "riotIdGameName", "summonerName", "gameName",
             "KillerName", "VictimName", "Recipient", "Acer", "puuid",
             "summonerId", "accountId"}
# Placeholders the anonymiser and the tests use. Anything else in a name field
# is presumed to be a real person.
PLACEHOLDER = re.compile(r"^(Player\d+|Blue\d+|Red\d+|[BRP]\d+|Minion.*|Turret.*|"
                         r"SRU_.*|Monster.*|teambuilder-match-.*|you|YourName|anonymous)(#[A-Z0-9]+)?$",
                         re.IGNORECASE)


def files(staged):
    cmd = (["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
           if staged else ["git", "ls-files"])
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line.strip()]


def json_names(obj, found, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in NAME_KEYS and isinstance(v, str) and v and not PLACEHOLDER.match(v):
                found.append(f"{path}.{k} = {v[:40]!r}")
            elif k == "Assisters" and isinstance(v, list):
                for n in v:
                    if isinstance(n, str) and not PLACEHOLDER.match(n):
                        found.append(f"{path}.Assisters contains {n[:40]!r}")
            json_names(v, found, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:500]):
            json_names(v, found, f"{path}[{i}]")


def check(rel):
    problems = []
    p = Path(rel)
    if "data" in p.parts:
        problems.append("lives under a data/ directory")
    if p.suffix.lower() in BLOCKED_EXT:
        problems.append(f"data-store file type ({p.suffix})")
    full = ROOT / rel
    if not full.is_file() or full.stat().st_size > 5_000_000:
        return problems
    try:
        text = full.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return problems
    if API_KEY.search(text):
        problems.append("contains a Riot API key")
    if PUUID.search(text) and p.suffix.lower() in {".json", ".md", ".txt", ".csv"}:
        problems.append("contains a PUUID-shaped token")
    if p.suffix.lower() == ".json":
        try:
            found = []
            json_names(json.loads(text), found)
            if found:
                problems.append(f"{len(found)} real-looking player name(s), e.g. {found[0]}")
        except ValueError:
            pass
    return problems


def main():
    staged = "--staged" in sys.argv
    bad = {}
    for rel in files(staged):
        problems = check(rel)
        if problems:
            bad[rel] = problems
    if not bad:
        print(f"no player data in {'staged' if staged else 'tracked'} files")
        return 0
    print("REFUSING: these files would publish player data\n")
    for rel, problems in sorted(bad.items()):
        print(f"  {rel}")
        for problem in problems:
            print(f"      - {problem}")
    print("\nUntrack with:  git rm --cached <file>   (the file stays on disk)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
