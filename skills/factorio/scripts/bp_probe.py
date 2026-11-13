#!/usr/bin/env python3
"""Find out how something is stored in a blueprint by asking the game itself.

Runs a piece of Lua that places and configures entities on an empty surface (variables `s` = surface
and `force` = the player force), exports the area as a blueprint and prints its JSON.

  bp_probe.py 'local e = s.create_entity{name="splitter", position={0.5,0}, direction=defines.direction.south, force=force}
               e.splitter_output_priority = "left"'

To inspect something that doesn't show up in the exported blueprint (fluidbox filters and connections,
prototypes...), write `log("PROBE INFO " .. text)` in the Lua and it will be printed.

Useful when you don't know a field's name (priorities, filters, recipes, circuit conditions...) and the
local API docs (doc-html) don't spell it out. Runs in an isolated headless game; never touches yours.
"""
import base64, json, os, re, sys, zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fenv  # noqa: E402

LUA = r'''
script.on_event(defines.events.on_tick, function(ev)
  if ev.tick ~= 1 then return end
  local force = game.forces.player
  force.research_all_technologies()
  local s = game.create_surface("probe"); s.generate_with_lab_tiles = true
  s.request_to_generate_chunks({0, 0}, 2); s.force_generate_chunk_requests()
  local ok, err = pcall(function()
__USER__
  end)
  if not ok then log("PROBE ERROR " .. tostring(err)); return end
  local inv = game.create_inventory(1)
  inv[1].set_stack{name = "blueprint"}
  inv[1].create_blueprint{surface = s, force = force, area = {{-30, -30}, {30, 30}}, include_entities = true, include_tiles = false}
  log("PROBE BP " .. inv[1].export_stack())
end)
'''


def main():
    if len(sys.argv) == 2 and sys.argv[1] in ("-h", "--help"):
        print(__doc__); return
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    with fenv.Instance(name="bpprobe", control_lua=LUA.replace("__USER__", sys.argv[1])) as inst:
        inst.run_save(inst.new_map(), 3)
        lines = inst.tagged("PROBE")
        errors = inst.errors()
    for l in lines:   # the Lua can write its own lines with log("PROBE INFO ...")
        if l.startswith("INFO"):
            print(l)
    bp = [l[3:] for l in lines if l.startswith("BP ")]
    if not bp:
        err = [l[6:] for l in lines if l.startswith("ERROR ")]
        raise SystemExit("Lua error: " + (err[0] if err else "no output; check the code\n" + "\n".join(errors)))
    obj = json.loads(zlib.decompress(base64.b64decode(bp[0].strip()[1:])))
    print(json.dumps(obj["blueprint"].get("entities", []), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
