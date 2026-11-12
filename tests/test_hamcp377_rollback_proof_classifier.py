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
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
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
        cls.receipts = {receipt["scenario"]: receipt for receipt in lane.load_receipts(RECEIPTS)}

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
                         ["G13: no source receipt for scenario 12a"])
        with tempfile.TemporaryDirectory() as empty:
            code, out, err = check(Path(empty))
        self.assertEqual((code, out), (1, "rollback proof lane: FAIL\n"))
        self.assertEqual(err.splitlines(), [
            f"G13: no source receipt for scenario {scenario}" for scenario in lane.CLASSIFIERS])


if __name__ == "__main__":
    unittest.main()
