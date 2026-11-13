# Blueprint format (Factorio 2.0) and design data

Everything here was verified on 2.0.77 (data.raw dump and simulations), base game without Space Age. If the
game version changes, re-check with `scripts/data.py`. When you discover a new field with
`scripts/bp_probe.py`, add it here.

## Encoding

`string = "0" + base64(zlib(json))`. `bp.py decode/encode` does it. Root:

```json
{"blueprint": {
  "item": "blueprint", "label": "Steel x12",
  "description": "free text (shown on hover)",
  "icons": [{"signal": {"type": "item", "name": "steel-plate"}, "index": 1}],
  "entities": [ ... ], "wires": [ ... ], "tiles": [ ... ],
  "version": 562949958467584
}}
```

- `version` = `(major<<48) | (minor<<32) | (patch<<16)`. For 2.0.77: `bp.version_int(2, 0, 77)`.
- A book is `{"blueprint_book": {"label", "blueprints": [{"index": 0, "blueprint": {...}}, ...], "active_index", "version"}}`.
- 1.1 blueprints import fine into 2.0: the game converts directions. No need to translate them unless they
  use entities that no longer exist (`filter-inserter` and `stack-filter-inserter` are gone; filtering is
  now an option on the fast and bulk inserters).

## Entities

```json
{"entity_number": 1, "name": "inserter", "position": {"x": 1.5, "y": 0.5}, "direction": 12}
```

- `entity_number`: 1..N, unique. Wires refer to entities by it.
- **Position = centre** of the entity, in tiles, y pointing down.
  - Odd sizes (1×1, 3×3): coordinates end in `.5`.
  - Even sizes (2×2): integer coordinates. A stone furnace covering tiles x=2..3, y=0..1 goes at `(3, 1)`.
  - Splitter (2×1 facing N/S): integer `x`, `y` ending in `.5`. Facing E/W, the other way round.
- **Directions in 2.0: 16 values. N=0, E=4, S=8, W=12.** In 1.1 there were 8 (N=0, E=2, S=4, W=6). Using
  the old numbering in a 2.0 blueprint, an inserter meant to face east (2) ends up facing NNE.
- **Inserters: `direction` points to the side they PICK UP from.** They drop on the opposite side. To take
  from a belt to the west and put into a furnace to the east, `direction = 12` (W). Verified: facing N the
  prototype has `pickup_position [0,-1]`. Long-handed inserters pick up 2 tiles away and drop 2 away.
- An inserter dropping onto a belt puts items on the **far lane**. Two rows of inserters, one on each side
  of the same belt, fill both lanes.
- Underground belts: `"type": "input"` (entrance) or `"output"` (exit), both with the same `direction`
  (travel direction). Reach: yellow `max_distance` 5 (4 tiles underground), red 7.
- `"quality"` is optional on any entity. Without the DLC everything is normal, so leave it out.
- Inserter filters (verified with `bp_probe.py`): `"use_filters": true, "filters": [{"index": 1,
  "name": "coal", "quality": "normal", "comparator": "="}]`. For a blacklist, `"filter_mode": "blacklist"`
  (not verified).
- Splitter (verified): `"output_priority": "left"|"right"`, `"input_priority"` and `"filter": {"name":
  "coal"}`. Left and right are seen in the direction of travel: on a splitter facing south
  (`direction` 8), `"left"` is the east side. Verified by simulation.
- Assembler (verified): `"recipe": "iron-gear-wheel", "recipe_quality": "normal"`.

## Wires

```json
"wires": [[1, 5, 7, 5], [3, 1, 9, 1]]
```

Each wire is `[entity_a, connector_a, entity_b, connector_b]`. Connector **5 = pole copper** (verified).
Red circuit = 1 and green = 2, following `defines.wire_connector_id`; combinators also use 3 and 4 for their
output side. When a player builds poles by hand the game connects them automatically, but including the
copper wires does no harm and makes the blueprint robot-ready.

## Design data (2.0 base)

| Thing | Value |
|---|---|
| Yellow / red / blue belt | 15 / 30 / 45 items/s per belt (half per lane) |
| Stone furnace | speed 1, 90 kW of fuel, 2×2 |
| Steel / electric furnace | speed 2 (2×2) / 2 (3×3, 180 kW electric) |
| Assembling machine 1 / 2 / 3 | speed 0.5 / 0.75 / 1.25; 3×3 |
| Electric / burner mining drill | 0.5 / 0.25 mining speed |
| Small pole | powers a 5×5 square (radius 2.5); wire reach 7.5 |
| Medium pole | 7×7 (3.5); wire reach 9 |
| Big pole | powers nothing; wire reach 32 |
| Substation | 18×18 (9); wire reach 18 |
| Coal | 4 MJ, so a stone furnace burns 0.0225 coal/s (1.35/min) |
| Iron / copper plate, stone brick | 3.2 s; at speed 1 that is 18.75/min per furnace |
| Steel | 16 s, 5 iron plates → 1 steel; at speed 1, 3.75/min per furnace |

Formula: items/min per machine = `60 × result_amount / (recipe_time / crafting_speed)`. Always take recipes
from `data.py recipe`, never from memory.

A full yellow belt of ore feeds 48 stone furnaces (900/min ÷ 18.75). Many popular smelting books are sized
like that.

## Useful patterns

- **Mixed input on one belt**: one lane with the ingredient and the other with fuel. Inserters take whatever
  the machine needs from both lanes and put fuel only into the fuel slot.
- **Moving everything onto one lane**: a **side-load**. If a belt ends perpendicular against the side of
  another, everything it carries goes onto the near lane of the second one. Splitters, on the other hand,
  keep lanes as they are.
- **From "iron on both lanes" to "iron | coal"**: side-load iron from one side and coal from the opposite
  side onto the same belt.
- **Chainable blueprints**: an underground belt entering on one side and leaving on the other passes a
  resource (for example coal) to the next copy placed alongside.
- **Two products on one belt, one per lane**: an inserter drops one (it goes to the lane far from the
  inserter) and the other is side-loaded from the opposite side (it goes to the lane on that side).
  Example: green circuits and copper together for an advanced-circuit block.
- **Cable assembler between two assemblers**: one copper-cable machine in the middle with inserters into
  both consumers (green or red circuits). No need to belt the cable.
- **Dense blueprints**: check that poles from different blocks reach each other (`bp_sim` warns about
  separate electric networks) and that no internal belt extends into another input (`bp_sim` warns when it
  places fewer ghosts than the blueprint has entities).

## Fluids (verified by simulation)

- **About 320 tiles per fluid system** (`default_pipeline_extent`). A longer pipeline carries nothing.
  Break it with a **pump** (1×2: in at the back, out at the front) at most every ~300 tiles. A pump needs an
  engine unit, and engines can't be hand-crafted.
- Underground pipe: at most **10** tiles between ends (11 doesn't connect). `direction` is the side of the
  above-ground connection.
- Oil refinery with basic oil processing: only the **bottom-right** port (crude in) and the **top-right**
  port (petroleum gas out) are used in the default orientation. For other recipes or orientations, ask the
  game with `bp_probe.py` and `fluidbox.get_filter()`.
- Chemical plant making sulfur, facing south: water enters on the right.
- Big pole to medium pole: the wire reaches 9 (the shorter of the two).
- Anything that depends on terrain (pumpjacks, mining drills, offshore pumps) must be tested with
  `scripts/save_bp_test.py --align N=x,y` on a copy of the player's save.


## Trains (verified 2.1.24)

- Rails: `straight-rail`, `curved-rail-a`, `curved-rail-b`, `half-diagonal-rail`; positions on the 2-tile grid
  (odd coordinates for straight rails), 16 directions. Generate them with `scripts/rails.py`, not by hand.
- `train-stop`: 2x2, 2 tiles to the right of the rail centre (seen in the direction of travel), `direction` =
  direction of travel (omitted for north), `"station": "Name"`. A locomotive stops with its centre 3 tiles
  behind the stop; wagon centres follow every 7 tiles (each wagon covers centre-3..centre+2: 6 inserters).
- Rolling stock: `{"name": "locomotive", "position", "orientation": 0|0.25|0.5|0.75, "items": [fuel request]}`,
  `cargo-wagon` the same without items. Fuel request: `{"id": {"name": "coal"}, "items": {"in_inventory":
  [{"inventory": 1, "stack": 0, "count": 20}]}}`.
- Blueprint-level: `"schedules": [{"locomotives": [entity_number], "schedule": {"records": [{"station": "A",
  "wait_conditions": [{"type": "full", "compare_type": "or"}]}, ...]}}]` and `"stock_connections": [{"stock": n,
  "front": m, "back": k}, ...]` (front/back omitted at the ends of the train).
- Signals for one-way track go on the right-hand side: `LuaRailEnd.out_signal_location` of the end the train
  leaves through.
