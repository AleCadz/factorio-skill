#!/usr/bin/env python3
"""Example generator fitted into the player's base: a new stone mine + 6 electric furnaces into passive
provider chests, powered from their grid, inside their robot network. Tested in the lab AND on a copy of
their latest save (their terrain, buildings and electric grid). About 7 s.

  python3 layout_fit.py out.txt
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from layout import Plan, obstacles_from_save  # noqa: E402
import blocks  # noqa: E402

P = Plan()
obstacles_from_save(P, -480, -60, 60, 300)                 # their base in that area: taken tiles, ore, poles, roboports
x, y = P.find_ore("stone", 18 + 2, 7 + 2, near=(-250, 100))  # 6 drill pairs (18×7) plus a free margin
mine = blocks.mine(P, x + 1, y + 1, 6)
cx, cy = P.find_free(7, 18, near=(x + 25, y), avoid_ore=True)
cells = blocks.column(P, cx, cy, "stone-brick", 6, out="passive-provider-chest", bar=10)
P.link(mine["output"], cells["input"])
P.power("medium-electric-pole")                            # only where needed, joined to their grid
P.roboports()                                              # robots can build it; chests inside a logistic area
ok = P.save(sys.argv[1] if len(sys.argv) > 1 else "piedra.txt", "Piedra nueva",
            f"{mine['per_min']:g} stone/min -> bricks into 6 passive provider chests", absolute=True)
sys.exit(0 if ok else 1)
