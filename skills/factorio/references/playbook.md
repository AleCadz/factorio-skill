# Playbook: guiding a new world from the first minute

For a player who wants to BUILD, not design: at every stage hand them a tested blueprint (or 3 concrete
steps) and say what comes next. Costs and unlocks below were read from the game data (base 2.1, no DLC);
re-check with `data.py tech <name>` when mods are involved. Ratios are never quoted from memory: every
command prints them.

## Every time the player comes back

1. `save_advise.py` (root causes, limiting science pack, robots/power/ore headroom, next steps).
2. Match the save to a stage below (research done is the best marker: `save_probe.py` lists it).
3. Deliver the ONE thing that stage needs, tested; update the notes file (stage, what was delivered, where).
4. Keep the research queue full: name the next 3-5 technologies in the player's language (`data.py name`).

Keep a small table in the notes file: where each block is (coordinates), the build corridor chosen, spare
belt ends ("tails") and free areas. Finding these again each session is what made later designs slow.

## Stage 0 — before the first building

- Survey: `save_map.py` on their first save (or `--new-map --seed N` to judge a seed): nearest iron, copper,
  coal, stone, water and oil, cliffs, nests and how close they are, whether the start is an island.
- Decide with the player (one question, not ten): compact base with a **build corridor** (a straight strip
  ~40 tiles wide kept free, blocks hang off both sides) and where it runs. Record it. The previous world had
  no corridor: blocks ended up scattered, joined by long belts, and every new design had to route around
  them.
- Mods: check `mods.py list`. Without Early Blueprints (or similar) blueprints, copy/paste and "import
  string" only unlock with Construction robotics (red+green+blue science): until then give short manual
  steps and tiny builds, not blueprints.

## Stage 1 — burner to electricity (automation science)

- Marker: no `automation` research yet / fewer than ~10 electric drills.
- Deliver: ore mines (`blocks.mine` fitted with `find_ore`, see `layout_fit.py`), smelting columns
  (`bp_make.py smelter --ore iron-ore --n 24`, copper the same; plates leave at the top centre, two full
  lanes), red science (`chain.py --item automation-science-pack --per-min 45 --inputs iron-plate,copper-plate`).
- Steam power has no preset: 1 offshore pump, boilers and 2 steam engines per boiler; `layout.offshore_spots`
  finds where a pump is allowed, then test on their save with `save_bp_test.py`.
- Research order: Automation, Logistics, Electronics (labs), Steel processing (50 red), Logistic science
  pack (75 red).

## Stage 2 — green science, steel, a first mall

- Marker: `logistic-science-pack` researched.
- Deliver: green science (`chain.py --item logistic-science-pack --per-min 45 ...`), steel
  (`bp_make.py smelter --ore iron-plate`), walls/ammunition only if `save_probe` shows nests in the
  pollution cloud (one line of warning; defence is theirs unless they ask).
- Mall before robots: `blocks.column`/`line` into chests with a bar, for belts, undergrounds, splitters,
  inserters, assemblers, poles, pipes. Feed it from the plate lines with a priority splitter that favours
  science.
- Research: Electric energy distribution 1 (medium/big poles, 120 red+green), Logistics 2 (200), Engine,
  Fluid handling, Oil gathering. **Oil processing has no science cost: it unlocks when the first crude oil
  is pumped**, so place one pumpjack early.

## Stage 3 — oil, plastic, blue science

- Marker: `oil-processing` researched; no `chemical-science-pack` yet.
- Deliver: `blocks.pumpjack` + `blocks.refineries` (basic processing: gas only) + `blocks.plastic`; sulfur
  needs water and gas (generator with `pipe_route`); red circuits (`pair_line advanced-circuit` with the
  copper-cable helper); engines; then blue science (`chain.py --item chemical-science-pack --per-min 30
  --inputs iron-plate,copper-plate,steel-plate,plastic-bar,sulfur`).
- Chemical science pack technology: 75 red+green, after Advanced circuit and Sulfur processing.
- Typical wall here: petroleum gas. `save_advise.py` says whether to add refineries (pumpjacks
  output-blocked) or wells.

## Stage 4 — robots, the turning point

- Research (red+green+blue): Electric engine -> Robotics -> **Construction robotics** (100) and Logistic
  robotics. Needs lubricant (heavy oil: one refinery on advanced processing with the rest handled, or coal
  liquefaction later), batteries, electric engines, flying robot frames.
- Deliver, in this order: a robot factory, a roboport chain covering the whole base as ONE network
  (`P.roboports()`; chests inside logistic range), repair packs. From here every blueprint is built by
  robots: always test with `save_sim.py --robots` and add cells for items the network lacks.
- Requester chests (logistic mall with `blocks.cells`) need **Logistic system: 500 packs including utility
  science**. Until then malls are belt-fed with passive provider outputs.

## Stage 5 — purple and yellow science, modules

- Production science: 100 red+green+blue, after Productivity module, Advanced material processing 2
  (electric furnaces, 250 r+g+b) and Railway. Needs rails, electric furnaces, productivity modules.
- Utility science: 100 r+g+b, after Robotics, Processing unit and Low density structure. Needs sulfuric
  acid for processing units.
- The limiter is almost always advanced circuits <- plastic <- gas, then flying robot frames. Build these
  as dedicated factories (belts in, belts out), not as requester-fed cells.
- Upgrade in place with upgrade planners (assembler 1 -> 2, stone -> steel -> electric furnaces, belts only
  where a line is actually full).

## Stage 6 — trains, outposts, nuclear, rocket

- Trains: Railway 75 red+green (after Logistics 2 and Engine), Automated rail transportation 200. Worth it
  when the next ore is hundreds of tiles away or the starting patches have < 10 h left
  (`save_advise.py` prints the runway). Use `layout_train.py` as the template: one-way loop, in-line
  stations, own fuel and mall, roboport chain so robots build it; stay inside charted map.
- Nuclear power: 800 r+g+b after Uranium processing (which unlocks by mining uranium ore; the mine needs
  sulfuric acid). Kovarex needs 40 U-235 to start: plan hours, or hand-feed.
- Rocket silo: 1000 of red, green, blue, purple and yellow.
- Ask what the player enjoys before optimizing for the rocket: this player wanted production, uranium and
  expansion first.

## What to hand over with every blueprint

- Where to put the cursor (one landmark), "do not rotate", which click (Ctrl+Shift+click when something
  must be removed or replaced).
- Inputs (side, lane, per minute), outputs, measured rate, materials against what they have, research
  still needed, and what was NOT tested.
- That it is in the clipboard and what replaces the clipboard.
