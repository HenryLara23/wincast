# Live Client Data API — verified schema

Base: `https://127.0.0.1:2999/liveclientdata` (self-signed cert; `verify=False` or use `riotgames.pem`)

Verified against Riot's Live Client Data docs and the typed client bindings
(`pkg.go.dev/github.com/Jonchun/lige/liveclientdata`). Run `lolmodel/scripts/capture_live.py`
during a game to re-verify against your actual patch — Riot adds fields over time.

## Endpoints

| Endpoint | Covers | Usable for the model? |
|---|---|---|
| `/playerlist` | all 10 players | **yes** |
| `/eventdata` | all objectives + kills, game-wide | **yes** |
| `/gamestats` | game-level | **yes** |
| `/allgamedata` | everything above in one call | yes (one request instead of three) |
| `/activeplayer` | **you only** | **no — asymmetric** |
| `/activeplayerabilities`, `/activeplayerrunes`, `/activeplayername` | you only | no |
| `/playerscores?riotId=`, `/playeritems?riotId=`, `/playersummonerspells?riotId=`, `/playermainrunes?riotId=` | one named player | redundant (same data as `/playerlist`) |

## `/playerlist` — one object per player

```
championName      string    "Ahri"
rawChampionName   string    "game_character_displayname_Ahri"
summonerName      string    legacy; still populated alongside riotId
riotId            string    "Name#TAG"      (newer patches)
riotIdGameName    string    "Name"          (newer patches)
riotIdTagLine     string    "TAG"           (newer patches)
team              string    "ORDER" (blue) | "CHAOS" (red)
position          string    "TOP"|"JUNGLE"|"MIDDLE"|"BOTTOM"|"UTILITY"; "NONE" outside SR draft
skinName          string    observed on current patches
rawSkinName       string    observed on current patches
level             int       1-18
isDead            bool
respawnTimer      float     seconds remaining, 0.0 when alive
isBot             bool
skinID            int
items             []Item
runes             { keystone, primaryRuneTree, secondaryRuneTree }   (each: id, displayName, rawDescription...)
summonerSpells    { summonerSpellOne, summonerSpellTwo }
scores            { assists:int, creepScore:int, deaths:int, kills:int, wardScore:float }
                  !! creepScore is QUANTISED TO MULTIPLES OF TEN -- all 7,570
                  !! readings in the 2026-09-19 capture. It also diverged from
                  !! match-v5's own end-of-game totals by 37 and 86 for two of the
                  !! ten players. Unusable as a model feature; dropped in spec 1.2.0.
```

### Item object
```
itemID          int
displayName     string
price           int     COMBINE cost, NOT the item's value -- see below
count           int     stack size (matters for consumables/wards)
consumable      bool
slot            int     0-6 (6 = trinket)
canUse          bool
rawDescription  string
rawDisplayName  string
```

**`price` is the combine cost.** Measured on a live game: Lost Chapter (3802)
reported `price: 250`, while the item costs 1200. 250 is its combine cost, and
matches Data Dragon's `gold.base`. Basic components have no build path, so for
them combine cost == total and the field looks correct -- which is why the bug
hides early and grows as builds complete. Price by `itemID` through
`items_db.py` instead.

## `/gamestats`
```
gameMode     string   "CLASSIC", "ARAM", ...
gameTime     float    seconds since game start
mapName      string   "Map11"
mapNumber    int      11 = Summoner's Rift
mapTerrain   string   "Default"|"Infernal"|"Mountain"|"Ocean"|"Cloud"|"Hextech"|"Chemtech"
```
`mapNumber` 11 = Summoner's Rift, 12 = Howling Abyss. Gate the model on this:
the objective features (dragon/herald/baron) only exist on 11, and non-SR modes
also change starting gold and starting level.
No `winningTeam`, no `gameState`, no `isPaused`.

## `/eventdata`
Returns `{"Events": [...]}` — cumulative from game start, so a single poll gives
the full objective history. Base fields on every event:
```
EventID    int
EventName  string
EventTime  float   seconds
```
Conditional fields: `KillerName`, `VictimName`, `Assisters` (list of names),
`DragonType` ("Fire"|"Water"|"Earth"|"Air"|"Hextech"|"Chemtech"|"Elder"),
`Stolen` ("True"/"False" string), `TurretKilled`, `InhibKilled`, `Recipient`,
`KillStreak`, `Acer`, `AcingTeam`, `Result`.

Event names seen in practice: `GameStart`, `MinionsSpawning`, `FirstBrick`,
`FirstBlood`, `ChampionKill`, `Multikill`, `Ace`, `TurretKilled`, `InhibKilled`,
`InhibRespawningSoon`, `InhibRespawned`, `DragonKill`, `HeraldKill`, `BaronKill`,
`GameEnd`, and **`HordeKill`** (voidgrubs -- confirmed 2026-09-19, matching the
timeline's `HORDE` monster type). Objective events also carry
`Stolen` ("True"/"False") and an `Assisters` list. `KillerName` is the bare game
name without the tag line, which `riotIdGameName` matches.
Confirm the exact set on your patch by capturing a game with `lolmodel/scripts/capture_live.py`.

Structure names encode the owner, not the killer -- and the spelling has changed.
A capture on 2026-09-19 (patch 16.18) gives:

```
Turret_TOrder_L1_P3_1242677625_0     Inhib_TOrder_L1_P1_2786523670_0
Turret_TChaos_L0_P3_511845594_0
```

Older patches (and earlier versions of this file) give `Turret_T1_C_05_A` and
`Barracks_T1_L1`. **Both spellings must be accepted**: matching only the old one
silently skipped every structure in that game, leaving towers and inhibitors at
zero for the whole match with nothing raised. `live_features.structure_owner()`
scans the name for any of `TOrder`/`T1` (blue) or `TChaos`/`T2` (red) and reports
a name it cannot parse rather than guessing.

`TOrder`/`T1` = ORDER = blue. A destroyed blue turret is a point **for red**.

## `/activeplayer` — do not use
```
summonerName, riotId, riotIdGameName, riotIdTagLine, level, teamRelativeColors
currentGold      float    <-- the only live gold figure, and only for you
championStats    { abilityPower, armor, attackDamage, currentHealth, maxHealth,
                   moveSpeed, magicResist, resourceValue, ... ~28 fields }
abilities        { Q,W,E,R,Passive: abilityLevel, displayName, ... }
fullRunes        { keystone, primaryRuneTree, secondaryRuneTree, generalRunes[], statRunes[] }
```
Rich, but only for the client running the script. Any feature built from it is
unavailable for the other nine players and would train the model on your seat,
not on the game state.

## What the live API does NOT give you

No gold. No gold earned, no gold per minute, no XP, no damage dealt or taken,
no healing/shielding, no wards placed/killed as counts, no per-minute deltas,
no vision score breakdown. `scores.wardScore` is a single opaque float and has
no clean post-game equivalent.

These all exist in the **post-game** match-v5 timeline, which is why the training
set has to be deliberately restricted — see `README.md`.

## Item pricing

The live API does expose `items[].price`, but this project prices items by
`itemID` through Data Dragon instead (`items_db.py`). The match-v5 timeline
stores only item IDs, so a price table is needed for training regardless, and
using one table on both sides keeps the two feature extractors comparable.
Data Dragon `gold.total` is the full cost, `gold.sell` the 70% refund, and
`tags` distinguish Consumable and Trinket more reliably than the live
`consumable` flag.
