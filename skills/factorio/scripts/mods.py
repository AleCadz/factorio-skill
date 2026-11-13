#!/usr/bin/env python3
"""Manage Factorio mods from the official portal (mods.factorio.com).

  mods.py list                       -> installed mods and their state in mod-list.json
  mods.py info <name> [...]          -> title, downloads, latest compatible version, dependencies
  mods.py search <text> [--top 25]   -> search titles/summaries of mods for this game version (cached a day)
  mods.py install <name> [...] [--dry-run]  -> download with required dependencies, verify sha1, enable
  mods.py enable|disable <name> [...]

Downloads use the portal username/token from player-data.json (the player logs in once from the game's
Mods menu). The token is never printed, never written anywhere and only ever sent to mods.factorio.com
over https (curl reads the URL from stdin, so it isn't in the process list either). Downloads go through
curl because the portal CDN answers 403 to urllib's User-Agent. Changes load when the game restarts.
"""
import argparse, glob, hashlib, json, os, re, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fenv  # noqa: E402

API = "https://mods.factorio.com/api/mods"
UA = {"User-Agent": "factorio-skill (+https://github.com/AleCadz/factorio-skill)"}
DLC = ("space-age", "quality", "elevated-rails")
BUILTIN = {"base", "core", *DLC}  # ship with the game / DLC


def MODS():
    return fenv.mods_dir()


def get_json(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA)))


def full(name):
    try:
        return get_json(f"{API}/{urllib.parse.quote(name)}/full")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def latest_release(d, fv):
    rel = [r for r in d.get("releases", []) if r["info_json"]["factorio_version"] == fv]
    return rel[-1] if rel else None


def parse_dep(dep):
    """'? foo >= 1.0' -> (prefix, name). Prefixes: '' required, '?' optional, '(?)' hidden, '!' incompatible, '~' no load order."""
    m = re.match(r"^\s*(\(\?\)|\?|!|~)?\s*([^<>=]+?)\s*(?:[<>=]=?\s*[\d.]+)?\s*$", dep)
    return (m.group(1) or ""), m.group(2).strip()


def mod_name(filename):
    return re.sub(r"_\d+\.\d+\.\d+(\.zip)?$", "", filename)


def installed(name):
    """Files/folders in the mods folder that are exactly this mod (foo, not foo_bar)."""
    return [f for f in glob.glob(os.path.join(MODS(), glob.escape(name) + "_*"))
            if mod_name(os.path.basename(f)) == name]


def check_release(name, r):
    """The portal's answer decides a file name on disk and a URL that carries the token: refuse odd ones."""
    fn, url = r.get("file_name", ""), r.get("download_url", "")
    if fn != os.path.basename(fn) or not fn.endswith(".zip") or mod_name(fn) != name:
        raise SystemExit(f"Unexpected file name from the portal for '{name}': {fn!r}")
    if not re.fullmatch(r"/download/[A-Za-z0-9_\-. %/]+", url) or ".." in url:
        raise SystemExit(f"Unexpected download URL from the portal for '{name}'.")


def download(url, dest):
    """curl reads the URL from stdin, so the token never appears in the process list."""
    cfg = f'url = "{url}"\n'
    cmd = ["curl", "-sfL", "--proto", "=https", "--proto-redir", "=https", "-o", dest, "-K", "-"]
    try:
        return subprocess.run(cmd, input=cfg, text=True).returncode == 0
    except FileNotFoundError:
        raise SystemExit("curl not found: install curl to download mods.")


def read_modlist():
    p = os.path.join(MODS(), "mod-list.json")
    if os.path.exists(p):
        return json.load(open(p))
    return {"mods": [{"name": "base", "enabled": True}]}


def write_modlist(ml):
    os.makedirs(MODS(), exist_ok=True)
    names = {m["name"] for m in ml["mods"]}
    for f in glob.glob(os.path.join(MODS(), "*.zip")) + [d for d in glob.glob(os.path.join(MODS(), "*")) if os.path.isdir(d)]:
        n = mod_name(os.path.basename(f))
        if n not in names:
            ml["mods"].append({"name": n, "enabled": True}); names.add(n)
    json.dump(ml, open(os.path.join(MODS(), "mod-list.json"), "w"), indent=2)


def creds():
    p = fenv.player_data_file()
    pd = json.load(open(p)) if os.path.exists(p) else {}
    u, t = pd.get("service-username"), pd.get("service-token")
    if not u or not t:
        raise SystemExit("No portal login in player-data.json: log in once from the game (Mods → log in), then retry.")
    return u, t


def restart_note(msg):
    if fenv.game_running():
        print(msg)


def cmd_list():
    ml = read_modlist()
    state = {m["name"]: m["enabled"] for m in ml["mods"]}
    files = sorted(os.path.basename(f) for f in glob.glob(os.path.join(MODS(), "*")) if not f.endswith((".json", ".dat")))
    running = fenv.game_running()
    print(f"Factorio {fenv.version()[0]}  DLC: {sorted(fenv.installed_dlc()) or 'none'}  "
          f"game running: {'unknown' if running is None else running}  mods folder: {MODS()}")
    for f in files:
        print(f"  {'✔' if state.get(mod_name(f), True) else '✘'} {f}")
    if not files:
        print("  (no mods installed)")


def cmd_info(names):
    _, fv = fenv.version()
    for n in names:
        d = full(n)
        if not d:
            print(f"{n}: DOES NOT EXIST on the portal"); continue
        r = latest_release(d, fv)
        deps = [x for x in (r["info_json"].get("dependencies", []) if r else []) if parse_dep(x)[1] not in BUILTIN]
        print(f"{n} | {d.get('title')} | {d.get('downloads_count')} downloads | "
              f"{(r['version'] + ' (' + r['released_at'][:10] + ')') if r else 'NO release for ' + fv} | deps: {deps}")
        print(f"    {d.get('summary', '').strip()[:200]}")


def cmd_search(text, top):
    _, fv = fenv.version()
    cache = os.path.join(fenv.cache_base(), f"factorio-mods-{fv}.json")
    if not os.path.exists(cache) or time.time() - os.path.getmtime(cache) > 86400:
        data = urllib.request.urlopen(urllib.request.Request(f"{API}?page_size=max&version={fv}", headers=UA)).read()
        open(cache, "wb").write(data)
    res = json.load(open(cache))["results"]
    words = [w.lower() for w in text.split()]
    hits = [m for m in res if all(w in (m["name"] + " " + m.get("title", "") + " " + m.get("summary", "")).lower() for w in words)]
    for m in sorted(hits, key=lambda m: -m.get("downloads_count", 0))[:top]:
        print(f"{m['name']} | {m.get('title')} | {m.get('downloads_count')} | {m.get('summary', '')[:130].strip()}".replace("\n", " "))
    print(f"({len(hits)} results)")


def cmd_install(names, dry):
    _, fv = fenv.version()
    dlc = fenv.installed_dlc()
    plan, seen, queue = [], set(), list(names)
    while queue:
        n = queue.pop(0)
        if n in seen or n in BUILTIN:
            if n in DLC and n not in dlc:
                raise SystemExit(f"A mod requires the '{n}' DLC, which is not installed.")
            continue
        seen.add(n)
        d = full(n)
        if not d:
            raise SystemExit(f"'{n}' does not exist on the portal.")
        r = latest_release(d, fv)
        if not r:
            raise SystemExit(f"'{n}' has no release for Factorio {fv}.")
        check_release(n, r)
        plan.append((n, r))
        for dep in r["info_json"].get("dependencies", []):
            pre, dn = parse_dep(dep)
            if pre in ("", "~"):
                queue.append(dn)
            elif pre == "!" and (installed(dn) or dn in names):
                print(f"WARNING: {n} is incompatible with {dn}, which is installed or requested.")
    for n, r in plan:
        have = [os.path.basename(h) for h in installed(n)]
        print(f"{'(already installed) ' if r['file_name'] in have else ''}{r['file_name']}")
    if dry:
        return
    u, t = creds()
    os.makedirs(MODS(), exist_ok=True)
    for n, r in plan:
        dest = os.path.join(MODS(), r["file_name"])
        if os.path.exists(dest) and hashlib.sha1(open(dest, "rb").read()).hexdigest() == r["sha1"]:
            continue
        part = dest + ".part"
        url = f"https://mods.factorio.com{r['download_url']}?username={urllib.parse.quote(u)}&token={urllib.parse.quote(t)}"
        ok = download(url, part)
        if not ok or hashlib.sha1(open(part, "rb").read()).hexdigest() != r["sha1"]:
            if os.path.exists(part):
                os.remove(part)
            # never print the URL: it carries the token
            raise SystemExit(f"Download of {r['file_name']} failed (curl)." if not ok
                             else f"Wrong sha1 for {r['file_name']}; download discarded.")
        for old in installed(n):
            if old.endswith(".zip"):
                os.remove(old)  # older versions of this same mod, only once the new one is verified
        os.replace(part, dest)
        print(f"OK {r['file_name']}")
    ml = read_modlist()
    for n, _ in plan:
        for m in ml["mods"]:
            if m["name"] == n:
                m["enabled"] = True
    write_modlist(ml)
    restart_note("The game is running: restart it to load the mods.")


def cmd_toggle(names, enabled):
    ml = read_modlist()
    for n in names:
        hit = [m for m in ml["mods"] if m["name"] == n]
        if not hit:
            print(f"{n}: not in mod-list.json"); continue
        hit[0]["enabled"] = enabled
    write_modlist(ml)
    restart_note("The game is running: restart it to apply the change (it may rewrite mod-list.json when it closes).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["list", "info", "search", "install", "enable", "disable"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--dry-run", action="store_true", help="install: only show what would be downloaded")
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    fenv.require()
    if a.cmd == "list":
        cmd_list()
    elif a.cmd == "info":
        cmd_info(a.args)
    elif a.cmd == "search":
        cmd_search(" ".join(a.args), a.top)
    elif a.cmd == "install":
        cmd_install(a.args, a.dry_run)
    else:
        cmd_toggle(a.args, a.cmd == "enable")


if __name__ == "__main__":
    main()
