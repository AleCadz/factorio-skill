#!/usr/bin/env python3
"""Exports an area of the player's save as a blueprint (on a copy; the original is never touched).

  save_export.py --recipe logistic-science-pack -o green.txt   # area around every machine with that recipe
  save_export.py --entity lab -o labs.txt                      # area around every entity with that name
  save_export.py --area -40,-80,20,-20 -o area.txt             # explicit area (x1,y1,x2,y2)
  options: --margin 6 (tiles around), --save path.zip, --surface nauvis

Prints the exported area (in world coordinates) and every machine found. Then look at it with
bp.py summary/render/decode, test it with bp_sim.py, or draw a whole base with bp_map.py.
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fenv  # noqa: E402

LUA = r'''
local CFG = helpers.json_to_table(__CFG__)
local function out(m) log("SAVEEXP " .. m) end
script.on_event(defines.events.on_tick, function()
  if storage.done then return end
  storage.done = true
  local s = game.surfaces[CFG.surface]
  if not s then out("ERROR no surface named " .. CFG.surface); return end
  local force = game.forces.player
  local area
  if CFG.area then
    area = {{CFG.area[1], CFG.area[2]}, {CFG.area[3], CFG.area[4]}}
  else
    local found = {}
    if CFG.recipe and not prototypes.recipe[CFG.recipe] then out("ERROR unknown recipe " .. CFG.recipe); return end
    if CFG.entity and not prototypes.entity[CFG.entity] then out("ERROR unknown entity " .. CFG.entity); return end
    if CFG.recipe then
      for _, e in pairs(s.find_entities_filtered{force = force, type = {"assembling-machine", "furnace"}}) do
        local r = e.get_recipe()
        if r and r.name == CFG.recipe then table.insert(found, e) end
      end
    else
      found = s.find_entities_filtered{force = force, name = CFG.entity}
    end
    if #found == 0 then out("ERROR none found"); return end
    local function sname(v) for k, x in pairs(defines.entity_status) do if x == v then return k end end return tostring(v) end
    local x1, y1, x2, y2 = math.huge, math.huge, -math.huge, -math.huge
    for _, e in pairs(found) do
      local b = e.bounding_box
      x1 = math.min(x1, b.left_top.x); y1 = math.min(y1, b.left_top.y)
      x2 = math.max(x2, b.right_bottom.x); y2 = math.max(y2, b.right_bottom.y)
      out(string.format("machine %s at (%.1f, %.1f) status %s", e.name, e.position.x, e.position.y, e.status and sname(e.status) or "-"))
    end
    area = {{math.floor(x1) - CFG.margin, math.floor(y1) - CFG.margin}, {math.ceil(x2) + CFG.margin, math.ceil(y2) + CFG.margin}}
  end
  out(string.format("area (%d, %d) to (%d, %d)", area[1][1], area[1][2], area[2][1], area[2][2]))
  local inv = game.create_inventory(1)
  inv[1].set_stack{name = "blueprint"}
  inv[1].create_blueprint{surface = s, force = force, area = area, include_entities = true, include_tiles = false, include_modules = true}
  if not inv[1].is_blueprint_setup() then out("ERROR the area has no entities of the player"); return end
  out("BP " .. inv[1].export_stack())
end)
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--recipe", help="recipe name, e.g. logistic-science-pack")
    g.add_argument("--entity", help="entity name, e.g. lab")
    g.add_argument("--area", help="x1,y1,x2,y2 in world coordinates")
    ap.add_argument("--margin", type=int, default=6, help="tiles added around the machines found (default 6)")
    ap.add_argument("--save", help="save .zip (default: the most recent one)")
    ap.add_argument("--surface", default="nauvis", help="surface/planet (default nauvis)")
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args()
    fenv.require()
    cfg = {"recipe": a.recipe, "entity": a.entity, "margin": a.margin, "surface": a.surface,
           "area": [float(v) for v in a.area.split(",")] if a.area else None}
    save = a.save or fenv.latest_save()
    print(f"Save: {save}")
    with fenv.Instance("saveexp", LUA.replace("__CFG__", fenv.long_string(json.dumps(cfg))), user_mods=True) as inst:
        inst.run_save(inst.copy_save(save), 2)
        lines = inst.tagged("SAVEEXP")
        if not lines:
            raise SystemExit("No output from the game:\n" + "\n".join(inst.errors(15)))
    bp = [l for l in lines if l.startswith("BP ")]
    for l in lines:
        if not l.startswith("BP "):
            print(l)
    if not bp:
        raise SystemExit("Nothing was exported.")
    with open(a.output, "w") as f:
        f.write(bp[0][3:] + "\n")
    print(f"Blueprint saved to {a.output}")


if __name__ == "__main__":
    main()
