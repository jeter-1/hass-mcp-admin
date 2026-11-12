"""HAMCP-377: the rollback proof lane's classifier and gate.

The baseline is the committed source receipts under
``docs/evidence/hamcp377/lane``. Each guard is shown twice: the recorded
receipt passes, and the same receipt with one fact changed fails with that
guard's id. Where a hand-edited receipt would also rewrite its own derived
words and summary to match, the test rewrites them too, so the guard is shown
to hold against a consistent lie and not only against an inconsistent one.
"""

from __future__ import annotations

import contextlib
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rollback_proof_lane as lane  # noqa: E402

RECEIPTS = ROOT / "docs/evidence/hamcp377/lane"
REFUSED_CALL = {"ok": False, "code": lane.REFUSAL_CODE, "status": None, "writes": []}


def check(directory: Path) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = lane.main(["check", str(directory)])
    return code, out.getvalue(), err.getvalue()


def synthetic_candidate(objects: list, *, shape: str = "candidate", sequence: int = 18,
                        newline: bool = True) -> bytes:
    """A stand-in file for the comparison tests only. It is never a recorded receipt's input."""

    envelope = {"sequence": sequence, "entries": [
        {"version": "2026.10.3", "capabilities": objects[:1]},
        {"version": lane.CORE_VERSION, "capabilities": objects}]}
    value = {"candidate": {"model": "synthetic-test-candidate", "envelope": envelope},
             "signed journal": {"envelopes": [{"sequence": sequence - 1, "entries": []},
                                              envelope]},
             "envelope": envelope}[shape]
    return lane.canonical(value) + (b"\n" if newline else b"")


def run_main(*arguments: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = lane.main(list(arguments))
    return code, out.getvalue(), err.getvalue()


def rederive(receipt: dict) -> None:
    """Rewrite derived words and summary the way a consistent hand edit would."""

    for name, case in receipt["cases"].items():
        if receipt["scenario"] == "12a":
            case["derived"] = lane.registry_words(case)
        elif receipt["scenario"] == "12b":
            case["derived"] = lane.device_words(case["observed"])
        else:
            case["derived"] = {"outcome": lane.plan_outcome(case["observed"])}
    for tier in receipt.get("recovery", {}).values():
        for step in ("blocked_before_recovery", "previous_release_read"):
            if step in tier.get("steps", {}):
                tier["steps"][step]["derived"] = lane.device_words(tier["steps"][step]["observed"])
    receipt["summary"] = lane.summarize(receipt)


class GuardCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        loaded = lane.load_receipts(RECEIPTS)
        cls.receipts = {receipt["scenario"]: receipt for receipt in loaded}
        # One receipt per scenario: a second one would be dropped silently by the mapping above.
        assert sorted(receipt["scenario"] for receipt in loaded) == ["12a", "12b", "12c"]

    def committed(self) -> dict[str, dict]:
        """Copies of the committed receipts, keyed by the file name each is committed under."""

        return {f"{scenario}-source.json": copy.deepcopy(receipt)
                for scenario, receipt in sorted(self.receipts.items())}

    def written(self, files: dict) -> Path:
        """A disposable receipt directory holding the given files. Nothing committed is edited."""

        directory = Path(tempfile.mkdtemp(prefix="hamcp377-receipts-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        for name, value in files.items():
            text = value if isinstance(value, str) else json.dumps(
                value, indent=1, sort_keys=True) + "\n"
            (directory / name).write_text(text, encoding="utf-8")
        return directory

    def changed(self, scenario: str, change, *, consistent: bool = False) -> list[str]:
        receipt = copy.deepcopy(self.receipts[scenario])
        change(receipt)
        if consistent:
            rederive(receipt)
        return lane.classify(receipt)

    def guard(self, guard: str, scenario: str, change, *, consistent: bool = False,
              text: str = "") -> list[str]:
        self.assertEqual(lane.classify(self.receipts[scenario]), [])
        failures = self.changed(scenario, change, consistent=consistent)
        self.assertTrue(any(item.startswith(guard + ":") and text in item for item in failures),
                        failures)
        return failures


class RecordedReceiptTests(GuardCase):
    def test_recorded_receipts_pass_the_gate(self) -> None:
        self.assertEqual(sorted(self.receipts), ["12a", "12b", "12c"])
        self.assertEqual(lane.gate(list(self.receipts.values())), [])
        self.assertEqual(check(RECEIPTS), (0, "rollback proof lane: PASS\n", ""))

    def test_recorded_receipts_carry_the_ruled_results(self) -> None:
        self.assertEqual(self.receipts["12a"]["cases"]["registry"]["derived"],
                         {"admitted": 33, "not_admitted": 5, "quarantined": 0,
                          "marketplace_admitted": True})
        self.assertEqual({name: case["derived"]["outcome"]
                          for name, case in self.receipts["12c"]["cases"].items()},
                         lane.EXPECTED_PLANS)
        for mode in ("scene_pending", "scene_approved", "scene_applied",
                     "scene_rollback_approved", "helper_rollback_approved"):
            self.assertEqual(lane.EXPECTED_PLANS[mode], "refused")
            self.assertEqual(self.receipts["12c"]["cases"][mode]["observed"]["provider_writes"], [])
        for mode in lane.EXECUTION_RECORDED:
            self.assertEqual(len(self.receipts["12c"]["cases"][mode]["observed"]
                                 ["provider_writes"]), 1)
        self.assertEqual({name: case["derived"]
                          for name, case in self.receipts["12b"]["cases"].items()},
                         lane.EXPECTED_DEVICE)
        steps = self.receipts["12b"]["recovery"]["27a"]["steps"]
        self.assertEqual(steps["blocked_before_recovery"]["derived"],
                         lane.EXPECTED_DEVICE["in_flight_expired"])
        self.assertEqual(steps["previous_release_read"]["derived"], lane.EXPECTED_AFTER_RECOVERY)

    def test_recorded_receipts_say_nothing_they_cannot_show(self) -> None:
        for receipt in self.receipts.values():
            self.assertEqual(receipt["subject"], "source")
            self.assertIn(lane.SYNTHETIC_PROVIDER_LIMITATION, receipt["limitations"])
            self.assertEqual(receipt["summary"], lane.summarize(receipt))
            self.assertEqual(receipt["writer"]["source"], "git-archive")
            self.assertEqual(receipt["lane"]["drivers_modified"], [])
        self.assertIs(self.receipts["12a"]["cases"]["registry"]["observed"]["real_read"]
                      ["exercised"], False)

    def test_recorded_12a_names_the_candidate_tool_and_claims_no_comparison(self) -> None:
        receipt = self.receipts["12a"]
        writer = receipt["cases"]["registry"]["observed"]["writer"]
        for name, path, operation in (
                ("candidate_tool", "scripts/prepare_core_release_registry.py",
                 "extend-capabilities"),
                ("test_signing_tool", "scripts/core_registry_one_tap.py", "dry-run-sign")):
            tool = receipt[name]
            self.assertEqual((tool["path"], tool["operation"]), (path, operation))
            self.assertEqual((tool["commit"], tool["source"]),
                             (receipt["writer"]["commit"], "git-archive"))
            self.assertEqual(tool["blob_run"], tool["blob_at_commit"])
        self.assertEqual((writer["published_sequence"], writer["candidate"]["sequence"],
                          writer["test_signed"]["sequence"]), (17, 18, 18))
        self.assertEqual(len(writer["reference_objects"]), 38)
        # No real candidate existed when these were recorded; the receipt must say exactly that.
        self.assertEqual(receipt["real_candidate_comparison"], {
            "status": "not yet available: the real candidate does not exist",
            "compares": lane.COMPARISON_SCOPE, "lane_reference_count": 38,
            "lane_reference_objects_sha256": lane.reference_digest(writer["reference_objects"])})
        self.assertIn("Comparison with the real sequence 18 candidate: not yet available: the "
                      "real candidate does not exist.", receipt["summary"])
        text = (RECEIPTS / "12a-source.json").read_text(encoding="utf-8")
        for fragment in ("PRIVATE KEY", "SIGNING_KEY", str(Path.home())):
            self.assertNotIn(fragment, text)

    def test_recorded_receipts_were_produced_by_the_committed_drivers(self) -> None:
        # Hash with LF line ends, as committed, whatever the checkout translated them to.
        for driver in (lane.WRITER_DRIVER, lane.READER_DRIVER):
            digest = hashlib.sha256(driver.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for receipt in self.receipts.values():
                self.assertEqual(receipt["drivers"][driver.name], digest, driver.name)


class IdentityAndSeparationTests(GuardCase):
    def test_g1_reader_must_be_the_tagged_previous_release(self) -> None:
        for key, value in (("commit", "0" * 40), ("archive_commit", "0" * 40),
                           ("tag_object", "0" * 40), ("version", "2.4.2-beta.6"),
                           ("source", "worktree"), ("isolated", False)):
            with self.subTest(key=key):
                self.guard("G1", "12a", lambda receipt: receipt["reader"].update({key: value}))

    def test_g1_reader_and_writer_must_be_different_releases(self) -> None:
        def same(receipt: dict) -> None:
            receipt["writer"]["version"] = receipt["writer"]["declared_version"] = (
                receipt["reader"]["version"])
            for run in receipt["runs"]:
                run["version"] = receipt["reader"]["version"]
        self.guard("G1", "12b", same)

    def test_g1_writer_must_run_the_version_its_tree_declares(self) -> None:
        self.guard("G1", "12a", lambda receipt: receipt["writer"].update(
            {"declared_version": "2.4.2-beta.9"}), text="declared version")

    def test_g1_every_step_must_run_its_own_tree(self) -> None:
        self.guard("G1", "12c", lambda receipt: receipt["runs"][-1].update(
            {"package_in_own_tree": False}))
        self.guard("G1", "12c", lambda receipt: receipt["runs"][-1].update(
            {"version": receipt["writer"]["version"]}))

    def test_g1_recorded_receipt_needs_an_exact_writer_and_locked_dependencies(self) -> None:
        self.guard("G1", "12a", lambda receipt: receipt["writer"].update(
            {"source": "worktree", "archive_commit": None}))
        self.guard("G1", "12a", lambda receipt: receipt["reader"]["dependencies"].update(
            {"mismatches": ["mcp: locked 1.0, installed 2.0"]}))
        self.guard("G1", "12a", lambda receipt: receipt["writer"].pop("dependencies"))

    def test_g1_a_worktree_writer_is_accepted_only_when_exactness_is_waived(self) -> None:
        receipt = copy.deepcopy(self.receipts["12a"])
        receipt["writer"].update({"source": "worktree", "archive_commit": None})
        for name in ("candidate_tool", "test_signing_tool"):
            receipt[name]["source"] = "worktree"
        receipt["lane"]["drivers_modified"] = ["?? scripts/rollback_proof_lane.py"]
        self.assertEqual(lane.classify(receipt, require_exact=False), [])
        self.assertTrue(lane.classify(receipt))

    def test_g2_trees_and_steps_must_be_separate(self) -> None:
        self.guard("G2", "12a", lambda receipt: receipt.update({"trees_distinct": False}))
        self.guard("G2", "12a", lambda receipt: receipt.update(
            {"runs": [run for run in receipt["runs"] if run["side"] == "writer"]}))
        self.guard("G2", "12a", lambda receipt: receipt["lane"].update(
            {"drivers_modified": [" M tests/fixtures/hamcp377_rollback_proof/tip_writer.py"]}))

    def test_g2_data_must_not_change_between_writer_and_reader(self) -> None:
        for key in ("after_write", "before_read", "after_read"):
            with self.subTest(key=key):
                self.guard("G2", "12c", lambda receipt: receipt["data"]["plans"].update(
                    {key: "0" * 64}))
        self.guard("G2", "12b", lambda receipt: receipt["data"]["device_recovery_27a"]
                   ["earlier"][0].update({"before_read": "0" * 64}))
        self.guard("G2", "12b", lambda receipt: receipt["data"].pop("device_crash"))
        self.guard("G2", "12a", lambda receipt: receipt["cases"]["registry"]["observed"]
                   ["reader"].update({"journal_sha256": "0" * 64}))
        # Each of the four records of the test-signed journal is compared, one at a time.
        text = "the journal the reader loaded is not the journal the writer signed"
        self.guard("G2", "12a", lambda receipt: receipt["data"]["registry"].update(
            {"journal_sha256": "0" * 64}), text=text)
        self.guard("G2", "12a", lambda receipt: receipt["cases"]["registry"]["observed"]["lane"]
                   ["journal"].update({"sha256": "0" * 64}), text=text)


class RegistryTests(GuardCase):
    @staticmethod
    def observed(receipt: dict) -> dict:
        return receipt["cases"]["registry"]["observed"]

    @classmethod
    def relist(cls, receipt: dict, objects: list) -> None:
        """Rewrite every record of the reference list the way a consistent hand edit would."""

        observed = cls.observed(receipt)
        writer, digest = observed["writer"], lane.reference_digest(objects)
        writer["reference_objects"] = objects
        writer["references"] = [item["capability_id"] for item in objects]
        writer["added"] = writer["references"][len(writer["previous_references"]):]
        observed["reader"]["entry_references"] = list(writer["references"])
        observed["reader"]["entry_reference_objects_sha256"] = digest
        for name in ("candidate", "journal"):
            observed["lane"][name]["reference_objects_sha256"] = digest
        receipt["real_candidate_comparison"].update(
            {"lane_reference_count": len(objects), "lane_reference_objects_sha256": digest})

    def test_g3_entry_must_extend_the_published_entry_in_the_ruled_order(self) -> None:
        def swap(receipt: dict) -> None:
            objects = self.observed(receipt)["writer"]["reference_objects"]
            self.relist(receipt, objects[:-2] + [objects[-1], objects[-2]])
        self.assertEqual(self.guard("G3", "12a", swap, consistent=True, text="ruled six"), [
            "G3: the added references are not the ruled six in the ruled order"])

        def prefix_changed(receipt: dict) -> None:
            objects = self.observed(receipt)["writer"]["reference_objects"]
            self.relist(receipt, [objects[1], objects[0], *objects[2:]])
        self.guard("G3", "12a", prefix_changed, consistent=True,
                   text="extension of the published entry")
        for key, value in (("published_journal_sha256", "0" * 64), ("published_sequence", 16),
                           ("published_expires_at", "2099-01-01T00:00:00Z"), ("sequence", 2)):
            with self.subTest(key=key):
                self.guard("G3", "12a", lambda receipt: self.observed(receipt)["writer"].update(
                    {key: value}), text="extension of the published entry")
        self.guard("G3", "12a", lambda receipt: self.observed(receipt)["writer"]
                   ["previous_references"].pop(), text="extension of the published entry")
        # The ruled six are checked in the entry and in the writer's own list of what it added.
        self.guard("G3", "12a", lambda receipt: self.observed(receipt)["writer"]["added"]
                   .reverse(), text="ruled six")

        def swap_in_entry_only(receipt: dict) -> None:
            swap(receipt)
            self.observed(receipt)["writer"]["added"] = list(lane.RULED_ADDED)
        self.guard("G3", "12a", swap_in_entry_only, consistent=True, text="ruled six")
        self.guard("G3", "12a", lambda receipt: self.observed(receipt)["reader"]
                   ["entry_references"].pop(), text="reader decoded a different reference list")
        self.guard("G3", "12a", lambda receipt: self.observed(receipt)["reader"].update(
            {"entry_reference_objects_sha256": "0" * 64}),
            text="reader decoded a different reference list")

    def test_g3_candidate_must_come_from_the_candidate_tool_on_the_published_journal(self) -> None:
        def tool(change) -> None:
            self.guard("G3", "12a", lambda receipt: change(
                self.observed(receipt)["writer"]["tools"]["candidate"]),
                text="was not built by scripts/prepare_core_release_registry.py "
                     "extend-capabilities on the published journal")
        tool(lambda ran: ran.update(
            {"path": "tests/fixtures/hamcp377_rollback_proof/tip_writer.py"}))
        tool(lambda ran: ran.update({"operation": "add"}))
        tool(lambda ran: ran.update({"exit": 1}))
        tool(lambda ran: ran.update({"arguments": [
            "<data>/test-journal.json" if item == "<published-journal>" else item
            for item in ran["arguments"]]}))
        tool(lambda ran: ran.update({"arguments": [
            "<data>/test-public-key.txt" if item.endswith("core-public-key.txt") else item
            for item in ran["arguments"]]}))
        tool(lambda ran: ran.update({"arguments": ran["arguments"] + ["--now", "2026-01-01"]}))

        test_key = self.observed(self.receipts["12a"])["writer"]["test_signed"]["key_sha256"]
        for key, value in (("sequence", 2), ("previous_journal_sha256", "0" * 64),
                           ("public_key_sha256", test_key), ("operation", "add"),
                           ("model", "core-registry-candidate-v1"),
                           ("unchanged_entries", ["2026.10.0"])):
            with self.subTest(key=key):
                self.guard("G3", "12a", lambda receipt: self.observed(receipt)["writer"]
                           ["candidate"].update({key: value}),
                           text="not the next sequence on the published journal under the "
                                "published public key with one changed entry")
        self.guard("G3", "12a", lambda receipt: self.observed(receipt)["writer"]
                   ["published_public_key"].update({"key_sha256": test_key}),
                   text="under the published public key")

    def test_g3_candidate_in_the_data_set_is_the_file_the_tool_reported(self) -> None:
        text = "not the file the tool reported"
        for key in ("sha256", "reported_sha256"):
            with self.subTest(key=key):
                self.guard("G3", "12a", lambda receipt: self.observed(receipt)["writer"]
                           ["candidate"].update({key: "0" * 64}), text=text)
        self.guard("G3", "12a", lambda receipt: receipt["data"]["registry"].update(
            {"candidate_sha256": "0" * 64}), text=text)
        self.guard("G3", "12a", lambda receipt: self.observed(receipt)["lane"]["candidate"]
                   .update({"sha256": "0" * 64}), text=text)
        self.guard("G3", "12a", lambda receipt: self.observed(receipt)["writer"]["candidate"]
                   .update({"file": "hand-built.json"}), text=text)

    def test_g3_recorded_reference_objects_are_the_candidates(self) -> None:
        text = "not the ones in the candidate and in the test-signed journal"

        def objects(receipt: dict) -> list:
            return self.observed(receipt)["writer"]["reference_objects"]
        self.guard("G3", "12a", lambda receipt: objects(receipt)[-1].update(
            {"contract_fingerprint": "sha256:" + "0" * 64}), text=text)
        self.guard("G3", "12a", lambda receipt: objects(receipt)[0].update(
            {"signature": "synthetic"}), text=text)
        self.guard("G3", "12a", lambda receipt: objects(receipt).pop(), text=text)
        for name in ("candidate", "journal"):
            with self.subTest(name=name):
                self.guard("G3", "12a", lambda receipt: self.observed(receipt)["lane"][name]
                           .update({"reference_objects_sha256": "0" * 64}), text=text)
                self.guard("G3", "12a", lambda receipt: self.observed(receipt)["lane"][name]
                           .update({"sequence": 2}), text=text)
        self.guard("G3", "12a", lambda receipt: self.observed(receipt)["lane"]["candidate"]
                   .update({"shape": "signed journal"}), text=text)
        self.guard("G3", "12a", lambda receipt: self.observed(receipt)["lane"]["journal"]
                   .update({"shape": "candidate"}), text=text)

        def both_sequences(receipt: dict) -> None:
            for name in ("candidate", "journal"):
                self.observed(receipt)["lane"][name]["sequence"] = 2
        self.guard("G3", "12a", both_sequences, text=text)
        self.guard("G3", "12a", lambda receipt: self.observed(receipt)["writer"]["references"]
                   .reverse(), text=text)

        # A sixth field stays a failure when every recorded digest is rewritten to include it.
        def sixth_field(receipt: dict) -> None:
            changed = copy.deepcopy(objects(receipt))
            changed[0]["signature"] = "synthetic"
            self.relist(receipt, changed)
        self.guard("G3", "12a", sixth_field, consistent=True, text=text)

        # A consistent lie: one field of one object changed everywhere the receipt records it,
        # except in the files of the data set, whose hashes the receipt also carries.
        def one_field(receipt: dict) -> None:
            changed = copy.deepcopy(objects(receipt))
            changed[-1]["adapter_id"] = "compiled-core-synthetic-v1"
            self.relist(receipt, changed)
            self.observed(receipt)["lane"]["journal"]["reference_objects_sha256"] = (
                self.observed(self.receipts["12a"])["lane"]["journal"]["reference_objects_sha256"])
        self.guard("G3", "12a", one_field, consistent=True, text=text)

    def test_g3_test_signed_journal_differs_from_the_candidate_only_in_signatures(self) -> None:
        text = "differs from the candidate in more than its signatures"

        def signed(receipt: dict) -> dict:
            return self.observed(receipt)["writer"]["test_signed"]
        for key in ("envelope_equals_candidate_envelope", "entry_references_equal_candidate",
                    "predecessor_content_unchanged"):
            with self.subTest(key=key):
                self.guard("G3", "12a", lambda receipt: signed(receipt).update({key: False}),
                           consistent=True, text=text)
                self.guard("G3", "12a", lambda receipt: signed(receipt).update({key: "yes"}),
                           text=text)
        self.guard("G3", "12a", lambda receipt: signed(receipt).update({"sequence": 2}), text=text)
        self.guard("G3", "12a", lambda receipt: signed(receipt).update({"file": "journal.json"}),
                   text=text)
        for key, value in (("path", "scripts/prepare_core_release_registry.py"),
                           ("operation", "sign"), ("exit", 1)):
            with self.subTest(key=key):
                self.guard("G3", "12a", lambda receipt: self.observed(receipt)["writer"]["tools"]
                           ["test_signing"].update({key: value}), text=text)
        self.guard("G3", "12a", lambda receipt: self.observed(receipt)["writer"]["tools"]
                   ["test_signing"].update({"arguments": [
                       "<data>/other.json" if item == "<data>/core-candidate.json" else item
                       for item in self.observed(receipt)["writer"]["tools"]["test_signing"]
                       ["arguments"]]}), text=text)
        self.guard("G3", "12a", lambda receipt: signed(receipt).update(
            {"key_sha256": lane.PUBLISHED_KEY_SHA256}), text="a key of the run's own")
        self.guard("G3", "12a", lambda receipt: signed(receipt).update({"key_sha256": None}),
                   text="a key of the run's own")

    def test_g3_each_tool_is_tied_to_the_writer_commit(self) -> None:
        for name, path in (("candidate_tool", "scripts/prepare_core_release_registry.py"),
                           ("test_signing_tool", "scripts/core_registry_one_tap.py")):
            text = f"does not tie {path} as it ran to the writer commit"
            for key, value in (("commit", "0" * 40), ("sha256", "0" * 64), ("path", "other.py"),
                               ("operation", "sign"), ("source", "worktree"),
                               ("blob_run", "0" * 40), ("blob_at_commit", "0" * 40)):
                with self.subTest(name=name, key=key):
                    self.guard("G3", "12a", lambda receipt: receipt[name].update({key: value}),
                               text=text)
            self.guard("G3", "12a", lambda receipt: receipt.pop(name), text=text)
            self.guard("G3", "12a", lambda receipt: receipt[name].update(
                {"blob_run": "abc", "blob_at_commit": "abc"}), text=text)
            self.guard("G3", "12a", lambda receipt: self.observed(receipt)["writer"]["tools"][
                "candidate" if name == "candidate_tool" else "test_signing"].update(
                    {"sha256": "0" * 64}), text=text)

    def test_g3_a_tool_edited_in_a_checkout_is_accepted_only_when_exactness_is_waived(self) -> None:
        receipt = copy.deepcopy(self.receipts["12a"])
        receipt["writer"].update({"source": "worktree", "archive_commit": None})
        for name in ("candidate_tool", "test_signing_tool"):
            receipt[name].update({"source": "worktree", "blob_run": "1" * 40})
        self.assertEqual(lane.classify(receipt, require_exact=False), [])
        failures = lane.classify(receipt)
        self.assertEqual(sum("as it ran to the writer commit" in item for item in failures), 2,
                         failures)

    def test_g4_a_reference_the_previous_release_does_not_compile_is_never_admitted(self) -> None:
        def admit(receipt: dict) -> None:
            reader = self.observed(receipt)["reader"]
            reader["route_status"]["core.security_metadata_read"] = {
                "available": True, "disposition": "admitted", "reason_codes": []}
            reader["selected"]["core.security_metadata_read"] = [
                "verified_compatibility", "positive"]
        failures = self.guard("G4", "12a", admit, consistent=True)
        self.assertTrue(any(item.startswith("G5: observed") for item in failures), failures)

        def routable_only(receipt: dict) -> None:
            self.observed(receipt)["reader"]["route_status"][
                "core.thread_matter_topology_read"]["available"] = True
        self.guard("G4", "12a", routable_only, consistent=True)
        text = "core.thread_matter_topology_read is selected or routable"
        self.guard("G4", "12a", lambda receipt: self.observed(receipt)["reader"]["selected"]
                   .update({"core.thread_matter_topology_read": ["verified_compatibility",
                                                                 "positive"]}),
                   consistent=True, text=text)
        self.guard("G4", "12a", lambda receipt: self.observed(receipt)["reader"]["route_status"]
                   ["core.thread_matter_topology_read"].update({"disposition": "admitted"}),
                   consistent=True, text=text)

    def test_g4_the_uncompiled_references_are_exactly_the_expected_five(self) -> None:
        self.guard("G4", "12a", lambda receipt: self.observed(receipt)["reader"]["compiled"]
                   .append("core.security_metadata_read"), consistent=True,
                   text="not the expected five")
        self.guard("G4", "12a", lambda receipt: self.observed(receipt)["reader"]["compiled"]
                   .remove("core.marketplace_read"), consistent=True,
                   text="not the expected five")

    def test_g4_unavailable_is_not_admitted(self) -> None:
        # The five are reported "unavailable"; that word must never count as admitted.
        words = lane.registry_words(self.receipts["12a"]["cases"]["registry"])
        route = self.observed(self.receipts["12a"])["reader"]["route_status"]
        self.assertEqual({route[item]["disposition"] for item in lane.NOT_ADMITTED},
                         {"unavailable"})
        self.assertEqual(words["admitted"], 33)

    def test_g5_every_compiled_capability_stays_admitted(self) -> None:
        def hold(receipt: dict) -> None:
            reader = self.observed(receipt)["reader"]
            reader["route_status"]["core.typed_power_operation"] = {
                "available": False, "disposition": "held", "reason_codes": ["synthetic"]}
            reader["counts"]["held_count"] = 1
        self.guard("G5", "12a", hold, consistent=True, text="but not admitted")
        self.guard("G5", "12a", lambda receipt: self.observed(receipt)["reader"]["selected"].pop(
            "core.typed_power_operation"), consistent=True, text="but not admitted")
        for change in ({"available": False}, {"disposition": "held"}):
            with self.subTest(change=change):
                self.guard("G5", "12a", lambda receipt: self.observed(receipt)["reader"]
                           ["route_status"]["core.typed_power_operation"].update(change),
                           consistent=True,
                           text="core.typed_power_operation is compiled by the previous release "
                                "but not admitted")
        self.guard("G5", "12a", lambda receipt: self.observed(receipt)["reader"].update(
            {"refreshed": False}), text="did not accept the journal as current authority")
        for count in ("held_count", "quarantined_count", "unavailable_count", "withheld_count",
                      "fallback_count"):
            with self.subTest(count=count):
                self.guard("G5", "12a", lambda receipt: self.observed(receipt)["reader"]["counts"]
                           .update({count: 1}), consistent=True,
                           text="held, quarantined or withheld")
        self.guard("G5", "12a", lambda receipt: self.observed(receipt)["reader"]["withheld"]
                   ["held_capabilities"].append("core.typed_power_operation"), consistent=True,
                   text="held, quarantined or withheld")
        self.guard("G5", "12a", lambda receipt: self.observed(receipt)["reader"]["counts"].update(
            {"compatible_count": 38}), consistent=True, text="held, quarantined or withheld")
        self.guard("G5", "12a", lambda receipt: self.observed(receipt)["reader"]["authority"]
                   .update({"positive_authority_current": False}))
        # The authority the previous release holds is the test-signed sequence 18, not the
        # published 17 and not a bootstrap journal's 2.
        for sequence in (2, 17):
            with self.subTest(sequence=sequence):
                self.guard("G5", "12a", lambda receipt: self.observed(receipt)["reader"]
                           ["authority"].update({"sequence": sequence}))
        self.guard("G5", "12a", lambda receipt: receipt["cases"]["registry"]["derived"].update(
            {"quarantined": 5, "not_admitted": 0}))

    def test_g6_marketplace_read_is_bound_to_the_pinned_fingerprint(self) -> None:
        self.guard("G6", "12a", lambda receipt: self.observed(receipt)["reader"]["marketplace"]
                   .update({"entry_fingerprint": "sha256:" + "0" * 64}))
        text = "not bound to the pinned contract fingerprint"
        self.guard("G6", "12a", lambda receipt: self.observed(receipt)["reader"]["marketplace"]
                   .update({"compiled_fingerprint": "sha256:" + "0" * 64}), text=text)
        self.guard("G6", "12a", lambda receipt: self.observed(receipt)["reader"]["marketplace"]
                   .update({"entry_fingerprint": "sha256:" + "0" * 64,
                            "compiled_fingerprint": "sha256:" + "0" * 64}), text=text)
        text = "cannot claim a real marketplace read"
        self.guard("G6", "12a", lambda receipt: self.observed(receipt)["real_read"].update(
            {"exercised": True}), text=text)
        self.guard("G6", "12a", lambda receipt: self.observed(receipt)["real_read"].update(
            {"reason": ""}), text=text)
        # That refusal is about the source subject: it has no Core to read from.
        claimed = copy.deepcopy(self.receipts["12a"])
        self.observed(claimed)["real_read"]["exercised"] = True
        self.assertTrue(any(text in item for item in lane.classify_registry(claimed)))
        claimed["subject"] = "image"
        self.assertFalse(any(text in item for item in lane.classify_registry(claimed)))


class RealCandidateComparisonTests(GuardCase):
    """G14 and the comparison it guards. Every candidate here is a synthetic test file."""

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="hamcp377-compare-"))
        self.addCleanup(shutil.rmtree, self.temp)
        self.objects = copy.deepcopy(self.receipts["12a"]["cases"]["registry"]["observed"]
                                     ["writer"]["reference_objects"])

    def file(self, raw: bytes, name: str = "supplied-candidate.json") -> Path:
        path = self.temp / name
        path.write_bytes(raw)
        return path

    def compared(self, raw: bytes) -> dict:
        return lane.compare_real_candidate(self.objects, self.file(raw))

    def with_comparison(self, comparison: dict) -> dict:
        receipt = copy.deepcopy(self.receipts["12a"])
        receipt["real_candidate_comparison"] = comparison
        receipt["summary"] = lane.summarize(receipt)
        return receipt

    def test_no_candidate_is_reported_as_not_yet_available(self) -> None:
        pending = lane.compare_real_candidate(self.objects, None)
        self.assertEqual(pending, self.receipts["12a"]["real_candidate_comparison"])
        self.assertEqual(pending["status"], "not yet available: the real candidate does not exist")
        self.assertEqual(lane.classify(self.with_comparison(pending)), [])

    def test_identical_reference_objects_compare_byte_for_byte_in_every_file_shape(self) -> None:
        for shape in ("candidate", "signed journal", "envelope"):
            for newline in (True, False):
                with self.subTest(shape=shape, newline=newline):
                    raw = synthetic_candidate(self.objects, shape=shape, newline=newline)
                    result = self.compared(raw)
                    self.assertEqual(
                        {key: result[key] for key in (
                            "status", "identical", "differences", "candidate_shape",
                            "candidate_sequence", "candidate_reference_count",
                            "candidate_sha256", "candidate_file")},
                        {"status": "compared", "identical": True, "differences": [],
                         "candidate_shape": shape, "candidate_sequence": 18,
                         "candidate_reference_count": 38,
                         "candidate_sha256": hashlib.sha256(raw).hexdigest(),
                         "candidate_file": "supplied-candidate.json"})
                    self.assertEqual(result["candidate_reference_objects_sha256"],
                                     result["lane_reference_objects_sha256"])
                    self.assertIn("not established by the lane", result["candidate_authenticity"])
                    receipt = self.with_comparison(result)
                    self.assertEqual(lane.classify(receipt), [])
                    self.assertTrue(any("are byte-identical to those of the supplied" in line
                                        for line in receipt["summary"]), receipt["summary"])
                    self.assertNotIn(str(self.temp), json.dumps(result))

    def test_g14_any_differing_byte_of_a_reference_object_fails_the_gate(self) -> None:
        def changed(change) -> list:
            objects = copy.deepcopy(self.objects)
            change(objects)
            return objects
        cases = {
            "fingerprint": (changed(lambda items: items[-1].update(
                {"contract_fingerprint": "sha256:" + "0" * 64})), [37]),
            "adapter": (changed(lambda items: items[3].update({"adapter_id": "other"})), [3]),
            "version": (changed(lambda items: items[0].update({"profile_version": 2})), [0]),
            "order": (changed(lambda items: items.reverse()), list(range(38))),
            "missing": (self.objects[:-1], [37]),
            "extra": (self.objects + [dict(self.objects[0], capability_id="core.synthetic")],
                      [38])}
        for name, (objects, indexes) in cases.items():
            with self.subTest(name=name):
                result = self.compared(synthetic_candidate(objects))
                self.assertIs(result["identical"], False)
                self.assertEqual([item["index"] for item in result["differences"]], indexes)
                receipt = self.with_comparison(result)
                failures = lane.classify(receipt)
                self.assertEqual(failures, [
                    "G14: the real candidate's reference objects are not byte-identical to the "
                    "ones the previous release read"])
                self.assertEqual(lane.gate([receipt, self.receipts["12b"], self.receipts["12c"]]),
                                 ["12a: " + failures[0]])
                self.assertTrue(any("different at" in line for line in receipt["summary"]))
        missing = self.compared(synthetic_candidate(self.objects[:-1]))["differences"][0]
        self.assertEqual((missing["lane"], missing["candidate"]), (self.objects[-1], None))

    def test_g14_a_difference_cannot_be_edited_into_a_match(self) -> None:
        different = copy.deepcopy(self.objects)
        different[5]["contract_fingerprint"] = "sha256:" + "1" * 64
        result = self.compared(synthetic_candidate(different))
        text = "not byte-identical"
        for edit in ({"identical": True}, {"identical": True, "differences": []},
                     {"identical": True, "differences": [],
                      "candidate_reference_objects_sha256":
                          result["lane_reference_objects_sha256"],
                      "candidate_reference_count": 37}):
            with self.subTest(edit=sorted(edit)):
                receipt = self.with_comparison({**result, **edit})
                self.assertTrue(any(item.startswith("G14:") and text in item
                                    for item in lane.classify(receipt)))
        # Nor does a match stand when one of its own fields says otherwise.
        same = self.compared(synthetic_candidate(self.objects))
        for edit in ({"identical": False}, {"identical": "true"},
                     {"differences": result["differences"]}, {"differences": None},
                     {"candidate_reference_count": 37},
                     {"candidate_reference_objects_sha256": "0" * 64}):
            with self.subTest(edit=sorted(edit)):
                self.assertEqual(lane.classify(self.with_comparison({**same, **edit})), [
                    "G14: the real candidate's reference objects are not byte-identical to the "
                    "ones the previous release read"])

    def test_g14_the_comparison_must_be_pending_or_recorded_against_these_objects(self) -> None:
        self.guard("G14", "12a", lambda receipt: receipt.pop("real_candidate_comparison"),
                   text="not recorded against the reference objects")
        for key, value in (("lane_reference_objects_sha256", "0" * 64),
                           ("lane_reference_count", 32), ("compares", "everything")):
            with self.subTest(key=key):
                self.guard("G14", "12a", lambda receipt: receipt["real_candidate_comparison"]
                           .update({key: value}), text="not recorded against the reference objects")
        for status in ("identical", "compared", "not applicable", None):
            with self.subTest(status=status):
                self.guard("G14", "12a", lambda receipt: receipt["real_candidate_comparison"]
                           .update({"status": status}), text="neither pending nor a recorded")
        self.guard("G14", "12a", lambda receipt: receipt["real_candidate_comparison"].update(
            {"identical": True}), text="a pending comparison carries a comparison result")

    def test_g14_the_lanes_own_test_candidate_is_not_a_real_candidate(self) -> None:
        own = self.receipts["12a"]["cases"]["registry"]["observed"]["lane"]
        result = self.compared(synthetic_candidate(self.objects))
        for name in ("candidate", "journal"):
            with self.subTest(name=name):
                receipt = self.with_comparison({**result, "candidate_sha256": own[name]["sha256"]})
                self.assertEqual(lane.classify(receipt), [
                    "G14: the file compared is the lane's own test candidate, not a real one"])
        for sequence in (17, 19):
            with self.subTest(sequence=sequence):
                receipt = self.with_comparison(
                    self.compared(synthetic_candidate(self.objects, sequence=sequence)))
                self.assertEqual(lane.classify(receipt), [
                    "G14: the file compared is not a sequence 18 candidate"])
        for edit in ({"candidate_sha256": "not a digest"}, {"candidate_sha256": "0" * 63},
                     {"candidate_sha256": "A" * 64}, {"status": "identical"},
                     {"status": "compared and accepted"}):
            with self.subTest(edit=edit):
                self.assertEqual(lane.classify(self.with_comparison({**result, **edit})), [
                    "G14: the real-candidate comparison is neither pending nor a recorded "
                    "comparison"])

    def test_a_file_that_cannot_be_compared_is_refused_and_never_compared(self) -> None:
        good = json.loads(synthetic_candidate(self.objects))
        entries = good["envelope"]["entries"]
        refused = {
            "not JSON": b"core-candidate",
            "not UTF-8": b"\xff\xfe",
            "not canonical": json.dumps(good, indent=1).encode(),
            "trailing bytes": synthetic_candidate(self.objects) + b"\n",
            "duplicate key": b'{"envelope":{"entries":[],"sequence":18},"envelope":{}}',
            "non-finite number": b'{"entries":[],"sequence":NaN}',
            "a list": b"[]",
            "no envelope": lane.canonical({"model": "synthetic"}),
            "no entry for the Core version": lane.canonical(
                {"envelope": {"sequence": 18, "entries": entries[:1]}}),
            "two entries for the Core version": lane.canonical(
                {"envelope": {"sequence": 18, "entries": entries + entries[1:]}}),
            "no references": synthetic_candidate([]),
            "a reference with a sixth field": synthetic_candidate(
                [dict(self.objects[0], signature="synthetic"), *self.objects[1:]]),
            "a reference with a missing field": synthetic_candidate(
                [{key: value for key, value in self.objects[0].items() if key != "adapter_id"}]),
            "a reference that is not an object": synthetic_candidate(["core.basic_rest_read"]),
            "a reference that is a list of the field names": synthetic_candidate(
                [list(lane.REFERENCE_FIELDS)]),
            "a reference with a nested value": synthetic_candidate(
                [dict(self.objects[0], adapter_id=["x"])]),
            "a sequence that is not an integer": lane.canonical(
                {"envelope": {"sequence": "18", "entries": entries}}),
            "empty envelopes": lane.canonical({"envelopes": []}),
        }
        because = {
            "not canonical": "not canonical JSON", "trailing bytes": "not canonical JSON",
            "duplicate key": "duplicate object key",
            "non-finite number": "non-finite number NaN",
            "no envelope": "neither a candidate, a signed journal nor an envelope",
            "a sequence that is not an integer": "the sequence is not an integer"}
        for name, raw in refused.items():
            with self.subTest(name=name):
                with self.assertRaises(lane.LaneError) as caught:
                    self.compared(raw)
                self.assertIn("the candidate file cannot be compared: " + because.get(
                    name, "a capability reference is not a five-field reference object"
                    if name.startswith(("a reference", "no references")) else ""),
                    str(caught.exception))
        with mock.patch.object(lane, "MAX_CANDIDATE_BYTES", 64):
            with self.assertRaises(lane.LaneError):
                self.compared(synthetic_candidate(self.objects))
        with self.assertRaises(lane.LaneError) as caught:
            lane.compare_real_candidate(self.objects, self.temp / "absent.json")
        self.assertIn("cannot be read", str(caught.exception))
        self.assertNotIn(str(self.temp), str(caught.exception))

    def test_compare_candidate_command_records_only_when_asked(self) -> None:
        directory = self.temp / "receipts"
        shutil.copytree(RECEIPTS, directory, ignore=shutil.ignore_patterns("*.md"))
        before = (directory / "12a-source.json").read_bytes()
        same = self.file(synthetic_candidate(self.objects))
        different = self.file(synthetic_candidate(self.objects[:-1]), "different.json")

        code, out, err = run_main("compare-candidate", str(directory), str(same))
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.endswith("rollback proof lane: PASS\n"), out)
        self.assertIs(json.loads(out.rsplit("rollback proof lane:", 1)[0])["identical"], True)
        self.assertEqual((directory / "12a-source.json").read_bytes(), before)

        code, out, err = run_main("compare-candidate", str(directory), str(different))
        self.assertEqual(code, 1)
        self.assertIn("12a: G14: the real candidate's reference objects are not byte-identical",
                      err)
        self.assertEqual((directory / "12a-source.json").read_bytes(), before)

        for candidate in (self.file(b"{}", "empty.json"), self.temp / "absent.json"):
            code, out, err = run_main("compare-candidate", str(directory), str(candidate),
                                      "--record")
            self.assertEqual((code, out), (2, ""))
            self.assertIn("no comparison made", err)
            self.assertEqual((directory / "12a-source.json").read_bytes(), before)

        code, out, err = run_main("compare-candidate", str(directory), str(same), "--record")
        self.assertEqual((code, err), (0, ""))
        recorded = json.loads((directory / "12a-source.json").read_text(encoding="utf-8"))
        self.assertEqual((recorded["result"], recorded["failures"]), ("PASS", []))
        self.assertEqual(recorded["real_candidate_comparison"]["status"], "compared")
        self.assertEqual(recorded["summary"], lane.summarize(recorded))
        self.assertEqual(check(directory), (0, "rollback proof lane: PASS\n", ""))

        code, out, err = run_main("compare-candidate", str(directory), str(different), "--record")
        self.assertEqual(code, 1)
        recorded = json.loads((directory / "12a-source.json").read_text(encoding="utf-8"))
        self.assertEqual(recorded["result"], "FAIL")
        code, out, err = check(directory)
        self.assertEqual((code, out), (1, "rollback proof lane: FAIL\n"))
        self.assertIn("G14", err)

    def test_compare_candidate_command_needs_a_recorded_12a_receipt(self) -> None:
        empty = self.temp / "empty"
        empty.mkdir()
        code, out, err = run_main("compare-candidate", str(empty),
                                  str(self.file(synthetic_candidate(self.objects))), "--record")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no usable 12a receipt", err)
        self.assertEqual(list(empty.iterdir()), [])

    def test_a_boolean_sequence_is_not_an_integer_sequence(self) -> None:
        # True is an int to isinstance; a registry sequence is never a boolean.
        raw = lane.canonical({"candidate": "synthetic", "envelope": {
            "sequence": True,
            "entries": [{"version": lane.CORE_VERSION, "capabilities": self.objects}]}})
        with self.assertRaises(lane.LaneError) as raised:
            self.compared(raw)
        self.assertEqual(str(raised.exception), "the candidate file cannot be compared: the "
                                                "sequence is not an integer")

    def test_canonical_bytes_keep_non_ascii_text_as_utf8(self) -> None:
        self.assertEqual(lane.canonical({"name": "café"}), b'{"name":"caf\xc3\xa9"}')
        self.assertEqual(lane.canonical({"b": 1, "a": [True, None]}), b'{"a":[true,null],"b":1}')
        self.assertNotEqual(lane.reference_digest([{"name": "café"}]),
                            lane.reference_digest([{"name": "caf\\u00e9"}]))

    def test_a_bare_envelope_is_recognised_by_its_entries_not_by_an_envelope_key(self) -> None:
        # Only an object under "envelope" makes the file the candidate tool's wrapper.
        raw = lane.canonical({"sequence": 18, "envelope": [], "entries": [
            {"version": lane.CORE_VERSION, "capabilities": self.objects}]})
        found = lane.candidate_reference_objects(raw)
        self.assertEqual((found["shape"], found["sequence"], found["objects"]),
                         ("envelope", 18, self.objects))

    def test_g14_objects_equal_in_python_but_not_in_bytes_are_a_difference(self) -> None:
        # True == 1 in Python, yet the canonical bytes are `true` and `1`.
        self.assertIs(type(self.objects[0]["profile_version"]), int)
        self.assertEqual(self.objects[0]["profile_version"], 1)
        ours = copy.deepcopy(self.objects)
        ours[0]["profile_version"] = True
        self.assertEqual(ours, self.objects)
        result = lane.compare_real_candidate(ours, self.file(synthetic_candidate(self.objects)))
        self.assertIs(result["identical"], False)
        self.assertEqual([item["index"] for item in result["differences"]], [0])
        self.assertNotEqual(result["lane_reference_objects_sha256"],
                            result["candidate_reference_objects_sha256"])

    def test_g14_the_candidates_reference_count_is_the_candidates_own(self) -> None:
        result = self.compared(synthetic_candidate(self.objects[:-1]))
        self.assertEqual((result["lane_reference_count"], result["candidate_reference_count"]),
                         (38, 37))
        self.assertIs(result["identical"], False)
        self.assertEqual([item["index"] for item in result["differences"]], [37])
        receipt = self.with_comparison(result)
        self.assertTrue(any(item.startswith("G14:") for item in lane.classify(receipt)))


class DeviceTests(GuardCase):
    def test_g7_block_needs_unchanged_lock_records_and_no_provider_attempt(self) -> None:
        def record_changed(receipt: dict) -> None:
            receipt["cases"]["hold"]["observed"]["power"]["locks_after"][0][3] += 1
        self.guard("G7", "12b", record_changed)
        self.guard("G7", "12b", record_changed, consistent=True)

        def record_removed(receipt: dict) -> None:
            receipt["cases"]["in_flight_expired"]["observed"]["governed"]["locks_after"].pop()
        self.guard("G7", "12b", record_removed, consistent=True)

        def attempted(receipt: dict) -> None:
            receipt["cases"]["in_flight_active"]["observed"]["held_switch"].update(
                {"provider_attempt_count": 1, "provider_writes": 1})
        self.guard("G7", "12b", attempted, consistent=True)

        def other_task(receipt: dict) -> None:
            receipt["cases"]["hold"]["observed"]["held_switch"]["blocking"][0]["task_id"] = "x"
        self.guard("G7", "12b", other_task, consistent=True)

    def test_g7_lease_word_comes_from_the_blocking_record(self) -> None:
        self.guard("G7", "12b", lambda receipt: receipt["cases"]["in_flight_active"]["derived"]
                   .update({"lease": ["conflict_hold"]}))

    def test_g8_scope_and_readiness_must_be_what_was_observed(self) -> None:
        self.guard("G8", "12b", lambda receipt: receipt["cases"]["in_flight_expired"]["derived"]
                   .update({"scope": "held_entity_only"}))
        self.guard("G8", "12b", lambda receipt: receipt["cases"]["in_flight_expired"]["derived"]
                   .update({"governed": "ready"}))

        def unrelated_blocked(receipt: dict) -> None:
            observed = receipt["cases"]["hold"]["observed"]
            observed["other_switch"] = copy.deepcopy(observed["held_switch"])
        self.guard("G8", "12b", unrelated_blocked, consistent=True)

        def recovers_alone(receipt: dict) -> None:
            governed = receipt["cases"]["in_flight_expired"]["observed"]["governed"]
            governed["readiness"] = [dict(lane.READY) for _ in range(3)]
            governed["require_execution_ready"] = {"ok": True, "code": None, "text": None}
        self.guard("G8", "12b", recovers_alone, consistent=True)

    def test_g8_summary_cannot_be_edited_by_hand(self) -> None:
        for scenario in ("12a", "12b", "12c"):
            with self.subTest(scenario=scenario):
                self.guard("G8", scenario, lambda receipt: receipt["summary"].append(
                    "Rollback is safe."))
                self.guard("G8", scenario, lambda receipt: receipt["summary"].pop(0))

    def test_g11_previous_release_must_load_the_store(self) -> None:
        def crashed(receipt: dict) -> None:
            receipt["cases"]["hold"]["observed"]["governed"]["passes"][0].update(
                {"ok": False, "code": "synthetic"})
        self.guard("G11", "12b", crashed, consistent=True)


class RecoveryTierTests(GuardCase):
    @staticmethod
    def steps(receipt: dict) -> dict:
        return receipt["recovery"]["27a"]["steps"]

    def test_g9_tier_27a_needs_exercised_evidence_for_every_step(self) -> None:
        self.guard("G9", "12b", lambda receipt: self.steps(receipt).pop("previous_release_read"))
        self.guard("G9", "12b", lambda receipt: self.steps(receipt).pop("blocked_before_recovery"))
        self.guard("G9", "12b", lambda receipt: self.steps(receipt).pop("settle_release"))
        self.guard("G9", "12b", lambda receipt: receipt["recovery"]["27a"].pop("steps"))
        self.guard("G9", "12b", lambda receipt: self.steps(receipt)["settle_release"]["settled"]
                   .update({"terminal": False}), consistent=True)
        self.guard("G9", "12b", lambda receipt: self.steps(receipt)["settle_release"]["settled"]
                   .update({"provider_writes": 1}), consistent=True)
        self.guard("G9", "12b", lambda receipt: self.steps(receipt)["settle_release"]["release"]
                   .update({"status": "hold_release_refused"}), consistent=True)
        self.guard("G9", "12b", lambda receipt: self.steps(receipt)["settle_release"]["release"]
                   .update({"path": "state.json edited"}), consistent=True)
        self.guard("G9", "12b", lambda receipt: self.steps(receipt)["settle_release"]["release"]
                   .update({"locks_after": [["entity:switch.plug_child_lock"]]}), consistent=True)
        self.guard("G9", "12b", lambda receipt: self.steps(receipt)["settle_release"].update(
            {"task_id": "another"}), consistent=True)
        self.guard("G9", "12b", lambda receipt: self.steps(receipt)["previous_release_read"]
                   ["observed"].update({"task_id": "another"}), consistent=True)

    def test_g9_tier_27a_needs_a_crashed_operation_holding_lock_records(self) -> None:
        for key, value in (("locks", []), ("died_in", None), ("provider_writes", 0)):
            with self.subTest(key=key):
                self.guard("G9", "12b", lambda receipt: self.steps(receipt)["crash"].update(
                    {key: value}), consistent=True, text="no crashed operation")

    def test_g9_a_recorded_tier_needs_exercised_evidence_even_when_not_required(self) -> None:
        for record in ({"exercised": False},
                       {"exercised": False, "available": False, "searched": "nothing found"},
                       {}):
            with self.subTest(record=record):
                receipt = copy.deepcopy(self.receipts["12b"])
                receipt["recovery"]["27a"] = record
                rederive(receipt)
                self.assertEqual(lane.classify(receipt, recovery_tiers=()),
                                 ["G9: 27a is recorded without exercised evidence"])

    def test_g9_recovery_must_end_with_the_previous_release_running_control(self) -> None:
        def still_blocked(receipt: dict) -> None:
            steps = self.steps(receipt)
            steps["previous_release_read"] = copy.deepcopy(steps["blocked_before_recovery"])
        failures = self.guard("G7", "12b", still_blocked, consistent=True)
        self.assertTrue(any("27a after recovery" in item for item in failures), failures)

        def never_blocked(receipt: dict) -> None:
            steps = self.steps(receipt)
            steps["blocked_before_recovery"] = copy.deepcopy(steps["previous_release_read"])
        failures = self.guard("G7", "12b", never_blocked, consistent=True)
        self.assertTrue(any("27a before recovery" in item for item in failures), failures)

    def test_g9_required_tier_must_be_exercised_not_declared(self) -> None:
        self.guard("G9", "12b", lambda receipt: receipt["recovery"].pop("27a"), consistent=True)
        self.guard("G9", "12b", lambda receipt: receipt["recovery"].update(
            {"27a": {"exercised": False, "available": False, "searched": "nothing found"}}),
            consistent=True)
        self.guard("G9", "12b", lambda receipt: receipt["recovery"]["27a"].update(
            {"exercised": False}), consistent=True)

    def test_g9_a_tier_without_a_classifier_fails(self) -> None:
        self.guard("G9", "12b", lambda receipt: receipt["recovery"].update(
            {"unreviewed": {"exercised": True}}), consistent=True)

    def test_a_tier_can_be_left_out_or_added_without_restructuring(self) -> None:
        receipt = copy.deepcopy(self.receipts["12b"])
        receipt["recovery"].pop("27a")
        receipt["data"].pop("device_recovery_27a")
        rederive(receipt)
        self.assertEqual(lane.classify(receipt, recovery_tiers=()), [])
        self.assertTrue(lane.classify(receipt, recovery_tiers=("27a",)))

        receipt = copy.deepcopy(self.receipts["12b"])
        receipt["recovery"]["synthetic"] = {"exercised": True, "proved": False}
        rederive(receipt)
        with mock.patch.dict(lane.RECOVERY_TIERS, {"synthetic": lambda tier: (
                [] if tier.get("proved") else ["G9: synthetic tier not proved"])}):
            self.assertEqual(lane.classify(receipt, recovery_tiers=("27a", "synthetic")),
                             ["G9: synthetic tier not proved"])
            receipt["recovery"]["synthetic"]["proved"] = True
            self.assertEqual(lane.classify(receipt, recovery_tiers=("27a", "synthetic")), [])
            self.assertEqual(lane.classify(receipt), [])

    def test_only_reviewed_tiers_are_registered(self) -> None:
        self.assertEqual(sorted(lane.RECOVERY_TIERS), ["27a"])
        self.assertEqual(lane.DEFAULT_RECOVERY_TIERS, ("27a",))


class PlanTests(GuardCase):
    @staticmethod
    def refuse(mode: str):
        def change(receipt: dict) -> None:
            observed = receipt["cases"][mode]["observed"]
            target = lane.plan_target(observed)
            target["calls"] = {name: dict(REFUSED_CALL) for name in (
                *lane.PLAN_CALLS, "status_after")} | {"approval_decision": None}
            observed["provider_writes"] = []
        return change

    def test_g10_a_receipt_saying_refused_for_automation_or_script_fails(self) -> None:
        for mode in lane.EXECUTION_RECORDED:
            with self.subTest(mode=mode, lie="recorded word only"):
                failures = self.guard("G10", "12c", lambda receipt: receipt["cases"][mode]
                                      ["derived"].update({"outcome": "refused"}))
                self.assertTrue(any("says the previous release refused" in item
                                    for item in failures), failures)
            with self.subTest(mode=mode, lie="observations rewritten too"):
                failures = self.guard("G10", "12c", self.refuse(mode), consistent=True)
                self.assertTrue(any("says the previous release refused" in item
                                    for item in failures), failures)
                receipt = copy.deepcopy(self.receipts["12c"])
                self.refuse(mode)(receipt)
                rederive(receipt)
                receipt["result"], receipt["failures"] = "PASS", []
                self.assertTrue(lane.gate([self.receipts["12a"], self.receipts["12b"], receipt]))

    def test_g10_recorded_outcome_must_be_the_observed_outcome(self) -> None:
        for mode, word in (("scene_pending", "executed"), ("automation_created", "refused"),
                           ("helper_rollback_approved", "no_rollback_plan_no_write")):
            with self.subTest(mode=mode):
                self.guard("G10", "12c", lambda receipt: receipt["cases"][mode]["derived"].update(
                    {"outcome": word}), text="but the observations give")

    def test_g10_a_provider_write_fails_a_refusal_even_if_the_derivation_ignores_it(self) -> None:
        def wrote(receipt: dict) -> None:
            receipt["cases"]["scene_approved"]["observed"]["provider_writes"].append(
                ["rest", "POST", "/config/scene/config/synthetic_evening"])
        derive = lane.plan_outcome
        with mock.patch.object(lane, "plan_outcome", lambda observed: derive(
                {**observed, "provider_writes": []} if "plan" in observed["plans"] else observed)):
            receipt = copy.deepcopy(self.receipts["12c"])
            self.assertEqual(lane.classify(receipt), [])
            wrote(receipt)
            self.assertEqual(lane.classify(receipt), [
                "G10: scene_approved was refused yet the provider was written"])

    def test_g10_refusal_needs_every_call_refused_and_no_provider_write(self) -> None:
        def wrote(receipt: dict) -> None:
            receipt["cases"]["scene_approved"]["observed"]["provider_writes"].append(
                ["rest", "POST", "/config/scene/config/synthetic_evening"])
        self.guard("G10", "12c", wrote)
        self.guard("G10", "12c", wrote, consistent=True)

        def applied(receipt: dict) -> None:
            lane.plan_target(receipt["cases"]["helper_rollback_approved"]["observed"])["calls"][
                "apply"] = {"ok": True, "code": None, "status": "applied", "writes": []}
        self.guard("G10", "12c", applied, consistent=True)

        def other_code(receipt: dict) -> None:
            lane.plan_target(receipt["cases"]["scene_pending"]["observed"])["calls"][
                "approve"]["code"] = "approval_already_consumed"
        self.guard("G10", "12c", other_code, consistent=True)

    def test_g10_execution_is_exactly_the_delete_the_plan_names(self) -> None:
        def another_object(receipt: dict) -> None:
            observed = receipt["cases"]["script_rollback_approved"]["observed"]
            write = ["rest", "DELETE", "/config/script/config/another_script"]
            observed["provider_writes"] = [write]
            lane.plan_target(observed)["calls"]["apply"]["writes"] = [write]
        self.guard("G10", "12c", another_object, consistent=True)

        def two_writes(receipt: dict) -> None:
            observed = receipt["cases"]["automation_rollback_approved"]["observed"]
            observed["provider_writes"] = observed["provider_writes"] * 2
        self.guard("G10", "12c", two_writes, consistent=True)

        def no_approval(receipt: dict) -> None:
            lane.plan_target(receipt["cases"]["automation_rollback_pending"]["observed"])[
                "calls"]["approval_decision"] = None
        self.guard("G10", "12c", no_approval, consistent=True)

        def control_wrote(receipt: dict) -> None:
            receipt["cases"]["automation_created"]["observed"]["provider_writes"].append(
                ["rest", "DELETE", "/config/automation/config/porch_light"])
        self.guard("G10", "12c", control_wrote, consistent=True)

    def test_g11_previous_release_must_load_every_plan_and_stay_ready(self) -> None:
        def undecodable(receipt: dict) -> None:
            decode = receipt["cases"]["scene_pending"]["observed"]["decode"]
            decode[next(iter(decode))].update({"ok": False, "error": "synthetic"})
        self.guard("G11", "12c", undecodable)
        self.guard("G11", "12c", lambda receipt: receipt["cases"]["scene_applied"]["observed"]
                   .update({"ready_final": {"execution_ready": False,
                                            "faults": ["storage_read"]}}))
        self.guard("G11", "12c", lambda receipt: receipt["cases"]["scene_applied"]["observed"]
                   .update({"startup_assertions": ["synthetic"]}))


class LaneAndManifestTests(GuardCase):
    def test_g12_source_subject_starts_no_container_and_no_network(self) -> None:
        self.guard("G12", "12a", lambda receipt: receipt["lane"].update({"containers": 1}))
        self.guard("G12", "12b", lambda receipt: receipt["lane"].update({"network": "bridge"}))

    def test_g13_every_case_and_the_limitation_must_be_present(self) -> None:
        self.guard("G13", "12b", lambda receipt: receipt["cases"].pop("in_flight_expired"))
        self.guard("G13", "12c", lambda receipt: receipt["cases"].pop("script_rollback_approved"),
                   consistent=True)
        self.guard("G13", "12a", lambda receipt: receipt.update({"limitations": []}))
        self.guard("G13", "12a", lambda receipt: receipt.update({"schema": "other"}))
        self.guard("G13", "12c", lambda receipt: receipt.pop("cases"))
        self.guard("G13", "12b", lambda receipt: receipt["cases"]["hold"].pop("observed"))

    def test_g13_gate_needs_every_scenario_recorded_as_pass(self) -> None:
        receipts = list(self.receipts.values())
        self.assertEqual(lane.gate(receipts), [])
        for missing in self.receipts:
            with self.subTest(missing=missing):
                failures = lane.gate([item for item in receipts if item["scenario"] != missing])
                self.assertEqual(failures, [f"G13: no source receipt for scenario {missing}"])
        failed = copy.deepcopy(self.receipts["12b"])
        failed["result"], failed["failures"] = "FAIL", ["G7: synthetic"]
        self.assertEqual(lane.gate([self.receipts["12a"], failed, self.receipts["12c"]]),
                         ["G13: the 12b receipt does not record PASS"])
        image = copy.deepcopy(self.receipts["12a"])
        image["subject"] = "image"
        self.assertEqual(lane.gate([image, self.receipts["12b"], self.receipts["12c"]]),
                         ["G13: a receipt names the unknown subject 'image'",
                          "G13: no source receipt for scenario 12a"])
        with tempfile.TemporaryDirectory() as empty:
            code, out, err = check(Path(empty))
        self.assertEqual((code, out), (1, "rollback proof lane: FAIL\n"))
        self.assertEqual(err.splitlines(), [
            f"G13: no source receipt for scenario {scenario}" for scenario in lane.CLASSIFIERS])


# --- single-fact receipt edits ---------------------------------------------------------------
def at(node, *path):
    for key in path:
        node = node[key]
    return node


def put(*path_and_value):
    """An edit that sets one recorded value."""

    *path, key, value = path_and_value

    def change(receipt: dict) -> None:
        at(receipt, *path)[key] = copy.deepcopy(value)
    return change


def add(*path_and_value):
    """An edit that appends one item to a recorded list."""

    *path, value = path_and_value

    def change(receipt: dict) -> None:
        at(receipt, *path).append(copy.deepcopy(value))
    return change


def repeat_first(*path, **changes):
    """An edit that appends a changed copy of a recorded list's first item."""

    def change(receipt: dict) -> None:
        items = at(receipt, *path)
        items.append(dict(items[0], **changes))
    return change


def both(*changes):
    def change(receipt: dict) -> None:
        for item in changes:
            item(receipt)
    return change


def reader_version_of_another_release(receipt: dict) -> None:
    receipt["reader"]["version"] = "2.4.2-beta.6"
    for run in receipt["runs"]:
        if run["side"] == "reader":
            run["version"] = "2.4.2-beta.6"


def writer_version_unknown(receipt: dict) -> None:
    receipt["writer"]["version"] = receipt["writer"]["declared_version"] = None
    for run in receipt["runs"]:
        if run["side"] == "writer":
            run["version"] = None


def writer_from_the_worktree(receipt: dict) -> None:
    receipt["writer"]["source"] = "worktree"
    for key in ("candidate_tool", "test_signing_tool"):
        if key in receipt:
            receipt[key]["source"] = "worktree"


def writer_commit_abbreviated(receipt: dict) -> None:
    short = receipt["writer"]["commit"][:8]
    receipt["writer"]["commit"] = receipt["writer"]["archive_commit"] = short
    for key in ("candidate_tool", "test_signing_tool"):
        if key in receipt:
            receipt[key]["commit"] = short


def reader_compiles_an_unpublished_reference(receipt: dict) -> None:
    """Still five references unknown and 33 admitted, but not the expected five."""

    observed = at(receipt, *O)
    reader, status = observed["reader"], observed["reader"]["route_status"]
    known = lane.NOT_ADMITTED[0]
    unknown = next(name for name in observed["writer"]["references"]
                   if name in reader["compiled"] and name != lane.MARKETPLACE)
    reader["compiled"] = [name for name in reader["compiled"] if name != unknown] + [known]
    reader["selected"][known] = reader["selected"].pop(unknown)
    status[known], status[unknown] = status[unknown], status[known]


def sequences_one_further(receipt: dict) -> None:
    observed = at(receipt, *O)
    for record in (observed["writer"]["candidate"], observed["writer"]["test_signed"],
                   observed["lane"]["candidate"], observed["lane"]["journal"]):
        record["sequence"] = 19


def readiness_cut_to_two(receipt: dict) -> None:
    governed = at(receipt, *HOLD, "governed")
    governed["readiness"] = governed["readiness"][:2]


def readiness_without_faults(receipt: dict) -> None:
    for state in at(receipt, *EXPIRED, "governed", "readiness"):
        state["faults"] = []


def blocked_read_of_another_task(receipt: dict) -> None:
    observed = at(receipt, *STEPS, "blocked_before_recovery", "observed")
    observed["task_id"] = OTHER_TASK
    for switch in ("held_switch", "other_switch"):
        for item in observed[switch].get("blocking") or []:
            item["task_id"] = OTHER_TASK


def rollback_plan_in_the_plan_slot(receipt: dict) -> None:
    plans = at(receipt, "cases", "automation_rollback_approved", "observed", "plans")
    plans["plan"], plans["rollback"] = plans["rollback"], plans["created"]


def limitation_reworded_to_a_real_core(receipt: dict) -> None:
    receipt["limitations"] = [
        "Synthetic provider: a real Home Assistant Core took part."
        if item == lane.SYNTHETIC_PROVIDER_LIMITATION else item
        for item in receipt["limitations"]]


def plan(case: str, *path) -> tuple:
    return ("cases", case, "observed", "plans", *path)


ALL = ("12a", "12b", "12c")
O = ("cases", "registry", "observed")
HOLD = ("cases", "hold", "observed")
EXPIRED = ("cases", "in_flight_expired", "observed")
STEPS = ("recovery", "27a", "steps")
SETTLE = (*STEPS, "settle_release")
READ = (*STEPS, "previous_release_read", "observed")
OTHER_TASK = "0" * 32
INVENTED_LOCK = ["entity:switch.invented", "exclusive", "x"]
FAULTED = {"execution_ready": False, "faults": ["storage_read"]}
APPROVED, PENDING = "automation_rollback_approved", "automation_rollback_pending"

READER_IDENTITY = "the reader is not the previous release at its tagged commit"
WRITER_ARCHIVE = "the writer source is not a git archive of one exact commit"
REGISTRY_DATA = "data set registry changed between the writer and the end of the read"
HOLD_BLOCKS = "hold behaviour is 'unknown', expected 'block'"
HOLD_SCOPE = "hold scope is 'unknown', expected 'held_entity_only'"
EXPIRED_SCOPE = "in_flight_expired scope is 'unknown', expected 'unrelated_entity_also_blocked'"
HOLD_READY = "hold governed is 'unknown', expected 'ready'"
EXPIRED_FAULTED = "in_flight_expired governed is 'unknown', expected 'faulted'"
SETTLED_ONCE = ("27a does not show the current release settling the crashed task without "
                "dispatching again")
HOLD_RELEASED = ("27a does not show the hold released through the current release's own "
                 "hold-release path")
PLAN_REFUSED = "scene_pending is 'unknown', expected 'refused'"
PLAN_EXECUTED = "automation_rollback_approved is 'unknown', expected 'executed'"
PLAN_APPROVED = "automation_rollback_pending is 'unknown', expected 'approved_then_executed'"
PLAN_NONE = "automation_created is 'unknown', expected 'no_rollback_plan_no_write'"
STORE_LOADED = "scene_pending: the previous release did not load the store and stay ready"

# One row per receipt edit the adversarial record demonstrated on aa21ee37: its ids, the
# scenarios it applies to, the guard that must name it, a fragment of that guard's failure,
# and the edit. Each is applied to a copy of a committed receipt whose derived words and
# summary are then rewritten to agree with the edit.
CLOSING_EDITS = (
    (("G1-03",), ALL, "G1", READER_IDENTITY, reader_version_of_another_release),
    (("G1-04",), ALL, "G1", READER_IDENTITY, put("reader", "tag", "v2.4.2-beta.6")),
    (("G1-09",), ALL, "G1", "the writer is not a different release from the reader",
     writer_version_unknown),
    (("G1-16", "G1-22", "G1-24"), ALL, "G1", WRITER_ARCHIVE, writer_from_the_worktree),
    (("G1-17",), ALL, "G1", WRITER_ARCHIVE, writer_commit_abbreviated),
    (("G1-18",), ALL, "G1", WRITER_ARCHIVE, put("writer", "archive_commit", "1" * 40)),
    (("G1-20",), ALL, "G1", "the writer interpreter does not hold its tree's locked dependencies",
     put("writer", "dependencies", "pins", 0)),
    (("G2-05",), ("12a",), "G2", REGISTRY_DATA, put("data", "registry", "files", 0)),
    (("G2-08",), ("12a",), "G2", REGISTRY_DATA, both(
        put("data", "registry", "after_write", ""), put("data", "registry", "before_read", ""),
        put("data", "registry", "after_read", ""))),
    (("G2-10",), ("12a",), "G2", "the drivers that ran are not the drivers of one exact commit",
     put("lane", "checkout_commit", "cd208084")),
    (("G3-06",), ("12a",), "G3",
     "the entry was not built as an extension of the published entry on the published journal",
     put(*O, "writer", "sequence", 19)),
    (("G3-19",), ("12a",), "G3",
     "the candidate is not the next sequence on the published journal under the published "
     "public key", sequences_one_further),
    (("G4-01",), ("12a",), "G4", "the references the reader does not compile are not the "
     "expected five", reader_compiles_an_unpublished_reference),
    (("G4-04",), ("12a",), "G4",
     f"{lane.NOT_ADMITTED[0]} is selected or routable on the previous release",
     put(*O, "reader", "route_status", lane.NOT_ADMITTED[0], "available", "unavailable")),
    (("G5-07",), ("12a",), "G5", "the previous release did not accept the journal as current "
     "authority", put(*O, "reader", "authority", "surface_denied", True)),
    (("G6-05",), ("12a",), "G6", "a source-subject receipt has no Core and cannot claim a real "
     "marketplace read", lambda receipt: at(receipt, *O, "real_read").pop("exercised")),

    (("G7-01",), ("12b",), "G7", HOLD_BLOCKS, put(*HOLD, "held_switch", "ok", False)),
    (("G7-02",), ("12b",), "G7", HOLD_BLOCKS,
     put(*HOLD, "held_switch", "terminal_outcome", "other")),
    (("G7-03",), ("12b",), "G7", HOLD_BLOCKS, put(*HOLD, "held_switch", "state", "other")),
    (("G7-04", "G7-06"), ("12b",), "G7", HOLD_BLOCKS,
     put(*HOLD, "held_switch", "provider_attempt_count", 1)),
    (("G7-05",), ("12b",), "G7", HOLD_BLOCKS, put(*HOLD, "held_switch", "provider_writes", 1)),
    (("G7-08", "G7-09"), ("12b",), "G7", HOLD_BLOCKS,
     repeat_first(*HOLD, "held_switch", "blocking", task_id=OTHER_TASK)),
    (("G7-15", "G7-18"), ("12b",), "G7", HOLD_BLOCKS,
     add(*HOLD, "power", "locks_after", INVENTED_LOCK)),
    (("G7-16",), ("12b",), "G7", HOLD_BLOCKS, add(*HOLD, "governed", "locks_after", INVENTED_LOCK)),
    (("G7-17",), ("12b",), "G7", HOLD_BLOCKS, both(
        put(*HOLD, "governed", "locks_before", []), put(*HOLD, "governed", "locks_after", []))),
    (("G7-19",), ("12b",), "G7", HOLD_BLOCKS, both(
        put(*HOLD, "power", "locks_before", []), put(*HOLD, "power", "locks_after", []),
        put(*HOLD, "governed", "locks_before", []), put(*HOLD, "governed", "locks_after", []))),
    (("G7-10",), ("12b",), "G8", HOLD_SCOPE, put(*HOLD, "other_switch", "ok", False)),
    (("G7-11",), ("12b",), "G8", HOLD_SCOPE, put(*HOLD, "other_switch", "state", "other")),
    (("G7-12",), ("12b",), "G8", HOLD_SCOPE,
     put(*HOLD, "other_switch", "provider_attempt_count", 2)),
    (("G7-13",), ("12b",), "G8", HOLD_SCOPE, put(*HOLD, "other_switch", "provider_writes", 2)),
    (("G7-14",), ("12b",), "G8", HOLD_SCOPE, both(
        put(*HOLD, "other_switch", "provider_attempt_count", 2),
        put(*HOLD, "other_switch", "provider_writes", 2))),
    (("G7-07",), ("12b",), "G8", EXPIRED_SCOPE, put(*EXPIRED, "other_switch", "blocking", [])),
    (("G8-03",), ("12b",), "G8", EXPIRED_SCOPE, put(*EXPIRED, "other_switch", "ok", False)),
    (("G8-04", "G8-06"), ("12b",), "G8", HOLD_READY, readiness_cut_to_two),
    (("G8-07", "G8-09"), ("12b",), "G8", HOLD_READY,
     put(*HOLD, "governed", "readiness", 0, FAULTED)),
    (("G8-08",), ("12b",), "G8", HOLD_READY,
     put(*HOLD, "governed", "require_execution_ready", "ok", False)),
    (("G8-10",), ("12b",), "G8", EXPIRED_FAULTED, readiness_without_faults),
    (("G8-11",), ("12b",), "G8", EXPIRED_FAULTED,
     put(*EXPIRED, "governed", "readiness", 0, "execution_ready", True)),
    (("G8-12",), ("12b",), "G8", EXPIRED_FAULTED,
     put(*EXPIRED, "governed", "require_execution_ready", "ok", True)),
    (("G8-13",), ("12b",), "G7",
     "in_flight_expired lease is ['active', 'expired_pending_recovery']",
     repeat_first(*EXPIRED, "held_switch", "blocking", lease_status="active")),
    (("G11-02",), ("12b",), "G11",
     "hold: the previous release's governed runtime did not load the store",
     put(*HOLD, "governed", "sdk_ok", False)),

    (("G7-20", "G7-21"), ("12b",), "G7", "27a after recovery behaviour is 'unknown', expected "
     "'proceed'", add(*READ, "power", "locks_before", INVENTED_LOCK)),
    (("G9-05",), ("12b",), "G9", "27a has no crashed operation holding lock records to recover",
     put(*STEPS, "crash", "provider_writes", 2)),
    (("G9-06",), ("12b",), "G9", "27a does not show the previous release blocked on this store "
     "before the recovery", blocked_read_of_another_task),
    (("G9-11",), ("12b",), "G9", SETTLED_ONCE, put(*SETTLE, "recovery_passes", 0)),
    (("G9-16",), ("12b",), "G9", HOLD_RELEASED,
     put(*SETTLE, "release", "dispatch", "call_service")),
    (("G9-17",), ("12b",), "G9", HOLD_RELEASED, put(*SETTLE, "release", "provider_writes", 1)),

    (("G10-02",), ("12c",), "G10", PLAN_REFUSED,
     put(*plan("scene_pending", "plan", "calls", "get_plan"), "ok", True)),
    (("G10-05",), ("12c",), "G10", PLAN_REFUSED,
     put(*plan("scene_pending", "plan", "calls", "rollback_change"), "ok", True)),
    (("G10-06", "G10-07"), ("12c",), "G10", PLAN_EXECUTED,
     add(*plan(APPROVED, "rollback", "operations"), ["delete", "automation", "other"])),
    (("G10-08",), ("12c",), "G10", PLAN_EXECUTED,
     put(*plan(APPROVED, "rollback", "calls", "apply"), "ok", False)),
    (("G10-09",), ("12c",), "G10", PLAN_EXECUTED,
     put(*plan(APPROVED, "rollback", "calls", "apply"), "status", "other")),
    (("G10-10",), ("12c",), "G10", PLAN_EXECUTED,
     put(*plan(APPROVED, "rollback", "calls", "apply"), "writes", [])),
    (("G10-16",), ("12c",), "G10", PLAN_EXECUTED,
     put(*plan(APPROVED, "rollback"), "writer_status", "other")),
    (("G10-17",), ("12c",), "G10", PLAN_EXECUTED,
     put(*plan(APPROVED, "rollback", "calls"), "approval_decision",
         {"ok": False, "code": "x", "status": None})),
    (("G10-26",), ("12c",), "G10", PLAN_EXECUTED, rollback_plan_in_the_plan_slot),
    (("G10-13",), ("12c",), "G10", PLAN_APPROVED,
     put(*plan(PENDING, "rollback"), "writer_status", "other")),
    (("G10-14",), ("12c",), "G10", PLAN_APPROVED,
     put(*plan(PENDING, "rollback", "calls", "approve"), "status", "other")),
    (("G10-15",), ("12c",), "G10", PLAN_APPROVED,
     put(*plan(PENDING, "rollback", "calls", "approval_decision"), "status", "other")),
    (("G10-18",), ("12c",), "G10", PLAN_NONE,
     put(*plan("automation_created"), "extra",
         {"calls": {}, "operations": [], "writer_status": "none"})),
    (("G10-20",), ("12c",), "G10", PLAN_NONE,
     put(*plan("automation_created", "created", "calls", "rollback_change"), "code", "other")),
    (("G11-05",), ("12c",), "G11", STORE_LOADED,
     put("cases", "scene_pending", "observed", "ready_after_startup", FAULTED)),
    (("G11-08",), ("12c",), "G11", STORE_LOADED,
     put("cases", "scene_pending", "observed", "decode", {})),
    (("G13-03",), ("12c",), "G13", "the receipt does not state the synthetic-provider limitation",
     limitation_reworded_to_a_real_core),
)
# The ids the record lists under its findings M1 (identity and data) and L3. G13-07 and G13-08
# are edits of the recorded result, which only the gate reads; they have their own test below.
RECORD_M1 = ("G1-03 G1-04 G1-09 G1-16 G1-17 G1-18 G1-20 G2-05 G2-08 G2-10").split()
RECORD_L3 = (
    "G3-06 G3-19 G4-01 G4-04 G5-07 G6-05 "
    "G7-01 G7-02 G7-03 G7-04 G7-05 G7-07 G7-08 G7-10 G7-11 G7-12 G7-13 G7-14 G7-17 G7-19 "
    "G7-20 G7-21 G8-03 G8-04 G8-06 G8-07 G8-08 G8-09 G8-10 G8-11 G8-12 G8-13 "
    "G9-05 G9-06 G9-11 G9-16 G9-17 "
    "G10-02 G10-05 G10-06 G10-07 G10-08 G10-09 G10-10 G10-13 G10-14 G10-15 G10-16 G10-17 "
    "G10-18 G10-20 G10-26 G11-02 G11-05 G11-08 G13-03 G13-07 G13-08").split()
GATE_EDITS = (("G13-07", {"failures": ["x"]}), ("G13-08", {"result": "FAIL"}))


class DemonstratedEditTests(GuardCase):
    """One subtest per receipt edit the adversarial record showed a disabled guard would miss."""

    def test_each_demonstrated_edit_fails_its_guard_by_name(self) -> None:
        for records, scenarios, guard, text, change in CLOSING_EDITS:
            for record in records:
                for scenario in scenarios:
                    with self.subTest(record=record, scenario=scenario):
                        self.guard(guard, scenario, change, consistent=True, text=text)

    def test_g13_gate_reads_the_recorded_result_and_the_recorded_failures(self) -> None:
        for record, change in GATE_EDITS:
            with self.subTest(record=record):
                edited = dict(self.receipts["12c"], **change)
                self.assertEqual(lane.classify(edited), [])
                self.assertEqual(
                    lane.gate([self.receipts["12a"], self.receipts["12b"], edited]),
                    ["G13: the 12c receipt does not record PASS"])

    def test_the_table_covers_every_edit_the_record_lists(self) -> None:
        self.assertEqual((len(RECORD_M1), len(RECORD_L3)), (10, 58))
        covered = {record for records, *_ in CLOSING_EDITS for record in records}
        covered |= {record for record, _ in GATE_EDITS}
        self.assertEqual(sorted(set(RECORD_M1 + RECORD_L3) - covered), [])

    def test_check_command_refuses_a_checkout_written_receipt(self) -> None:
        # Record id G1-23: the command, not only the gate function, applies exact mode.
        files = self.committed()
        for name in ("12b-source.json", "12c-source.json"):
            writer_from_the_worktree(files[name])
            rederive(files[name])
        code, out, err = check(self.written(files))
        self.assertEqual((code, out), (1, "rollback proof lane: FAIL\n"))
        self.assertEqual(err.splitlines(),
                         [f"{scenario}: G1: {WRITER_ARCHIVE}" for scenario in ("12b", "12c")])

    def test_check_command_requires_the_default_recovery_tier(self) -> None:
        # Record id G9-30: the command asks for tier 27a when no tier is named.
        files = self.committed()
        files["12b-source.json"]["recovery"] = {}
        rederive(files["12b-source.json"])
        code, out, err = check(self.written(files))
        self.assertEqual((code, out), (1, "rollback proof lane: FAIL\n"))
        self.assertEqual(err.splitlines(),
                         ["12b: G9: recovery tier 27a is required and was not exercised"])
        self.assertEqual(check(self.written(self.committed())),
                         (0, "rollback proof lane: PASS\n", ""))


# --- recorded identities against git, and the recorded times ---------------------------------
DRIVER_PATHS = {
    "rollback_proof_lane.py": "scripts/rollback_proof_lane.py",
    "tip_writer.py": "tests/fixtures/hamcp377_rollback_proof/tip_writer.py",
    "previous_reader.py": "tests/fixtures/hamcp377_rollback_proof/previous_reader.py"}
TOOLS = ("candidate_tool", "test_signing_tool")
COMMIT_ID = re.compile(r"[0-9a-f]{40}\Z")


def git_out(*arguments: str) -> bytes | None:
    """The test's own git call (not the lane's): the output, or None when git refuses."""

    try:
        result = subprocess.run(["git", "-C", str(ROOT), *arguments], capture_output=True,
                                check=False)
    except OSError:
        return None
    return None if result.returncode else result.stdout


def is_commit(value: object) -> bool:
    return (isinstance(value, str) and COMMIT_ID.match(value) is not None
            and git_out("cat-file", "-e", f"{value}^{{commit}}") is not None)


def git_binding_failures(receipt: dict) -> list[str]:
    """What a receipt records about drivers and tools that the repository's objects contradict."""

    failures = []
    checkout = receipt["lane"].get("checkout_commit")
    if not is_commit(checkout):
        failures.append("lane.checkout_commit is not a commit of this repository")
    if set(receipt["drivers"]) != set(DRIVER_PATHS):
        failures.append("drivers does not name exactly the lane and its two drivers")
    for name, path in DRIVER_PATHS.items():
        content = git_out("show", f"{checkout}:{path}") if is_commit(checkout) else None
        if content is None or receipt["drivers"].get(name) != hashlib.sha256(content).hexdigest():
            failures.append(f"drivers.{name} is not the sha256 of {path} at the checkout commit")
    for key in TOOLS:
        tool = receipt.get(key)
        if tool is None:
            continue
        blob = (git_out("rev-parse", "--verify", "--quiet", f"{tool.get('commit')}:{tool['path']}")
                if is_commit(tool.get("commit")) else None)
        blob = blob.decode().strip() if blob else None
        content = git_out("cat-file", "blob", blob) if blob else None
        if blob is None or tool.get("blob_at_commit") != blob or tool.get("blob_run") != blob:
            failures.append(f"{key} blobs are not the blob of {tool['path']} at its commit")
        if content is None or tool.get("sha256") != hashlib.sha256(content).hexdigest():
            failures.append(f"{key}.sha256 is not the sha256 of {tool['path']} at its commit")
    return failures


def time_failures(receipt: dict) -> list[str]:
    try:
        started, ended = (datetime.fromisoformat(receipt[key].replace("Z", "+00:00"))
                          for key in ("started_at", "ended_at"))
    except (AttributeError, KeyError, ValueError):
        return ["started_at and ended_at are not both timestamps"]
    if started.tzinfo is None or ended.tzinfo is None:
        return ["started_at and ended_at do not both carry a UTC offset"]
    return [] if started <= ended else ["the receipt ends before it starts"]


class GitBoundReceiptTests(GuardCase):
    """The committed receipts against the repository's own objects.

    `check` does not consult git, so it cannot tell a recorded driver hash, tool blob or
    checkout commit from an invented one. These tests can, wherever the repository is at
    hand. They are skipped where git is unavailable. Each side's commit, version and lock
    hash are compared by `WriterCommitGitTests`, and `check` pins the writer commit.
    """

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if shutil.which("git") is None or git_out("rev-parse", "--git-dir") is None:
            raise unittest.SkipTest("git is unavailable; recorded identities are not compared")
        recorded = {receipt["lane"]["checkout_commit"] for receipt in cls.receipts.values()}
        recorded |= {receipt[key]["commit"] for receipt in cls.receipts.values()
                     for key in TOOLS if key in receipt}
        shallow = (git_out("rev-parse", "--is-shallow-repository") or b"").strip() == b"true"
        if shallow and not all(is_commit(commit) for commit in recorded):
            raise unittest.SkipTest("shallow clone without the commits the receipts record")

    def test_recorded_drivers_and_tools_are_the_objects_git_holds(self) -> None:
        for scenario, receipt in self.receipts.items():
            with self.subTest(scenario=scenario):
                self.assertEqual(git_binding_failures(receipt), [])
        self.assertEqual([key for key in TOOLS if key in self.receipts["12a"]], list(TOOLS))

    def test_invented_identities_do_not_match_git(self) -> None:
        def tools(**changes):
            def change(receipt: dict) -> None:
                for key in TOOLS:
                    receipt[key].update(changes)
            return change

        def writer_commit(receipt: dict) -> None:
            receipt["writer"]["commit"] = receipt["writer"]["archive_commit"] = "f" * 40
            tools(commit="f" * 40)(receipt)

        drivers = [f"drivers.{name} is not the sha256 of {path} at the checkout commit"
                   for name, path in DRIVER_PATHS.items()]
        blobs = [f"{key} blobs are not the blob of {self.receipts['12a'][key]['path']} at its "
                 "commit" for key in TOOLS]
        hashes = [f"{key}.sha256 is not the sha256 of {self.receipts['12a'][key]['path']} at "
                  "its commit" for key in TOOLS]
        for name, scenarios, change, expected in (
                ("checkout commit invented", ALL, put("lane", "checkout_commit", "f" * 40),
                 ["lane.checkout_commit is not a commit of this repository", *drivers]),
                ("driver hashes invented", ALL,
                 put("drivers", {name: "0" * 64 for name in DRIVER_PATHS}), drivers),
                ("the lane's own hash invented", ALL,
                 put("drivers", "rollback_proof_lane.py", "0" * 64), drivers[:1]),
                ("a driver left out", ALL,
                 lambda receipt: receipt["drivers"].pop("previous_reader.py"),
                 ["drivers does not name exactly the lane and its two drivers", drivers[2]]),
                ("tool blobs invented", ("12a",),
                 tools(blob_run="0" * 40, blob_at_commit="0" * 40), blobs),
                ("tool hashes invented", ("12a",), both(tools(sha256="0" * 64), *(
                    put(*O, "writer", "tools", name, "sha256", "0" * 64)
                    for name in ("candidate", "test_signing"))), hashes),
                ("writer commit invented with its tool commits", ("12a",), writer_commit,
                 [blobs[0], hashes[0], blobs[1], hashes[1]])):
            for scenario in scenarios:
                with self.subTest(edit=name, scenario=scenario):
                    edited = copy.deepcopy(self.receipts[scenario])
                    change(edited)
                    rederive(edited)
                    self.assertEqual(lane.classify(edited), [])  # `check` cannot see it
                    self.assertEqual(git_binding_failures(edited), expected)


class RecordedTimeTests(GuardCase):
    def test_recorded_receipts_end_no_earlier_than_they_start(self) -> None:
        for scenario, receipt in self.receipts.items():
            with self.subTest(scenario=scenario):
                self.assertEqual(time_failures(receipt), [])
                inverted = dict(receipt, started_at="2020-01-01T00:00:00Z",
                                ended_at="2019-01-01T00:00:00Z")
                self.assertEqual(time_failures(inverted), ["the receipt ends before it starts"])
                self.assertEqual(time_failures(dict(receipt, ended_at="yesterday")),
                                 ["started_at and ended_at are not both timestamps"])
                self.assertEqual(time_failures(dict(receipt, ended_at="2026-01-01T00:00:00")),
                                 ["started_at and ended_at do not both carry a UTC offset"])


# --- the writer commit: pinned in `check`, bound to git (M1 a, b, d) -------------------------
PINNED_WRITER = "aeb3b314dd180592874243c9ae370a63f0622c2b"
CONFIG = "hass_mcp_engineering_beta/config.yaml"
ANCESTRY = "writer.commit is not an ancestor of lane.checkout_commit"


def declared_at(commit: str) -> str | None:
    for line in (git_out("show", f"{commit}:{CONFIG}") or b"").decode("utf-8").splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return None


def tree_binding_failures(receipt: dict) -> list[str]:
    """What a receipt records about each side's tree that the side's own commit contradicts."""

    failures = []
    for side in ("writer", "reader"):
        record = receipt[side]
        commit = record.get("commit")
        if not is_commit(commit):
            failures.append(f"{side}.commit is not a commit of this repository")
            continue
        if record.get("archive_commit") != commit:
            failures.append(f"{side}.archive_commit is not {side}.commit")
        if not record.get("version") == record.get("declared_version") == declared_at(commit):
            failures.append(f"{side}.version is not the version {CONFIG} declares at "
                            f"{side}.commit")
        lock = git_out("show", f"{commit}:{lane.LOCK}")
        if lock is None or (record.get("dependencies") or {}).get(
                "lock_sha256") != hashlib.sha256(lock).hexdigest():
            failures.append(f"{side}.dependencies.lock_sha256 is not the sha256 of {lane.LOCK} "
                            f"at {side}.commit")
    return failures


def ancestry_failures(receipt: dict) -> list[str]:
    writer, checkout = receipt["writer"].get("commit"), receipt["lane"].get("checkout_commit")
    if not (is_commit(writer) and is_commit(checkout)) or git_out(
            "merge-base", "--is-ancestor", writer, checkout) is None:
        return [ANCESTRY]
    return []


def writer_commit(commit: str):
    """The record's B08: one writer commit named consistently, with its tools' commits."""

    def change(receipt: dict) -> None:
        receipt["writer"]["commit"] = receipt["writer"]["archive_commit"] = commit
        for key in TOOLS:
            if key in receipt:
                receipt[key]["commit"] = commit
    return change


def writer_version(version: str):
    def change(receipt: dict) -> None:
        receipt["writer"]["version"] = receipt["writer"]["declared_version"] = version
        for run in receipt["runs"]:
            if run["side"] == "writer":
                run["version"] = version
    return change


class WriterPinTests(GuardCase):
    """M1 (a): `check` accepts only the writer commit the committed receipts were written from."""

    def test_check_pins_the_writer_commit_of_the_committed_receipts(self) -> None:
        self.assertEqual(lane.WRITER_COMMIT, PINNED_WRITER)
        for scenario, receipt in self.receipts.items():
            with self.subTest(scenario=scenario):
                self.assertEqual((receipt["writer"]["commit"],
                                  receipt["writer"]["archive_commit"]),
                                 (PINNED_WRITER, PINNED_WRITER))
        self.assertEqual(check(self.written(self.committed())),
                         (0, "rollback proof lane: PASS\n", ""))

    def test_check_command_refuses_a_consistent_writer_commit_forgery(self) -> None:
        pin = f"G1: the writer is not the pinned writer commit {PINNED_WRITER}"
        for name, commit in (("invented", "f" * 40),
                             ("a real commit", self.receipts["12a"]["lane"]["checkout_commit"])):
            with self.subTest(commit=name):
                files = self.committed()
                for receipt in files.values():
                    writer_commit(commit)(receipt)
                    rederive(receipt)
                self.assertEqual(lane.gate(list(files.values())), [])  # the classifier passes it
                code, out, err = check(self.written(files))
                self.assertEqual((code, out), (1, FAIL))
                self.assertEqual(err.splitlines(), [f"{scenario}: {pin}" for scenario in ALL])


class WriterCommitGitTests(GuardCase):
    """M1 (b), (d): each side's commit, version and lock against git, where git is at hand."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if shutil.which("git") is None or git_out("rev-parse", "--git-dir") is None:
            raise unittest.SkipTest("git is unavailable; the writer commit is not compared")
        named = {receipt[side]["commit"] for receipt in cls.receipts.values()
                 for side in ("writer", "reader")}
        named |= {receipt["lane"]["checkout_commit"] for receipt in cls.receipts.values()}
        if not all(is_commit(commit) for commit in named):
            raise unittest.SkipTest("this clone does not hold the commits the receipts name")
        cls.shallow = (git_out("rev-parse", "--is-shallow-repository") or b"").strip() == b"true"

    def test_each_side_is_the_version_and_lock_its_commit_holds(self) -> None:  # (d)
        for scenario, receipt in self.receipts.items():
            with self.subTest(scenario=scenario):
                self.assertEqual(tree_binding_failures(receipt), [])
                self.assertEqual(declared_at(receipt["writer"]["commit"]), "2.4.2-beta.8")
                self.assertEqual(declared_at(receipt["reader"]["commit"]), lane.PREVIOUS_VERSION)

    def test_the_writer_commit_is_an_ancestor_of_the_checkout_commit(self) -> None:  # (b)
        if self.shallow:
            self.skipTest("skipped: shallow clone; the writer commit's ancestry is not compared")
        for scenario, receipt in self.receipts.items():
            with self.subTest(scenario=scenario):
                self.assertEqual(ancestry_failures(receipt), [])

    def test_forged_writer_facts_do_not_match_git(self) -> None:
        version = f"writer.version is not the version {CONFIG} declares at writer.commit"
        lock = f"dependencies.lock_sha256 is not the sha256 of {lane.LOCK} at "
        edits = [
            ("writer version forged", writer_version("2.4.2-beta.9"), [version], []),
            ("writer lock hash forged (B21)", put("writer", "dependencies", "lock_sha256",
                                                  "0" * 64), ["writer." + lock + "writer.commit"],
             []),
            ("reader lock hash forged (B21)", put("reader", "dependencies", "lock_sha256",
                                                  "0" * 64), ["reader." + lock + "reader.commit"],
             []),
            ("writer commit invented (B08)", writer_commit("f" * 40),
             ["writer.commit is not a commit of this repository"], [ANCESTRY])]
        head = (git_out("rev-parse", "HEAD") or b"").decode().strip()
        checkout = self.receipts["12a"]["lane"]["checkout_commit"]
        if head != checkout and git_out("merge-base", "--is-ancestor", head, checkout) is None:
            # A real commit after the checkout: it holds a tree, so only its ancestry is wrong.
            edits.append(("writer commit a later commit", writer_commit(head), None, [ANCESTRY]))
        for name, change, tree, ancestry in edits:
            for scenario in ALL:
                with self.subTest(edit=name, scenario=scenario):
                    edited = copy.deepcopy(self.receipts[scenario])
                    change(edited)
                    rederive(edited)
                    self.assertEqual(lane.classify(edited), [])  # the classifier passes it
                    if tree is not None:
                        self.assertEqual(tree_binding_failures(edited), tree)
                    if not self.shallow:
                        self.assertEqual(ancestry_failures(edited), ancestry)


# --- closed schema, recovery outcome, receipt directory, limitations -------------------------
FAIL = "rollback proof lane: FAIL\n"


class ClosedSchemaTests(GuardCase):
    def test_g13_a_receipt_carries_no_key_its_schema_does_not_define(self) -> None:
        text = "the receipt carries keys its schema does not define: "
        claim = "the previous release ran against a real Home Assistant Core"
        for scenario in ALL:
            with self.subTest(scenario=scenario):
                self.guard("G13", scenario, put("unchecked_claim", claim),
                           text=text + "unchecked_claim")
        # A key another scenario's receipt defines is still not one this scenario's does.
        self.guard("G13", "12b", put("source_facts", {}), text=text + "source_facts")
        self.guard("G13", "12c", put("candidate_tool", {}), text=text + "candidate_tool")
        self.guard("G13", "12a", both(put("zeta", 1), put("alpha", 1)), text=text + "alpha, zeta")
        self.guard("G13", "12a", put("lane", "sockets_opened", 0), text="the receipt's lane "
                   "record carries keys its schema does not define: sockets_opened")

    def test_check_command_refuses_a_receipt_with_an_unchecked_claim(self) -> None:
        files = self.committed()
        files["12c-source.json"]["unchecked_claim"] = "a real Home Assistant Core took part"
        code, out, err = check(self.written(files))
        self.assertEqual((code, out), (1, FAIL))
        self.assertEqual(err.splitlines(), ["12c: G13: the receipt carries keys its schema does "
                                            "not define: unchecked_claim"])

    def test_committed_receipts_hold_exactly_the_keys_the_schema_defines(self) -> None:
        for scenario, receipt in self.receipts.items():
            with self.subTest(scenario=scenario):
                self.assertEqual(set(receipt),
                                 lane.RECEIPT_KEYS | lane.SCENARIO_KEYS[scenario])
                self.assertEqual(set(receipt["lane"]), lane.LANE_KEYS)
        self.assertEqual(set(lane.SCENARIO_KEYS), set(lane.CLASSIFIERS))

    def test_g13_a_top_level_note_is_not_a_receipt_key(self) -> None:
        # NK-07: `note` is a key of the lane record only. At the top level it is a statement
        # nothing checks, so a receipt carrying one is not a PASS.
        self.assertIn("note", lane.LANE_KEYS)
        self.assertNotIn("note", lane.RECEIPT_KEYS)
        for scenario in ALL:
            with self.subTest(scenario=scenario):
                self.guard("G13", scenario, put("note", "a real Home Assistant Core took part"),
                           consistent=True,
                           text="the receipt carries keys its schema does not define: note")

    def test_g13_nested_records_carry_no_key_their_schema_does_not_define(self) -> None:
        # R1: the records inside a receipt are closed as well as its top level.
        text = "the receipt's {} record carries keys its schema does not define: unchecked_claim"
        for scenario in ALL:
            receipt = self.receipts[scenario]
            case = sorted(receipt["cases"])[0]
            paths = [("writer",), ("reader",), ("writer", "dependencies"),
                     ("reader", "dependencies"), ("runs", 0), ("cases", case),
                     ("cases", case, "derived"), ("cases", case, "observed"),
                     *(("data", name) for name in sorted(receipt["data"])),
                     *((key,) for key in TOOLS if key in receipt)]
            if scenario == "12a":
                paths += [*(("cases", case, "observed", part)
                            for part in ("writer", "lane", "reader", "real_read")),
                          ("real_candidate_comparison",)]
            if scenario == "12c":
                paths += [("source_facts",), ("reader_harness",)]
            if scenario == "12b":
                paths += [("data", "device_recovery_27a", "earlier", 0), ("recovery", "27a"),
                          ("recovery", "27a", "steps"),
                          *(("recovery", "27a", "steps", step)
                            for step in sorted(receipt["recovery"]["27a"]["steps"]))]
            for path in paths:
                where = ".".join(str(part) for part in path).replace(".0", "[0]")
                with self.subTest(scenario=scenario, record=where):
                    # Rederiving would rewrite a derived record and drop the key again.
                    self.guard("G13", scenario, put(*path, "unchecked_claim", True),
                               consistent=path[-1] != "derived", text=text.format(where))

            def another_case(edited: dict, case: str = case) -> None:
                edited["cases"]["unchecked_claim"] = copy.deepcopy(edited["cases"][case])
            with self.subTest(scenario=scenario, record="cases"):
                self.guard("G13", scenario, another_case, consistent=True,
                           text=text.format("cases"))

    def test_committed_nested_records_hold_only_the_keys_the_schema_defines(self) -> None:
        for scenario, receipt in self.receipts.items():
            with self.subTest(scenario=scenario):
                self.assertEqual([where for where, record, known in
                                  lane.closed_records(receipt, scenario)
                                  if not set(record) <= known], [])
                self.assertEqual(set(receipt["cases"]), set(lane.REQUIRED_CASES[scenario]))
                # A writer recorded from the worktree also records whether it was clean.
                self.assertEqual(lane.SIDE_KEYS["writer"] - set(receipt["writer"]),
                                 {"worktree_clean"})
                self.assertEqual(set(receipt["reader"]), lane.SIDE_KEYS["reader"])
        worktree = copy.deepcopy(self.receipts["12c"])
        writer_from_the_worktree(worktree)
        worktree["writer"].update(archive_commit=None, worktree_clean=True)
        self.assertEqual(lane.classify(worktree, require_exact=False), [])


def settled_as_succeeded(receipt: dict) -> None:
    """The record's bypass: the crashed task reads as verified success with no review."""

    settle = at(receipt, *SETTLE)
    settle["settled"]["normalized_outcome"] = "succeeded_verified"
    settle["release"].update(terminal_outcome="succeeded_verified", status="hold_released",
                             readback_matches_requested_state=True)
    del settle["review"]


class RecoveryOutcomeTests(GuardCase):
    def test_g9_27a_settles_to_manual_review_with_its_review_record(self) -> None:
        text = "27a does not show the crashed task settled to manual_review_required"
        for name, change in (
                ("settled as verified success", settled_as_succeeded),
                ("settled outcome", put(*SETTLE, "settled", "normalized_outcome",
                                        "succeeded_verified")),
                ("release outcome", put(*SETTLE, "release", "terminal_outcome",
                                        "succeeded_verified")),
                ("no review record", lambda receipt: at(receipt, *SETTLE).pop("review")),
                ("empty review record", put(*SETTLE, "review", {})),
                ("review without a reason", put(*SETTLE, "review", "manual_review_reason", None)),
                ("review without a hold", put(*SETTLE, "review", "hold_binding", None))):
            with self.subTest(edit=name):
                self.guard("G9", "12b", change, consistent=True, text=text)

    def test_g9_27a_crash_is_at_the_pinned_crash_point(self) -> None:
        text = "27a's crash is not at the pinned crash point"
        self.guard("G9", "12b", put(*STEPS, "crash", "died_in", "nothing crashed"),
                   consistent=True, text=text)
        self.guard("G9", "12b", put(*STEPS, "crash", "died_in", "before dispatch"), text=text)
        self.assertEqual(at(self.receipts["12b"], *STEPS, "crash", "died_in"),
                         lane.CRASH_POINT_27A)
        self.assertEqual(lane.SETTLED_OUTCOME_27A, "manual_review_required")

    def test_check_command_refuses_a_recovery_recorded_as_verified_success(self) -> None:
        files = self.committed()
        settled_as_succeeded(files["12b-source.json"])
        rederive(files["12b-source.json"])
        code, out, err = check(self.written(files))
        self.assertEqual((code, out), (1, FAIL))
        self.assertEqual(err.splitlines(), ["12b: G9: 27a does not show the crashed task settled "
                                            "to manual_review_required with its review record"])


class ReceiptDirectoryTests(GuardCase):
    def refused(self, files: dict) -> list[str]:
        code, out, err = check(self.written(files))
        self.assertEqual((code, out), (1, FAIL))
        return err.splitlines()

    def failing(self, scenario: str = "12a") -> dict:
        receipt = copy.deepcopy(self.receipts[scenario])
        receipt["result"], receipt["failures"] = "FAIL", ["G3: forged failure"]
        return receipt

    def test_a_shadow_copy_cannot_stand_in_for_a_failing_receipt(self) -> None:
        files = self.committed()
        files["12a-source.json"] = self.failing()
        files["12a-source.json"]["cases"]["registry"]["derived"]["admitted"] = 1
        files["12a-zz-shadow.json"] = copy.deepcopy(self.receipts["12a"])
        lines = self.refused(files)
        self.assertIn("G13: 12a-zz-shadow.json is not the file name of the receipt it holds",
                      lines)
        self.assertIn("G13: more than one source receipt for scenario 12a", lines)

    def test_two_receipts_for_one_scenario_fail_whichever_is_listed_last(self) -> None:
        good, bad = self.receipts["12a"], self.failing()
        rest = [self.receipts["12b"], self.receipts["12c"]]
        duplicate = "G13: more than one source receipt for scenario 12a"
        self.assertEqual(lane.gate([bad, good, *rest]), [duplicate])
        self.assertEqual(lane.gate([good, good, *rest]), [duplicate])
        self.assertEqual(lane.gate([good, bad, *rest]),
                         [duplicate, "G13: the 12a receipt does not record PASS"])

    def test_a_receipt_of_an_unknown_subject_is_not_ignored(self) -> None:
        files = self.committed()
        files["12a-image.json"] = dict(self.failing(), subject="image", failures=["anything"])
        self.assertEqual(self.refused(files), ["G13: a receipt names the unknown subject 'image'"])
        self.assertEqual(lane.SUBJECTS, ("source",))

    def test_a_receipt_of_an_unknown_scenario_is_not_ignored(self) -> None:
        files = self.committed()
        files["12z-source.json"] = dict(self.failing(), scenario="12z")
        self.assertEqual(self.refused(files),
                         ["G13: a receipt names the unknown scenario '12z'"])

    def test_a_file_must_be_named_for_the_receipt_it_holds(self) -> None:
        name = "G13: {} is not the file name of the receipt it holds"
        files = self.committed()
        files["12c-other.json"] = files.pop("12c-source.json")
        self.assertEqual(self.refused(files), [name.format("12c-other.json")])
        files = self.committed()
        files["12b-source.json"], files["12c-source.json"] = (files["12c-source.json"],
                                                              files["12b-source.json"])
        self.assertEqual(self.refused(files),
                         [name.format("12b-source.json"), name.format("12c-source.json")])
        # A name without a hyphen was never looked at; it is now.
        files = dict(self.committed(), **{"shadow.json": self.failing()})
        self.assertEqual(self.refused(files), [name.format("shadow.json"),
                                               "G13: more than one source receipt for scenario 12a",
                                               "G13: the 12a receipt does not record PASS"])

    def test_a_json_file_that_is_not_a_receipt_fails_the_check(self) -> None:
        self.assertEqual(self.refused(dict(self.committed(), **{"notes.json": "[]\n"})),
                         ["G13: notes.json is not a receipt"])
        self.assertEqual(self.refused(dict(self.committed(), **{"12d-source.json": "{"})),
                         ["G13: 12d-source.json cannot be read as a receipt (JSONDecodeError)"])

    def test_the_committed_directory_holds_one_file_per_scenario(self) -> None:
        self.assertEqual(sorted(path.name for path in RECEIPTS.glob("*.json")),
                         ["12a-source.json", "12b-source.json", "12c-source.json"])
        receipts, failures = lane.read_receipts(RECEIPTS)
        self.assertEqual(failures, [])
        self.assertEqual(receipts, lane.load_receipts(RECEIPTS))


class LimitationTests(GuardCase):
    def test_g13_each_scenario_states_exactly_its_pinned_limitations(self) -> None:
        text = "the receipt's limitations are not exactly the ones scenario {} must state"
        self.guard("G13", "12a", put("limitations", [lane.SYNTHETIC_PROVIDER_LIMITATION]),
                   consistent=True, text=text.format("12a"))
        for scenario in ALL:
            recorded = self.receipts[scenario]["limitations"]
            for index in range(1, len(recorded)):
                with self.subTest(scenario=scenario, removed=index):
                    self.guard("G13", scenario,
                               put("limitations", recorded[:index] + recorded[index + 1:]),
                               consistent=True, text=text.format(scenario))
            with self.subTest(scenario=scenario, edit="one added"):
                self.guard("G13", scenario, add("limitations", "An invented limitation."),
                           consistent=True, text=text.format(scenario))
            with self.subTest(scenario=scenario, edit="one repeated"):
                self.guard("G13", scenario, add("limitations", recorded[-1]), consistent=True,
                           text=text.format(scenario))
        self.guard("G13", "12a", put("limitations", list(reversed(
            self.receipts["12a"]["limitations"]))), text=text.format("12a"))
        # Text in place of the list: with the synthetic-provider sentence in it, it is still
        # not the pinned list; without it, the first check already names what is missing.
        self.guard("G13", "12c", put("limitations", lane.SYNTHETIC_PROVIDER_LIMITATION),
                   text=text.format("12c"))
        self.guard("G13", "12c", put("limitations", "not a list"),
                   text="the receipt does not state the synthetic-provider limitation")

    def test_committed_receipts_state_the_pinned_limitations(self) -> None:
        self.assertEqual({scenario: len(receipt["limitations"])
                          for scenario, receipt in self.receipts.items()},
                         {"12a": 4, "12b": 1, "12c": 2})
        for scenario, receipt in self.receipts.items():
            with self.subTest(scenario=scenario):
                self.assertEqual(receipt["limitations"], lane.LIMITATIONS[scenario])
                self.assertEqual(receipt["limitations"][0], lane.SYNTHETIC_PROVIDER_LIMITATION)


# --- the README beside the receipts ----------------------------------------------------------
README_ROW = re.compile(
    r"^\| `([^`|\n]+\.json)` \| [^|\n]+ \| ([A-Z]+) \| `([0-9a-f]{64})` \|$", re.M)
README_SUMMARY = re.compile(
    r"^Recorded `summary` of `([^`\n]+\.json)`:\n\n```text\n(.*?)\n```$", re.M | re.S)
HEX_TOKEN = re.compile(r"(?<![0-9a-f])(?:[0-9a-f]{40}|[0-9a-f]{64})(?![0-9a-f])")
PINNED_IDENTITIES = (lane.PREVIOUS_COMMIT, lane.PREVIOUS_TAG_OBJECT, lane.PUBLISHED_JOURNAL_BLOB,
                     lane.PUBLISHED_JOURNAL_SHA256)


def lf(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n")


def readme_failures(directory: Path) -> list[str]:
    """What a receipt directory's README states that its receipts do not bear out.

    Bound: the table (one row per receipt file, its result word and the sha256 of its LF
    bytes), one recorded-summary block per receipt (the receipt's `summary`, line for line),
    and every 40- or 64-digit identity anywhere in the text (a table hash, a constant the
    lane pins, or a value some receipt records). The remaining prose is not bound.
    """

    text = lf((directory / "README.md").read_bytes()).decode("utf-8")
    files = {path.name: lf(path.read_bytes()) for path in sorted(directory.glob("*.json"))}
    receipts = {name: json.loads(raw) for name, raw in files.items()}
    failures = []
    rows = README_ROW.findall(text)
    if sorted(name for name, _, _ in rows) != sorted(files):
        failures.append("the table does not list exactly the receipts in the directory")
    for name, result, digest in rows:
        if name in files and digest != hashlib.sha256(files[name]).hexdigest():
            failures.append(f"the table's sha256 for {name} is not that of the file")
        if name in files and result != receipts[name].get("result"):
            failures.append(f"the table's result for {name} is not the receipt's")
    blocks = README_SUMMARY.findall(text)
    if sorted(name for name, _ in blocks) != sorted(files):
        failures.append("there is not exactly one recorded-summary block per receipt")
    for name, block in blocks:
        if name in files and block.split("\n") != receipts[name].get("summary"):
            failures.append(f"the summary block for {name} is not the receipt's summary")
    known = {digest for _, _, digest in rows} | set(PINNED_IDENTITIES)
    recorded = "\n".join(raw.decode("utf-8") for raw in files.values())
    for token in sorted(set(HEX_TOKEN.findall(text)) - known):
        if token not in recorded:
            failures.append(f"{token} is in no receipt")
    return failures


class ReadmeTests(unittest.TestCase):
    """The README beside the committed receipts repeats them; it does not add to them."""

    HASH_12A = "b7b66591d2620e09063627c4dec6d2aa42ef94846b9f2f1526ce354d78646437"
    HASH_12C = "74b26509179b4edfe412e017f90503ecde3fc1aba56d006b68a569a46258d474"

    def edited(self, old: str, new: str, name: str = "README.md") -> list[str]:
        directory = Path(tempfile.mkdtemp(prefix="hamcp377-readme-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        for path in RECEIPTS.iterdir():
            (directory / path.name).write_bytes(lf(path.read_bytes()))
        text = (directory / name).read_text(encoding="utf-8")
        self.assertIn(old, text)
        (directory / name).write_text(text.replace(old, new), encoding="utf-8")
        return readme_failures(directory)

    def test_the_committed_readme_is_bound_to_the_committed_receipts(self) -> None:
        self.assertEqual(readme_failures(RECEIPTS), [])
        text = lf((RECEIPTS / "README.md").read_bytes()).decode("utf-8")
        names = ["12a-source.json", "12b-source.json", "12c-source.json"]
        self.assertEqual([name for name, _, _ in README_ROW.findall(text)], names)
        self.assertEqual([name for name, _ in README_SUMMARY.findall(text)], names)
        self.assertEqual(self.edited("# HAMCP-377", "# HAMCP-377"), [])

    def test_a_changed_hash_or_result_sentence_is_caught(self) -> None:
        # The record's bypass B13, one edit at a time and then both together.
        table = "the table's sha256 for 12a-source.json is not that of the file"
        summary = "the summary block for 12a-source.json is not the receipt's summary"
        self.assertEqual(self.edited(self.HASH_12A, "0" * 64), [table])
        self.assertEqual(self.edited("33 admitted including", "38 admitted including"), [summary])
        directory = Path(tempfile.mkdtemp(prefix="hamcp377-readme-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        shutil.copytree(RECEIPTS, directory, dirs_exist_ok=True)
        readme = directory / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace(
            self.HASH_12A, "0" * 64).replace("33 admitted including", "38 admitted including"),
            encoding="utf-8")
        self.assertEqual(readme_failures(directory), [table, summary])

    def test_every_bound_part_of_the_readme_is_compared(self) -> None:
        blocks = "there is not exactly one recorded-summary block per receipt"
        rows = "the table does not list exactly the receipts in the directory"
        reader = self.receipt("12c")["drivers"]["previous_reader.py"]
        checkout = self.receipt("12c")["lane"]["checkout_commit"]
        last = self.receipt("12c")["summary"][-1]
        for name, old, new, expected in (
                ("result word", f"| PASS | `{self.HASH_12A}`", f"| FAIL | `{self.HASH_12A}`",
                 ["the table's result for 12a-source.json is not the receipt's"]),
                # A row that no longer reads as one leaves its hash as an identity of nothing.
                ("row removed", "| `12c-source.json` |", "| 12c-source.json |",
                 [rows, self.HASH_12C + " is in no receipt"]),
                ("block unnamed", "Recorded `summary` of `12b-source.json`:",
                 "Recorded summary of 12b:", [blocks]),
                ("block repeated", "Recorded `summary` of `12b-source.json`:",
                 "Recorded `summary` of `12a-source.json`:",
                 [blocks, "the summary block for 12a-source.json is not the receipt's summary"]),
                ("sentence dropped", "\n" + last + "\n```", "\n```",
                 ["the summary block for 12c-source.json is not the receipt's summary"]),
                ("sentence added", "\n" + last + "\n```",
                 "\n" + last + "\nA real Home Assistant Core took part.\n```",
                 ["the summary block for 12c-source.json is not the receipt's summary"]),
                ("driver hash", reader, "1" * 64, ["1" * 64 + " is in no receipt"]),
                ("checkout commit", checkout, "f" * 40, ["f" * 40 + " is in no receipt"])):
            with self.subTest(edit=name):
                self.assertEqual(self.edited(old, new), expected)

    def test_a_changed_or_added_receipt_is_caught(self) -> None:
        self.assertEqual(
            self.edited('"result": "PASS"', '"result": "FAIL"', "12b-source.json"),
            ["the table's sha256 for 12b-source.json is not that of the file",
             "the table's result for 12b-source.json is not the receipt's"])
        directory = Path(tempfile.mkdtemp(prefix="hamcp377-readme-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        shutil.copytree(RECEIPTS, directory, dirs_exist_ok=True)
        shutil.copyfile(directory / "12a-source.json", directory / "12a-zz-shadow.json")
        self.assertEqual(readme_failures(directory), [
            "the table does not list exactly the receipts in the directory",
            "there is not exactly one recorded-summary block per receipt"])

    def receipt(self, scenario: str) -> dict:
        return json.loads((RECEIPTS / f"{scenario}-source.json").read_text(encoding="utf-8"))


# --- the lane's prerequisites against a git the test answers for -----------------------------
EXPIRES = datetime(2027, 2, 5, 6, 16, 34, tzinfo=timezone.utc)
IDENTITIES = ((lane.PREVIOUS_TAG, lane.PREVIOUS_TAG_OBJECT),
              (f"{lane.PREVIOUS_TAG}^{{commit}}", lane.PREVIOUS_COMMIT),
              (f"{lane.PUBLISHED_JOURNAL_BLOB}^{{blob}}", lane.PUBLISHED_JOURNAL_BLOB))


class PrerequisiteTests(unittest.TestCase):
    """`prerequisites` with a stand-in for git, so a wrong identity can be answered."""

    def missing(self, wrong: tuple = (), refused: dict | None = None,
                now: datetime | None = None) -> list[str]:
        asked = []

        def fake(*arguments: str, data: bytes | None = None) -> bytes:
            self.assertEqual(arguments[:3], ("rev-parse", "--verify", "--quiet"))
            self.assertEqual((len(arguments), data), (4, None))
            asked.append(arguments[3])
            if arguments[3] in (refused or {}):
                raise refused[arguments[3]]
            answer = "0" * 40 if arguments[3] in wrong else dict(IDENTITIES)[arguments[3]]
            return answer.encode() + b"\n"

        with mock.patch.object(lane, "git", fake):
            missing = lane.prerequisites(now or EXPIRES - timedelta(days=1))
        self.assertEqual(asked, [name for name, _ in IDENTITIES])
        return missing

    def test_the_three_pinned_identities_are_asked_for_and_accepted(self) -> None:
        self.assertEqual(self.missing(), [])
        self.assertEqual(EXPIRES.strftime("%Y-%m-%dT%H:%M:%SZ"), lane.PUBLISHED_EXPIRES_AT)

    def test_each_wrong_identity_is_a_missing_prerequisite(self) -> None:
        said = {name: f"{name} is {'0' * 40}, expected {expected}"
                for name, expected in IDENTITIES}
        for name, _ in IDENTITIES:
            with self.subTest(wrong=name):
                self.assertEqual(self.missing(wrong=(name,)), [said[name]])
        self.assertEqual(self.missing(wrong=tuple(said)), list(said.values()))

    def test_an_identity_git_cannot_resolve_is_a_missing_prerequisite(self) -> None:
        for name, _ in IDENTITIES:
            for error in (lane.LaneError("git rev-parse failed: synthetic refusal"),
                          FileNotFoundError("synthetic: no git")):
                with self.subTest(refused=name, error=type(error).__name__):
                    self.assertEqual(self.missing(refused={name: error}), [f"{name}: {error}"])

    def test_the_journal_is_expired_from_its_expiry_instant_on(self) -> None:
        text = f"the pinned published journal expired at {lane.PUBLISHED_EXPIRES_AT}"
        for moment in (EXPIRES - timedelta(days=1), EXPIRES - timedelta(seconds=1)):
            self.assertEqual(self.missing(now=moment), [])
        for moment in (EXPIRES, EXPIRES + timedelta(seconds=1), EXPIRES + timedelta(days=1)):
            with self.subTest(now=moment.isoformat()):
                missing = self.missing(now=moment)
                self.assertEqual(len(missing), 1, missing)
                self.assertTrue(missing[0].startswith(text), missing)
        both = self.missing(wrong=(IDENTITIES[1][0],), now=EXPIRES)
        self.assertEqual(len(both), 2, both)
        self.assertTrue(both[0].startswith(text) and both[1].startswith(IDENTITIES[1][0]), both)


# --- what a run measures, one measurement at a time ------------------------------------------
SYNTHETIC_DRIVER = r'''
import json, os, sys
tree, side, package, mode, code = sys.argv[1:6]
if mode != "silent":
    print("a line that is not the result")
if mode == "result":
    print(json.dumps({
        side + "_version": "0.0.0-synthetic", side + "_package": package, "tree": tree,
        "isolated": sys.flags.isolated, "no_bytecode": sys.flags.dont_write_bytecode,
        "environment": sorted(os.environ), "home": os.environ.get("HOME"),
        "cwd": os.path.realpath(os.getcwd())}))
sys.exit(int(code))
'''
SCRUBBED = {"HOME", "LANG", "PATH", "PYTHONDONTWRITEBYTECODE", "TMPDIR"}


def tree_digest(root: Path) -> tuple[int, str]:
    """The test's own measurement of a data set: it walks the tree and hashes it itself."""

    files = [Path(folder, name) for folder, _, names in os.walk(root) for name in names]
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(root).parts):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0"
                      + hashlib.sha256(path.read_bytes()).hexdigest().encode() + b"\n")
    return len(files), digest.hexdigest()


def answering(*answers: tuple) -> mock.Mock:
    """A stand-in for the lane's git: each call must match one expected argument prefix."""

    def fake(*arguments: str, data: bytes | None = None) -> bytes:
        for prefix, answer in answers:
            if arguments[:len(prefix)] == prefix:
                return answer(arguments, data) if callable(answer) else answer
        raise AssertionError(f"the lane asked git something unexpected: {arguments}")

    return mock.Mock(side_effect=fake)


class RunMeasurementCase(unittest.TestCase):
    def work(self) -> Path:
        work = Path(tempfile.mkdtemp(prefix="hamcp377-lane-unit-")).resolve()
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        return work

    def bare(self) -> lane.Lane:
        """A lane with the fields a run holds, without the run's set-up having taken place."""

        work = self.work()
        for name in ("data", "read", "raw", "tmp", "reader-source", "writer-source"):
            (work / name).mkdir()
        subject = lane.Lane.__new__(lane.Lane)
        subject.work, subject.real_candidate = work, None
        subject.python = {"reader": sys.executable, "writer": sys.executable}
        subject.tree = {"reader": work / "reader-source", "writer": work / "writer-source"}
        subject.reader = {"version": None, "flags": ["unset"]}
        subject.writer = {"version": None, "flags": ["unset"], "commit": "c" * 40,
                          "source": "git-archive"}
        subject.runs, subject.commands = [], []
        return subject


class DriverStepTests(RunMeasurementCase):
    """`Lane.drive` with a synthetic driver in place of the two real ones."""

    def setUp(self) -> None:
        self.subject = self.bare()
        self.driver = self.subject.work / "synthetic_driver.py"
        self.driver.write_text(SYNTHETIC_DRIVER, encoding="utf-8")
        for name in ("READER_DRIVER", "WRITER_DRIVER"):
            patch = mock.patch.object(lane, name, self.driver)
            patch.start()
            self.addCleanup(patch.stop)

    def step(self, side: str = "reader", package: str | None = None, mode: str = "result",
             code: int = 0) -> dict:
        inside = self.subject.tree[side] / "hass_mcp_engineering_beta/ha_mcp_engineering"
        return self.subject.drive(side, "unit", side, package or inside, mode, code)

    def test_a_step_records_what_its_driver_reported(self) -> None:
        for side in ("reader", "writer"):
            with self.subTest(side=side):
                output = self.step(side)
                record = getattr(self.subject, side)
                self.assertEqual(output["tree"], str(self.subject.tree[side]))
                self.assertEqual(record["version"], "0.0.0-synthetic")
                self.assertEqual(self.subject.runs[-1], {
                    "side": side, "step": "unit", "version": "0.0.0-synthetic",
                    "package_in_own_tree": True})
        self.assertEqual(len(self.subject.runs), 2)

    def test_a_package_outside_the_sides_own_tree_is_recorded_as_such(self) -> None:
        self.step("reader", package=str(self.subject.tree["writer"] / "ha_mcp_engineering"))
        self.assertIs(self.subject.runs[-1]["package_in_own_tree"], False)
        self.step("writer", package=str(self.subject.work / "elsewhere"))
        self.assertIs(self.subject.runs[-1]["package_in_own_tree"], False)
        self.step("writer")
        self.assertIs(self.subject.runs[-1]["package_in_own_tree"], True)

    def test_a_failing_step_is_no_evidence_whatever_it_printed(self) -> None:
        for mode, code, text in (("result", 3, "reader step unit exited 3"),
                                 ("result", 1, "reader step unit exited 1"),
                                 ("silent", 0, "reader step unit exited 0"),
                                 ("noise", 0, "reader step unit printed no JSON result")):
            with self.subTest(mode=mode, code=code):
                with self.assertRaises(lane.LaneError) as raised:
                    self.step(mode=mode, code=code)
                self.assertIn(text, str(raised.exception))
                self.assertEqual((self.subject.runs, self.subject.reader["version"]), ([], None))

    def test_each_step_runs_isolated_without_bytecode_and_says_so(self) -> None:
        output = self.step()
        self.assertEqual((output["isolated"], output["no_bytecode"]), (1, 1))
        command = self.subject.commands[-1]
        self.assertEqual(command[:4], ["<reader-python>", "-B", "-I",
                                       "<work>/synthetic_driver.py"])
        self.assertNotIn(str(self.subject.work), json.dumps(self.subject.commands))

    def test_the_recorded_flags_are_the_ones_on_the_command_that_ran(self) -> None:
        for side in ("reader", "writer"):
            with self.subTest(side=side):
                self.assertEqual(getattr(self.subject, side)["flags"], ["unset"])
                self.step(side)
                command = self.subject.commands[-1]
                flags = command[1:command.index("<work>/synthetic_driver.py")]
                self.assertEqual(flags, ["-B", "-I"])
                self.assertEqual(getattr(self.subject, side)["flags"], flags)

    def test_a_step_sees_a_scrubbed_environment_and_the_runs_own_directory(self) -> None:
        with mock.patch.dict(os.environ, {"HAMCP377_SYNTHETIC_SENTINEL": "unmistakably-synthetic",
                                          "PYTHONPATH": "/unmistakably/synthetic"}):
            output = self.step()
        self.assertEqual(set(output["environment"]) - {"LC_CTYPE"}, SCRUBBED)
        scratch = str(self.subject.work / "tmp")
        self.assertEqual((output["home"], output["cwd"]), (scratch, scratch))
        self.assertEqual(self.subject.environment()["TMPDIR"], scratch)

    def test_an_interpreter_whose_probe_fails_is_no_evidence(self) -> None:
        failing = "import sys; sys.stderr.write('synthetic probe failure'); sys.exit(4)"
        with mock.patch.object(lane, "DEPENDENCY_PROBE", failing):
            for side in ("reader", "writer"):
                with self.subTest(side=side), self.assertRaises(lane.LaneError) as raised:
                    self.subject.dependencies(side)
                self.assertEqual(str(raised.exception),
                                 f"{side} interpreter probe failed: synthetic probe failure")
        answering_probe = ("import json, sys; print(json.dumps("
                           "{'lock': sys.argv[1], 'isolated': sys.flags.isolated}))")
        with mock.patch.object(lane, "DEPENDENCY_PROBE", answering_probe):
            self.assertEqual(self.subject.dependencies("reader"), {
                "lock": str(self.subject.tree["reader"] / lane.LOCK), "isolated": 1})


class DataSetMeasurementTests(RunMeasurementCase):
    """The store digests are measurements of the files, taken when the receipt says."""

    def setUp(self) -> None:
        self.subject = self.bare()
        self.root = self.subject.work / "data/synthetic"
        (self.root / "nested/deeper").mkdir(parents=True)
        for name, text in (("a.json", "one"), ("nested/b.json", "two"),
                           ("nested/deeper/c.json", "three")):
            (self.root / name).write_text(text, encoding="utf-8")

    def test_the_manifest_covers_every_file_and_its_content(self) -> None:
        count, digest = tree_digest(self.root)
        self.assertEqual(count, 3)
        self.assertEqual(lane.manifest(self.root), {"files": 3, "digest": digest})
        self.assertEqual(self.subject.written("synthetic"), {"files": 3, "after_write": digest})
        seen = {digest}
        for name in ("a.json", "nested/b.json", "nested/deeper/c.json"):
            with self.subTest(changed=name):
                (self.root / name).write_text("changed", encoding="utf-8")
                after = lane.manifest(self.root)
                self.assertEqual((after["files"], after["digest"]), tree_digest(self.root))
                self.assertNotIn(after["digest"], seen)
                seen.add(after["digest"])
        (self.root / "nested/b.json").rename(self.root / "nested/renamed.json")
        self.assertNotIn(lane.manifest(self.root)["digest"], seen)
        self.assertEqual(lane.manifest(self.root)["digest"], tree_digest(self.root)[1])

    def test_a_read_is_hashed_immediately_before_and_immediately_after(self) -> None:
        record = self.subject.written("synthetic")
        written = record["after_write"]
        (self.root / "a.json").write_text("edited between the write and the read", "utf-8")
        before = tree_digest(self.root)[1]

        def drive(side: str, step: str, *arguments: str) -> dict:
            self.assertEqual((side, step, arguments), ("reader", "unit", ("argument",)))
            (self.root / "nested/b.json").write_text("changed by the reader", "utf-8")
            return {"synthetic": True}

        self.subject.drive = drive
        self.assertEqual(self.subject.read(record, "synthetic", "unit", "argument"),
                         {"synthetic": True})
        after = tree_digest(self.root)[1]
        self.assertEqual(len({written, before, after}), 3)
        self.assertEqual(record, {"files": 3, "after_write": written, "before_read": before,
                                  "after_read": after})

    def test_a_failed_read_still_records_the_digest_after_it(self) -> None:
        record = self.subject.written("synthetic")

        def drive(*arguments: str) -> dict:
            (self.root / "a.json").unlink()
            raise lane.LaneError("synthetic reader failure")

        self.subject.drive = drive
        with self.assertRaises(lane.LaneError):
            self.subject.read(record, "synthetic", "unit")
        self.assertEqual(record["before_read"], record["after_write"])
        self.assertEqual(record["after_read"], tree_digest(self.root)[1])
        self.assertNotEqual(record["after_read"], record["after_write"])


class ReceiptAssemblyTests(RunMeasurementCase):
    """`Lane.receipt` and `Lane.tool`: what they take from git and from the files."""

    COMMITTED = json.loads((RECEIPTS / "12c-source.json").read_text(encoding="utf-8"))
    STATUS = ("status", "--porcelain", "--")

    def assembled(self, status: bytes = b"", exact: bool = True, shared: bool = False) -> dict:
        committed = copy.deepcopy(self.COMMITTED)
        subject = self.bare()
        subject.reader, subject.writer = committed["reader"], committed["writer"]
        subject.runs, subject.commands = committed["runs"], committed["lane"]["commands"]
        if shared:
            subject.tree["writer"] = subject.tree["reader"] / "." / ""
        body = {key: committed[key] for key in ("data", "cases", "source_facts",
                                                "reader_harness")}
        body["limitations"] = committed["limitations"][1:]
        self.git = answering((("rev-parse", "HEAD"), b"d" * 40 + b"\n"), (self.STATUS, status))
        with mock.patch.object(lane, "git", self.git):
            return subject.receipt("12c", committed["started_at"], body, 0, 0, ("27a",), exact)

    def test_an_assembled_receipt_takes_its_identities_from_git_and_the_files(self) -> None:
        receipt = self.assembled()
        self.assertEqual((receipt["result"], receipt["failures"]), ("PASS", []))
        self.assertEqual(receipt["lane"]["checkout_commit"], "d" * 40)
        self.assertEqual(receipt["lane"]["drivers_modified"], [])
        self.assertEqual(receipt["drivers"], {
            name: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for name, path in DRIVER_PATHS.items()})
        asked = [call.args for call in self.git.call_args_list]
        self.assertEqual(sorted(asked)[0], ("rev-parse", "HEAD"))
        status, = (arguments for arguments in asked if arguments[:3] == self.STATUS)
        self.assertEqual(sorted(status[3:]), sorted(DRIVER_PATHS.values()))
        self.assertEqual(len(asked), 2)
        self.assertIs(receipt["trees_distinct"], True)
        self.assertEqual(receipt["limitations"], self.COMMITTED["limitations"])
        self.assertEqual(len(receipt["limitations"]), 2)
        for key in ("schema", "row", "scenario", "title", "subject", "summary", "runs",
                    "started_at", "writer", "reader"):
            self.assertEqual(receipt[key], self.COMMITTED[key], key)
        self.assertEqual(receipt["lane"]["commands"], self.COMMITTED["lane"]["commands"])
        self.assertEqual(time_failures(receipt), [])

    def test_a_modified_driver_is_recorded_and_fails_an_exact_run(self) -> None:
        dirty = b" M scripts/rollback_proof_lane.py\n?? tests/fixtures/synthetic\n"
        receipt = self.assembled(dirty)
        self.assertEqual(receipt["lane"]["drivers_modified"],
                         [" M scripts/rollback_proof_lane.py", "?? tests/fixtures/synthetic"])
        self.assertEqual((receipt["result"], receipt["failures"]), ("FAIL", [
            "G2: the drivers that ran are not the drivers of one exact commit"]))
        relaxed = self.assembled(dirty, exact=False)
        self.assertEqual((relaxed["result"], relaxed["failures"]), ("PASS", []))
        self.assertEqual(relaxed["lane"]["drivers_modified"], receipt["lane"]["drivers_modified"])

    def test_one_tree_for_both_sides_is_recorded_and_fails(self) -> None:
        receipt = self.assembled(shared=True)
        self.assertIs(receipt["trees_distinct"], False)
        self.assertEqual((receipt["result"], receipt["failures"]), ("FAIL", [
            "G2: the writer and the reader shared a source tree"]))

    def test_a_tool_record_is_measured_on_the_file_in_the_writers_tree(self) -> None:
        subject = self.bare()
        path = lane.CANDIDATE_TOOL
        file = subject.tree["writer"] / path
        file.parent.mkdir(parents=True)
        file.write_bytes(b"# a synthetic stand-in for the tool in the writer's tree\n")
        self.assertNotEqual(file.read_bytes(), (ROOT / path).read_bytes())
        git = answering(
            (("hash-object", "--no-filters", str(file)), b"a" * 40 + b"\n"),
            (("rev-parse", "--verify", f"{'c' * 40}:{path}"), b"b" * 40 + b"\n"))
        with mock.patch.object(lane, "git", git):
            record = subject.tool(path, "synthetic-operation")
        self.assertEqual(record, {
            "path": path, "operation": "synthetic-operation", "commit": "c" * 40,
            "source": "git-archive", "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            "blob_run": "a" * 40, "blob_at_commit": "b" * 40})
        self.assertEqual(git.call_count, 2)

    def test_the_sdk_result_is_the_one_the_reader_reported(self) -> None:
        switch = {"entity_id": "switch.synthetic", "ok": True, "value": {}, "provider_writes": 0}

        def reported(sdk: dict) -> dict:
            return {"task_id": "synthetic-task", "now": "2026-01-01T00:00:00+00:00",
                    "power": {"held_switch": switch, "other_switch": switch,
                              "provider": "synthetic", "locks_before": [], "locks_after": [],
                              "files": {}, "provider_writes": 0},
                    "governed": {"driver": "synthetic", "sdk": sdk, "passes": [], "readiness": [],
                                 "require_execution_ready": {"ok": True}, "snapshot": {},
                                 "locks_before": [], "locks_after": [], "files": {}}}

        for sdk, expected in (({"ok": False}, False), ({"ok": True}, True), ({}, True),
                              ({"ok": 0}, False)):
            with self.subTest(sdk=sdk):
                observed = lane.observe_device(reported(sdk))
                self.assertIs(observed["governed"]["sdk_ok"], expected)


class RunSetUpTests(RunMeasurementCase):
    """`Lane.__init__`, `extract` and `main run`: refusals first, then what is recorded."""

    JOURNAL = b'{"synthetic": "not the published journal"}\n'

    def patched(self, status: bytes = b"", prerequisites: list | None = None) -> dict:
        """Stand-ins for everything the set-up reaches outside the work directory."""

        stand_ins = {
            "prerequisites": mock.Mock(return_value=prerequisites or []),
            "extract": mock.Mock(side_effect=lambda commit, destination: "archive-of-" + commit),
            "declared_version": mock.Mock(return_value="0.0.0-synthetic"),
            "git": answering(
                (("rev-parse", "HEAD"), b"e" * 40 + b"\n"),
                (("rev-parse", "--verify", "HEAD^{commit}"), b"c" * 40 + b"\n"),
                (("status", "--porcelain"), status),
                (("cat-file", "blob", lane.PUBLISHED_JOURNAL_BLOB), self.JOURNAL))}
        for name, stand_in in stand_ins.items():
            patch = mock.patch.object(lane, name, stand_in)
            patch.start()
            self.addCleanup(patch.stop)
        probe = mock.patch.object(lane.Lane, "dependencies", lambda subject, side: {"side": side})
        probe.start()
        self.addCleanup(probe.stop)
        return stand_ins

    def journal_pinned(self) -> None:
        patch = mock.patch.object(lane, "PUBLISHED_JOURNAL_SHA256",
                                  hashlib.sha256(self.JOURNAL).hexdigest())
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_used_work_directory_is_refused_before_anything_is_asked(self) -> None:
        stand_ins = self.patched()
        self.journal_pinned()
        work = self.work()
        (work / "left-over").write_text("from an earlier run", encoding="utf-8")
        with self.assertRaises(lane.LaneError) as raised:
            lane.Lane(work, sys.executable, sys.executable, None)
        self.assertEqual(str(raised.exception), f"work directory {work} is not empty")
        for stand_in in stand_ins.values():
            stand_in.assert_not_called()
        self.assertEqual([path.name for path in work.iterdir()], ["left-over"])

    def test_a_missing_prerequisite_is_refused_before_anything_is_extracted(self) -> None:
        stand_ins = self.patched(prerequisites=["synthetic missing prerequisite", "another"])
        self.journal_pinned()
        work = self.work()
        with self.assertRaises(lane.LaneError) as raised:
            lane.Lane(work, sys.executable, sys.executable, None)
        self.assertEqual(str(raised.exception), "checkout cannot run the lane: synthetic missing "
                                                "prerequisite; another")
        stand_ins["extract"].assert_not_called()
        stand_ins["git"].assert_not_called()
        self.assertEqual(list(work.iterdir()), [])
        out = work.parent / (work.name + "-out")
        code, printed, err = run_main("run", "--out", str(out), "--work", str(work),
                                      "--reader-python", sys.executable)
        self.assertEqual((code, printed), (2, ""))
        self.assertEqual(err, "rollback proof lane: no evidence produced: checkout cannot run "
                              "the lane: synthetic missing prerequisite; another\n")
        self.assertFalse(out.exists())
        self.assertEqual(list(work.iterdir()), [])

    def test_a_journal_blob_without_the_pinned_sha256_is_refused(self) -> None:
        self.patched()
        with self.assertRaises(lane.LaneError) as raised:
            lane.Lane(self.work(), sys.executable, sys.executable, None)
        self.assertEqual(str(raised.exception),
                         "the published journal blob does not have the pinned sha256")
        self.journal_pinned()
        work = self.work()
        lane.Lane(work, sys.executable, sys.executable, None)
        self.assertEqual((work / "published-journal.json").read_bytes(), self.JOURNAL)

    def test_the_reader_tree_is_the_archive_of_the_previous_releases_commit(self) -> None:
        stand_ins = self.patched()
        self.journal_pinned()
        work = self.work()
        subject = lane.Lane(work, "reader-python", "writer-python", "HEAD")
        self.assertEqual(stand_ins["extract"].call_args_list, [
            mock.call(lane.PREVIOUS_COMMIT, work / "reader-source"),
            mock.call("c" * 40, work / "writer-source")])
        self.assertEqual(subject.reader, {
            "version": None, "tag": lane.PREVIOUS_TAG, "tag_object": lane.PREVIOUS_TAG_OBJECT,
            "commit": lane.PREVIOUS_COMMIT, "archive_commit": "archive-of-" + lane.PREVIOUS_COMMIT,
            "source": "git-archive", "isolated": True, "declared_version": "0.0.0-synthetic",
            "flags": ["-B", "-I"], "dependencies": {"side": "reader"}})
        self.assertEqual(subject.writer, {
            "commit": "c" * 40, "source": "git-archive", "archive_commit": "archive-of-" + "c" * 40,
            "version": None, "flags": ["-B", "-I"], "declared_version": "0.0.0-synthetic",
            "dependencies": {"side": "writer"}})
        self.assertEqual(subject.tree, {"reader": work / "reader-source",
                                        "writer": work / "writer-source"})
        self.assertEqual(subject.python, {"reader": "reader-python", "writer": "writer-python"})

    def test_a_checkout_writer_records_whether_the_worktree_was_clean(self) -> None:
        for status, clean in ((b"", True), (b" M scripts/rollback_proof_lane.py\n", False),
                              (b"?? untracked\n", False)):
            with self.subTest(status=status):
                stand_ins = self.patched(status)
                self.journal_pinned()
                subject = lane.Lane(self.work(), sys.executable, sys.executable, None)
                self.assertEqual(
                    {key: subject.writer[key] for key in ("commit", "source", "archive_commit",
                                                          "worktree_clean")},
                    {"commit": "e" * 40, "source": "worktree", "archive_commit": None,
                     "worktree_clean": clean})
                self.assertEqual(subject.tree["writer"], lane.ROOT)
                self.assertEqual(stand_ins["extract"].call_count, 1)

    def test_an_archive_is_identified_by_the_commit_id_inside_it(self) -> None:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            member = tarfile.TarInfo("nested/hello.txt")
            member.size = len(b"synthetic\n")
            tar.addfile(member, io.BytesIO(b"synthetic\n"))
        archive = buffer.getvalue()

        def commit_id(arguments: tuple, data: bytes | None) -> bytes:
            self.assertEqual(data, archive)
            return b"f" * 40 + b"\n"

        git = answering((("archive", "--format=tar", "c" * 40), archive),
                        (("get-tar-commit-id",), commit_id))
        destination = self.work() / "tree"
        with mock.patch.object(lane, "git", git):
            self.assertEqual(lane.extract("c" * 40, destination), "f" * 40)
            self.assertEqual((destination / "nested/hello.txt").read_bytes(), b"synthetic\n")
            with self.assertRaises(FileExistsError):
                lane.extract("c" * 40, destination)
        self.assertEqual(git.call_count, 4)

    def test_only_a_run_from_a_named_writer_commit_is_classified_as_exact(self) -> None:
        made = []

        class Recording:
            def __init__(self, *arguments: object) -> None:
                self.arguments, self.ran = arguments, None
                made.append(self)

            def run(self, scenarios: tuple, tiers: tuple, require_exact: bool) -> list:
                self.ran = (scenarios, tiers, require_exact)
                return []

        work = self.work()
        base = ("run", "--out", str(work / "out"), "--work", str(work / "work"),
                "--reader-python", "reader-python")
        with mock.patch.object(lane, "Lane", Recording):
            run_main(*base, "--writer-commit", "HEAD")
            run_main(*base)
            run_main(*base, "--scenario", "12b")
        everything = ("12a", "12b", "12c")
        self.assertEqual([item.ran for item in made], [
            (everything, ("27a",), True), (everything, ("27a",), False),
            (("12b",), ("27a",), False)])
        self.assertEqual(made[0].arguments, (work / "work", "reader-python", sys.executable,
                                             "HEAD", None))


if __name__ == "__main__":
    unittest.main()
