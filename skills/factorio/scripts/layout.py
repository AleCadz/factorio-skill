#!/usr/bin/env python3
"""Build blueprints in code without coordinate mistakes, and attach the test that proves they work.

Writing a generator from scratch (centres, sizes, directions, poles, wires, which belt is fed with what)
is where most of the design time went. This library does those parts:

    import sys; sys.path.insert(0, "<skill>/scripts")
    from layout import Plan, N, E, S, W
    import blocks

    P = Plan()
    out = blocks.smelter(P, 0, 0, n=24, ore="iron-ore")        # tested, parametric pieces (see blocks.py)
    P.belt([(5, -1), (5, -6), (12, -6)], end=E)                 # straight runs between corner tiles
    P.route((20, 0), (30, 10), goal_dir=S)                      # A* belt router: turns + undergrounds
    P.power()                                                   # poles wherever something lacks power
    P.save("fundicion.txt", "Iron x24")                         # blueprint + .test.json, then tests it

Coordinates are TILES: (x, y) is the top-left tile an entity covers, y grows downwards. The library
works out the real size (from the game's data, rotated by direction), the centre position the blueprint
needs, collisions, copper wires and entity numbers. Directions: N=0 E=4 S=8 W=12 (2.0).

Each Plan also carries its test: feed(), sink(), fluid(), fill(), expect() and research() are written
next to the blueprint as <name>.test.json, which bp_sim.py --spec (and bp_make.py test) reads, so feeds
go to the right belts by entity, not by guessing the order of belt heads.
"""
import heapq, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bp as bptool  # noqa: E402
import fenv  # noqa: E402

N, E, S, W = 0, 4, 8, 12
V = {N: (0, -1), E: (1, 0), S: (0, 1), W: (-1, 0)}
LEFT = {N: W, W: S, S: E, E: N}
RIGHT = {N: E, E: S, S: W, W: N}
OPP = {N: S, S: N, E: W, W: E}
BELT_TYPES = ("transport-belt", "underground-belt", "splitter")

# ---------------------------------------------------------------------------------------------------------
# Prototype facts (sizes, speeds, reaches, recipes) from the game's data.raw, cached compactly.

_PROTOS = None
FALLBACK_SIZES = {"assembling-machine": 3, "furnace": 2, "lab": 3, "roboport": 4, "beacon": 3}


def protos():
    """{'entities': {name: {...}}, 'recipes': {name: {...}}, 'items': {...}} from data.raw (cached in TMPDIR)."""
    global _PROTOS
    if _PROTOS is not None:
        return _PROTOS
    import data
    path = os.path.join(data.cache_dir(), "layout-protos-2.json")
    if os.path.exists(path):
        _PROTOS = json.load(open(path))
        return _PROTOS
    d = data.dump()
    ents = {}
    for t, group in d.items():
        if t in data.NOT_ENTITY or t.endswith("-achievement"):
            continue
        for name, p in group.items():
            cb = p.get("collision_box")
            if not cb or not isinstance(cb, list):
                continue
            w = p.get("tile_width") or max(1, math.ceil(cb[1][0] - cb[0][0] - 0.01))
            h = p.get("tile_height") or max(1, math.ceil(cb[1][1] - cb[0][1] - 0.01))
            es = p.get("energy_source") or {}
            ent = {"type": t, "w": w, "h": h, "electric": es.get("type") == "electric"}
            for k in ("crafting_speed", "mining_speed", "supply_area_distance", "maximum_wire_distance",
                      "max_distance", "pickup_position", "insert_position", "researching_speed", "energy_usage",
                      "construction_radius", "logistics_radius", "resource_searching_radius"):
                if k in p:
                    ent[k] = p[k]
            if t in BELT_TYPES and "speed" in p:
                ent["items_per_s"] = round(p["speed"] * 480, 3)
            if t == "inserter":
                ent["rotation_speed"] = p.get("rotation_speed")
            if "crafting_categories" in p:
                ent["categories"] = p["crafting_categories"]
            if t == "pipe-to-ground":
                ent["max_distance"] = max((c.get("max_underground_distance", 0)
                                           for c in p.get("fluid_box", {}).get("pipe_connections", [])), default=10)
            ents[name] = ent
    recipes = {}
    for name, r in d.get("recipe", {}).items():
        cats = r.get("categories") or [r.get("category") or "crafting"]
        recipes[name] = {"categories": cats, "time": r.get("energy_required", 0.5),
                         "ingredients": [(i["name"], i["amount"], i.get("type", "item")) for i in r.get("ingredients", [])],
                         "results": [(i["name"], i.get("amount", 1), i.get("type", "item")) for i in r.get("results", [])],
                         "enabled": r.get("enabled", True),
                         "unlocked_by": data.unlocked_by(d, name)}
    items = {}
    for t in ("item", "item-with-entity-data", "ammo", "capsule", "module", "tool", "rail-planner"):
        for name, it in d.get(t, {}).items():
            items[name] = {"place_result": it.get("place_result"), "fuel_value": it.get("fuel_value")}
    _PROTOS = {"entities": ents, "recipes": recipes, "items": items}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(_PROTOS, open(path, "w"))
    return _PROTOS


def ent_info(name):
    p = protos()["entities"].get(name)
    if not p:
        raise ValueError(f"unknown entity '{name}' (data.py find {name})")
    return p


def size(name, d=N):
    p = ent_info(name)
    w, h = p["w"], p["h"]
    return (h, w) if d in (E, W) else (w, h)


def recipe(name):
    r = protos()["recipes"].get(name)
    if not r:
        raise ValueError(f"unknown recipe '{name}' (data.py find {name})")
    return r


def per_minute(recipe_name, machine):
    """Products per minute of one machine (no modules): {item: n}."""
    r = recipe(recipe_name)
    speed = ent_info(machine).get("crafting_speed", 1)
    return {n: 60 * a * speed / r["time"] for n, a, _ in r["results"]}


def consumes_per_minute(recipe_name, machine):
    r = recipe(recipe_name)
    speed = ent_info(machine).get("crafting_speed", 1)
    return {n: 60 * a * speed / r["time"] for n, a, _ in r["ingredients"]}


def smelting_recipe(ingredient):
    """The furnace recipe that takes `ingredient` (iron-ore -> iron-plate, iron-plate -> steel-plate...)."""
    for name, r in protos()["recipes"].items():
        if "smelting" in r["categories"] and any(i[0] == ingredient for i in r["ingredients"]):
            return name
    raise ValueError(f"no smelting recipe takes {ingredient}")


# ---------------------------------------------------------------------------------------------------------

class Collision(Exception):
    pass


class Plan:
    """Entities placed by top-left tile, with collision checks, routing, auto power and a test spec."""

    def __init__(self):
        self.ents = []
        self.occ = {}            # tile -> entity (or a placeholder for obstacles)
        self.fronts = set()      # tiles a belt points into: a new belt there would get side-loaded
        self.ug_lines = {}       # (axis, fixed coord) -> [(lo, hi)] spans already used by undergrounds
        self.ug_tiles = {"x": set(), "y": set()}
        self.ext_poles = []      # poles outside the plan (from the save) that already give power
        self.ext_roboports = []  # roboports of the save (construction coverage)
        self.ore = {}            # tile -> (resource name, amount), from obstacles_from_save
        self.known = []          # areas read from the save: spots are only chosen inside them
        self.circuit = []        # (entity a, entity b, colour): circuit wires, 1 = red, 2 = green
        self.uncharted = set()   # chunks (cx, cy) of the save the player never charted: no ghosts can go there
        self.base_box = None     # (x1, y1, x2, y2) of everything the player has built (from the save)
        self.save_path = None    # the save the obstacles came from (rails() plans on a copy of it)
        self.trains = []         # (locomotive entity, [wagon entities], schedule records)
        self.spec = {"feed": [], "sink": [], "fluid": [], "fill": [], "expect": {}, "research": [],
                     "minutes": 3}
        self.notes = []

    # ---- placing -------------------------------------------------------------------------------------
    def tiles_of(self, name, x, y, d=N):
        w, h = size(name, d)
        return [(x + i, y + j) for i in range(w) for j in range(h)]

    def free(self, x, y=None):
        t = x if y is None else (x, y)
        return t not in self.occ

    def at(self, x, y):
        return self.occ.get((x, y))

    def add(self, name, x, y, d=None, **kw):
        """Places `name` with its top-left tile at (x, y). Returns the entity dict (add fields to it freely)."""
        dd = N if d is None else d
        tiles = self.tiles_of(name, x, y, dd)
        clash = [t for t in tiles if t in self.occ]
        if clash:
            other = self.occ[clash[0]]
            raise Collision(f"{name} at tile {x},{y} overlaps {other['name']} at {clash[0]}")
        w, h = size(name, dd)
        e = {"name": name, "position": {"x": x + w / 2, "y": y + h / 2}}
        if d is not None:
            e["direction"] = d
        e.update(kw)
        e["_tile"] = (x, y)
        for t in tiles:
            self.occ[t] = e
        self.ents.append(e)
        t = ent_info(name)["type"]
        if t in BELT_TYPES and d is not None:
            v = V[d]
            if t == "splitter":
                for tx, ty in tiles:
                    self.fronts.add((tx + v[0], ty + v[1]))
            elif not (t == "underground-belt" and kw.get("type") == "input"):
                self.fronts.add((x + v[0], y + v[1]))
        return e

    def snapshot(self):
        """State to go back to with restore() when trying a spot that may not work out."""
        return (list(self.ents), dict(self.occ), set(self.fronts), {k: list(v) for k, v in self.ug_lines.items()},
                {k: set(v) for k, v in self.ug_tiles.items()}, json.loads(json.dumps(self.spec, default=id)),
                list(self.notes), list(self.spec["feed"]), list(self.spec["sink"]), list(self.spec["fluid"]),
                list(self.spec["fill"]))

    def restore(self, s):
        (self.ents, self.occ, self.fronts, self.ug_lines, self.ug_tiles, _, self.notes,
         feed, sink, fluid, fill) = (s[0], dict(s[1]), set(s[2]), {k: list(v) for k, v in s[3].items()},
                                     {k: set(v) for k, v in s[4].items()}, s[5], list(s[6]), s[7], s[8], s[9], s[10])
        self.ents = list(self.ents)
        self.spec["feed"], self.spec["sink"], self.spec["fluid"], self.spec["fill"] = list(feed), list(sink), list(fluid), list(fill)
        for k in ("expect", "lab_missing", "replaces", "external_targets"):
            if k in s[5]:
                self.spec[k] = s[5][k]
            else:
                self.spec.pop(k, None)

    def remove(self, e):
        self.ents.remove(e)
        for t, o in list(self.occ.items()):
            if o is e:
                del self.occ[t]

    def block(self, tiles, label="obstacle", real=None):
        """Marks tiles as taken by something not in the blueprint (terrain, the player's base...)."""
        dummy = {"name": label, "position": {"x": 0, "y": 0}, "_external": True, "_real": real or label}
        for t in tiles:
            self.occ.setdefault(tuple(t), dummy)

    def raw(self, name, x, y, tiles=None, **kw):
        """Places `name` by its blueprint POSITION (centre) for things add() can't size from a top-left tile:
        rails, signals, train stops, anything exported by the game. tiles = the tiles it takes (default: its
        size around the centre). Keys starting with '_' stay out of the blueprint. Rails may overlap rails."""
        e = {"name": name, "position": {"x": x, "y": y}}
        e.update(kw)
        if tiles is None:
            d = kw.get("direction", N)
            w, h = size(name, d if d in V else N)
            x0, y0 = math.floor(x - w / 2 + 0.01), math.floor(y - h / 2 + 0.01)
            tiles = [(x0 + i, y0 + j) for i in range(w) for j in range(h)]
        for t in tiles:
            o = self.occ.get(t)
            if o is not None and not (o.get("_rail") and kw.get("_rail")):
                raise Collision(f"{name} at {x},{y} overlaps {o['name']} at {t}")
        for t in tiles:
            self.occ[t] = e
        self.ents.append(e)
        return e

    def rails(self, start, cmds, save=None, allow_blocked=False):
        """Lays rails with the game's own rail planner on a copy of the save (scripts/rails.py) and adds them.
        start = (x, y, direction) of the first straight rail (odd coordinates; 0 = heading north, 4 east...);
        cmds = 'S20 ! R4 S10 L8 @mark': S<n> straight pieces (2 tiles), L<n>/R<n> turn steps of 22.5 degrees (R4 =
        90 degrees, 13 tiles forward and 13 sideways; L8 = a U-turn 26 tiles wide), ! a rail signal on the
        right-hand side (one-way track), !c a chain signal, @name remembers the spot. Returns the trace
        (marks, closed, blocked). Raises if water, cliffs or buildings are in the way (reroute)."""
        import rails as railtool
        res = railtool.trace(start, cmds, save=save or self.save_path, quiet=True)
        if not res:
            raise RuntimeError("the rail planner did not run (see rails.py)")
        if res["blocked"] and not allow_blocked:
            raise Collision(f"rails blocked by terrain or buildings at {res['blocked'][:6]}")
        for r in res["rails"]:
            l, t, rr, b = r["bb"]
            tiles = [tuple(t_) for t_ in r["tiles"]] if r.get("tiles") else [
                (tx, ty) for tx in range(math.floor(l + 0.01), math.ceil(rr - 0.01))
                for ty in range(math.floor(t + 0.01), math.ceil(b - 0.01))]
            self.raw(r["name"], r["x"], r["y"], tiles, _rail=True, **({"direction": r["dir"]} if r["dir"] else {}))
        for sg in res["signals"] or []:
            self.raw(sg["name"], sg["x"], sg["y"], [(math.floor(sg["x"]), math.floor(sg["y"]))], direction=sg["dir"])
        return res

    def train(self, cars, schedule, fuel=("coal", 20)):
        """Adds rolling stock to the blueprint. cars = [(name, x, y, orientation)] from the front (0 = heading
        north, 0.25 east, 0.5 south, 0.75 west; centres 7 tiles apart), schedule = [(station, wait)] with wait
        'full', 'empty' or a wait_conditions list. Locomotives carry a fuel request that robots deliver; a
        robot-built train switches to automatic by itself."""
        ents = []
        for name, x, y, o in cars:
            e = {"name": name, "position": {"x": x, "y": y}, "orientation": o}
            if ent_info(name)["type"] == "locomotive" and fuel:
                e["items"] = [{"id": {"name": fuel[0]}, "items": {"in_inventory": [
                    {"inventory": 1, "stack": 0, "count": fuel[1]}]}}]
            self.ents.append(e)
            ents.append(e)
        recs = [{"station": st, "wait_conditions": ([{"type": w, "compare_type": "or"}] if isinstance(w, str) else w)}
                for st, w in schedule]
        self.trains.append((ents, recs))
        return ents

    def block_blueprint(self, obj, dx=None, dy=None):
        """Treats a blueprint that is (or will be) pasted in the world as part of the base: its tiles are taken,
        its belts keep their fronts, its poles and roboports count for power() and roboports(). obj = decoded
        dict or a file path. dx, dy = world position of blueprint coordinate (0, 0); for a blueprint with
        absolute snapping they default to the grid cell that contains the area read from the save."""
        if isinstance(obj, str):
            obj = bptool.decode(open(obj).read().strip())
        b = obj.get("blueprint", obj)
        if dx is None:
            if not b.get("absolute-snapping"):
                raise ValueError("give dx, dy: this blueprint has no absolute snapping")
            g = b["snap-to-grid"]
            rel = b.get("position-relative-to-grid", {"x": 0, "y": 0})
            k = self.known[0] if self.known else (0, 0, 0, 0)
            cx, cy = (k[0] + k[2]) / 2, (k[1] + k[3]) / 2
            dx = rel["x"] + g["x"] * math.floor((cx - rel["x"]) / g["x"])
            dy = rel["y"] + g["y"] * math.floor((cy - rel["y"]) / g["y"])
        for e in b["entities"]:
            name = e["name"]
            typ = ent_info(name)["type"]
            if typ in ("locomotive", "cargo-wagon", "fluid-wagon", "artillery-wagon"):
                continue
            x, y = e["position"]["x"] + dx, e["position"]["y"] + dy
            d = e.get("direction", 0)
            if typ.startswith("curved-rail") or typ == "half-diagonal-rail":
                w = h = 8                      # generous: their real shape is diagonal
            else:
                w, h = size(name, d if d in V else N)
            x0, y0 = math.floor(x - w / 2 + 0.01), math.floor(y - h / 2 + 0.01)
            tiles = [(x0 + i, y0 + j) for i in range(w) for j in range(h)]
            self.block(tiles, label=name, real=name)
            if typ in ("transport-belt", "splitter") and d in V:
                for tx, ty in tiles:
                    self.fronts.add((tx + V[d][0], ty + V[d][1]))
            if typ == "underground-belt" and d in V:
                self.ug_tiles["x" if d in (E, W) else "y"].add(tiles[0])
                if e.get("type") != "input":
                    self.fronts.add((tiles[0][0] + V[d][0], tiles[0][1] + V[d][1]))
            if typ == "electric-pole":
                self.ext_poles.append({"name": name, "position": {"x": x, "y": y}})
            if typ == "roboport":
                self.ext_roboports.append({"name": name, "position": {"x": x, "y": y}})
        return dx, dy

    def bounds(self, pad=0):
        xs = [t[0] for t, e in self.occ.items() if not e.get("_external")]
        ys = [t[1] for t, e in self.occ.items() if not e.get("_external")]
        return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)

    # ---- belts ---------------------------------------------------------------------------------------
    def belt(self, points, end=None, kind="transport-belt"):
        """Belt through corner tiles [(x0,y0), (x1,y1), ...] (axis-aligned runs). Each tile faces the next
        one; the last tile faces `end` (default: the direction of the last run). Returns the entities."""
        pts = [tuple(p) for p in points]
        if len(pts) == 1:
            if end is None:
                raise ValueError("a one-tile belt needs end=direction")
            return [self.add(kind, pts[0][0], pts[0][1], end)]
        tiles = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            if a[0] != b[0] and a[1] != b[1]:
                raise ValueError(f"belt run {a}->{b} is not straight")
            dx = (b[0] > a[0]) - (b[0] < a[0])
            dy = (b[1] > a[1]) - (b[1] < a[1])
            t = a
            while t != b:
                t = (t[0] + dx, t[1] + dy)
                tiles.append(t)
        out = []
        for i, t in enumerate(tiles):
            if i + 1 < len(tiles):
                nx = tiles[i + 1]
                d = {(0, -1): N, (1, 0): E, (0, 1): S, (-1, 0): W}[(nx[0] - t[0], nx[1] - t[1])]
            else:
                pv = tiles[i - 1]
                d = end if end is not None else {(0, -1): N, (1, 0): E, (0, 1): S, (-1, 0): W}[(t[0] - pv[0], t[1] - pv[1])]
            out.append(self.add(kind, t[0], t[1], d))
        return out

    def underground(self, x, y, d, length, kind="underground-belt"):
        """Entrance at (x, y) facing d and exit `length` tiles further along d."""
        md = ent_info(kind).get("max_distance", 5)
        if length > md:
            raise ValueError(f"{kind} reaches {md} tiles, asked {length}")
        v = V[d]
        a = self.add(kind, x, y, d, type="input")
        b = self.add(kind, x + v[0] * length, y + v[1] * length, d, type="output")
        self._note_ug((x, y), (x + v[0] * length, y + v[1] * length), d)
        return a, b

    def _note_ug(self, a, b, d):
        axis = "x" if d in (E, W) else "y"
        fixed = a[1] if axis == "x" else a[0]
        lo, hi = sorted((a[0], b[0]) if axis == "x" else (a[1], b[1]))
        self.ug_lines.setdefault((axis, fixed), []).append((lo, hi))
        self.ug_tiles[axis].update({a, b})

    def splitter(self, x, y, d, **kw):
        """Splitter whose two lanes start at tile (x, y): facing N/S it covers x..x+1, facing E/W y..y+1.
        output_priority / input_priority = 'left'|'right' as seen in the direction of travel."""
        return self.add(kind_or(kw.pop("kind", None), "splitter"), x, y, d, **kw)

    def route(self, start, goal, goal_dir, start_dir=None, kind="transport-belt", bounds=None, **kw):
        """A* belt from tile `start` to tile `goal` (the last belt faces `goal_dir`), going around anything
        placed and under obstacles with undergrounds. It never ends a belt pointing into another one by
        accident. start_dir: allowed direction(s) of the first tile. Returns the entities."""
        ug = kind.replace("transport-belt", "underground-belt")
        md = ent_info(ug).get("max_distance", 5)
        blocked = set(self.occ) - {start, goal}
        if bounds is None and len(self.known) == 1:
            bounds = self.known[0]
        pcs = _route(blocked, start, goal, goal_dir, fronts=self.fronts - {start, goal}, ug_lines=self.ug_lines,
                     start_dir=start_dir, bounds=bounds, max_ug=md, max_fast=md, ug_tiles=self.ug_tiles, **kw)
        out, entrance = [], None
        for k, x, y, d in pcs:
            if k == "belt":
                out.append(self.add(kind, x, y, d))
            else:
                io = k.split(":")[1]
                out.append(self.add(ug, x, y, d, type=io))
                if io == "input":
                    entrance = (x, y)
                else:
                    self._note_ug(entrance, (x, y), d)
        return out

    def wire(self, a, b, colour=1):
        """Circuit wire between two entities (1 = red, 2 = green), e.g. a chest and an inserter whose
        control_behavior={"circuit_enabled": True, "circuit_condition": {"first_signal": {"name": item},
        "constant": n, "comparator": ">"}} reads it (format verified with bp_probe)."""
        self.circuit.append((a, b, colour))

    def link(self, src, dst, kind="transport-belt", bounds=None, priority=None):
        """Belt from the output belt `src` (an entity, e.g. a block's 'output') into the input belt(s) `dst`
        (an entity or a list of two: a splitter right after src shares it between them; priority='left' or
        'right' as seen along the travel direction). The test stops feeding dst and emptying src."""
        dsts = dst if isinstance(dst, (list, tuple)) else [dst]
        d0 = src.get("direction", N)
        sx, sy = src["_tile"]
        start = (sx + V[d0][0], sy + V[d0][1])
        made = []
        if len(dsts) == 1:
            made += self._link_one(start, dsts[0], kind, bounds, start_dir=None)
        elif len(dsts) == 2:
            sp = kind.replace("transport-belt", "splitter")
            side = RIGHT[d0]
            placed = None
            for off in (0, -1):        # the splitter covers the start tile and its right or left neighbour
                ox, oy = start[0] + V[side][0] * off, start[1] + V[side][1] * off
                try:
                    kw = {"output_priority": priority} if priority else {}
                    placed = self.add(sp, min(ox, ox + V[side][0]), min(oy, oy + V[side][1]), d0, **kw)
                    break
                except Collision:
                    continue
            if placed is None:
                raise Collision(f"no room for a splitter in front of {src['name']} at {src['_tile']}")
            tiles = sorted(t for t, e in self.occ.items() if e is placed)
            outs = [(t[0] + V[d0][0], t[1] + V[d0][1]) for t in tiles]
            # left output (seen along d0) feeds dsts[0]
            if d0 in (S, W):
                outs.reverse()
            for o, dd in zip(outs, dsts):
                made += self._link_one(o, dd, kind, bounds, start_dir=None)
            made.append(placed)
        else:
            raise ValueError("link to one or two inputs (chain more splitters by hand)")
        self.spec["sink"] = [e for e in self.spec["sink"] if e is not src]
        self.spec["feed"] = [f for f in self.spec["feed"] if not any(f[0] is d for d in dsts)]
        return made

    def _link_one(self, start, dst, kind, bounds, start_dir):
        dd = dst.get("direction", N)
        dx, dy = dst["_tile"]
        goal = (dx - V[dd][0], dy - V[dd][1])
        return self.route(start, goal, dd, start_dir=start_dir, kind=kind, bounds=bounds)

    def unexpect(self, item):
        """Drop an expected rate (an intermediate product that now goes into another block)."""
        self.spec["expect"].pop(item, None)

    # ---- power ---------------------------------------------------------------------------------------
    def poles(self):
        return [e for e in self.ents if ent_info(e["name"])["type"] == "electric-pole"]

    def unpowered(self):
        poles = self.poles() + self.ext_poles
        return [e for e in self.ents if ent_info(e["name"])["electric"] and not any(_covers(p, e) for p in poles)]

    def power(self, pole="small-electric-pole", bounds=None):
        """Adds poles (greedy cover of whatever needs power, then joins them into one network). Returns the
        number added. Blocks place their own poles; use this for the glue between them."""
        info = ent_info(pole)
        self._pole = pole
        r, reach = info["supply_area_distance"], info["maximum_wire_distance"]
        need = self.unpowered()
        if bounds:      # a block powering itself must not fail over something elsewhere in the plan
            need = [e for e in need if bounds[0] - 1 <= e["position"]["x"] <= bounds[2] + 2
                    and bounds[1] - 1 <= e["position"]["y"] <= bounds[3] + 2]
        added = 0
        if need:
            bx = bounds or self.bounds(pad=1)
            byid = {id(e): e for e in need}
            left = set(byid)
            # candidate tiles = those within the pole's supply area of something that needs power (built from
            # the entities outwards: scoring every free tile of a big plan against every pole took a minute)
            cover = {}
            for i, e in byid.items():
                w, h = size(e["name"], e.get("direction", N))
                ex, ey = _xy(e)
                for x in range(math.floor(ex - w / 2 - r - 1), math.ceil(ex + w / 2 + r) + 1):
                    for y in range(math.floor(ey - h / 2 - r - 1), math.ceil(ey + h / 2 + r) + 1):
                        if (x, y) in self.occ or not (bx[0] <= x <= bx[2] and bx[1] <= y <= bx[3]):
                            continue
                        if _covers({"name": pole, "position": {"x": x + 0.5, "y": y + 0.5}}, e, r):
                            cover.setdefault((x, y), set()).add(i)
            cell = 34
            grid = {}

            def note(p):
                grid.setdefault((int(p[0] // cell), int(p[1] // cell)), []).append(p)

            def near(c):
                gx, gy = int(c[0] // cell), int(c[1] // cell)
                return any(math.dist(c, q) <= reach for i in (gx - 1, gx, gx + 1) for j in (gy - 1, gy, gy + 1)
                           for q in grid.get((i, j), ()))
            for p in self.poles() + self.ext_poles:
                note(_xy(p))
            while left:
                best, bkey = None, None
                for c, cov in cover.items():
                    n = len(cov & left)
                    if not n or c in self.occ:
                        continue
                    key = (near((c[0] + 0.5, c[1] + 0.5)), n)
                    if bkey is None or key > bkey:
                        best, bkey = c, key
                if best is None:
                    raise Collision(f"no free tile can power {[byid[i]['name'] for i in left][:5]}")
                self.add(pole, *best)
                note((best[0] + 0.5, best[1] + 0.5))
                added += 1
                left -= cover[best]
        added += self._join_poles(pole, bounds)
        return added

    def _join_poles(self, pole, bounds):
        """Every group of new poles must touch the existing grid (or, with no existing grid, each other).
        Existing groups are never joined to each other: they may already be connected outside the area."""
        reach = ent_info(pole)["maximum_wire_distance"]
        added = 0
        ext = {id(p) for p in self.ext_poles}
        skip = set()

        def near_bounds(p, pad):
            x, y = _xy(p)
            return bounds[0] - pad <= x <= bounds[2] + 1 + pad and bounds[1] - pad <= y <= bounds[3] + 1 + pad
        for _ in range(300):
            comps = _components(self.poles() + self.ext_poles)
            mine = [c for c in comps if any(id(p) not in ext for p in c)]
            if ext:
                lonely = [c for c in mine if not any(id(p) in ext for p in c)]
            else:
                lonely = mine if len(mine) > 1 else []
            if bounds:
                # a block powering itself joins only its own poles (and a grid right next to it); the long
                # link to the rest is left to the final power() of the plan, or blocks far from the base would
                # each lay a line of poles back to it
                lonely = [c for c in lonely if any(id(p) not in ext and near_bounds(p, 0) for p in c)]
            lonely = [c for c in lonely if id(c[0]) not in skip]
            if not lonely:
                return added
            ca = lonely[0]
            a_best = None
            for cb in comps:
                if cb is ca:
                    continue
                for b in cb:
                    if bounds and not near_bounds(b, 2 * reach):
                        continue
                    for a in ca:
                        if id(a) in ext:
                            continue
                        dd = math.dist(_xy(a), _xy(b))
                        if a_best is None or dd < a_best[0]:
                            a_best = (dd, a, b)
            if a_best is None:
                skip.add(id(ca[0]))
                continue
            _, a, b = a_best
            ax, ay = _xy(a)
            r_ = int(reach) + 1
            bx_ = (math.floor(min(ax, _xy(b)[0])) - r_, math.floor(min(ay, _xy(b)[1])) - r_,
                   math.floor(max(ax, _xy(b)[0])) + r_, math.floor(max(ay, _xy(b)[1])) + r_)
            r2 = min(reach, ent_info(a["name"])["maximum_wire_distance"])
            best = None
            for x in range(bx_[0], bx_[2] + 1):
                for y in range(bx_[1], bx_[3] + 1):
                    if (x, y) in self.occ:
                        continue
                    c = (x + 0.5, y + 0.5)
                    if math.dist(c, (ax, ay)) <= r2:
                        dd = math.dist(c, _xy(b))
                        if best is None or dd < best[0]:
                            best = (dd, (x, y))
            if best is None:
                raise Collision(f"cannot join the pole at {ax},{ay} to the rest")
            self.add(pole, *best[1])
            added += 1
        raise Collision("could not join the electric networks")

    # ---- fluids --------------------------------------------------------------------------------------
    def pipe_route(self, start, goal, bounds=None, kind="pipe"):
        """Pipe from tile `start` to tile `goal` (both included), with pipe-to-ground under obstacles. It
        keeps away from other pipes and fluid machines (pipes join anything next to them, mixing fluids),
        except at the two ends. Returns the entities."""
        ptg = "pipe-to-ground"
        md = ent_info(ptg).get("max_distance", 10)
        fluidish = {"pipe", "pipe-to-ground", "pump", "storage-tank", "offshore-pump", "assembling-machine",
                    "furnace", "boiler", "generator", "mining-drill", "fluid-turret"}
        near = set()
        for tile, e in self.occ.items():
            typ = protos()["entities"].get(e.get("_real", e["name"]), {}).get("type")
            if typ in fluidish:
                for v in V.values():
                    near.add((tile[0] + v[0], tile[1] + v[1]))
        ends = {start, goal}
        for t0 in (start, goal):
            for v in V.values():
                ends.add((t0[0] + v[0], t0[1] + v[1]))
        blocked = (set(self.occ) | (near - ends)) - {start, goal}
        # both ends must be plain pipes: an underground end only connects on one side, which may not face
        # the pump or the machine (that is how a route once came out disconnected)
        pcs = _route(blocked, start, goal, None, bounds=bounds, max_ug=md, max_fast=md, turn_cost=0.0,
                     ug_cost=4.0, any_dir=True, plain_ends=True)
        out = []
        for k, x, y, d in pcs:
            if k == "belt":
                out.append(self.add(kind, x, y))
            elif k.endswith("input"):
                out.append(self.add(ptg, x, y, OPP[d]))      # its above-ground side faces back along the run
            else:
                out.append(self.add(ptg, x, y, d))
        return out

    def replace(self, x, y, name, **kw):
        """Puts `name` where the save has another entity of the same size (steel chest -> passive provider,
        belt -> splitter...): paste with Ctrl+Shift+click. The save test accepts it being in the way."""
        w, h = size(name, kw.get("direction", N))
        for i in range(w):
            for j in range(h):
                if self.occ.get((x + i, y + j), {}).get("_external"):
                    del self.occ[(x + i, y + j)]
        e = self.add(name, x, y, kw.pop("direction", None), **kw)
        self.spec.setdefault("replaces", []).append([e["position"]["x"], e["position"]["y"]])
        return e

    def attach_chest(self, box, chest="requester-chest", inserter="fast-inserter", into=True, sides=(W, E, N, S),
                     **chest_kw):
        """Puts a chest + inserter against a machine whose tiles are box = (x0, y0, w, h) (e.g. one of the
        player's assemblers): into=True the inserter feeds the machine from the chest, False takes from the
        machine into the chest. Tries every side and position, then a long-handed inserter with the chest one
        tile further (over a belt); returns (chest, inserter) or raises."""
        x0, y0, w, h = box
        for sd in sides:
            v = V[sd]
            if sd in (W, E):
                spots = [(x0 - 1 if sd == W else x0 + w, y0 + j) for j in range(h)]
            else:
                spots = [(x0 + i, y0 - 1 if sd == N else y0 + h) for i in range(w)]
            for reach, ins in ((1, inserter), (2, "long-handed-inserter")):
                if reach == 2 and min(w, h) < 2:
                    continue
                for ix, iy in spots:
                    # a long-handed inserter reaches over one tile (a belt, say) to a chest behind it
                    cx, cy = ix + v[0] * reach, iy + v[1] * reach
                    if (ix, iy) in self.occ or (cx, cy) in self.occ:
                        continue
                    d = sd if into else OPP[sd]       # inserters face where they pick up from
                    c = self.add(chest, cx, cy, **chest_kw)
                    i = self.add(ins, ix, iy, d)
                    if any(self.occ.get(t_, {}).get("_external") for t_ in [(x0, y0)]):
                        self.spec["external_targets"] = self.spec.get("external_targets", 0) + 1
                    return c, i
        raise Collision(f"no free side on the machine at {box[:2]} for a chest and an inserter (also tried long-handed)")

    def stamp(self, obj, x, y):
        """Places a whole blueprint (decoded dict, e.g. a tested module) with its top-left tile at (x, y).
        Collisions are checked like add(). Returns {original entity_number: new entity}."""
        bpd = obj.get("blueprint", obj)
        boxes = []
        for e in bpd["entities"]:
            w, h = size(e["name"], e.get("direction", N))
            boxes.append((e, math.floor(e["position"]["x"] - w / 2 + 0.01), math.floor(e["position"]["y"] - h / 2 + 0.01)))
        mx = min(b[1] for b in boxes)
        my = min(b[2] for b in boxes)
        out = {}
        for e, tx, ty in boxes:
            kw = {k: v for k, v in e.items() if k not in ("entity_number", "position", "name", "direction")}
            out[e["entity_number"]] = self.add(e["name"], tx - mx + x, ty - my + y, e.get("direction"), **kw)
        return out

    def footprint(self, obj):
        """(w, h) in tiles of a blueprint, for find_free before stamp()."""
        bpd = obj.get("blueprint", obj)
        xs, ys = [], []
        for e in bpd["entities"]:
            w, h = size(e["name"], e.get("direction", N))
            x0, y0 = e["position"]["x"] - w / 2, e["position"]["y"] - h / 2
            xs += [x0, x0 + w]
            ys += [y0, y0 + h]
        return round(max(xs) - min(xs)), round(max(ys) - min(ys))

    # ---- robots ----------------------------------------------------------------------------------------
    def robot_uncovered(self):
        """Plan entities robots can't build (outside every construction area) plus logistic chests outside
        every logistic area (the save's roboports and the plan's)."""
        allp = self.ext_roboports + [e for e in self.ents if not e.get("_external")
                                     and ent_info(e["name"])["type"] == "roboport"]
        out = []
        for e in self.ents:
            if e.get("_external"):
                continue
            key = "logistics_radius" if ent_info(e["name"])["type"] == "logistic-container" else "construction_radius"
            if not any(abs(_xy(e)[0] - _xy(r)[0]) <= ent_info(r["name"]).get(key, 25) and
                       abs(_xy(e)[1] - _xy(r)[1]) <= ent_info(r["name"]).get(key, 25) for r in allp):
                out.append(e)
        return out

    def roboports(self, name="roboport", bounds=None):
        """Adds roboports until robots can build every entity of the plan. Each new one is placed where its
        logistic area touches an existing one (so they form one network). Returns how many were added."""
        added = 0
        lr = ent_info(name).get("logistics_radius", 25)
        for _ in range(20):
            todo = self.robot_uncovered()
            if not todo:
                if added and self.unpowered():
                    # a roboport added after the plan's power() would sit there without a pole
                    self.power(getattr(self, "_pole", "medium-electric-pole"))
                return added
            ports = [_xy(r) for r in self.ext_roboports] + [_xy(e) for e in self.ents if not e.get("_external")
                                                            and ent_info(e["name"])["type"] == "roboport"]
            if not ports:
                raise Collision("no roboport in the save or the plan to extend from")
            tx, ty = _xy(todo[0])
            best = None
            bx = bounds or (int(tx) - 60, int(ty) - 60, int(tx) + 60, int(ty) + 60)
            need = ("logistics_radius" if ent_info(todo[0]["name"])["type"] == "logistic-container"
                    else "construction_radius")
            r_need = ent_info(name).get(need, 25)
            for x in range(bx[0], bx[2] + 1):
                for y in range(bx[1], bx[3] + 1):
                    c = (x + 2, y + 2)
                    if abs(c[0] - tx) > r_need or abs(c[1] - ty) > r_need:
                        continue
                    if not any(abs(c[0] - p[0]) <= 2 * lr and abs(c[1] - p[1]) <= 2 * lr for p in ports):
                        continue
                    if any((x + i, y + j) in self.occ for i in range(4) for j in range(4)):
                        continue
                    dd = math.dist(c, (tx, ty))
                    if best is None or dd < best[0]:
                        best = (dd, (x, y))
            if best is None:
                raise Collision(f"no spot for a roboport that reaches {todo[0]['name']} at {_xy(todo[0])}")
            self.add(name, *best[1])
            added += 1
        raise Collision("too many roboports needed")

    def _known(self, x1, y1, x2, y2):
        """True if the rectangle lies in an area read from the save (or no save was read)."""
        return not self.known or any(a[0] <= x1 and a[1] <= y1 and x2 <= a[2] and y2 <= a[3] for a in self.known)

    def find_free(self, w, h, near, margin=1, radius=80, avoid_ore=False):
        """Top-left tile of the free w×h rectangle (plus `margin` free tiles around it) closest to `near`.
        avoid_ore: don't build on ore (keep patches for drills)."""
        nx, ny = near
        best = None
        for r in range(0, radius + 1):
            ring = [(x, ny - r) for x in range(nx - r, nx + r + 1)] + [(x, ny + r) for x in range(nx - r, nx + r + 1)]
            ring += [(nx - r, y) for y in range(ny - r + 1, ny + r)] + [(nx + r, y) for y in range(ny - r + 1, ny + r)]
            for (x, y) in (ring if r else [(nx, ny)]):
                if not self._known(x - margin, y - margin, x + w + margin - 1, y + h + margin - 1):
                    continue
                if self.uncharted and any((cx_ // 32, cy_ // 32) in self.uncharted
                                          for cx_ in (x, x + w - 1) for cy_ in (y, y + h - 1)):
                    continue
                if all((x + i, y + j) not in self.occ and not (avoid_ore and (x + i, y + j) in self.ore)
                       for i in range(-margin, w + margin) for j in range(-margin, h + margin)):
                    d = math.dist((x + w / 2, y + h / 2), near)
                    if best is None or d < best[0]:
                        best = (d, (x, y))
            if best and r > best[0] + max(w, h):
                break
        if best is None:
            raise Collision(f"no free {w}x{h} spot within {radius} tiles of {near}")
        return best[1]

    # ---- ore -------------------------------------------------------------------------------------------
    def find_ore(self, ore, w, h, near=None, min_fill=0.6):
        """Top-left tile of a free w×h rectangle whose tiles are mostly `ore` (after obstacles_from_save).
        Picks the richest; with near=(x, y) the closest of those at least min_fill full."""
        tiles = {t for t, (n, _) in self.ore.items() if n == ore}
        if not tiles:
            raise ValueError(f"no {ore} in the area read from the save")
        xs = [t[0] for t in tiles]
        ys = [t[1] for t in tiles]
        best = None
        for x in range(min(xs) - w + 1, max(xs) + 1):
            for y in range(min(ys) - h + 1, max(ys) + 1):
                cnt = sum((x + i, y + j) in tiles for i in range(w) for j in range(h))
                if cnt < min_fill * w * h:
                    continue
                if any((x + i, y + j) in self.occ for i in range(w) for j in range(h)):
                    continue
                if not self._known(x, y, x + w - 1, y + h - 1):
                    continue
                key = (-math.dist((x, y), near), cnt) if near else (cnt, 0)
                if best is None or key > best[0]:
                    best = (key, (x, y))
        if best is None:
            raise ValueError(f"no free {w}x{h} spot at least {min_fill:.0%} on {ore}")
        return best[1]

    def in_uncharted(self):
        """Plan entities inside chunks of the save that were never charted (ghosts can't be placed there)."""
        if not self.uncharted:
            return []
        return [e for e in self.ents if not e.get("_external")
                and (math.floor(e["position"]["x"] / 32), math.floor(e["position"]["y"] / 32)) in self.uncharted]

    # ---- the test that goes with it -------------------------------------------------------------------
    def feed(self, e, lanes):
        """The belt entity `e` (an input head) gets 'left|right' lanes, e.g. 'iron-ore|coal' or 'iron-plate'."""
        self.spec["feed"].append((e, lanes))

    def sink(self, e):
        """The belt entity `e` is an output: the test empties it (every other tail backs up as in game)."""
        self.spec["sink"].append(e)

    def fluid(self, e, name):
        """The 1x1 pipe `e` becomes an infinite source of fluid `name` in the test."""
        self.spec["fluid"].append((e, name))

    def fill(self, e, item):
        self.spec["fill"].append((e, item))

    def expect(self, item, per_min):
        """The test passes only if at least 90 % of this rate leaves through the outputs/chests."""
        self.spec["expect"][item] = self.spec["expect"].get(item, 0) + per_min

    def research(self, *techs):
        self.spec["research"] += list(techs)

    # ---- output ------------------------------------------------------------------------------------------
    def blueprint(self, label, description="", icons=None, absolute=False, version=None):
        ents = [e for e in self.ents if not e.get("_external")]
        out, num = [], {}
        for i, e in enumerate(ents, 1):
            num[id(e)] = i
            c = {k: v for k, v in e.items() if not k.startswith("_")}
            c["entity_number"] = i
            c["position"] = dict(e["position"])
            out.append(c)
        if icons is None:
            icons = [_main_icon(self)]
        b = {"item": "blueprint", "label": label,
             "icons": [{"signal": {"type": "item", "name": n}, "index": i + 1} for i, n in enumerate(icons[:4])],
             "entities": out, "wires": _wires(ents, num) + [[num[id(a)], c, num[id(b_)], c] for a, b_, c in self.circuit
                                                               if id(a) in num and id(b_) in num],
             "version": version or _version()}
        if description:
            b["description"] = description
        if self.trains:
            b["schedules"] = [{"locomotives": [num[id(c)] for c in cars if ent_info(c["name"])["type"] == "locomotive"],
                               "schedule": {"records": recs}} for cars, recs in self.trains]
            sc = []
            for cars, _ in self.trains:
                for i, c in enumerate(cars):
                    d = {"stock": num[id(c)]}
                    if i > 0:
                        d["front"] = num[id(cars[i - 1])]
                    if i < len(cars) - 1:
                        d["back"] = num[id(cars[i + 1])]
                    sc.append(d)
            b["stock_connections"] = sc
        if absolute:
            # Verified in game (2.1): with absolute snapping the blueprint lands in the grid cell that contains the
            # CURSOR, at cell origin + its positions; cells start at position-relative-to-grid + k*grid. A cursor in
            # the neighbouring cell pastes everything one grid away (it happened twice: a phantom copy 512 tiles
            # south). So the cell is made big enough to hold the blueprint AND everything the player has built:
            # wherever in their base the cursor is, it lands in the same place.
            xs = [e["position"]["x"] for e in out]
            ys = [e["position"]["y"] for e in out]
            x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
            if self.base_box:
                x1, y1 = min(x1, self.base_box[0]), min(y1, self.base_box[1])
                x2, y2 = max(x2, self.base_box[2]), max(y2, self.base_box[3])
            grid = 512
            while grid < max(x2 - x1, y2 - y1) + 96:
                grid *= 2
            ox = math.floor((x1 + x2) / 2) - grid // 2
            oy = math.floor((y1 + y2) / 2) - grid // 2
            for e in out:
                e["position"] = {"x": e["position"]["x"] - ox, "y": e["position"]["y"] - oy}
            b["snap-to-grid"] = {"x": grid, "y": grid}
            b["absolute-snapping"] = True
            b["position-relative-to-grid"] = {"x": ox % grid, "y": oy % grid}
            self.cursor = (math.floor((min(xs) + max(xs)) / 2), math.floor((min(ys) + max(ys)) / 2))
            self.grid_origin, self.grid = (ox, oy), grid
            where = ("anywhere on the base or on the spot" if self.base_box else "on the spot where it goes")
            self.notes.append(f"absolute ({grid}-tile cell): put the cursor {where}, e.g. on {self.cursor}; any point in "
                              f"x {ox}..{ox + grid - 1}, y {oy}..{oy + grid - 1} lands it in the same place. Do not rotate.")
        return {"blueprint": b}, num

    def test_spec(self, num):
        sp = self.spec
        return {"feed": {str(num[id(e)]): lanes for e, lanes in sp["feed"]},
                "sink": [num[id(e)] for e in sp["sink"]],
                "fluid": {str(num[id(e)]): f for e, f in sp["fluid"]},
                "fill": {str(num[id(e)]): i for e, i in sp["fill"]},
                "expect": sp["expect"], "research": sp["research"] or ["all"], "minutes": sp["minutes"],
                "lab_missing": sp.get("lab_missing", 0), "replaces": sp.get("replaces", []),
                "external_targets": sp.get("external_targets", 0)}

    def save(self, path, label, description="", icons=None, absolute=False, test=True, quiet=False):
        """Writes the blueprint string to `path` and its test to `path` minus .txt + .test.json. With
        test=True runs bp_sim on it and prints the verdict. Returns True when the test passed (or was skipped)."""
        obj, num = self.blueprint(label, description, icons, absolute)
        with open(path, "w") as f:
            f.write(bptool.encode(obj) + "\n")
        dark = self.in_uncharted()
        if dark:
            # the game puts no ghosts in chunks the player never charted (found out with a rail loop whose
            # northern third silently stayed unbuilt)
            print(f"FAIL: {len(dark)} entities lie in map the player never explored (e.g. {dark[0]['name']} at "
                  f"{_xy(dark[0])}): the game would skip them. Keep the plan inside the charted map, or have them "
                  f"build a radar nearby first and paste again.")
            return False
        spec = self.test_spec(num)
        if absolute:
            first = next(e for e in self.ents if not e.get("_external"))
            spec["align"] = f"1={first['position']['x']},{first['position']['y']}"
        with open(spec_path(path), "w") as f:
            json.dump(spec, f, indent=1)
        if not quiet:
            print(f"saved {path} ({len(obj['blueprint']['entities'])} entities) + {os.path.basename(spec_path(path))}")
            for n in self.notes:
                print("note:", n)
        if test:
            import bp_make
            return bp_make.test(path, on_save=absolute)
        return True


def request(*pairs):
    """request_filters for a requester chest: request(("advanced-circuit", 20), ("steel-plate", 50))."""
    return {"sections": [{"index": 1, "filters": [{"index": i + 1, "name": n, "quality": "normal", "comparator": "=",
                                                  "count": k} for i, (n, k) in enumerate(pairs)]}]}


def spec_path(path):
    base = path[:-4] if path.endswith(".txt") else path
    return base + ".test.json"


def kind_or(k, default):
    return k or default


def _version():
    try:
        v, _ = fenv.version()
        a, b, c = (int(x) for x in v.split(".")[:3])
        return bptool.version_int(a, b, c)
    except Exception:
        return bptool.version_int(2, 0, 0)


def _main_icon(P):
    for e in P.ents:
        if e.get("recipe"):
            return protos()["recipes"][e["recipe"]]["results"][0][0]
    return P.ents[0]["name"] if P.ents else "transport-belt"


def _xy(e):
    return (e["position"]["x"], e["position"]["y"])


def _covers(pole, e, r=None):
    r = r if r is not None else ent_info(pole["name"]).get("supply_area_distance", 0)
    px, py = _xy(pole)
    w, h = size(e["name"], e.get("direction", N))
    ex, ey = _xy(e)
    return (abs(px - ex) < r + w / 2 - 1e-6) and (abs(py - ey) < r + h / 2 - 1e-6)


def _reach(a, b):
    return min(ent_info(a["name"])["maximum_wire_distance"], ent_info(b["name"])["maximum_wire_distance"])


def _components(poles):
    cell = 34                       # no pole reaches further (big pole: 32)
    grid = {}
    for p in poles:
        x, y = _xy(p)
        grid.setdefault((int(x // cell), int(y // cell)), []).append(p)
    seen, comps = set(), []
    for p in poles:
        if id(p) in seen:
            continue
        comp, stack = [], [p]
        seen.add(id(p))
        while stack:
            q = stack.pop()
            comp.append(q)
            qx, qy = _xy(q)
            gx, gy = int(qx // cell), int(qy // cell)
            for i in (gx - 1, gx, gx + 1):
                for j in (gy - 1, gy, gy + 1):
                    for o in grid.get((i, j), ()):
                        if id(o) not in seen and math.dist((qx, qy), _xy(o)) <= _reach(q, o):
                            seen.add(id(o))
                            stack.append(o)
        comps.append(comp)
    return comps


def _wires(ents, num):
    """Copper wires as a minimum spanning tree of the poles (what robots need to build a connected grid)."""
    poles = [e for e in ents if ent_info(e["name"])["type"] == "electric-pole"]
    pairs = sorted((math.dist(_xy(a), _xy(b)), i, j) for i, a in enumerate(poles) for j, b in enumerate(poles)
                   if i < j and math.dist(_xy(a), _xy(b)) <= _reach(a, b))
    parent = list(range(len(poles)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    wires = []
    for _, i, j in pairs:
        ri, rj = root(i), root(j)
        if ri != rj:
            parent[ri] = rj
            wires.append([num[id(poles[i])], 5, num[id(poles[j])], 5])
    return wires


# ---------------------------------------------------------------------------------------------------------
# Belt router (A*): turns, undergrounds under obstacles, never pointing into an existing belt.

def _route(blocked, start, goal, goal_dir, fronts=frozenset(), ug_lines=None, start_dir=None, bounds=None,
           max_ug=5, max_fast=5, turn_cost=0.3, ug_cost=8.0, fast_cost=11.0, limit=600000, ug_tiles=None,
           any_dir=False, plain_ends=False):
    ug_lines = ug_lines or {}
    bad = set(blocked) | set(fronts)
    if start in bad and start != goal:
        raise Collision(f"route start {start} is taken")
    ug_tiles = ug_tiles or {"x": set(), "y": set()}

    def inside(t):
        return bounds is None or (bounds[0] <= t[0] <= bounds[2] and bounds[1] <= t[1] <= bounds[3])

    def free(t):
        return t not in bad and inside(t)

    def span_ok(a, b, d):
        axis = "x" if d in (E, W) else "y"
        fixed = a[1] if axis == "x" else a[0]
        lo, hi = sorted((a[0], b[0]) if axis == "x" else (a[1], b[1]))
        for v in range(lo + 1, hi):
            if ((v, fixed) if axis == "x" else (fixed, v)) in ug_tiles[axis]:
                return False
        for (p, q) in ug_lines.get((axis, fixed), []):
            if not (hi < p or lo > q):
                return False
        return True

    def h(a):
        return abs(a[0] - goal[0]) + abs(a[1] - goal[1])

    dirs0 = (list(start_dir) if isinstance(start_dir, (list, tuple)) else [start_dir]) if start_dir is not None else [N, E, S, W]
    openq, best, parent, cnt = [], {}, {}, 0
    for d in dirs0:
        st = (start, d, True)
        best[st] = 0.0
        heapq.heappush(openq, (h(start), 0.0, cnt, st, None))
        cnt += 1
    for d in ([] if plain_ends else dirs0):   # the start tile itself may be an underground entrance
        for k in range(2, max_fast + 1):
            ex = (start[0] + V[d][0] * k, start[1] + V[d][1] * k)
            if not (free(ex) or ex == goal) or not span_ok(start, ex, d):
                continue
            c = (ug_cost if k <= max_ug else fast_cost) + k * 0.2
            ns = (ex, d, True)
            if c < best.get(ns, 1e18):
                best[ns] = c
                heapq.heappush(openq, (c + h(ex), c, cnt, ns, ((start, d, "ug0"), "underground-belt", start)))
                cnt += 1
    while openq:
        f, g, _, st, par = heapq.heappop(openq)
        if st in parent:
            continue
        if par is not None and par[0][2] == "ug0":
            parent[par[0]] = None
        parent[st] = par
        t, d, straight = st
        if t == goal and (d == goal_dir or any_dir):
            return _rebuild(parent, st)
        if len(parent) > limit:
            break
        recent = {t}
        cur, k = st, 0
        while parent.get(cur) is not None and k < 24:
            pst, kind, ent = parent[cur]
            recent.add(pst[0])
            if ent is not None:
                recent.add(ent)
            cur, k = pst, k + 1
        nx = (t[0] + V[d][0], t[1] + V[d][1])
        if (free(nx) or nx == goal) and nx not in recent:
            for nd, c in ((d, 1.0), (LEFT[d], 1.0 + turn_cost), (RIGHT[d], 1.0 + turn_cost)):
                ns = (nx, nd, nd == d)
                ng = g + c
                if ng < best.get(ns, 1e18):
                    best[ns] = ng
                    heapq.heappush(openq, (ng + h(nx), ng, cnt, ns, (st, "belt", None)))
                    cnt += 1
        if free(nx) and nx != goal and nx not in recent:
            for k in range(2, max_fast + 1):
                ex = (nx[0] + V[d][0] * k, nx[1] + V[d][1] * k)
                if not (free(ex) or ex == goal) or not span_ok(nx, ex, d) or ex in recent:
                    continue
                if plain_ends and (ex == goal or nx == start):
                    continue
                c = (ug_cost if k <= max_ug else fast_cost) + k * 0.2
                ns = (ex, d, True)
                ng = g + c
                if ng < best.get(ns, 1e18):
                    best[ns] = ng
                    heapq.heappush(openq, (ng + h(ex), ng, cnt, ns, (st, "underground-belt", nx)))
                    cnt += 1
    raise Collision(f"no belt route from {start} to {goal} (widen bounds or clear the way)")


def _rebuild(parent, st):
    out, steps, cur = [], [], st
    while parent[cur] is not None:
        prev, kind, ent = parent[cur]
        steps.append((prev, kind, ent, cur))
        cur = prev
    steps.reverse()
    (t0, d0, _) = cur
    if not (steps and steps[0][1] != "belt" and steps[0][2] == t0):
        out.append(["belt", t0[0], t0[1], d0])
    for prev, kind, ent, cur in steps:
        t, d, _ = cur
        if kind == "belt":
            out.append(["belt", t[0], t[1], d])
        else:
            out.append([kind + ":input", ent[0], ent[1], d])
            out.append([kind + ":output", t[0], t[1], d])
    return out


# ---------------------------------------------------------------------------------------------------------
# The player's base as obstacles (for blueprints that must fit an exact spot of their map).

OBST_LUA = r'''
local A = CFG_AREA
local area = {{A[1], A[2]}, {A[3] + 1, A[4] + 1}}
local ents, res = {}, {}
for _, e in pairs(s.find_entities_filtered{area = area}) do
  if e.type == "resource" then
    res[#res + 1] = {e.name, e.position.x, e.position.y, e.amount}
  elseif e.type ~= "character" and e.type ~= "fish" and e.type ~= "corpse" then
    local b = e.bounding_box
    local ghost = e.type == "entity-ghost" and e.ghost_name or nil
    local real_type = ghost and e.ghost_type or e.type
    local ok, d = pcall(function() return e.direction end)
    local io
    if real_type == "underground-belt" then
      local ok2, v = pcall(function() return e.belt_to_ground_type end)
      io = ok2 and v or nil
    end
    ents[#ents + 1] = {e.name, e.type, b.left_top.x, b.left_top.y, b.right_bottom.x, b.right_bottom.y,
                       ok and d or 0, io or false, e.position.x, e.position.y, ghost or false}
  end
end
local water = {}
for _, t in pairs(s.find_tiles_filtered{area = area, collision_mask = "water_tile"}) do
  water[#water + 1] = {t.position.x, t.position.y}
end
local unch = {}
for cx = math.floor(A[1] / 32), math.floor(A[3] / 32) do
  for cy = math.floor(A[2] / 32), math.floor(A[4] / 32) do
    if not f.is_chunk_charted(s, {cx, cy}) then unch[#unch + 1] = {cx, cy} end
  end
end
local bb
for _, e in pairs(s.find_entities_filtered{force = f}) do
  if e.type ~= "character" and e.type ~= "construction-robot" and e.type ~= "logistic-robot" and e.type ~= "entity-ghost"
     and e.type ~= "car" and e.type ~= "spider-vehicle" and e.type ~= "land-mine" then
    local p = e.position
    if not bb then bb = {p.x, p.y, p.x, p.y} else
      if p.x < bb[1] then bb[1] = p.x end if p.y < bb[2] then bb[2] = p.y end
      if p.x > bb[3] then bb[3] = p.x end if p.y > bb[4] then bb[4] = p.y end
    end
  end
end
helpers.write_file("obst.json", helpers.table_to_json({ents = ents, water = water, res = res, unch = unch, base = bb or false}))
out("ok", #ents, #water)
'''


PUMP_LUA = r'''
local A = CFG_AREA
local spots, seen = {}, {}
local dirs = {defines.direction.north, defines.direction.east, defines.direction.south, defines.direction.west}
for _, t in pairs(s.find_tiles_filtered{area = {{A[1], A[2]}, {A[3] + 1, A[4] + 1}}, collision_mask = "water_tile"}) do
  for dx = -1, 1 do for dy = -1, 1 do
    local x, y = t.position.x + dx, t.position.y + dy
    local k = x .. "," .. y
    if not seen[k] then
      seen[k] = true
      for _, d in pairs(dirs) do
        -- "manual" applies the real rules (water in front); the default script check accepts almost anything
        if s.can_place_entity{name = "offshore-pump", position = {x + 0.5, y + 0.5}, direction = d, force = f,
                              build_check_type = defines.build_check_type.manual} then
          spots[#spots + 1] = {x, y, d}
        end
      end
    end
  end end
end
helpers.write_file("pumps.json", helpers.table_to_json(spots))
out("ok", #spots)
'''

# offshore pump: direction -> where its output connection is (verified on 2.1 in a placed pump)
PUMP_OUT = {N: (0, 1), E: (-1, 0), S: (0, -1), W: (1, 0)}


def offshore_spots(x1, y1, x2, y2, save=None):
    """[(x, y, direction, output_tile)] where the game accepts an offshore pump in that area of the save
    (tile = the pump's own tile). Cached per save file and area."""
    import save_lua, shutil, tempfile, hashlib
    save = save or fenv.latest_save()
    key = hashlib.sha1(f"pump2{save}{os.path.getmtime(save)}{x1},{y1},{x2},{y2}".encode()).hexdigest()[:16]
    cache = os.path.join(fenv.cache_base(), f"pumps-{key}.json")
    if not os.path.exists(cache):
        tmp = tempfile.mkdtemp(dir=fenv.tmp_base())
        lines = save_lua.run(PUMP_LUA.replace("CFG_AREA", "{%d,%d,%d,%d}" % (x1, y1, x2, y2)), save, out_dir=tmp)
        src = os.path.join(tmp, "pumps.json")
        if not os.path.exists(src):
            raise RuntimeError("could not read the save:\n" + "\n".join(lines))
        os.replace(src, cache)
        shutil.rmtree(tmp, ignore_errors=True)
    out = []
    for x, y, d in json.load(open(cache)) or []:
        v = PUMP_OUT[d]
        out.append((x, y, d, (x + v[0], y + v[1])))
    return out


def obstacles_from_save(P, x1, y1, x2, y2, save=None, ignore_trees=True):
    """Marks everything the player has in that area of their save (buildings, ghosts, cliffs, water; trees
    and rocks too unless ignore_trees) as taken, records where their belts point, and remembers their poles
    so power() only adds what is missing. Cached per save file and area."""
    import save_lua, shutil, tempfile, hashlib
    save = save or fenv.latest_save()
    key = hashlib.sha1(f"v3{save}{os.path.getmtime(save)}{x1},{y1},{x2},{y2}".encode()).hexdigest()[:16]
    cache = os.path.join(fenv.cache_base(), f"obst-{key}.json")
    if not os.path.exists(cache):
        tmp = tempfile.mkdtemp(dir=fenv.tmp_base())
        body = OBST_LUA.replace("CFG_AREA", json.dumps([x1, y1, x2, y2]).replace("[", "{").replace("]", "}"))
        lines = save_lua.run(body, save, out_dir=tmp)
        src = os.path.join(tmp, "obst.json")
        if not os.path.exists(src):
            raise RuntimeError("could not read the save:\n" + "\n".join(lines))
        os.replace(src, cache)
        shutil.rmtree(tmp, ignore_errors=True)
    o = json.load(open(cache))
    P.known.append((x1, y1, x2, y2))
    P.save_path = save
    P.uncharted |= {tuple(c) for c in (o.get("unch") or [])}
    if o.get("base"):
        P.base_box = tuple(o["base"])
    dummy_cliff = {"name": "cliff", "position": {"x": 0, "y": 0}, "_external": True}
    for name, typ, lx, ly, rx, ry, d, io, px, py, ghost in o["ents"]:
        if ignore_trees and typ in ("tree", "simple-entity"):
            continue
        tiles = [(tx, ty) for tx in range(math.floor(lx + 0.01), math.ceil(rx - 0.01))
                 for ty in range(math.floor(ly + 0.01), math.ceil(ry - 0.01))]
        if typ == "cliff":   # cliffs are diagonal-ish; keep a margin
            tiles = {(tx + dx, ty + dy) for tx, ty in tiles for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
            for t in tiles:
                P.occ.setdefault(t, dummy_cliff)
            continue
        real = ghost or name
        P.block(tiles, label=f"{ghost + ' ghost' if ghost else name}", real=real)
        rt = protos()["entities"].get(real, {}).get("type")
        if rt in BELT_TYPES and d in V:
            v = V[d]
            if rt == "splitter":
                for tx, ty in tiles:
                    P.fronts.add((tx + v[0], ty + v[1]))
            elif not (rt == "underground-belt" and io == "input"):
                P.fronts.add((math.floor(px) + v[0], math.floor(py) + v[1]))
            if rt == "underground-belt":
                P.ug_tiles["x" if d in (E, W) else "y"].add((math.floor(px), math.floor(py)))
        if rt == "electric-pole" and not ghost:
            P.ext_poles.append({"name": real, "position": {"x": px, "y": py}})
        if rt == "roboport" and not ghost:
            P.ext_roboports.append({"name": real, "position": {"x": px, "y": py}})
    P.block([tuple(w) for w in o["water"]], label="water")
    for name, x, y, amount in o.get("res", []):
        P.ore[(math.floor(x), math.floor(y))] = (name, amount)
    return P
