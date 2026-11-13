#!/usr/bin/env python3
"""What is actually holding the player's factory back: root causes, not symptoms.

  save_advise.py                          -> the most recent save
  save_advise.py --save x.zip --after 1200 --json advise.json
  save_advise.py --surface vulcanus

Loads a copy of the save, lets it run --after ticks (default 600) while sampling every crafting
machine, furnace, lab, mining drill and pumpjack once a second, and reports, most important first:

  1. ROOT CAUSES. For every machine short of ingredients it works out WHICH ingredient is missing
     (input inventory and fluidboxes against the recipe), then follows that item upstream:
       - its makers are starved themselves       -> keep following (the chain is printed);
       - its makers are full_output / blocked    -> DISTRIBUTION: it is made but does not arrive
         (or a by-product has nowhere to go and stops the machine);
       - its makers all work flat out            -> CAPACITY: roughly how many more machines;
       - nothing makes it / raw resource         -> NOT BUILT, with the drills/pumpjacks status.
     Each root cause is printed once with the chain it starves, how many machines that is, and a
     location (centre of the densest cluster) to go and look.
  2. SCIENCE: packs made vs used per minute, labs working vs missing packs (and which), the
     current research, the queue, and the root cause behind the pack the labs lack most.
  3. CAPACITY: power (production vs nameplate, machines without power), logistic and construction
     robots per network (flags saturation), ore runway in hours under the drills (flags < 5 h).
  4. IDLE CAPITAL: recipes whose machines sit at full_output (what is over-built), top 8.
  5. NEXT STEPS: at most 6 actions, ordered by impact.

Counts such as "12/16" are machine-equivalents averaged over the samples (a machine starved half the
time counts 0.5). Production statistics are never used to decide a cause, only to illustrate it.
Never touches the original save.
"""
import argparse, json, math, os, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fenv  # noqa: E402
import save_lua  # noqa: E402  (long_string)

LUA = r'''
local CFG = helpers.json_to_table(__CFG__)
local ST = {} for k, v in pairs(defines.entity_status) do ST[v] = k end
local ES = defines.entity_status
local SHORT = {[ES.item_ingredient_shortage] = true, [ES.fluid_ingredient_shortage] = true, [ES.no_ingredients] = true}
local BLOCK = {[ES.full_output] = true}
local M, nsamples, s, f = {}, 0, nil, nil
local function try(fn, default) local ok, v = pcall(fn) if ok and v ~= nil then return v end return default end

local function recipe_of(e)
  local r = e.get_recipe()
  if not r and e.type == "furnace" then r = e.previous_recipe if r and r.name and type(r.name) ~= "string" then r = r.name end end
  if not r then return nil end
  local name = type(r) == "string" and r or r.name
  return prototypes.recipe[name]
end

local function init()
  s, f = game.surfaces[CFG.surface], game.forces.player
  if not s then return end
  for _, pl in pairs(game.players) do if pl.character then pl.character.destructible = false end end
  for _, e in pairs(s.find_entities_filtered{type = {"assembling-machine", "furnace", "rocket-silo", "lab", "mining-drill"}, force = f}) do
    local crafter = e.type ~= "lab" and e.type ~= "mining-drill"
    M[#M+1] = {e = e, crafter = crafter, st = {}, mi = {}, bl = {}, pf0 = crafter and e.products_finished or 0}
  end
end

local function bump(t, k) t[k] = (t[k] or 0) + 1 end

local function sample()
  nsamples = nsamples + 1
  local cr = f.current_research
  for _, m in pairs(M) do
    local e = m.e
    if e.valid then
      local status = e.status
      bump(m.st, ST[status] or tostring(status))
      if m.crafter then
        local r = recipe_of(e)
        if r then m.r = r.name end
        if r and SHORT[status] then
          local inv, found = e.get_inventory(defines.inventory.crafter_input), false
          for _, ing in pairs(r.ingredients) do
            local have
            if ing.type == "fluid" then have = e.get_fluid_count(ing.name) else have = inv and inv.get_item_count(ing.name) or 0 end
            if have < ing.amount then bump(m.mi, ing.name) found = true end
          end
          if not found then bump(m.mi, "?") end
        elseif r and BLOCK[status] then
          local inv, found = e.get_inventory(defines.inventory.crafter_output), false
          for _, p in pairs(r.products) do
            if p.type == "fluid" then
              for i = 1, e.fluids_count do
                local fl = e.get_fluid(i)
                local kind = try(function()
                  local bp = e.get_fluid_box_prototype(i)
                  if bp and bp.object_name ~= "LuaFluidBoxPrototype" then bp = bp[1] end
                  return bp.production_type
                end, "output")
                if fl and fl.name == p.name and kind == "output" then
                  local amt, capa = p.amount or p.amount_max or 0, e.get_fluid_capacity(i)
                  if fl.amount + amt > capa or fl.amount >= 0.9 * capa then bump(m.bl, p.name) found = true end
                end
              end
            elseif inv and inv.get_item_count(p.name) >= (p.amount or p.amount_min or 1) then bump(m.bl, p.name) found = true end
          end
          if not found then bump(m.bl, "?") end
        end
      elseif e.type == "lab" then
        if cr and status == ES.missing_science_packs then
          local inv = e.get_inventory(defines.inventory.lab_input)
          for _, ing in pairs(cr.research_unit_ingredients) do if inv.get_item_count(ing.name) == 0 then bump(m.mi, ing.name) end end
        end
      else
        local t = e.mining_target
        if t then m.res = t.name end
      end
    end
  end
end

local function amount(p) return (p.amount or ((p.amount_min or 0) + (p.amount_max or 0)) / 2) * (p.probability or 1) end

local function finish()
  local T = {surface = s.name, tick = game.tick, samples = nsamples, machines = {}, recipes = {}, resources = {}}
  local cr = f.current_research
  local lab_mod = try(function() return f.laboratory_speed_modifier end, 0)
  local mine_bonus = try(function() return f.mining_drill_productivity_bonus end, 0)
  local used_recipes, seen, left = {}, {}, {}
  for _, m in pairs(M) do
    local e = m.e
    if e.valid then
      local o = {n = e.name, t = e.type, x = math.floor(e.position.x), y = math.floor(e.position.y), st = m.st}
      if next(m.mi) then o.mi = m.mi end
      if next(m.bl) then o.bl = m.bl end
      if m.crafter then
        o.r = m.r
        if m.r then used_recipes[m.r] = true end
        o.sp = e.crafting_speed
        o.pb = try(function() return e.productivity_bonus end, 0)
        o.done = e.products_finished - m.pf0
      elseif e.type == "lab" then
        o.r = "lab"
        o.sp = try(function() return e.prototype.get_researching_speed(e.quality) end, 1) * (1 + lab_mod) * (1 + try(function() return e.speed_bonus end, 0))
      else
        local t = e.mining_target
        local res = t and t.name or m.res
        o.r = "mine:" .. (res or "nothing")
        if t then
          local rp = t.prototype
          local mp = rp.mineable_properties
          local rate = e.prototype.mining_speed / mp.mining_time * (1 + try(function() return e.speed_bonus end, 0))
          if rp.infinite_resource then rate = rate * t.amount / rp.normal_resource_amount end
          o.rate = rate * 60 -- mining operations per minute (before productivity)
          o.pb = mine_bonus
          if not T.resources[res] then
            local pr = {}
            for _, p in pairs(mp.products or {}) do pr[#pr+1] = {name = p.name, type = p.type, amount = amount(p)} end
            T.resources[res] = {products = pr, infinite = rp.infinite_resource, normal = rp.normal_resource_amount, left = 0, yield = 0}
          end
        end
        for _, r in pairs(s.find_entities_filtered{area = e.mining_area, type = "resource"}) do
          local key = r.position.x .. "," .. r.position.y
          if not seen[key] then
            seen[key] = true
            local R = T.resources[r.name]
            if R then R.left = R.left + r.amount end
          end
        end
      end
      T.machines[#T.machines+1] = o
    end
  end
  -- resources that exist as prototypes but have no drill (to tell "raw, no drills" from "not built")
  T.raw = {}
  for name, rp in pairs(prototypes.get_entity_filtered{{filter = "type", type = "resource"}}) do
    for _, p in pairs(rp.mineable_properties.products or {}) do T.raw[p.name] = name end
  end
  -- every recipe the force can use (so "nothing makes X" can name the recipe)
  for name, r in pairs(f.recipes) do
    if r.enabled or used_recipes[name] then
      local ing, pr = {}, {}
      for _, i in pairs(r.ingredients) do ing[#ing+1] = {name = i.name, type = i.type, amount = i.amount} end
      for _, p in pairs(r.products) do pr[#pr+1] = {name = p.name, type = p.type, amount = amount(p)} end
      T.recipes[name] = {ing = ing, pr = pr, energy = r.energy, used = used_recipes[name] or false, hidden = r.hidden}
    end
  end
  -- production statistics, 10-minute window, per minute
  T.flow = {}
  local P10 = defines.flow_precision_index.ten_minutes
  for _, st in pairs({f.get_item_production_statistics(s), f.get_fluid_production_statistics(s)}) do
    local names = {}
    for n, _ in pairs(st.input_counts) do names[n] = true end
    for n, _ in pairs(st.output_counts) do names[n] = true end
    for n, _ in pairs(names) do
      local made = st.get_flow_count{name = n, category = "input", precision_index = P10, count = true} / 10
      local used = st.get_flow_count{name = n, category = "output", precision_index = P10, count = true} / 10
      if made > 0 or used > 0 then T.flow[n] = {made, used} end
    end
  end
  -- research
  local packs = {}
  for _, lp in pairs(prototypes.get_entity_filtered{{filter = "type", type = "lab"}}) do for _, n in pairs(lp.lab_inputs or {}) do packs[n] = true end end
  T.packs = {} for n, _ in pairs(packs) do T.packs[#T.packs+1] = n end
  local function tech(t)
    local ing = {} for _, i in pairs(t.research_unit_ingredients) do ing[#ing+1] = {name = i.name, amount = i.amount} end
    return {name = t.name, level = t.level, packs = ing, units = try(function() return t.research_unit_count end, 0), energy = t.research_unit_energy}
  end
  if cr then T.research = tech(cr) T.research.progress = f.research_progress end
  T.queue = {} for _, t in pairs(f.research_queue or {}) do T.queue[#T.queue+1] = tech(t) end
  -- power
  T.power = {}
  local nets = {}
  local P5 = defines.flow_precision_index.five_seconds
  for _, p in pairs(s.find_entities_filtered{type = "electric-pole", force = f}) do
    local id = p.electric_network_id
    if id and not nets[id] then
      local st = p.electric_network_statistics
      local prod, cons = 0, 0
      for n, _ in pairs(st.output_counts) do prod = prod + st.get_flow_count{name = n, category = "output", precision_index = P5} end
      for n, _ in pairs(st.input_counts) do cons = cons + st.get_flow_count{name = n, category = "input", precision_index = P5} end
      nets[id] = {id = id, prod = prod * 60, cons = cons * 60, cap = 0, gens = 0, poles = 0, acc = 0, acc_e = 0, acc_max = 0, x = math.floor(p.position.x), y = math.floor(p.position.y), kinds = {}}
    end
    if id then nets[id].poles = nets[id].poles + 1 end
  end
  for _, g in pairs(s.find_entities_filtered{type = {"generator", "solar-panel", "burner-generator", "fusion-generator", "accumulator"}, force = f}) do
    local n = nets[g.electric_network_id or -1]
    if n then
      if g.type == "accumulator" then
        n.acc = n.acc + 1 n.acc_e = n.acc_e + g.energy n.acc_max = n.acc_max + g.electric_buffer_size
      else
        n.gens = n.gens + 1
        n.cap = n.cap + try(function() return g.prototype.get_max_energy_production(g.quality) end, 0) * 60
        n.kinds[g.name] = (n.kinds[g.name] or 0) + 1
      end
    end
  end
  for _, n in pairs(nets) do T.power[#T.power+1] = n end
  T.unplugged = 0
  for _, m in pairs(M) do if m.e.valid and m.e.prototype.electric_energy_source_prototype and not m.e.is_connected_to_electric_network() then T.unplugged = T.unplugged + 1 end end
  -- robots
  T.robots = {}
  for i, ln in pairs(f.logistic_networks[s.name] or {}) do
    local cells, tocharge, charging = ln.cells, 0, 0
    for _, c in pairs(cells) do tocharge = tocharge + c.to_charge_robot_count charging = charging + c.charging_robot_count end
    local c = cells[1] and cells[1].owner
    T.robots[#T.robots+1] = {id = i, roboports = #cells, logi = ln.all_logistic_robots, logi_free = ln.available_logistic_robots,
      cons = ln.all_construction_robots, cons_free = ln.available_construction_robots, waiting_charge = tocharge, charging = charging,
      x = c and math.floor(c.position.x) or 0, y = c and math.floor(c.position.y) or 0}
  end
  T.ghosts = s.count_entities_filtered{name = "entity-ghost", force = f}
  helpers.write_file("save_advise.json", helpers.table_to_json(T))
  log("SAVEADVISE done")
end

script.on_event(defines.events.on_tick, function(ev)
  if storage.sa_done then return end
  if not storage.sa_t0 then storage.sa_t0 = ev.tick init() if not s then log("SAVEADVISE ERROR no surface " .. CFG.surface) storage.sa_done = true return end end
  local dt = ev.tick - storage.sa_t0
  local ok, err = true, nil
  if dt >= CFG.after then
    ok, err = pcall(function() sample() finish() end)
    storage.sa_done = true
  elseif dt % CFG.every == 0 and dt > 0 then
    ok, err = pcall(sample)
  end
  if not ok then log("SAVEADVISE ERROR " .. tostring(err)) storage.sa_done = true end
end)
'''

SHORT = {"item_ingredient_shortage", "fluid_ingredient_shortage", "no_ingredients", "missing_science_packs",
         "missing_required_fluid", "no_input_fluid", "low_input_fluid"}
BLOCK = {"full_output", "waiting_for_space_in_destination", "not_enough_space_in_output", "full_burnt_result_output"}
POWER = {"no_power", "low_power", "not_plugged_in_electric_network", "no_fuel", "recharging_after_power_outage"}
WORK = {"working", "normal"}


def collect(save, after, surface, every=60):
    cfg = {"after": int(after), "surface": surface, "every": every}
    with fenv.Instance("saveadvise", LUA.replace("__CFG__", save_lua.long_string(json.dumps(cfg))), user_mods=True) as inst:
        inst.run_save(inst.copy_save(save), int(after) + 3)
        p = os.path.join(inst.write, "script-output", "save_advise.json")
        if not os.path.exists(p):
            raise SystemExit("Could not read the save:\n" + "\n".join(inst.tagged("SAVEADVISE") + inst.errors(15)))
        return json.load(open(p, encoding="utf-8"))


def lst(v):
    """helpers.table_to_json writes an empty table as {}: normalise lists."""
    return v if isinstance(v, list) else []


def centre(ms, cell=32):
    """Centre of the densest cluster of machines (weights optional): where to go and look."""
    cells = defaultdict(list)
    for m, w in ms:
        if w > 0:
            cells[(m["x"] // cell, m["y"] // cell)].append((m, w))
    if not cells:
        return None
    best = max(cells.values(), key=lambda c: sum(w for _, w in c))
    tw = sum(w for _, w in best)
    return (round(sum(m["x"] * w for m, w in best) / tw), round(sum(m["y"] * w for m, w in best) / tw))


def at(c):
    return f"({c[0]},{c[1]})" if c else "-"


def num(v):
    v = float(v)
    if abs(v) >= 1e6:
        return f"{v / 1e6:.1f}M"
    if abs(v) >= 1e4:
        return f"{v / 1e3:.0f}k"
    return f"{v:.0f}" if abs(v) >= 100 or v == int(v) else f"{v:.1f}"


def cnt(v):
    """Machine-equivalents as a count: 12.4 -> 12, 0.4 -> 0.4."""
    return str(int(round(v))) if v >= 0.95 or v == 0 else f"{v:.1f}"


def watts(w):
    for unit, k in (("GW", 1e9), ("MW", 1e6), ("kW", 1e3)):
        if w >= k:
            return f"{w / k:.1f} {unit}"
    return f"{w:.0f} W"


class Group:
    """All machines with the same recipe (or drills on the same resource, or the labs)."""

    def __init__(self, key):
        self.key, self.ms = key, []
        self.w = defaultdict(float)      # W/S/B/P/O machine-equivalents
        self.miss = defaultdict(float)   # item -> machine-equivalents missing it
        self.block = defaultdict(float)  # product -> machine-equivalents blocked by it
        self.status = defaultdict(float)
        self.products, self.ingredients = {}, {}

    @property
    def total(self):
        return len(self.ms)

    @property
    def label(self):
        return self.key[5:] + " drills" if self.key.startswith("mine:") else ("labs" if self.key == "lab" else self.key)

    def machine_names(self):
        c = defaultdict(int)
        for m in self.ms:
            c[m["n"]] += 1
        return max(c, key=c.get) if c else "?"

    def mix(self):
        parts = [f"{cnt(self.w[k])} {n}" for k, n in (("W", "working"), ("S", "starved"), ("B", "output-blocked"), ("P", "no/low power"), ("O", "other")) if self.w[k] >= 0.05]
        return ", ".join(parts)


def analyse(T):
    ns = max(1, T["samples"])
    recipes = T.get("recipes") or {}
    resources = T.get("resources") or {}
    flow = T.get("flow") or {}
    groups = {}
    for m in lst(T["machines"]):
        key = m.get("r")
        st = m.get("st") or {}
        n = sum(st.values()) or 1
        m["f"] = f = defaultdict(float)
        for k, v in st.items():
            f["W" if k in WORK else "S" if k in SHORT else "B" if k in BLOCK else "P" if k in POWER else "O"] += v / n
        m["miss"] = {k: v / n for k, v in (m.get("mi") or {}).items()}
        m["blk"] = {k: v / n for k, v in (m.get("bl") or {}).items()}
        if not key:
            continue  # no recipe set / furnace never used
        g = groups.get(key) or groups.setdefault(key, Group(key))
        g.ms.append(m)
        for k, v in f.items():
            g.w[k] += v
        for k, v in st.items():
            g.status[k] += v / n
        for k, v in m["miss"].items():
            g.miss[k] += v
        for k, v in m["blk"].items():
            g.block[k] += v
        if key.startswith("mine:") and f["B"] > 0:
            for p in lst((resources.get(key[5:]) or {}).get("products")):
                g.block[p["name"]] += f["B"]
    for key, g in groups.items():
        if key.startswith("mine:"):
            for p in lst((resources.get(key[5:]) or {}).get("products")):
                g.products[p["name"]] = p["amount"]
        elif key in recipes:
            g.products = {p["name"]: p["amount"] for p in lst(recipes[key]["pr"])}
            g.ingredients = {i["name"]: i["amount"] for i in lst(recipes[key]["ing"])}
    research = T.get("research")
    if "lab" in groups and research:
        groups["lab"].ingredients = {p["name"]: p["amount"] for p in lst(research["packs"])}

    def rate(m, g, item, out):
        """Items per minute this machine makes (out) or eats at full speed."""
        if g.key.startswith("mine:"):
            return m.get("rate", 0) * g.products.get(item, 0) * (1 + m.get("pb", 0)) if out else 0
        if g.key == "lab":
            return 0 if out or not research else m.get("sp", 1) * 3600 / max(1, research["energy"]) * g.ingredients.get(item, 0)
        e = recipes[g.key]["energy"] if g.key in recipes else 1
        per = m.get("sp", 1) / e * 60
        return per * g.products.get(item, 0) * (1 + m.get("pb", 0)) if out else per * g.ingredients.get(item, 0)

    producers, consumers = defaultdict(list), defaultdict(list)
    for g in groups.values():
        for it in g.products:
            if it not in g.ingredients:  # kovarex-style loops don't make the item from nothing
                producers[it].append(g)
        for it in g.ingredients:
            consumers[it].append(g)

    causes, memo = {}, {}

    def leaf(kind, item, **kw):
        c = causes.get((kind, item))
        if not c:
            c = causes[(kind, item)] = dict(kind=kind, item=item, **kw)
        return c

    def slack(g):
        """Inputs of a maker group whose own makers are output-blocked: more of the input is there for the taking."""
        res = []
        for ing in g.ingredients:
            gs = producers[ing]
            tot = sum(x.total for x in gs)
            b = sum(x.w["B"] for x in gs)
            if tot and b / tot >= 0.1:
                res.append(ing)
        return res

    def find(item, stack=()):
        """Root causes behind a shortage of `item`: [(cause, share)], shares adding up to 1."""
        if item in memo:
            return memo[item]
        if item in stack:
            return []
        gs = producers[item]
        tot = sum(g.total for g in gs)
        cap = sum(rate(m, g, item, True) for g in gs for m in g.ms)
        dem = sum(rate(m, g, item, False) for g in consumers[item] for m in g.ms)
        unmet = sum(rate(m, g, item, False) * m["miss"].get(item, 0) for g in consumers[item] for m in g.ms)
        made, used = (flow.get(item) or [0, 0])[:2]
        base = dict(cap=cap, demand=dem, unmet=unmet, made=made, used=used, makers=tot,
                    maker_recipes=[g.label for g in sorted(gs, key=lambda g: -g.total)])
        if tot == 0:
            raw = (T.get("raw") or {}).get(item)
            rec = [r for r, v in recipes.items() if not v.get("hidden") and any(p["name"] == item for p in lst(v["pr"]))]
            res = [(leaf("not_built", item, raw=raw, recipe=rec[0] if rec else None, **base), 1.0)]
            memo[item] = res
            return res
        W, S, B, P, O = (sum(g.w[k] for g in gs) for k in "WSBPO")
        miss, block = defaultdict(float), defaultdict(float)
        for g in gs:
            for k, v in g.miss.items():
                miss[k] += v
            for k, v in g.block.items():
                block[k] += v
        b_self = min(B, block.get(item, 0) + block.get("?", 0))
        b_other = max(0.0, B - b_self)
        base["mix"] = dict(working=W, starved=S, blocked=B, power=P, other=O)
        base["where"] = centre([(m, 1) for g in gs for m in g.ms])

        def sig(w):
            return w >= 0.5 and w / tot >= 0.1

        comps = []  # (weight, [(cause, share)])
        if sig(b_self):
            comps.append((b_self, [(leaf("distribution", item, **dict(base, where=centre(
                [(m, m["blk"].get(item, m["f"]["B"])) for g in gs for m in g.ms]))), 1.0)]))
        if sig(b_other):
            others = {k: v for k, v in block.items() if k not in (item, "?")}
            by = max(others, key=others.get)
            comps.append((b_other, [(leaf("byproduct", item, by=by, **dict(base, where=centre(
                [(m, m["blk"].get(by, 0)) for g in gs for m in g.ms]))), 1.0)]))
        if sig(S):
            subs = {k: v for k, v in miss.items() if k != "?" and v >= 0.25 * max(miss.values())}
            tw, sub = sum(subs.values()), []
            for k, v in sorted(subs.items(), key=lambda kv: -kv[1]):
                for c, sh in find(k, stack + (item,)):
                    sub.append((c, sh * v / tw))
            if not sub:
                sub = [(leaf("unknown", item, **base), 1.0)]
            comps.append((S, sub))
        if sig(P):
            comps.append((P, [(leaf("power", item, **dict(base, where=centre([(m, m["f"]["P"]) for g in gs for m in g.ms]))), 1.0)]))
        if sig(O):
            sts = defaultdict(float)
            for g in gs:
                for k, v in g.status.items():
                    if k not in WORK | SHORT | BLOCK | POWER:
                        sts[k] += v
            comps.append((O, [(leaf("stopped", item, status=max(sts, key=sts.get), **base), 1.0)]))
        if not sig(b_self) and (W / tot >= 0.7 or not comps):
            big = max(gs, key=lambda g: g.total)
            per = cap / tot if tot else 0
            if made > 1.25 * used and made > 0:
                # the makers run and more is made than used: it is filling belts/chests or going elsewhere
                c = leaf("transit", item, **base)
            else:
                c = leaf("capacity", item, extra=math.ceil(unmet / per) if per > 0 else 0, machine=big.machine_names(),
                         recipe=big.label, slack=slack(big), **base)
            comps.append((max(W, 0.5), [(c, 1.0)]))
        tw = sum(w for w, _ in comps)
        acc = {}
        for w, sub in comps:
            for c, sh in sub:
                k = (c["kind"], c["item"])
                acc[k] = (c, acc.get(k, (c, 0))[1] + sh * w / tw)
        memo[item] = res = sorted(acc.values(), key=lambda t: -t[1])
        return res

    # starvation edges: group g has machines missing item
    edges = []
    for g in groups.values():
        for it, w in g.miss.items():
            if it != "?" and w >= 0.5:
                edges.append((g, it, w))
                find(it)
    for c in causes.values():
        c["_score"], c["_edges"] = 0.0, []
    for g, it, w in edges:
        for c, sh in memo.get(it, []):
            c["_edges"].append((g, it, w))
            c["_score"] += w * sh

    def chain(c):
        """Greedy downstream path from the cause, plus the other first-level victims with their own paths."""
        by_item = defaultdict(list)
        for g, it, w in c["_edges"]:
            by_item[it].append((g, w))
        seen = set()

        def walk(g, w, depth):
            steps = []
            while True:
                seen.add(g.key)
                steps.append(f"{g.label} ({cnt(w)}/{g.total})")
                opts = [(g2, w2) for p in g.products for g2, w2 in by_item.get(p, []) if g2.key not in seen]
                if not opts or len(steps) >= depth:
                    return steps
                g, w = max(opts, key=lambda t: t[1])

        item = c["item"]
        if item not in by_item and by_item:
            item = max(by_item, key=lambda k: sum(w for _, w in by_item[k]))
        opts = sorted(by_item.get(item, []), key=lambda t: -t[1])
        if not opts:
            return [], []
        for g, _ in opts:
            seen.add(g.key)
        main = walk(*opts[0], 5)
        also = [" -> ".join(walk(g, w, 3)) for g, w in opts[1:4]]
        return main, also

    out_causes = []
    for c in sorted(causes.values(), key=lambda c: -c["_score"]):
        if not c["_edges"]:
            continue
        steps, also = chain(c)
        direct = [(m, m["miss"].get(c["item"], 0)) for g, it, w in c["_edges"] if it == c["item"] for m in g.ms]
        d = {k: v for k, v in c.items() if not k.startswith("_")}
        d.update(starves=c["_score"], chain=steps, also=also, victims_at=centre(direct) or centre(
            [(m, m["miss"].get(it, 0)) for g, it, w in c["_edges"] for m in g.ms]))
        out_causes.append(d)

    # ---- science
    packs = sorted(lst(T.get("packs")))
    sci = dict(packs={p: dict(made=(flow.get(p) or [0, 0])[0], used=(flow.get(p) or [0, 0])[1]) for p in packs if p in flow},
               research=research, queue=[q["name"] for q in lst(T.get("queue"))], labs=0)
    lab = groups.get("lab")
    labs_all = [m for m in lst(T["machines"]) if m["t"] == "lab"]
    sci["labs"] = len(labs_all)
    sci["working"] = sum(m["f"]["W"] for m in labs_all)
    sci["missing_packs"] = sum(m["f"]["S"] for m in labs_all)
    sci["lab_status"] = defaultdict(float)
    for m in labs_all:
        for k, v in (m.get("st") or {}).items():
            sci["lab_status"][k] += v / (sum(m["st"].values()) or 1)
    sci["lab_status"] = dict(sci["lab_status"])
    sci["lack"] = dict(sorted(lab.miss.items(), key=lambda kv: -kv[1])) if lab else {}
    sci["needs"] = [p["name"] for p in lst(research["packs"])] if research else []
    sci["queue_needs"] = sorted({p["name"] for q in lst(T.get("queue")) for p in lst(q["packs"])})
    sci["limiting"] = next(iter(sci["lack"]), None)
    sci["limiting_causes"] = [[c["kind"], c["item"], round(sh, 2)] for c, sh in memo.get(sci["limiting"], [])] if sci["limiting"] else []
    sci["at"] = centre([(m, 1) for m in labs_all])
    lg = producers.get(sci["limiting"], []) if sci["limiting"] else []
    sci["limiting_makers"] = dict(total=sum(g.total for g in lg), working=sum(g.w["W"] for g in lg), starved=sum(g.w["S"] for g in lg),
                                  blocked=sum(g.w["B"] for g in lg), at=centre([(m, 1) for g in lg for m in g.ms]),
                                  missing={k: v for g in lg for k, v in sorted(g.miss.items(), key=lambda kv: -kv[1])})

    # ---- capacity
    power = sorted(lst(T.get("power")), key=lambda n: -n["cons"])
    lowp = [(m, m["f"]["P"]) for m in lst(T["machines"]) if m["f"]["P"] >= 0.5]
    ore = []
    for name, R in resources.items():
        g = groups.get("mine:" + name)
        if not g:
            continue
        prods = lst(R.get("products"))
        item = prods[0]["name"] if prods else name
        made = (flow.get(item) or [0, 0])[0]
        full = sum(rate(m, g, item, True) for m in g.ms)
        o = dict(resource=name, item=item, drills=g.total, working=g.w["W"], blocked=g.w["B"], depleted=g.status.get("no_minable_resources", 0),
                 other=g.total - g.w["W"] - g.w["B"] - g.status.get("no_minable_resources", 0), made=made, full_rate=full,
                 infinite=bool(R.get("infinite")), left=R.get("left", 0), at=centre([(m, 1) for m in g.ms]))
        if not o["infinite"]:
            per_op = sum(p["amount"] for p in prods) or 1
            r = made or full * (g.w["W"] / max(1, g.total))
            # productivity makes ore out of nothing: the patch drains slower than items appear
            drain = r / per_op / (1 + (g.ms[0].get("pb", 0) if g.ms else 0))
            o["hours"] = o["left"] / drain / 60 if drain > 0 else None
        ore.append(o)
    ore.sort(key=lambda o: (o.get("hours") is None, o.get("hours") or 0))
    cap = dict(power=power, low_power_machines=len(lowp), low_power_at=centre(lowp), unplugged=T.get("unplugged", 0),
               robots=lst(T.get("robots")), ghosts=T.get("ghosts", 0), ore=ore)

    # ---- idle capital
    idle = []
    for g in groups.values():
        if g.w["B"] >= 1 and g.key != "lab":
            idle.append(dict(recipe=g.label, blocked=g.w["B"], total=g.total, machine=g.machine_names(),
                             by=max(g.block, key=g.block.get) if g.block else None,
                             at=centre([(m, m["f"]["B"]) for m in g.ms])))
    idle.sort(key=lambda d: -d["blocked"])
    norecipe = sum(1 for m in lst(T["machines"]) if not m.get("r") and m["t"] in ("assembling-machine", "furnace"))

    A = dict(surface=T["surface"], hours=T["tick"] / 216000, samples=T["samples"], machines=len(lst(T["machines"])),
             causes=out_causes, science=sci, capacity=cap, idle=idle, no_recipe=norecipe)
    A["next_steps"] = next_steps(A)
    return A


KIND = dict(distribution="DISTRIBUTION", byproduct="BY-PRODUCT BLOCKED", capacity="CAPACITY", transit="IN TRANSIT / HOARDED",
            not_built="NOT BUILT", power="POWER", stopped="MAKERS STOPPED", unknown="UNTRACED")


def cause_text(c):
    it, k = c["item"], c["kind"]
    mix = c.get("mix")
    mixs = ""
    if mix:
        mixs = ", ".join(f"{cnt(v)} {n}" for n, v in (("working", mix["working"]), ("starved", mix["starved"]), ("output-blocked", mix["blocked"]),
                                                      ("no power", mix["power"]), ("other", mix["other"])) if v >= 0.05)
    makers = f"{c['makers']} makers [{', '.join(c['maker_recipes'][:2])}]: {mixs}"
    head = f"{it}: {KIND[k]} -"
    if k == "distribution":
        return f"{head} made but not delivered. {makers}; the blocked ones sit at {at(c.get('where'))}. More makers will NOT help: fix the belt/pipe/train/robot route to the consumers."
    if k == "byproduct":
        return f"{head} makers stop because their {c['by']} output is full. {makers}. Give {c['by']} somewhere to go (cracking, a consumer, a tank) at {at(c.get('where'))}."
    if k == "capacity":
        if c["extra"] > c["makers"]:
            more = f"far more than the {c['makers']} makers give, so pick the rate you want downstream and add {c['machine']} ({c['recipe']}) for it"
        elif c["extra"] > 0:
            more = f"~{c['extra']} more {c['machine']} ({c['recipe']}) would run every starved consumer at full speed"
        else:
            more = "a little more is enough"
        sl = f"; input {', '.join(c['slack'])} has slack (its makers are output-blocked)" if c.get("slack") else ""
        return f"{head} makers run flat out and it is still short. {makers}; {num(c['made'])}/min made, starved consumers want {num(c['unmet'])}/min more: {more}{sl}."
    if k == "transit":
        return (f"{head} makers run and {num(c['made'])}/min is made against {num(c['used'])}/min used, yet some consumers have none: "
                f"it is filling belts/chests or going to another branch. {makers}. Check the route and splits, not the makers.")
    if k == "not_built":
        if c.get("raw"):
            return f"{head} raw resource ({c['raw']}) with no drills on this surface; starved consumers want {num(c['unmet'])}/min. Tap a patch or bring it in."
        how = f"recipe {c['recipe']}" if c.get("recipe") else "no unlocked recipe"
        return f"{head} no machine makes it ({how}); it only arrives by hand/chest. Starved consumers want {num(c['unmet'])}/min."
    if k == "power":
        return f"{head} its makers have no/low power or fuel. {makers}, at {at(c.get('where'))}."
    if k == "stopped":
        return f"{head} {c.get('status')}. {makers}, at {at(c.get('where'))}."
    return f"{head} makers starved but the missing input could not be traced. {makers}."


def short_action(c):
    it, k = c["item"], c["kind"]
    n = f"(~{cnt(c['starves'])} starved machines)"
    if k == "distribution":
        return f"Deliver {it}: it backs up at its makers {at(c.get('where'))} while consumers at {at(c.get('victims_at'))} have none; fix that route, do not add makers {n}."
    if k == "byproduct":
        return f"Drain {c['by']} at {at(c.get('where'))} (cracking/consumer/tank) so the machines making {it} stop blocking {n}."
    if k == "capacity":
        sl = f"; {', '.join(c['slack'])} is available" if c.get("slack") else " and their inputs"
        many = f"~{max(1, c['extra'])} more" if c["extra"] <= c["makers"] else f"more (now {c['makers']} makers, {num(c['made'])}/min; starved consumers could take {num(c['unmet'])}/min more)"
        return f"Add {many} {c['machine']} making {it} ({c['recipe']}){sl} {n}."
    if k == "transit":
        return f"Check where {it} goes: more is made than used but consumers at {at(c.get('victims_at'))} get none {n}."
    if k == "not_built":
        return f"Start producing {it} ({'mine ' + c['raw'] if c.get('raw') else c.get('recipe') or 'no recipe yet'}) {n}."
    if k == "power":
        return f"Power/fuel the {it} makers at {at(c.get('where'))} {n}."
    return f"Look at the {it} makers at {at(c.get('where'))} {n}."


def next_steps(A):
    steps = []
    sci, cap = A["science"], A["capacity"]
    if not sci["research"] and sci["labs"]:
        steps.append((10 ** 6, f"Queue research: nothing is being researched and {sci['labs']} labs do nothing."))
    for n in cap["power"]:
        if n["cons"] > 0 and n["cap"] > 0 and n["prod"] >= 0.97 * n["cap"]:
            steps.append((10 ** 5, f"Add power: network {n['id']} makes {watts(n['prod'])} of {watts(n['cap'])} nameplate."))
            break
    if cap["low_power_machines"] >= 5:
        steps.append((cap["low_power_machines"], f"{cap['low_power_machines']} machines have no/low power or fuel around {at(cap['low_power_at'])}: connect or add generation."))
    lim = {(k, i): sh for k, i, sh in sci.get("limiting_causes") or []}
    for c in A["causes"][:6]:
        key = (c["kind"], c["item"])
        boost = sci["missing_packs"] * lim.get(key, 0) if sci["research"] else 0
        steps.append((c["starves"] + boost, short_action(c)))
    for r in cap["robots"]:
        if r["logi"] >= 20 and r["logi_free"] < 0.1 * r["logi"]:
            steps.append((r["logi"] / 5, f"Add logistic robots to network {r['id']} at ({r['x']},{r['y']}): {r['logi_free']} of {r['logi']} free"
                          + (f", {r['waiting_charge']} queueing to charge (add roboports too)." if r["waiting_charge"] > r["roboports"] else ".")))
            break
    for o in cap["ore"]:
        if o.get("hours") is not None and o["hours"] < 5:
            steps.append((40, f"Open a new {o['item']} patch: {o['hours']:.1f} h left under the {o['drills']} drills at {at(o['at'])}."))
    if sci["research"] and sci["labs"] and sci["missing_packs"] >= 0.5 * sci["labs"] and sci["limiting"]:
        lm, p = sci["limiting_makers"], sci["limiting"]
        if not lm["total"]:
            how = "nothing makes it on this surface: build it"
        elif lm["missing"] and lm["starved"] >= 0.25 * lm["total"]:
            how = f"its {lm['total']} assemblers at {at(lm['at'])} wait for {', '.join(list(lm['missing'])[:3])}: feed those first"
        elif lm["blocked"] >= 0.25 * lm["total"]:
            how = f"its assemblers at {at(lm['at'])} are output-blocked: the packs do not reach the labs at {at(sci['at'])}"
        else:
            how = f"its {lm['total']} assemblers all run: add more of them (and their inputs)"
        steps.append((sci["missing_packs"], f"Research is limited by {p} ({cnt(sci['lack'][p])}/{sci['labs']} labs lack it); {how}."))
    steps.sort(key=lambda t: -t[0])
    return [s for _, s in steps[:6]]


TOP = 7


def render(A, save):
    L = [f"save: {save}", f"{A['surface']}, {A['hours']:.1f} h played; {A['machines']} machines sampled {A['samples']}x "
         f"(n/m = machine-equivalents over the samples)"]
    L.append("== ROOT CAUSES (starved machines traced upstream; biggest first)")
    if not A["causes"]:
        L.append("  none: no machine was short of ingredients")
    for i, c in enumerate(A["causes"][:TOP], 1):
        L.append(f"{i}. {cause_text(c)}")
        extra = f"; also {', '.join(c['also'])}" if c["also"] else ""
        L.append(f"   ~{cnt(c['starves'])} starved machines: {c['item']} short -> {' -> '.join(c['chain'])}{extra}; starved ones at {at(c.get('victims_at'))}")
    if len(A["causes"]) > TOP:
        rest = A["causes"][TOP:]
        L.append(f"   +{len(rest)} smaller (--json): " + ", ".join(f"{c['item']} {KIND[c['kind']].lower()}" for c in rest[:6]) + (" ..." if len(rest) > 6 else ""))

    s = A["science"]
    L.append("== SCIENCE")
    r = s["research"]
    short = lambda p: p.replace("-science-pack", "")  # noqa: E731
    if r:
        L.append(f"research: {r['name']} {r['progress'] * 100:.0f}% of {r['units']} units; needs " + ", ".join(short(p) for p in s["needs"]))
    else:
        L.append("research: NONE - the queue is EMPTY  <-- queue something" if not s["queue"] else "research: none in progress")
    if s["queue"]:
        q = s["queue"]
        L.append(f"queue ({len(q)}): " + ", ".join(q[:7]) + (" ..." if len(q) > 7 else "") + "; packs the queue needs: " + ", ".join(short(p) for p in s["queue_needs"]))
    if s["labs"]:
        other = {k: v for k, v in s["lab_status"].items() if k not in ("working", "missing_science_packs") and v >= 0.5}
        L.append(f"labs at {at(s['at'])}: {cnt(s['working'])}/{s['labs']} working, {cnt(s['missing_packs'])} missing packs"
                 + ("".join(f", {cnt(v)} {k}" for k, v in other.items()))
                 + (" -> lacking: " + ", ".join(f"{short(k)} in {cnt(v)}" for k, v in s["lack"].items()) if s["lack"] else ""))
    if s["packs"]:
        need = set(s["needs"])
        L.append("packs made/used per min (10 min): " + "; ".join(
            f"{short(p)}{'*' if p in need else ''} {num(v['made'])}/{num(v['used'])}" for p, v in s["packs"].items())
            + ("  (* = current research needs it)" if need else ""))
    if s["limiting"]:
        idx = {(c["kind"], c["item"]): i for i, c in enumerate(A["causes"], 1)}
        parts = [f"#{idx[(k, it)]} {it} {KIND[k].lower()} ({sh * 100:.0f}%)" if (k, it) in idx and idx[(k, it)] <= TOP else f"{it} {KIND[k].lower()} ({sh * 100:.0f}%)"
                 for k, it, sh in s["limiting_causes"][:5]]
        lm = s["limiting_makers"]
        mk = "nothing makes it here" if not lm["total"] else (
            f"{lm['total']} assemblers at {at(lm['at'])}: {cnt(lm['working'])} working, {cnt(lm['starved'])} starved, {cnt(lm['blocked'])} output-blocked"
            + ("; missing " + ", ".join(f"{k} ({cnt(v)})" for k, v in lm["missing"].items()) if lm["missing"] else ""))
        L.append(f"limiting pack: {short(s['limiting'])} ({cnt(s['lack'][s['limiting']])}/{s['labs']} labs lack it) - {mk}")
        if parts:
            L.append("  traced to: " + ", ".join(parts))

    c = A["capacity"]
    L.append("== CAPACITY")
    shown = 0
    for n in c["power"]:
        if (n["cons"] <= 0 and n["gens"] == 0) or shown >= 3:
            continue
        shown += 1
        load = f" ({n['prod'] / n['cap'] * 100:.0f}% of {watts(n['cap'])} nameplate)" if n["cap"] > 0 else ""
        acc = f", {n['acc']} accumulators at {n['acc_e'] / n['acc_max'] * 100:.0f}%" if n["acc"] and n["acc_max"] else ""
        flag = "  <-- AT THE LIMIT" if n["cap"] > 0 and n["prod"] >= 0.97 * n["cap"] else ""
        L.append(f"power net {n['id']}: producing {watts(n['prod'])}{load}, {n['gens']} generators{acc}{flag}")
    dead = [n for n in c["power"] if n["gens"] == 0 and n["poles"] > 3]
    if dead:
        L.append(f"  {len(dead)} pole networks with no generator, e.g. at ({dead[0]['x']},{dead[0]['y']}) (save_diag.py lists them)")
    L.append(f"machines with no/low power or fuel: {c['low_power_machines']}" + (f" around {at(c['low_power_at'])}" if c["low_power_machines"] else "")
             + (f"; not plugged into any network: {c['unplugged']}" if c["unplugged"] else ""))
    if not c["robots"]:
        L.append("robots: no logistic network")
    for r in sorted(c["robots"], key=lambda r: -(r["logi"] + r["cons"]))[:3]:
        flag = ""
        if r["logi"] >= 20 and r["logi_free"] < 0.1 * r["logi"]:
            flag = "  <-- LOGISTIC ROBOTS SATURATED"
        if r["waiting_charge"] > 2 * r["roboports"]:
            flag += "  <-- CHARGING QUEUE (more roboports)"
        if c["ghosts"] and r["cons"] and r["cons_free"] == 0:
            flag += "  <-- no free construction robots"
        L.append(f"robot net {r['id']} at ({r['x']},{r['y']}): logistic {r['logi_free']}/{r['logi']} free, construction {r['cons_free']}/{r['cons']} free, "
                 f"{r['roboports']} roboports, {r['waiting_charge']} waiting to charge{flag}")
    for o in c["ore"]:
        st = f"{o['drills']} drills ({cnt(o['working'])} working, {cnt(o['blocked'])} output-blocked" + (f", {cnt(o['depleted'])} depleted" if o["depleted"] >= 0.5 else "") \
             + (f", {cnt(o['other'])} other" if o["other"] >= 0.5 else "") + ")"
        if o["infinite"]:
            L.append(f"{o['item']}: {st}, {num(o['made'])}/min now, {num(o['full_rate'])}/min if all ran")
        else:
            h = o.get("hours")
            L.append(f"{o['item']}: {num(o['left'])} left under {st}, {num(o['made'])}/min now ({num(o['full_rate'])}/min if all ran)"
                     + (f" = {h:.0f} h" if h is not None else "") + ("  <-- LESS THAN 5 h" if h is not None and h < 5 else ""))

    L.append("== IDLE CAPITAL (output-blocked: over-built, or the product has nowhere to go)")
    if not A["idle"]:
        L.append("  none")
    for d in A["idle"][:8]:
        by = f" (full of {d['by']})" if d.get("by") and d["by"] != "?" and d["by"] not in d["recipe"] else ""
        L.append(f"  {d['recipe']}: {cnt(d['blocked'])}/{d['total']} {d['machine']} blocked{by} at {at(d['at'])}")
    if A["no_recipe"]:
        L.append(f"  plus {A['no_recipe']} assemblers/furnaces with no recipe (never used)")

    L.append("== NEXT STEPS")
    for i, t in enumerate(A["next_steps"], 1):
        L.append(f"{i}. {t}")
    if not A["next_steps"]:
        L.append("  nothing is starved: expand (more science, next ore patch) rather than fix")
    return L


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save", help="save .zip (default: the most recent one)")
    ap.add_argument("--after", type=int, default=600, help="ticks to run (and sample once a second) before judging; default 600")
    ap.add_argument("--surface", default="nauvis")
    ap.add_argument("--json", metavar="FILE", help="also write the full analysis as JSON")
    ap.add_argument("--raw", metavar="FILE", help="also write the raw per-machine samples as JSON (debugging)")
    a = ap.parse_args()
    save = a.save or fenv.latest_save()
    T = collect(save, a.after, a.surface)
    if a.raw:
        json.dump(T, open(a.raw, "w"))
    A = analyse(T)
    if a.json:
        json.dump(A, open(a.json, "w"), indent=1, default=lambda o: sorted(o) if isinstance(o, set) else str(o))
    print("\n".join(render(A, save)))


if __name__ == "__main__":
    main()
