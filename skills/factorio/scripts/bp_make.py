#!/usr/bin/env python3
"""Make and test a blueprint in one command (aim: a finished, simulated blueprint in a minute or two).

  bp_make.py smelter --ore iron-ore --n 24 -o fundicion.txt         # stone furnaces, ore|coal, split head
  bp_make.py smelter --ore copper-ore --furnace electric-furnace --n 16,32 -o cobre.txt   # book, 2 sizes
  bp_make.py line --recipe iron-gear-wheel --n 6 -o engranajes.txt  # assemblers around a central output
  bp_make.py line --recipe automation-science-pack --n 10 --machine assembling-machine-1 -o roja.txt
  bp_make.py line --recipe electronic-circuit --helper copper-cable --n 8 -o verdes.txt   # cable inserted directly
  bp_make.py rate electronic-circuit 120                            # machines, belts and raw input per minute
  bp_make.py test plan.txt [--minutes 5]                            # re-test a saved blueprint (uses plan.test.json)

smelter/line write the blueprint, its test spec, simulate it and print: size, where each input enters
(lanes and per minute), where the product leaves, rate measured vs expected, bill of materials, research
needed (in the game's language) and a VERDICT line. Options shared: --belt (transport-belt, fast-...,
express-...), --no-split (feed the left and right input belts separately), --pole, --label, --render,
--no-test, --copy (to the clipboard when it passes).

For anything these presets don't cover, write a generator with layout.Plan + blocks (see layout.py) and
finish it with P.save(path, label): that runs the same test.
"""
import argparse, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bp as bptool  # noqa: E402
import fenv  # noqa: E402


# ---------------------------------------------------------------------------------------------------------
# Test + verdict

def _parse_table(s):
    # serpent writes ["iron-plate"] = 3 but grenade = 3 for names that are plain identifiers
    return {(a or b): float(v) for a, b, v in re.findall(r'(?:\["([^"]+)"\]|\b([A-Za-z_]\w*))\s*=\s*([\d.]+)', s)}


def _reports(lines):
    out = []
    for l in lines:
        m = re.search(r"t=(\d+)s states=(\{.*?\}) recipes_completed=(\{.*?\}) collected_at_outputs=(\{.*?\}) "
                      r"in_chests=(\{.*?\}) unpowered_inserters=(\d+)", l)
        if m:
            out.append({"t": int(m.group(1)), "states": _parse_table(m.group(2)), "made": _parse_table(m.group(3)),
                        "out": _parse_table(m.group(4)), "chests": _parse_table(m.group(5)),
                        "unpowered": int(m.group(6))})
    return out


def _produced(rep):
    tot = {}
    for k, v in rep["out"].items():
        item = k.split(":", 1)[1]
        tot[item] = tot.get(item, 0) + v
    for k, v in rep["chests"].items():
        tot[k] = tot.get(k, 0) + v
    return tot


def judge(lines, errors, spec):
    """(passed, report lines)."""
    txt = "\n".join(lines)
    msgs, ok = [], True

    def grab(key):
        m = re.search(key + r"=(\S+)", txt)
        return m.group(1) if m else None
    fails = int(grab("build_failures") or 0)
    bad_ins = int(grab("misaligned_or_floating_inserters") or 0)
    power = grab("infinite_power")
    ents = re.search(r"entities (\{.*\})", txt)
    msgs.append(f"build: failures={fails} bad_inserters={bad_ins} power={power or 'n/a'}"
                + (" networks>1!" if "separate_electric_networks" in txt else ""))
    m = re.search(r"has (\d+) entities but (\d+) ghosts", txt)
    missing = int(m.group(1)) - int(m.group(2)) if m else 0
    lab_missing = spec.get("lab_missing", 0)
    if lab_missing:
        msgs.append(f"lab: {lab_missing} pieces need ore or water (drills, offshore pumps) and can't be built here; "
                    f"the save test checks them")
    fitted = bool(spec.get("align"))
    split_net = "separate_electric_networks" in txt
    if split_net and fitted:
        msgs.append("lab: several electric networks here; in the save they join through the existing poles "
                    "(checked by the save test)")
    ext = spec.get("external_targets", 0)
    if ext:
        msgs.append(f"lab: {ext} inserters serve machines of the save (not in the blueprint): checked by the save test")
    if fails or bad_ins > ext or power == "false" or (split_net and not fitted) or missing > lab_missing:
        ok = False
        msgs += [l for l in lines if ("ERROR" in l or "WARNING" in l) and not (fitted and "separate_electric" in l)
                 and not (lab_missing and "ghosts were placed" in l and missing <= lab_missing)
                 and not (ext and "drops into NOTHING" in l)][:10]
    reps = _reports(lines)
    if not reps:
        return False, msgs + ["no reports from the simulation"] + lines[-10:] + errors[-5:]
    last = reps[-1]
    states = ", ".join(f"{k.split(':')[0]} {k.split(':')[1]}×{int(v)}" for k, v in sorted(last["states"].items()))
    msgs.append(f"machines at t={last['t']}s: {states}")
    idle = {k: v for k, v in last["states"].items() if not k.endswith(":working")}
    if last["unpowered"] and fitted:
        msgs.append(f"lab: {last['unpowered']} inserters rely on the save's poles: checked by the save test")
    elif last["unpowered"]:
        ok = False
        msgs.append(f"unpowered inserters: {last['unpowered']}")
    # steady-state rate: from the report nearest half time to the last one
    half = min(reps, key=lambda r: abs(r["t"] - last["t"] / 2))
    dt = (last["t"] - half["t"]) / 60 or 1
    p1, p0 = _produced(last), _produced(half)
    rates = {k: (p1.get(k, 0) - p0.get(k, 0)) / dt for k in set(p1) | set(p0)}
    for item, want in (spec.get("expect") or {}).items():
        got = rates.get(item, 0)
        good = got >= 0.9 * want
        ok &= good
        msgs.append(f"{item}: {got:.1f}/min measured, {want:.1f}/min expected {'OK' if good else 'LOW'}")
    other = {k: v for k, v in rates.items() if k not in (spec.get("expect") or {}) and v > 0.5}
    if other:
        msgs.append("also leaving: " + ", ".join(f"{k} {v:.1f}/min" for k, v in sorted(other.items())))
    if idle and not ok:
        msgs.append("not working: " + ", ".join(f"{k}×{int(v)}" for k, v in idle.items()))
    if errors:
        ok = False
        msgs += ["GAME ERROR: " + e for e in errors[-5:]]
    return ok, msgs


def test_on_save(path, align, seconds=30, replaces=()):
    """Builds the blueprint on a copy of the player's save at its world position (absolute blueprints)."""
    import shutil, subprocess, tempfile
    obj = bptool.decode(bptool.read_src(path))
    _strip_grid(obj)       # save_bp_test places it by --align (entity 1 on its world position)
    tmpdir = tempfile.mkdtemp(dir=fenv.tmp_base())
    tmp = os.path.join(tmpdir, "bp.txt")
    open(tmp, "w").write(bptool.encode(obj))
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "save_bp_test.py"), tmp, "--align", align,
                            "--seconds", str(seconds), "--no-power"], capture_output=True, text=True)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    lines = (r.stdout + r.stderr).strip().splitlines()
    status = [l for l in lines if re.search(r"t=\d+s status=", l)]
    keep = [l for l in lines if not re.search(r"t=\d+s status=", l)]
    print("test on the save copy (their terrain, buildings and electric grid, no extra power):")
    for l in keep[:20]:
        print("  " + l)
    ok = True
    m = re.search(r"failures=(\d+)", r.stdout)
    rep_pos = {(float(x), float(y)) for x, y in replaces}
    blocked = []
    for l in lines:
        mm = re.search(r"not placed: (\S+) at \((-?[\d.]+), (-?[\d.]+)\)", l)
        if mm and (float(mm.group(2)), float(mm.group(3))) not in rep_pos:
            blocked.append(l.strip())
    if rep_pos:
        print(f"  {len(rep_pos)} piece(s) replace one of theirs (paste with Ctrl+Shift+click)")
    if not m or m.group(1) != "0" or blocked:
        ok = False
    if status:
        last = _parse_table(status[-1].split("status=", 1)[1])
        print("  at the end: " + ", ".join(f"{k} ×{int(v)}" for k, v in sorted(last.items())))
        dead = {k: v for k, v in last.items() if k.endswith(":no_power")}
        if dead:
            ok = False
            print("  NOT CONNECTED to their grid: " + ", ".join(dead))
    if re.search(r"mines NOTHING", r.stdout):
        ok = False
        print("  some drills have no ore under them")
    print(f"VERDICT {'PASS' if ok else 'FAIL'} (in the save)")
    return ok


def _strip_grid(obj):
    for k in ("snap-to-grid", "absolute-snapping", "position-relative-to-grid"):
        obj["blueprint"].pop(k, None)


def test(path, minutes=None, quiet=False, on_save=False):
    """Simulates the blueprint (or every blueprint of a book) at `path` with its .test.json. Prints the
    verdict; returns True if everything passed."""
    import bp_sim
    from layout import spec_path
    sp = spec_path(path)
    if not os.path.exists(sp):
        raise SystemExit(f"no test spec {sp}: write it with layout.Plan.save, or use bp_sim.py with --feed")
    spec_all = json.load(open(sp))
    obj = bptool.decode(bptool.read_src(path))
    jobs = []
    if "book" in spec_all:
        kids = bptool.children(obj["blueprint_book"])
        if len(kids) != len(spec_all["book"]):
            raise SystemExit(f"{sp} has {len(spec_all['book'])} tests but the book {len(kids)} blueprints")
        for i, (child, spec) in enumerate(zip(kids, spec_all["book"])):
            child = {k: v for k, v in child.items() if k != "index"}
            jobs.append((child, spec, f"#{i + 1} {child['blueprint'].get('label', '')}"))
    else:
        jobs.append((obj, spec_all, obj["blueprint"].get("label", "")))
    all_ok = True
    for o, spec, name in jobs:
        o = json.loads(json.dumps(o))
        _strip_grid(o)     # the lab test checks how it works, not where it lands
        bp_sim.apply_spec(o, spec)
        lines, errors = bp_sim.run(o, spec.get("research") or ["all"], minutes=minutes or spec.get("minutes", 3),
                                   sink=bool(spec.get("sink")), join_networks=bool(spec.get("align")))
        ok, msgs = judge(lines, errors, spec)
        all_ok &= ok
        print(f"test {name}:")
        for m in msgs:
            print("  " + m)
        print(f"VERDICT {'PASS' if ok else 'FAIL'}")
        if on_save and spec.get("align") and "book" not in spec_all:
            all_ok &= test_on_save(path, spec["align"], replaces=spec.get("replaces", []))
    return all_ok


# ---------------------------------------------------------------------------------------------------------
# Description for the player: inputs, output, materials, research

LANG = os.environ.get("FACTORIO_LANG")


def _lang():
    import data
    if LANG:
        return LANG
    lang = data.default_lang()
    if lang == "en" and fenv.ROOT and fenv.locale() == "auto":
        env = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "")[:5]
        cand = {"es": "es-ES", "pt": "pt-BR", "zh": "zh-CN"}.get(env[:2], env[:2])
        if cand and os.path.isdir(os.path.join(fenv.read_data(), "base", "locale", cand)):
            return cand
    return lang


_LOC = {}


def loc_name(id_):
    import data
    lang = _lang()
    if lang not in _LOC:
        _LOC[lang] = data.locale(lang)
    for sec in ("item-name", "entity-name", "recipe-name", "technology-name", "fluid-name"):
        for s, v in _LOC[lang].get(id_, []):
            if s == sec:
                return v
    m = re.match(r"^(.*)-(\d+)$", id_)
    if m:
        for s, v in _LOC[lang].get(m.group(1), []):
            if s == "technology-name":
                return f"{v} {m.group(2)}"
    return id_


def research_needed(entities):
    import layout
    pr = layout.protos()
    need = set()
    names = {e["name"] for e in entities}
    recs = {e["recipe"] for e in entities if e.get("recipe")}
    for n in names:
        r = pr["recipes"].get(n)
        if r and not r["enabled"]:
            need.update(r["unlocked_by"][:1])
    for n in recs:
        r = pr["recipes"].get(n)
        if r and not r["enabled"]:
            need.update(r["unlocked_by"][:1])
    return sorted(need)


def describe(P, info, label):
    """Text for the player (in their language where it's a game name)."""
    from collections import Counter
    ents = [e for e in P.ents if not e.get("_external")]
    x0, y0, x1, y1 = P.bounds()
    lines = [f"«{label}»: {x1 - x0 + 1}×{y1 - y0 + 1} tiles"]
    for e, lanes in P.spec["feed"]:
        tx, ty = e["_tile"]
        parts = " | ".join(loc_name(x) for x in lanes.split("|"))
        lines.append(f"  input at column {tx - x0 + 1} from the left, top row {ty - y0 + 1}: {parts}")
    for e in P.spec["sink"]:
        tx, ty = e["_tile"]
        lines.append(f"  output at column {tx - x0 + 1} from the left, row {ty - y0 + 1}")
    for b in info:
        ins = ", ".join(f"{loc_name(k)} {v:g}/min" for k, v in b.get("inputs_per_min", {}).items())
        lines.append(f"  {loc_name(b['product'])}: {b['per_min']:g}/min (consumes {ins})")
    bom = Counter(e["name"] for e in ents)
    lines.append("  materials: " + ", ".join(f"{v} {loc_name(k)}" for k, v in bom.most_common()))
    lines.append("  research: " + (", ".join(f"{loc_name(t)} [{t}]" for t in research_needed(ents)) or "nothing"))
    for n in P.notes:
        lines.append("  NOTE: " + n)
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------

def _build(kind, a, n):
    import blocks
    from layout import Plan
    P = Plan()
    common = dict(belt=a.belt, pole=a.pole, split=not a.no_split)
    if a.inserter:
        common["inserter"] = a.inserter
    if kind == "smelter":
        info = blocks.smelter(P, 0, 0, n, ore=a.ore, furnace=a.furnace, fuel=a.fuel, **common)
    elif getattr(a, "helper", None):
        common.pop("inserter", None)
        info = blocks.pair_line(P, 0, 0, a.recipe, n, a.helper, machine=a.machine, lanes=a.lanes, **common)
    else:
        lanes = a.lanes.split(",") if a.lanes else None
        info = blocks.line(P, 0, 0, a.recipe, n, machine=a.machine, lanes=lanes, **common)
    return P, info


def make(kind, a):
    sizes = [int(x) for x in str(a.n).split(",")]
    built = []
    for n in sizes:
        P, info = _build(kind, a, n)
        prod = info["product"]
        label = a.label or f"{loc_name(prod)} ×{n} {loc_name(a.furnace if kind == 'smelter' else a.machine)}"
        if len(sizes) > 1 and a.label:
            label = f"{a.label} ×{n}"
        desc = describe(P, [info], label)
        built.append((P, info, label, desc))
    out = a.o
    if not os.path.isabs(out) and os.sep not in out:
        out = os.path.join(fenv.blueprints_dir() or ".", out)
    if len(built) == 1:
        P, info, label, desc = built[0]
        obj, num = P.blueprint(label, desc.split("\n", 1)[1] if "\n" in desc else "")
        spec = P.test_spec(num)
    else:
        children, specs = [], []
        for i, (P, info, label, desc) in enumerate(built):
            o, num = P.blueprint(label, desc.split("\n", 1)[1])
            children.append({"index": i, **o})
            specs.append(P.test_spec(num))
        obj = {"blueprint_book": {"item": "blueprint-book", "label": a.label or built[0][2].split(" ×")[0],
                                  "blueprints": children, "active_index": 0,
                                  "version": children[0]["blueprint"]["version"]}}
        spec = {"book": specs}
    from layout import spec_path
    with open(out, "w") as f:
        f.write(bptool.encode(obj) + "\n")
    with open(spec_path(out), "w") as f:
        json.dump(spec, f, indent=1)
    print(f"saved {out}")
    for P, info, label, desc in built:
        print(desc)
        if a.render:
            o, _ = P.blueprint(label)
            bptool.render(o["blueprint"])
    ok = True
    if not a.no_test:
        ok = test(out, a.minutes)
    if ok and a.copy:
        bptool.copy(bptool.encode(obj))
    return ok


def rate(item, per_min, machine, furnace, depth=0, raw=None, seen=None):
    """Prints the production tree for `per_min` of `item`."""
    import layout
    pr = layout.protos()
    raw = {} if raw is None else raw
    rec = None
    if item in pr["recipes"] and pr["recipes"][item]["results"] and pr["recipes"][item]["results"][0][0] == item:
        rec = item
    else:
        for name, r in pr["recipes"].items():
            if any(x[0] == item for x in r["results"]) and "recycling" not in name and "barrel" not in name:
                rec = name
                break
    base = {"iron-ore", "copper-ore", "stone", "coal", "crude-oil", "water", "uranium-ore", "wood"}
    if item in base or rec is None:
        raw[item] = raw.get(item, 0) + per_min
        print("  " * depth + f"{per_min:8.1f}/min {loc_name(item)} [{item}] (raw)")
        return raw
    r = pr["recipes"][rec]
    cats = r["categories"]
    m = furnace if "smelting" in cats else machine
    if not any(c in layout.ent_info(m).get("categories", []) for c in cats):
        for cand in ("assembling-machine-2", "assembling-machine-3", "chemical-plant", "oil-refinery", "centrifuge",
                     "electric-furnace"):
            if any(c in layout.ent_info(cand).get("categories", []) for c in cats):
                m = cand
                break
    each = layout.per_minute(rec, m)[item]
    count = per_min / each
    print("  " * depth + f"{per_min:8.1f}/min {loc_name(item)} [{item}]: {count:.2f} × {m} ({each:g}/min each)")
    out_amount = next(a for n, a, _ in r["results"] if n == item)
    for n, amt, _ in r["ingredients"]:
        rate(n, per_min * amt / out_amount, machine, furnace, depth + 1, raw)
    if depth == 0:
        print("raw per minute: " + ", ".join(f"{loc_name(k)} {v:.1f}" for k, v in raw.items()))
        print("yellow belt = 900/min (450 per lane), red 1800, blue 2700")
    return raw


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--n", required=True, help="number of machines (comma list = a book with those sizes)")
        p.add_argument("-o", required=True, help="output .txt (bare name: the player's blueprints folder)")
        p.add_argument("--belt", default="transport-belt")
        p.add_argument("--pole", default="small-electric-pole")
        p.add_argument("--inserter", help="force the inserter type (default: chosen from the load)")
        p.add_argument("--no-split", action="store_true", help="feed both sides yourself (no splitter head)")
        p.add_argument("--label")
        p.add_argument("--render", action="store_true", help="print an ASCII drawing")
        p.add_argument("--no-test", action="store_true")
        p.add_argument("--minutes", type=float)
        p.add_argument("--copy", action="store_true", help="copy to the clipboard if the test passes")
    s = sub.add_parser("smelter")
    s.add_argument("--ore", default="iron-ore", help="what goes in: iron-ore, copper-ore, stone, iron-plate (steel)")
    s.add_argument("--furnace", default="stone-furnace")
    s.add_argument("--fuel", default="coal")
    common(s)
    ln = sub.add_parser("line")
    ln.add_argument("--recipe", required=True)
    ln.add_argument("--machine", default="assembling-machine-2")
    ln.add_argument("--lanes", help="override belt contents: 'a|b' or 'a|b,c|d' (inner belt first)")
    ln.add_argument("--helper", help="recipe of a machine placed above each one that inserts its product "
                                     "directly (copper-cable for electronic-circuit, iron-gear-wheel for red science)")
    common(ln)
    t = sub.add_parser("test")
    t.add_argument("file")
    t.add_argument("--minutes", type=float)
    t.add_argument("--on-save", action="store_true", help="also build it on a copy of their save (absolute blueprints)")
    r = sub.add_parser("rate")
    r.add_argument("item")
    r.add_argument("per_min", type=float)
    r.add_argument("--machine", default="assembling-machine-2")
    r.add_argument("--furnace", default="stone-furnace")
    for p in (s, ln, t, r):
        p.add_argument("--lang", help="locale for the names in the report (es-ES, de, fr...; default: the game's)")
    a = ap.parse_args()
    global LANG
    LANG = a.lang or LANG
    if a.cmd == "test":
        sys.exit(0 if test(a.file, a.minutes, on_save=a.on_save) else 1)
    if a.cmd == "rate":
        rate(a.item, a.per_min, a.machine, a.furnace)
        return
    sys.exit(0 if make(a.cmd, a) else 1)


if __name__ == "__main__":
    main()
