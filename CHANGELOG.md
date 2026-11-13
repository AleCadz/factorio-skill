# Changelog

## 1.3.0 — 2026-11-13

Everything learned from one complete 21-hour world, turned into tools so the next world goes faster.

- `save_advise.py`: what is actually holding a factory back. Follows each starved machine's missing
  ingredient upstream to a root cause (distribution, capacity, by-product blocked, not built, power)
  instead of reading production statistics, which misled the audit more than once. Limiting science pack,
  robot and power headroom, ore runway, over-built recipes, next steps.
- `save_map.py`: map survey (ore patches and oil fields by distance, nests, water, charted against
  generated area) as text, PNG with coordinates, tile-level ASCII or JSON; `--new-map --seed N` judges a
  seed before playing.
- `chain.py`: compiles a multi-recipe chain (a science pack from plates) into one tested blueprint.
- Trains: `Plan.rails()` lays track with the game's own rail planner (`rails.py`), `blocks.station()`,
  `blocks.refuel()`, `Plan.train()` (schedules, fuel requests, stock connections in the blueprint).
  `references/layout_train.py`: a one-way loop with two ore trains, mines, smelters, its own mall and
  roboport chain, built entirely by robots.
- Oil and glue blocks: `pumpjack`, `refineries`, `plastic`, `tee` (lane merge), `chest_tail`,
  `drill_chests`; `pair_line` takes three belt items (red circuits with direct cable insertion).
- `save_sim.py --robots [give]`: places ghosts as the game does and lets the save's own robots build;
  reports ghosts left, items the network lacks and train state. Revived blueprints now get their item
  requests (train fuel) and report ghosts that vanished because entities overlap. Fluids in `--items` no
  longer abort the run.
- Safer pasting: absolute-snapping cells are sized to contain the whole base (a cursor on the wrong side
  of a 512 cell border pasted phantom copies), and `Plan.save` fails when a plan enters map the player
  never charted (the game silently skips those ghosts). `find_free` avoids such areas.
- `Plan.power()` is ~30x faster on big plans and blocks no longer string poles back to a distant grid;
  `Plan.raw()`, `Plan.block_blueprint()` (design on top of a blueprint that is not built yet).
- Hardening before the public release: the portal token is passed to curl on stdin (not visible in the
  process list) and only to validated mods.factorio.com URLs; installing a mod no longer deletes other
  mods that share its name prefix (`foo` vs `foo_bar`), and the old version is removed only after the new
  one passes its sha1; text from blueprints can no longer break out of the Lua literal it is embedded
  in; `bp.py fetch` only accepts https factoriobin links; `rails.py` no longer evals its argument.
  Blueprint strings are decompressed with a size limit (512 MB) and downloads are capped; caches live in a
  private per-user folder instead of predictable names in the shared temp folder; the player's
  `mod-settings.dat` is copied into the throwaway instance, not linked.
  Requests identify themselves as `factorio-skill` instead of a browser, and temp folders are removed.
- `references/lessons.md` (design and diagnosis rules, each from a real mistake) and
  `references/playbook.md` (guiding a new world stage by stage).

## 1.2.0 — 2026-11-09

Blueprint design in minutes instead of an hour: measured on past sessions, ~80 % of the time went into
hand-writing generators (coordinates, poles, which belt to feed) and re-simulating them 3-10 times.

- `bp_make.py`: one command builds, simulates and judges a standard block (`smelter`, `line` for any
  solid recipe with 1-4 ingredients, `line --helper` for direct insertion such as green circuits), as a
  single blueprint or a book of sizes. Prints inputs, output, measured vs expected rate, materials,
  research in the player's language and `VERDICT PASS|FAIL` in 2-5 s. Also `rate` (production tree) and
  `test` (re-test any layout-built blueprint).
- `layout.py`: entities placed by top-left tile with real sizes from the game data, collision checks,
  belt runs, undergrounds, an A* belt router, `link()` to chain blocks, automatic poles and wires, the
  player's base as obstacles (`obstacles_from_save`) and absolute-grid output tested on a save copy.
- `blocks.py`: parametric, simulation-tested smelter, assembler line and helper-pair line.
- `bp_sim.py`: `--spec` feeds and empties belts by entity (no more guessing the head order); powers
  compact designs with no free spot next to a pole; `run()` is importable.
- Fitting into the player's base: `obstacles_from_save` also reads ore and roboports; `find_ore`,
  `find_free`, `blocks.mine` (only where the game accepts a drill), `blocks.column` (cells into chests),
  `pipe_route` (pipe-to-ground, keeps fluids apart), `roboports()` (construction and logistic coverage),
  `power()` joins new poles to their grid. Fitted blueprints are tested in the lab and on a save copy with
  their real grid (no extra power).
- `tests/test_layout.py` (`--sim` also simulates the presets).

## 1.1.0 — 2026-11-07

- `save_diag.py`: locates problems in a save (isolated electric networks and the closest powered pole,
  labs missing packs, stopped machine clusters, leftover ghosts, robot coverage and items for ghosts).
- `save_trace.py`: follows a belt downstream or upstream through undergrounds and splitters.
- `save_sim.py`: runs the player's base N minutes ahead, optionally pasting blueprints, finishing the
  ghosts already placed or applying pending orders; rates, research, power and losses.
- `save_lua.py`: run any Lua snippet on a copy of a save (importable `run()`).
- Works with Factorio 2.1 (API renames in bp_sim, save_probe and save_bp_test).

## 1.0.0 — 2026-10-20

First public release.

- Blueprint tools: decode, encode, summary, ASCII render, extract from books, factoriobin download,
  clipboard copy with read-back check, PNG map of whole bases.
- `bp_sim.py`: builds a blueprint in an isolated headless Factorio, feeds its belts, empties its outputs
  and reports machine states, recipes per minute, inserter and power problems.
- Save tools: `save_probe.py` (state of your game), `save_export.py` (export part of your base as a
  blueprint), `save_bp_test.py` (try a blueprint on a copy of your map, terrain included).
- `data.py` (recipes, technologies, entities, localized names from the game's own data) and `mods.py`
  (search, info, install with dependencies and sha1 check, enable/disable).
- Works with standalone, Steam and installer versions on Linux, Windows and macOS; paths auto-detected
  by `fenv.py`.
- Per-player notes file outside the skill so updates never erase what Claude learned about your base.
