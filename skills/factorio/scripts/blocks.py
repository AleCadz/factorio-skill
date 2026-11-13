#!/usr/bin/env python3
"""Parametric, simulation-tested building blocks for layout.Plan.

Every block is the layout a good player would build: machines on both sides of a central output belt,
inputs on the outer belts flowing down from a head at the top, the product flowing up through the head.
They place their own poles and register their test (feeds, output, expected rate), so

    P = Plan(); blocks.smelter(P, 0, 0, n=24); P.save("x.txt", "Iron x24")

is a finished, tested blueprint. Each returns a dict with the entities that matter (inputs, output,
machines) and the numbers (per_min, inputs_per_min, width, height) so blocks can be chained with
P.route() in a custom generator.

  smelter(P, x, y, n, ore="iron-ore", furnace="stone-furnace", fuel="coal", ...)
  line(P, x, y, recipe, n, machine="assembling-machine-2", ...)   # any solid recipe, 1-4 ingredients
  pair_line(P, x, y, recipe, n, helper)                          # helper machine inserting directly (green circuits)
  mine(P, x, y, pairs, d=E)                                      # electric drills on both sides of a belt
  column(P, x, y, recipe, n, machine, out="chest")               # one column fed from a belt, output to chests/belt
  cells(P, x, y, [(recipe, bar), ...])                           # logistic cells: requester -> machine -> passive provider
  station(P, name, d, rail, stop_at, side, load) / refuel(P, st) # train stop + chest rows per wagon (after P.rails())
  refineries(P, x, y, n) / plastic(P, x, y, n) / pumpjack(P, wx, wy)   # basic oil -> gas -> plastic
  tee(P, x, y, d) / chest_tail(P, x, y, d, n) / drill_chests(P, x, y, n)   # lane merge, belt end into chests, drill -> chest

Columns of one side (left side shown; the right side is its mirror):
  1-2 ingredients:  [in1][ins][machine][out] | centre
  3-4 ingredients:  [in2][in1][ins+long][machine][out] | centre
"""
import math, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from layout import Plan, Collision, N, E, S, W, ent_info, size, recipe, per_minute, consumes_per_minute, smelting_recipe  # noqa: E402,F401

# Rough belt->machine throughput of one inserter with no stack bonus research (items/s). The simulation is
# the judge; this only picks a sensible default and warns.
INSERTER_RATE = {"burner-inserter": 0.6, "inserter": 0.83, "long-handed-inserter": 1.15, "fast-inserter": 2.31,
                 "bulk-inserter": 2.31}


def _pick_inserter(load, allowed=("inserter", "fast-inserter", "bulk-inserter")):
    for k in allowed:
        if INSERTER_RATE[k] * 0.9 >= load:
            return k
    return allowed[-1]


def _belt_names(belt):
    ug = belt.replace("transport-belt", "underground-belt")
    sp = belt.replace("transport-belt", "splitter")
    return belt, ug, sp


def _lane_cap(belt):
    return ent_info(belt)["items_per_s"] * 60 / 2      # items/min per lane


def _column_line(P, x0, y0, n, machine, place_machine, ingredients_lanes, in_ins, long_ins, out_ins, belt, pole,
                 split, head_extra=0):
    """The shared skeleton. ingredients_lanes: list of 'a|b' per input belt (1 or 2 belts per side)."""
    kbelts = len(ingredients_lanes)
    mw, mh = size(machine)
    if kbelts == 2 and mh < 3:
        raise ValueError("two input belts per side need 3x3 machines")
    per_side = math.ceil(n / 2)
    cols, width = _columns(x0, kbelts, mw)
    body_h = per_side * mh
    machines, poles = [], []
    pole_every = 1 if (kbelts == 2 or (mh == 3 and len(ingredients_lanes) == 1 and in_ins[1])) else 2
    for i in range(per_side):
        r = y0 + i * mh
        for side, sgn in (("L", 1), ("R", -1)):
            if side == "R" and per_side * 2 > n and i == per_side - 1:
                continue      # odd n: the last right machine is left out
            mx = cols[f"{side}m"]
            machines.append(place_machine(mx, r))
            pick = W if side == "L" else E   # inserters face where they pick up from
            P.add(in_ins[0], cols[f"{side}ins"], r, pick)
            if kbelts == 2:
                P.add(long_ins, cols[f"{side}ins"], r + 1, pick)
            elif in_ins[1]:
                P.add(in_ins[1], cols[f"{side}ins"], r + 1, pick)
            P.add(out_ins, cols[f"{side}out"], r, pick)
        # poles: input-inserter columns every machine (or every other), the left output column covers both
        if i % pole_every == 0 or i == per_side - 1:
            free_row = r + mh - 1 if (kbelts == 2 or in_ins[1]) else r + 1
            for c in ("Lins", "Rins"):
                if P.free(cols[c], free_row):
                    poles.append(P.add(pole, cols[c], free_row))
        if i % 2 == 0 or i == per_side - 1:
            if P.free(cols["Lout"], r + 1):
                poles.append(P.add(pole, cols["Lout"], r + 1))
    res = _finish(P, x0, y0, cols, width, body_h, ingredients_lanes, belt, pole, split)
    res["machines"] = machines
    return res


def _columns(x0, kbelts, mw):
    cols = {}
    x = x0
    for j in reversed(range(kbelts)):        # outermost belt first
        cols[f"L{j}"] = x; x += 1
    cols["Lins"] = x; x += 1
    cols["Lm"] = x; x += mw
    cols["Lout"] = x; x += 1
    cols["C"] = x; x += 1
    cols["Rout"] = x; x += 1
    cols["Rm"] = x; x += mw
    cols["Rins"] = x; x += 1
    for j in range(kbelts):
        cols[f"R{j}"] = x; x += 1
    return cols, x - x0


def _finish(P, x0, y0, cols, width, body_h, ingredients_lanes, belt, pole, split):
    """Input belts down both sides, the output up the centre, the head (or bare tops), test and power."""
    kbelts = len(ingredients_lanes)
    for j in range(kbelts):
        P.belt([(cols[f"L{j}"], y0), (cols[f"L{j}"], y0 + body_h - 1)], kind=belt)
        P.belt([(cols[f"R{j}"], y0), (cols[f"R{j}"], y0 + body_h - 1)], kind=belt)
    P.belt([(cols["C"], y0 + body_h - 1), (cols["C"], y0)], kind=belt)
    feeds = []
    if not split:
        for j, lanes in enumerate(ingredients_lanes):
            feeds += [(P.at(cols[f"L{j}"], y0), lanes), (P.at(cols[f"R{j}"], y0), lanes)]
        out = P.at(cols["C"], y0)
        top = y0
    else:
        top, out, feeds = _head(P, x0, y0, cols, kbelts, ingredients_lanes, belt, width)
        if kbelts == 2:
            # known limit (measured: flying robot frames at half rate): the head's route to the right side can
            # start with a sideways underground under the splitter, which passes one lane only
            P.notes.append("two input belts per side with a split head: the RIGHT side's outer belt may get one "
                           "lane only (half its items). If the test comes out LOW use split=False / --no-split "
                           "and feed both sides, or build the chain with chain.py (its head is fixed).")
    for e, lanes in feeds:
        P.feed(e, lanes)
    P.sink(out)
    if P.unpowered():
        P.power(pole, bounds=(x0, top, x0 + width - 1, y0 + body_h - 1))
    return {"inputs": feeds, "output": out, "cols": cols, "width": width,
            "height": y0 + body_h - top, "top": top, "x": x0}


def _head(P, x0, y0, cols, kbelts, ingredients_lanes, belt, width):
    """One entry belt per input at the top-left; a splitter sends half to the right side (routed with
    undergrounds across the other belts). The product leaves at the top of the centre column."""
    _, _, sp = _belt_names(belt)
    last = None
    for H in range(2 * kbelts + 2, 2 * kbelts + 9):
        snap = (list(P.ents), dict(P.occ), set(P.fronts), {k: list(v) for k, v in P.ug_lines.items()},
                {k: set(v) for k, v in P.ug_tiles.items()})
        try:
            top = y0 - H
            P.belt([(cols["C"], y0 - 1), (cols["C"], top)], kind=belt)
            feeds = []
            for j in reversed(range(kbelts)):     # outermost first
                L, R = cols[f"L{j}"], cols[f"R{j}"]
                srow = top + 1 + 2 * (kbelts - 1 - j)
                a = L if j == 0 else L - 1        # inner belt: splitter on L..L+1, outer: L-1..L
                other = a + 1 if a == L else a
                entry = (P.belt([(L, top), (L, srow - 1)], kind=belt) if srow - 1 > top
                         else P.belt([(L, top)], end=S, kind=belt))
                P.splitter(a, srow, S, kind=sp)
                P.belt([(L, srow + 1), (L, y0 - 1)], kind=belt)
                P.route((other, srow + 1), (R, y0 - 1), S, start_dir=(S, E, W) if other != a else (S, W, E),
                        kind=belt, bounds=(x0 - 2, top, x0 + width + 1, y0 - 1))
                feeds.append((entry[0], ingredients_lanes[j]))
            return top, P.at(cols["C"], top), feeds
        except Collision as ex:
            last = ex
            P.ents[:], P.occ, P.fronts, P.ug_lines, P.ug_tiles = snap[0], snap[1], snap[2], snap[3], snap[4]
    raise Collision(f"could not route the head: {last}")


def smelter(P, x, y, n, ore="iron-ore", furnace="stone-furnace", fuel="coal", belt="transport-belt",
            inserter="auto", pole="small-electric-pole", split=True):
    """n furnaces (two columns) around a central output belt. Burner furnaces get ore|fuel on each input
    belt; electric furnaces get ore on both lanes. split=True: one entry belt at the top-left is split to
    both sides; split=False: feed the left and right input belts yourself."""
    rec = smelting_recipe(ore)
    electric = ent_info(furnace)["electric"]
    each = per_minute(rec, furnace)
    need = consumes_per_minute(rec, furnace)
    product = next(iter(each))
    lanes = ore if electric else f"{ore}|{fuel}"
    load = max(need.values()) / 60
    ins = _pick_inserter(load) if inserter == "auto" else inserter
    out_ins = _pick_inserter(each[product] / 60) if inserter == "auto" else inserter
    res = _column_line(P, x, y, n, furnace, lambda mx, r: P.add(furnace, mx, r), [lanes], (ins, None), None,
                       out_ins, belt, pole, split)
    total = each[product] * n
    ore_min = need[ore] * n
    res.update(product=product, per_min=total, inputs_per_min={ore: ore_min}, recipe=rec)
    P.expect(product, total)
    cap = _lane_cap(belt) * (1 if not electric else 2) * (1 if split else 2)
    if ore_min > cap * 1.001:
        P.notes.append(f"{n} {furnace} need {ore_min:g} {ore}/min but the input carries at most {cap:g}/min "
                       f"({'one entry' if split else 'two entries'}, {'one lane' if not electric else 'two lanes'} of {belt}): "
                       f"they will run at {cap / ore_min:.0%}. Use split=False, a faster belt or fewer furnaces.")
        P.spec["expect"][product] = P.spec["expect"][product] - total + total * cap / ore_min
    if total > _lane_cap(belt) * 2 * 1.001:
        P.notes.append(f"output {total:g}/min exceeds one {belt} ({_lane_cap(belt) * 2:g}/min)")
    if not electric:
        fuel_mj = _fuel_mj(fuel)
        kw = _energy_kw(furnace)
        res["inputs_per_min"][fuel] = n * kw / 1000 / fuel_mj * 60
    return res


def line(P, x, y, recipe_name, n, machine="assembling-machine-2", belt="transport-belt", inserter="auto",
         pole="small-electric-pole", split=True, lanes=None):
    """n assemblers making `recipe_name` around a central output belt. Ingredients go two per belt
    (lanes); 3-4 ingredients use a second, outer belt reached by long-handed inserters. lanes: override the
    belt contents, e.g. ['iron-plate|copper-cable']."""
    r = recipe(recipe_name)
    ings = [i for i in r["ingredients"]]
    if any(t == "fluid" for _, _, t in ings) or any(t == "fluid" for _, _, t in r["results"]):
        raise ValueError(f"{recipe_name} uses fluids: build it with Plan + pipes and P.fluid() in a generator")
    names = [i[0] for i in ings]
    if len(names) > 4:
        raise ValueError("more than 4 ingredients")
    if lanes is None:
        lanes = []
        for k in range(0, len(names), 2):
            pair = names[k:k + 2]
            lanes.append(pair[0] if len(pair) == 1 else f"{pair[0]}|{pair[1]}")
    each = per_minute(recipe_name, machine)
    need = consumes_per_minute(recipe_name, machine)
    product = r["results"][0][0]
    in1_load = sum(need.get(nm, 0) for nm in lanes[0].split("|")) / 60
    two = False
    if inserter == "auto":
        ins = _pick_inserter(in1_load, ("inserter", "fast-inserter"))
        if len(lanes) == 1 and in1_load > INSERTER_RATE["inserter"] * 0.9:
            ins = _pick_inserter(in1_load / 2)     # two inserters from the same belt (rows 1 and 2)
            two = True
        out_ins = _pick_inserter(each[product] / 60)
    else:
        ins = out_ins = inserter
    in_ins = (ins, ins if two else None)
    long_ins = "long-handed-inserter"
    if len(lanes) == 2:
        in2_load = sum(need.get(nm, 0) for nm in lanes[1].split("|")) / 60
        if in2_load > INSERTER_RATE[long_ins]:
            P.notes.append(f"the long-handed inserter carries {in2_load:.2f}/s per machine (it manages ~1.15/s)")

    def place(mx, ry):
        return P.add(machine, mx, ry, recipe=recipe_name)
    res = _column_line(P, x, y, n, machine, place, lanes, in_ins, long_ins, out_ins, belt, pole, split)
    total = each[product] * n
    res.update(product=product, per_min=total, recipe=recipe_name,
               inputs_per_min={k: v * n for k, v in need.items()})
    P.expect(product, total)
    if in1_load > INSERTER_RATE[ins] * (2 if two else 1):
        P.notes.append(f"each machine needs {in1_load:.2f}/s from its inner belt; {('2 × ' if two else '') + ins} "
                       f"moves about {INSERTER_RATE[ins] * (2 if two else 1):.2f}/s: the line will be inserter-limited")
    worst = 1.0
    for lane_set in lanes:
        parts = lane_set.split("|")
        parts = parts * 2 if len(parts) == 1 else parts
        for nm in set(parts):
            supply = _lane_cap(belt) * parts.count(nm) * (1 if split else 2)
            want = need.get(nm, 0) * n
            if want > supply * 1.001:
                worst = min(worst, supply / want)
                P.notes.append(f"{nm}: {want:g}/min needed but its input carries {supply:g}/min "
                               f"({parts.count(nm)} lane(s) of {belt}, {'one entry' if split else 'two entries'}): "
                               f"the line runs at {supply / want:.0%}. Fewer machines, a faster belt or --no-split.")
    if worst < 1:
        P.spec["expect"][product] -= total * (1 - worst)
    return res


def pair_line(P, x, y, recipe_name, n, helper, machine="assembling-machine-2", helper_machine=None,
              belt="transport-belt", pole="small-electric-pole", split=True, lanes=None):
    """n units of [helper machine above, main machine below], the helper inserting its product straight into
    the main one (no belt for it): green circuits with copper cable, red science with gears... One input belt
    per side carries what both need (at most two items), e.g. copper|iron for green circuits.

    Per side, 7 rows per unit:  helper (3 rows) / gap row with 2 transfer inserters + pole / main (3 rows)."""
    helper_machine = helper_machine or machine
    r = recipe(recipe_name)
    hr = recipe(helper)
    hprod = hr["results"][0][0]
    if not any(i[0] == hprod for i in r["ingredients"]):
        raise ValueError(f"{helper} makes {hprod}, which {recipe_name} doesn't use")
    for rr in (r, hr):
        if any(t_ == "fluid" for _, _, t_ in rr["ingredients"] + rr["results"]):
            raise ValueError("fluids: build it with Plan + pipes in a generator")
    belt_items = []
    for nm, _, _ in hr["ingredients"] + [i for i in r["ingredients"] if i[0] != hprod]:
        if nm not in belt_items:
            belt_items.append(nm)
    if len(belt_items) > 3:
        raise ValueError(f"the input belts would need {belt_items}: more than three items")
    # three items (red circuits: copper for the cable helper + green circuits|plastic): the helper's ingredient
    # gets its own outer belt, reached with a long-handed inserter; the main machine's two share the inner one
    two_belts = len(belt_items) == 3
    if two_belts:
        if len(hr["ingredients"]) != 1:
            raise ValueError("with three belt items the helper must have a single ingredient")
        helper_lane = hr["ingredients"][0][0]
        lanes = lanes or "|".join(b for b in belt_items if b != helper_lane)
    else:
        lanes = lanes or ("|".join(belt_items) if len(belt_items) == 2 else belt_items[0])
    main_each = per_minute(recipe_name, machine)
    product = r["results"][0][0]
    h_out = per_minute(helper, helper_machine)[hprod]
    m_need = consumes_per_minute(recipe_name, machine)[hprod]
    factor = min(1.0, h_out / m_need)          # the main machine runs at the pace its helper feeds it
    per_side = math.ceil(n / 2)
    cols, width = _columns(x, 2 if two_belts else 1, 3)
    unit_h = 7
    body_h = per_side * unit_h
    transfer = _pick_inserter(min(h_out, m_need) / 60 / 2, ("inserter", "fast-inserter"))
    machines = []
    in_load = max(sum(consumes_per_minute(helper, helper_machine).values()),
                  sum(v for k, v in consumes_per_minute(recipe_name, machine).items() if k != hprod) * factor) / 60
    ins = _pick_inserter(in_load, ("inserter", "fast-inserter"))
    out_ins = _pick_inserter(main_each[product] * factor / 60, ("inserter", "fast-inserter"))
    for i in range(per_side):
        r0 = y + i * unit_h
        for side in ("L", "R"):
            if side == "R" and per_side * 2 > n and i == per_side - 1:
                continue
            mx = cols[f"{side}m"]
            pick = W if side == "L" else E
            machines.append(P.add(helper_machine, mx, r0, recipe=helper))
            machines.append(P.add(machine, mx, r0 + 4, recipe=recipe_name))
            P.add(transfer, mx, r0 + 3, N)            # picks from the helper above, drops into the main one
            P.add(transfer, mx + 2, r0 + 3, N)
            P.add(pole, mx + 1, r0 + 3)               # powers both machines and the side's inserter columns
            P.add("long-handed-inserter" if two_belts else ins, cols[f"{side}ins"], r0 + 1, pick)
            P.add(ins, cols[f"{side}ins"], r0 + 5, pick)
            P.add(out_ins, cols[f"{side}out"], r0 + 5, pick)
    res = _finish(P, x, y, cols, width, body_h, [lanes, helper_lane] if two_belts else [lanes], belt, pole, split)
    units = n
    total = main_each[product] * factor * units
    need = {k: v * units * factor for k, v in consumes_per_minute(recipe_name, machine).items() if k != hprod}
    for k, v in consumes_per_minute(helper, helper_machine).items():
        need[k] = need.get(k, 0) + v * units * min(1.0, m_need / h_out)
    res.update(machines=machines, product=product, per_min=total, recipe=recipe_name, inputs_per_min=need)
    P.expect(product, total)
    if factor < 1:
        P.notes.append(f"one {helper} machine feeds {factor:.0%} of what a {recipe_name} machine can use: "
                       f"{main_each[product] * factor:g}/min per unit")
    parts = lanes.split("|") * (2 if "|" not in lanes else 1)
    if two_belts:
        parts += [helper_lane] * 2
    worst = 1.0
    for nm in set(parts):
        supply = _lane_cap(belt) * parts.count(nm) * (1 if split else 2)
        if need.get(nm, 0) > supply * 1.001:
            worst = min(worst, supply / need[nm])
            P.notes.append(f"{nm}: {need[nm]:g}/min needed, its input carries {supply:g}/min: runs at {supply / need[nm]:.0%}")
    if worst < 1:
        P.spec["expect"][product] -= total * (1 - worst)
    return res


def mine(P, x, y, pairs, d=E, drill="electric-mining-drill", belt="transport-belt", pole="medium-electric-pole",
         min_ore=4, ore=None):
    """Two rows of drills facing a belt between them; the belt flows along d (E/W: rows above and below,
    N/S: columns left and right). Tile (x, y) is the top-left of the whole block (7 tiles across the belt,
    3*pairs along it). With ore read from the save (obstacles_from_save) drills that would cover fewer than
    min_ore ore tiles are left out. Find the spot with P.find_ore(ore, 3 * pairs, 7). Returns {'output': last belt tile (feeds whatever is in front of it),
    'drills': [...], 'per_min': mining rate if every drill works}."""
    horiz = d in (E, W)
    drills = []
    rad = ent_info(drill).get("resource_searching_radius", 2.49)
    dw, _ = size(drill)
    lo = math.floor(dw / 2 - rad)              # mining area relative to the drill's top-left tile
    hi = math.ceil(dw / 2 + rad)
    found = {}

    def ore_under(cx, cy):
        # the game refuses a drill with no ore in its mining area; count the tiles it would mine
        if not P.ore:
            return 99
        hits = [P.ore[(cx + i, cy + j)][0] for i in range(lo, hi) for j in range(lo, hi) if (cx + i, cy + j) in P.ore]
        for h_ in hits:
            found[h_] = found.get(h_, 0) + 1
        return len(hits)
    for k in range(pairs):
        if horiz:
            a, b = (x + 3 * k, y, S), (x + 3 * k, y + 4, N)
        else:
            a, b = (x, y + 3 * k, E), (x + 4, y + 3 * k, W)
        for dx, dy, dd in (a, b):
            if ore_under(dx, dy) >= min_ore:
                drills.append(P.add(drill, dx, dy, dd))
    L = 3 * pairs
    if horiz:
        pts = [(x, y + 3), (x + L - 1, y + 3)]
    else:
        pts = [(x + 3, y), (x + 3, y + L - 1)]
    if d in (W, N):
        pts.reverse()
    belts = P.belt(pts, kind=belt)
    bx = (x - 2, y - 2, x + L + 1, y + 8) if horiz else (x - 2, y - 2, x + 8, y + L + 1)
    if P.unpowered():
        P.power(pole, bounds=bx)
    speed = ent_info(drill).get("mining_speed", 0.5)
    ore = ore or (max(found, key=found.get) if found else None)
    if ore:
        P.feed(belts[0], ore)           # lab test only: there is no ore in the lab, so the belt is fed instead
    P.spec["lab_missing"] = P.spec.get("lab_missing", 0) + len(drills)
    return {"drills": drills, "output": belts[-1], "belt": belts, "per_min": len(drills) * speed * 60,
            "width": L if horiz else 7, "height": 7 if horiz else L}


def column(P, x, y, recipe_name, n, machine="electric-furnace", out="passive-provider-chest", bar=None,
           belt="transport-belt", inserter="auto", pole="medium-electric-pole", side=W, fuel=None):
    """One column of n machines fed from a belt on `side` (W: belt on the left flowing south, E: mirrored).
    Output: into a chest per machine (out = chest name; bar limits its slots) or out="belt" (a belt on the
    other side flowing north). Good for 'ore -> plates into provider chests' cells and small mall rows.
    Footprint: (machine width + 4) × (n × machine height); find a spot with P.find_free(w, h, near).
    Returns {'input': top belt tile, 'output': belt top or chests, 'machines', 'per_min', 'inputs_per_min'}."""
    r = recipe(recipe_name)
    mw, mh = size(machine)
    each = per_minute(recipe_name, machine)
    need = consumes_per_minute(recipe_name, machine)
    product = r["results"][0][0]
    ins = _pick_inserter(sum(need.values()) / 60, ("inserter", "fast-inserter")) if inserter == "auto" else inserter
    oins = _pick_inserter(each[product] / 60, ("inserter", "fast-inserter")) if inserter == "auto" else inserter
    if side == W:
        cb, ci, cm, co, cx = x, x + 1, x + 2, x + 2 + mw, x + 3 + mw
        pick_in, pick_out = W, W
    else:
        cx, co, cm, ci, cb = x, x + 1, x + 2, x + 2 + mw, x + 3 + mw
        pick_in, pick_out = E, E
    machines, chests = [], []
    for k in range(n):
        r0 = y + k * mh
        kw = {} if ent_info(machine)["type"] == "furnace" else {"recipe": recipe_name}
        machines.append(P.add(machine, cm, r0, **kw))
        P.add(ins, ci, r0 + mh // 2, pick_in)
        P.add(oins, co, r0 + mh // 2, pick_out)
        if out != "belt":
            c = P.add(out, cx, r0 + mh // 2, **({"bar": bar} if bar else {}))
            chests.append(c)
    body = n * mh
    inb = P.belt([(cb, y), (cb, y + body - 1)], kind=belt)
    lanes = "|".join(i[0] for i in r["ingredients"][:2]) if len(r["ingredients"]) > 1 else r["ingredients"][0][0]
    if fuel and not ent_info(machine)["electric"]:
        lanes = f"{lanes}|{fuel}"
    P.feed(inb[0], lanes)
    res = {"input": inb[0], "machines": machines, "per_min": each[product] * n, "product": product,
           "inputs_per_min": {k: v * n for k, v in need.items()}}
    if out == "belt":
        ob = P.belt([(cx, y + body - 1), (cx, y)], kind=belt)
        P.sink(ob[-1])
        res["output"] = ob[-1]
    else:
        res["output"] = chests
    P.expect(product, each[product] * n)
    if out == "belt" and each[product] * n > _lane_cap(belt):
        P.notes.append(f"{each[product] * n:g} {product}/min but one column of inserters fills one lane of {belt} "
                       f"({_lane_cap(belt):g}/min): use line() (both sides) or a faster belt")
        P.spec["expect"][product] = _lane_cap(belt)
    if P.unpowered():
        P.power(pole, bounds=(x - 1, y - 1, x + mw + 5, y + body))
    return res


def cells(P, x, y, specs, machine="assembling-machine-2", inserter="fast-inserter", pole="medium-electric-pole",
          minutes=1.0):
    """Logistic cells stacked downwards from (x, y): each is [requester][inserter][machine][inserter][passive
    provider], 7 tiles wide and 3 high. Robots bring the ingredients (requests = `minutes` of consumption, at
    least two crafts) and take the product from the passive chest, which stops the machine when its bar is
    full. specs: [(recipe, bar_slots)] or [(recipe, bar_slots, {item: request})]. Returns {'cells': [...]}.
    The lab test fills the requesters with everything (infinity chests) and expects every product."""
    from layout import request
    mw, mh = size(machine)
    out = []
    for k, spec in enumerate(specs):
        rec, bar = spec[0], spec[1]
        r = recipe(rec)
        if any(t_ == "fluid" for _, _, t_ in r["ingredients"]):
            raise ValueError(f"{rec} needs a fluid: not a logistic cell")
        need = consumes_per_minute(rec, machine)
        reqs = dict(spec[2]) if len(spec) > 2 else {
            n: max(math.ceil(need[n] * minutes), 2 * a) for n, a, _ in r["ingredients"]}
        r0 = y + k * mh
        req = P.add("requester-chest", x, r0 + 1, request_filters=request(*reqs.items()))
        P.add(inserter, x + 1, r0 + 1, W)                       # picks from the requester (west)
        m = P.add(machine, x + 2, r0, recipe=rec)
        P.add(inserter, x + 2 + mw, r0 + 1, W)                  # picks from the machine (west)
        prov = P.add("passive-provider-chest", x + 3 + mw, r0 + 1, bar=bar)
        P.fill(req, ",".join(reqs))
        product = r["results"][0][0]
        P.expect(product, per_minute(rec, machine)[product])
        out.append({"recipe": rec, "machine": m, "requester": req, "provider": prov, "requests": reqs})
    if P.unpowered():
        P.power(pole, bounds=(x - 1, y - 1, x + mw + 4, y + len(specs) * mh))
    return {"cells": out, "width": mw + 4, "height": len(specs) * mh}


# ---------------------------------------------------------------------------------------------------------
# Glue pieces learned the hard way

def tee(P, x, y, d=S, belt="transport-belt"):
    """Lane merge: a belt tile at (x, y) flowing d with a feeder on each side pointing into it. Two single-item
    belts routed to the feeders (P.link(src, feeder)) end up one per lane. Nothing may point into (x, y) from
    behind. Returns (the merged belt tile, left feeder, right feeder) seen along d. Why: inserters and machines
    only ever fill ONE lane of a belt (450/min on yellow); side-loading from both sides is how to get two
    items, or a full belt, onto one."""
    from layout import LEFT, RIGHT, OPP, V
    t = P.add(belt, x, y, d)
    lv, rv = V[LEFT[d]], V[RIGHT[d]]
    a = P.add(belt, x + lv[0], y + lv[1], RIGHT[d])
    b = P.add(belt, x + rv[0], y + rv[1], LEFT[d])
    return t, a, b


def chest_tail(P, x, y, d, n=6, chest="passive-provider-chest", bar=None, inserter="bulk-inserter",
               belt="transport-belt"):
    """A belt that continues from tile (x, y) for n tiles along d with an inserter and a chest on both sides of
    every tile: the end of a product line into 2*n (provider) chests. Chests fill first; put a belt after
    the last tile to pass the overflow on. Returns {'belt': [...], 'chests': [...]}."""
    from layout import LEFT, RIGHT, V
    v, lv, rv = V[d], V[LEFT[d]], V[RIGHT[d]]
    belts = P.belt([(x, y), (x + v[0] * (n - 1), y + v[1] * (n - 1))], kind=belt)
    chests = []
    for k in range(n):
        tx, ty = x + v[0] * k, y + v[1] * k
        for sv, pick in ((lv, RIGHT[d]), (rv, LEFT[d])):
            P.add(inserter, tx + sv[0], ty + sv[1], pick)                 # picks from the belt
            chests.append(P.add(chest, tx + 2 * sv[0], ty + 2 * sv[1], **({"bar": bar} if bar else {})))
    return {"belt": belts, "chests": chests}


def drill_chests(P, x, y, n=2, d=E, chest="passive-provider-chest", bar=8, drill="electric-mining-drill"):
    """n drills stacked downwards from (x, y), each dropping straight into its own chest (d = E: chest on the
    right, W: on the left). A few hundred items/min for robots with no belts: coal for train fuel, stone for
    rails. Returns the chests."""
    out = []
    for k in range(n):
        yy = y + 3 * k
        P.add(drill, x, yy, d)
        out.append(P.add(chest, x + 3 if d == E else x - 1, yy + 1, **({"bar": bar} if bar else {})))
    P.spec["lab_missing"] = P.spec.get("lab_missing", 0) + n
    return out


# ---------------------------------------------------------------------------------------------------------
# Fluids (port offsets verified in game, 2.1, machines facing north)

def pumpjack(P, wx, wy):
    """A pumpjack on the oil well whose centre TILE is (wx, wy) (wells are at x.5, y.5: floor both). Returns
    (entity, output tile): start the crude pipe there with P.pipe_route(output_tile, goal)."""
    e = P.add("pumpjack", wx - 1, wy - 1, N)
    P.spec["lab_missing"] = P.spec.get("lab_missing", 0) + 1
    return e, (wx + 1, wy - 2)


def refineries(P, x, y, n, recipe_name="basic-oil-processing"):
    """n refineries in a row (pitch 6) from top-left tile (x, y + 1): one pipe row under them takes the crude
    oil, one row above them collects the petroleum gas. Basic oil processing only (2.0: crude in, gas out, no
    water, nothing to crack or to back up). 540 gas/min and 1200 crude/min each. Needs width 6n-1, height 7.
    Returns {'crude': [pipes], 'gas': [pipes], 'machines'}: route to a tile next to either row's end."""
    if recipe_name != "basic-oil-processing":
        raise ValueError("only basic-oil-processing: advanced processing needs water, cracking and overflow handling")
    refs = [P.add("oil-refinery", x + 6 * k, y + 1, N, recipe=recipe_name) for k in range(n)]
    gas = [P.add("pipe", xx, y) for xx in range(x, x + 6 * n - 1)]
    crude = [P.add("pipe", xx, y + 6) for xx in range(x, x + 6 * n - 1)]
    P.fluid(crude[0], "crude-oil")
    return {"crude": crude, "gas": gas, "machines": refs, "gas_per_min": 540 * n, "crude_per_min": 1200 * n,
            "width": 6 * n - 1, "height": 7}


def plastic(P, x, y, n=3, belt="transport-belt"):
    """n chemical plants in a row (pitch 4) making plastic. Layout from top-left tile (x, y), n*4 wide, 7 high:
    row y gas pipe (feed it at either end), rows y+1..y+3 plants, row y+4 inserters, row y+5 coal belt flowing
    east (coal enters at its west end), row y+6 plastic belt flowing west (one lane: 450/min on yellow).
    120 plastic/min per plant from 1200 gas + 60 coal. Returns {'gas': pipes, 'coal': first coal belt tile,
    'output': last plastic belt tile, 'per_min'}."""
    for k in range(n):
        px = x + 1 + 4 * k
        P.add("chemical-plant", px, y + 1, N, recipe="plastic-bar")
        P.add("fast-inserter", px, y + 4, S)                       # coal from the belt below
        P.add("long-handed-inserter", px + 1, y + 4, N)            # plastic two tiles down, over the coal belt
        P.add("long-handed-inserter", px + 2, y + 4, N)
    gas = [P.add("pipe", xx, y) for xx in range(x, x + 4 * n)]
    coal = P.belt([(x, y + 5), (x + 4 * n - 1, y + 5)], kind=belt)
    out = P.belt([(x + 4 * n - 1, y + 6), (x, y + 6)], kind=belt)
    P.fluid(gas[0], "petroleum-gas")
    P.feed(coal[0], "coal")
    P.sink(out[-1])
    per = min(120 * n, _lane_cap(belt))
    P.expect("plastic-bar", per)
    return {"gas": gas, "coal": coal[0], "output": out[-1], "per_min": per, "width": 4 * n, "height": 7}


# ---------------------------------------------------------------------------------------------------------
# Trains (geometry verified in game: a locomotive stops with its centre 3 tiles behind the stop's centre)

def station(P, name, d, rail, stop_at, side, load, wagons=4, chest="steel-chest", bar=None,
            inserter="bulk-inserter", belt_inserter="fast-inserter"):
    """A train stop on a straight piece of track already in the plan, with a chest row for every wagon.
    d = direction of travel (N/E/S/W); rail = the centre coordinate of the track across it (x for N/S, y for
    E/W; odd); stop_at = the stop's coordinate along the track (odd, next to a rail piece); side = +1 or -1:
    which side the chests go (towards +x for N/S track, +y for E/W). The stop itself always goes on the
    right-hand side of the travel direction. Per wagon tile, from the track outwards: inserter, chest, inserter,
    belt tile (free: lay the belt yourself along 'belt_line').
      load=True : belt -> chests -> wagons; chests get bar=4 by default so the first wagon's chests don't hoard
                  the ore (unlimited chests starved the other wagons for half an hour).
      load=False: wagons -> chests -> belt. The belt inserters drop on ONE lane (450/min): for a full belt use
                  two belts ('tiles'[:half] and [half:]) or a tee().
    Returns {'tiles': coordinates along the track that have inserters (sorted), 'belt_line': coordinate across
    the track where the belt goes, 'T': f(along, across) -> (x, y), 'cars': rolling stock for P.train(), ...}.
    A 1-4 train needs 36 straight tiles behind the stop."""
    from layout import OPP, V
    vert = d in (N, S)
    fwd = V[d][1] if vert else V[d][0]
    right = {N: 1, S: -1, E: 1, W: -1}[d]
    across_stop = rail + 2 * right

    def T(a, p):
        return (p, a) if vert else (a, p)
    kw = {"station": name}
    if d != N:
        kw["direction"] = d
    sx, sy = T(stop_at, across_stop)
    P.raw("train-stop", sx, sy, [T(stop_at - 1 + i, across_stop - 1 + j) for i in (0, 1) for j in (0, 1)], **kw)
    ins1 = rail + 1 if side > 0 else rail - 2
    c_row = ins1 + side
    ins2 = c_row + side
    belt_line = ins2 + side
    out_d = ({1: E, -1: W}[side]) if vert else ({1: S, -1: N}[side])     # facing away from the track
    in_d = OPP[out_d]
    if bar is None and load:
        bar = 4
    centres = [stop_at - fwd * (3 + 7 * (k + 1)) for k in range(wagons)]
    tiles = []
    for c in centres:
        for a in range(c - 3, c + 3):
            tiles.append(a)
            P.add(inserter, *T(a, ins1), out_d if load else in_d)        # inserters face where they pick up
            P.add(chest, *T(a, c_row), **({"bar": bar} if bar else {}))
            P.add(belt_inserter, *T(a, ins2), out_d if load else in_d)
    orient = {N: 0, E: 0.25, S: 0.5, W: 0.75}[d]
    loco_at = stop_at - fwd * 3
    cars = [("locomotive",) + tuple(float(v) for v in T(loco_at, rail)) + (orient,)]
    cars += [("cargo-wagon",) + tuple(float(v) for v in T(c, rail)) + (orient,) for c in centres]
    return {"tiles": sorted(tiles), "belt_line": belt_line, "T": T, "cars": cars, "loco_at": loco_at,
            "inserter_line": ins1, "chest_line": c_row, "side": side, "centres": centres}


def refuel(P, st, fuel="coal", count=50):
    """A requester chest + inserter beside the spot where the locomotive of station() `st` stops: robots keep
    the train fuelled. Put it on stations inside the logistic network."""
    from layout import request
    T, a = st["T"], st["loco_at"]
    vert = T(0, 1) == (1, 0)
    out_d = ({1: E, -1: W}[st["side"]]) if vert else ({1: S, -1: N}[st["side"]])
    c = P.add("requester-chest", *T(a, st["chest_line"]), request_filters=request((fuel, count)))
    P.add("inserter", *T(a, st["inserter_line"]), out_d)
    return c


def _fuel_mj(item):
    import layout
    v = layout.protos()["items"].get(item, {}).get("fuel_value") or "4MJ"
    return _num(v) / 1e6


def _energy_kw(entity):
    return _num(ent_info(entity).get("energy_usage", "90kW")) / 1000


def _num(s):
    mult = {"k": 1e3, "M": 1e6, "G": 1e9, "": 1}
    s = str(s)
    unit = s.rstrip("JWjw")
    m = unit[-1] if unit and unit[-1] in "kMG" else ""
    return float(unit[:-1] if m else unit) * mult[m]
