#!/usr/bin/env python3
"""Factorio blueprint tool.

  bp.py decode  <src>            -> readable JSON on stdout
  bp.py encode  <json|->         -> blueprint string on stdout
  bp.py summary <src>            -> book/blueprint tree with version and entity counts
  bp.py render  <src> [--index 1.3] [--rows N]  -> ASCII drawing of one blueprint
  bp.py extract <src> --index 1.3 -> string of a single blueprint inside a book
  bp.py fetch   <factoriobin-url> [-o file] -> download the string (the whole book if there is one)
  bp.py copy    <src>            -> put the string on the clipboard and check it really got there

<src> can be a file, '-' (stdin) or 'clip' (the system clipboard).
--index uses 1-based positions inside nested books: "2" = 2nd blueprint, "4.3" = 3rd inside the 4th.
"""
import argparse, base64, collections, json, math, os, re, subprocess, sys, urllib.parse, urllib.request, zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fenv  # noqa: E402

UA = {"User-Agent": "factorio-skill (+https://github.com/AleCadz/factorio-skill)"}
MAX_JSON = 512 << 20   # decompressed size limit for a blueprint string
MAX_FETCH = 64 << 20   # download size limit


def read_src(src):
    if src == "-":
        return sys.stdin.read().strip()
    if src == "clip":
        return fenv.clipboard_paste().strip()
    return open(src).read().strip()


def decode(s):
    if not s.startswith("0"):
        raise SystemExit("This doesn't look like a blueprint string (it must start with '0').")
    d = zlib.decompressobj()
    raw = d.decompress(base64.b64decode(s[1:]), MAX_JSON)
    if d.unconsumed_tail:
        raise SystemExit(f"Blueprint expands to more than {MAX_JSON >> 20} MB: refusing (a zip bomb, or raise bp.MAX_JSON).")
    return json.loads(raw)


def encode(obj):
    return "0" + base64.b64encode(zlib.compress(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode(), 9)).decode()


def version_str(v):
    return f"{v >> 48}.{(v >> 32) & 0xFFFF}.{(v >> 16) & 0xFFFF}"


def version_int(major, minor, patch):
    return (major << 48) | (minor << 32) | (patch << 16)


def kind(o):
    for k in ("blueprint_book", "blueprint", "deconstruction_planner", "upgrade_planner"):
        if k in o:
            return k, o[k]
    return None, o


def children(book):
    # each child of a book carries its own "index" (0-based); sort by it
    return sorted(book.get("blueprints", []), key=lambda c: c.get("index", 0))


def pick(obj, index):
    if not index:
        return obj
    for part in str(index).split("."):
        k, body = kind(obj)
        if k != "blueprint_book":
            raise SystemExit(f"--index {index}: '{part}' points inside something that is not a book")
        kids = children(body)
        i = int(part) - 1
        if not 0 <= i < len(kids):
            raise SystemExit(f"--index {index}: the book has {len(kids)} items")
        obj = {kk: vv for kk, vv in kids[i].items() if kk != "index"}
    return obj


def summary(obj, depth=0, path=""):
    k, b = kind(obj)
    pad = "  " * depth
    prefix = f"[{path}] " if path else ""
    label = b.get("label", "")
    ver = version_str(b["version"]) if "version" in b else "?"
    if k == "blueprint_book":
        kids = children(b)
        print(f"{pad}{prefix}BOOK «{label}» v{ver} ({len(kids)} items)")
        for n, c in enumerate(kids, 1):
            summary(c, depth + 1, f"{path}.{n}" if path else str(n))
    elif k == "blueprint":
        ents = b.get("entities", [])
        c = collections.Counter(e["name"] for e in ents)
        q = {e.get("quality") for e in ents} - {None, "normal"}
        tiles = collections.Counter(t["name"] for t in b.get("tiles", []))
        print(f"{pad}{prefix}BLUEPRINT «{label}» v{ver}: {sum(c.values())} entities {dict(c)}"
              + (f" tiles {dict(tiles)}" if tiles else "") + (f" QUALITIES {q}" if q else ""))
    else:
        print(f"{pad}{prefix}{k or 'unknown'} «{label}»")


# Size (width, height) facing north; anything else is assumed 1x1.
SIZES = {
    "stone-furnace": (2, 2), "steel-furnace": (2, 2), "electric-furnace": (3, 3),
    "assembling-machine-1": (3, 3), "assembling-machine-2": (3, 3), "assembling-machine-3": (3, 3),
    "chemical-plant": (3, 3), "oil-refinery": (5, 5), "lab": (3, 3), "boiler": (3, 2),
    "steam-engine": (3, 5), "electric-mining-drill": (3, 3), "burner-mining-drill": (2, 2),
    "pumpjack": (3, 3), "offshore-pump": (1, 2), "splitter": (2, 1), "fast-splitter": (2, 1),
    "express-splitter": (2, 1), "medium-electric-pole": (1, 1), "big-electric-pole": (2, 2),
    "substation": (2, 2), "roboport": (4, 4), "radar": (3, 3), "storage-tank": (3, 3),
    "centrifuge": (3, 3), "beacon": (3, 3), "solar-panel": (3, 3), "accumulator": (2, 2),
    "gun-turret": (2, 2), "laser-turret": (2, 2), "rocket-silo": (9, 9), "steel-chest": (1, 1),
}
SYMBOL = {
    "transport-belt": None, "fast-transport-belt": None, "express-transport-belt": None,
    "underground-belt": "U", "fast-underground-belt": "U", "express-underground-belt": "U",
    "splitter": "S", "fast-splitter": "S", "express-splitter": "S",
    "inserter": "i", "burner-inserter": "b", "fast-inserter": "f", "long-handed-inserter": "l", "bulk-inserter": "k",
    "small-electric-pole": "+", "medium-electric-pole": "+", "big-electric-pole": "P", "substation": "P",
    "wooden-chest": "C", "iron-chest": "C", "steel-chest": "C",
    "stone-furnace": "F", "steel-furnace": "F", "electric-furnace": "F",
    "assembling-machine-1": "A", "assembling-machine-2": "A", "assembling-machine-3": "A",
    "electric-mining-drill": "D", "burner-mining-drill": "D", "pipe": "=", "pipe-to-ground": "u",
    "lab": "L", "boiler": "B", "steam-engine": "E", "chemical-plant": "Q", "oil-refinery": "R",
}


def render(bp, rows=None):
    ver = bp.get("version", 0)
    eight_way = (ver >> 48) < 2  # 1.x uses 8 directions (N0 E2 S4 W6); 2.0 uses 16 (N0 E4 S8 W12)
    arrows = {0: "^", 2: ">", 4: "v", 6: "<"} if eight_way else {0: "^", 4: ">", 8: "v", 12: "<"}
    east_west = {2, 6} if eight_way else {4, 12}
    grid, unknown = {}, set()
    for e in bp.get("entities", []):
        n, d = e["name"], e.get("direction", 0)
        w, h = SIZES.get(n, (1, 1))
        if d in east_west:
            w, h = h, w
        x0 = round(e["position"]["x"] - w / 2)
        y0 = round(e["position"]["y"] - h / 2)
        ch = SYMBOL.get(n, "?")
        if n not in SYMBOL:
            unknown.add(n)
        if ch is None:
            ch = arrows.get(d, "?")
        for i in range(w):
            for j in range(h):
                grid[(x0 + i, y0 + j)] = ch
    if not grid:
        print("(blueprint has no entities)")
        return
    xs = [p[0] for p in grid]; ys = [p[1] for p in grid]
    ymax = max(ys) if rows is None else min(max(ys), min(ys) + rows - 1)
    for y in range(min(ys), ymax + 1):
        print("".join(grid.get((x, y), ".") for x in range(min(xs), max(xs) + 1)))
    print("Legend: ^>v< belt  U underground  S splitter  i/b/f/l/k inserter  + pole  C chest  F furnace"
          "  A assembler  D drill  = pipe  ? other" + (f" ({', '.join(sorted(unknown))})" if unknown else ""))
    ins = collections.Counter((e["name"], e.get("direction", 0)) for e in bp.get("entities", []) if "inserter" in e["name"])
    if ins:
        print("Inserters (name, direction = side they PICK UP from):", dict(ins))


def fetch(url):
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or (u.hostname or "").lower() not in ("factoriobin.com", "www.factoriobin.com"):
        raise SystemExit("fetch only downloads from https://factoriobin.com/... links.")
    html = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read(MAX_FETCH).decode("utf-8", "replace")
    links = sorted(set(re.findall(r"https://cdn\.factoriobin\.com/perma/[^\"' ]+?\.txt", html)))
    if not links:
        raise SystemExit("Couldn't find the link to the blueprint .txt on that page.")
    # post /N links to blueprint N; the -0 suffix is usually the whole book
    candidates = [re.sub(r"-\d+\.txt$", "-0.txt", links[0])] + links
    for c in candidates:
        try:
            s = urllib.request.urlopen(urllib.request.Request(c, headers=UA), timeout=60).read(MAX_FETCH).decode().strip()
            decode(s)
            return s, c
        except Exception:
            continue
    raise SystemExit("Couldn't download a valid blueprint from: " + ", ".join(candidates))


def copy(s):
    decode(s)  # refuse to copy something that isn't a blueprint
    if not fenv.clipboard_name():
        raise SystemExit("No clipboard tool found (install wl-clipboard, xclip or xsel). Import the string from its file instead.")
    if not fenv.clipboard_copy(s):
        raise SystemExit(f"Copied with {fenv.clipboard_name()}, but reading the clipboard back gave something else.")
    print(f"On the clipboard ({len(s)} characters, checked by reading it back with {fenv.clipboard_name()}).")
    print("Paste it in-game with Ctrl+V. Copying anything else or taking a screenshot before that replaces it.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["decode", "encode", "summary", "render", "extract", "fetch", "copy"])
    ap.add_argument("src")
    ap.add_argument("--index")
    ap.add_argument("--rows", type=int)
    ap.add_argument("-o", "--output")
    a = ap.parse_args()

    if a.cmd == "fetch":
        s, used = fetch(a.src)
        if a.output:
            open(a.output, "w").write(s + "\n")
        print(f"Downloaded from {used}", file=sys.stderr)
        summary(decode(s))
        if not a.output:
            print(s)
        return
    if a.cmd == "encode":
        obj = json.load(sys.stdin if a.src == "-" else open(a.src))
        print(encode(obj))
        return
    if a.cmd == "copy":
        s = read_src(a.src)
        copy(encode(pick(decode(s), a.index)) if a.index else s)
        return

    obj = pick(decode(read_src(a.src)), a.index)
    if a.cmd == "decode":
        print(json.dumps(obj, indent=1, ensure_ascii=False))
    elif a.cmd == "summary":
        summary(obj)
    elif a.cmd == "extract":
        print(encode(obj))
    elif a.cmd == "render":
        k, b = kind(obj)
        if k != "blueprint":
            raise SystemExit("render needs a single blueprint; pick one inside the book with --index (see 'summary').")
        print(f"«{b.get('label', '')}» v{version_str(b.get('version', 0))}")
        render(b, a.rows)


if __name__ == "__main__":
    main()
