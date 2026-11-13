# Lessons from a full playthrough (21 hours, ~60 blueprints delivered)

Each rule below cost at least one wrong blueprint or one wrong piece of advice. Read it before designing for
a base or diagnosing one. Game facts were verified on Factorio 2.0/2.1 by running the game, not from memory.

## Diagnosing a base

- **Statuses, not statistics.** "Made 701, used 939 iron plates" led to "build more furnaces" when every
  furnace was `full_output`: the 10-minute statistics include what the player spends by hand and say nothing
  about where items are. Use `save_advise.py`: it reads what each starved machine lacks and follows it
  upstream to DISTRIBUTION (made but not delivered), CAPACITY, NOT BUILT or POWER.
- **A save just loaded lies.** Belts connected minutes ago look empty and machines starved. Let it run
  (`save_advise.py --after`, `save_lua.py --after 1200`, `save_sim.py`) before concluding.
- **Fixing one bottleneck exposes the next** (copper -> plastic -> gas -> power -> steel). Audit the whole
  chain once and deliver one plan, not one patch per turn. Simulate "with the plan" against "without" for
  30-60 minutes and compare science per minute.
- **An empty research queue makes a simulation meaningless**: labs stop, demand disappears, everything backs
  up. Queue something with `--post-lua` first. Research that needs only some packs leaves the others piling
  up (`full_output` science assemblers are then normal).
- **Mostly idle is often correct.** Furnaces with output full, drills waiting for space: the limit is
  downstream. Don't add capacity where things are output-blocked.
- Check **power headroom** before adding anything (roboports charge in 1-5 MW bursts each) and **free
  logistic robots** before adding requester chests.

## Belts and inserters

- **Inserters fill one lane.** A belt loaded by one row of inserters (or by a drill row on one side) carries
  half a belt: 450/min on yellow. For a full belt load from both sides, or merge two belts with
  `blocks.tee()`. This halved a train unloading station until each half-station got its own belt.
- **Side-loading**: a belt entering another from the side fills only the near lane. But a belt entering the
  side of a belt with NOTHING behind it is just a curve and keeps both lanes: to put two items one per lane,
  feed the same tile from both sides (that is `tee`), never from one side plus the back.
- A belt whose two lanes carry different items must end in machines that use both, or the unused item
  blocks its lane and then the head (science packs entering the lab line sideways jammed research at 62 %).
- **Splitter priority**, seen on the map (verified): with `output_priority: "left"` a splitter facing south
  sends to the EAST output, facing north to the WEST, facing west to the SOUTH, facing east to the NORTH.
  A splitter is 2 tiles wide: when it faces west at x=-329.5 its second tile is x=-330.
- A route that starts at a splitter or belt output must leave in the same direction (an underground placed
  sideways receives nothing). Side-loading INTO an underground entrance does not work.
- Pasting a blueprint does not replace belts, furnaces or assemblers with other entities: use an upgrade
  planner, or Ctrl+Shift+click (super-forced), which also removes trees, rocks and belts in the way. A
  splitter pasted over a belt with plain Shift+click is NOT placed.
- Mining drills mine a 5x5 area: on the edge of another ore they mix it into the belt.
- Long belts take minutes to fill: give the lab test 10+ minutes (`P.spec["minutes"]`) before calling a
  chain slow.

## Fluids

- Pipes join anything beside them. Keep one free tile between different fluids (`pipe_route` does).
- The first pipe must leave the source in a straight line and the last must arrive on a pipe or on the
  machine's port tile; both ends plain pipes (an underground end connects on one side only).
- Verified ports, machine facing north (centre cx, cy): refinery inputs (cx-1, cy+3) water and (cx+1, cy+3)
  crude, outputs (cx-2, cy-3) heavy, (cx, cy-3) light, (cx+2, cy-3) gas; chemical plant inputs (cx-1, cy-2)
  and (cx+1, cy-2), outputs (cx-1, cy+2) and (cx+1, cy+2); pumpjack output (cx+1, cy-2); assembler fluid
  input (cx, cy-2); mining drill fluid ports (cx-1, cy), (cx+1, cy), (cx, cy+1) and drills pass it on;
  offshore pump is 1 tile wide for building although its box says 2.
- **Basic oil processing (2.0) is crude -> petroleum gas only**: no water, no by-products to back up. Prefer
  it (`blocks.refineries`) unless lubricant/light oil are needed; with advanced processing one full tank of
  heavy or light oil stops everything unless it is cracked or burnt.
- Refineries, not wells, are usually the limit: check `save_advise.py` (pumpjacks output-blocked = add
  refineries).

## Robots and logistics

- Requester/provider chests are right for LOW volume (mall, ammunition, modules, train fuel, a few hundred
  items/min). For plates and circuits in bulk use belts or trains: the network was saturated twice (0 of
  400 robots free) by bulk requests and by a new mall filling its chests.
- Every passive provider at the end of a mall machine needs a **bar** (slot limit) or the mall keeps
  crafting and eats the plates and circuits science needs. Turning the mall's old chests into providers
  must keep their bars.
- Chests robots use must be inside a **logistic** area (25 tiles from a roboport); the construction area
  (55) is not enough. Roboports form one network only if their logistic areas touch (centres <= 50 apart).
- Robots can only place what is in the network. Compare the bill of materials with the network's stock
  (`save_sim.py --robots` lists what is missing): pumpjacks, rails, radars and small poles were at 0 more
  than once. Add a logistic cell for the missing item to the same blueprint.
- Blueprints need **Construction robotics** in 2.0 (so do copy/paste and the import-string shortcut) unless
  a mod such as Early Blueprints is installed. Requester chests need **Logistic system**.

## Pasting in the player's world

- Always test on a copy of their save, twice: built instantly (does it work: `save_sim.py --bp`) and with
  their own robots (`--robots give`: is everything placeable, do they have the items, do trains start).
- **Absolute snapping**: the blueprint lands in the grid cell under the CURSOR. If the cell border crosses
  the base, a cursor on the wrong side pastes a phantom copy one cell away (it happened twice).
  `P.save(absolute=True)` now sizes the cell to contain the whole base; tell the player "cursor anywhere on
  your base, do not rotate" (a rotated absolute blueprint lands somewhere else in the cell).
- **Never-charted map**: the game silently places no ghosts there. `obstacles_from_save` records it,
  `find_free` avoids it and `P.save` fails if the plan enters it. Radars only chart 3 chunks around them
  continuously; far sectors come slowly.
- The player's character may stand on a ghost; walls of text of coordinates don't help them: give one
  landmark ("on your uranium mine") or "anywhere".
- Pasting a blueprint with trains twice adds a second set of trains at the stations.
- Landfill bridges give biters a land route into an island base. Don't add landfill across water that
  protects them without saying so. Defence is the player's call: one line of warning at most.
- A screenshot or any copy replaces the blueprint in their clipboard: say so every time.

## Trains (see blueprint-format.md for the fields)

- Lay rails with `P.rails()` (the game's planner); never compute curves by hand. Curved rails cover odd
  diagonal shapes: the planner reports the exact tiles.
- A one-way loop with in-line stations needs no junctions or chain signals and cannot deadlock while there
  are more signal blocks than trains. Legs 26 tiles apart = one U-turn. Trains queue behind each other, so
  throughput is set by the slowest stop: fine for 2-3 trains, add sidings beyond that.
- Loading chests need a small bar (4 slots = one wagon per 6 chests) or the first wagon's chests hoard the
  ore and the rest starve. Wagons load/unload in seconds with bulk inserters; the mine is the limit.
- Robots build locomotives and wagons, deliver the fuel request and the train goes to automatic by itself.
  Give every loop its own fuel source (a `drill_chests` coal mine + `refuel()` at the unloading stops).

## Working method

- Standard block -> `bp_make.py` (seconds). Chain of recipes -> `chain.py`. Fitted to their base -> a short
  generator with `obstacles_from_save`, `find_free/find_ore`, blocks, `link`, `power`, `roboports`,
  `save(absolute=True)`. If a piece is missing, add it to `blocks.py` with a lab test rather than writing a
  one-off 300-line generator.
- Blocks power themselves locally; call `P.power(pole)` once at the end to cover the rest and join the grid.
  Use a pole type the player has in stock.
- `blocks.line` with 3-4 ingredients and the split head can starve the right side's outer belt (one lane):
  if its test is LOW use `--no-split`, or `chain.py`. Inserter throughput measured in the lab: ~135 items/min
  per fast inserter on one item, ~110 mixed, ~40 for a long-handed one; size machine counts for that.
- When a blueprint "almost" builds (N-1 of N), find the missing entity: it is an overlap (a pole on a curve).
- Never state where items enter or leave, a keybind or an unlock from memory: trace it, read the config,
  query the data.
