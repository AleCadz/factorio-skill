#!/usr/bin/env python3
"""Map survey of a save: where the ore, oil, water, cliffs, nests and the player's buildings are.

  save_map.py                                  -> text report on the most recent save
  save_map.py --save x.zip --png map.png       -> overview image of the whole generated map
  save_map.py --png m.png --area -700,-400,0,200 [--scale 2]   -> image of one area (pixels per tile)
  save_map.py --ascii -420,-140,-300,-60 [--step 2]            -> tile-level ASCII map for route planning
  save_map.py --json map.json                  -> patches, nests, bounds as JSON
  save_map.py --new-map [--seed N] [--radius 500] [--png m.png]  -> survey a fresh default map

Use it at the start of a new world to decide where to build, and on a big base to pick sites and routes.

Report: generated and CHARTED bounds (the game refuses to place blueprint ghosts in chunks the force has
never charted, so generated-but-uncharted regions are listed), base centre (spawn if nothing is built),
water share around it, resource patches sorted by distance (amount, bounding box, centre, player drills
on it, enemy nests within 100 tiles; oil fields with well count and total yield %) and nest clusters.
The report is printed when no output option is given, or with --report.

PNG: water blue, ores by colour, cliffs, buildings white, roboports yellow, nests red, uncharted chunks
dark, never-generated black; grid lines with world coordinates on the top and left edges; numbers on the
map are the patch numbers of the report. The pixel -> world mapping is also printed.

ASCII: ~ water, ^ cliff, # building or ghost, N nest/worm, i c k s u iron/copper/coal/stone/uranium,
o crude oil, r other resource, . free, blank = not generated. With --step N one character is NxN tiles
and shows the worst thing in them. Trees and rocks are ignored (robots clear them).

Runs on a copy of the save in an isolated headless Factorio; never touches the original.
"""
import argparse, json, math, os, shutil, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fenv  # noqa: E402
import save_lua  # noqa: E402
from bp_map import write_png  # noqa: E402

LUA = r'''
local CFG = helpers.json_to_table(__CFG__)
local floor = math.floor
if CFG.generate then
  s.request_to_generate_chunks({0, 0}, CFG.generate)
  s.force_generate_chunk_requests()
end
local SKIP = {character = 1, ["logistic-robot"] = 1, ["construction-robot"] = 1, ["combat-robot"] = 1, car = 1,
  ["spider-vehicle"] = 1, ["spider-leg"] = 1, ["tile-ghost"] = 1, ["item-request-proxy"] = 1, ["highlight-box"] = 1,
  ["character-corpse"] = 1, corpse = 1, ["artillery-flare"] = 1, ["capture-robot"] = 1, unit = 1,
  locomotive = 1, ["cargo-wagon"] = 1, ["fluid-wagon"] = 1, ["artillery-wagon"] = 1, ["item-entity"] = 1,
  ["deconstructible-tile-proxy"] = 1, projectile = 1, ["land-mine"] = 1}

-- chunks; water is only counted here (full / partly water), the tiles are read per grid
local chunks, wfull, wpart, nch = {}, {}, {}, 0
for ch in s.get_chunks() do
  if s.is_chunk_generated(ch) then
    chunks[nch + 1] = ch.x; chunks[nch + 2] = ch.y; chunks[nch + 3] = f.is_chunk_charted(s, ch) and 1 or 0
    nch = nch + 3
    local w = s.count_tiles_filtered{area = {{ch.x * 32, ch.y * 32}, {ch.x * 32 + 32, ch.y * 32 + 32}}, collision_mask = "water_tile"}
    if w == 1024 then wfull[#wfull + 1] = {ch.x * 32, ch.y * 32}
    elseif w > 0 then wpart[#wpart + 1] = {ch.x * 32, ch.y * 32} end
  end
end

-- entities, read once
local res, resmeta, nres = {}, {}, 0
for _, e in pairs(s.find_entities_filtered{type = "resource"}) do
  local p, n, name = e.position, nres, e.name
  nres = nres + 4
  res[n + 1] = name; res[n + 2] = floor(p.x); res[n + 3] = floor(p.y); res[n + 4] = e.amount
  if not resmeta[name] then
    local pr = e.prototype
    resmeta[name] = {infinite = pr.infinite_resource and 1 or 0, normal = pr.normal_resource_amount or 0}
  end
end
local boxes, drills, drill_names, robo, nb = {}, {}, {}, {}, 0
for _, e in pairs(s.find_entities_filtered{force = "player"}) do
  local t = e.type
  if not SKIP[t] then
    local b, n = e.bounding_box, nb * 4
    boxes[n + 1] = b.left_top.x; boxes[n + 2] = b.left_top.y; boxes[n + 3] = b.right_bottom.x; boxes[n + 4] = b.right_bottom.y
    local p = e.position
    nb = nb + 1
    if t == "mining-drill" then
      local tg = e.mining_target
      local q = tg and tg.position or p
      local m = #drills
      drills[m + 1] = floor(q.x); drills[m + 2] = floor(q.y); drill_names[#drill_names + 1] = tg and tg.name or ""
    elseif t == "roboport" then
      robo[#robo + 1] = floor(p.x); robo[#robo + 1] = floor(p.y)
    end
  end
end
local cliffs = {}
for _, e in pairs(s.find_entities_filtered{type = "cliff"}) do
  local b, n = e.bounding_box, #cliffs
  cliffs[n + 1] = b.left_top.x; cliffs[n + 2] = b.left_top.y; cliffs[n + 3] = b.right_bottom.x; cliffs[n + 4] = b.right_bottom.y
end
local nests, worms = {}, {}
for _, e in pairs(s.find_entities_filtered{force = "enemy", type = {"unit-spawner", "turret"}}) do
  local p = e.position
  local t = e.type == "unit-spawner" and nests or worms
  t[#t + 1] = floor(p.x); t[#t + 1] = floor(p.y)
end

-- bin everything into square cells of g.cell tiles, optionally only inside g.area
local OFF, M = 1048576, 2097152
-- helpers.table_to_json takes ~10 s on a few MB of numbers, so the big arrays are written by hand
local function A(t) return "[" .. table.concat(t, ",") .. "]" end
local function grid(g)
  local C, a = g.cell, g.area
  if C == "auto" then   -- whole map: the smallest power of two that fits it in g.target cells
    local x1, y1, x2, y2 = 1e9, 1e9, -1e9, -1e9
    for i = 1, nch, 3 do
      x1 = math.min(x1, chunks[i]); x2 = math.max(x2, chunks[i]); y1 = math.min(y1, chunks[i + 1]); y2 = math.max(y2, chunks[i + 1])
    end
    C = 1
    while C < 32 and math.max(x2 - x1 + 1, y2 - y1 + 1) * 32 / C > g.target do C = C * 2 end
  end
  local function tin(x, y) return not a or (x >= a[1] and x < a[3] and y >= a[2] and y < a[4]) end
  local function key(x, y) return (floor(x / C) + OFF) * M + floor(y / C) + OFF end
  local water, cl, bu, rs = {}, {}, {}, {}
  local blocks, nbl = {}, 0   -- water as {x, y, size, tiles}: big output tables are very slow to build and encode
  local aligned = 32 % C == 0
  local function touches(c) return not a or (c[1] + 32 > a[1] and c[1] < a[3] and c[2] + 32 > a[2] and c[2] < a[4]) end
  local function within(c) return not a or (c[1] >= a[1] and c[1] + 32 <= a[3] and c[2] >= a[2] and c[2] + 32 <= a[4]) end
  local function clipped(c)   -- the chunk's box cut to the area
    if not a then return c[1], c[2], c[1] + 32, c[2] + 32 end
    return math.max(c[1], a[1]), math.max(c[2], a[2]), math.min(c[1] + 32, a[3]), math.min(c[2] + 32, a[4])
  end
  for _, c in pairs(wfull) do
    if touches(c) then
      if aligned and within(c) then
        blocks[nbl + 1] = c[1]; blocks[nbl + 2] = c[2]; blocks[nbl + 3] = 32; blocks[nbl + 4] = 1024; nbl = nbl + 4
      else
        local xa, ya, xb, yb = clipped(c)
        for x = xa, xb - 1 do
          local kx = (floor(x / C) + OFF) * M + OFF
          for y = ya, yb - 1 do local k = kx + floor(y / C); water[k] = (water[k] or 0) + 1 end
        end
      end
    end
  end
  local function quad(x, y, size)   -- quadtree count: only the shoreline is looked at closely
    local n = s.count_tiles_filtered{area = {{x, y}, {x + size, y + size}}, collision_mask = "water_tile"}
    if n == 0 then return end
    if size <= C or n == size * size then   -- one cell's count, or a block that is all water
      blocks[nbl + 1] = x; blocks[nbl + 2] = y; blocks[nbl + 3] = size; blocks[nbl + 4] = n; nbl = nbl + 4
    else
      local h = size / 2
      quad(x, y, h); quad(x + h, y, h); quad(x, y + h, h); quad(x + h, y + h, h)
    end
  end
  for _, c in pairs(wpart) do
    if touches(c) then
      if aligned and within(c) then
        quad(c[1], c[2], 16); quad(c[1] + 16, c[2], 16); quad(c[1], c[2] + 16, 16); quad(c[1] + 16, c[2] + 16, 16)
      else
        local xa, ya, xb, yb = clipped(c)
        for _, t in pairs(s.find_tiles_filtered{area = {{xa, ya}, {xb, yb}}, collision_mask = "water_tile"}) do
          local p = t.position
          local k = (floor(p.x / C) + OFF) * M + floor(p.y / C) + OFF
          water[k] = (water[k] or 0) + 1
        end
      end
    end
  end
  local function mark(list, into)
    for i = 1, #list, 4 do
      for cx = floor((list[i] + 0.01) / C), floor((list[i + 2] - 0.01) / C) do
        for cy = floor((list[i + 1] + 0.01) / C), floor((list[i + 3] - 0.01) / C) do
          if tin(cx * C, cy * C) then local k = (cx + OFF) * M + cy + OFF; into[k] = (into[k] or 0) + 1 end
        end
      end
    end
  end
  mark(cliffs, cl); mark(boxes, bu)
  for i = 1, #res, 4 do
    local name, x, y, am = res[i], res[i + 1], res[i + 2], res[i + 3]
    if tin(x, y) then
      local t = rs[name]; if not t then t = {}; rs[name] = t end
      local k = key(x, y)
      local c = t[k]
      if not c then t[k] = {am, 1, x, y, x, y}
      else
        c[1] = c[1] + am; c[2] = c[2] + 1
        if x < c[3] then c[3] = x end; if y < c[4] then c[4] = y end
        if x > c[5] then c[5] = x end; if y > c[6] then c[6] = y end
      end
    end
  end
  local function flat(t)
    local o, n = {}, 0   -- (a counter, not #o: the length operator gets slow on big tables)
    for k, v in pairs(t) do o[n + 1] = floor(k / M) - OFF; o[n + 2] = k % M - OFF; o[n + 3] = v; n = n + 3 end
    return o
  end
  local ro = {}
  for name, t in pairs(rs) do
    local o, n = {}, 0
    for k, c in pairs(t) do
      o[n + 1] = floor(k / M) - OFF; o[n + 2] = k % M - OFF
      for j = 1, 6 do o[n + 2 + j] = c[j] end
      n = n + 8
    end
    ro[#ro + 1] = '"' .. name .. '":' .. A(o)   -- prototype names need no escaping
  end
  return '{"cell":' .. C .. ',"water":' .. A(flat(water)) .. ',"wblocks":' .. A(blocks) .. ',"cliff":' .. A(flat(cl))
    .. ',"build":' .. A(flat(bu)) .. ',"res":{' .. table.concat(ro, ",") .. "}}"
end

local grids = {}
for i, g in pairs(CFG.grids) do grids[i] = grid(g) end
local sp = f.get_spawn_position(s)
local meta = json({resmeta = resmeta, drill_targets = drill_names, nbuild = nb, spawn = {sp.x, sp.y}, tick = game.tick,
  seed = s.map_gen_settings.seed})
helpers.write_file("save_map.json", '{"meta":' .. meta .. ',"chunks":' .. A(chunks) .. ',"drills":' .. A(drills) .. ',"robo":' .. A(robo)
  .. ',"nests":' .. A(nests) .. ',"worms":' .. A(worms) .. ',"grids":[' .. table.concat(grids, ",") .. "]}")
out("OK", nch / 3, nb)
'''

SYM = {"iron-ore": "i", "copper-ore": "c", "coal": "k", "stone": "s", "uranium-ore": "u", "crude-oil": "o"}
COL = {"iron-ore": (90, 140, 190), "copper-ore": (215, 120, 55), "coal": (15, 15, 15), "stone": (185, 170, 120),
       "uranium-ore": (90, 230, 60), "crude-oil": (225, 70, 225)}
OTHER_RES = (150, 255, 220)
LAND, WATER, CLIFF, BUILD, ROBO, NEST, WORM = (88, 76, 58), (40, 85, 180), (150, 105, 35), (240, 240, 240), (255, 235, 0), (255, 30, 30), (170, 30, 30)
DARK = 0.5    # brightness of generated-but-uncharted chunks

FONT = {  # 3x5 glyphs, rows top to bottom
    "0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111",
    "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001010010010",
    "8": "111101111101111", "9": "111101111001111", "-": "000000111000000", " ": "000000000000000",
    "A": "010101111101101", "B": "110101110101110", "C": "011100100100011", "D": "110101101101110",
    "E": "111100110100111", "F": "111100110100100", "G": "011100101101011", "H": "101101111101101",
    "I": "111010010010111", "J": "001001001101010", "K": "101101110101101", "L": "100100100100111",
    "M": "101111111101101", "N": "110101101101101", "O": "010101101101010", "P": "110101110100100",
    "Q": "010101101110011", "R": "110101110101101", "S": "011100010001110", "T": "111010010010010",
    "U": "101101101101111", "V": "101101101101010", "W": "101101111111101", "X": "101101010101101",
    "Y": "101101010010010", "Z": "111001010100111", ".": "000000000000010", ",": "000000000010100",
    ":": "000010000010000", "/": "001001010100100", "=": "000111000111000", "+": "000010111010000",
    "#": "101111101111101", "%": "101001010100101", "(": "001010010010001", ")": "100010010010100",
    "_": "000000000000111",
}


def arr(x):
    """Lua writes an empty table as {} (a JSON object); treat it as an empty list."""
    return x if isinstance(x, list) else []


class Grid:
    def __init__(self, g):
        self.cell = g["cell"]
        w, c, b = arr(g["water"]), arr(g["cliff"]), arr(g["build"])
        self.water = {(w[i], w[i + 1]): w[i + 2] for i in range(0, len(w), 3)}
        C, wb = self.cell, arr(g.get("wblocks"))
        for i in range(0, len(wb), 4):
            x, y, size, n = wb[i:i + 4]
            if size <= C:
                self.water[(x // C, y // C)] = self.water.get((x // C, y // C), 0) + n
            else:
                for cx in range(x // C, (x + size) // C):
                    for cy in range(y // C, (y + size) // C):
                        self.water[(cx, cy)] = C * C
        self.cliff = {(c[i], c[i + 1]) for i in range(0, len(c), 3)}
        self.build = {(b[i], b[i + 1]): b[i + 2] for i in range(0, len(b), 3)}
        self.res = {}
        for name, r in (g["res"] or {}).items():
            self.res[name] = {(r[i], r[i + 1]): tuple(r[i + 2:i + 8]) for i in range(0, len(r), 8)}


def survey(save, surface, grids, generate=None):
    cfg = {"grids": grids, "generate": generate}
    tmp = tempfile.mkdtemp(prefix="savemap-", dir=fenv.tmp_base())
    try:
        lines = save_lua.run(LUA.replace("__CFG__", save_lua.long_string(json.dumps(cfg))), save, surface=surface, out_dir=tmp)
        src = os.path.join(tmp, "save_map.json")
        if not os.path.exists(src):
            if any("global 's'" in l for l in lines):
                raise SystemExit(f"The save has no surface called {surface}.")
            raise SystemExit("Could not read the save:\n" + "\n".join(lines))
        d = json.load(open(src))
        d.update(d.pop("meta"))
        return d
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def new_map(seed):
    """Create a fresh default map; returns (path, tmpdir to remove)."""
    tmp = tempfile.mkdtemp(prefix="savemap-new-", dir=fenv.tmp_base())
    save = os.path.join(tmp, "new.zip")
    with fenv.Instance("savemapnew", user_mods=True) as inst:
        inst.run("--create", save, *(["--map-gen-seed", str(seed)] if seed is not None else []), check=True)
    return save, tmp


# ---------------------------------------------------------------- analysis

def components(cells, reach=1):
    """Connected groups of (x, y) keys; two keys touch when both coordinates differ by <= reach."""
    todo, out = set(cells), []
    nb = [(dx, dy) for dx in range(-reach, reach + 1) for dy in range(-reach, reach + 1) if dx or dy]
    while todo:
        start = todo.pop()
        comp, stack = [start], [start]
        while stack:
            x, y = stack.pop()
            for dx, dy in nb:
                k = (x + dx, y + dy)
                if k in todo:
                    todo.remove(k); comp.append(k); stack.append(k)
        out.append(comp)
    return out


def box_dist(px, py, b):
    dx = max(b[0] - px, 0, px - b[2]); dy = max(b[1] - py, 0, py - b[3])
    return math.hypot(dx, dy)


def analyse(d, g, newmap=False):
    C = g.cell
    ch = arr(d["chunks"])
    chunks = {(ch[i], ch[i + 1]): ch[i + 2] for i in range(0, len(ch), 3)}
    if newmap:
        chunks = {k: 1 for k in chunks}
    r = {"chunks": chunks, "spawn": [round(v) for v in d["spawn"]], "buildings": d["nbuild"], "tick": d["tick"], "seed": d.get("seed")}

    def bounds(keys):
        keys = list(keys)
        if not keys:
            return None
        return [min(k[0] for k in keys) * 32, min(k[1] for k in keys) * 32, max(k[0] for k in keys) * 32 + 32, max(k[1] for k in keys) * 32 + 32]
    r["generated"] = bounds(chunks); r["generated_chunks"] = len(chunks)
    charted = [k for k, v in chunks.items() if v]
    r["charted"] = bounds(charted); r["charted_chunks"] = len(charted)

    # base centre: median of building cells (robust against a long rail line or a far outpost)
    if d["nbuild"] >= 5 and g.build:
        ws = sorted((k[0], n) for k, n in g.build.items()); hs = sorted((k[1], n) for k, n in g.build.items())

        def wmed(v):
            half, acc = sum(n for _, n in v) / 2, 0
            for x, n in v:
                acc += n
                if acc >= half:
                    return x * C + C // 2
        r["centre"] = [wmed(ws), wmed(hs)]; r["centre_kind"] = "base"
        r["base_box"] = [min(k[0] for k in g.build) * C, min(k[1] for k in g.build) * C,
                         max(k[0] for k in g.build) * C + C, max(k[1] for k in g.build) * C + C]
    else:
        r["centre"] = list(r["spawn"]); r["centre_kind"] = "spawn"; r["base_box"] = None
    cx, cy = r["centre"]

    # generated but never charted
    un = [k for k, v in chunks.items() if not v]
    r["uncharted_chunks"] = len(un)
    r["uncharted_sides"], r["uncharted_holes"], r["uncharted_nearest"] = {}, 0, None
    if un and charted:
        G, K = r["generated"], r["charted"]
        for side, lo, hi, ax in (("north", G[1], K[1], "y"), ("south", K[3], G[3], "y"), ("west", G[0], K[0], "x"), ("east", K[2], G[2], "x")):
            if hi > lo:
                r["uncharted_sides"][side] = f"{ax} {lo}..{hi}"
        r["uncharted_holes"] = sum(1 for k in un if K[0] <= k[0] * 32 < K[2] and K[1] <= k[1] * 32 < K[3])
        bb = r["base_box"] or [cx, cy, cx, cy]

        def gap(k):   # tiles between the chunk and the box around the buildings
            return math.hypot(max(k[0] * 32 - bb[2], 0, bb[0] - k[0] * 32 - 32), max(k[1] * 32 - bb[3], 0, bb[1] - k[1] * 32 - 32))
        k = min(un, key=gap)
        r["uncharted_nearest"] = {"box": [k[0] * 32, k[1] * 32, k[0] * 32 + 32, k[1] * 32 + 32], "from_buildings": round(gap(k)),
                                  "from_centre": round(box_dist(cx, cy, [k[0] * 32, k[1] * 32, k[0] * 32 + 32, k[1] * 32 + 32]))}

    # water share around the centre
    r["water_share"] = {}
    for rad in (100, 300):
        tot = wat = 0
        for ix in range((cx - rad) // C, (cx + rad) // C + 1):
            for iy in range((cy - rad) // C, (cy + rad) // C + 1):
                if ((ix * C) // 32, (iy * C) // 32) in chunks:
                    tot += C * C; wat += g.water.get((ix, iy), 0)
        r["water_share"][rad] = round(100 * wat / tot, 1) if tot else None

    # nests
    n, w = arr(d["nests"]), arr(d["worms"])
    nests = [(n[i], n[i + 1]) for i in range(0, len(n), 2)]
    worms = [(w[i], w[i + 1]) for i in range(0, len(w), 2)]
    r["nest_points"], r["worm_points"] = nests, worms
    bins = {}
    for p in nests:
        bins.setdefault((p[0] // 32, p[1] // 32), []).append(p)
    wb = {}
    for p in worms:
        wb[(p[0] // 32, p[1] // 32)] = wb.get((p[0] // 32, p[1] // 32), 0) + 1
    r["nests"] = []
    for comp in components(bins):
        pts = [p for k in comp for p in bins[k]]
        mx, my = round(sum(p[0] for p in pts) / len(pts)), round(sum(p[1] for p in pts) / len(pts))
        r["nests"].append({"spawners": len(pts), "worms": sum(wb.get(k, 0) for k in comp), "centre": [mx, my],
                           "box": [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)],
                           "dist": round(min(math.hypot(p[0] - cx, p[1] - cy) for p in pts))})
    r["nests"].sort(key=lambda x: x["dist"])

    # resource patches
    dr = arr(d["drills"])
    drills = [(dr[i], dr[i + 1], t) for i, t in zip(range(0, len(dr), 2), arr(d["drill_targets"]))]
    patches = []
    for name, cells in g.res.items():
        meta = d["resmeta"].get(name, {})
        inf = bool(meta.get("infinite"))
        for comp in components(cells, reach=max(1, 32 // C) if inf else max(1, 8 // C)):
            vals = [cells[k] for k in comp]
            amount = sum(v[0] for v in vals); count = sum(v[1] for v in vals)
            box = [min(v[2] for v in vals), min(v[3] for v in vals), max(v[4] for v in vals) + 1, max(v[5] for v in vals) + 1]
            mx = sum((v[2] + v[4] + 1) / 2 * v[1] for v in vals) / count; my = sum((v[3] + v[5] + 1) / 2 * v[1] for v in vals) / count
            p = {"name": name, "amount": amount, "tiles": count, "box": box, "centre": [round(mx), round(my)],
                 "dist": round(math.hypot(mx - cx, my - cy)), "edge_dist": round(box_dist(cx, cy, box)), "drills": 0,
                 "nests_100": sum(1 for q in nests if box_dist(q[0], q[1], box) <= 100), "_cells": set(comp)}
            if inf:
                p["wells"] = count
                p["yield_pct"] = round(100 * amount / meta["normal"]) if meta.get("normal") else None
            ch_ = [bool(chunks.get((v[2] // 32, v[3] // 32))) for v in vals]
            p["charted"] = "yes" if all(ch_) else "partly" if any(ch_) else "no"
            patches.append(p)
    by_name = {}
    for p in patches:
        by_name.setdefault(p["name"], []).append(p)
    for x, y, target in drills:
        k = (x // C, y // C)
        cands = by_name.get(target, []) if target else patches
        hit = next((p for p in cands if k in p["_cells"]), None)
        if not hit and not target:   # idle drill: look one cell around
            hit = next((p for p in cands for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (k[0] + dx, k[1] + dy) in p["_cells"]), None)
        if hit:
            hit["drills"] += 1
    patches.sort(key=lambda p: p["dist"])
    for i, p in enumerate(patches, 1):
        p["id"] = i; del p["_cells"]
    r["patches"] = patches
    r["robo"] = arr(d["robo"])
    return r


def fmt_amount(a):
    return f"{a / 1e6:.1f}M" if a >= 1e6 else f"{a / 1e3:.0f}k"


def fmt_box(b):
    return f"x {b[0]}..{b[2]}, y {b[1]}..{b[3]}"


def report(r, title, top, newmap=False):
    L = [f"Map survey: {title}   ({r['tick'] / 216000:.1f} h played" + (f", seed {r['seed']}" if r.get("seed") else "") + ")"]
    if not r["generated"]:
        return L + ["Nothing generated on this surface."]
    L.append(f"Generated: {fmt_box(r['generated'])} ({r['generated_chunks']} chunks)")
    cx, cy = r["centre"]
    if not newmap:
        if r["charted"]:
            L.append(f"Charted:   {fmt_box(r['charted'])} ({r['charted_chunks']} chunks)")
        else:
            L.append("Charted:   nothing (no radar or player has seen this surface)")
        if r["uncharted_chunks"] and r["charted"]:
            L.append(f"Generated but never charted: {r['uncharted_chunks']} chunks. The game refuses blueprint ghosts there until a player or"
                     " radar charts them.")
            if r["uncharted_sides"]:
                L.append("  beyond the charted box: " + "; ".join(f"{k} {v}" for k, v in r["uncharted_sides"].items())
                         + (f"; plus {r['uncharted_holes']} chunks inside it" if r["uncharted_holes"] else ""))
            n = r["uncharted_nearest"]
            L.append(f"  nearest: chunk {fmt_box(n['box'])}, {n['from_buildings']} tiles from the buildings, {n['from_centre']} from the centre")
    if r["centre_kind"] == "base":
        L.append(f"Base centre ({cx},{cy}) = median of {r['buildings']} buildings; they span {fmt_box(r['base_box'])}. Spawn ({r['spawn'][0]},{r['spawn'][1]}).")
    else:
        L.append(f"No base yet; distances are from the spawn ({cx},{cy}).")
    ws = r["water_share"]
    L.append("Water around the centre: " + ", ".join(f"{v}% within {k} tiles" for k, v in ws.items() if v is not None))
    P = r["patches"]
    tot = {}
    for p in P:
        t = tot.setdefault(p["name"], [0, 0, 0]); t[0] += p["amount"]; t[1] += 1; t[2] += p.get("wells", 0)
    L.append("")
    L.append(f"Resources: {len(P)} patches. Totals: " + "; ".join(
        (f"{n} {t[2]} wells in {t[1]} fields" if t[2] else f"{n} {fmt_amount(t[0])} in {t[1]}") for n, t in sorted(tot.items(), key=lambda kv: -kv[1][0])))
    L.append(f"  {'#':>3} {'resource':<12} {'amount':>11} {'box':<32} {'centre':<13} {'dist':>5} {'drills':>6} {'nests<100':>9}")
    shown = P[:top]
    for p in shown:
        am = f"{p['wells']}w {p['yield_pct']}%" if "wells" in p else fmt_amount(p["amount"])
        L.append(f"  {p['id']:>3} {p['name']:<12} {am:>11} {fmt_box(p['box']):<32} {'(%d,%d)' % tuple(p['centre']):<13} {p['dist']:>5} "
                 f"{p['drills'] or '-':>6} {p['nests_100'] or '-':>9}" + ("" if newmap else {"yes": "", "partly": "  partly uncharted", "no": "  uncharted"}[p["charted"]]))
    if len(P) > len(shown):
        L.append(f"  ... {len(P) - len(shown)} more further away (--top N, or --json for all)")
    L.append("  (dist = base centre to patch centre; oil: wells and summed yield %; drills = player drills/pumpjacks mining it)")
    N = r["nests"]
    L.append("")
    if N:
        L.append(f"Enemy: {sum(n['spawners'] for n in N)} nests in {len(N)} clusters, {len(r['worm_points'])} worms. Nearest:")
        for n in N[:6]:
            L.append(f"  {n['spawners']:>3} nests {n['worms']:>3} worms  centre ({n['centre'][0]},{n['centre'][1]})  {fmt_box(n['box'])}  {n['dist']} tiles away")
    else:
        L.append("Enemy: no nests on the generated map.")
    return L


# ---------------------------------------------------------------- ASCII

def ascii_map(g, r, area):
    x1, y1, x2, y2 = area
    C = g.cell
    chunks = r["chunks"]
    top = {}
    for name, cells in g.res.items():
        for k, v in cells.items():
            if k not in top or v[0] > top[k][1]:
                top[k] = (name, v[0])
    enemy = {(p[0] // C, p[1] // C) for p in r["nest_points"] + r["worm_points"]}
    cols = range(x1 // C, -(-x2 // C))
    lab = [" "] * (len(cols) + 8); tick = [" "] * len(cols)
    for i, ix in enumerate(cols):
        if ix % 10 == 0:
            tick[i] = "|"
            for j, c in enumerate(str(ix * C)):
                lab[i + j] = c
    L = [f"x {x1}..{x2}, y {y1}..{y2}; one character = {C}x{C} tiles; the column under each '|' is the x written above it",
         " " * 7 + "".join(lab).rstrip(), " " * 7 + "".join(tick)]
    for iy in range(y1 // C, -(-y2 // C)):
        row = []
        for ix in cols:
            k = (ix, iy)
            if ((ix * C) // 32, (iy * C) // 32) not in chunks: ch = " "
            elif k in enemy: ch = "N"
            elif k in g.cliff: ch = "^"
            elif k in g.build: ch = "#"
            elif k in g.water: ch = "~"
            elif k in top: ch = SYM.get(top[k][0], "r")
            else: ch = "."
            row.append(ch)
        L.append(f"{iy * C:>6} " + "".join(row).rstrip())
    L.append("~ water  ^ cliff  # building/ghost  N nest/worm  i iron  c copper  k coal  s stone  u uranium  o oil  r other  . free")
    return L


# ---------------------------------------------------------------- PNG

class Canvas:
    def __init__(self, w, h, bg=(0, 0, 0)):
        self.w, self.h = w, h
        self.rows = [bytearray(bytes(bg) * w) for _ in range(h)]

    def rect(self, x, y, w, h, c):
        x0, x1 = max(0, x), min(self.w, x + w)
        if x1 <= x0:
            return
        b = bytes(c) * (x1 - x0)
        for yy in range(max(0, y), min(self.h, y + h)):
            self.rows[yy][x0 * 3:x1 * 3] = b

    def text(self, x, y, s, c=(255, 255, 255), k=2, bg=None):
        s = s.upper()
        if bg is not None:
            self.rect(x - 2, y - 2, len(s) * 4 * k + 2, 5 * k + 4, bg)
        for ch in s:
            gl = FONT.get(ch, FONT["_"])
            for i, bit in enumerate(gl):
                if bit == "1":
                    self.rect(x + (i % 3) * k, y + (i // 3) * k, k, k, c)
            x += 4 * k

    def save(self, path):
        write_png(path, self.w, self.h, b"".join(self.rows))


def text_w(s, k=2):
    return len(s) * 4 * k


def pick_scale(area, target=1500):
    span = max(area[2] - area[0], area[3] - area[1])
    for px in (8, 6, 4, 3, 2, 1):
        if span * px <= target:
            return px, 1
    for cell in (2, 4, 8, 16, 32):
        if span / cell <= target:
            return 1, cell
    return 1, 32


def render(g, r, area, px, path, title, labels=25):
    C = g.cell
    x1, y1, x2, y2 = area
    nx, ny = (x2 - x1) // C, (y2 - y1) // C
    ML, MT, MR = 52, 40, 10
    legend = [("water", WATER), ("iron", COL["iron-ore"]), ("copper", COL["copper-ore"]), ("coal", COL["coal"]),
              ("stone", COL["stone"]), ("uranium", COL["uranium-ore"]), ("oil", COL["crude-oil"]), ("cliff", CLIFF),
              ("building", BUILD), ("roboport", ROBO), ("nest", NEST), ("worm", WORM), ("land", LAND),
              ("uncharted", tuple(int(v * DARK) for v in LAND)), ("not generated", (0, 0, 0))]
    W = max(ML + nx * px + MR, 420)
    lines, cur, curw = [], [], 0
    for it in legend:
        w = 14 + text_w(it[0]) + 14
        if cur and curw + w > W - 16:
            lines.append(cur); cur, curw = [], 0
        cur.append(it); curw += w
    lines.append(cur)
    MB = 10 + 16 * len(lines) + 16
    H = MT + ny * px + MB
    cv = Canvas(W, H, (24, 24, 28))
    chunks = r["chunks"]
    tpp = C / px   # tiles per pixel

    def X(x): return ML + int((x - x1) // C) * px
    def Y(y): return MT + int((y - y1) // C) * px

    def shade(c, k):
        return c if chunks.get(((k[0] * C) // 32, (k[1] * C) // 32)) else tuple(int(v * DARK) for v in c)

    def cell(k, c, grow=0):
        cv_x, cv_y = ML + (k[0] - x1 // C) * px, MT + (k[1] - y1 // C) * px
        if 0 <= k[0] - x1 // C < nx and 0 <= k[1] - y1 // C < ny:
            cv.rect(cv_x - grow, cv_y - grow, px + 2 * grow, px + 2 * grow, c)

    def clip(x, y, w, h, c):   # rect clipped to the map window
        xa, ya = max(x, ML), max(y, MT); xb, yb = min(x + w, ML + nx * px), min(y + h, MT + ny * px)
        if xb > xa and yb > ya:
            cv.rect(xa, ya, xb - xa, yb - ya, c)

    cv.rect(ML, MT, nx * px, ny * px, (0, 0, 0))
    for (cx, cy), charted in chunks.items():
        clip(X(cx * 32), Y(cy * 32), X(cx * 32 + 32) - X(cx * 32), Y(cy * 32 + 32) - Y(cy * 32),
             LAND if charted else tuple(int(v * DARK) for v in LAND))
    for k, n in g.water.items():
        fr = min(1.0, n / (C * C))
        cell(k, shade(tuple(int(LAND[i] + (WATER[i] - LAND[i]) * (0.35 + 0.65 * fr)) for i in range(3)), k))
    # grid
    gstep = next((s for s in (100, 500, 1000, 5000) if s / tpp >= 14), 10000)
    lstep = next((s for s in (10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000) if s / tpp >= 64 and (s % gstep == 0 or s < gstep)), 20000)
    for v in range(-(-x1 // gstep) * gstep, x2, gstep):
        clip(X(v), MT, 1, ny * px, (230, 200, 60) if v == 0 else (150, 150, 150) if v % (gstep * 5) == 0 else (118, 108, 92))
    for v in range(-(-y1 // gstep) * gstep, y2, gstep):
        clip(ML, Y(v), nx * px, 1, (230, 200, 60) if v == 0 else (150, 150, 150) if v % (gstep * 5) == 0 else (118, 108, 92))
    # features
    top = {}
    for name, cells in g.res.items():
        for k, v in cells.items():
            if k not in top or v[0] > top[k][1]:
                top[k] = (name, v[0])
    grow_oil = 0 if px >= 3 else 1
    for k, (name, _) in top.items():
        cell(k, COL.get(name, OTHER_RES), grow_oil if name == "crude-oil" else 0)
    for k in g.cliff:
        cell(k, shade(CLIFF, k))
    for k in g.build:
        cell(k, BUILD)
    rb = r["robo"]
    m = max(3, min(px * 4, 9))
    for i in range(0, len(rb), 2):
        if x1 <= rb[i] < x2 and y1 <= rb[i + 1] < y2:
            clip(X(rb[i]) - m // 2, Y(rb[i + 1]) - m // 2, m, m, ROBO)
    m = max(3, min(int(5 / tpp), 14))
    for pts, c, mm in ((r["worm_points"], WORM, max(2, m // 2)), (r["nest_points"], NEST, m)):
        for p in pts:
            if x1 <= p[0] < x2 and y1 <= p[1] < y2:
                clip(X(p[0]) - mm // 2, Y(p[1]) - mm // 2, mm, mm, c)
    # centre marker
    cx, cy = r["centre"]
    if x1 <= cx < x2 and y1 <= cy < y2:
        for dx, dy, w, h in ((-9, -1, 19, 3), (-1, -9, 3, 19)):
            clip(X(cx) + dx, Y(cy) + dy, w, h, (0, 0, 0))
        clip(X(cx) - 8, Y(cy), 17, 1, (255, 0, 255)); clip(X(cx), Y(cy) - 8, 1, 17, (255, 0, 255))
    # patch numbers
    if labels:
        for p in r["patches"][:labels]:
            px_, py_ = p["centre"]
            if x1 <= px_ < x2 and y1 <= py_ < y2:
                s = str(p["id"])
                tx = min(max(X(px_) - text_w(s) // 2, ML + 2), ML + nx * px - text_w(s) - 2)
                ty = min(max(Y(py_) - 5, MT + 2), MT + ny * px - 12)
                cv.text(tx, ty, s, (255, 255, 255), 2, bg=(20, 20, 20))
    # axes
    for v in range(-(-x1 // lstep) * lstep, x2, lstep):
        s = str(v)
        cv.rect(X(v), MT - 5, 1, 5, (255, 255, 255))
        cv.text(min(max(X(v) - text_w(s) // 2, 2), W - text_w(s) - 2), MT - 17, s, (255, 255, 255))
    for v in range(-(-y1 // lstep) * lstep, y2, lstep):
        s = str(v)
        cv.rect(ML - 5, Y(v), 5, 1, (255, 255, 255))
        cv.text(ML - 7 - text_w(s), Y(v) - 5, s, (255, 255, 255))
    head = f"{title}  x {x1}..{x2}  y {y1}..{y2}  " + (f"1 px = {C} tiles" if C > 1 else f"1 tile = {px} px") + f"  grid {gstep}"
    while text_w(head) > W - 8 and "  " in head:
        head = head[head.index("  ") + 2:]
    cv.text(4, 3, head, (200, 200, 200))
    ly = MT + ny * px + 10
    for line in lines:
        lx = 8
        for name, c in line:
            cv.rect(lx - 1, ly - 1, 12, 12, (200, 200, 200)); cv.rect(lx, ly, 10, 10, c)
            cv.text(lx + 14, ly, name, (220, 220, 220))
            lx += 14 + text_w(name) + 14
        ly += 16
    cv.text(8, ly, "+ centre" + (f"   numbers = the {labels} nearest patches, # as in the report" if labels else ""), (255, 0, 255))
    cv.save(path)
    return (f"{path}: {W}x{H} px. World x = {x1} + (px - {ML}) * {tpp:g}, y = {y1} + (py - {MT}) * {tpp:g}; "
            f"grid lines every {gstep} tiles, labels every {lstep}.")


def parse_area(text, what):
    try:
        v = [int(math.floor(float(t))) for t in text.replace(" ", "").split(",")]
        assert len(v) == 4 and v[2] > v[0] and v[3] > v[1]
        return v
    except (ValueError, AssertionError):
        raise SystemExit(f"{what} wants x1,y1,x2,y2 with x1 < x2 and y1 < y2")


def snap(area, c):
    return [area[0] // c * c, area[1] // c * c, -(-area[2] // c) * c, -(-area[3] // c) * c]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save", help="save .zip (default: the most recent one)")
    ap.add_argument("--surface", default="nauvis")
    ap.add_argument("--png", metavar="FILE", help="write an overview image")
    ap.add_argument("--area", help="x1,y1,x2,y2 for --png (default: everything generated)")
    ap.add_argument("--scale", type=float, help="pixels per tile for --png: 1, 2, 3... or 0.5, 0.25... (default: fit in ~1500 px)")
    ap.add_argument("--no-labels", action="store_true", help="don't draw patch numbers on the image")
    ap.add_argument("--ascii", metavar="X1,Y1,X2,Y2", help="print a tile-level ASCII map of this area")
    ap.add_argument("--step", type=int, default=1, help="tiles per character for --ascii (default 1)")
    ap.add_argument("--json", metavar="FILE", help="write bounds, patches and nests as JSON")
    ap.add_argument("--report", action="store_true", help="print the text report even with --png/--ascii/--json")
    ap.add_argument("--top", type=int, default=25, help="patches listed in the report and numbered on the image (nearest first, default 25)")
    ap.add_argument("--new-map", action="store_true", help="survey a freshly created default map instead of a save")
    ap.add_argument("--seed", type=int, help="map seed for --new-map (default: random)")
    ap.add_argument("--radius", type=int, default=500, help="tiles around the spawn to generate for --new-map (default 500)")
    argv, i = [], 1   # "--area -700,-400,0,200": argparse would take the leading minus for an option
    while i < len(sys.argv):
        if sys.argv[i] in ("--area", "--ascii") and i + 1 < len(sys.argv):
            argv.append(sys.argv[i] + "=" + sys.argv[i + 1]); i += 2
        else:
            argv.append(sys.argv[i]); i += 1
    a = ap.parse_args(argv)

    grids = [{"cell": 8}]   # grid 0: whole map, for the report
    png_area = parse_area(a.area, "--area") if a.area else None
    asc_area = None
    if a.ascii:
        if a.step < 1:
            raise SystemExit("--step must be 1 or more")
        asc_area = snap(parse_area(a.ascii, "--ascii"), a.step)
        if (asc_area[2] - asc_area[0]) // a.step > 400:
            raise SystemExit(f"That is {(asc_area[2] - asc_area[0]) // a.step} characters wide; use a bigger --step or a narrower area (max 400).")
        grids.append({"cell": a.step, "area": asc_area})
    tmp_new = None
    if a.new_map:
        save, tmp_new = new_map(a.seed)
        title = "new map"
    else:
        save = a.save or fenv.latest_save()
        title = os.path.basename(save)
    try:
        px = cell = None
        if a.png and (png_area or a.scale):
            # the area and scale are known up front: one game run
            if a.scale:
                px, cell = (int(a.scale), 1) if a.scale >= 1 else (1, min((2, 4, 8, 16, 32), key=lambda c: abs(c - 1 / a.scale)))
            if png_area:
                if not a.scale:
                    px, cell = pick_scale(png_area)
                png_area = snap(png_area, cell)
                grids.append({"cell": cell, "area": png_area})
            else:
                grids.append({"cell": cell})
        elif a.png:
            grids.append({"cell": "auto", "target": 1500})
        gen = math.ceil(a.radius / 32) if a.new_map else None
        d = survey(save, a.surface, grids, gen)
        g0 = Grid(d["grids"][0])
        r = analyse(d, g0, a.new_map)
        if not r["generated"]:
            raise SystemExit(f"Nothing is generated on surface {a.surface}.")
        if a.new_map:
            title = f"new map seed {r['seed']}"
        out = []
        if a.report or not (a.png or a.ascii or a.json):
            out += report(r, f"{title}, surface {a.surface}", a.top, a.new_map)
        if a.ascii:
            out += ascii_map(Grid(d["grids"][1]), r, asc_area)
        if a.png:
            gp = Grid(d["grids"][-1])
            if png_area is None:   # whole map
                png_area = snap(r["generated"], gp.cell)
                if px is None:
                    px = pick_scale(png_area)[0] if gp.cell == 1 else 1
            out.append(render(gp, r, png_area, px, a.png, title, 0 if a.no_labels else a.top))
        if a.json:
            j = {k: v for k, v in r.items() if k not in ("chunks", "nest_points", "worm_points", "robo")}
            j["save"] = None if a.new_map else os.path.abspath(save)
            j["surface"] = a.surface
            j["roboports"] = [[r["robo"][i], r["robo"][i + 1]] for i in range(0, len(r["robo"]), 2)]
            with open(a.json, "w") as fh:
                json.dump(j, fh, indent=1)
            out.append(f"{a.json}: {len(r['patches'])} patches, {len(r['nests'])} nest clusters.")
        print("\n".join(out))
    finally:
        if tmp_new:
            shutil.rmtree(tmp_new, ignore_errors=True)


if __name__ == "__main__":
    main()
