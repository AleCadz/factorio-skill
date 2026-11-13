#!/usr/bin/env python3
"""Tests a blueprint on a COPY of the player's save, on their real terrain.

For whatever depends on the map: pumpjacks on wells, drills on ore, offshore pumps on the shore,
pipelines across the terrain... Builds the blueprint (removing trees, rocks and cliffs in the way),
connects the poles, optionally adds infinite power, and watches the status of what was built.

  save_bp_test.py bp.txt --align 1=-213.5,-123.5            # entity no. 1 lands on that world tile
  save_bp_test.py book.txt --index 1 --align 1=-213.5,-123.5 --seconds 30
  save_bp_test.py bp.txt --align 3=10.5,20.5 --no-power     # no extra power: uses their grid if it connects

The original save is never touched. Loads the player's mods. Positions of wells and ore patches
come from save_probe.py.
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fenv  # noqa: E402
import bp as bptool  # noqa: E402

LUA = r'''
local CFG = helpers.json_to_table(__CFG__)
local function out(m) log("SAVEBP " .. m) end
local function sname(v) for k, x in pairs(defines.entity_status) do if x == v then return k end end return tostring(v) end
local function pos(p) return string.format("(%.1f,%.1f)", p.x, p.y) end

local function build(s, force, at)
  local inv = game.create_inventory(1)
  inv[1].import_stack(CFG.bp)
  return inv[1].build_blueprint{surface = s, force = force, position = at, build_mode = defines.build_mode.forced}
end

script.on_event(defines.events.on_tick, function(ev)
  local s = game.surfaces[CFG.surface]
  if not s then
    if not storage.built then storage.built = true; out("ERROR no surface named " .. CFG.surface) end
    return
  end
  local force = game.forces.player
  if not storage.built then
    storage.built = true
    -- Find where to build so that entity no. N lands exactly on (ax, ay). Probing happens on a separate
    -- lab surface (obstacles on the map can't interfere), shifted by a multiple of 64 tiles so the
    -- blueprint snaps to the grid exactly as it will on the real map.
    local S = {64 * math.floor(CFG.ax / 64), 64 * math.floor(CFG.ay / 64)}
    local T = {CFG.ax - S[1], CFG.ay - S[2]}
    local lab = game.create_surface("savebp_probe")
    lab.generate_with_lab_tiles = true
    lab.request_to_generate_chunks({32, 32}, CFG.r); lab.force_generate_chunk_requests()
    local by_name = {}
    for _, e in pairs(CFG.ents) do
      by_name[e[1]] = by_name[e[1]] or {}
      if #by_name[e[1]] < 200 then table.insert(by_name[e[1]], e) end
    end
    local function landed(ghosts)
      -- every ghost votes for the translation that maps a same-named blueprint entity onto it
      local votes, best, bestn = {}, nil, 0
      for i, g in pairs(ghosts) do
        if i > 200 then break end
        if g.valid and g.type == "entity-ghost" then
          for _, e in pairs(by_name[g.ghost_name] or {}) do
            local k = string.format("%.1f,%.1f", g.position.x - e[2], g.position.y - e[3])
            votes[k] = (votes[k] or 0) + 1
            if votes[k] > bestn then bestn = votes[k]; best = {g.position.x - e[2], g.position.y - e[3]} end
          end
        end
      end
      return best and {CFG.nx + best[1], CFG.ny + best[2]}, best
    end
    local at, ok = {T[1], T[2]}, false
    for _ = 1, 4 do
      local probe = build(lab, force, at)
      local w = landed(probe)
      for _, g in pairs(probe) do if g.valid then g.destroy() end end
      if not w then break end
      local cx, cy = T[1] - w[1], T[2] - w[2]
      if math.abs(cx) < 0.01 and math.abs(cy) < 0.01 then ok = true; break end
      at = {at[1] + (cx >= 0 and math.floor(cx + 0.5) or -math.floor(-cx + 0.5)), at[2] + (cy >= 0 and math.floor(cy + 0.5) or -math.floor(-cy + 0.5))}
    end
    game.delete_surface(lab)
    if not ok then out("WARNING could not land entity no. " .. CFG.num .. " exactly on (" .. CFG.ax .. ", " .. CFG.ay .. "): is that a valid centre for it? (odd sizes end in .5, even sizes are whole numbers)") end
    at = {at[1] + S[1], at[2] + S[2]}
    local ghosts = build(s, force, at)
    out("ghosts=" .. #ghosts .. " of " .. CFG.n .. " entities")
    local w, tr = landed(ghosts)
    if w then out(string.format("entity no. %d at (%.1f, %.1f)", CFG.num, w[1], w[2])) end
    if #ghosts < CFG.n then
      out("WARNING fewer ghosts than entities: something is in the way (buildings, water, cliffs) or pieces overlap")
      if tr then
        local have, shown = {}, 0
        for _, g in pairs(ghosts) do if g.valid then have[g.ghost_name .. string.format("@%.1f,%.1f", g.position.x, g.position.y)] = true end end
        for _, e in pairs(CFG.ents) do
          local x, y = e[2] + tr[1], e[3] + tr[2]
          if not have[e[1] .. string.format("@%.1f,%.1f", x, y)] then
            shown = shown + 1
            if shown <= 8 then
              local blockers = {}
              for _, o in pairs(s.find_entities_filtered{position = {x, y}, radius = 1.5}) do
                if o.type ~= "entity-ghost" and o.type ~= "resource" then blockers[o.name] = true end
              end
              local names = {}
              for k in pairs(blockers) do table.insert(names, k) end
              if s.get_tile(x, y).collides_with("water_tile") then table.insert(names, "water") end
              out(string.format("  not placed: %s at (%.1f, %.1f), in the way: %s", e[1], x, y, #names > 0 and table.concat(names, ", ") or "?"))
            end
          end
        end
        if shown > 8 then out("  ... and " .. (shown - 8) .. " more") end
      end
    end
    local fails, built = 0, {}
    for _, g in pairs(ghosts) do
      if g.valid then
        for _, o in pairs(s.find_entities_filtered{area = g.bounding_box, type = {"tree", "simple-entity", "cliff"}}) do o.destroy() end
        local gname, gpos = g.ghost_name, g.position
        local _, ent = g.revive{raise_revive = true}
        if ent then table.insert(built, ent) else fails = fails + 1; out("COULD NOT build " .. gname .. " at " .. pos(gpos)) end
      end
    end
    out("built=" .. #built .. " failures=" .. fails)
    -- poles: connect to every pole in reach (the player's too), as when building by hand
    for _, p in pairs(built) do
      if p.type == "electric-pole" then
        local reach = p.prototype.get_max_wire_distance()
        for _, q in pairs(s.find_entities_filtered{type = "electric-pole", position = p.position, radius = reach}) do
          if q ~= p then
            local r = math.min(reach, q.prototype.get_max_wire_distance())
            local dx, dy = p.position.x - q.position.x, p.position.y - q.position.y
            if dx * dx + dy * dy <= r * r then
              p.get_wire_connector(defines.wire_connector_id.pole_copper, true).connect_to(q.get_wire_connector(defines.wire_connector_id.pole_copper, true), false)
            end
          end
        end
      end
    end
    if CFG.power then
      local ok = false
      for _, p in pairs(built) do
        if p.type == "electric-pole" then
          local spot = s.find_non_colliding_position("electric-energy-interface", p.position, 4, 0.5)
          local eei = spot and s.create_entity{name = "electric-energy-interface", position = spot, force = force}
          if eei and eei.is_connected_to_electric_network() then
            eei.power_production = 1e8; eei.electric_buffer_size = 1e9
            out("infinite_power next to " .. p.name .. " " .. pos(p.position)); ok = true; break
          elseif eei then eei.destroy() end
        end
      end
      if not ok then out("infinite_power=false (no pole in the blueprint, or no free spot next to one)") end
    end
    storage.built_list = built
    storage.t0 = ev.tick
  elseif storage.t0 and ev.tick == storage.t0 + 2 then
    for _, e in pairs(storage.built_list or {}) do
      if e.valid and e.type == "mining-drill" then
        out(string.format("%s at %s mines %s", e.name, pos(e.position), e.mining_target and (e.mining_target.name .. " (" .. e.mining_target.amount .. ")") or "NOTHING"))
      end
    end
  elseif storage.t0 and (ev.tick - storage.t0) % CFG.every == 0 then
    local st = {}
    for _, e in pairs(storage.built_list or {}) do
      if e.valid and e.status then local k = e.name .. ":" .. sname(e.status); st[k] = (st[k] or 0) + 1 end
    end
    local fluids = {}
    for _, e in pairs(storage.built_list or {}) do
      if e.valid and (e.type == "pipe" or e.type == "pipe-to-ground" or e.type == "storage-tank") then
        -- 2.1.7 removed LuaEntity::fluidbox; get_fluid_contents exists in 2.0 and 2.1
        for name, amount in pairs(e.get_fluid_contents()) do fluids[name] = math.max(fluids[name] or 0, amount) end
      end
    end
    out(string.format("t=%ds status=%s max_fluid_in_pipes=%s", (ev.tick - storage.t0) / 60, serpent.line(st), serpent.line(fluids)))
  end
end)
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="file with the blueprint string, '-' or 'clip'")
    ap.add_argument("--index", help="blueprint inside a book (as in bp.py summary)")
    ap.add_argument("--align", required=True, help="N=x,y: entity no. N of the blueprint lands on that world position")
    ap.add_argument("--save", help="save .zip (default: the most recent one)")
    ap.add_argument("--surface", default="nauvis", help="surface/planet (default nauvis)")
    ap.add_argument("--seconds", type=float, default=20, help="game seconds to run (default 20)")
    ap.add_argument("--every", type=float, default=5, help="seconds between status reports (default 5)")
    ap.add_argument("--no-power", action="store_true", help="don't add infinite power")
    ap.add_argument("--keep", action="store_true", help="keep the temporary folder")
    a = ap.parse_args()
    fenv.require()

    obj = bptool.pick(bptool.decode(bptool.read_src(a.src)), a.index)
    if bptool.kind(obj)[0] != "blueprint":
        raise SystemExit("I need a single blueprint (use --index; see 'bp.py summary').")
    ents = obj["blueprint"].get("entities", [])
    if not ents:
        raise SystemExit("The blueprint has no entities.")
    num, xy = a.align.split("=")
    ax, ay = map(float, xy.split(","))
    target = [e for e in ents if e["entity_number"] == int(num)]
    if not target:
        raise SystemExit(f"--align: there is no entity no. {num}")
    cfg = {"bp": bptool.encode(obj), "ax": ax, "ay": ay, "n": len(ents), "surface": a.surface,
           "nx": target[0]["position"]["x"], "ny": target[0]["position"]["y"],
           "num": int(num), "ents": [[e["name"], e["position"]["x"], e["position"]["y"]] for e in ents],
           "r": int(max(max(abs(e["position"][k]) for e in ents) for k in "xy") // 32) + 4,
           "power": not a.no_power, "every": max(60, int(a.every * 60))}

    save = a.save or fenv.latest_save()
    print(f"Save: {save}")
    with fenv.Instance("savebp", LUA.replace("__CFG__", fenv.long_string(json.dumps(cfg))), user_mods=True, keep=a.keep) as inst:
        inst.run_save(inst.copy_save(save), a.seconds * 60 + 2)
        lines = inst.tagged("SAVEBP")
        if not lines:
            raise SystemExit("No output from the game:\n" + "\n".join(inst.errors(15)))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
