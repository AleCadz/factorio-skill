#!/usr/bin/env python3
"""Lays rails on a copy of the player's save with the game's own rail planner (LuaRailEnd.get_rail_extensions),
so curves are always valid 2.0 rails, and returns every piece for a layout.Plan.

  rails.py "(-403,-13,0)" "S132 ! R4 S61 L8 S74 L4 S146 L8" [--save x.zip]

start = (x, y, direction) of the first straight rail (odd coordinates; direction 0 = vertical, heading north).
Commands: S<n> n straight pieces (2 tiles each), L<n>/R<n> n turn steps of 22.5 degrees (R4 = 90 degrees: 13 tiles
forward and 13 sideways; L8 = U-turn 26 tiles wide), ! a rail signal on the right-hand side at the current
end (one-way traffic), !c a chain signal, @name remembers the current end. Returns a dict: rails [{name, x, y,
dir, bb}], signals, marks, blocked (water/cliffs/buildings it had to remove to continue: reroute until empty),
closed (the end met the start: a loop). See references/layout_train.py for a full example
(stations, trains with schedule and fuel request, stock_connections).
"""
import ast, sys, json, os, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_lua, fenv  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def trace(start, cmds, save=None, post=None, quiet=False):
    cfg = {"start": list(start), "cmds": cmds.split(), "post": post}
    body = "local CFGJ=" + save_lua.long_string(json.dumps(cfg)) + "\n" + open(os.path.join(HERE, "rails_turtle.lua")).read()
    tmp = tempfile.mkdtemp(dir=fenv.tmp_base())
    lines = save_lua.run(body, save or fenv.latest_save(), out_dir=tmp)
    if not quiet:
        print("\n".join(l[:1500] for l in lines))
    p = os.path.join(tmp, "turtle.json")
    try:
        return json.load(open(p)) if os.path.exists(p) else None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sv = sys.argv[sys.argv.index("--save") + 1] if "--save" in sys.argv else None
    r = trace(ast.literal_eval(sys.argv[1]), sys.argv[2], save=sv)
    if r:
        print({k: v for k, v in r.items() if k != "rails"})
