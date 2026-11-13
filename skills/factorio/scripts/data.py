#!/usr/bin/env python3
"""Look up the game's real prototypes (data.raw dumped with --dump-data and cached).

  data.py recipe <name>               -> ingredients, results, time, which technology unlocks it
  data.py tech <name> [--tree]        -> cost, prerequisites (full tree with --tree), what it unlocks
  data.py entity <name>               -> type and key fields (speed, energy, size, reach...)
  data.py raw <type> <name>           -> the whole prototype as JSON
  data.py name <id> [--lang es-ES]    -> localized name the player sees in game (default: game language, else en)
  data.py find <text>                 -> search prototype ids containing the text

Loads only the base game + installed DLC (none of the player's mods) and never touches the open game.
The first call takes a few seconds; later calls read the cache in $TMPDIR.
"""
import argparse, glob, json, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fenv  # noqa: E402


def cache_dir():
    v, _ = fenv.version()
    dlc = "+".join(sorted(fenv.installed_dlc())) or "base"
    return os.path.join(fenv.cache_base(), f"factorio-dump-{v}-{dlc}")


def dump_path():
    return os.path.join(cache_dir(), "write", "script-output", "data-raw-dump.json")


def dump():
    """data.raw as a dict (dumped once per game version and DLC set, then cached)."""
    fenv.require()
    path = dump_path()
    if not os.path.exists(path):
        d = cache_dir()
        os.makedirs(os.path.join(d, "mods"), exist_ok=True)
        os.makedirs(os.path.join(d, "write"), exist_ok=True)
        cfg = os.path.join(d, "config.ini")
        with open(cfg, "w") as f:
            f.write(f"[path]\nread-data={fenv.posix_path(fenv.read_data())}\nwrite-data={fenv.posix_path(os.path.join(d, 'write'))}\n")
        r = subprocess.run([fenv.EXE, "--config", cfg, "--mod-directory", os.path.join(d, "mods"), "--dump-data"],
                           capture_output=True, text=True, errors="replace")
        if r.returncode != 0 or not os.path.exists(path):
            raise SystemExit("--dump-data failed:\n" + r.stdout[-3000:] + r.stderr[-2000:])
    return json.load(open(path, encoding="utf-8"))


NOT_ENTITY = {"item", "recipe", "technology", "explosion", "corpse", "optimized-particle", "tips-and-tricks-item",
              "item-with-entity-data", "shortcut", "custom-input", "ammo", "capsule", "module", "tool", "fluid",
              "virtual-signal", "item-subgroup", "item-group", "achievement", "sound", "sprite", "particle-source"}


def find_proto(d, name, types=None):
    # the placed entity first; if none, any prototype with that name (item, fluid...)
    for prefer_entity in (True, False):
        for t, protos in d.items():
            if types and t not in types:
                continue
            if prefer_entity and (t in NOT_ENTITY or t.endswith("-achievement")):
                continue
            if name in protos:
                return t, protos[name]
    return None, None


def unlocked_by(d, recipe):
    return [t for t, tech in d.get("technology", {}).items()
            for e in tech.get("effects", []) or [] if e.get("type") == "unlock-recipe" and e.get("recipe") == recipe]


def tech_line(t):
    u = t.get("unit") or {}
    packs = [i[0] if isinstance(i, list) else i.get("name") for i in u.get("ingredients", [])]
    return f"{u.get('count', u.get('count_formula', '?'))} × {packs} ({u.get('time', '?')}s)" if u else f"trigger: {t.get('research_trigger')}"


def tree(d, name, depth=0, seen=None):
    seen = set() if seen is None else seen
    t = d["technology"].get(name)
    if not t:
        print("  " * depth + f"{name} (?)"); return
    mark = " (already listed)" if name in seen else ""
    print("  " * depth + f"{name}: {tech_line(t)}{mark}")
    if mark:
        return
    seen.add(name)
    for p in t.get("prerequisites", []) or []:
        tree(d, p, depth + 1, seen)


KEYS = ["type", "crafting_speed", "energy_usage", "energy_source", "mining_speed", "speed", "max_distance",
        "supply_area_distance", "maximum_wire_distance", "collision_box", "module_slots", "crafting_categories",
        "rotation_speed", "extension_speed", "pickup_position", "insert_position", "belt_speed", "max_health",
        "fluid_boxes_off_when_no_fluid_recipe", "researching_speed", "inputs", "result_inventory_size",
        "source_inventory_size", "energy_per_hit_point", "fuel_value", "stack_size"]


def locale(lang):
    table = {}
    for f in glob.glob(os.path.join(fenv.read_data(), "*", "locale", lang, "*.cfg")):
        section = ""
        for line in open(f, encoding="utf-8", errors="replace"):
            line = line.rstrip("\n")
            if line.startswith("["):
                section = line.strip("[]")
            elif "=" in line and not line.startswith(";"):
                k, v = line.split("=", 1)
                table.setdefault(k, []).append((section, v))
    return table


def default_lang():
    loc = fenv.locale() if fenv.ROOT else "auto"
    return "en" if loc == "auto" else loc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["recipe", "tech", "entity", "raw", "name", "find"])
    ap.add_argument("args", nargs="+")
    ap.add_argument("--tree", action="store_true")
    ap.add_argument("--lang", default=None, help="locale folder, e.g. en, es-ES, de, fr (default: the game's language)")
    a = ap.parse_args()
    fenv.require()
    if a.cmd == "name":
        lang = a.lang or default_lang()
        loc = locale(lang)
        if not loc:
            raise SystemExit(f"No locale '{lang}' under {fenv.read_data()}/*/locale/ (try en, es-ES, de, fr, pt-BR...).")
        for n in a.args:
            hits = loc.get(n, [])
            m = re.match(r"^(.*)-(\d+)$", n)
            if m and not any(sec == "technology-name" for sec, _ in hits):
                # leveled technologies without their own name: "automation-2" = name of "automation" + " 2"
                hits += [(sec, f"{v} {m.group(2)}") for sec, v in loc.get(m.group(1), []) if sec == "technology-name"]
            print(n, "→", "; ".join(f"{v} [{s}]" for s, v in hits) or "(no translation)")
        return
    d = dump()
    if a.cmd == "recipe":
        r = d["recipe"].get(a.args[0])
        if not r:
            raise SystemExit(f"No recipe '{a.args[0]}'. Try: data.py find {a.args[0]}")
        info = {k: r.get(k) for k in ("category", "energy_required", "ingredients", "results", "enabled")}
        info["energy_required"] = r.get("energy_required", 0.5)   # 0.5 s is the default
        info["category"] = r.get("category") or "crafting"
        print(json.dumps(info, ensure_ascii=False))
        print("unlocked by:", unlocked_by(d, a.args[0]) or ("available from the start" if r.get("enabled", True) else "?"))
    elif a.cmd == "tech":
        t = d["technology"].get(a.args[0])
        if not t:
            raise SystemExit(f"No technology '{a.args[0]}'. Try: data.py find {a.args[0]}")
        print(f"cost: {tech_line(t)}")
        print("prerequisites:", t.get("prerequisites"))
        print("effects:", [e.get("recipe") or e.get("type") for e in t.get("effects", []) or []])
        if a.tree:
            tree(d, a.args[0])
    elif a.cmd == "entity":
        t, p = find_proto(d, a.args[0], None)
        if not p:
            raise SystemExit(f"'{a.args[0]}' not found. Try: data.py find {a.args[0]}")
        print(f"[{t}]", json.dumps({k: p[k] for k in KEYS if k in p}, ensure_ascii=False))
    elif a.cmd == "raw":
        if len(a.args) < 2:
            raise SystemExit("usage: data.py raw <type> <name>")
        print(json.dumps(d[a.args[0]][a.args[1]], indent=1, ensure_ascii=False))
    elif a.cmd == "find":
        pat = a.args[0].lower()
        for t, protos in d.items():
            hits = [n for n in protos if pat in n.lower()]
            if hits:
                print(f"{t}: {', '.join(hits[:30])}")


if __name__ == "__main__":
    main()
