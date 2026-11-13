#!/usr/bin/env python3
"""Finds WHERE the problems in the player's save are (save_probe.py says what; this says where).

  save_diag.py                  -> the most recent save
  save_diag.py --save x.zip --cell 24 --top 25

Reports, with world coordinates you can paste into save_lua.py / save_export.py:
  - electric networks: id, poles, bounding box, generators, consumers, and machines covered by no
    pole at all; a network with consumers but no generators is a block someone forgot to connect;
  - labs: what the current research needs, how many labs lack each pack, and each lab group;
  - science: every science assembler grouped by status (full_output + labs missing that pack =
    the belt to the labs is blocked or the pack goes elsewhere);
  - clusters of stopped machines (assemblers, furnaces, drills) by recipe/resource and status,
    grouped in cells of --cell tiles, biggest first;
  - unfinished construction: entity ghosts and upgrade/deconstruction orders by area, robot networks,
    ghosts no roboport reaches, and items the ghosts need vs what robots and the player have.
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_lua  # noqa: E402

LUA = r'''
local CELL, TOP = __CELL__, __TOP__
local st = {} for k, v in pairs(defines.entity_status) do st[v] = k end
local function cellkey(p) return math.floor(p.x / CELL) .. "," .. math.floor(p.y / CELL) end
local function bbox(list)
  local x1, y1, x2, y2 = 1e9, 1e9, -1e9, -1e9
  for _, p in pairs(list) do x1 = math.min(x1, p.x) y1 = math.min(y1, p.y) x2 = math.max(x2, p.x) y2 = math.max(y2, p.y) end
  return string.format("x %.0f..%.0f y %.0f..%.0f", x1, x2, y1, y2)
end

-- electric networks
local nets = {}
local function net(id) nets[id] = nets[id] or {poles = {}, gen = 0, cons = 0, bad = 0, badpos = {}, kinds = {}} return nets[id] end
for _, p in pairs(s.find_entities_filtered{type = "electric-pole", force = f}) do
  if p.electric_network_id then table.insert(net(p.electric_network_id).poles, p.position) end
end
for _, g in pairs(s.find_entities_filtered{type = {"generator", "solar-panel", "accumulator", "burner-generator", "electric-energy-interface", "fusion-generator"}, force = f}) do
  if g.electric_network_id then local n = net(g.electric_network_id) n.gen = n.gen + 1 end
end
local CONS = {"assembling-machine", "furnace", "mining-drill", "inserter", "lab", "roboport", "radar", "electric-turret", "beacon", "pump", "rocket-silo", "lamp"}
local nopole, nopolepos, nopolekinds = 0, {}, {}
for _, e in pairs(s.find_entities_filtered{type = CONS, force = f}) do
  if e.type ~= "furnace" or (e.prototype.electric_energy_source_prototype ~= nil) then
    if e.type ~= "inserter" or e.prototype.electric_energy_source_prototype ~= nil then
      local id = e.electric_network_id
      if id then
        local n = net(id) n.cons = n.cons + 1
        if e.status == defines.entity_status.no_power or e.status == defines.entity_status.low_power then n.bad = n.bad + 1 table.insert(n.badpos, e.position) end
        n.kinds[e.name] = (n.kinds[e.name] or 0) + 1
      else
        nopole = nopole + 1 table.insert(nopolepos, e.position) nopolekinds[e.name] = (nopolekinds[e.name] or 0) + 1
      end
    end
  end
end
local ids = {} for id, _ in pairs(nets) do ids[#ids+1] = id end
table.sort(ids, function(a, b) return nets[a].cons > nets[b].cons end)
out("== electric networks: " .. #ids)
for _, id in pairs(ids) do
  local n = nets[id]
  local k = {} for name, c in pairs(n.kinds) do k[#k+1] = {name, c} end table.sort(k, function(a, b) return a[2] > b[2] end)
  local ks = {} for i = 1, math.min(4, #k) do ks[#ks+1] = k[i][1] .. "=" .. k[i][2] end
  local flag = (n.cons > 0 and n.gen == 0) and "  <-- NO GENERATORS (not connected to the main grid?)" or ""
  out(string.format("net %d: poles=%d generators=%d consumers=%d no/low_power=%d area %s [%s]%s", id, #n.poles, n.gen, n.cons, n.bad,
    #n.poles > 0 and bbox(n.poles) or "-", table.concat(ks, " "), flag))
  if n.gen == 0 and #n.poles > 0 then
    -- nearest pole of a network that has generators, to say where to bridge
    local best, bd, bp, bid
    for _, id2 in pairs(ids) do if nets[id2].gen > 0 then for _, q in pairs(nets[id2].poles) do for _, p in pairs(n.poles) do
      local d = (p.x - q.x) ^ 2 + (p.y - q.y) ^ 2 if not bd or d < bd then bd, best, bp, bid = d, q, p, id2 end end end end end
    if best then out(string.format("   closest powered pole (%.1f,%.1f) of net %d is %.1f tiles from this net's pole (%.1f,%.1f)", best.x, best.y, bid, math.sqrt(bd), bp.x, bp.y)) end
  end
end
if nopole > 0 then
  local k = {} for name, c in pairs(nopolekinds) do k[#k+1] = name .. "=" .. c end
  out(string.format("not covered by any pole: %d (%s) area %s", nopole, table.concat(k, " "), bbox(nopolepos)))
end

-- labs and research
local cr = f.current_research
out("== research: " .. (cr and string.format("%s %.0f%%", cr.name, f.research_progress * 100) or "NONE (queue empty)"))
local labs = s.find_entities_filtered{type = "lab", force = f}
if cr then
  local need = {} for _, ing in pairs(cr.research_unit_ingredients) do need[#need+1] = ing.name end
  local lack, groups = {}, {}
  for _, l in pairs(labs) do
    local inv = l.get_inventory(defines.inventory.lab_input)
    local miss = {}
    for _, n in pairs(need) do if inv.get_item_count(n) == 0 then miss[#miss+1] = n:gsub("%-science%-pack", "") lack[n] = (lack[n] or 0) + 1 end end
    local key = cellkey(l.position) .. "|" .. st[l.status] .. "|" .. table.concat(miss, "+")
    groups[key] = groups[key] or {n = 0, pos = {}} groups[key].n = groups[key].n + 1 table.insert(groups[key].pos, l.position)
  end
  local ls = {} for _, n in pairs(need) do ls[#ls+1] = n:gsub("%-science%-pack", "") .. " missing in " .. (lack[n] or 0) end
  out(string.format("labs=%d needs: %s", #labs, table.concat(ls, ", ")))
  for key, g in pairs(groups) do local _, stt, miss = key:match("([^|]*)|([^|]*)|(.*)") out(string.format("  %d labs %s missing[%s] at %s", g.n, stt, miss, bbox(g.pos))) end
end

-- science assemblers
out("== science assemblers")
local sci = {}
for _, a in pairs(s.find_entities_filtered{type = "assembling-machine", force = f}) do
  local r = a.get_recipe()
  if r and r.name:find("science%-pack") then
    local key = r.name .. "|" .. st[a.status] .. "|" .. cellkey(a.position)
    sci[key] = sci[key] or {n = 0, pos = {}} sci[key].n = sci[key].n + 1 table.insert(sci[key].pos, a.position)
  end
end
local sk = {} for k, _ in pairs(sci) do sk[#sk+1] = k end table.sort(sk)
for _, k in pairs(sk) do local r, stt = k:match("([^|]*)|([^|]*)") out(string.format("  %s %s x%d at %s", r:gsub("%-science%-pack", ""), stt, sci[k].n, bbox(sci[k].pos))) end

-- stopped machine clusters
local cl = {}
for _, e in pairs(s.find_entities_filtered{type = {"assembling-machine", "furnace", "mining-drill"}, force = f}) do
  if e.status ~= defines.entity_status.working then
    local what
    if e.type == "mining-drill" then what = "drill:" .. (e.mining_target and e.mining_target.name or "nothing")
    else local r = e.get_recipe() if not r and e.type == "furnace" then r = e.previous_recipe end
      local rn = r and r.name if rn and type(rn) ~= "string" then rn = rn.name end
      what = e.name .. ":" .. (rn or "no-recipe") end
    local key = what .. "|" .. st[e.status] .. "|" .. cellkey(e.position)
    cl[key] = cl[key] or {n = 0, pos = {}, what = what, st = st[e.status]}
    cl[key].n = cl[key].n + 1 table.insert(cl[key].pos, e.position)
  end
end
local cs = {} for _, c in pairs(cl) do cs[#cs+1] = c end table.sort(cs, function(a, b) return a.n > b.n end)
out(string.format("== stopped machine clusters (cell %d), top %d of %d", CELL, math.min(TOP, #cs), #cs))
for i = 1, math.min(TOP, #cs) do local c = cs[i] out(string.format("  %3d %s %s at %s", c.n, c.what, c.st, bbox(c.pos))) end

-- construction left
local gh = {}
for _, g in pairs(s.find_entities_filtered{name = "entity-ghost", force = f}) do
  local k = cellkey(g.position) gh[k] = gh[k] or {n = 0, pos = {}, names = {}} gh[k].n = gh[k].n + 1 table.insert(gh[k].pos, g.position)
  gh[k].names[g.ghost_name] = (gh[k].names[g.ghost_name] or 0) + 1
end
local gs = {} for _, g in pairs(gh) do gs[#gs+1] = g end table.sort(gs, function(a, b) return a.n > b.n end)
local tg = s.count_entities_filtered{name = "tile-ghost", force = f}
local up = s.count_entities_filtered{to_be_upgraded = true, force = f}
local de = s.count_entities_filtered{to_be_deconstructed = true, force = f}
out(string.format("== ghosts: %d entity ghosts in %d cells, %d tile ghosts, %d to upgrade, %d to deconstruct", s.count_entities_filtered{name = "entity-ghost", force = f}, #gs, tg, up, de))
for i = 1, math.min(TOP, #gs) do
  local g = gs[i] local k = {} for n, c in pairs(g.names) do k[#k+1] = {n, c} end table.sort(k, function(a, b) return a[2] > b[2] end)
  local ks = {} for j = 1, math.min(4, #k) do ks[#ks+1] = k[j][1] .. "=" .. k[j][2] end
  out(string.format("  %3d at %s [%s]", g.n, bbox(g.pos), table.concat(ks, " ")))
end
-- robots: can the ghosts be built?
local nets_l = f.logistic_networks[s.name] or {}
out("== robot networks: " .. #nets_l)
for i, ln in pairs(nets_l) do
  local cells = ln.cells
  local c = cells[1] and cells[1].owner
  out(string.format("  net %d: roboports=%d construction robots %d/%d idle, logistic %d/%d, first roboport %s", i, #cells,
    ln.available_construction_robots, ln.all_construction_robots, ln.available_logistic_robots, ln.all_logistic_robots, c and pos(c) or "-"))
end
local need, outside, outpos = {}, 0, {}
for _, g in pairs(s.find_entities_filtered{name = {"entity-ghost", "tile-ghost"}, force = f}) do
  for _, it in pairs(g.ghost_prototype.items_to_place_this or {}) do
    local nm = type(it) == "string" and it or it.name
    need[nm] = (need[nm] or 0) + (it.count or 1)
    break
  end
  local cov = false
  for _, ln in pairs(nets_l) do for _, cell in pairs(ln.cells) do if cell.is_in_construction_range(g.position) then cov = true break end end if cov then break end end
  if not cov then outside = outside + 1 table.insert(outpos, g.position) end
end
out(string.format("ghosts outside every robot construction area: %d%s", outside, outside > 0 and (" at " .. bbox(outpos)) or ""))
local have = {}
for _, ln in pairs(nets_l) do for _, it in pairs(ln.get_contents()) do have[it.name] = (have[it.name] or 0) + it.count end end
local pl = game.players[1]
local short = {}
for nm, c in pairs(need) do
  local inv = pl and pl.get_main_inventory() and pl.get_main_inventory().get_item_count(nm) or 0
  local h = have[nm] or 0
  short[#short+1] = {nm, c, h, inv}
end
table.sort(short, function(a, b) return (a[2] - a[3]) > (b[2] - b[3]) end)
local ln = {}
for _, x in pairs(short) do ln[#ln+1] = string.format("%s need %d / in robot storage %d / player %d%s", x[1], x[2], x[3], x[4], x[2] > x[3] + x[4] and " SHORT" or "") end
out("items for ghosts: " .. table.concat(ln, "; "))
'''

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save", help="save .zip (default: the most recent one)")
    ap.add_argument("--surface", default="nauvis")
    ap.add_argument("--cell", type=int, default=32, help="cluster size in tiles (default 32)")
    ap.add_argument("--top", type=int, default=20, help="how many clusters to list (default 20)")
    a = ap.parse_args()
    body = LUA.replace("__CELL__", str(a.cell)).replace("__TOP__", str(a.top))
    print("\n".join(save_lua.run(body, a.save, 0, a.surface)))
