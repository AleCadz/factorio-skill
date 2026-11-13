#!/usr/bin/env python3
"""Tests for layout.py / blocks.py (need the game installed for prototype sizes; skipped otherwise).
With --sim also simulates every preset in a headless game (about 30 s).

  python3 tests/test_layout.py [--sim]
"""
import os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "factorio", "scripts"))
import fenv  # noqa: E402

SIM = "--sim" in sys.argv
if SIM:
    sys.argv.remove("--sim")


@unittest.skipUnless(fenv.ROOT, "Factorio not installed")
class Layout(unittest.TestCase):
    def setUp(self):
        global Plan, layout, blocks, N, E, S, W
        import layout, blocks
        from layout import Plan, N, E, S, W

    def test_centres_and_sizes(self):
        P = Plan()
        self.assertEqual(P.add("assembling-machine-2", 0, 0)["position"], {"x": 1.5, "y": 1.5})
        self.assertEqual(P.add("stone-furnace", 3, 0)["position"], {"x": 4, "y": 1})
        self.assertEqual(P.add("splitter", 0, 3, S)["position"], {"x": 1, "y": 3.5})
        self.assertEqual(P.add("splitter", 3, 3, E)["position"], {"x": 3.5, "y": 4})

    def test_collision(self):
        P = Plan()
        P.add("assembling-machine-2", 0, 0)
        with self.assertRaises(layout.Collision):
            P.add("inserter", 2, 2, N)

    def test_belt_directions(self):
        P = Plan()
        b = P.belt([(0, 0), (2, 0), (2, 2)])
        self.assertEqual([e["direction"] for e in b], [E, E, S, S, S])

    def test_route_goes_under(self):
        P = Plan()
        P.block([(5, y) for y in range(-10, 10)])
        pcs = P.route((0, 0), (10, 0), E)
        self.assertTrue(any(e["name"] == "underground-belt" for e in pcs))
        self.assertEqual(pcs[-1]["position"], {"x": 10.5, "y": 0.5})

    def test_power_and_wires(self):
        P = Plan()
        for i in range(4):
            P.add("assembling-machine-2", i * 10, 0, recipe="iron-gear-wheel")
        P.power()
        self.assertEqual(P.unpowered(), [])
        obj, _ = P.blueprint("t")
        poles = [e for e in obj["blueprint"]["entities"] if e["name"] == "small-electric-pole"]
        self.assertEqual(len(obj["blueprint"]["wires"]), len(poles) - 1)   # one connected tree

    def test_raw_and_trains(self):
        P = Plan()
        r1 = P.raw("straight-rail", 1, 1, _rail=True)
        P.raw("straight-rail", 1, 1, _rail=True)                 # rails may overlap rails
        with self.assertRaises(layout.Collision):
            P.add("inserter", 0, 0, N)
        self.assertNotIn("_rail", P.blueprint("t")[0]["blueprint"]["entities"][0])
        cars = P.train([("locomotive", 1.0, 4.0, 0), ("cargo-wagon", 1.0, 11.0, 0)], [("A", "full"), ("B", "empty")])
        b = P.blueprint("t")[0]["blueprint"]
        loco = next(e["entity_number"] for e in b["entities"] if e["name"] == "locomotive")
        self.assertEqual(b["schedules"][0]["locomotives"], [loco])
        self.assertEqual(b["schedules"][0]["schedule"]["records"][1]["wait_conditions"][0]["type"], "empty")
        self.assertEqual(len(b["stock_connections"]), 2)
        self.assertEqual(cars[0]["items"][0]["id"]["name"], "coal")

    def test_absolute_cell_contains_the_base(self):
        P = Plan()
        P.add("assembling-machine-2", -400, -300, recipe="iron-gear-wheel")
        P.base_box = (-470, -20, 120, 400)
        b = P.blueprint("t", absolute=True)[0]["blueprint"]
        g = b["snap-to-grid"]["x"]
        ox, oy = P.grid_origin
        self.assertGreaterEqual(g, 1024)
        for x, y in ((-470, -20), (120, 400), (-400, -300)):       # any cursor on the base lands in the same cell
            self.assertTrue(ox <= x < ox + g and oy <= y < oy + g)
        e = b["entities"][0]["position"]
        self.assertEqual((e["x"] + ox, e["y"] + oy), (-398.5, -298.5))

    def test_uncharted_fails(self):
        P = Plan()
        P.add("assembling-machine-2", 0, -330, recipe="iron-gear-wheel")
        P.uncharted = {(0, -11)}
        self.assertEqual(len(P.in_uncharted()), 1)
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(P.save(os.path.join(d, "x.txt"), "t", test=False, quiet=True))

    def test_bounded_power_stays_local(self):
        P = Plan()
        P.ext_poles.append({"name": "medium-electric-pole", "position": {"x": 300.5, "y": 0.5}})
        P.add("assembling-machine-2", 0, 0, recipe="iron-gear-wheel")
        n = P.power("medium-electric-pole", bounds=(-2, -2, 5, 5))
        self.assertEqual(n, 1)                                      # no line of poles towards the far grid
        self.assertGreater(P.power("medium-electric-pole"), 20)     # the final call makes the link

    def test_block_blueprint(self):
        P = Plan()
        P.known.append((0, 0, 100, 100))
        bp_ = {"blueprint": {"entities": [{"entity_number": 1, "name": "transport-belt", "position": {"x": 10.5, "y": 10.5},
                                           "direction": E},
                                          {"entity_number": 2, "name": "roboport", "position": {"x": 20, "y": 20}}]}}
        P.block_blueprint(bp_, 0, 0)
        self.assertFalse(P.free(10, 10))
        self.assertIn((11, 10), P.fronts)
        self.assertEqual(len(P.ext_roboports), 1)

    def test_glue_blocks(self):
        P = Plan()
        t, a, b = blocks.tee(P, 5, 5, S)
        self.assertEqual((a["direction"], b["direction"]), (W, E))  # both point into the middle tile
        ct = blocks.chest_tail(P, 5, 6, S, 3)
        self.assertEqual(len(ct["chests"]), 6)
        st_tiles = blocks.plastic(P, 20, 0, 2)
        self.assertEqual(st_tiles["per_min"], 240)
        r = blocks.refineries(P, 40, 0, 2)
        self.assertEqual(r["gas_per_min"], 1080)

    def test_station_geometry(self):
        P = Plan()
        for y in range(-41, 4, 2):
            P.raw("straight-rail", 1, y, _rail=True)
        st = blocks.station(P, "X", N, 1, 1, -1, load=True)
        self.assertEqual(st["cars"][0][:3], ("locomotive", 1.0, 4.0))     # centre 3 tiles behind the stop
        self.assertEqual([c[2] for c in st["cars"][1:]], [11.0, 18.0, 25.0, 32.0])
        self.assertEqual(len(st["tiles"]), 24)
        self.assertEqual(st["belt_line"], -4)                              # rail 0..1, inserter -1, chest -2, inserter -3
        self.assertEqual(P.at(-2, 8)["bar"], 4)

    def test_blocks_build(self):
        for build in (lambda P: blocks.smelter(P, 0, 0, 24),
                      lambda P: blocks.smelter(P, 0, 0, 9, furnace="electric-furnace", ore="copper-ore"),
                      lambda P: blocks.line(P, 0, 0, "engine-unit", 6),
                      lambda P: blocks.pair_line(P, 0, 0, "electronic-circuit", 6, "copper-cable")):
            P = Plan()
            r = build(P)
            self.assertEqual(P.unpowered(), [])
            self.assertTrue(r["inputs"] and r["output"])

    def test_pipe_route(self):
        P = Plan()
        P.add("pipe", 0, 0)
        P.block([(5, y) for y in range(-5, 6)])
        P.add("pipe", 6, 6)                       # another fluid: the route must keep away from it
        pcs = P.pipe_route((1, 0), (12, 6))
        names = [e["name"] for e in pcs]
        self.assertIn("pipe-to-ground", names)
        for e in pcs:
            x, y = e["_tile"]
            self.assertFalse(abs(x - 6) + abs(y - 6) == 1 and e["name"] == "pipe")

    def test_find_free_and_mine(self):
        P = Plan()
        P.block([(x, y) for x in range(0, 10) for y in range(0, 10)])
        x, y = P.find_free(4, 4, near=(5, 5))
        self.assertTrue(all((x + i, y + j) not in P.occ for i in range(4) for j in range(4)))
        Q = Plan()
        Q.ore = {(x, y): ("stone", 100) for x in range(0, 6) for y in range(0, 7)}
        m = blocks.mine(Q, 0, 0, 4)                # pairs 3 and 4 (x 6..11) have no ore in reach
        self.assertEqual(len(m["drills"]), 6)
        self.assertEqual(Q.unpowered(), [])

    @unittest.skipUnless(SIM, "run with --sim")
    def test_presets_simulate(self):
        import bp_make
        d = tempfile.mkdtemp()
        for i, build in enumerate((lambda P: blocks.smelter(P, 0, 0, 16),
                                   lambda P: blocks.line(P, 0, 0, "engine-unit", 4),
                                   lambda P: blocks.pair_line(P, 0, 0, "electronic-circuit", 4, "copper-cable"))):
            P = Plan()
            build(P)
            self.assertTrue(P.save(os.path.join(d, f"t{i}.txt"), f"t{i}", test=True))


if __name__ == "__main__":
    unittest.main()
