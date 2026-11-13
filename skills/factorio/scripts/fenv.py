#!/usr/bin/env python3
"""Finds the Factorio install and the player's data folder, and runs isolated headless instances.

  fenv.py            -> print what was detected (install, data folder, version, DLC, tools); run it first
  fenv.py --json     -> the same as JSON

Every other script imports this module, so paths are resolved in one place:

  FACTORIO_ROOT  install folder (the one with data/ and bin/). Auto-detected: standalone zip in
                 ~/Games/factorio or ~/factorio, Steam libraries, the Windows installer, /Applications
                 on macOS, /opt and /usr/share on Linux.
  FACTORIO_DATA  the player's write-data folder (saves/, mods/, config/, player-data.json). Read from
                 config-path.cfg and config.ini like the game does: the install folder for the
                 standalone zip, ~/.factorio / %APPDATA%\\Factorio / ~/Library/Application Support/factorio
                 for Steam and installers.

Both can be overridden with environment variables of the same name. Temporary files go to $TMPDIR.
"""
import configparser, glob, json, os, platform, re, shutil, stat, subprocess, sys, tempfile

SYSTEM = platform.system()  # Linux, Windows, Darwin
IS_WSL = SYSTEM == "Linux" and "microsoft" in platform.release().lower()
HOME = os.path.expanduser("~")


def _steam_libraries():
    if SYSTEM == "Windows":
        steams = [os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Steam")]
    elif SYSTEM == "Darwin":
        steams = [os.path.join(HOME, "Library/Application Support/Steam")]
    else:
        steams = [os.path.join(HOME, p) for p in (".local/share/Steam", ".steam/steam", ".steam/root",
                                                    ".var/app/com.valvesoftware.Steam/.local/share/Steam")]
    libs = []
    for s in steams:
        libs.append(s)
        vdf = os.path.join(s, "steamapps", "libraryfolders.vdf")
        if os.path.exists(vdf):
            libs += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', _read(vdf))]
    out = []
    for lib in libs:
        game = os.path.join(lib, "steamapps", "common", "Factorio")
        out.append(os.path.join(game, "factorio.app") if SYSTEM == "Darwin" else game)
    return out


def _root_candidates():
    if SYSTEM == "Windows":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        c = [os.path.join(pf, "Factorio"), os.path.join(HOME, "Games", "Factorio"), os.path.join(HOME, "Games", "factorio")]
    elif SYSTEM == "Darwin":
        c = ["/Applications/factorio.app", os.path.join(HOME, "Applications/factorio.app")]
    else:
        c = [os.path.join(HOME, p) for p in ("Games/factorio", "Games/Factorio", "factorio", "Factorio")]
        c += ["/opt/factorio", "/usr/share/factorio"]
    return c + _steam_libraries()


def _normalize_root(path):
    path = os.path.abspath(os.path.expanduser(path))
    if path.endswith(".app"):
        path = os.path.join(path, "Contents")
    return path


def find_root():
    env = os.environ.get("FACTORIO_ROOT")
    if env:
        return _normalize_root(env)
    for c in _root_candidates():
        r = _normalize_root(c)
        if os.path.exists(os.path.join(r, "data", "base", "info.json")):
            return r
    return None


def find_exe(root):
    for rel in ("bin/x64/factorio", "bin/x64/factorio.exe", "MacOS/factorio"):
        p = os.path.join(root, rel)
        if os.path.exists(p):
            return p
    return None


def _system_write_dir():
    if SYSTEM == "Windows":
        return os.path.join(os.environ.get("APPDATA", os.path.join(HOME, "AppData", "Roaming")), "Factorio")
    if SYSTEM == "Darwin":
        return os.path.join(HOME, "Library/Application Support/factorio")
    return os.path.join(HOME, ".factorio")


def _read(path):
    with open(path, errors="replace") as f:
        return f.read()


def _resolve_tokens(value, exe, root):
    value = value.replace("__PATH__executable__", os.path.dirname(exe) if exe else os.path.join(root, "bin", "x64"))
    value = value.replace("__PATH__system-write-data__", _system_write_dir())
    value = value.replace("__PATH__system-read-data__", os.path.join(root, "data"))
    return os.path.normpath(value)


def find_data(root, exe):
    """The player's write-data folder, resolved the same way the game does."""
    env = os.environ.get("FACTORIO_DATA")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    cfgpath, use_system = None, True
    cp = os.path.join(root, "config-path.cfg")
    if os.path.exists(cp):
        for line in _read(cp).splitlines():
            k, _, v = line.strip().partition("=")
            if k == "config-path":
                cfgpath = _resolve_tokens(v, exe, root)
            elif k == "use-system-read-write-data-directories":
                use_system = v.strip().lower() == "true"
    candidates = []
    if cfgpath:
        candidates.append(os.path.join(cfgpath, "config.ini"))
    candidates.append(os.path.join(_system_write_dir() if use_system else root, "config", "config.ini"))
    for ini in candidates:
        if os.path.exists(ini):
            m = re.search(r"^write-data=(.+)$", _read(ini), re.M)
            if m:
                return _resolve_tokens(m.group(1).strip(), exe, root)
    return _system_write_dir() if use_system else root


ROOT = find_root()
EXE = find_exe(ROOT) if ROOT else None
DATA = find_data(ROOT, EXE) if ROOT else None


def require():
    """Exit with a clear message if the game can't be found."""
    if not ROOT:
        raise SystemExit("Factorio not found. Set FACTORIO_ROOT to the install folder (the one containing data/ and bin/).")
    if not EXE:
        raise SystemExit(f"No Factorio executable under {ROOT} (looked for bin/x64/factorio, MacOS/factorio).")


def read_data():
    return os.path.join(ROOT, "data")


def version():
    """('2.0.77', '2.0')"""
    v = json.load(open(os.path.join(read_data(), "base", "info.json")))["version"]
    return v, ".".join(v.split(".")[:2])


def installed_dlc():
    return {d for d in ("space-age", "quality", "elevated-rails") if os.path.isdir(os.path.join(read_data(), d))}


def mods_dir():
    return os.path.join(DATA, "mods")


def saves_dir():
    return os.path.join(DATA, "saves")


def player_data_file():
    return os.path.join(DATA, "player-data.json")


def config_ini():
    return os.path.join(DATA, "config", "config.ini")


def notes_file():
    """Per-player notes kept by Claude between sessions (outside the skill, so updates never touch it)."""
    return os.path.join(DATA, "claude-notes.md")


def blueprints_dir():
    return os.path.join(DATA, "blueprints")


def locale():
    """Game language from config.ini ('auto' if not set)."""
    try:
        m = re.search(r"^locale=(.+)$", open(config_ini(), errors="replace").read(), re.M)
        return m.group(1).strip() if m else "auto"
    except OSError:
        return "auto"


def latest_save():
    saves = glob.glob(os.path.join(saves_dir(), "*.zip"))
    if not saves:
        raise SystemExit(f"No saves in {saves_dir()}. Pass the save path explicitly.")
    return max(saves, key=os.path.getmtime)


def tmp_base():
    return os.environ.get("TMPDIR") or tempfile.gettempdir()


def cache_base():
    """A folder only this user can touch, for caches that are read back later. Predictable names straight
    in a shared temp folder would let another local user plant or symlink them."""
    uid = str(os.getuid()) if hasattr(os, "getuid") else re.sub(r"\W", "_", os.environ.get("USERNAME", "user"))
    d = os.path.join(tmp_base(), f"factorio-skill-{uid}")
    os.makedirs(d, mode=0o700, exist_ok=True)
    if hasattr(os, "getuid"):
        st = os.lstat(d)
        if stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise SystemExit(f"{d} is not a private folder of this user: remove it or set TMPDIR.")
    return d


def game_running():
    try:
        if SYSTEM == "Windows":
            r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq factorio.exe"], capture_output=True, text=True)
            return "factorio.exe" in r.stdout.lower()
        return subprocess.run(["pgrep", "-x", "factorio"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return None  # unknown


def posix_path(p):
    return os.path.abspath(p).replace("\\", "/")


def long_string(text):
    """A Lua long-bracket literal for any text (picks a level of '=' the text doesn't contain), so
    names and labels from a downloaded blueprint can never close the literal and run as Lua."""
    n = 0
    while ("]" + "=" * n + "]") in text:
        n += 1
    return "[" + "=" * n + "[" + text + "]" + "=" * n + "]"


def link_or_copy(src, dst):
    try:
        os.symlink(src, dst)
    except (OSError, NotImplementedError):  # Windows without symlink rights
        (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)


class Instance:
    """A throwaway headless Factorio: its own config, write-data and mod folder in a temp dir.

    Never touches the player's game, saves or settings, so it can run while the game is open.
    `control_lua` becomes a tiny helper mod called `name`. With user_mods=True the player's mods (and
    their enabled/disabled state and settings) are linked in, which is needed to load their saves.
    """

    def __init__(self, name, control_lua=None, user_mods=False, keep=False):
        require()
        self.name, self.keep = name, keep
        self.tmp = tempfile.mkdtemp(prefix=f"{name}-", dir=tmp_base())
        self.mods = os.path.join(self.tmp, "mods")
        self.write = os.path.join(self.tmp, "write")
        os.makedirs(self.mods)
        os.makedirs(self.write)
        self.config = os.path.join(self.tmp, "config.ini")
        with open(self.config, "w") as f:
            f.write(f"[path]\nread-data={posix_path(read_data())}\nwrite-data={posix_path(self.write)}\n")
        modlist = {"mods": [{"name": "base", "enabled": True}]}
        if user_mods and os.path.isdir(mods_dir()):
            for f in glob.glob(os.path.join(mods_dir(), "*")):
                if f.endswith("mod-settings.dat"):
                    shutil.copy2(f, os.path.join(self.mods, os.path.basename(f)))  # a copy: the game may rewrite it
                elif f.endswith(".zip") or os.path.isdir(f):
                    link_or_copy(f, os.path.join(self.mods, os.path.basename(f)))
            ml = os.path.join(mods_dir(), "mod-list.json")
            if os.path.exists(ml):
                modlist = json.load(open(ml))
        if control_lua is not None:
            d = os.path.join(self.mods, f"{name}_0.0.1")
            os.makedirs(d)
            with open(os.path.join(d, "info.json"), "w") as f:
                json.dump({"name": name, "version": "0.0.1", "title": name, "author": "claude-factorio-skill",
                           "factorio_version": version()[1], "dependencies": ["base"]}, f)
            with open(os.path.join(d, "control.lua"), "w", encoding="utf-8") as f:
                f.write(control_lua)
            modlist["mods"] = [m for m in modlist["mods"] if m["name"] != name] + [{"name": name, "enabled": True}]
        with open(os.path.join(self.mods, "mod-list.json"), "w") as f:
            json.dump(modlist, f)

    def run(self, *args, check=False):
        cmd = [EXE, "--config", self.config, "--mod-directory", self.mods, *args]
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        if check and r.returncode != 0:
            raise SystemExit(f"Factorio failed ({' '.join(args[:2])}):\n{r.stdout[-3000:]}\n{r.stderr[-2000:]}")
        return r

    def run_save(self, save, ticks):
        """Load a save (or a copy of one) and run it for `ticks` ticks without rendering."""
        return self.run("--benchmark", save, "--benchmark-ticks", str(int(ticks)), "--benchmark-runs", "1")

    def copy_save(self, save):
        dst = os.path.join(self.tmp, "copy.zip")
        shutil.copy2(save, dst)
        return dst

    def new_map(self):
        save = os.path.join(self.tmp, "new.zip")
        self.run("--create", save, check=True)
        return save

    def log(self):
        p = os.path.join(self.write, "factorio-current.log")
        return open(p, errors="replace").read() if os.path.exists(p) else ""

    def tagged(self, tag):
        """Lines the helper mod wrote with log("<tag> ..."), without the log prefix."""
        rx = re.compile(r"^\s*[\d.]+ Script @[^:]+:\d+: " + re.escape(tag) + " ?")
        return [rx.sub("", l) for l in self.log().splitlines() if tag in l and "Script @" in l]

    def errors(self, limit=20):
        return [l for l in self.log().splitlines() if "rror" in l and "Script @" not in l][:limit]

    def close(self):
        if self.keep:
            print(f"(temporary instance kept in {self.tmp})", file=sys.stderr)
        else:
            shutil.rmtree(self.tmp, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ---------------------------------------------------------------- clipboard

def _which(*names):
    return all(shutil.which(n) for n in names)


def _clip_tools():
    """(copy command, paste command) for this desktop, best first."""
    tools = []
    if SYSTEM == "Darwin":
        tools.append((["pbcopy"], ["pbpaste"]))
    elif SYSTEM == "Windows" or IS_WSL:
        ps = "powershell.exe" if IS_WSL else "powershell"
        tools.append((["clip.exe" if IS_WSL else "clip"], [ps, "-NoProfile", "-Command", "Get-Clipboard"]))
    else:
        if os.environ.get("WAYLAND_DISPLAY") and _which("wl-copy", "wl-paste"):
            tools.append((["wl-copy", "--type", "text/plain"], ["wl-paste", "--no-newline"]))
        if _which("xclip"):
            tools.append((["xclip", "-selection", "clipboard"], ["xclip", "-selection", "clipboard", "-o"]))
        if _which("xsel"):
            tools.append((["xsel", "--clipboard", "--input"], ["xsel", "--clipboard", "--output"]))
    return [t for t in tools if shutil.which(t[0][0])]


def clipboard_name():
    t = _clip_tools()
    return t[0][0][0] if t else None


def clipboard_paste():
    for _, paste in _clip_tools():
        r = subprocess.run(paste, capture_output=True, text=True, errors="replace")
        if r.returncode == 0:
            return r.stdout.strip()
    raise SystemExit("No clipboard tool found (install wl-clipboard, xclip or xsel). Save the string to a file instead.")


def clipboard_copy(text):
    """Copy and read back. Returns True only if the clipboard really holds the text."""
    for copy, _ in _clip_tools():
        if subprocess.run(copy, input=text, text=True).returncode == 0:
            try:
                return clipboard_paste() == text.strip()
            except SystemExit:
                return False
    return False


# ---------------------------------------------------------------- report

def report():
    info = {"system": SYSTEM + (" (WSL)" if IS_WSL else ""), "factorio_root": ROOT, "executable": EXE, "data_folder": DATA}
    if ROOT and os.path.exists(os.path.join(ROOT, "data", "base", "info.json")):
        info["version"] = version()[0]
        info["dlc"] = sorted(installed_dlc())
        info["game_language"] = locale()
        info["mods_folder"] = mods_dir()
        info["saves"] = len(glob.glob(os.path.join(saves_dir(), "*.zip")))
        info["portal_login"] = bool(os.path.exists(player_data_file()) and
                                    json.load(open(player_data_file())).get("service-token"))
        info["notes_file"] = notes_file() + ("" if os.path.exists(notes_file()) else "  (not created yet)")
        info["blueprints_folder"] = blueprints_dir()
        info["local_api_docs"] = os.path.exists(os.path.join(ROOT, "doc-html", "runtime-api.json"))
        info["game_running"] = game_running()
    info["clipboard"] = clipboard_name() or "none (strings will be saved to files only)"
    info["temp_folder"] = tmp_base()
    info["python"] = sys.version.split()[0]
    return info


if __name__ == "__main__":
    r = report()
    if "--json" in sys.argv:
        print(json.dumps(r, indent=1))
    else:
        for k, v in r.items():
            print(f"{k:18} {v}")
        if not ROOT:
            print("\nFactorio was not found. Set FACTORIO_ROOT to the install folder and try again.")
