#!/usr/bin/env python3
"""Factory compiler: ONE tested blueprint with the whole multi-recipe chain for an item, from input belts.

  chain.py --item automation-science-pack --per-min 45 --inputs iron-plate,copper-plate -o red.txt
  chain.py --item logistic-science-pack --per-min 45 --inputs iron-plate,copper-plate -o green.txt --render
  chain.py --item military-science-pack --per-min 30 --inputs iron-plate,copper-plate,steel-plate,coal,stone-brick -o mil.txt
  chain.py --item chemical-science-pack --per-min 30 --inputs iron-plate,copper-plate,steel-plate,plastic-bar,sulfur -o blue.txt
  chain.py --item electronic-circuit --per-min 300 --inputs iron-plate,copper-plate -o circuits.txt --copy

It expands the recipe tree down to the items in --inputs, decides the machines of every recipe from the
rate and lays it out as a main bus: every input enters at the TOP on its own belt (left side), the belts
run down, and each recipe is a block (blocks.line / blocks.pair_line) to the right of the bus, stacked
top to bottom in production order. A block taps what it needs with splitters (the bus belts dive under
the tap rows), two items for one belt are merged one per lane by side-loading from both sides, and its
product goes back to a new bus belt on the right of the bus. A recipe whose only use is one consumer is
inserted directly (copper cable into circuits, gears into red science) when that fits. The final product
leaves on a belt at the right edge.

Anything not craftable from --inputs is an error that names what is missing. Fluids and furnaces are out
of scope: give the item that needs them (plastic-bar, sulfur, steel-plate, stone-brick, battery...) as an
input. It prints the size, where every input enters (column, item, per minute), where the product
leaves, machines per recipe, materials and research, then simulates it: VERDICT PASS|FAIL.

Options: --machine (assembling-machine-2), --belt (transport-belt, fast-..., express-...), --pole,
--minutes (simulated time; default grows with the chain), --label, --render, --no-test, --copy, --lang.

In a generator:  import chain;  r = chain.build(P, x, y, item, per_min, inputs)  places it in a bigger
plan and returns {'inputs': [(belt, item, per_min)], 'output': belt, 'width', 'height', 'machines'}.
"""
import argparse, math, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _imports():
    """layout/blocks may be mid-edit by someone else: retry a few times before giving up."""
    last = None
    for _ in range(5):
        try:
            import layout, blocks, bp_make, bp, fenv
            return layout, blocks, bp_make, bp, fenv
        except (SyntaxError, ImportError, AttributeError) as ex:
            last = ex
            time.sleep(2)
    raise last


layout, blocks, bp_make, bptool, fenv = _imports()
from layout import Plan, Collision, N, E, S, W  # noqa: E402,F401

HEADROOM = 0.9          # plan belts and lanes to 90 % of what they carry
# What one inserter really moves per minute in the lab (measured, all research): the blocks give each
# machine one inserter per belt (two on a single-belt line) and one for the product, so quick recipes
# (0.5 s: rails, pipes, gears) are inserter-bound and get more machines instead.
INS_ONE, INS_MIXED, INS_LONG = 135.0, 110.0, 40.0


class ChainError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------------------
# Recipe tree

def _recipe_for(item):
    pr = layout.protos()["recipes"]
    r = pr.get(item)
    if r and r["results"] and r["results"][0][0] == item:
        return item
    for name, r in pr.items():
        if any(x[0] == item for x in r["results"]) and "recycling" not in name and "barrel" not in name:
            return name
    return None


def expand(item, per_min, inputs, machine="assembling-machine-2"):
    """{'order': [items, producers before consumers], 'rate': {item: per min}, 'recipe': {item: name},
    'uses': {item: [consumer items]}} for everything between `inputs` and `item`."""
    inputs = set(inputs)
    cats = set(layout.ent_info(machine).get("categories", []))
    rec, uses, order, missing = {}, {}, [], []

    def visit(it, parent):
        if it in inputs:
            return
        if it in rec:
            return
        name = _recipe_for(it)
        why = None
        if name is None:
            why = "it has no recipe"
        else:
            r = layout.recipe(name)
            if any(t == "fluid" for _, _, t in r["ingredients"] + r["results"]):
                why = "its recipe uses a fluid"
            elif not cats & set(r["categories"]):
                why = f"its recipe needs {'/'.join(r['categories'])}, which {machine} can't do"
            elif len(r["ingredients"]) > 4:
                why = "its recipe has more than 4 ingredients"
        if why:
            missing.append(f"{it} ({why}{', needed by ' + parent if parent else ''})")
            return
        rec[it] = name
        for n, _, _ in layout.recipe(name)["ingredients"]:
            uses.setdefault(n, [])
            if it not in uses[n]:
                uses[n].append(it)
            visit(n, it)
        order.append(it)

    if item in inputs:
        raise ChainError(f"{item} is one of the inputs")
    visit(item, None)
    if missing:
        raise ChainError("missing input(s): add to --inputs " + "; ".join(missing))
    rate = {item: float(per_min)}
    for it in reversed(order):                      # consumers first
        r = layout.recipe(rec[it])
        out = next(a for n, a, _ in r["results"] if n == it)
        for n, amt, _ in r["ingredients"]:
            rate[n] = rate.get(n, 0) + rate[it] * amt / out
    unused = [i for i in inputs if i not in rate]
    return {"order": order, "rate": rate, "recipe": rec, "uses": uses, "unused": unused}


def _pair_items(tree, main, helper):
    """Belt items of pair_line(main, helper) in its order (helper's ingredients first), or None if it doesn't fit."""
    r, hr = layout.recipe(tree["recipe"][main]), layout.recipe(tree["recipe"][helper])
    items = []
    for nm, _, _ in hr["ingredients"] + [i for i in r["ingredients"] if i[0] != helper]:
        if nm not in items:
            items.append(nm)
    if len(items) > 3 or (len(items) == 3 and len(hr["ingredients"]) != 1):
        return None
    return items


def choose_pairs(tree, target, machine, banned=()):
    """{main item: helper item} for direct insertion (blocks.pair_line), biggest flows first."""
    rate, rec, uses = tree["rate"], tree["recipe"], tree["uses"]
    helper_of, is_helper = {}, set()
    cands = [h for h in tree["order"] if h != target and len(uses.get(h, [])) == 1 and (uses[h][0], h) not in banned]
    for h in sorted(cands, key=lambda h: -rate[h]):
        main = uses[h][0]
        if main in helper_of or main in is_helper or h in helper_of:
            continue
        if layout.recipe(rec[h])["results"][0][0] != h or _pair_items(tree, main, h) is None:
            continue
        # not if pairing 1:1 wastes far more machines than separate blocks would use
        each = layout.per_minute(rec[main], machine)[main]
        h_out = layout.per_minute(rec[h], machine)[h]
        m_need = layout.consumes_per_minute(rec[main], machine)[h]
        units = math.ceil(rate[main] / (each * min(1.0, h_out / m_need)) - 1e-6)
        separate = math.ceil(rate[main] / each - 1e-6) + math.ceil(rate[h] / h_out - 1e-6)
        if 2 * units > 2 * separate + 2:
            continue
        helper_of[main] = h
        is_helper.add(h)
    return helper_of


def plan_blocks(tree, it, per_min, helper, machine, belt):
    """The block(s) making `per_min` of `it`: [{'item', 'recipe', 'helper', 'n', 'needs': {item: per min},
    'per_min'}]. More than one when a single block's input lanes or output belt could not carry it."""
    rec = tree["recipe"]
    lane = layout.ent_info(belt)["items_per_s"] * 60 / 2
    r = layout.recipe(rec[it])
    amount = next(a for n, a, _ in r["results"] if n == it)
    each = layout.per_minute(rec[it], machine)[it]
    needs = {}
    for n, amt, _ in r["ingredients"]:
        if n == helper:
            hr = layout.recipe(rec[helper])
            hamount = next(a for nn, a, _ in hr["results"] if nn == helper)
            for hn, hamt, _ in hr["ingredients"]:
                needs[hn] = needs.get(hn, 0) + per_min * amt / amount * hamt / hamount
        else:
            needs[n] = needs.get(n, 0) + per_min * amt / amount
    full = layout.consumes_per_minute(rec[it], machine)
    slow = min(1.0, INS_MIXED / each)                       # the output inserter
    if helper:
        h_out = layout.per_minute(rec[helper], machine)[helper]
        each *= min(1.0, h_out / full[helper])
        hfull = layout.consumes_per_minute(rec[helper], machine)
        mine = [v for k, v in full.items() if k != helper]
        if mine:
            slow = min(slow, (INS_ONE if len(mine) == 1 else INS_MIXED) / sum(mine))
        hcap = INS_LONG if len(needs) == 3 else INS_ONE if len(hfull) == 1 else INS_MIXED
        slow = min(slow, hcap / sum(hfull.values()) * h_out / full[helper])
        n = math.ceil(per_min / (each * slow) - 1e-6)
    else:
        amounts = sorted(full.values())
        outer, inner = (amounts[:len(amounts) - 2], amounts[-2:]) if len(amounts) > 2 else ([], amounts)
        one = INS_ONE if len(inner) == 1 else INS_MIXED
        if outer:
            slow = min(slow, INS_LONG / sum(outer), one / sum(inner))
            n = math.ceil(per_min / (each * slow) - 1e-6)
        else:
            n = math.ceil(per_min / (each * min(slow, 2 * one / sum(inner))) - 1e-6)
            if n <= 2:                                      # it will be a column: one input inserter
                n = math.ceil(per_min / (each * min(slow, one / sum(inner))) - 1e-6)
    k = 1
    lim = HEADROOM * lane * (1 if len(needs) > 1 else 2)
    while not (all(v / k <= lim for v in needs.values()) and per_min / k <= HEADROOM * lane * 2):
        k += 1
    out, left = [], n
    for j in range(k):
        nj = math.ceil(left / (k - j))
        left -= nj
        b = {"item": it, "recipe": rec[it], "helper": helper, "n": nj, "per_min": per_min * nj / n,
             "needs": {a: v * nj / n for a, v in needs.items()},
             "rechelper": rec[helper] if helper else None, "outer": None}
        if helper and len(needs) == 3:
            b["outer"] = layout.recipe(rec[helper])["ingredients"][0][0]
        # one or two machines with one input belt: a single column (belt, machine, output belt)
        b["kind"] = "column" if (not helper and nj <= 2 and len(needs) <= 2) else "line"
        out.append(b)
    return out


# ---------------------------------------------------------------------------------------------------------
# Layout: bus on the left, blocks on the right

class _Retry(Exception):
    def __init__(self, block, why):
        Exception.__init__(self, why)
        self.block = block


def _make_block(P, x, y, b, order, machine, belt, pole):
    """Places block b with its belts carrying `order`: [inner lane, inner lane, outer, outer]."""
    m = len(order)
    if b["kind"] == "column":
        res = blocks.column(P, x, y, b["recipe"], b["n"], machine=machine, out="belt", belt=belt, pole=pole)
        mw, mh = layout.size(machine)
        res.update(top=y, width=mw + 4, height=b["n"] * mh)
        return res
    if b["helper"]:
        lanes = order[0] if m == 1 else f"{order[0]}|{order[1]}"
        return blocks.pair_line(P, x, y, b["recipe"], b["n"], b["rechelper"], machine=machine, belt=belt, pole=pole,
                                lanes=lanes, split=m < 3)
    lanes = [order[0] if m == 1 else f"{order[0]}|{order[1]}"]
    if m == 3:
        lanes.append(order[2])
    elif m == 4:
        lanes.append(f"{order[2]}|{order[3]}")
    return blocks.line(P, x, y, b["recipe"], b["n"], machine=machine, belt=belt, pole=pole, lanes=lanes,
                       split=m < 3)


def _design(item, per_min, inputs, machine, belt, banned):
    """(tree, blocks in building order, bus belts left to right, bus width)."""
    tree = expand(item, per_min, inputs, machine)
    cap = layout.ent_info(belt)["items_per_s"] * 60 * HEADROOM
    helper_of = choose_pairs(tree, item, machine, banned)
    helpers = set(helper_of.values())
    by_item, lanes_of, made = {}, {}, []

    def group(it):
        """Bus belts for `it`: its consumer blocks packed into as few belts as carry them."""
        g = []
        for b in made:
            if it in b["needs"]:
                fit = [l for l in g if l["load"] + b["needs"][it] <= cap + 1e-6]
                if not fit:
                    g.append({"item": it, "raw": it in inputs, "load": 0.0, "users": []})
                    fit = [g[-1]]
                fit[0]["load"] += b["needs"][it]
                fit[0]["users"].append(b)
                b.setdefault("lane", {})[it] = fit[0]
        lanes_of[it] = g
        return g

    for it in reversed(tree["order"]):              # consumers first: they decide the bus belts of each item
        if it in helpers:
            continue
        if it == item:
            by_item[it] = plan_blocks(tree, it, per_min, helper_of.get(it), machine, belt)
            for b in by_item[it]:
                b["to"] = None
        else:
            by_item[it] = []
            for l in group(it):
                bl = plan_blocks(tree, it, l["load"], helper_of.get(it), machine, belt)
                for b in bl:
                    b["to"] = l
                l["sources"] = len(bl)
                by_item[it] += bl
        made += by_item[it]
    raws = [i for i in inputs if i in tree["rate"]]
    for it in raws:
        for l in group(it):
            l["sources"] = 1
    blks = [b for it in tree["order"] if it in by_item for b in by_item[it]]
    lanes = [l for it in raws + [i for i in tree["order"] if i in by_item and i != item] for l in lanes_of[it]]
    x = 0
    for l in lanes:
        l["taps"] = sorted(k for k, b in enumerate(blks) if any(b is u for u in l["users"]))
        l["x"] = x
        l["w"] = 1 if (len(l["taps"]) == 1 and l["sources"] == 1) else 2
        x += l["w"]
    return tree, blks, lanes, x


def _tap_order(b, natural):
    """Items of block b in tap-row order (top first): the first two share the inner belt, the rest go on the
    outer one (long-handed inserters, ~1/s at best). Natural = by bus position, which needs no crossings."""
    nat = sorted(b["needs"], key=lambda i: -b["lane"][i]["x"])
    m = len(nat)
    if m <= 2 or (natural and not b["outer"]):
        return nat
    if b["outer"]:
        outer = [b["outer"]]
    else:
        load = {i: b["needs"][i] / b["n"] / 60 for i in nat}          # items/s per machine
        if sum(load[i] for i in nat[2:]) <= 0.6:
            return nat
        outer = sorted(nat, key=lambda i: load[i])[:m - 2]
    return [i for i in nat if i not in outer] + [i for i in nat if i in outer]


def build(P, x, y, item, per_min, inputs, machine="assembling-machine-2", belt="transport-belt",
          pole="small-electric-pole"):
    """Places the whole chain for `per_min` of `item` with its top-left tile at (x, y) and registers its test
    (feeds, sink, expected rate). Returns {'inputs': [(belt entity, item, per_min)], 'output': belt entity,
    'width', 'height', 'machines': {recipe: n}, 'blocks': [...], 'rate': {item: per min}}."""
    inputs = [i.strip() for i in (inputs.split(",") if isinstance(inputs, str) else inputs) if i.strip()]
    for i in inputs + [item]:
        if i not in layout.protos()["items"] and _recipe_for(i) is None:
            raise ChainError(f"unknown item '{i}' (data.py find {i})")
    banned, natural = set(), set()
    spec0 = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in P.spec.items()}
    notes0 = list(P.notes)
    for _ in range(40):
        snap = P.snapshot()
        mine = {id(e) for e in P.ents}
        add = P.add

        def guarded(name, gx, gy, d=None, **kw):
            # a block's own pole may stand where the glue belts go: drop it, power() at the end re-covers
            if not P._chain_block:
                for t in P.tiles_of(name, gx, gy, N if d is None else d):
                    o = P.occ.get(t)
                    if (o is not None and not o.get("_external") and id(o) not in mine
                            and layout.ent_info(o["name"])["type"] == "electric-pole"):
                        P.remove(o)
            return add(name, gx, gy, d, **kw)
        P._chain_block = False
        P.add = guarded
        try:
            return _build(P, x, y, item, per_min, inputs, machine, belt, pole, banned, natural, spec0, notes0)
        except _Retry as ex:
            P.restore(snap)
            P.notes[:] = notes0
            b = ex.block
            if b["item"] not in natural:
                natural.add(b["item"])              # lay its taps in bus order (no crossings)
            elif b["helper"]:
                banned.add((b["item"], b["helper"]))  # ... and if that can't be, no direct insertion there
            else:
                raise ChainError(f"{b['recipe']}: {ex}")
        finally:
            P.__dict__.pop("add", None)
            P.__dict__.pop("_chain_block", None)
    raise ChainError("could not lay out the bus")


def _build(P, x, y, item, per_min, inputs, machine, belt, pole, banned, natural, spec0, notes0):
    tree, blks, lanes, bus_w = _design(item, per_min, inputs, machine, belt, banned)
    ug = belt.replace("transport-belt", "underground-belt")
    sp = belt.replace("transport-belt", "splitter")
    reach = layout.ent_info(ug).get("max_distance", 5)
    x0 = x + bus_w + 2
    for g in lanes:
        g["x"] += x
        g["y"] = None                 # next row to lay; None = not started (or finished)
        g["left"] = list(g["taps"])
    for b in blks:                    # dry run of every block: height of its head and its width
        b["order"] = _tap_order(b, b["item"] in natural)
        res = _make_block(Plan(), 0, 0, b, b["order"], machine, belt, pole)
        b["head"], b["w"] = -res["top"], res["width"]
    nfinal = sum(1 for b in blks if b["item"] == item)
    xt = x0 + max(b["w"] for b in blks) + (1 if nfinal > 1 else 0)      # the product belt at the right edge
    ins = []
    for g in lanes:
        if g["raw"]:
            head = P.add(belt, g["x"], y, S)
            g["y"] = y + 1
            ins.append((head, g["item"], g["load"]))

    def fill(g, upto):
        """Plain belts down the bus belt g up to row `upto` (included)."""
        if g["y"] is not None and g["y"] <= upto:
            P.belt([(g["x"], g["y"]), (g["x"], upto)], end=S, kind=belt)
            g["y"] = upto + 1

    def row_east(b, sx, hx, row):
        """Belts flowing east on `row` from sx to hx - 1, under whatever stands in the way."""
        xx = sx
        while xx < hx:
            if xx + 1 < hx and not P.free(xx + 1, row):
                x2 = xx + 2
                while x2 < hx and not P.free(x2, row):
                    x2 += 1
                if xx == sx or x2 - xx > reach or x2 >= hx or not P.free(x2 + 1, row):
                    raise _Retry(b, f"a tap row can't cross the bus at column {xx + 1 - x}")
                P.underground(xx, row, E, x2 - xx, kind=ug)
                xx = x2 + 1
            else:
                P.add(belt, xx, row, E)
                xx += 1

    free_y = y + 1                 # first row the next block may use
    machines, output, trunk = {}, None, None
    for bi, b in enumerate(blks):
        order = b["order"]
        m = len(order)
        if m + 1 > reach:
            raise ChainError(f"{b['recipe']}: {m} tap rows are more than a {ug} crosses")
        tapped = [b["lane"][i] for i in order]
        leftmost = min(g["x"] for g in tapped)
        own = b["to"]
        join = own is not None and own["y"] is not None       # a second block making the same thing
        split = any(len(g["left"]) > 1 for g in tapped)
        dive = own is not None or any(g["y"] is not None and g["x"] > leftmost and
                                      (len(g["left"]) > 1 if g in tapped else g["left"]) for g in lanes)
        out_row = free_y if own else None             # the product goes west along this row to its bus belt
        r = free_y + (1 if own else 0) + (1 if join else 0) + (2 if split else 1 if dive else 0)   # first tap row
        top = r + {1: 0, 2: 2, 3: 8, 4: 10}[m]       # 3-4 items: top = first row of the machines (own head)
        P._chain_block = True
        try:
            res = _make_block(P, x0, top + b["head"], b, order, machine, belt, pole)
        finally:
            P._chain_block = False
        if res["top"] != top:
            raise Collision(f"{b['recipe']}: the head came out at row {res['top']}, planned {top}")
        b["res"] = res
        cx = res["output"]["_tile"][0]
        for mm in res["machines"]:
            machines[mm["recipe"]] = machines.get(mm["recipe"], 0) + 1
        # ---- product: to its bus belt, or out at the right edge
        if own is None:
            e = top - 1 if m < 3 else top - 5         # the row it leaves on (above the right-hand input belts)
            if trunk is None:
                run = P.belt([(cx, top - 1), (cx, e), (xt, e)], end=E if nfinal == 1 else S, kind=belt)
                trunk = {"x": xt, "y": e + 1}
                output = run[-1]
            else:                   # a splitter with one free output joins both belts, lane by lane
                fill(trunk, e)
                P.belt([(cx, top - 1), (cx, e), (xt - 1, e)], end=S, kind=belt)
                P.splitter(xt - 1, e + 1, S, kind=sp)
                output = P.add(belt, xt, e + 2, S)
                trunk["y"] = e + 3
        elif not join:
            P.belt([(cx, top - 1), (cx, out_row), (own["x"], out_row)], end=S, kind=belt)
            own["y"] = out_row + 1
        else:
            fill(own, out_row)
            P.belt([(cx, top - 1), (cx, out_row), (own["x"] + 1, out_row)], end=S, kind=belt)
            P.splitter(own["x"], out_row + 1, S, kind=sp)
            own["y"] = out_row + 2
        # ---- taps: a splitter (or the end of the bus belt), down to its row, east to the block's head.
        # Bus belts to the right of the leftmost tap go underground below the tap rows.
        heads = _head_paths(m, x0, r)
        rows = []
        for k, g in enumerate(tapped):
            ry = r + k
            g["left"].remove(bi)
            if g["left"]:
                fill(g, r - 3)
                P.splitter(g["x"], r - 2, S, kind=sp)
                P.belt([(g["x"] + 1, r - 1), (g["x"] + 1, ry - 1)], end=S, kind=belt)
                rows.append((g["x"] + 1, ry, heads[k]))
                if g["x"] > leftmost:
                    P.underground(g["x"], r - 1, S, m + 1, kind=ug)
                    g["y"] = r + m + 1
                else:
                    g["y"] = r - 1
            else:
                fill(g, ry - 1)
                rows.append((g["x"], ry, heads[k]))
                g["y"] = None
        for g in lanes:
            if g["y"] is not None and g not in tapped and g["x"] > leftmost and g["left"]:
                fill(g, r - 2)
                P.underground(g["x"], r - 1, S, m + 1, kind=ug)
                g["y"] = r + m + 1
        for sx, ry, (path, end) in rows:
            row_east(b, sx, path[0][0], ry)
            P.belt(path, end=end, kind=belt)
        if m >= 3:
            _head2(P, res["cols"], r, top, m, belt, ug, sp)
        free_y = top + res["height"] + 1
    # ---- the test: only the bus heads are fed, only the product leaves
    for k in ("feed", "sink", "expect"):
        P.spec[k] = dict(spec0[k]) if isinstance(spec0[k], dict) else list(spec0[k])
    for head, it, rate in ins:
        P.feed(head, it)
    P.sink(output)
    P.expect(item, per_min)
    P.power(pole)
    # the blocks' notes are about machines at full speed; here they run at the chain's pace
    P.notes[:] = [n for n in P.notes if n in notes0]
    bottom = max(t[1] for t, e in P.occ.items() if not e.get("_external") and t[0] >= x)
    return {"inputs": ins, "output": output, "width": xt + 1 - x, "height": bottom - y + 1, "machines": machines,
            "blocks": blks, "rate": tree["rate"], "unused": tree["unused"], "bus": lanes}


def _head_paths(m, x0, r):
    """For each tap row (top first): the corner tiles after it reaches its row, and where its last tile points.
    Two items for one belt end on both sides of the same tile (nothing behind it), one per lane."""
    if m == 1:
        return [([(x0 - 1, r)], E)]
    if m == 2:
        return [([(x0 + 1, r), (x0 + 1, r + 2)], W), ([(x0 - 1, r + 1), (x0 - 1, r + 2)], E)]
    inner = [([(x0 + 4, r), (x0 + 4, r + 2)], W), ([(x0 + 2, r + 1), (x0 + 2, r + 2)], E)]
    if m == 3:
        return inner + [([(x0, r + 2)], S)]
    return inner + [([(x0 + 1, r + 2), (x0 + 1, r + 4)], W), ([(x0 - 1, r + 3), (x0 - 1, r + 4)], E)]


def _head2(P, cols, r, y0, m, belt, ug, sp):
    """Head of a block with two input belts per side (3-4 items). The inner pair merges on column L1 + 3 and
    the outer belt comes down column L1; each is split in two, the right halves cross to the right side
    under the output belt. Every tile below a splitter is a plain belt (a sideways underground entrance
    there would take one lane only)."""
    x0, c = cols["L1"], cols["C"]
    t = y0 - 5                                              # row of the outer belt's splitter
    P.belt([(x0 + 3, r + 2), (x0 + 3, t - 1)], end=S, kind=belt) if t - 1 > r + 2 else P.add(belt, x0 + 3, r + 2, S)
    if m == 4:
        P.add(belt, x0, r + 4, S)
    # outer belt: left half straight down, right half east along row t + 1
    P.splitter(x0, t, S, kind=sp)
    P.belt([(x0, t + 1), (x0, y0 - 1)], end=S, kind=belt)
    P.belt([(x0 + 1, t + 1), (c - 2, t + 1)], end=E, kind=belt)
    P.underground(c - 1, t + 1, E, 2, kind=ug)
    P.belt([(c + 2, t + 1), (cols["R1"], t + 1), (cols["R1"], y0 - 1)], end=S, kind=belt)
    # inner belt: under that row, then split; left half one step west, right half east along row t + 4
    P.underground(x0 + 3, t, S, 2, kind=ug)
    P.splitter(x0 + 2, t + 3, S, kind=sp)
    P.belt([(x0 + 2, t + 4), (x0 + 1, t + 4)], end=S, kind=belt)
    P.belt([(x0 + 3, t + 4), (c - 2, t + 4)], end=E, kind=belt)
    P.underground(c - 1, t + 4, E, 2, kind=ug)
    P.belt([(c + 2, t + 4), (cols["R0"], t + 4)], end=S, kind=belt)


# ---------------------------------------------------------------------------------------------------------

def describe(P, res, label, item, per_min):
    from collections import Counter
    name = bp_make.loc_name
    x0, y0, x1, y1 = P.bounds()
    lines = [f"«{label}»: {x1 - x0 + 1}×{y1 - y0 + 1} tiles"]
    for e, it, rate in res["inputs"]:
        tx, ty = e["_tile"]
        lines.append(f"  input at column {tx - x0 + 1} from the left, top row: {name(it)} [{it}] {rate:.0f}/min")
    tx, ty = res["output"]["_tile"]
    lines.append(f"  output at column {tx - x0 + 1} from the left, row {ty - y0 + 1} (right edge): "
                 f"{name(item)} {per_min:g}/min")
    for rec, n in res["machines"].items():
        prod = layout.recipe(rec)["results"][0][0]
        lines.append(f"  {name(prod)} [{rec}]: {n} machines, {res['rate'].get(prod, 0):.1f}/min")
    ents = [e for e in P.ents if not e.get("_external")]
    bom = Counter(e["name"] for e in ents)
    lines.append("  materials: " + ", ".join(f"{v} {name(k)}" for k, v in bom.most_common()))
    lines.append("  research: " + (", ".join(f"{name(t)} [{t}]" for t in bp_make.research_needed(ents)) or "nothing"))
    for u in res.get("unused", []):
        lines.append(f"  NOTE: input {u} is not needed")
    for n in P.notes:
        lines.append("  NOTE: " + n)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--item", required=True, help="what to make")
    ap.add_argument("--per-min", type=float, required=True)
    ap.add_argument("--inputs", required=True, help="comma list of the items that arrive on belts")
    ap.add_argument("-o", required=True, help="output .txt (bare name: the player's blueprints folder)")
    ap.add_argument("--machine", default="assembling-machine-2")
    ap.add_argument("--belt", default="transport-belt")
    ap.add_argument("--pole", default="small-electric-pole")
    ap.add_argument("--minutes", type=float, help="simulated minutes (default: 4 + 1.5 per block, at most 20)")
    ap.add_argument("--label")
    ap.add_argument("--render", action="store_true", help="print an ASCII drawing")
    ap.add_argument("--no-test", action="store_true")
    ap.add_argument("--copy", action="store_true", help="copy to the clipboard if the test passes")
    ap.add_argument("--lang", help="locale for the names in the report (es-ES, de, fr...; default: the game's)")
    a = ap.parse_args()
    if a.lang:
        bp_make.LANG = a.lang
    P = Plan()
    try:
        res = build(P, 0, 0, a.item, a.per_min, a.inputs, machine=a.machine, belt=a.belt, pole=a.pole)
    except (ChainError, Collision) as ex:
        sys.exit(f"chain.py: {ex}")
    P.spec["minutes"] = a.minutes or min(20, 4 + 1.5 * len(res["blocks"]))
    label = a.label or f"{bp_make.loc_name(a.item)} {a.per_min:g}/min"
    desc = describe(P, res, label, a.item, a.per_min)
    out = a.o
    if not os.path.isabs(out) and os.sep not in out:
        out = os.path.join(fenv.blueprints_dir() or ".", out)
    print(desc)
    if a.render:
        o, _ = P.blueprint(label)
        bptool.render(o["blueprint"])
    P.notes = []
    ok = P.save(out, label, desc.split("\n", 1)[1], icons=[a.item], test=not a.no_test)
    if ok and a.copy:
        bptool.copy(open(out).read().strip())
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
