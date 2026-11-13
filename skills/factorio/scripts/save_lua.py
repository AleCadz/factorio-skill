#!/usr/bin/env python3
"""Runs a Lua snippet on a copy of the player's save (in an isolated Factorio) and prints what it says.

  save_lua.py script.lua                     -> on the most recent save
  save_lua.py -e 'out(#s.find_entities_filtered{type="lab"})' --save x.zip
  save_lua.py script.lua --after 3600        -> let the game run 3600 ticks first, then run the snippet
  save_lua.py script.lua --out-dir DIR       -> files written with helpers.write_file() are copied to DIR

Inside the snippet: `s` is the surface (nauvis or --surface), `f` the player force, `out(...)` prints
one line (any number of arguments; tables are serialized), `pos(e)` formats an entity position and
`json(t)` encodes a table. Errors are printed with their line. The player's character is made
indestructible so a long --after doesn't end the run. Never touches the original save.
"""
import argparse, glob, json, os, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fenv  # noqa: E402

PRELUDE = r'''
local CFG = helpers.json_to_table(__CFG__)
local function out(...)
  local t = {}
  for i = 1, select("#", ...) do local v = select(i, ...); t[#t+1] = type(v) == "table" and serpent.line(v) or tostring(v) end
  log("SAVELUA " .. table.concat(t, " "):gsub("\n", " | "))
end
local function pos(e) local p = e.position or e; return string.format("(%.1f,%.1f)", p.x or p[1], p.y or p[2]) end
local function json(t) return helpers.table_to_json(t) end
script.on_event(defines.events.on_tick, function(ev)
  if not storage.t0 then
    storage.t0 = ev.tick
    for _, pl in pairs(game.players) do if pl.character then pl.character.destructible = false end end
  end
  if storage.done or ev.tick - storage.t0 < CFG.after then return end
  storage.done = true
  local s, f = game.surfaces[CFG.surface], game.forces.player
  local chunk, err = load(CFG.body, "snippet", "t", setmetatable({s = s, f = f, out = out, pos = pos, json = json}, {__index = _ENV}))
  if not chunk then out("LUA COMPILE ERROR: " .. err) return end
  local ok, e2 = pcall(chunk)
  if not ok then out("LUA ERROR: " .. tostring(e2)) end
end)
'''


def long_string(text):
    """A Lua long-bracket literal for any text (picks a level of '=' the text doesn't contain)."""
    n = 0
    while ("]" + "=" * n + "]") in text:
        n += 1
    return "[" + "=" * n + "[" + text + "]" + "=" * n + "]"


def run(body, save=None, after=0, surface="nauvis", out_dir=None, tag="SAVELUA"):
    """Run `body` on a copy of `save`; returns the printed lines. Usable from other scripts."""
    save = save or fenv.latest_save()
    cfg = {"body": body, "after": int(after), "surface": surface}
    with fenv.Instance("savelua", PRELUDE.replace("__CFG__", long_string(json.dumps(cfg))), user_mods=True) as inst:
        inst.run_save(inst.copy_save(save), int(after) + 3)
        lines = inst.tagged(tag) + ["! " + l for l in inst.errors(5)]
        if out_dir:
            for p in glob.glob(os.path.join(inst.write, "script-output", "*")):
                shutil.copy(p, out_dir)
    return lines


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", nargs="?", help="Lua file (or use -e)")
    ap.add_argument("-e", dest="expr", help="Lua code inline")
    ap.add_argument("--save", help="save .zip (default: the most recent one)")
    ap.add_argument("--after", type=int, default=0, help="ticks to run before the snippet (60 = 1 s)")
    ap.add_argument("--surface", default="nauvis")
    ap.add_argument("--out-dir", help="copy script-output files here")
    a = ap.parse_args()
    if not a.script and not a.expr:
        ap.error("give a Lua file or -e CODE")
    body = a.expr if a.expr else open(a.script, encoding="utf-8").read()
    print("\n".join(run(body, a.save, a.after, a.surface, a.out_dir)))
