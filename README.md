# openIG2 mod index

The list of mods that [openIG2](https://github.com/openIG2-org/openIG2)'s Mods page shows under **Browse**. This
repository holds one small TOML file per mod release and nothing else: the mods themselves are zip files that
their authors publish as GitHub release assets of their own repositories. Every change is a pull request, checked
by CI; after a merge, CI builds one signed `index.json` that the client downloads.

## The index the client reads

| File | URL |
|---|---|
| Index | `https://github.com/openIG2-org/mods/releases/download/index/index.json` |
| Signature | `https://github.com/openIG2-org/mods/releases/download/index/index.json.minisig` |

Both are assets of the rolling release `index`, replaced on every push to `main` that touches `mods/`, `keys/` or
`scripts/`. The signature is [minisign](https://jedisct1.github.io/minisign/) (Ed25519, the pre-hashed form) with
the key in [`keys/`](keys/); the client accepts only an index signed by a key it has built in. Its trusted comment
reads `timestamp:<unix time>`, `file:index.json` and `format:1`, separated by tabs.

`index.json`:

```json
{
 "format": 1,
 "generated": "2026-11-08T12:00:00Z",
 "mods": [ { "author": "…", "description": "…", "download": "https://…", "id": "…", "license": "…", "name": "…",
   "requires": ">=0.7, <0.9", "sha256": "…", "size": 12345, "version": "1.2.0" } ]
}
```

`mods` holds every entry below with the same keys (keys sorted), ordered by `id`, then newest `version` first.
`generated` is the time of the commit it was built from (UTC). A new `format` number means a change an older client
can't read.

## The entry: `mods/<id>/<version>.toml`

```toml
id = "zorg-race"                 # the mod.toml id: lower-case letters, digits, - and _ (at most 64)
version = "1.2.0"                # semantic version: MAJOR.MINOR.PATCH, optionally -prerelease
name = "The Zorg race"           # at most 80 characters
author = "Your name or handle"   # at most 80 characters
description = "What it changes, in a sentence or a few."   # at most 2000 characters
license = "CC-BY-4.0"            # SPDX licence expression that allows sharing
requires = ">=0.7, <0.9"         # optional: the openIG2 versions it runs on (missing = any)
kind = "full"                    # optional: "full" (default) or "visual" (look-only)
conflicts = ["other-mod"]        # optional: mod ids it can't run with
picture = "https://github.com/you/zorg-race/releases/download/v1.2.0/screenshot.png"   # optional, PNG or JPEG
download = "https://github.com/you/zorg-race/releases/download/v1.2.0/zorg-race.zip"
size = 123456                    # the zip's size in bytes
sha256 = "3f…"                   # the zip's SHA-256, 64 lower-case hex digits

[depends]                        # optional: mod id = the versions it needs
base-pack = ">=1.2"
```

The full rules are in [`schema/entry.schema.json`](schema/entry.schema.json). Ranges use `mod.toml`'s syntax:
comma-separated `>=`, `>`, `<=`, `<`, `=`, `^`, `~` and plain versions, or `*`. `id`, `version`, `requires`,
`depends`, `conflicts`, `license` and `kind` must be the same as in the zip's `mod.toml`.

- `download` is a release asset on `github.com` (your own repository's, or this repository's if you have none).
  All releases of a mod download from the same GitHub account or organisation.
- `picture` is a GitHub release asset or a `raw.githubusercontent.com` file.
- A released entry never changes: a fix is a new version. A file is only ever added or (takedown) removed.

`openIG2-check --package <folder>` (in each openIG2 release) checks a mod folder and writes its zip, the SHA-256 and
this entry for you.

## Rules for contributions

1. **Your own work only**, under a licence that allows sharing it (say which in `license`).
2. **Nothing from the game.** No file from Imperium Galactica II, and nothing decoded, converted, edited or upscaled
   from one. openIG2 reads the player's own install; a mod holds only what is new.
3. **Data only.** No executables, scripts, libraries or archives inside the zip. The file kinds allowed:
   `toml`, `txt`, `md`, `png`, `jpg`, `jpeg`, `ogg`, `wav`, `mp4`, the game's data formats (`pvr`, `xv`, `mou`,
   `map`, `dsc`, `scn`, `eng`, `ani`, `tut`, `res`, `msg`, `raw`, `bin`), fonts (`ttf`, `otf`), and `LICENSE`,
   `README`, `COPYING`, `NOTICE`, `AUTHORS`, `CREDITS` without an extension. Ask in an issue if your mod needs
   another kind.
4. **Takedown on request.** A rights holder (or you, for your own mod) can ask for an entry to be removed by opening
   an issue; the maintainers remove the entry and the next index drops it.

The maintainers merge pull requests and handle takedowns.

## Adding a mod

1. Run `openIG2-check --package <your mod folder>`; fix what it reports.
2. Make a release in your repository (for example tag `v1.2.0`) and upload the zip (and a screenshot, if you like)
   as release assets.
3. In this repository, **Add file → Create new file**, name it `mods/<id>/<version>.toml`, paste the entry, and
   propose the change: GitHub forks the repository for you and opens the pull request. (openIG2's Publish button
   opens that page with the entry filled in.)
4. CI checks the entry: the schema, the path, duplicates, then downloads the zip and checks its size, SHA-256, its
   paths (relative, no `..`), the file kinds and its `mod.toml`. Fix what it reports by pushing to the same branch.
5. A maintainer reviews and merges; the next index lists the mod.

A new version is a new file next to the old one, `mods/<id>/<new version>.toml`. Delete an old file to take that
version off the list.

## Checking locally

```sh
pip install -r requirements.txt
python -m unittest discover -s tests -v            # the checks and the index build, on a sample made by the tests
python scripts/check.py                            # every entry: schema, path, duplicates
python scripts/check.py --base origin/main --download   # what a pull request adds, with its zips
python scripts/build_index.py out --unsigned       # out/index.json
```
