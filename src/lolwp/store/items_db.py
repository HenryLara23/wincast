#!/usr/bin/env python3
"""
Item price table from Riot's Data Dragon, keyed by itemID.

Why not just use the live API's `items[].price`:

  * The live `price` field is the COMBINE cost, not the item's value. Measured
    on a real game: Lost Chapter (3802) reported price=250 against a true cost
    of 1200. It equals Data Dragon's `gold.base`. Summing it undercounts every
    completed item, and the gap widens as builds finish.
  * The match-v5 timeline stores only item IDs on ITEM_PURCHASED / ITEM_SOLD /
    ITEM_DESTROYED events. There is no price in it. So a price table is required
    for the training side no matter what.
  * Using the same table on both sides guarantees the two feature extractors
    agree. Mixing live `price` at inference with a Data Dragon lookup in
    training would bake in a constant offset between train and serve.
  * Item costs change between patches. Training data spans patches, so prices
    have to be pinned per patch — match-v5 gives you `gameVersion`, and
    resolve_version() maps it to the right Data Dragon release.
  * `gold.sell` lets you value sold components correctly (70%, not 100%).

    python -m lolwp.store.items_db --update            # cache the latest patch
    python -m lolwp.store.items_db --show 3078
    python -m lolwp.store.items_db --audit-live        # live `price` vs Data Dragon `total`

(run from the repo root with src/ on PYTHONPATH)

Cache lands in data/ddragon/item-<version>.json. Keep those files with the
training set: they are what makes an old patch's features reproducible.
"""

import argparse
import json
import os
import urllib.request

VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
ITEMS_URL = "https://ddragon.leagueoflegends.com/cdn/{v}/data/{loc}/item.json"
# lolmodel/ is two levels up from lolmodel/lolwp/store/. The cache lives inside it so

# Items the game hands out rather than selling. Excluded from the gold proxy so
# the live side and the timeline side agree -- see ItemDB.counts_toward_gold.
# The support-item chain: World Atlas -> Runic Compass -> Bounty of Worlds ->
# the five finished items, plus the lane/jungle quest trackers.
MAX_ITEM_SLOTS = 6           # six item slots; the trinket is never priced
TRANSFORMED_MIN_GOLD = 500   # see ItemDB.counts_toward_gold

GRANTED_ITEM_IDS = frozenset({
    3865, 3866, 3867,                          # World Atlas line
    3869, 3870, 3871, 3876, 3877,              # finished support items
    1200, 1201, 1202, 1203, 1204,              # lane / jungle quests
    1206, 1207, 1208, 1209, 1220, 1222,        # quest rewards
    3513, 1104,                                # Eye of the Herald
    3400,                                      # Your Cut
    1101, 1102, 1103,                          # jungle pets -- see below
})
# Jungle pets (Scorchclaw Pup, Gustwalker Hatchling, Mosstomper Seedling, 450g
# each) are bought by the game and, confirmed by the 2026-09-19 parity capture,
# never appear in the live inventory at all: the replay held them for both
# junglers while the live side showed nothing, a standing 900g gap across the two
# teams.

# the whole folder can be copied to the server or the gaming PC as one unit.
PKG_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
CACHE_DIR = os.environ.get("DDRAGON_CACHE") or os.path.join(PKG_ROOT, "data", "ddragon")


def _get_json(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def all_versions(refresh=False):
    path = os.path.join(CACHE_DIR, "versions.json")
    if not refresh and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    data = _get_json(VERSIONS_URL)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


class NoReleaseYet(LookupError):
    """Data Dragon has no release for this patch (yet): it can lag a new patch by hours."""


def resolve_version(game_version=None, refresh=False):
    """match-v5 `gameVersion` looks like '15.18.634.2345'; Data Dragon releases
    look like '15.18.1'. Match on major.minor, newest first.

    A miss in the CACHED version list re-downloads it once before giving up:
    otherwise the list cached on the first run never learns about a later patch
    (2026-09-27: both retrains crashed hourly on 16.19 with a list from 16.18)."""
    versions = all_versions(refresh=refresh)
    if not game_version:
        return versions[0]
    parts = str(game_version).split(".")
    if len(parts) < 2:
        return versions[0]
    prefix = f"{parts[0]}.{parts[1]}."
    found = next((v for v in versions if v.startswith(prefix)), None)
    if found is None and not refresh:
        try:
            versions = all_versions(refresh=True)
        except Exception:                       # offline: keep the cached answer
            versions = []
        found = next((v for v in versions if v.startswith(prefix)), None)
    if found is None:
        raise NoReleaseYet(f"no Data Dragon release for game version {game_version!r}")
    return found


class ItemDB:
    """Price/metadata lookup for one patch."""

    def __init__(self, version, data):
        self.version = version
        self.data = data
        self.misses = set()
        self._upgrades = {}

    @classmethod
    def load(cls, version=None, game_version=None, locale="en_US", refresh=False):
        version = version or resolve_version(game_version, refresh=refresh)
        path = os.path.join(CACHE_DIR, f"item-{version}-{locale}.json")
        if not refresh and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                blob = json.load(f)
        else:
            blob = _get_json(ITEMS_URL.format(v=version, loc=locale))
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(blob, f)
        return cls(version, blob.get("data", {}))

    def _entry(self, item_id):
        e = self.data.get(str(item_id))
        if e is None and item_id:
            self.misses.add(int(item_id))
        return e

    def base(self, item_id):
        """Combine cost. This is what the live API's `items[].price` reports."""
        e = self._entry(item_id)
        return int(e["gold"]["base"]) if e else 0

    def total(self, item_id):
        """Full gold cost, components included. 0 for unknown/free items."""
        e = self._entry(item_id)
        return int(e["gold"]["total"]) if e else 0

    def sell(self, item_id):
        e = self._entry(item_id)
        return int(e["gold"]["sell"]) if e else 0

    def name(self, item_id):
        e = self._entry(item_id)
        return e["name"] if e else f"<unknown {item_id}>"

    def tags(self, item_id):
        e = self._entry(item_id)
        return set(e.get("tags", [])) if e else set()

    def is_consumable(self, item_id):
        return "Consumable" in self.tags(item_id)

    def is_trinket(self, item_id):
        return "Trinket" in self.tags(item_id)

    def purchasable(self, item_id):
        e = self._entry(item_id)
        return bool(e and e.get("gold", {}).get("purchasable"))

    def in_place_upgrade(self, item_id):
        """The free upgrade this item turns into, or None.

        An in-place upgrade is an item with exactly one `into` target whose `from`
        is exactly this item -- the boots line under the feats system
        (Berserker's Greaves -> Gunmetal Greaves, and five more). The game grants
        the upgrade with no purchase event, so the replay would otherwise hold the
        predecessor forever while the live API shows the successor. Every such
        pair in 16.18 has identical total gold, so the swap is gold-neutral and
        keeps the two sides holding the same item."""
        if item_id in self._upgrades:
            return self._upgrades[item_id]
        entry = self._entry(item_id) or {}
        into = entry.get("into") or []
        result = None
        if len(into) == 1:
            target = self._entry(int(into[0])) or {}
            if [str(item_id)] == (target.get("from") or []):
                result = int(into[0])
        self._upgrades[item_id] = result
        return result

    def counts_toward_gold(self, item_id):
        """What we sum for the item-gold feature.

        Three exclusions, and the last two exist for training/serving parity
        rather than for correctness of "what is this worth":

        1. Consumables and trinkets -- spent or free value, not held value.
        2. `gold.purchasable == false` -- items the game GRANTS. Verified against
           1,160 collected games (2026-09-18): Recall (60g), Enhanced Recall,
           Biscuits, Runic Compass (400g), Bounty of Worlds (400g), quest items
           and elixirs are destroyed thousands of times and purchased by a real
           participant zero times. The live API shows them sitting in the
           inventory, so counting them would add gold on the serving side that
           the timeline can never produce.
        3. GRANTED_ITEM_IDS -- the support-item chain. World Atlas is bought by
           "participant 0" (the game) in 2,320 of 2,324 purchases, and the
           finished support items are mostly granted on quest completion too, so
           the timeline cannot attribute any of them to a player. Both teams have
           exactly one support, so dropping them costs the diff almost nothing.
        """
        if item_id in GRANTED_ITEM_IDS:
            return False
        t = self.tags(item_id)
        if {"Consumable", "Trinket"} & t:
            return False
        if self.purchasable(item_id):
            return self.total(item_id) > 0
        # Not purchasable. Two very different things live here: cheap granted
        # junk (Recall 60g, Biscuit 50g, Runic Compass 400g), and TRANSFORMED
        # real items -- Archangel's -> Seraph's Embrace (2900g), Manamune ->
        # Muramana, Winter's Approach -> Fimbulwinter. The transform emits no
        # timeline event, so the replay still holds the pre-transform item at
        # almost the same price while the live API shows the transformed one.
        # Counting both keeps the two sides within a few gold; excluding both
        # would drop ~2,900g from one side only. The cheap granted items all sit
        # far below this threshold.
        return self.total(item_id) >= TRANSFORMED_MIN_GOLD

    def inventory_value(self, item_ids_with_counts, max_slots=MAX_ITEM_SLOTS):
        """[(itemID, count), ...] -> summed gold, capped at the 6 most valuable.

        A champion has six item slots plus a trinket, so the live API can never
        report more than six priced items. The replay can drift past that when a
        transform or an upgrade leaves no timeline event behind (Archangel's ->
        Seraph's emits nothing, so the replay keeps holding Archangel's). Taking
        the six most valuable on both sides bounds that drift instead of letting
        a stale item inflate the training side forever."""
        priced = []
        for item_id, count in item_ids_with_counts:
            if self.counts_toward_gold(item_id):
                priced.extend([self.total(item_id)] * max(1, int(count)))
        priced.sort(reverse=True)
        return sum(priced[:max_slots])


def player_item_gold(player, db):
    """Live /playerlist player object -> item gold, priced from Data Dragon."""
    return db.inventory_value(
        (it.get("itemID"), it.get("count", 1)) for it in player.get("items", [])
    )


def audit_live(db):
    """Compare the live API's `price` against Data Dragon `total` for every item
    currently held by all ten players. Settles whether `price` is the full cost
    without needing to watch purchases one at a time."""
    import requests
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    try:
        d = requests.get("https://127.0.0.1:2999/liveclientdata/allgamedata",
                         verify=False, timeout=5).json()
    except requests.exceptions.RequestException:
        print("No live game on 127.0.0.1:2999. Start one and rerun.")
        return

    seen, agree, disagree = {}, 0, []
    for p in d.get("allPlayers", []):
        for it in p.get("items", []):
            iid, live_price = it.get("itemID"), it.get("price", 0)
            if iid in seen:
                continue
            seen[iid] = True
            dd = db.total(iid)
            if dd == live_price:
                agree += 1
            else:
                disagree.append((iid, db.name(iid), live_price, dd, db.base(iid)))

    print(f"Data Dragon {db.version} vs live API, {len(seen)} distinct items held\n")
    print(f"  agree     : {agree}")
    print(f"  disagree  : {len(disagree)}")
    combine_matches = 0
    for iid, nm, live_price, dd, base in disagree:
        tag = "  <- == gold.base (combine cost)" if live_price == base else ""
        combine_matches += live_price == base
        print(f"    {iid:<6} {nm:<28} live={live_price:<6} total={dd:<6} base={base}{tag}")
    if db.misses:
        print(f"\n  not in Data Dragon at all: {sorted(db.misses)}")
    if not disagree and agree:
        print("\n-> live `price` IS the full item cost. Either source works,")
        print("   but keep using Data Dragon so training and inference match.")
    elif disagree:
        print(f"\n-> {len(disagree)} disagree, {combine_matches} of them exactly equal to")
        print("   `gold.base`. That confirms live `price` = combine cost, so it must")
        print("   NOT be summed as item value. Data Dragon `total` is the authority.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", help="Data Dragon version, e.g. 15.18.1")
    ap.add_argument("--game-version", help="match-v5 gameVersion, e.g. 15.18.634.2345")
    ap.add_argument("--refresh", action="store_true", help="ignore the cache")
    ap.add_argument("--update", action="store_true", help="download and cache, then exit")
    ap.add_argument("--show", metavar="ITEMID", type=int)
    ap.add_argument("--audit-live", action="store_true")
    args = ap.parse_args()

    try:
        db = ItemDB.load(version=args.version, game_version=args.game_version,
                         refresh=args.refresh or args.update)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"Could not reach Data Dragon: {e}")
        print("Needs internet access the first time; after that the cache is used.")
        return

    print(f"Data Dragon {db.version}: {len(db.data)} items cached in data/ddragon/")

    if args.show is not None:
        i = args.show
        print(f"\n  {i}  {db.name(i)}")
        print(f"    total {db.total(i)}   sell {db.sell(i)}")
        print(f"    tags  {sorted(db.tags(i)) or '-'}")
        print(f"    counts toward item gold: {db.counts_toward_gold(i)}")

    if args.audit_live:
        print()
        audit_live(db)


if __name__ == "__main__":
    main()
