"""HAMCP-377: the rollback proof lane end to end, source subject.

The lane runs twice for the module, and each time the previous release extracted
from its tagged commit reads the stores. In the first run this checkout writes
them; in the second the writer is an archive of the checked-out commit and the
receipts are classified in exact mode, as recorded receipts are. Nothing here is
a recorded receipt; the recorded ones are checked by the classifier test. No
network, no container, no Home Assistant.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rollback_proof_lane as lane  # noqa: E402

RUN: dict[str, Any] = {}
EXACT: dict[str, Any] = {}
DRIVERS = {"rollback_proof_lane.py": "scripts/rollback_proof_lane.py",
           "tip_writer.py": "tests/fixtures/hamcp377_rollback_proof/tip_writer.py",
           "previous_reader.py": "tests/fixtures/hamcp377_rollback_proof/previous_reader.py"}
DATA_SETS = {"12a": ["registry"], "12b": ["device_crash", "device_hold", "device_recovery_27a"],
             "12c": ["plans"]}


def run_lane(*arguments: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = lane.main(list(arguments))
    return code, out.getvalue(), err.getvalue()


def git_text(*arguments: str) -> str:
    """This module's own question to git; the lane's `git` helper is not used for it."""

    return subprocess.run(["git", "-C", str(ROOT), *arguments], check=True, capture_output=True,
                          text=True).stdout


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_digest(root: Path) -> tuple[int, str]:
    """This module's own measurement of a data set: it walks the tree and hashes it itself."""

    files = [Path(folder, name) for folder, _, names in os.walk(root) for name in names]
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(root).parts):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0"
                      + sha256(path).encode() + b"\n")
    return len(files), digest.hexdigest()


def checkout_state() -> dict:
    return {"head": git_text("rev-parse", "HEAD").strip(),
            "status": git_text("status", "--porcelain"),
            "drivers_modified": git_text("status", "--porcelain", "--",
                                         *DRIVERS.values()).split("\n")[:-1]}


def setUpModule() -> None:
    if shutil.which("git") is None:
        raise unittest.SkipTest("git is unavailable")
    missing = lane.prerequisites()
    if missing:
        raise unittest.SkipTest("this clone cannot run the lane: " + "; ".join(missing))
    temp = Path(tempfile.mkdtemp(prefix="hamcp377-rollback-proof-"))
    unittest.addModuleCleanup(shutil.rmtree, temp)
    interpreter = os.environ.get("HAMCP377_PREVIOUS_PYTHON") or sys.executable
    RUN.update(temp=temp, interpreter=interpreter, out=temp / "out", before=checkout_state())
    RUN["code"], RUN["stdout"], RUN["stderr"] = run_lane(
        "run", "--out", str(temp / "out"), "--work", str(temp / "work"),
        "--reader-python", interpreter)
    RUN["receipts"] = {receipt["scenario"]: receipt
                       for receipt in lane.load_receipts(temp / "out")}
    EXACT.update(out=temp / "exact-out", work=temp / "exact-work", before=checkout_state())
    EXACT["code"], EXACT["stdout"], EXACT["stderr"] = run_lane(
        "run", "--out", str(EXACT["out"]), "--work", str(EXACT["work"]),
        "--reader-python", interpreter, "--writer-commit", "HEAD")
    EXACT["after"] = checkout_state()
    EXACT["receipts"] = {receipt["scenario"]: receipt
                         for receipt in lane.load_receipts(EXACT["out"])}


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
            self.assertEqual((writer["commit"], writer["archive_commit"]),
                             (RUN["before"]["head"], None))
            self.assertEqual(RUN["before"], EXACT["before"], "the checkout changed during a run")
            self.assertIs(writer["worktree_clean"], RUN["before"]["status"] == "")
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

    def test_registry_entry_comes_out_of_the_candidate_tool_and_its_dry_run(self) -> None:
        receipt = RUN["receipts"]["12a"]
        observed = receipt["cases"]["registry"]["observed"]
        writer, data = observed["writer"], RUN["temp"] / "work/data/registry"
        self.assertEqual(sorted(str(path.relative_to(data).as_posix())
                                for path in data.rglob("*") if path.is_file()),
                         ["core-candidate.json", "core-entry.json", "core-review-evidence.json",
                          "dry-run/ephemeral-predecessor.json", "dry-run/ephemeral-public-key.txt",
                          "dry-run/ha-core-release-registry.json"])
        for name, path, operation in (
                ("candidate", "scripts/prepare_core_release_registry.py", "extend-capabilities"),
                ("test_signing", "scripts/core_registry_one_tap.py", "dry-run-sign")):
            ran = writer["tools"][name]
            self.assertEqual((ran["path"], ran["operation"], ran["exit"]), (path, operation, 0))
            self.assertEqual(ran["sha256"], lane.sha256_file(ROOT / path))
            tool = receipt[f"{name}_tool"]
            self.assertEqual((tool["path"], tool["commit"], tool["sha256"]),
                             (path, receipt["writer"]["commit"], ran["sha256"]))
        candidate = data / "core-candidate.json"
        self.assertEqual(writer["candidate"]["sha256"], lane.sha256_file(candidate))
        self.assertEqual(writer["candidate"]["sha256"], writer["candidate"]["reported_sha256"])
        self.assertEqual((writer["published_sequence"], writer["candidate"]["sequence"],
                          observed["reader"]["authority"]["sequence"]), (17, 18, 18))
        self.assertEqual(writer["candidate"]["previous_journal_sha256"],
                         lane.PUBLISHED_JOURNAL_SHA256)
        self.assertEqual(writer["candidate"]["public_key_sha256"], lane.PUBLISHED_KEY_SHA256)
        self.assertNotEqual(writer["test_signed"]["key_sha256"], lane.PUBLISHED_KEY_SHA256)
        # The objects the previous release decoded are the ones in the tool's candidate file.
        in_file = lane.candidate_reference_objects(candidate.read_bytes())
        self.assertEqual(in_file["objects"], writer["reference_objects"])
        self.assertEqual(observed["reader"]["entry_reference_objects_sha256"],
                         lane.reference_digest(in_file["objects"]))
        self.assertEqual(len(in_file["objects"]), 38)
        for path in data.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"PRIVATE", path.read_bytes(), path.name)

    def test_no_real_candidate_is_claimed_and_the_runs_own_candidate_is_not_one(self) -> None:
        receipt = RUN["receipts"]["12a"]
        self.assertEqual(receipt["real_candidate_comparison"]["status"],
                         "not yet available: the real candidate does not exist")
        before = (RUN["out"] / "12a-source.json").read_bytes()
        code, out, err = run_lane(
            "compare-candidate", str(RUN["out"]),
            str(RUN["temp"] / "work/data/registry/core-candidate.json"))
        self.assertEqual(code, 1)
        self.assertIn("12a: G14: the file compared is the lane's own test candidate, not a real "
                      "one", err)
        self.assertIs(json.loads(out.rsplit("rollback proof lane:", 1)[0])["identical"], True)
        self.assertEqual((RUN["out"] / "12a-source.json").read_bytes(), before)

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
        self.assertEqual(steps["crash"]["died_in"], lane.CRASH_POINT_27A)
        self.assertEqual((settle["settled"]["normalized_outcome"],
                          settle["release"]["terminal_outcome"]),
                         ("manual_review_required", "manual_review_required"))
        self.assertTrue(settle["review"]["manual_review_reason"], settle["review"])
        self.assertTrue(settle["review"]["hold_binding"], settle["review"])
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
        self.assertEqual(err, "rollback proof lane: no evidence produced: work directory "
                              f"{(RUN['temp'] / 'work').resolve()} is not empty\n")
        self.assertFalse((RUN["temp"] / "again").exists())


class ExactRunTests(unittest.TestCase):
    """The run from `--writer-commit HEAD`: what it recorded against what this module measures.

    A recorded receipt is classified in exact mode, so this is the mode the committed
    receipts were produced in. Every identity and digest below is measured here, with this
    module's own git calls and hashes, and then compared with what the lane wrote down.
    """

    def expected(self, receipt: dict) -> list[str]:
        failures = [f"G1: the {side} interpreter does not hold its tree's locked dependencies"
                    for side in ("reader", "writer")
                    if receipt[side]["dependencies"]["mismatches"] != []
                    or not receipt[side]["dependencies"]["pins"]]
        if EXACT["before"]["drivers_modified"]:
            failures.append("G2: the drivers that ran are not the drivers of one exact commit")
        return failures

    def test_an_exact_run_passes_only_with_unmodified_drivers_and_locked_dependencies(
            self) -> None:
        self.assertEqual(EXACT["before"], EXACT["after"], "the checkout changed during the run")
        self.assertEqual(sorted(path.name for path in EXACT["out"].iterdir()),
                         ["12a-source.json", "12b-source.json", "12c-source.json"])
        said, gate = [], []
        for scenario in ("12a", "12b", "12c"):
            receipt = EXACT["receipts"][scenario]
            expected = self.expected(receipt)
            self.assertEqual(receipt["failures"], expected, scenario)
            self.assertEqual(receipt["result"], "FAIL" if expected else "PASS", scenario)
            said += [f"{scenario}: {item}" for item in expected]
            gate += [f"{scenario}: {item}" for item in expected]
            gate += [f"G13: the {scenario} receipt does not record PASS"] if expected else []
        self.assertEqual(lane.gate(list(EXACT["receipts"].values()), require_exact=True), gate)
        verdict = f"rollback proof lane: {'FAIL' if gate else 'PASS'}\n"
        self.assertEqual(EXACT["code"], 1 if gate else 0)
        self.assertTrue(EXACT["stdout"].endswith(verdict), EXACT["stdout"])
        self.assertEqual(EXACT["stderr"], "".join(item + "\n" for item in said))
        self.assertEqual(run_lane("check", str(EXACT["out"])),
                         (EXACT["code"], verdict, "".join(item + "\n" for item in gate)))

    def test_the_writer_is_an_archive_of_the_checked_out_commit(self) -> None:
        head = EXACT["before"]["head"]
        self.assertEqual(len(head), 40)
        for scenario, receipt in EXACT["receipts"].items():
            with self.subTest(scenario=scenario):
                writer = receipt["writer"]
                self.assertEqual((writer["commit"], writer["archive_commit"], writer["source"]),
                                 (head, head, "git-archive"))
                self.assertNotIn("worktree_clean", writer)
                self.assertEqual(receipt["lane"]["checkout_commit"], head)
                self.assertIs(receipt["trees_distinct"], True)
                self.assertTrue(all(run["package_in_own_tree"] for run in receipt["runs"]))
                self.assertEqual({run["side"] for run in receipt["runs"]}, {"reader", "writer"})
        for side, commit in (("writer", head), ("reader", lane.PREVIOUS_COMMIT)):
            tree = EXACT["work"] / f"{side}-source"
            for path in (lane.LOCK, "hass_mcp_engineering_beta/config.yaml", *DRIVERS.values(),
                         lane.CANDIDATE_TOOL, lane.TEST_SIGNING_TOOL):
                listed = git_text("ls-tree", commit, "--", path).split()
                with self.subTest(side=side, path=path):
                    self.assertEqual(tree.joinpath(path).is_file(), bool(listed))
                    if listed:
                        self.assertEqual(git_text("hash-object", "--no-filters",
                                                  str(tree / path)).strip(), listed[2])
        self.assertNotEqual(*(sha256(EXACT["work"] / f"{side}-source/hass_mcp_engineering_beta"
                                     / "config.yaml") for side in ("reader", "writer")))

    def test_the_recorded_drivers_are_the_files_that_ran(self) -> None:
        measured = {name: sha256(ROOT / path) for name, path in DRIVERS.items()}
        for scenario, receipt in EXACT["receipts"].items():
            with self.subTest(scenario=scenario):
                self.assertEqual(receipt["drivers"], measured)
                self.assertEqual(receipt["lane"]["drivers_modified"],
                                 EXACT["before"]["drivers_modified"])

    def test_the_recorded_tools_are_the_files_in_the_writers_tree(self) -> None:
        head, receipt = EXACT["before"]["head"], EXACT["receipts"]["12a"]
        ran = receipt["cases"]["registry"]["observed"]["writer"]["tools"]
        for key, step, path in (("candidate_tool", "candidate", lane.CANDIDATE_TOOL),
                                ("test_signing_tool", "test_signing", lane.TEST_SIGNING_TOOL)):
            with self.subTest(tool=key):
                file = EXACT["work"] / "writer-source" / path
                record = receipt[key]
                self.assertEqual(
                    {name: record[name] for name in ("path", "commit", "source", "sha256",
                                                     "blob_run", "blob_at_commit")},
                    {"path": path, "commit": head, "source": "git-archive",
                     "sha256": sha256(file),
                     "blob_run": git_text("hash-object", "--no-filters", str(file)).strip(),
                     "blob_at_commit": git_text("rev-parse", "--verify",
                                                f"{head}:{path}").strip()})
                self.assertEqual((ran[step]["path"], ran[step]["sha256"]), (path, sha256(file)))

    def test_the_recorded_store_digests_are_those_of_the_stores(self) -> None:
        for scenario, receipt in EXACT["receipts"].items():
            self.assertEqual(sorted(receipt["data"]), DATA_SETS[scenario])
            for name, record in receipt["data"].items():
                with self.subTest(data=name):
                    files, digest = tree_digest(EXACT["work"] / "data" / name)
                    self.assertGreater(files, 0)
                    self.assertEqual(
                        {key: record[key] for key in ("files", "after_write", "before_read",
                                                      "after_read")},
                        {"files": files, "after_write": digest, "before_read": digest,
                         "after_read": digest})

    def test_the_recorded_dependency_state_names_the_trees_own_lock(self) -> None:
        interpreters = {"reader": RUN["interpreter"], "writer": sys.executable}
        for side, interpreter in interpreters.items():
            version = subprocess.run(
                [interpreter, "-B", "-I", "-c", "import sys; print('%d.%d.%d' % sys.version_info"
                 "[:3])"], check=True, capture_output=True, text=True).stdout.strip()
            lock = sha256(EXACT["work"] / f"{side}-source" / lane.LOCK)
            for scenario, receipt in EXACT["receipts"].items():
                with self.subTest(side=side, scenario=scenario):
                    recorded = receipt[side]["dependencies"]
                    self.assertEqual((recorded["python"], recorded["lock_sha256"]),
                                     (version, lock))
                    self.assertEqual(receipt[side]["flags"], ["-B", "-I"])


class PublishedJournalTests(unittest.TestCase):
    """The candidate tool extends only a current journal, so the lane depends on the clock."""

    def test_pinned_expiry_is_the_pinned_journals_own(self) -> None:
        raw = lane.git("cat-file", "blob", lane.PUBLISHED_JOURNAL_BLOB)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), lane.PUBLISHED_JOURNAL_SHA256)
        tip = json.loads(raw)["envelopes"][-1]
        self.assertEqual((tip["sequence"], tip["expires_at"]),
                         (lane.PUBLISHED_SEQUENCE, lane.PUBLISHED_EXPIRES_AT))

    def test_an_expired_published_journal_is_a_missing_prerequisite(self) -> None:
        expires = datetime(2027, 2, 5, 6, 16, 34, tzinfo=timezone.utc)
        self.assertEqual(lane.prerequisites(expires - timedelta(seconds=1)), [])
        for moment in (expires, expires + timedelta(days=400)):
            missing = lane.prerequisites(moment)
            self.assertEqual(len(missing), 1, missing)
            self.assertIn("the pinned published journal expired at 2027-02-05T06:16:34Z",
                          missing[0])

    def test_an_expired_published_journal_is_no_evidence(self) -> None:
        temp = Path(tempfile.mkdtemp(prefix="hamcp377-expired-"))
        self.addCleanup(shutil.rmtree, temp)
        real = lane.prerequisites
        with mock.patch.object(lane, "prerequisites", lambda: real(
                datetime(2027, 2, 5, 6, 16, 34, tzinfo=timezone.utc))):
            code, out, err = run_lane("run", "--out", str(temp / "out"), "--work",
                                      str(temp / "work"), "--reader-python", RUN["interpreter"])
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no evidence produced", err)
        self.assertIn("the pinned published journal expired", err)
        self.assertFalse((temp / "out").exists())


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
