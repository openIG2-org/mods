"""Builds index.json from every entry and signs it into index.json.minisig.

    python scripts/build_index.py OUT_DIR                 the secret key from $MINISIGN_SECRET_KEY
    python scripts/build_index.py OUT_DIR --unsigned      index.json only

The timestamp is $SOURCE_DATE_EPOCH when set (CI sets the commit's time), else now. Entries sort by id, then by
version (newest first); the signature is checked against keys/*.pub before anything is written.
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check  # noqa: E402
import minisign  # noqa: E402

FORMAT = 1


def version_key(v):
    """Semantic-version precedence as a sort key (pre-releases below their release)."""
    core, _, pre = v.partition("-")
    nums = tuple(int(x) for x in core.split("."))
    pre_key = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")) if pre else ()
    return nums, not pre, pre_key


def build(timestamp):
    entries, problems = check.check_entries(check.entry_files())
    if problems:
        raise SystemExit("\n".join(problems))
    by_id = {}
    for e in entries.values():
        by_id.setdefault(e["id"], []).append(e)
    ordered = [e for i in sorted(by_id) for e in sorted(by_id[i], key=lambda e: version_key(e["version"]), reverse=True)]
    when = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {"format": FORMAT, "generated": when, "mods": [{k: e[k] for k in sorted(e)} for e in ordered]}
    return (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--unsigned", action="store_true")
    args = ap.parse_args(argv)
    timestamp = int(os.environ.get("SOURCE_DATE_EPOCH") or datetime.datetime.now(datetime.timezone.utc).timestamp())
    data = build(timestamp)
    args.out.mkdir(parents=True, exist_ok=True)
    if not args.unsigned:
        secret = os.environ.get("MINISIGN_SECRET_KEY", "")
        if not secret.strip():
            raise SystemExit("MINISIGN_SECRET_KEY isn't set")
        sig = minisign.sign(data, secret, f"timestamp:{timestamp}\tfile:index.json\tformat:{FORMAT}")
        pubs = [p.read_text(encoding="utf-8") for p in sorted((check.ROOT / "keys").glob("*.pub"))]
        minisign.verify(data, sig, pubs)
        (args.out / "index.json.minisig").write_text(sig, encoding="utf-8", newline="\n")
    (args.out / "index.json").write_bytes(data)
    print(f"index.json: {len(json.loads(data)['mods'])} releases, {len(data)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
