#!/usr/bin/env python3
"""Test a blueprint in an isolated headless Factorio (never touches the player's game).

Builds the blueprint on a lab-tile surface, revives every ghost, connects the poles the way a player
building by hand would, gives infinite power, feeds the input belts, empties the output belts (as if
they went into a chest) and reports production.

Examples:
  bp_sim.py plan.txt --research steel-processing --feed "iron-plate|coal" --minutes 5
  bp_sim.py book.txt --index 2 --feed "iron-plate|coal" --research all
  bp_sim.py plan.txt --check-only            # only import, build and check inserters/power

--feed "A|B"  lane 1 = A, lane 2 = B on an input belt ("A|A" or "A" for both lanes).
              Repeat --feed for several inputs; they are assigned in order to the belt heads
              (sorted by x, then y, of the built blueprint; see the numbered list it prints).
              Use --feed "|" to skip a head that is really the start of an output.

Output keys: import=, ghosts=, build_failures=, entities, infinite_power=, misaligned_or_floating_inserters=,
WARNING separate_electric_networks=, "input N at (x,y)", "output N at (x,y)", and every --report-every seconds
t=..s states= recipes_completed= collected_at_outputs= in_chests= unpowered_inserters=.
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bp as bptool  # noqa: E402
import fenv  # noqa: E402

LUA = r'''
local CFG = helpers.json_to_table(__CFG__)

local function status_name(v)
  for n, x in pairs(defines.entity_status) do if x == v then return n end end
  return tostring(v)
end
local function out(msg) log("BPSIM " .. msg) end
local function pos(p) return string.format("(%.1f,%.1f)", p.x, p.y) end

local function setup()
  local force = game.forces.player
  if CFG.research_all then force.research_all_technologies()
  else for _, t in pairs(CFG.research) do
    if force.technologies[t] then force.technologies[t].researched = true else out("WARNING unknown technology: " .. t) end
  end end

  local s = game.create_surface("bpsim")
  s.generate_with_lab_tiles = true; s.always_day = true
  s.request_to_generate_chunks({0, 0}, 6); s.force_generate_chunk_requests()

  local inv = game.create_inventory(1)
  local r = inv[1].import_stack(CFG.bp)
  out("import=" .. tostring(r) .. " (0 = ok)")
  if not inv[1].is_blueprint then out("ERROR the string is not a single blueprint (a book? use --index)"); return end
  local ghosts = inv[1].build_blueprint{surface = s, force = force, position = {0, 0}, build_mode = defines.build_mode.forced}
  local fails = 0
  local tagged_feeds, tagged_sinks = {}, {}
  for _, g in pairs(ghosts) do
    local tags = g.tags
    local _, ent = g.revive{raise_revive = true}
    if not ent then fails = fails + 1; out("ERROR could not build " .. (g.valid and (g.ghost_name .. " at " .. pos(g.position)) or "?")) end
    if ent and tags and tags.bpsim_fill then
      -- input chest: replaced with an editor infinity chest (simulation only)
      local p, items = ent.position, tags.bpsim_fill
      ent.destroy()
      local ic = s.create_entity{name = "infinity-chest", position = p, force = force}
      local i = 0
      for item in string.gmatch(items, "[^,]+") do   -- "a,b,c": one filter per item
        i = i + 1
        ic.set_infinity_container_filter(i, {name = item, count = 100, mode = "exactly"})
      end
      out("infinity chest of " .. items .. " at " .. pos(p))
    end
    if ent and tags and tags.bpsim_feed then
      local lanes = {}
      for l in string.gmatch(tags.bpsim_feed .. "|", "([^|]*)|") do table.insert(lanes, l) end
      if #lanes == 1 then lanes[2] = lanes[1] end
      table.insert(tagged_feeds, {ent = ent, lanes = lanes})
      out("feed " .. tags.bpsim_feed .. " at " .. pos(ent.position))
    end
    if ent and tags and tags.bpsim_sink then
      table.insert(tagged_sinks, ent)
      out("output at " .. pos(ent.position))
    end
    if ent and tags and tags.bpsim_fluid then
      -- fluid input: replaced with an editor infinity pipe (simulation only)
      local p, fluid = ent.position, tags.bpsim_fluid
      ent.destroy()
      local ip = s.create_entity{name = "infinity-pipe", position = p, force = force}
      ip.set_infinity_pipe_filter{name = fluid, percentage = 1}
      out("fluid " .. fluid .. " enters at " .. pos(p))
    end
  end
  out("ghosts=" .. #ghosts .. " build_failures=" .. fails)
  if #ghosts ~= CFG.n_entities then
    out("WARNING the blueprint has " .. CFG.n_entities .. " entities but " .. #ghosts .. " ghosts were placed (overlapping pieces?)")
  end

  local counts = {}
  for _, e in pairs(s.find_entities()) do counts[e.name] = (counts[e.name] or 0) + 1 end
  out("entities " .. serpent.line(counts))

  -- poles: connect copper to every pole in reach, as when building by hand
  local poles = s.find_entities_filtered{type = "electric-pole"}
  for _, p in pairs(poles) do
    local c = p.get_wire_connector(defines.wire_connector_id.pole_copper, true)
    local reach = p.prototype.get_max_wire_distance()
    for _, q in pairs(s.find_entities_filtered{type = "electric-pole", position = p.position, radius = reach}) do
      if q ~= p then
        local reach2 = math.min(reach, q.prototype.get_max_wire_distance())
        local dx, dy = p.position.x - q.position.x, p.position.y - q.position.y
        if dx * dx + dy * dy <= reach2 * reach2 then
          c.connect_to(q.get_wire_connector(defines.wire_connector_id.pole_copper, true), false)
        end
      end
    end
  end
  if #poles > 0 then
    -- try each pole until the power source lands inside its supply area
    local eei
    for _, p in pairs(poles) do
      local spot = s.find_non_colliding_position("electric-energy-interface", p.position, 3, 0.5)
      if spot then
        eei = s.create_entity{name = "electric-energy-interface", position = spot, force = force}
        if eei and eei.is_connected_to_electric_network() then break end
        if eei then eei.destroy(); eei = nil end
      end
    end
    if not eei then
      -- compact designs leave no free 2x2 spot inside a pole's area: power them from outside with a wire
      local box = {x = -1e9, y = 1e9}
      for _, e in pairs(s.find_entities()) do
        if e.position.x > box.x then box.x = e.position.x end
        if e.position.y < box.y then box.y = e.position.y end
      end
      local px, py = math.floor(box.x) + 4.5, math.floor(box.y) + 0.5
      local feeder = s.create_entity{name = "small-electric-pole", position = {px, py}, force = force}
      eei = s.create_entity{name = "electric-energy-interface", position = {px + 2, py}, force = force}
      if feeder and eei then
        local c = feeder.get_wire_connector(defines.wire_connector_id.pole_copper, true)
        local best, bd
        for _, p in pairs(poles) do
          local d = (p.position.x - px) ^ 2 + (p.position.y - py) ^ 2
          if not bd or d < bd then best, bd = p, d end
        end
        c.connect_to(best.get_wire_connector(defines.wire_connector_id.pole_copper, true), false)
        if not eei.is_connected_to_electric_network() then eei.destroy(); eei = nil end
      end
    end
    if eei then eei.power_production = 1e8; eei.electric_buffer_size = 1e9 end
    if CFG.join_networks and #poles > 1 then
      -- a blueprint fitted into a base may rely on the base's poles to join its groups: wire them all to one
      -- (network ids only update next tick, so don't compare them here)
      local hub = poles[1].get_wire_connector(defines.wire_connector_id.pole_copper, true)
      for i = 2, #poles do
        poles[i].get_wire_connector(defines.wire_connector_id.pole_copper, true).connect_to(hub, false)
      end
    end
    out("infinite_power=" .. tostring(eei ~= nil))
  else
    out("no electric poles in the blueprint")
  end

  -- inserters: what is at their pickup and drop positions
  local bad = 0
  for _, i in pairs(s.find_entities_filtered{type = "inserter"}) do
    local function at(p)
      for _, e in pairs(s.find_entities_filtered{position = p}) do
        if e.type ~= "inserter" and e.type ~= "electric-pole" then return e.name end
      end
      return "NOTHING"
    end
    local a, b = at(i.pickup_position), at(i.drop_position)
    if a == "NOTHING" or b == "NOTHING" then
      bad = bad + 1
      if bad <= 8 then out("WARNING inserter " .. pos(i.position) .. " dir=" .. i.direction .. " picks up from " .. a .. " and drops into " .. b) end
    end
  end
  out("misaligned_or_floating_inserters=" .. bad)
  local nets, nn = {}, 0
  for _, p in pairs(s.find_entities_filtered{type = "electric-pole"}) do
    local id = p.electric_network_id
    if id and id ~= 0 and not nets[id] then nets[id] = true; nn = nn + 1 end
  end
  if nn > 1 then out("WARNING separate_electric_networks=" .. nn .. ": poles are missing to join them") end

  -- belt heads and tails
  local heads, tails = {}, {}
  -- 2.1.7 removed LuaEntity::neighbours; underground_belt_neighbour replaces it for undergrounds
  local function ug_partner(b)
    local ok, v = pcall(function() return b.underground_belt_neighbour end)
    if ok then return v end
    return b.neighbours
  end
  for _, b in pairs(s.find_entities_filtered{type = {"transport-belt", "underground-belt", "splitter"}}) do
    local n = b.belt_neighbours
    local is_head = #n.inputs == 0
    -- an underground exit with no partner inside the blueprint is a real input (fed from outside)
    if b.type == "underground-belt" and b.belt_to_ground_type == "output" then is_head = (ug_partner(b) == nil) end
    if is_head then table.insert(heads, b) end
    -- a paired underground entrance continues below ground: not a tail even without neighbours
    local is_tail = #n.outputs == 0
    if b.type == "underground-belt" and b.belt_to_ground_type == "input" and ug_partner(b) then is_tail = false end
    if is_tail then table.insert(tails, b) end
  end
  table.sort(heads, function(a, b) if a.position.x ~= b.position.x then return a.position.x < b.position.x end return a.position.y < b.position.y end)
  for k, h in pairs(heads) do out("input " .. k .. " at " .. pos(h.position) .. " (" .. h.type .. ")") end
  for k, t in pairs(tails) do out("output " .. k .. " at " .. pos(t.position) .. " (" .. t.type .. ")") end

  storage.s = s; storage.heads = heads; storage.tails = tails; storage.sunk = {}
  if #tagged_feeds > 0 then storage.tagged_feeds = tagged_feeds end
  if #tagged_sinks > 0 then storage.tails = tagged_sinks; storage.tagged_sinks = true end
end

local function report(t)
  local s = storage.s
  local st, made = {}, {}
  for _, e in pairs(s.find_entities_filtered{type = {"furnace", "assembling-machine", "mining-drill", "lab"}}) do
    local key = e.name .. ":" .. status_name(e.status)
    st[key] = (st[key] or 0) + 1
    if e.type ~= "lab" and e.type ~= "mining-drill" then
      local r = e.get_recipe()
      local k = e.name .. ":" .. (r and r.name or "?")
      made[k] = (made[k] or 0) + e.products_finished
    end
  end
  local chests = {}
  for _, c in pairs(s.find_entities_filtered{type = {"container", "logistic-container"}}) do
    for _, it in pairs(c.get_inventory(defines.inventory.chest).get_contents()) do chests[it.name] = (chests[it.name] or 0) + it.count end
  end
  local unpowered = 0
  for _, e in pairs(s.find_entities_filtered{type = "inserter"}) do
    if e.status == defines.entity_status.no_power or e.status == defines.entity_status.low_power then unpowered = unpowered + 1 end
  end
  out(string.format("t=%ds states=%s recipes_completed=%s collected_at_outputs=%s in_chests=%s unpowered_inserters=%d",
    t / 60, serpent.line(st), serpent.line(made), serpent.line(storage.sunk), serpent.line(chests), unpowered))
end

script.on_event(defines.events.on_tick, function(ev)
  if ev.tick == 1 then setup(); return end
  if not storage.s then return end
  local feeds = CFG.feeds
  if storage.tagged_feeds then
    feeds = {}
    for k, f in pairs(storage.tagged_feeds) do storage.heads[k] = f.ent; feeds[k] = f.lanes end
  end
  for k, feed in pairs(feeds) do
    local h = storage.heads[k]
    if h and h.valid then
      -- belt/underground: lanes 1-2; splitter: 1-2 (left input) and 3-4 (right input)
      local lines = h.type == "splitter" and {1, 2, 3, 4} or {1, 2}
      for idx, line in pairs(lines) do
        local item = feed[(idx - 1) % 2 + 1]
        if item and item ~= "" then h.get_transport_line(line).insert_at_back({name = item, count = 1}) end
      end
    end
  end
  if CFG.sink then
    local only = {}
    for _, k in pairs(CFG.sink_only) do only[k] = true end
    for k, t in pairs(storage.tails) do
      if t.valid and (#CFG.sink_only == 0 or only[k] or storage.tagged_sinks) then
        for lane = 1, t.get_max_transport_line_index() do
          local tl = t.get_transport_line(lane)
          for _, it in pairs(tl.get_contents()) do
            local key = "output" .. k .. ".lane" .. lane .. ":" .. it.name
            storage.sunk[key] = (storage.sunk[key] or 0) + it.count
          end
          tl.clear()
        end
      end
    end
  end
  if ev.tick % CFG.report_every == 0 then report(ev.tick) end
end)
'''


def apply_spec(obj, spec):
    """Tags the entities named in a layout test spec (feed/sink/fluid/fill by entity number)."""
    by_num = {e["entity_number"]: e for e in obj["blueprint"]["entities"]}
    for key, tag in (("feed", "bpsim_feed"), ("fluid", "bpsim_fluid"), ("fill", "bpsim_fill")):
        for num, what in (spec.get(key) or {}).items():
            by_num[int(num)].setdefault("tags", {})[tag] = what
    for num in spec.get("sink") or []:
        by_num[int(num)].setdefault("tags", {})["bpsim_sink"] = True


def run(obj, research=(), feeds=(), minutes=3, report_every=30, sink=True, sink_only=(), check_only=False, keep=False,
        join_networks=False):
    """Simulates one blueprint object; returns (BPSIM lines, game errors)."""
    minutes = 0.1 if check_only else minutes
    cfg = {
        "bp": bptool.encode(obj),
        "research": [r for r in research if r != "all"],
        "research_all": "all" in research,
        "feeds": [list(f) for f in feeds],
        "sink": sink,
        "sink_only": list(sink_only),
        "n_entities": len(obj["blueprint"]["entities"]),
        "report_every": max(60, int(report_every * 60)) if not check_only else 300,
        "join_networks": join_networks,
    }
    with fenv.Instance("bpsim", control_lua=LUA.replace("__CFG__", fenv.long_string(json.dumps(cfg))), keep=keep) as inst:
        save = inst.new_map()
        inst.run_save(save, int(minutes * 3600) + 2)
        return inst.tagged("BPSIM"), [l for l in inst.errors() if "BPSIM" not in l]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="file with the blueprint string, '-' (stdin) or 'clip' (clipboard)")
    ap.add_argument("--index", help="blueprint inside a book (as numbered by bp.py summary)")
    ap.add_argument("--research", action="append", default=[], help="technology to mark as researched (repeatable) or 'all'")
    ap.add_argument("--feed", action="append", default=[], help='"lane1|lane2" for each input belt, in order')
    ap.add_argument("--minutes", type=float, default=3)
    ap.add_argument("--report-every", type=float, default=30, help="seconds between reports")
    ap.add_argument("--no-sink", action="store_true", help="don't empty the output belts")
    ap.add_argument("--sink", help="empty only these outputs (numbers from the list, e.g. 1,3); the rest back up as in game")
    ap.add_argument("--check-only", action="store_true", help="only build and check (a few seconds of game time)")
    ap.add_argument("--fluid", action="append", default=[],
                    help="N=fluid: entity #N of the blueprint (a 1x1 pipe) becomes an infinite source of that fluid")
    ap.add_argument("--fill", action="append", default=[],
                    help="N=item: chest #N of the blueprint becomes an infinity chest of that item")
    ap.add_argument("--keep", action="store_true", help="keep the temporary folder")
    ap.add_argument("--spec", help="layout test spec (<name>.test.json written by layout.Plan.save): feeds and "
                                   "outputs by entity; bp_make.py test does this and gives a verdict")
    a = ap.parse_args()

    obj = bptool.pick(bptool.decode(bptool.read_src(a.src)), a.index)
    k, _ = bptool.kind(obj)
    if k != "blueprint":
        raise SystemExit("Need a single blueprint; use --index (see 'bp.py summary').")

    def tag(opt, specs, key, must):
        for spec in specs:
            num, what = spec.split("=")
            hit = [e for e in obj["blueprint"]["entities"] if e["entity_number"] == int(num)]
            if not hit:
                raise SystemExit(f"{opt}: there is no entity #{num}")
            if not any(m in hit[0]["name"] for m in must):
                raise SystemExit(f"{opt}: entity #{num} is '{hit[0]['name']}', not a {' or '.join(must)}")
            hit[0].setdefault("tags", {})[key] = what
    if a.spec:
        spec = json.load(open(a.spec))
        apply_spec(obj, spec)
        a.research = a.research or spec.get("research", [])
    tag("--fluid", a.fluid, "bpsim_fluid", ["pipe"])
    tag("--fill", a.fill, "bpsim_fill", ["chest"])
    feeds = []
    for f in a.feed:
        lanes = f.split("|")
        feeds.append([lanes[0], lanes[1] if len(lanes) > 1 else lanes[0]])
    lines, errors = run(obj, a.research, feeds, a.minutes, a.report_every, not a.no_sink,
                        [int(x) for x in a.sink.split(",")] if a.sink else [], a.check_only, a.keep)
    print("\n".join(lines) if lines else "(no output from the script: check the log, rerun with --keep)")
    if errors:
        print("\nGAME ERRORS:\n" + "\n".join(errors))


if __name__ == "__main__":
    main()
