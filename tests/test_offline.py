#!/usr/bin/env python3
"""Tests that don't need Factorio installed: blueprint encoding, books, rendering, path detection.

  python3 tests/test_offline.py
"""
import contextlib, io, json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "factorio", "scripts"))
import bp, fenv  # noqa: E402


def write(path, text):
    with open(path, "w") as f:
        f.write(text)

BOOK = os.path.join(HERE, "..", "examples", "steel-modular-book.txt")


def read(path):
    with open(path) as f:
        return f.read().strip()


class Blueprints(unittest.TestCase):
    def test_roundtrip(self):
        s = read(BOOK)
        obj = bp.decode(s)
        self.assertEqual(bp.decode(bp.encode(obj)), obj)

    def test_version(self):
        v = bp.version_int(2, 0, 77)
        self.assertEqual(bp.version_str(v), "2.0.77")

    def test_pick_and_kind(self):
        book = bp.decode(read(BOOK))
        self.assertEqual(bp.kind(book)[0], "blueprint_book")
        second = bp.pick(book, "2")
        self.assertEqual(bp.kind(second)[0], "blueprint")
        self.assertIn("x24", second["blueprint"]["label"])
        with self.assertRaises(SystemExit):
            bp.pick(book, "9")

    def test_render_directions(self):
        ents = [{"entity_number": 1, "name": "transport-belt", "position": {"x": 0.5, "y": 0.5}, "direction": 4},
                {"entity_number": 2, "name": "inserter", "position": {"x": 1.5, "y": 0.5}, "direction": 12}]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bp.render({"entities": ents, "version": bp.version_int(2, 0, 77)})
        self.assertTrue(out.getvalue().startswith(">i"))

    def test_rejects_garbage(self):
        with self.assertRaises(SystemExit):
            bp.decode("hello")


class Paths(unittest.TestCase):
    def test_standalone_data_folder(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "bin", "x64"))
            os.makedirs(os.path.join(root, "config"))
            write(os.path.join(root, "config-path.cfg"),
                "config-path=__PATH__executable__/../../config\nuse-system-read-write-data-directories=false\n")
            write(os.path.join(root, "config", "config.ini"),
                "[path]\nread-data=__PATH__executable__/../../data\nwrite-data=__PATH__executable__/../..\n")
            exe = os.path.join(root, "bin", "x64", "factorio")
            self.assertEqual(fenv.find_data(root, exe), os.path.normpath(root))

    def test_system_data_folder(self):
        with tempfile.TemporaryDirectory() as root:
            write(os.path.join(root, "config-path.cfg"),
                "config-path=__PATH__system-write-data__/config\nuse-system-read-write-data-directories=true\n")
            old = os.environ.pop("FACTORIO_DATA", None)
            try:
                self.assertEqual(fenv.find_data(root, None), fenv._system_write_dir())
            finally:
                if old is not None:
                    os.environ["FACTORIO_DATA"] = old

    def test_env_override(self):
        os.environ["FACTORIO_DATA"] = "/somewhere/else"
        try:
            self.assertEqual(fenv.find_data("/x", None), os.path.abspath("/somewhere/else"))
        finally:
            del os.environ["FACTORIO_DATA"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
