#!/usr/bin/env python3
"""Pastes blueprints into a copy of the player's save, builds them and lets the game run N minutes.

  save_sim.py --minutes 10                           -> forecast: the base as it is, 10 minutes ahead
  save_sim.py --bp plan.txt@-150,160 --minutes 20    -> paste plan.txt with the cursor at (-150,160)
  save_sim.py --bp book.txt#2 --bp other.txt ...     -> blueprint 2 of a book (index as in bp.py summary)
  save_sim.py --finish-ghosts -330,0,-90,300 ...     -> as if the ghosts already placed there were built
                                                        (repeat --finish-ghosts for several areas)
  save_sim.py ... --items iron-plate,rail --area smelter=-130,180,-110,215 --probe -115.5,194.5

Blueprints are pasted in the given order like Ctrl+Shift+click (superforced): whatever is in the way and
gets marked for deconstruction (player buildings, trees, rocks) is removed, the tiles they bring
(landfill...) are placed, every ghost is built and upgrade orders (an assembler 2 pasted on an
assembler 1...) are applied keeping splitter settings. Without @x,y a blueprint with absolute snapping
goes to the grid cell that contains the base centre (any point inside the intended cell works; give
@x,y when the cell is another one); other blueprints need @x,y.

Every --every minutes it prints production per minute of --items (default: science packs, plates,
advanced circuits), recipe:status counts of the machines inside each --area, the research in progress,
how many electric networks have consumers and how many machines have no/low power. At the end: what
is on each --probe belt (per lane), the packs inside labs, power use of the biggest network, and the
player's buildings that died (the character is indestructible, so the run never ends early). --attacks adds
where attacks come from and what they destroy (defence questions: run 45-60 min).

Hooks (Lua files, run with pcall; `s` surface, `f` force, `out(...)` prints, `pos(e)`, `json(t)`, set storage.texto to
print a line at the end): --pre-lua before pasting, --post-lua after building, --tick-lua every tick
(receives `d`, ticks since start), --end-lua at the end. --research marks technologies as researched
before pasting (for recipes the blueprint needs). Never touches the original save.
"""
import argparse, glob, json, os, re, shutil, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bp, fenv  # noqa: E402

DEFAULT_ITEMS = ["automation-science-pack", "logistic-science-pack", "military-science-pack",
                 "chemical-science-pack", "production-science-pack", "utility-science-pack",
                 "iron-plate", "copper-plate", "steel-plate", "advanced-circuit"]

LUA = r'''
local CFG = helpers.json_to_table(__CFG__)
local function out(...)
  local t = {}
  for i = 1, select("#", ...) do local v = select(i, ...); t[#t+1] = type(v) == "table" and serpent.line(v) or tostring(v) end
  log("SAVESIM " .. table.concat(t, " "):gsub("\n", " | "))
end
local ST = {} for k, v in pairs(defines.entity_status) do ST[v] = k end
local POWERED = {"assembling-machine", "furnace", "mining-drill", "lab", "inserter", "electric-turret", "roboport",
  "radar", "beacon", "pump", "rocket-silo", "lamp", "accumulator", "agricultural-tower", "electric-energy-interface"}

local function key(e)  -- LuaEntity wrappers are not the same table twice: key by unit number or name+position
  return e.unit_number or string.format("%s@%.2f,%.2f", e.name, e.position.x, e.position.y)
end

local function hook(name, d)
  local code = CFG.hooks[name]
  if not code or code == "" then return end
  local s, f = game.surfaces[CFG.surface], game.forces.player
  local chunk, err = load(code, name, "t", setmetatable({s = s, f = f, out = out, d = d, json = helpers.table_to_json,
    pos = function(e) local p = e.position or e return string.format("(%.1f,%.1f)", p.x or p[1], p.y or p[2]) end}, {__index = _ENV}))
  if not chunk then out(name .. " compile error: " .. err) CFG.hooks[name] = nil return end
  local ok, e2 = pcall(chunk)
  if not ok then out(name .. " error: " .. tostring(e2)) CFG.hooks[name] = nil end
end

local function research_line(f)
  local r = f.current_research
  return r and string.format("%s %.0f%%", r.name, f.research_progress * 100) or "none"
end

local function power_line(s)
  local nets, bad = {}, 0
  for _, e in pairs(s.find_entities_filtered{force = "player", type = POWERED}) do
    local id = e.electric_network_id
    if id then nets[id] = true end
    local st = e.status
    if st == defines.entity_status.no_power or st == defines.entity_status.low_power then bad = bad + 1 end
  end
  local n = 0 for _ in pairs(nets) do n = n + 1 end
  return string.format("networks_with_consumers=%d no_or_low_power=%d", n, bad)
end

local function main_pole(s)
  local count, pole = {}, {}
  for _, p in pairs(s.find_entities_filtered{force = "player", type = "electric-pole"}) do
    local id = p.electric_network_id
    if id then count[id] = (count[id] or 0) + 1; pole[id] = pole[id] or p end
  end
  local best, bn = nil, -1
  for id, c in pairs(count) do if c > bn then best, bn = id, c end end
  return best and pole[best], bn
end

local function production(f)
  local ps = f.get_item_production_statistics(CFG.surface)
  local fs = f.get_fluid_production_statistics(CFG.surface)
  local r = {}
  for _, it in pairs(CFG.items) do
    if prototypes.item[it] then r[it] = ps.get_input_count(it)
    elseif prototypes.fluid[it] then r[it] = fs.get_input_count(it)     -- a fluid name here used to abort the run
    else
      r[it] = 0
      if not storage.warned then out("unknown item or fluid in --items: " .. it) end
    end
  end
  storage.warned = true
  return r
end

local function ghost_summary(s)
  local c, n = {}, 0
  for _, gh in pairs(s.find_entities_filtered{name = "entity-ghost", force = "player"}) do
    if not (storage.gh0 and storage.gh0[gh.unit_number]) then c[gh.ghost_name] = (c[gh.ghost_name] or 0) + 1; n = n + 1 end
  end
  local t = {} for k, v in pairs(c) do t[#t+1] = {k, v} end table.sort(t, function(a, b) return a[2] > b[2] end)
  local ts = {} for i = 1, math.min(8, #t) do ts[#ts+1] = t[i][1] .. "=" .. t[i][2] end
  return n, table.concat(ts, " "), c
end

local function main_network(s, f)
  local net
  for _, n in pairs(f.logistic_networks[s.name] or {}) do if not net or #n.cells > #net.cells then net = n end end
  return net
end

-- what a robot-built blueprint gets and a revived one does not: its item requests (train fuel, modules) and
-- trains switching to automatic once complete
local function finish_like_robots(s, proxies_before, trains_before)
  local filled, autos = 0, 0
  for _, pr in pairs(s.find_entities_filtered{name = "item-request-proxy", force = "player"}) do
    local tgt = pr.proxy_target
    if tgt and tgt.valid and not proxies_before[pr.unit_number] then
      for _, plan in pairs(pr.insert_plan or {}) do
        for _, pos in pairs((plan.items or {}).in_inventory or {}) do
          local inv = tgt.get_inventory(pos.inventory)
          if inv then filled = filled + inv.insert{name = plan.id.name, count = pos.count or 1} end
        end
      end
      pr.destroy()
    end
  end
  for _, l in pairs(s.find_entities_filtered{type = "locomotive", force = "player"}) do
    local t = l.train
    if not trains_before[t.id] and t.manual_mode and t.schedule and #t.schedule.records > 0 then t.manual_mode = false; autos = autos + 1 end
  end
  if filled + autos > 0 then out(string.format("item requests delivered: %d items; trains set to automatic: %d (robots do both in the real game)", filled, autos)) end
end

local function areas_line(s)
  local z = {}
  for _, a in pairs(CFG.areas) do
    local m = {}
    for _, e in pairs(s.find_entities_filtered{area = a.box, type = {"assembling-machine", "furnace", "lab", "mining-drill", "rocket-silo"}}) do
      local rn = "-"
      if e.type == "furnace" or e.type == "assembling-machine" or e.type == "rocket-silo" then
        local r = e.get_recipe() or (e.type == "furnace" and e.previous_recipe)
        if r then rn = r.name end
        if type(rn) ~= "string" then rn = rn and rn.name or "?" end  -- previous_recipe.name may be a prototype
      else rn = e.type end
      local k = rn .. ":" .. (ST[e.status] or "?")
      m[k] = (m[k] or 0) + 1
    end
    local t = {} for k, v in pairs(m) do t[#t+1] = k .. "=" .. v end
    table.sort(t)
    z[#z+1] = a.name .. "{" .. table.concat(t, " ") .. "}"
  end
  return table.concat(z, " ")
end

local function setup(s, f)
  for _, pl in pairs(game.players) do if pl.character then pl.character.destructible = false end end
  hook("pre", 0)
  for _, t in pairs(CFG.research) do
    if f.technologies[t] then f.technologies[t].researched = true; out("researched " .. t) else out("unknown technology " .. t) end
  end
  local cursor_default
  if #CFG.bps > 0 then
    local sx, sy, n = 0, 0, 0
    for _, e in pairs(s.find_entities_filtered{force = "player", type = POWERED}) do sx = sx + e.position.x; sy = sy + e.position.y; n = n + 1 end
    cursor_default = n > 0 and {sx / n, sy / n} or {0, 0}
  end
  local tg_before, decon_before, up_before = {}, {}, {}
  local proxies_before, trains_before = {}, {}
  for _, pr in pairs(s.find_entities_filtered{name = "item-request-proxy"}) do proxies_before[pr.unit_number] = true end
  for _, t in pairs(game.train_manager.get_trains{surface = s}) do trains_before[t.id] = true end
  storage.gh0 = {}
  for _, gh in pairs(s.find_entities_filtered{name = "entity-ghost", force = "player"}) do storage.gh0[gh.unit_number] = true end
  for _, tg in pairs(s.find_entities_filtered{name = "tile-ghost"}) do tg_before[tg.unit_number] = true end
  if not CFG.apply_pending then
    for _, e in pairs(s.find_entities_filtered{to_be_deconstructed = true}) do decon_before[key(e)] = true end
    for _, e in pairs(s.find_entities_filtered{to_be_upgraded = true}) do up_before[key(e)] = true end
  end
  local ghosts = {}
  for i, b in pairs(CFG.bps) do
    local inv = game.create_inventory(1)
    inv[1].import_stack(b.str)
    local at = b.at or cursor_default
    local g = inv[1].build_blueprint{surface = s, force = "player", position = at, build_mode = defines.build_mode.superforced, skip_fog_of_war = CFG.robots ~= nil}
    inv.destroy()
    for _, x in pairs(g) do ghosts[#ghosts+1] = x end
    out(string.format("blueprint %d (%s) at (%.1f,%.1f): ghosts=%d%s", i, b.name, at[1], at[2], #g,
      (b.count and #g < b.count) and string.format("  <-- %d of its %d entities were NOT placed (map never charted there, or blocked)", b.count - #g, b.count) or ""))
  end
  local dark = 0
  for _, gh in pairs(ghosts) do
    if gh.valid then local p = gh.position if not f.is_chunk_charted(s, {math.floor(p.x / 32), math.floor(p.y / 32)}) then dark = dark + 1 end end
  end
  if dark > 0 then out(string.format("WARNING: %d ghosts are in map the player never charted; the real game silently skips them (this run built them anyway: use --robots to see what really happens)", dark)) end
  if CFG.robots then
    local net = main_network(s, f)
    if not net then out("--robots: the save has no logistic network") hook("post", 0) return end
    if CFG.robots == "give" then
      local need = {}
      for _, gh in pairs(ghosts) do
        if gh.valid and gh.name == "entity-ghost" then
          local it = gh.ghost_prototype.items_to_place_this
          if it and it[1] then need[it[1].name] = (need[it[1].name] or 0) + (it[1].count or 1) end
          for _, plan in pairs(gh.insert_plan or {}) do
            for _, pos in pairs((plan.items or {}).in_inventory or {}) do need[plan.id.name] = (need[plan.id.name] or 0) + (pos.count or 1) end
          end
        end
      end
      local short = {}
      for n, k in pairs(need) do local got = net.insert{name = n, count = k} if got < k then short[#short+1] = n .. " " .. got .. "/" .. k end end
      out("--robots give: the blueprint's items were put into the biggest logistic network" .. (#short > 0 and (" (no room for: " .. table.concat(short, ", ") .. ")") or ""))
    end
    out(string.format("--robots: nothing is built by magic; %d construction robots (%d free) in the biggest network do it", net.all_construction_robots, net.available_construction_robots))
    hook("post", 0)
    return
  end
  if CFG.finish and #CFG.finish > 0 then
    local n0, seen = #ghosts, {}
    for _, area in pairs(CFG.finish) do
      for _, g in pairs(s.find_entities_filtered{name = "entity-ghost", force = "player", area = area}) do
        if not seen[g.unit_number] then seen[g.unit_number] = true ghosts[#ghosts+1] = g end
      end
      for _, tg in pairs(s.find_entities_filtered{name = "tile-ghost", force = "player", area = area}) do tg_before[tg.unit_number] = nil end
    end
    out(string.format("existing ghosts to finish in the area(s): %d", #ghosts - n0))
  end
  if #ghosts == 0 and not CFG.apply_pending then hook("post", 0) return end
  local nd, nu = 0, 0 for _ in pairs(decon_before) do nd = nd + 1 end for _ in pairs(up_before) do nu = nu + 1 end
  if nd + nu > 0 then out(string.format("left as they were (orders already in the save; --apply-pending carries them out): %d marked for deconstruction, %d for upgrade", nd, nu)) end
  local removed = 0
  for _, e in pairs(s.find_entities_filtered{to_be_deconstructed = true}) do
    if e.valid and not decon_before[key(e)] and (e.force.name == "player" or e.type == "tree" or e.type == "simple-entity" or e.type == "cliff") then e.destroy(); removed = removed + 1 end
  end
  local tiles = 0
  for _, tg in pairs(s.find_entities_filtered{name = "tile-ghost"}) do
    if tg.valid and not tg_before[tg.unit_number] and tg.revive() then tiles = tiles + 1 end
  end
  local built, total, why = 0, 0, {}
  for _, gh in pairs(ghosts) do if gh.valid and gh.name == "entity-ghost" then total = total + 1 end end
  for pass = 1, 4 do
    for _, gh in pairs(ghosts) do
      if gh.valid and gh.name == "entity-ghost" then
        local nm, p = gh.ghost_name, gh.position
        if gh.revive() then built = built + 1
        elseif pass == 4 then
          local blk = s.find_entities_filtered{area = gh.bounding_box, to_be_deconstructed = false}
          local who = {}
          for _, o in pairs(blk) do if o ~= gh and o.valid and o.name ~= "entity-ghost" and o.type ~= "resource" then who[#who+1] = o.name end end
          why[#why+1] = string.format("%s(%.1f,%.1f)%s", nm, p.x, p.y, #who > 0 and ("<-" .. table.concat(who, "+")) or "")
        end
      end
    end
  end
  local up, upf = 0, 0
  for _, e in pairs(s.find_entities_filtered{to_be_upgraded = true}) do
    if e.valid and not up_before[key(e)] then
      local tp = e.get_upgrade_target()
      if tp then
        local p = {name = tp.name, position = e.position, direction = e.direction, force = e.force, fast_replace = true, spill = false, create_build_effect_smoke = false}
        if e.type == "underground-belt" then p.type = e.belt_to_ground_type end
        local sp
        if e.type == "splitter" then sp = {e.splitter_input_priority, e.splitter_output_priority, e.splitter_filter} end
        local n = s.create_entity(p)
        if n then
          up = up + 1
          if sp then n.splitter_input_priority = sp[1]; n.splitter_output_priority = sp[2]; if sp[3] then n.splitter_filter = sp[3] end end
        else upf = upf + 1 end
      end
    end
  end
  out(string.format("removed_in_the_way=%d tiles_placed=%d ghosts=%d built=%d failures=%d upgraded=%d upgrade_failures=%d",
    removed, tiles, total, built, #why, up, upf))
  for i = 1, math.min(#why, 15) do out("  not built: " .. why[i]) end
  if total - built - #why > 0 then
    out(string.format("WARNING: %d ghosts vanished without being built: the blueprint has entities on top of each other (e.g. a pole on a curved rail); the first one built wins", total - built - #why))
  end
  finish_like_robots(s, proxies_before, trains_before)
  hook("post", 0)
end

local function belt_line(s, x, y)
  local b = s.find_entities_filtered{position = {x, y}, type = {"transport-belt", "underground-belt", "splitter"}}[1]
  if not b then return "no belt" end
  local c = {}
  for l = 1, b.get_max_transport_line_index() do
    for _, it in pairs(b.get_transport_line(l).get_contents()) do c[#c+1] = "lane" .. l .. ":" .. it.name .. "=" .. it.count end
  end
  return string.format("%s dir=%d [%s]", b.name, b.direction, table.concat(c, ", "))
end

local function finish(s, f)
  if CFG.robots then
    local n, top, c = ghost_summary(s)
    local net, miss = main_network(s, f), {}
    for name in pairs(c) do
      local it = prototypes.entity[name].items_to_place_this
      if it and it[1] and net and net.get_item_count(it[1].name) == 0 then miss[#miss+1] = it[1].name end
    end
    out(string.format("robots: %d ghosts not built after %.0f min %s%s", n, CFG.T / 3600, top,
      #miss > 0 and (" | not in the network: " .. table.concat(miss, ", ")) or ""))
  end
  for _, p in pairs(CFG.probes) do out(string.format("probe (%.1f,%.1f) %s", p[1], p[2], belt_line(s, p[1], p[2]))) end
  local z, nl = {}, 0
  for _, l in pairs(s.find_entities_filtered{type = "lab", force = "player"}) do
    nl = nl + 1
    for _, it in pairs(l.get_inventory(defines.inventory.lab_input).get_contents()) do z[it.name] = (z[it.name] or 0) + it.count end
  end
  local tz = {} for k, v in pairs(z) do tz[#tz+1] = k .. "=" .. v end table.sort(tz)
  out("labs " .. nl .. " holding: " .. table.concat(tz, " "))
  out("research: " .. research_line(f))
  local pole, np = main_pole(s)
  if pole then
    local es = pole.electric_network_statistics
    local function sum(cat)
      local t = 0
      for name, _ in pairs(cat == "input" and es.input_counts or es.output_counts) do
        t = t + es.get_flow_count{name = name, category = cat, precision_index = defines.flow_precision_index.one_minute}
      end
      return t * 60 / 1e6
    end
    out(string.format("main electric network (%d poles): consumption %.1f MW, production %.1f MW (last minute)", np, sum("input"), sum("output")))
  end
  out(power_line(s))
  out(string.format("player buildings destroyed: %d", storage.dead or 0))
  if CFG.attacks then attacks_report() end
  hook("fin", CFG.T)
  if storage.texto then out("hooks: " .. tostring(storage.texto)) end
end

script.on_event(defines.events.on_entity_died, function(ev)
  local e = ev.entity
  if e.force.name == "player" then
    storage.dead = (storage.dead or 0) + 1
    if storage.dead <= 8 then out(string.format("died %s (%.0f,%.0f) at min %.1f", e.name, e.position.x, e.position.y, (ev.tick - (storage.t0 or 0)) / 3600)) end
    if CFG.attacks then
      local k = math.floor(e.position.x / 50) * 50 .. "," .. math.floor(e.position.y / 50) * 50
      storage.cells = storage.cells or {}
      local c = storage.cells[k] or {n = 0, t = ev.tick, names = {}} storage.cells[k] = c
      c.n = c.n + 1 c.names[e.name] = (c.names[e.name] or 0) + 1
      if ev.cause and ev.cause.valid then c.by = ev.cause.name end
    end
  end
end)

if CFG.attacks then
  script.on_event(defines.events.on_unit_group_finished_gathering, function(ev)
    local g = ev.group if not (g and g.valid) then return end
    storage.groups = storage.groups or {}
    local c = {} for _, m in pairs(g.members) do c[m.name] = (c[m.name] or 0) + 1 end
    local t = {} for k, v in pairs(c) do t[#t+1] = k .. "=" .. v end table.sort(t)
    table.insert(storage.groups, {tick = ev.tick, n = #g.members, x = g.position.x, y = g.position.y, what = table.concat(t, " ")})
  end)
  script.on_event(defines.events.on_biter_base_built, function(ev)
    local e = ev.entity if not (e and e.valid) then return end
    storage.newbases = storage.newbases or {}
    table.insert(storage.newbases, string.format("min %.1f %s (%.0f,%.0f)", (ev.tick - storage.t0) / 3600, e.name, e.position.x, e.position.y))
  end)
end

function attacks_tick(s, d)
  if d % 300 ~= 0 or d < 300 then return end
  local A = CFG.attacks_area
  local us = s.find_entities_filtered{force = "enemy", type = "unit", area = A}
  if #us < 5 then return end
  storage.track = storage.track or {}
  if #storage.track >= 80 then return end
  local sx, sy, x1, y1, x2, y2 = 0, 0, 1e9, 1e9, -1e9, -1e9
  for _, u in pairs(us) do local p = u.position sx = sx + p.x sy = sy + p.y x1 = math.min(x1, p.x) y1 = math.min(y1, p.y) x2 = math.max(x2, p.x) y2 = math.max(y2, p.y) end
  table.insert(storage.track, string.format("min %.1f %d enemies near the base, centre (%.0f,%.0f) spread x %.0f..%.0f y %.0f..%.0f", d / 3600, #us, sx / #us, sy / #us, x1, x2, y1, y2))
end

function attacks_report()
  local gs = storage.groups or {}
  local big = {} for _, g in pairs(gs) do if g.n >= 20 then big[#big+1] = g end end
  out(string.format("attack groups gathered: %d (%d of 20+); largest:", #gs, #big))
  table.sort(big, function(a, b) return a.n > b.n end)
  for i = 1, math.min(10, #big) do local g = big[i] out(string.format("  min %.1f at (%.0f,%.0f) %d: %s", (g.tick - storage.t0) / 3600, g.x, g.y, g.n, g.what)) end
  for _, l in pairs(storage.track or {}) do out("  " .. l) end
  out("new nests from expansion: " .. #(storage.newbases or {}) .. " " .. table.concat(storage.newbases or {}, "; ", 1, math.min(10, #(storage.newbases or {}))))
  local r = {} for k, c in pairs(storage.cells or {}) do r[#r+1] = {c.t, k, c} end
  table.sort(r, function(a, b) return a[1] < b[1] end)
  out("losses by 50x50 cell, in the order they started:")
  for _, x in pairs(r) do
    local c = x[3] local t = {} for nm, v in pairs(c.names) do t[#t+1] = {nm, v} end table.sort(t, function(a, b) return a[2] > b[2] end)
    local ts = {} for i = 1, math.min(4, #t) do ts[#ts+1] = t[i][1] .. "=" .. t[i][2] end
    out(string.format("  cell x %s..+50 y ..+50 from min %.1f: %d lost, last killer %s [%s]", x[2], (x[1] - storage.t0) / 3600, c.n, tostring(c.by), table.concat(ts, " ")))
  end
end

script.on_event(defines.events.on_tick, function(ev)
  local s, f = game.surfaces[CFG.surface], game.forces.player
  if not storage.t0 then
    storage.t0 = ev.tick
    setup(s, f)
    storage.c = production(f)
    out("min 0: research " .. research_line(f) .. " | " .. power_line(s))
  end
  local d = ev.tick - storage.t0
  if CFG.hooks.tick then hook("tick", d) end
  if CFG.attacks then
    if not CFG.attacks_area then
      local x1, y1, x2, y2 = 1e9, 1e9, -1e9, -1e9
      for _, e in pairs(s.find_entities_filtered{force = "player", type = {"assembling-machine", "furnace", "mining-drill", "lab", "ammo-turret", "electric-turret", "generator", "boiler"}}) do
        local p = e.position x1 = math.min(x1, p.x) y1 = math.min(y1, p.y) x2 = math.max(x2, p.x) y2 = math.max(y2, p.y)
      end
      CFG.attacks_area = {{x1 - 40, y1 - 40}, {x2 + 40, y2 + 40}}
      out(string.format("attacks: watching enemies inside x %.0f..%.0f y %.0f..%.0f", x1 - 40, x2 + 40, y1 - 40, y2 + 40))
    end
    attacks_tick(s, d)
  end
  if d > 0 and d % CFG.every == 0 and d <= CFG.T then
    local now, r = production(f), {}
    for _, it in pairs(CFG.items) do r[#r+1] = it:gsub("%-science%-pack", "") .. "=" .. string.format("%.0f", (now[it] - storage.c[it]) * 3600 / CFG.every) end
    storage.c = now
    out(string.format("min %.0f per_min: %s | research %s | %s %s", d / 3600, table.concat(r, " "), research_line(f), power_line(s), areas_line(s)))
    if CFG.robots then
      local n, top = ghost_summary(s)
      local tr = {}
      for _, l in pairs(s.find_entities_filtered{type = "locomotive", force = "player"}) do
        tr[#tr+1] = string.format("[%d cars %s]", #l.train.carriages, l.train.manual_mode and "manual" or "automatic")
      end
      out(string.format("  robots: %d ghosts left %s %s", n, top, table.concat(tr, " ")))
    end
  end
  if d == CFG.T and not storage.finished then storage.finished = true; finish(s, f) end
end)
'''


def lua_long_string(text):
    """Wrap text in a Lua long bracket whose level doesn't occur inside it."""
    n = 0
    while f"]{'=' * n}]" in text:
        n += 1
    return f"[{'=' * n}[{text}]{'=' * n}]"


def load_bp(spec):
    m = re.match(r"^(.*?)(?:#([\d.]+))?(?:@(-?[\d.]+),(-?[\d.]+))?$", spec)
    path, index, x, y = m.group(1), m.group(2), m.group(3), m.group(4)
    obj = bp.pick(bp.decode(bp.read_src(path)), index)
    k, body = bp.kind(obj)
    if k != "blueprint":
        raise SystemExit(f"{spec}: is a {k}, not a blueprint (use #N to pick one from a book; bp.py summary lists them)")
    at = [float(x), float(y)] if x is not None else None
    if at is None and not body.get("absolute-snapping"):
        raise SystemExit(f"{spec}: the blueprint has no absolute snapping; give the cursor position as {path}@x,y")
    return {"str": bp.encode(obj), "at": at, "name": os.path.basename(path) + (f"#{index}" if index else ""),
            "count": len(body.get("entities", []))}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save", help="save .zip (default: the most recent one)")
    ap.add_argument("--bp", action="append", default=[], help="file.txt[#index][@x,y]; repeat for several, pasted in order")
    ap.add_argument("--minutes", type=float, required=True)
    ap.add_argument("--every", type=float, default=2, help="minutes between reports (default 2)")
    ap.add_argument("--items", help="comma list of items to measure per minute")
    ap.add_argument("--area", action="append", default=[], help="name=x1,y1,x2,y2 (repeatable)")
    ap.add_argument("--probe", action="append", default=[], help="x,y of a belt to show at the end (repeatable)")
    ap.add_argument("--research", default="", help="comma list of technologies to mark researched first")
    for h in ("pre", "post", "tick", "end"):
        ap.add_argument(f"--{h}-lua", help=f"Lua file run {'each tick' if h == 'tick' else h}")
    ap.add_argument("--apply-pending", action="store_true",
                    help="also carry out deconstruction/upgrade orders that were already in the save (as if the robots finished)")
    ap.add_argument("--finish-ghosts", metavar="X1,Y1,X2,Y2", action="append", default=[],
                    help="also build the ghosts (and tile ghosts) already in the save inside this area: 'what if I finish building' (repeatable)")
    ap.add_argument("--attacks", action="store_true",
                    help="report biter attacks: groups that gather (size, units), enemies moving inside the base over time "
                         "(shows the entry point), new nests from expansion, and losses per 50x50 cell in the order they started")
    ap.add_argument("--robots", nargs="?", const="stock", choices=["stock", "give"],
                    help="don't build by magic: place the ghosts like the game does (none in never-charted map) and let "
                         "the save's own construction robots build with what the network has; 'give' first puts the "
                         "blueprint's items into the biggest network. Reports ghosts left and trains every --every min")
    ap.add_argument("--out-dir", help="copy script-output files (helpers.write_file) here")
    ap.add_argument("--surface", default="nauvis")
    ap.add_argument("--keep", action="store_true", help="keep the temporary folder")
    a = ap.parse_args()

    save = a.save or fenv.latest_save()
    areas = []
    for spec in a.area:
        name, coords = spec.split("=", 1)
        x1, y1, x2, y2 = map(float, coords.split(","))
        areas.append({"name": name, "box": [[min(x1, x2), min(y1, y2)], [max(x1, x2), max(y1, y2)]]})
    hooks = {}
    for h, key in (("pre", "pre"), ("post", "post"), ("tick", "tick"), ("end", "fin")):
        p = getattr(a, f"{h}_lua")
        if p:
            hooks[key] = open(p, encoding="utf-8").read()
    T = int(a.minutes * 3600)
    cfg = {"bps": [load_bp(s) for s in a.bp], "T": T, "every": max(60, int(a.every * 3600)),
           "items": a.items.split(",") if a.items else DEFAULT_ITEMS, "areas": areas,
           "probes": [list(map(float, p.split(","))) for p in a.probe],
           "research": [t for t in a.research.split(",") if t], "hooks": hooks, "surface": a.surface, "apply_pending": a.apply_pending, "attacks": a.attacks, "robots": a.robots,
           "finish": [(lambda v: [[min(v[0], v[2]), min(v[1], v[3])], [max(v[0], v[2]), max(v[1], v[3])]])(
               list(map(float, spec.split(",")))) for spec in a.finish_ghosts]}
    payload = json.dumps(cfg)
    print(f"save: {save}  simulating {a.minutes:g} min", flush=True)
    t0 = time.time()
    with fenv.Instance("savesim", LUA.replace("__CFG__", lua_long_string(payload)), user_mods=True, keep=a.keep) as inst:
        inst.run_save(inst.copy_save(save), T + 5)
        lines = inst.tagged("SAVESIM")
        print("\n".join(lines) if lines else "(no output: the save did not load? see errors below)")
        for l in inst.errors(8):
            print("!", l)
        if a.out_dir:
            for p in glob.glob(os.path.join(inst.write, "script-output", "*")):
                shutil.copy(p, a.out_dir)
    print(f"wall time {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
