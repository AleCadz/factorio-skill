#!/usr/bin/env python3
"""Draw a blueprint as a top-down PNG, colored by entity type.

  bp_map.py blueprint.txt -o map.png [--px 3] [--index 2]

Meant for taking in whole bases exported with save_export.py (thousands of entities) at a glance.
Entity sizes come from the game's prototypes (data.py dump); if the game isn't available, a built-in
table is used and anything unknown is drawn 1x1. No external tools needed.
"""
import argparse, os, struct, sys, zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bp as bptool  # noqa: E402

COLORS = [  # (prefix or name, RGB color, legend)
    ("transport-belt", (230, 190, 40), "belt"), ("fast-transport-belt", (230, 60, 40), "belt"),
    ("underground-belt", (160, 130, 20), "belt"), ("splitter", (255, 230, 120), "belt"),
    ("inserter", (90, 200, 90), "inserter"), ("fast-inserter", (90, 200, 90), "inserter"),
    ("long-handed-inserter", (90, 200, 90), "inserter"), ("burner-inserter", (90, 200, 90), "inserter"),
    ("assembling-machine", (70, 120, 230), "assembler"), ("lab", (240, 240, 255), "lab"),
    ("stone-furnace", (220, 110, 40), "furnace"), ("steel-furnace", (220, 110, 40), "furnace"),
    ("electric-mining-drill", (140, 90, 50), "drill"), ("pumpjack", (120, 60, 160), "oil"),
    ("oil-refinery", (170, 80, 200), "oil"), ("chemical-plant", (190, 120, 220), "oil"),
    ("pipe", (110, 110, 130), "pipe"), ("pump", (110, 110, 130), "pipe"), ("storage-tank", (110, 110, 130), "pipe"),
    ("boiler", (200, 60, 60), "power"), ("steam-engine", (200, 60, 60), "power"), ("offshore-pump", (200, 60, 60), "power"),
    ("solar-panel", (40, 60, 120), "power"), ("accumulator", (40, 60, 120), "power"),
    ("small-electric-pole", (60, 60, 60), "pole"), ("medium-electric-pole", (60, 60, 60), "pole"),
    ("big-electric-pole", (60, 60, 60), "pole"), ("substation", (60, 60, 60), "pole"),
    ("stone-wall", (150, 150, 150), "wall"), ("gate", (150, 150, 150), "wall"),
    ("gun-turret", (255, 40, 40), "turret"), ("laser-turret", (255, 40, 40), "turret"),
    ("chest", (150, 100, 60), "chest"), ("radar", (0, 200, 200), "other"),
]
BG = (25, 30, 25)


def color(name):
    for key, c, _ in COLORS:
        if name == key or name.startswith(key) or (key == "chest" and name.endswith("chest")) or (key == "pipe" and name.startswith("pipe")):
            return c
    return (255, 0, 255)


def sizes():
    try:
        import data
        d = data.dump()
    except BaseException:  # no game, failed dump: fall back to the built-in table
        return dict(bptool.SIZES)
    out = {}
    for t, protos in d.items():
        for n, p in protos.items():
            cb = p.get("collision_box") if isinstance(p, dict) else None
            if cb and "item" not in t:
                w = round(cb[1][0] - cb[0][0] + 0.2); h = round(cb[1][1] - cb[0][1] + 0.2)
                out[n] = (max(1, w), max(1, h))
    return out


def write_png(path, width, height, rgb):
    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    stride = width * 3
    raw = b"".join(b"\x00" + bytes(rgb[y * stride:(y + 1) * stride]) for y in range(height))  # filter 0 per row
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src"); ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--px", type=int, default=3, help="pixels per tile"); ap.add_argument("--index")
    a = ap.parse_args()
    obj = bptool.pick(bptool.decode(bptool.read_src(a.src)), a.index)
    if bptool.kind(obj)[0] != "blueprint":
        raise SystemExit("Need a single blueprint; pick one inside the book with --index (see 'bp.py summary').")
    ents = obj["blueprint"].get("entities", [])
    if not ents:
        raise SystemExit("The blueprint has no entities.")
    eight = (obj["blueprint"].get("version", 0) >> 48) < 2
    ew = {2, 6} if eight else {4, 12}
    sz = sizes()
    rects = []
    for e in ents:
        w, h = sz.get(e["name"], (1, 1))
        if e.get("direction", 0) in ew:
            w, h = h, w
        x0 = round(e["position"]["x"] - w / 2); y0 = round(e["position"]["y"] - h / 2)
        rects.append((x0, y0, w, h, color(e["name"])))
    minx = min(r[0] for r in rects); miny = min(r[1] for r in rects)
    maxx = max(r[0] + r[2] for r in rects); maxy = max(r[1] + r[3] for r in rects)
    W, H, px = (maxx - minx), (maxy - miny), a.px
    img = bytearray(bytes(BG) * (W * px * H * px))
    # big things first, small ones after (poles and inserters on top)
    for x0, y0, w, h, c in sorted(rects, key=lambda r: -(r[2] * r[3])):
        line = bytes(c) * (w * px)
        for yy in range((y0 - miny) * px, (y0 - miny + h) * px):
            i = (yy * W * px + (x0 - minx) * px) * 3
            img[i:i + len(line)] = line
    write_png(a.output, W * px, H * px, img)
    seen = {}
    for key, c, label in COLORS:
        seen.setdefault(label, c)
    print(f"{a.output}: {W}x{H} tiles, top-left corner at ({minx}, {miny}) in blueprint coordinates")
    print("Legend: " + ", ".join(f"{l}=rgb{c}" for l, c in seen.items()) + ", magenta=other")


if __name__ == "__main__":
    main()
