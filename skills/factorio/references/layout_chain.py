#!/usr/bin/env python3
"""Example generator: an iron smelter feeding a gear line, chained with layout.Plan + blocks.

A custom design is this short: blocks for the standard parts, link/route/belt for the glue, power() for
whatever is left unpowered, save() to write it and run its test (prints VERDICT PASS|FAIL).

  python3 layout_chain.py [out.txt]
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from layout import Plan, N, E, S, W  # noqa: E402,F401
import blocks  # noqa: E402

P = Plan()
iron = blocks.smelter(P, 0, 0, n=24, ore="iron-ore")              # 450 iron/min, entry top-left, output top centre
gears = blocks.line(P, 16, 4, "iron-gear-wheel", n=2, split=False)  # 180 gears/min, needs 360 iron/min
# the smelter output goes to both gear input belts through a splitter; the test stops feeding them
P.link(iron["output"], [gears["inputs"][0][0], gears["inputs"][1][0]])
P.unexpect("iron-plate")                                           # it no longer leaves the blueprint
P.power()                                                          # joins the two blocks' poles
out = sys.argv[1] if len(sys.argv) > 1 else "iron-gears.txt"
ok = P.save(out, "Iron x24 + gears x2",
            f"Iron ore|coal enters at the top left; {gears['per_min']:g} gears/min leave at the top of the right block.")
sys.exit(0 if ok else 1)
