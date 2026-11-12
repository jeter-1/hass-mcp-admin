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
from pathlib import Path
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


class RegistryTests(GuardCase):
    @staticmethod
    def observed(receipt: dict) -> dict:
        return receipt["cases"]["registry"]["observed"]

    def test_g3_entry_must_extend_the_published_entry_in_the_ruled_order(self) -> None:
        def swap(receipt: dict) -> None:
            observed = self.observed(receipt)
            for target in (observed["writer"]["references"], observed["writer"]["added"],
                           observed["reader"]["entry_references"]):
                target[-1], target[-2] = target[-2], target[-1]
        self.guard("G3", "12a", swap, consistent=True)
        self.guard("G3", "12a", lambda receipt: self.observed(receipt)["writer"].update(
            {"built_by": "written by hand"}))
        self.guard("G3", "12a", lambda receipt: self.observed(receipt)["writer"].update(
            {"published_journal_sha256": "0" * 64}))
        self.guard("G3", "12a", lambda receipt: self.observed(receipt)["reader"]
                   ["entry_references"].pop())

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
        self.guard("G5", "12a", lambda receipt: receipt["cases"]["registry"]["derived"].update(
            {"quarantined": 5, "not_admitted": 0}))

    def test_g6_marketplace_read_is_bound_to_the_pinned_fingerprint(self) -> None:
        self.guard("G6", "12a", lambda receipt: self.observed(receipt)["reader"]["marketplace"]
                   .update({"entry_fingerprint": "sha256:" + "0" * 64}))
        self.guard("G6", "12a", lambda receipt: self.observed(receipt)["real_read"].update(
            {"exercised": True}))


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
