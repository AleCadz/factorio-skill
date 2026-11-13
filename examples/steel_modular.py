#!/usr/bin/env python3
"""Modular steel smelter: furnaces on both sides of a central belt of pure steel.

Example of the generator approach the skill uses: the blueprint is built in code, then rendered and
simulated with bp.py / bp_sim.py before it is handed over.

  steel_modular.py full <segments>  -> head + N segments (8 furnaces each) as one blueprint
  steel_modular.py book             -> book: complete x16/x24/x48, the head alone and the extension segment
  steel_modular.py head|segment     -> a single piece
  Add --steel for steel furnaces (double speed: at most 3 segments on one yellow belt).

Columns (x):  0 left input (iron|coal, down)   1 ins   2-3 furnace   4 ins
              5 STEEL (up)                      6 ins   7-8 furnace   9 ins   10 right input (down)
Head (y<0, 9 rows): iron (x=-3) and coal (x=11) come in at the top and steel leaves at x=5.
  A splitter with priority for the steel branch lets the spare iron continue down x=-3.
Segment (8 rows): 4 furnaces per side. Paste it under the previous one; the centre belt flows up to the head.

Simulated: x24 = 90 steel/min while 450 iron/min passes through; x48 = ~178 steel/min, taking all the iron.
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "factorio", "scripts"))
import bp  # noqa: E402

N_, E_, S_, W_ = 0, 4, 8, 12
STEEL = "--steel" in sys.argv
if STEEL:
    sys.argv.remove("--steel")
FURNACE = "steel-furnace" if STEEL else "stone-furnace"
SPEED = 2 if STEEL else 1
KIND = "steel furnaces" if STEEL else "stone furnaces"
MAX_SEGS = 3 if STEEL else 6
SEG_H = 8
VERSION = bp.version_int(2, 0, 77)


class Plan:
    def __init__(self):
        self.ents, self.wires = [], []

    def add(self, name, x, y, **kw):
        self.ents.append({"entity_number": len(self.ents) + 1, "name": name, "position": {"x": x, "y": y}, **kw})
        return len(self.ents)

    def belt(self, x, y, d):
        return self.add("transport-belt", x + 0.5, y + 0.5, direction=d)

    def ug(self, x, y, d, kind):
        return self.add("underground-belt", x + 0.5, y + 0.5, direction=d, type=kind)


def head(p):
    # main splitter: your iron enters at (-3,-9); the spare leaves down x=-3 and the steel branch goes via x=-2
    p.belt(-3, -9, S_)
    p.add("splitter", -2, -7.5, direction=S_, output_priority="left")   # facing south, "left" = east = steel branch
    for y in range(-7, 0):
        p.belt(-3, y, S_)                                               # spare iron continues down
    p.belt(-2, -7, E_)
    for x in range(-1, 3):
        p.belt(x, -7, E_)
    for y in range(-7, -3):
        p.belt(3, y, S_)
    # splitter dividing the iron between both sides
    p.add("splitter", 4, -2.5, direction=S_)                            # covers x=3..4, y=-3
    p.belt(3, -2, S_); p.belt(3, -1, W_); p.belt(2, -1, W_); p.belt(1, -1, W_)   # -> left side (side-load from the east)
    p.belt(4, -2, E_)
    for x in range(5, 9):
        p.belt(x, -2, E_)
    p.belt(9, -2, S_); p.belt(9, -1, E_)                                # -> right side (side-load from the west)
    # coal: enters at (11,-9) and is split; one branch goes underground to the left side
    p.belt(11, -9, S_); p.belt(11, -8, S_); p.belt(11, -7, S_)
    p.add("splitter", 11, -5.5, direction=S_)                           # covers x=10..11, y=-6
    for y in range(-5, -1):
        p.belt(11, y, S_)
    p.belt(11, -1, W_)                                                  # -> right side (side-load from the east)
    p.belt(10, -5, W_)
    for x in range(7, 10):
        p.belt(x, -5, W_)
    p.ug(6, -5, W_, "input"); p.ug(1, -5, W_, "output")                 # passes under the iron and steel belts
    p.belt(0, -5, W_); p.belt(-1, -5, S_)
    for y in range(-4, -1):
        p.belt(-1, y, S_)
    p.belt(-1, -1, E_)                                                  # -> left side (side-load from the west)
    # side-loads: a belt behind so the target belt is straight and both sides load sideways
    p.belt(0, -2, S_); p.belt(0, -1, S_)
    p.belt(10, -2, S_); p.belt(10, -1, S_)
    # steel: goes up under the iron row and leaves at the top
    p.ug(5, -1, N_, "input"); p.ug(5, -3, N_, "output")
    for y in range(-9, -3):
        p.belt(5, y, N_)


def segment(p, y0):
    poles = {1: [], 4: [], 9: []}
    for y in range(y0, y0 + SEG_H):
        p.belt(0, y, S_); p.belt(10, y, S_); p.belt(5, y, N_)
    for k in range(SEG_H // 2):
        y = y0 + 2 * k
        p.add(FURNACE, 3, y + 1)
        p.add(FURNACE, 8, y + 1)
        p.add("inserter", 1.5, y + 0.5, direction=W_)    # picks from the left input, inserts into the furnace
        p.add("inserter", 4.5, y + 0.5, direction=W_)    # picks from the left furnace, drops on the centre belt
        p.add("inserter", 6.5, y + 0.5, direction=E_)    # picks from the right furnace, drops on the centre belt
        p.add("inserter", 9.5, y + 0.5, direction=E_)    # picks from the right input, inserts into the furnace
        if k % 2 == 0:
            for x in poles:
                poles[x].append(p.add("small-electric-pole", x + 0.5, y + 1.5))
    for col in poles.values():
        p.wires += [[a, 5, b, 5] for a, b in zip(col, col[1:])]
    for i in range(len(poles[1])):
        p.wires += [[poles[1][i], 5, poles[4][i], 5], [poles[4][i], 5, poles[9][i], 5]]


def blueprint(label, desc, build):
    p = Plan()
    build(p)
    return {"blueprint": {"item": "blueprint", "label": label, "description": desc,
                          "icons": [{"signal": {"type": "item", "name": "steel-plate"}, "index": 1},
                                    {"signal": {"type": "item", "name": FURNACE}, "index": 2}],
                          "entities": p.ents, "wires": p.wires, "version": VERSION}}


def full(n):
    def build(p):
        head(p)
        for i in range(n):
            segment(p, i * SEG_H)
    f = 8 * n
    return blueprint(f"Steel x{f} modular" + (" (steel furnaces)" if STEEL else ""),
                     f"{f} {KIND}. At the top: iron comes in on the left (both lanes full is fine) and coal on the "
                     f"right; pure steel leaves through the centre. Spare iron continues down the left side. "
                     f"{f * 3.75 * SPEED:g} steel/min, {f * 18.75 * SPEED:g} iron/min, {f * 1.35:.1f} coal/min. To extend, paste "
                     f"the '+8 furnaces' segment right below (at most {MAX_SEGS} segments on one yellow iron belt).", build)


SEGMENT_DESC = "Paste it right below the head or the last segment: the three belts must line up."
HEAD_DESC = "Splitters and side-loads. Paste the segments below it."


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "book"
    if what == "full":
        print(bp.encode(full(int(sys.argv[2]))))
    elif what == "head":
        print(bp.encode(blueprint("Steel modular: head", HEAD_DESC, head)))
    elif what == "segment":
        print(bp.encode(blueprint("Steel modular: +8 " + KIND, SEGMENT_DESC, lambda p: segment(p, 0))))
    elif what == "book":
        items = ([full(1), full(2), full(3)] if STEEL else [full(2), full(3), full(6)]) + [
                 blueprint("Steel modular: +8 " + KIND, SEGMENT_DESC, lambda p: segment(p, 0)),
                 blueprint("Steel modular: head", HEAD_DESC, head)]
        book = {"blueprint_book": {"item": "blueprint-book", "label": "Steel modular" + (" (steel furnaces)" if STEEL else ""),
                                   "icons": [{"signal": {"type": "item", "name": "steel-plate"}, "index": 1}],
                                   "blueprints": [{"index": i, **b} for i, b in enumerate(items)],
                                   "active_index": 0, "version": VERSION}}
        print(bp.encode(book))


if __name__ == "__main__":
    main()
