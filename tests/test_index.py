"""The checks and the index build against a sample entry, a zip made here and a throwaway key.

    python -m unittest discover -s tests -v

With `minisign` on PATH (CI installs it) the signatures are also checked by the real tool.
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_index  # noqa: E402
import check  # noqa: E402
import minisign  # noqa: E402

MOD_TOML = 'id = "sample-mod"\nversion = "1.0.0"\nname = "Sample"\nauthor = "Nobody"\nrequires = ">=0.7, <0.9"\nlicense = "CC0-1.0"\n'


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files:
            z.writestr(name, data)
    return buf.getvalue()


def entry_toml(data, version="1.0.0", mod_id="sample-mod", owner="someone"):
    return (f'id = "{mod_id}"\nversion = "{version}"\nname = "Sample"\nauthor = "Nobody"\n'
            f'description = "A sample mod for the tests."\nlicense = "CC0-1.0"\nrequires = ">=0.7, <0.9"\n'
            f'download = "https://github.com/{owner}/{mod_id}/releases/download/v{version}/{mod_id}.zip"\n'
            f'size = {len(data)}\nsha256 = "{hashlib.sha256(data).hexdigest()}"\n')


GOOD_ZIP = make_zip([("mod.toml", MOD_TOML), ("dataraw/text/notes.txt", "hello\n"), ("LICENSE", "CC0\n")])


class Repo(unittest.TestCase):
    """Each test runs against its own copy of the repository layout in a temporary folder."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(check.ROOT / "schema", self.tmp / "schema")
        (self.tmp / "keys").mkdir()
        self.old_root, check.ROOT = check.ROOT, self.tmp

    def tearDown(self):
        check.ROOT = self.old_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, rel, text):
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return rel


class Entries(Repo):
    def test_good_entry_and_zip(self):
        rel = self.add("mods/sample-mod/1.0.0.toml", entry_toml(GOOD_ZIP))
        entries, problems = check.check_entries(check.entry_files())
        self.assertEqual(problems, [])
        self.assertEqual(check.check_zip(rel, entries[rel], GOOD_ZIP), [])

    def test_path_and_schema(self):
        self.add("mods/sample-mod/1.0.1.toml", entry_toml(GOOD_ZIP))
        self.add("mods/other/1.0.0.toml", entry_toml(GOOD_ZIP).replace("CC0-1.0", "not a licence!"))
        self.add("mods/stray.txt", "x")
        problems = check.check_entries(check.entry_files())[1]
        self.assertEqual(len(problems), 3, problems)
        self.assertIn("the path must be mods/sample-mod/1.0.0.toml", problems[1] + problems[0] + problems[2])

    def test_duplicates_and_owner(self):
        self.add("mods/sample-mod/1.0.0.toml", entry_toml(GOOD_ZIP))
        self.add("mods/sample-mod/1.1.0.toml", entry_toml(GOOD_ZIP, "1.1.0", owner="someone-else"))
        problems = check.check_entries(check.entry_files())[1]
        self.assertTrue(any("same GitHub owner" in p for p in problems), problems)

    def test_bad_zips(self):
        cases = {
            "../evil.txt": [("mod.toml", MOD_TOML), ("../evil.txt", "x")],
            "/abs.txt": [("mod.toml", MOD_TOML), ("/abs.txt", "x")],
            "file kind": [("mod.toml", MOD_TOML), ("tool.exe", "x")],
            "script": [("mod.toml", MOD_TOML), ("run.py", "print()")],
            "executable": [("mod.toml", MOD_TOML), ("data.bin", b"MZ\x90\0rest")],
            "archive": [("mod.toml", MOD_TOML), ("inner.bin", GOOD_ZIP)],
            "no mod.toml": [("notes.txt", "x")],
            "mod.toml's version": [("mod.toml", MOD_TOML.replace("1.0.0", "2.0.0"))],
        }
        for what, files in cases.items():
            data = make_zip(files)
            entry = check.tomllib.loads(entry_toml(data))
            problems = check.check_zip("mods/sample-mod/1.0.0.toml", entry, data)
            self.assertTrue(problems, what)
        entry = check.tomllib.loads(entry_toml(GOOD_ZIP))
        self.assertIn("SHA-256", check.check_zip("x", dict(entry, sha256="0" * 64), GOOD_ZIP)[0])
        self.assertIn("bytes", check.check_zip("x", dict(entry, size=entry["size"] + 1), GOOD_ZIP)[0])

    def test_package_check_command(self):
        rel = self.add("mods/sample-mod/1.0.0.toml", entry_toml(GOOD_ZIP))
        entry = check.check_entries([rel])[0][rel]
        probe = "import os, sys; sys.exit(0 if os.path.isfile(os.path.join(sys.argv[1], 'mod.toml')) else 3)"
        ok = check.check_zip(rel, entry, GOOD_ZIP, f'"{Path(sys.executable).as_posix()}" -c "{probe}" {{dir}}')
        self.assertEqual(ok, [])
        bad = check.check_zip(rel, entry, GOOD_ZIP, f'"{Path(sys.executable).as_posix()}" -c "import sys; sys.exit(3)" {{dir}}')
        self.assertTrue(bad and "failed (3)" in bad[0], bad)

    def test_released_entries_are_immutable(self):
        git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *a],
                                        cwd=self.tmp, check=True, capture_output=True)
        git("init", "-q", "-b", "main")
        self.add("mods/sample-mod/1.0.0.toml", entry_toml(GOOD_ZIP))
        git("add", "-A"); git("commit", "-qm", "base")
        git("switch", "-qc", "pr")
        self.add("mods/sample-mod/1.0.0.toml", entry_toml(GOOD_ZIP).replace("Nobody", "Somebody"))
        self.add("mods/sample-mod/1.1.0.toml", entry_toml(GOOD_ZIP, "1.1.0"))
        git("add", "-A"); git("commit", "-qm", "pr")
        added, problems = check.changed("main")
        self.assertEqual(added, ["mods/sample-mod/1.1.0.toml"])
        self.assertTrue(problems and "can't change" in problems[0], problems)


class Index(Repo):
    def setUp(self):
        super().setUp()
        self.pub, self.secret = minisign.generate()
        (self.tmp / "keys" / "test.pub").write_text(self.pub, encoding="utf-8")
        self.add("mods/sample-mod/1.0.0.toml", entry_toml(GOOD_ZIP))
        self.add("mods/sample-mod/1.10.0.toml", entry_toml(GOOD_ZIP, "1.10.0"))
        self.add("mods/sample-mod/1.10.0-rc.1.toml", entry_toml(GOOD_ZIP, "1.10.0-rc.1"))
        self.add("mods/another/0.1.0.toml", entry_toml(GOOD_ZIP, "0.1.0", "another"))
        self.out = self.tmp / "out"
        os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
        os.environ["MINISIGN_SECRET_KEY"] = self.secret

    def tearDown(self):
        os.environ.pop("SOURCE_DATE_EPOCH", None)
        os.environ.pop("MINISIGN_SECRET_KEY", None)
        super().tearDown()

    def test_build_and_verify(self):
        self.assertEqual(build_index.main([str(self.out)]), 0)
        data = (self.out / "index.json").read_bytes()
        sig = (self.out / "index.json.minisig").read_text(encoding="utf-8")
        doc = json.loads(data)
        self.assertEqual((doc["format"], doc["generated"]), (1, "2023-11-14T22:13:20Z"))
        self.assertEqual([(m["id"], m["version"]) for m in doc["mods"]],
                         [("another", "0.1.0"), ("sample-mod", "1.10.0"), ("sample-mod", "1.10.0-rc.1"), ("sample-mod", "1.0.0")])
        self.assertIn("file:index.json", minisign.verify(data, sig, [self.pub]))
        # Stable: the same entries and time give the same bytes.
        build_index.main([str(self.tmp / "again")])
        self.assertEqual((self.tmp / "again" / "index.json").read_bytes(), data)
        # A rotation key next to the old one: either verifies; a stranger's key or a changed byte doesn't.
        other_pub, _ = minisign.generate()
        minisign.verify(data, sig, [other_pub, self.pub])
        with self.assertRaises(ValueError):
            minisign.verify(data, sig, [other_pub])
        with self.assertRaises(Exception):
            minisign.verify(data.replace(b"sample", b"sampla"), sig, [self.pub])
        if shutil.which("minisign"):
            pub_file = self.tmp / "keys" / "test.pub"
            r = subprocess.run(["minisign", "-Vm", str(self.out / "index.json"), "-p", str(pub_file)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_unset_or_foreign_key(self):
        os.environ["MINISIGN_SECRET_KEY"] = ""
        with self.assertRaises(SystemExit):
            build_index.main([str(self.out)])
        os.environ["MINISIGN_SECRET_KEY"] = minisign.generate()[1]
        with self.assertRaises(ValueError):
            build_index.main([str(self.out)])
        self.assertFalse((self.out / "index.json").exists())

    @unittest.skipUnless(shutil.which("minisign"), "minisign isn't on PATH")
    def test_key_made_by_minisign(self):
        sk, pk = self.tmp / "real.key", self.tmp / "keys" / "real.pub"
        subprocess.run(["minisign", "-G", "-W", "-s", str(sk), "-p", str(pk)], check=True, capture_output=True)
        os.environ["MINISIGN_SECRET_KEY"] = sk.read_text(encoding="utf-8")
        self.assertEqual(build_index.main([str(self.out)]), 0)
        r = subprocess.run(["minisign", "-Vm", str(self.out / "index.json"), "-p", str(pk)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class RealKey(unittest.TestCase):
    def test_committed_public_key(self):
        keys = sorted((check.ROOT / "keys").glob("*.pub"))
        self.assertTrue(keys)
        ids = [minisign.key_id_hex(minisign.read_public_key(k.read_text(encoding="utf-8"))[0]) for k in keys]
        self.assertIn("A9B771443098FFEF", ids)


if __name__ == "__main__":
    unittest.main()
