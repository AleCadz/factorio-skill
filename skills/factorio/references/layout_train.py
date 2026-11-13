#!/usr/bin/env python3
"""Example generator: a one-way rail loop with two ore trains, built by robots. It shows every train call:
Plan.rails() (the game's own rail planner), blocks.station()/refuel(), Plan.train(), plus the pieces a remote
outpost needs (mine, smelter fed by two single-lane belts, chest_tail, drill_chests for fuel, a roboport chain
linked to the base network, big poles). The coordinates belong to one particular save: copy the structure, not
the numbers. Find a route with `save_map.py --ascii` and `rails.py` first (trace until 'blocked' is empty).

  python3 layout_train.py save.zip out.txt

Verified on that save: 1750 entities, robots build all of it including the trains, which switch to automatic
by themselves; ~850 plates/min per metal after the first trip.
"""
import math, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from layout import Plan, obstacles_from_save, N, E, S, W, ent_info  # noqa: E402
import blocks  # noqa: E402

SAVE, OUT = sys.argv[1], sys.argv[2]
MP = "medium-electric-pole"
XE, XW, YT = -403, -429, -291           # track centres: east leg (north-bound), west leg (south-bound), top (east-bound)

P = Plan()
obstacles_from_save(P, -480, -380, -240, 130, save=SAVE)   # also reads which chunks were never charted

# One closed loop, no junctions: legs 26 tiles apart = the width of a U-turn (L8). Signals (!) on straight track
# only, never where station inserters or drills stand next to the rail.
loop = P.rails((XE, -13, 0),
               "S5 ! S23 ! S23 ! S29 ! S30 ! S22 R4 "      # up the east leg
               "S13 ! S42 ! S6 L8 "                        # east along the top, U-turn
               "S5 ! S35 ! S34 L4 "                        # back west, down
               "S22 ! S30 ! S30 ! S20 ! S22 ! S22 ! L8")   # down the west leg, U-turn into the start
assert loop["closed"]

# Blocks power themselves; the long link to the base grid is made once, at the end.
# ---- loading: the mine's belt runs straight into the station's belt line (both lanes full)
cu = blocks.station(P, "Cobre Norte", N, XE, -113, -1, load=True)
blocks.mine(P, -412, -148, 13, d=S, pole=MP, ore="copper-ore")       # one tile off the track: room for poles
P.belt([(-409, -109), (cu["belt_line"], -109), (cu["belt_line"], cu["tiles"][-1])])
fe = blocks.station(P, "Hierro Norte", E, YT, -325, +1, load=True)
blocks.mine(P, -322, YT + 2, 13, d=W, pole=MP, ore="iron-ore")
P.belt([(-323, YT + 5), (-323, fe["belt_line"]), (fe["tiles"][0], fe["belt_line"])])


# ---- unloading: inserters fill ONE lane, so each half of the station feeds one side of the smelter
def unload(name, stop_at, ore):
    st = blocks.station(P, name, S, XW, stop_at, +1, load=False, bar=16)
    ys, bx = st["tiles"], st["belt_line"]
    y0 = ys[0]
    sm = blocks.smelter(P, bx + 2, y0, 24, ore=ore, furnace="electric-furnace", pole=MP, split=False)
    left, right = sm["cols"]["L0"], sm["cols"]["R0"]
    P.belt([(bx, ys[11]), (bx, y0 - 1), (left, y0 - 1)], end=S)
    P.belt([(bx, ys[12]), (bx, y0 + 36), (right + 1, y0 + 36), (right + 1, y0 - 1), (right, y0 - 1)], end=S)
    blocks.chest_tail(P, sm["cols"]["C"], sm["top"] - 1, N, 6, bar=24)   # plates into 12 provider chests
    blocks.refuel(P, st)
    return st


dcu = unload("Descarga Cobre", -15, "copper-ore")
dfe = unload("Descarga Hierro", -59, "iron-ore")

# ---- what the network lacks: coal for fuel, stone for rails, and a mall for the train parts
def ore_spot(ore, near):
    x, y = P.find_ore(ore, 5, 8, near=near, min_fill=0.75)
    return x, y + 1


blocks.drill_chests(P, *ore_spot("stone", (-409, -6)))
blocks.drill_chests(P, *ore_spot("coal", (-447, -306)))
blocks.cells(P, -447, -57, [("engine-unit", 1), ("locomotive", 1), ("cargo-wagon", 1), ("rail", 6),
                            ("train-stop", 1), ("rail-signal", 1), ("rail-chain-signal", 1), ("radar", 1)], minutes=0.2)

# ---- roboports in a chain (each within 48 tiles of its parent so the logistic areas touch), big poles along it
ROBO = [((-405, 35), -1), ((-417, -8), 0), ((-439, -52), 1), ((-440, -98), 2), ((-419, -144), 3), ((-419, -190), 4),
        ((-419, -236), 5), ((-420, -282), 6), ((-376, -305), 7), ((-330, -305), 8), ((-284, -305), 9), ((-437, -310), 7)]
ext = min(P.ext_roboports, key=lambda p: math.dist((p["position"]["x"], p["position"]["y"]), ROBO[0][0]))
robo = []
for (rx, ry), par in ROBO:
    px, py = (ext if par < 0 else robo[par])["position"].values()
    spots = [(math.dist((x, y), (rx, ry)), x, y) for x in range(rx - 16, rx + 17) for y in range(ry - 16, ry + 17)
             if max(abs(x + 2 - px), abs(y + 2 - py)) <= 48
             and not any((x + i, y + j) in P.occ for i in range(-1, 5) for j in range(-1, 5))]
    _, x, y = min(spots)
    robo.append(P.add("roboport", x, y))
reach = ent_info("big-electric-pole")["maximum_wire_distance"] - 5
pole0 = min(P.ext_poles, key=lambda p: math.dist((p["position"]["x"], p["position"]["y"]), ROBO[0][0]))
chain = [tuple(pole0["position"].values())] + [tuple(r["position"].values()) for r in robo[:-1]]
for (ax, ay), (bx, by) in zip(chain, chain[1:]):
    n = max(1, math.ceil(math.dist((ax, ay), (bx, by)) / reach))
    for k in range(1, n + 1):
        tx, ty = ax + (bx - ax) * k / n, ay + (by - ay) * k / n
        P.add("big-electric-pole", *P.find_free(2, 2, (round(tx) + (3 if k == n else 0), round(ty) + (3 if k == n else 0)),
                                                margin=0, radius=8))
P.power(MP)                                   # covers what is left and joins every group to the grid
assert not P.robot_uncovered(), P.robot_uncovered()[:5]

# ---- trains: parked at their loading station, fuel requested from the network
P.train(cu["cars"], [("Cobre Norte", "full"), ("Descarga Cobre", "empty")])
P.train(fe["cars"], [("Hierro Norte", "full"), ("Descarga Hierro", "empty")])

# The lab test can't run a rail loop: write the blueprint (the unexplored-map check still applies) and test it
# on the save:  save_sim.py --save save.zip --bp out.txt --minutes 30   and   ... --robots give
ok = P.save(OUT, "Tren: anillo norte", "One-way rail loop: 2 mines, 2 smelters, train mall. Ctrl+Shift+click.",
            icons=["locomotive", "rail", "roboport", "electric-furnace"], absolute=True, test=False)
sys.exit(0 if ok else 1)
