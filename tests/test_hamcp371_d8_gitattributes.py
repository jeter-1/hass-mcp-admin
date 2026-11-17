"""HAMCP-371 D8: exact-path whitespace entries generated for one SR run."""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docs/evidence/hamcp371/sr/d8_gitattributes.py"
SPEC = importlib.util.spec_from_file_location("hamcp371_d8_gitattributes", SCRIPT)
d8 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d8)

RUN = "docs/evidence/hamcp371/sr/run-x"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True,
                          text=True).stdout.strip()


class D8GitattributesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "d8@example.invalid")
        _git(self.repo, "config", "user.name", "d8")
        (self.repo / ".gitattributes").write_text("*.png binary\n", encoding="utf-8")
        _git(self.repo, "add", ".gitattributes")
        _git(self.repo, "commit", "-qm", "base")
        self.base = _git(self.repo, "rev-parse", "HEAD")
        self.run = self.repo / RUN
        self.run.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _files(self, files: dict[str, bytes], listed=None, sums_override=None):
        for name, data in files.items():
            (self.run / name).parent.mkdir(parents=True, exist_ok=True)
            (self.run / name).write_bytes(data)
        listed = list(files) if listed is None else listed
        lines = []
        for name in listed:
            digest = (sums_override or {}).get(name) or hashlib.sha256(files[name]).hexdigest()
            lines.append(f"{digest}  {name}\n")
        (self.run / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")
        _git(self.repo, "add", "-A")

    def test_listed_flagged_file_gets_one_exact_path_entry(self):
        self._files({"a/peer.log": b"ok\ntrailing  \n", "clean.json": b"{}\n"})
        report = d8.entries_for(self.repo, self.base, "run-x")
        self.assertEqual([e["path"] for e in report["entries"]], [f"{RUN}/a/peer.log"])
        self.assertEqual(report["entries"][0]["lines"], [2])
        self.assertIn(f"{RUN}/a/peer.log -text -whitespace\n", report["block"])
        self.assertIn(f"{RUN}/SHA256SUMS", report["block"])
        self.assertNotIn("*", report["block"])

    def test_clean_run_has_no_entries(self):
        self._files({"clean.json": b"{}\n"})
        self.assertEqual(d8.entries_for(self.repo, self.base, "run-x")["entries"], [])

    def test_flagged_file_outside_sums_stops(self):
        self._files({"listed.txt": b"ok\n", "stray.log": b"bad \n"}, listed=["listed.txt"])
        with self.assertRaisesRegex(d8.StopAndReport, "not in"):
            d8.entries_for(self.repo, self.base, "run-x")

    def test_hash_mismatch_stops(self):
        self._files({"peer.log": b"bad \n"}, sums_override={"peer.log": "0" * 64})
        with self.assertRaisesRegex(d8.StopAndReport, "does not match"):
            d8.entries_for(self.repo, self.base, "run-x")

    def test_run_name_must_be_one_sr_directory(self):
        with self.assertRaises(d8.StopAndReport):
            d8.entries_for(self.repo, self.base, "../run-x")

    def test_write_appends_once_and_then_check_is_clean(self):
        self._files({"peer.log": b"bad \n"})
        report = d8.entries_for(self.repo, self.base, "run-x")
        d8.write_block(self.repo, report)
        text = (self.repo / ".gitattributes").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("*.png binary\n"))
        self.assertTrue(text.endswith(f"{RUN}/peer.log -text -whitespace\n"))
        _git(self.repo, "add", ".gitattributes")
        self.assertEqual(d8.entries_for(self.repo, self.base, "run-x")["entries"], [])
        with self.assertRaisesRegex(d8.StopAndReport, "already has a D8 block"):
            d8.write_block(self.repo, report)


if __name__ == "__main__":
    unittest.main()
