"""Checks the index entries (mods/<id>/<version>.toml) and, with --download, the zips they point at.

    python scripts/check.py                       every entry: schema, path, duplicates
    python scripts/check.py --base origin/main --download
                                                  a pull request: what changed against the base, each new entry's zip
    python scripts/check.py --download --package-check "openIG2-check --package {dir}"
                                                  also runs the format checks on each unpacked zip

Exit code 0 when everything passes, 1 otherwise. Messages go to stdout (one line each, the entry's path first).
"""
import argparse
import hashlib
import io
import json
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

import fastjsonschema

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema" / "entry.schema.json").read_text(encoding="utf-8"))
validate = fastjsonschema.compile(SCHEMA)

# Data-only file kinds a mod zip may hold: its own files, the game's data formats, pictures, sound, video and text.
ALLOWED_KINDS = {
    "toml", "txt", "md", "png", "jpg", "jpeg",
    "ogg", "wav", "mp4", "pvr", "xv", "mou", "map", "dsc", "scn", "eng", "ani", "tut", "res", "msg", "raw", "bin",
    "ttf", "otf",
}
# Files without an extension that are fine anywhere (licence and read-me texts).
ALLOWED_PLAIN = {"license", "licence", "copying", "readme", "notice", "authors", "credits"}
# Magic numbers of executables, scripts and archives: refused whatever the file is called.
REFUSED_MAGIC = [b"MZ", b"\x7fELF", b"\xfe\xed\xfa", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe",
                 b"#!", b"PK\x03\x04", b"Rar!", b"7z\xbc\xaf", b"\x1f\x8b", b"BZh", b"\xfd7zXZ", b"\0asm"]
MAX_UNPACKED = 4 << 30
MAX_RATIO = 200
ENTRY_PATH = re.compile(r"^mods/([^/]+)/([^/]+)\.toml$")


def entry_files():
    return sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "mods").rglob("*") if p.is_file()) if (ROOT / "mods").is_dir() else []


def load(rel):
    """(entry dict or None, problems) for the repository-relative path `rel`."""
    m = ENTRY_PATH.match(rel)
    if not m:
        return None, [f"{rel}: only mods/<id>/<version>.toml files belong under mods/"]
    try:
        entry = tomllib.loads((ROOT / rel).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        return None, [f"{rel}: {e}"]
    try:
        validate(entry)
    except fastjsonschema.JsonSchemaValueException as e:
        return None, [f"{rel}: {e.message}"]
    problems = []
    if (entry["id"], entry["version"]) != m.groups():
        problems.append(f"{rel}: the path must be mods/{entry['id']}/{entry['version']}.toml")
    return entry, problems


def owner(url):
    return url.split("/")[3].lower()


def check_entries(rels):
    """Every entry in `rels` (repository-relative paths) checked alone and against each other."""
    problems, entries, seen, owners = [], {}, {}, {}
    for rel in rels:
        entry, p = load(rel)
        problems += p
        if entry is None:
            continue
        entries[rel] = entry
        key = (entry["id"], entry["version"].lower())
        if key in seen:
            problems.append(f"{rel}: {entry['id']} {entry['version']} is already in {seen[key]}")
        seen.setdefault(key, rel)
        owners.setdefault(entry["id"], {}).setdefault(owner(entry["download"]), rel)
    for mod_id, by in owners.items():
        if len(by) > 1:
            problems.append(f"mods/{mod_id}: every release must download from the same GitHub owner ({', '.join(sorted(by))})")
    return entries, problems


def changed(base):
    """(added entry paths, problems) for the commits since `base`: released entries can be removed, never changed."""
    out = subprocess.run(["git", "diff", "--name-status", "--no-renames", f"{base}...HEAD"], cwd=ROOT, check=True,
                         capture_output=True, text=True).stdout
    added, problems = [], []
    for line in out.splitlines():
        status, path = line.split("\t", 1)
        if not path.startswith("mods/"):
            continue
        if status == "A":
            added.append(path)
        elif status == "M":
            problems.append(f"{path}: a released entry can't change; add a new version instead")
    return added, problems


def download(url, size):
    if not url.startswith("https://"):
        raise ValueError("downloads must be https")
    req = urllib.request.Request(url, headers={"User-Agent": "openIG2-mods-index-check"})
    with urllib.request.urlopen(req, timeout=120) as r:
        if not r.geturl().startswith("https://"):
            raise ValueError(f"redirected away from https: {r.geturl()}")
        data = r.read(size + 1)
    return data


def check_zip(rel, entry, data, package_check=None):
    """The zip `data` against `entry`: size, SHA-256, the zip rules, its mod.toml and the optional format checks."""
    problems = []
    if len(data) != entry["size"]:
        problems.append(f"{rel}: the download has {len(data)} bytes, size says {entry['size']}")
    digest = hashlib.sha256(data).hexdigest()
    if digest != entry["sha256"]:
        problems.append(f"{rel}: the download's SHA-256 is {digest}, sha256 says {entry['sha256']}")
    if problems:
        return problems
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = z.infolist()
    except (zipfile.BadZipFile, ValueError) as e:
        return [f"{rel}: not a zip: {e}"]
    names, total = set(), 0
    for i in infos:
        n = i.filename
        parts = n.rstrip("/").split("/")
        if n.startswith("/") or "\\" in n or ":" in n or any(p in ("", ".", "..") for p in parts) or "\0" in n:
            problems.append(f"{rel}: {n!r}: zip paths must be relative, with / separators and no . or .. parts")
            continue
        if n.lower() in names:
            problems.append(f"{rel}: {n}: twice in the zip (names are compared without case)")
        names.add(n.lower())
        if i.flag_bits & 1:
            problems.append(f"{rel}: {n}: encrypted entries aren't allowed")
        mode = i.external_attr >> 16
        if mode & 0o170000 == 0o120000:
            problems.append(f"{rel}: {n}: symbolic links aren't allowed")
        if i.is_dir():
            continue
        total += i.file_size
        if i.file_size > MAX_RATIO * max(i.compress_size, 1) + 4096:
            problems.append(f"{rel}: {n}: compresses more than {MAX_RATIO}:1")
        name = PurePosixPath(n).name.lower()
        ext = name.rsplit(".", 1)[1] if "." in name else None
        if (ext is None and name not in ALLOWED_PLAIN) or (ext is not None and ext not in ALLOWED_KINDS):
            problems.append(f"{rel}: {n}: file kind not allowed (data only: {', '.join(sorted(ALLOWED_KINDS))})")
    if total > MAX_UNPACKED:
        problems.append(f"{rel}: unpacks to {total} bytes, more than {MAX_UNPACKED}")
    if problems:
        return problems
    for i in infos:
        if i.is_dir():
            continue
        try:
            head = z.open(i).read(8)
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as e:
            problems.append(f"{rel}: {i.filename}: {e}")
            continue
        if any(head.startswith(m) for m in REFUSED_MAGIC):
            problems.append(f"{rel}: {i.filename}: looks like an executable, script or archive")
    if z.testzip() is not None:
        problems.append(f"{rel}: the zip has a damaged file ({z.testzip()})")
    if "mod.toml" not in names:
        return problems + [f"{rel}: the zip has no mod.toml at its top"]
    try:
        info = tomllib.loads(z.read(next(x for x in z.namelist() if x.lower() == "mod.toml")).decode("utf-8-sig"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        return problems + [f"{rel}: mod.toml: {e}"]
    for key in ("id", "version"):
        if info.get(key) != entry[key]:
            problems.append(f"{rel}: mod.toml's {key} is {info.get(key)!r}, the entry says {entry[key]!r}")
    for key in ("requires", "license", "kind", "conflicts", "depends"):
        default = "full" if key == "kind" else None
        if info.get(key, default) != entry.get(key, default):
            problems.append(f"{rel}: mod.toml's {key} is {info.get(key)!r}, the entry says {entry.get(key)!r}")
    if package_check and not problems:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / entry["id"]
            z.extractall(folder)
            cmd = [a.replace("{dir}", str(folder)) for a in shlex.split(package_check)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                problems.append(f"{rel}: {cmd[0]} failed ({r.returncode}):\n{r.stdout}{r.stderr}")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", help="check only the entries added since this git ref (a pull request's base)")
    ap.add_argument("--download", action="store_true", help="download each checked entry's zip and check it")
    ap.add_argument("--package-check", help="command run on each unpacked zip; {dir} = its folder")
    args = ap.parse_args(argv)
    all_rels = entry_files()
    entries, problems = check_entries(all_rels)
    targets = all_rels
    if args.base:
        targets, p = changed(args.base)
        problems += p
    if args.download:
        for rel in targets:
            if rel not in entries:
                continue
            try:
                data = download(entries[rel]["download"], entries[rel]["size"])
            except (OSError, ValueError) as e:
                problems.append(f"{rel}: download failed: {e}")
                continue
            problems += check_zip(rel, entries[rel], data, args.package_check)
    for p in problems:
        print(p)
    print(f"{len(all_rels)} entries, {len(targets)} checked, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
