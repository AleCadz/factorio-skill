# Example: a modular steel smelter

`steel_modular.py` is a blueprint generator of the kind the skill writes for you. It builds the entity list
in code (getting coordinates right by hand is where blueprints usually break), encodes it with `bp.py` and
is then checked in a headless game with `bp_sim.py` before it reaches the player.

```sh
S=skills/factorio/scripts
python3 examples/steel_modular.py book > examples/steel-modular-book.txt
python3 $S/bp.py summary examples/steel-modular-book.txt
python3 $S/bp.py render examples/steel-modular-book.txt --index 4
python3 $S/bp_sim.py examples/steel-modular-book.txt --index 2 --research steel-processing \
    --feed iron-plate --feed "|" --feed "|" --feed "|" --feed coal --sink 1,3 --minutes 6 --report-every 60
```

The x24 blueprint has five belt heads: input 1 is your iron belt, input 5 the coal, and 2–4 are internal
belt starts, skipped with `"|"`. Of the four belt tails only 1 (spare iron continuing on) and 3 (steel) are
real outputs; 2 and 4 are the dead ends of the furnace input belts, so `--sink 1,3` lets those fill up as
they would in game.

Result on Factorio 2.0.77: `build_failures=0`, `misaligned_or_floating_inserters=0`, all 24 furnaces
`working`, **91 steel/min** out of output 3 on both lanes, and **452 iron/min** passing through output 1 —
the calculation says 90 and 450.

`steel-modular-book.txt` is the generated book, ready to paste into the game.
