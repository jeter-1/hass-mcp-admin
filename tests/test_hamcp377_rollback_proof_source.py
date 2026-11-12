"""HAMCP-377: the rollback proof lane end to end, source subject.

The lane runs once for the module: this checkout writes the stores, the previous
release extracted from its tagged commit reads them. Nothing here is a recorded
receipt; the recorded ones are checked by the classifier test. No network, no
container, no Home Assistant.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rollback_proof_lane as lane  # noqa: E402

RUN: dict[str, Any] = {}


def run_lane(*arguments: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = lane.main(list(arguments))
    return code, out.getvalue(), err.getvalue()


def setUpModule() -> None:
    if shutil.which("git") is None:
        raise unittest.SkipTest("git is unavailable")
    missing = lane.prerequisites()
    if missing:
        raise unittest.SkipTest("this clone cannot run the lane: " + "; ".join(missing))
    temp = Path(tempfile.mkdtemp(prefix="hamcp377-rollback-proof-"))
    unittest.addModuleCleanup(shutil.rmtree, temp)
    interpreter = os.environ.get("HAMCP377_PREVIOUS_PYTHON") or sys.executable
    RUN.update(temp=temp, interpreter=interpreter, out=temp / "out")
    RUN["code"], RUN["stdout"], RUN["stderr"] = run_lane(
        "run", "--out", str(temp / "out"), "--work", str(temp / "work"),
        "--reader-python", interpreter)
    RUN["receipts"] = {receipt["scenario"]: receipt
                       for receipt in lane.load_receipts(temp / "out")}


class SourceSubjectLaneTests(unittest.TestCase):
    def test_lane_passes_and_writes_one_receipt_per_scenario(self) -> None:
        self.assertEqual((RUN["code"], RUN["stderr"]), (0, ""))
        self.assertTrue(RUN["stdout"].endswith("rollback proof lane: PASS\n"), RUN["stdout"])
        self.assertEqual(sorted(path.name for path in RUN["out"].iterdir()),
                         ["12a-source.json", "12b-source.json", "12c-source.json"])
        receipts = list(RUN["receipts"].values())
        self.assertEqual(lane.gate(receipts, require_exact=False), [])
        for receipt in receipts:
            self.assertEqual((receipt["result"], receipt["failures"]), ("PASS", []))
            self.assertIn(lane.SYNTHETIC_PROVIDER_LIMITATION, receipt["limitations"])

    def test_reader_is_the_previous_release_and_the_writer_is_this_checkout(self) -> None:
        for receipt in RUN["receipts"].values():
            reader, writer = receipt["reader"], receipt["writer"]
            self.assertEqual((reader["version"], reader["commit"], reader["archive_commit"]),
                             (lane.PREVIOUS_VERSION, lane.PREVIOUS_COMMIT, lane.PREVIOUS_COMMIT))
            self.assertEqual(writer["version"], lane.declared_version(ROOT))
            self.assertNotEqual(writer["version"], reader["version"])
            self.assertEqual(writer["source"], "worktree")
            self.assertIs(receipt["trees_distinct"], True)
            self.assertTrue(all(run["package_in_own_tree"] for run in receipt["runs"]))
            self.assertEqual({run["side"] for run in receipt["runs"]}, {"reader", "writer"})

    def test_no_store_changed_between_the_writer_and_the_end_of_the_read(self) -> None:
        seen = 0
        for receipt in RUN["receipts"].values():
            for record in receipt["data"].values():
                for part in (record, *record.get("earlier", [])):
                    seen += 1
                    self.assertGreater(part["files"], 0)
                    self.assertEqual(part["after_write"], part["before_read"])
                    self.assertEqual(part["after_write"], part["after_read"])
        self.assertEqual(seen, 6)
        for name in ("registry", "device_hold", "device_crash", "device_recovery_27a", "plans"):
            self.assertTrue((RUN["temp"] / "work/data" / name).is_dir(), name)

    def test_registry_result_is_the_ruled_result(self) -> None:
        case = RUN["receipts"]["12a"]["cases"]["registry"]
        self.assertEqual(case["derived"], {"admitted": 33, "not_admitted": 5, "quarantined": 0,
                                           "marketplace_admitted": True})
        reader = case["observed"]["reader"]
        self.assertEqual(len(reader["entry_references"]), 38)
        self.assertEqual(sorted(item for item in reader["entry_references"]
                                if item not in reader["compiled"]), sorted(lane.NOT_ADMITTED))
        self.assertIs(case["observed"]["real_read"]["exercised"], False)

    def test_lock_records_block_the_previous_release_as_recorded(self) -> None:
        receipt = RUN["receipts"]["12b"]
        self.assertEqual({name: case["derived"] for name, case in receipt["cases"].items()},
                         lane.EXPECTED_DEVICE)
        for name in ("hold", "in_flight_active", "in_flight_expired"):
            observed = receipt["cases"][name]["observed"]
            self.assertEqual(observed["held_switch"]["provider_attempt_count"], 0)
            self.assertEqual(observed["power"]["locks_before"], observed["power"]["locks_after"])
            self.assertTrue(observed["power"]["locks_before"])

    def test_recovery_27a_is_exercised_end_to_end(self) -> None:
        receipt = RUN["receipts"]["12b"]
        self.assertEqual(sorted(receipt["recovery"]), ["27a"])
        tier = receipt["recovery"]["27a"]
        self.assertIs(tier["exercised"], True)
        steps = tier["steps"]
        self.assertEqual(steps["blocked_before_recovery"]["derived"],
                         lane.EXPECTED_DEVICE["in_flight_expired"])
        settle = steps["settle_release"]
        self.assertEqual(settle["task_id"], steps["crash"]["task_id"])
        self.assertEqual((settle["settled"]["terminal"], settle["settled"]["provider_writes"]),
                         (True, 0))
        self.assertTrue(settle["release"]["status"].startswith("hold_released"))
        self.assertEqual((settle["release"]["dispatch"], settle["release"]["provider_writes"],
                          settle["release"]["locks_after"]), ("none", 0, []))
        self.assertEqual(steps["previous_release_read"]["derived"], lane.EXPECTED_AFTER_RECOVERY)
        self.assertIn("Ingress HTTP layer", tier["limitation"])

    def test_plans_are_refused_or_executed_as_recorded(self) -> None:
        receipt = RUN["receipts"]["12c"]
        self.assertEqual({name: case["derived"]["outcome"]
                          for name, case in receipt["cases"].items()}, lane.EXPECTED_PLANS)
        for mode, outcome in lane.EXPECTED_PLANS.items():
            writes = receipt["cases"][mode]["observed"]["provider_writes"]
            if outcome in ("executed", "approved_then_executed"):
                self.assertEqual([write[1] for write in writes], ["DELETE"], mode)
            else:
                self.assertEqual(writes, [], mode)
        self.assertEqual(receipt["source_facts"]["reader_created_identity_references"], 0)
        self.assertGreater(receipt["source_facts"]["writer_created_identity_references"], 0)

    def test_receipts_carry_no_local_path_and_no_key_material(self) -> None:
        for path in RUN["out"].iterdir():
            text = path.read_text(encoding="utf-8")
            for fragment in (str(ROOT), str(RUN["temp"]), str(Path.home()), "PRIVATE KEY"):
                self.assertNotIn(fragment, text, path.name)
            self.assertEqual(json.loads(text)["lane"]["network"], "none")

    def test_a_checkout_written_run_is_not_accepted_as_a_recorded_receipt(self) -> None:
        code, out, err = run_lane("check", str(RUN["out"]))
        self.assertEqual((code, out), (1, "rollback proof lane: FAIL\n"))
        for scenario in ("12a", "12b", "12c"):
            self.assertIn(f"{scenario}: G1: the writer source is not a git archive of one exact "
                          "commit", err)

    def test_a_used_work_directory_is_refused_before_anything_runs(self) -> None:
        code, out, err = run_lane("run", "--out", str(RUN["temp"] / "again"), "--work",
                                  str(RUN["temp"] / "work"), "--reader-python", RUN["interpreter"])
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no evidence produced", err)
        self.assertFalse((RUN["temp"] / "again").exists())


class NoEvidenceTests(unittest.TestCase):
    """A lane that cannot run reports no evidence. It never reports PASS."""

    def run_broken(self, reader: str) -> tuple[int, str, str]:
        temp = Path(tempfile.mkdtemp(prefix="hamcp377-no-evidence-"))
        self.addCleanup(shutil.rmtree, temp)
        result = run_lane("run", "--out", str(temp / "out"), "--work", str(temp / "work"),
                          "--reader-python", reader, "--scenario", "12a")
        self.assertFalse((temp / "out").exists())
        return result

    def test_missing_reader_interpreter_is_no_evidence(self) -> None:
        code, out, err = self.run_broken(str(ROOT / "no-such-interpreter"))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no evidence produced", err)

    def test_failing_reader_interpreter_is_no_evidence(self) -> None:
        code, out, err = self.run_broken(shutil.which("false") or "/bin/false")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("reader interpreter probe failed", err)


if __name__ == "__main__":
    unittest.main()
