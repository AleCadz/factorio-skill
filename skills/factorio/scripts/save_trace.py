#!/usr/bin/env python3
"""Follows a belt in the player's save and says where it goes, what it carries and where it stops.

  save_trace.py X,Y                 -> downstream from the belt at X,Y (follows splitters both ways)
  save_trace.py X,Y --up            -> upstream: where does what arrives here come from
  save_trace.py X,Y --after 600     -> let the game run 10 s first (states settle after loading)

Prints the path as runs of belts in one direction ("(x,y) -> (x,y) S 12 belts"), what each run
carries per lane (L = left, R = right, relative to belt direction) and how full it is, every
splitter (priority, filter), undergrounds, which inserters take from it (and into what machine),
and how each branch ends: into a machine/chest via inserter only, side-loaded into another belt,
a dead end, or a ghost (unbuilt) belt. Uses a copy of the save; never touches the original.
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import save_lua  # noqa: E402

LUA = r'''
local X, Y, UP, MAX = __X__, __Y__, __UP__, __MAX__
local BELT = {"transport-belt", "underground-belt", "splitter", "linked-belt", "loader", "loader-1x1"}
local DIR = {[0] = "N", [4] = "E", [8] = "S", [12] = "W"}
local function at(x, y) return s.find_entities_filtered{position = {x, y}, type = BELT, force = f}[1] end
local start = at(X, Y)
if not start then
  local g = s.find_entities_filtered{position = {X, Y}, name = "entity-ghost"}[1]
  out("no belt at " .. X .. "," .. Y .. (g and (" (there is a ghost " .. g.ghost_name .. ")") or "")) return
end
local function lanes(b)
  local r = {}
  for l = 1, math.min(2, b.get_max_transport_line_index()) do
    local c = {} local n = 0
    for _, it in pairs(b.get_transport_line(l).get_contents()) do c[it.name] = (c[it.name] or 0) + it.count n = n + it.count end
    r[l] = {c = c, n = n}
  end
  return r
end
local function short(n) return (n:gsub("%-science%-pack", "-sp"):gsub("%-plate", ""):gsub("advanced%-circuit", "red-circ"):gsub("electronic%-circuit", "green-circ")) end
local function desc(c) local t = {} for k, v in pairs(c) do t[#t+1] = short(k) .. "=" .. v end table.sort(t) return #t > 0 and table.concat(t, ",") or "-" end
local function takers(b)
  local t = {}
  for _, i in pairs(s.find_entities_filtered{type = "inserter", area = {{b.position.x - 3, b.position.y - 3}, {b.position.x + 3, b.position.y + 3}}, force = f}) do
    local p = i.pickup_target
    if (not UP) and p == b then
      local d = i.drop_target
      t[#t+1] = i.name:gsub("%-inserter", "") .. "->" .. (d and (d.name .. (d.get_recipe and d.type == "assembling-machine" and d.get_recipe() and ("[" .. d.get_recipe().name .. "]") or "")) or "ground")
    elseif UP and i.drop_target == b then
      t[#t+1] = "from " .. (p and p.name or "?") .. " via " .. i.name
    end
  end
  return t
end
local seen, lines, queue = {}, 0, {{start, 0}}
local cur_ghost_pair
local function key(e) return e.unit_number end
while #queue > 0 and lines < MAX do
  local item = table.remove(queue, 1)
  local b, depth = item[1], item[2]
  local run = {}
  local cur = b
  while cur and not seen[key(cur)] do
    seen[key(cur)] = true
    run[#run+1] = cur
    local okn, nb = pcall(function() return cur.belt_neighbours end)
    if not okn then nb = {inputs = {}, outputs = {}} end
    local nexts = UP and nb.inputs or nb.outputs
    if cur.type == "underground-belt" then
      local pair = cur.underground_belt_neighbour
      local goes = (UP and cur.belt_to_ground_type == "output") or (not UP and cur.belt_to_ground_type == "input")
      if pair and goes then
        if pair.name == "entity-ghost" then nexts = {} cur_ghost_pair = pair else nexts = {pair} end
      end
    end
    if cur.type == "splitter" or #nexts ~= 1 or (#nexts == 1 and nexts[1].direction ~= cur.direction) then
      -- end of a straight run: report it and branch
      local agg, tk = {{}, {}}, {}
      local full, cap = 0, 0
      for _, r in pairs(run) do
        local ln = lanes(r)
        for l = 1, 2 do if ln[l] then for k, v in pairs(ln[l].c) do agg[l][k] = (agg[l][k] or 0) + v end full = full + ln[l].n end end
        cap = cap + (r.type == "splitter" and 16 or 8)
        for _, x in pairs(takers(r)) do tk[x] = (tk[x] or 0) + 1 end
      end
      local tks = {} for k, v in pairs(tk) do tks[#tks+1] = v .. "x " .. k end
      local a, z = run[1], run[#run]
      local extra = ""
      if z.type == "splitter" then
        extra = string.format(" SPLITTER in=%s out=%s filter=%s", z.splitter_input_priority, z.splitter_output_priority, z.splitter_filter and z.splitter_filter.name and z.splitter_filter.name.name or tostring(z.splitter_filter and z.splitter_filter.name or "none"))
      end
      local ending = ""
      if #nexts == 0 then
        local p = z.position local d = z.direction
        local dx, dy = 0, 0 if d == 0 then dy = -1 elseif d == 4 then dx = 1 elseif d == 8 then dy = 1 else dx = -1 end
        if UP then dx, dy = -dx, -dy end
        local g = s.find_entities_filtered{position = {p.x + dx, p.y + dy}, name = "entity-ghost"}[1]
        local o = s.find_entities_filtered{position = {p.x + dx, p.y + dy}}[1]
        ending = g and (" ENDS at a GHOST " .. g.ghost_name) or (" ENDS" .. (o and (" facing " .. o.name) or " (dead end)"))
        if cur_ghost_pair then ending = " ENDS: its underground exit " .. pos(cur_ghost_pair) .. " is a GHOST (not built)" cur_ghost_pair = nil end
      elseif #nexts == 1 and nexts[1].direction ~= z.direction and nexts[1].type ~= "splitter" then
        local n = nexts[1]
        ending = string.format(" -> into %s %s %s", n.name, pos(n), DIR[n.direction] or n.direction)
      end
      out(string.format("%s%s -> %s %s %d belts, %d/%d items  L[%s] R[%s]%s%s%s", string.rep("  ", depth), pos(a), pos(z), DIR[a.direction] or a.direction, #run, full, cap,
        desc(agg[1]), desc(agg[2]), extra, #tks > 0 and ("  takers: " .. table.concat(tks, ", ")) or "", ending))
      lines = lines + 1
      for i, n in pairs(nexts) do if not seen[key(n)] then table.insert(queue, i == 1 and 1 or #queue + 1, {n, depth + (#nexts > 1 and 1 or 0)}) end end
      cur = nil
    else
      cur = nexts[1]
    end
  end
end
if lines >= MAX then out("... stopped after " .. MAX .. " runs (use --max)") end
'''

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xy", help="world position X,Y of a belt, underground or splitter")
    ap.add_argument("--up", action="store_true", help="trace upstream instead of downstream")
    ap.add_argument("--save", help="save .zip (default: the most recent one)")
    ap.add_argument("--after", type=int, default=0, help="ticks to run first")
    ap.add_argument("--max", type=int, default=60, help="max runs to print (default 60)")
    ap.add_argument("--surface", default="nauvis")
    a = ap.parse_args()
    x, y = (float(v) for v in a.xy.split(","))
    body = (LUA.replace("__X__", repr(x)).replace("__Y__", repr(y))
            .replace("__UP__", "true" if a.up else "false").replace("__MAX__", str(a.max)))
    print("\n".join(save_lua.run(body, a.save, a.after, a.surface)))
