#!/usr/bin/env python3
"""Reads the state of the player's save without touching it (works on a copy, in an isolated Factorio).

  save_probe.py                      -> the most recent save in the player's saves folder
  save_probe.py <save.zip>           -> a specific save
  save_probe.py --surface vulcanus   -> another planet/surface (Space Age); the default is nauvis

Reports: play time, enemy evolution (total and by cause) and evolution/expansion settings, pollution,
nests and worms (how many, how far from the base, how many inside the pollution cloud), turrets and
ammo, the character's weapons and armor, research done, in progress, queued and available,
production per minute (10-minute average), assemblers, furnaces and drills with their status, ore
left under drills and free ore nearby (position and nests close to it), pumpjacks and the nearest
oil field, power, electric networks, chest stock and entity counts. If the save has other surfaces
with player entities it lists them, so you can run it again with --surface.
Loads the same mods the player has, plus a small read-only helper mod.
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fenv  # noqa: E402

LUA = r'''
local CFG = helpers.json_to_table(__CFG__)
local function out(m) log("SAVEPROBE " .. m) end
script.on_event(defines.events.on_tick, function()
  if storage.done then return end
  storage.done = true
  local player = game.forces.player
  local enemy = game.forces.enemy

  local others = {}
  for _, surf in pairs(game.surfaces) do
    if surf.name ~= CFG.surface then
      local n = surf.count_entities_filtered{force = player}
      if n > 0 then table.insert(others, surf.name .. "=" .. n) end
    end
  end
  if #others > 0 then out("other_surfaces_with_player_entities " .. table.concat(others, ", ") .. " (re-run with --surface NAME)") end

  local s = game.surfaces[CFG.surface]
  if not s then
    local names = {}
    for _, surf in pairs(game.surfaces) do table.insert(names, surf.name) end
    out("ERROR no surface named " .. CFG.surface .. "; surfaces: " .. table.concat(names, ", "))
    return
  end
  out("surface=" .. s.name)
  local ms = game.map_settings
  out(string.format("play_time=%.1f h (tick %d)", game.tick / 216000, game.tick))
  out(string.format("evolution=%.4f  by_time=%.4f  by_pollution=%.4f  by_killed_spawners=%.4f",
    enemy.get_evolution_factor(s), enemy.get_evolution_factor_by_time(s),
    enemy.get_evolution_factor_by_pollution(s), enemy.get_evolution_factor_by_killing_spawners(s)))
  local ev = ms.enemy_evolution
  out(string.format("evolution_settings enabled=%s time_factor=%g destroy_factor=%g pollution_factor=%g",
    tostring(ev.enabled), ev.time_factor, ev.destroy_factor, ev.pollution_factor))
  out("expansion " .. serpent.line({enabled = ms.enemy_expansion.enabled, min_cooldown_min = ms.enemy_expansion.min_expansion_cooldown / 3600,
    max_cooldown_min = ms.enemy_expansion.max_expansion_cooldown / 3600, max_distance = ms.enemy_expansion.max_expansion_distance}))
  out("pollution_enabled=" .. tostring(ms.pollution.enabled) .. " total_on_surface=" .. string.format("%.0f", s.get_total_pollution()))
  local ac = s.map_gen_settings.autoplace_controls
  out("enemy_bases_mapgen=" .. tostring(ac and ac["enemy-base"] and serpent.line(ac["enemy-base"]) or "?")
    .. " peaceful_mode=" .. tostring(s.peaceful_mode))

  -- base centre: average position of the player's entities
  local sx, sy, n = 0, 0, 0
  for _, e in pairs(s.find_entities_filtered{force = player}) do
    if e.type ~= "character" and e.type ~= "car" then sx = sx + e.position.x; sy = sy + e.position.y; n = n + 1 end
  end
  local cx, cy = n > 0 and sx / n or 0, n > 0 and sy / n or 0
  out(string.format("base: %d player entities, centre approx. (%.0f, %.0f)", n, cx, cy))

  local function dist(e) return math.sqrt((e.position.x - cx) ^ 2 + (e.position.y - cy) ^ 2) end
  local spawners = s.find_entities_filtered{force = enemy, type = "unit-spawner"}
  local worms = s.find_entities_filtered{force = enemy, type = "turret"}
  table.sort(spawners, function(a, b) return dist(a) < dist(b) end)
  local polluted, buckets = 0, {[150] = 0, [300] = 0, [500] = 0, [1000] = 0}
  for _, sp in pairs(spawners) do
    if s.get_pollution(sp.position) > 0 then polluted = polluted + 1 end
    for r, _ in pairs(buckets) do if dist(sp) <= r then buckets[r] = buckets[r] + 1 end end
  end
  out(string.format("nests_loaded=%d worms=%d  nests_in_pollution_cloud=%d  within_150=%d within_300=%d within_500=%d within_1000=%d",
    #spawners, #worms, polluted, buckets[150], buckets[300], buckets[500], buckets[1000]))
  for i = 1, math.min(8, #spawners) do
    local sp = spawners[i]
    local near_worms = s.count_entities_filtered{force = enemy, type = "turret", position = sp.position, radius = 20}
    out(string.format("  nest %s %.0f tiles away at (%.0f, %.0f), pollution there %.1f, worms nearby %d",
      sp.name, dist(sp), sp.position.x, sp.position.y, s.get_pollution(sp.position), near_worms))
  end
  local wc = {}
  for _, w in pairs(worms) do wc[w.name] = (wc[w.name] or 0) + 1 end
  out("worm_types " .. serpent.line(wc))

  local turrets = s.find_entities_filtered{force = player, type = {"ammo-turret", "electric-turret", "fluid-turret"}}
  local tc, empty, ammo = {}, 0, {}
  for _, t in pairs(turrets) do
    tc[t.name] = (tc[t.name] or 0) + 1
    if t.type == "ammo-turret" then
      local inv = t.get_inventory(defines.inventory.turret_ammo)
      if inv.is_empty() then empty = empty + 1 end
      for _, it in pairs(inv.get_contents()) do ammo[it.name] = (ammo[it.name] or 0) + it.count end
    end
  end
  out("turrets " .. serpent.line(tc) .. " empty=" .. empty .. " ammo_loaded=" .. serpent.line(ammo))

  for _, c in pairs(s.find_entities_filtered{type = "character", force = player}) do
    local function inv(id) local i = c.get_inventory(id); local r = {}; if i then for _, it in pairs(i.get_contents()) do r[it.name] = (r[it.name] or 0) + it.count end end; return r end
    out(string.format("character at (%.0f, %.0f) health %.0f", c.position.x, c.position.y, c.health))
    out("  guns " .. serpent.line(inv(defines.inventory.character_guns)) .. " ammo " .. serpent.line(inv(defines.inventory.character_ammo))
      .. " armor " .. serpent.line(inv(defines.inventory.character_armor)))
    local main = inv(defines.inventory.character_main)
    local keep = {}
    for k, v in pairs(main) do if k:find("magazine") or k:find("grenade") or k:find("capsule") or k:find("fish") or k:find("turret") or k:find("armor") or k:find("shotgun") or k:find("rocket") then keep[k] = v end end
    out("  combat_items_in_inventory " .. serpent.line(keep))
  end

  local done, mil = 0, {}
  for name, t in pairs(player.technologies) do
    if t.researched then
      done = done + 1
      if name:find("military") or name:find("turret") or name:find("weapon") or name:find("physical") or name:find("armor") or name:find("stronger") then table.insert(mil, name) end
    end
  end
  table.sort(mil)
  out("technologies_researched=" .. done .. " military=" .. serpent.line(mil))
  out("researching=" .. (player.current_research and player.current_research.name or "nothing"))
  local q = {}
  for _, t in pairs(player.research_queue or {}) do table.insert(q, t.name) end
  out("research_queue=" .. table.concat(q, ", "))

  ---------------------------------------------------------------- ECONOMY
  local function status_name(v) for k, x in pairs(defines.entity_status) do if x == v then return k end end return tostring(v) end
  local function hist(list, keyf)
    local r = {}
    for _, e in pairs(list) do local k = keyf(e); r[k] = r[k] or {}; local st = status_name(e.status); r[k][st] = (r[k][st] or 0) + 1 end
    return r
  end
  -- 10-minute average (count=true gives the total over 10 min; /10 = per minute)
  local ist = player.get_item_production_statistics(s)
  local fst = player.get_fluid_production_statistics(s)
  local items = {"iron-ore","copper-ore","stone","coal","iron-plate","copper-plate","steel-plate","stone-brick","iron-gear-wheel",
    "copper-cable","electronic-circuit","automation-science-pack","logistic-science-pack","military-science-pack",
    "chemical-science-pack","plastic-bar","advanced-circuit","sulfur","engine-unit","firearm-magazine","piercing-rounds-magazine",
    "battery","electric-engine-unit","flying-robot-frame","processing-unit","low-density-structure","rocket-fuel",
    "production-science-pack","utility-science-pack","space-science-pack","uranium-ore","uranium-235","uranium-238","solid-fuel",
    "explosives","grenade","stone-wall","concrete","rail","speed-module","productivity-module","efficiency-module"}
  for _, it in pairs(items) do
    if prototypes.item[it] then
      local made = ist.get_flow_count{name = it, category = "input", precision_index = defines.flow_precision_index.ten_minutes, count = true} / 10
      local used = ist.get_flow_count{name = it, category = "output", precision_index = defines.flow_precision_index.ten_minutes, count = true} / 10
      local total = ist.get_input_count(it)
      if total > 0 then out(string.format("prod %-26s made %7.1f/min  used %7.1f/min  all_time %d", it, made, used, total)) end
    end
  end
  for _, fl in pairs({"water", "steam", "crude-oil", "petroleum-gas", "heavy-oil", "light-oil", "lubricant", "sulfuric-acid"}) do
    if prototypes.fluid[fl] then
      local made = fst.get_flow_count{name = fl, category = "input", precision_index = defines.flow_precision_index.ten_minutes, count = true} / 10
      if made > 0 then out(string.format("prod %-26s made %7.0f/min", fl, made)) end
    end
  end

  out("labs " .. serpent.line(hist(s.find_entities_filtered{force = player, type = "lab"}, function(e) return e.name end)))
  out("assemblers " .. serpent.line(hist(s.find_entities_filtered{force = player, type = "assembling-machine"},
    function(e) local r = e.get_recipe(); return r and r.name or "NO_RECIPE" end)))
  out("furnaces " .. serpent.line(hist(s.find_entities_filtered{force = player, type = "furnace"},
    function(e)
      local r = e.get_recipe()
      if r then return e.name .. ":" .. r.name end
      local res = e.get_inventory(defines.inventory.crafter_output or defines.inventory.furnace_result).get_contents()[1]
      return e.name .. ":" .. (res and ("(" .. res.name .. ")") or "idle")
    end)))
  local drills = s.find_entities_filtered{force = player, type = "mining-drill"}
  out("drills " .. serpent.line(hist(drills, function(e) return e.name .. ":" .. (e.mining_target and e.mining_target.name or "nothing") end)))

  -- ore left under the drills
  local seen, left, ndr = {}, {}, {}
  for _, d in pairs(drills) do
    local res = d.mining_target and d.mining_target.name
    if res then ndr[res] = (ndr[res] or 0) + 1 end
    for _, r in pairs(s.find_entities_filtered{area = d.mining_area, type = "resource"}) do
      local key = r.position.x .. "," .. r.position.y
      if not seen[key] then seen[key] = true; left[r.name] = (left[r.name] or 0) + r.amount end
    end
  end
  for res, amt in pairs(left) do out(string.format("ore_under_drills %s: %d left (%d drills)", res, amt, ndr[res] or 0)) end
  local crude = prototypes.entity["crude-oil"]
  for _, d in pairs(drills) do
    if d.name == "pumpjack" then
      local t = d.mining_target
      out(string.format("pumpjack at (%.1f, %.1f) status %s, well %s", d.position.x, d.position.y, status_name(d.status),
        t and string.format("at (%.1f, %.1f) yield %.0f%%", t.position.x, t.position.y, crude and t.amount / crude.normal_resource_amount * 100 or 0) or "NONE"))
    end
  end
  local op = player.technologies["oil-processing"]
  if op then out("oil_processing=" .. (op.researched and "UNLOCKED" or "no")) end

  -- untapped ore near the base (radius 400)
  local area = {{cx - 400, cy - 400}, {cx + 400, cy + 400}}
  local free, nearest = {}, {}
  for _, r in pairs(s.find_entities_filtered{area = area, type = "resource"}) do
    if not seen[r.position.x .. "," .. r.position.y] then
      free[r.name] = (free[r.name] or 0) + r.amount
      local dd = math.sqrt((r.position.x - cx) ^ 2 + (r.position.y - cy) ^ 2)
      if not nearest[r.name] or dd < nearest[r.name] then nearest[r.name] = dd; nearest[r.name .. "@"] = r.position end
    end
  end
  for res, amt in pairs(free) do
    local p0 = nearest[res .. "@"]
    local nests = s.count_entities_filtered{force = enemy, type = "unit-spawner", position = p0, radius = 80}
    local wormsn = s.count_entities_filtered{force = enemy, type = "turret", position = p0, radius = 80}
    out(string.format("free_ore_r400 %s: %d (nearest %.0f tiles from the centre, at (%.0f, %.0f); within 80 tiles: %d nests, %d worms)",
      res, amt, nearest[res], p0.x, p0.y, nests, wormsn))
  end

  -- wells of the nearest oil field: position and yield (100 % = 10 crude/s with one pumpjack)
  local p0 = nearest["crude-oil@"]
  if p0 and crude then
    local wells = s.find_entities_filtered{name = "crude-oil", position = p0, radius = 60}
    local normal = crude.normal_resource_amount
    local tot = 0
    for _, w in pairs(wells) do
      local y = w.amount / normal
      tot = tot + y
      out(string.format("well (%.1f, %.1f) yield %.0f%%", w.position.x, w.position.y, y * 100))
    end
    out(string.format("oil_field %d wells, %.0f crude/s in total with a pumpjack on each", #wells, tot * 10))
  end

  -- power: generators and consumers short of power
  out("power " .. serpent.line(hist(s.find_entities_filtered{force = player, type = {"boiler", "generator", "offshore-pump", "solar-panel", "accumulator", "reactor", "fusion-generator"}},
    function(e) return e.name end)))
  local low, tot = 0, 0
  for _, e in pairs(s.find_entities_filtered{force = player, type = {"assembling-machine", "lab", "inserter", "mining-drill", "furnace", "radar"}}) do
    if e.prototype.electric_energy_source_prototype then
      tot = tot + 1
      if e.status == defines.entity_status.low_power or e.status == defines.entity_status.no_power then low = low + 1 end
    end
  end
  out(string.format("electric_consumers=%d low_or_no_power=%d", tot, low))
  local nets, nn = {}, 0
  for _, pole in pairs(s.find_entities_filtered{force = player, type = "electric-pole"}) do
    local id = pole.electric_network_id
    if id and not nets[id] then nets[id] = true; nn = nn + 1 end
  end
  out("electric_networks=" .. nn)

  -- research available now (prerequisites done)
  local avail = {}
  for name, t in pairs(player.technologies) do
    if t.enabled and not t.researched and not t.prototype.hidden then
      local ok = true
      for _, pre in pairs(t.prerequisites) do if not pre.researched then ok = false end end
      if ok then
        local packs = {}
        for _, ing in pairs(t.research_unit_ingredients) do table.insert(packs, (ing.name:gsub("%-science%-pack", ""))) end
        if #packs > 0 then
          table.insert(avail, string.format("%s[%s x%s]", name, table.concat(packs, "+"), tostring(t.research_unit_count)))
        else
          table.insert(avail, name .. "[trigger]")
        end
      end
    end
  end
  table.sort(avail)
  out("research_available " .. table.concat(avail, ", "))
  local done_list = {}
  for name, t in pairs(player.technologies) do if t.researched then table.insert(done_list, name) end end
  table.sort(done_list)
  out("researched " .. table.concat(done_list, ", "))

  -- chest stock
  local stock = {}
  for _, c in pairs(s.find_entities_filtered{force = player, type = {"container", "logistic-container"}}) do
    for _, it in pairs(c.get_inventory(defines.inventory.chest).get_contents()) do stock[it.name] = (stock[it.name] or 0) + it.count end
  end
  local arr = {}
  for k, v in pairs(stock) do table.insert(arr, {k, v}) end
  table.sort(arr, function(a, b) return a[2] > b[2] end)
  local top = {}
  for i = 1, math.min(15, #arr) do table.insert(top, arr[i][1] .. "=" .. arr[i][2]) end
  out("in_chests " .. table.concat(top, ", "))
  local cnt = {}
  for _, t in pairs({"transport-belt", "fast-transport-belt", "express-transport-belt", "inserter", "fast-inserter", "long-handed-inserter",
      "burner-inserter", "bulk-inserter", "stone-furnace", "steel-furnace", "electric-furnace", "electric-mining-drill", "burner-mining-drill",
      "radar", "pipe", "pipe-to-ground", "pump", "big-electric-pole", "medium-electric-pole", "substation", "pumpjack", "oil-refinery",
      "chemical-plant", "assembling-machine-1", "assembling-machine-2", "assembling-machine-3", "steam-engine", "boiler", "offshore-pump",
      "storage-tank", "solar-panel", "accumulator", "roboport", "beacon"}) do
    if prototypes.entity[t] then
      local c = s.count_entities_filtered{force = player, name = t}
      if c > 0 then cnt[t] = c end
    end
  end
  out("counts " .. serpent.line(cnt))
end)
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("save", nargs="?", help="save .zip (default: the most recent one)")
    ap.add_argument("--surface", default="nauvis", help="surface/planet to inspect (default nauvis)")
    a = ap.parse_args()
    fenv.require()
    save = a.save or fenv.latest_save()
    print(f"Save: {save}")
    with fenv.Instance("saveprobe", LUA.replace("__CFG__", fenv.long_string(json.dumps({"surface": a.surface}))), user_mods=True) as inst:
        inst.run_save(inst.copy_save(save), 2)
        lines = inst.tagged("SAVEPROBE")
        if not lines:
            raise SystemExit("Could not read the save:\n" + "\n".join(inst.errors(15)))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
