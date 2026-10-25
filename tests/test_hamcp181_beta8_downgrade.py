"""HAMCP-181 design section 4.3 item 3: downgrade to the shipped beta.8 reader.

The committed records in ``tests/fixtures/f3_hamcp181_candidate/pr2`` were
written by the HAMCP-181 PR2 candidate writer (see that directory's
``provenance.json``). This module:

* verifies every committed record against its recorded SHA-256;
* extracts the exact beta.8 source (commit ``7370ad2``, tag
  ``v2.4.1-beta.8``) with ``git archive`` and checks the archive's commit id;
* runs ``shipped_reader.py`` under that source in a separate ``-I``
  interpreter over a copy of every store, and asserts what section 4.3 item 3
  requires of the old reader; and
* opens the same records with the current reader and checks the T2
  projections and that nothing is rewritten.

beta.8 does not meet section 4.3 item 3 for these records: it quarantines
every candidate-written change plan. The quarantine comes from beta.8's
own ``ChangePlan.from_dict``. Configuration plans fail on the
``called_scripts`` operation field. All other plans fail with an
``unsupported policy version`` error for ``f2-v3``. Main introduced both
fields with HAMCP-213 (commits ``c74fe735`` and ``7d536dab``), before PR2
branched. ``Beta8KnownContradictionTests`` pins this exact behaviour, and the
section 4.3 expectations it breaks are kept as explicit expected failures,
so the gap stays visible instead of being asserted away.

Owner decision (Josh, 2026-10-25): beta.8 is documented as a fan/power/task-only
floor, and the hidden governed plans are an accepted pre-existing limitation.
The supported governed-plan rollback target is 2.4.1-beta.36, tested in
``tests/test_hamcp181_beta36_downgrade.py``. The expected failures stay.

Set ``HAMCP181_BETA8_PYTHON`` to an interpreter with beta.8's exact
requirements (``mcp==1.28.1``) to run the reader without the SDK version shim.
Otherwise the driver replaces only beta.8's pinned-SDK import gate and
records that it did.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Any
import unittest


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "tests" / "fixtures" / "f3_hamcp181_candidate"
FIXTURES = CANDIDATE / "pr2"
DRIVER = CANDIDATE / "shipped_reader.py"
GENERATOR = CANDIDATE / "generate.py"

BETA8_COMMIT = "7370ad25eb98e03347d73e152f38881895477d74"
BETA8_TAG = "v2.4.1-beta.8"
BETA8_TAG_OBJECT = "4913bfd64948442a7a08733db4a93196175d60d1"
BETA8_MCP_PIN = "1.28.1"
DRIVER_TIMEOUT_SECONDS = 1200

# shape: (task state, terminal_outcome, dispatch_phase, plan status)
CANDIDATE_EXPECTED = {
    "a_helper_already_present": (
        "succeeded_verified", "desired_state_already_present_not_dispatched",
        "not_dispatched_already_present", "applied"),
    "b_configuration_callback_not_sent": (
        "failed_post_dispatch", "failed_after_intent_not_dispatched",
        "intent_recorded_not_sent", "failed"),
    "b_hvac_helper_appeared_not_sent": (
        "failed_post_dispatch", "failed_after_intent_not_dispatched",
        "intent_recorded_not_sent", "failed"),
    "b_dashboard_setter_not_sent": (
        "failed_post_dispatch", "failed_after_intent_not_dispatched",
        "intent_recorded_not_sent", "failed"),
    "r_proven_configuration_4xx": (
        "failed_post_dispatch", "failed_post_dispatch",
        "sent_rejected_no_mutation", "failed"),
    "r_unproven_operational_rejection": (
        "failed_post_dispatch", "failed_post_dispatch",
        "sent_failed_mutation_unproven", "failed"),
    "r_unproven_entity_registry_error": (
        "failed_post_dispatch", "failed_post_dispatch",
        "intent_recorded_send_uncertain", "failed"),
    "c_response_lost_verified": (
        "succeeded_verified", "succeeded_verified",
        "intent_recorded_send_uncertain", "applied"),
    "sent_verified_configuration": (
        "succeeded_verified", "succeeded_verified",
        "sent_response_received", "applied"),
    "v9_entity_metadata_already_present": (
        "succeeded_verified", "desired_state_already_present_not_dispatched",
        "not_dispatched_already_present", "applied"),
    "mixed_three_child_parent": (
        "manual_review_required", "manual_review_required",
        "sent_response_received", "verification_failed"),
}
NOT_DISPATCHED_PHASES = {"not_dispatched_already_present", "intent_recorded_not_sent"}
TYPED_EXPECTED = {
    "fan_sent_verified": ("succeeded_verified", "succeeded_verified"),
    "power_sent_verified": ("succeeded_verified", "succeeded_verified"),
}
OPERATIONAL_NAMESPACE = {"a_helper_already_present", "b_dashboard_setter_not_sent",
                         "r_unproven_operational_rejection"}
# beta.8 ChangePlan.from_dict failure for each candidate plan (see module docstring).
BETA8_PLAN_DECODE = {
    shape: ("TypeError", "unexpected keyword argument 'called_scripts'")
    for shape in ("b_configuration_callback_not_sent", "b_hvac_helper_appeared_not_sent",
                  "c_response_lost_verified", "mixed_three_child_parent",
                  "r_proven_configuration_4xx", "sent_verified_configuration")
}
BETA8_PLAN_DECODE.update({
    shape: ("ValueError", "unsupported policy version")
    for shape in ("a_helper_already_present", "b_dashboard_setter_not_sent",
                  "r_unproven_operational_rejection", "r_unproven_entity_registry_error",
                  "v9_entity_metadata_already_present")
})
# ``ChangeGovernanceService.health_summary`` reads the lifecycle gateway's
# in-memory ``health_snapshot`` (beta.8 governance/service.py
# ``_lifecycle_provider_health_snapshot``). The driver's own observation calls
# health_summary, so this read is excluded from the provider-call count. It
# sends nothing.
LOCAL_OBSERVATION_METHODS = {"health_snapshot"}
FORBIDDEN_FAULT_MARKERS = ("ExecutionRecordCorrupt", "execution_storage", "storage_read",
                           "execution_task_storage_error", "EXECUTION_TASK_STORAGE_ERROR")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verified_provenance() -> dict[str, Any]:
    """Every committed record matches provenance.json, and nothing is unlisted."""

    provenance = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))
    on_disk = {path.relative_to(FIXTURES).as_posix() for path in FIXTURES.rglob("*")
               if path.is_file() and "__pycache__" not in path.parts}
    listed = set(provenance["files"])
    if on_disk - {"provenance.json"} != listed:
        raise AssertionError(f"fixture files differ from provenance: "
                             f"unlisted={sorted(on_disk - listed - {'provenance.json'})} "
                             f"missing={sorted(listed - on_disk)}")
    for relative, digest in provenance["files"].items():
        actual = _sha256(FIXTURES / relative)
        if actual != digest:
            raise AssertionError(f"{relative}: sha256 {actual} != provenance {digest}")
    return provenance


def _git(*args: str, binary: bool = False) -> Any:
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                            check=False, timeout=300)
    if result.returncode != 0:
        return None
    return result.stdout if binary else result.stdout.decode().strip()


RUN: dict[str, Any] = {}


def setUpModule() -> None:
    if shutil.which("git") is None:
        raise unittest.SkipTest("git is unavailable")
    if _git("rev-parse", "--verify", "--quiet", f"{BETA8_COMMIT}^{{commit}}") != BETA8_COMMIT:
        raise unittest.SkipTest(f"beta.8 commit {BETA8_COMMIT} is not in this clone")
    RUN["tag_object_type"] = _git("cat-file", "-t", BETA8_TAG_OBJECT)
    RUN["tag_object"] = _git("cat-file", "-p", BETA8_TAG_OBJECT)
    RUN["provenance"] = verified_provenance()

    temp = Path(tempfile.mkdtemp(prefix="hamcp181-beta8-downgrade-"))
    RUN["temp"] = temp
    archive = _git("archive", "--format=tar", BETA8_COMMIT, binary=True)
    if archive is None:
        raise AssertionError("git archive of beta.8 failed")
    RUN["archive_commit"] = subprocess.run(
        ["git", "-C", str(ROOT), "get-tar-commit-id"], input=archive,
        capture_output=True, check=True, timeout=120).stdout.decode().strip()
    source = temp / "beta8-source"
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(source, filter="data")
    fixture_copy = temp / "fixtures"
    shutil.copytree(FIXTURES, fixture_copy)
    work = temp / "work"
    work.mkdir()
    interpreter = os.environ.get("HAMCP181_BETA8_PYTHON") or sys.executable
    RUN["interpreter"] = interpreter
    completed = subprocess.run(
        [interpreter, "-I", str(DRIVER), str(source), str(fixture_copy), str(work)],
        cwd=str(temp), capture_output=True, text=True, timeout=DRIVER_TIMEOUT_SECONDS,
        check=False)
    RUN["returncode"] = completed.returncode
    RUN["stderr"] = completed.stderr[-4000:]
    if completed.returncode != 0:
        raise AssertionError(f"beta.8 reader failed ({completed.returncode}):\n{completed.stderr[-4000:]}")
    RUN["result"] = json.loads(completed.stdout)
    RUN["fixture_copy_changed"] = sorted(
        relative for relative, digest in RUN["provenance"]["files"].items()
        if _sha256(fixture_copy / relative) != digest)


def tearDownModule() -> None:
    temp = RUN.get("temp")
    if temp is not None:
        shutil.rmtree(temp, ignore_errors=True)


def _calls(names: list[str]) -> list[str]:
    return [name for name in names if name.rsplit(".", 1)[-1] not in LOCAL_OBSERVATION_METHODS]


def _plan_file(shape: str, plan_id: str) -> str:
    prefix = "operational-administration-v3/" if shape in OPERATIONAL_NAMESPACE else ""
    return f"{prefix}{plan_id}.json"


def _errors(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "error" in value and isinstance(value["error"], dict):
            found.append(value["error"])
        for item in value.values():
            found.extend(_errors(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_errors(item))
    return found


class FixtureProvenanceTests(unittest.TestCase):
    def test_fixture_set_is_the_recorded_candidate_output(self):
        provenance = RUN["provenance"]
        self.assertEqual(set(provenance["shapes"]), set(CANDIDATE_EXPECTED) | set(TYPED_EXPECTED))
        self.assertRegex(provenance["candidate_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(provenance["candidate_runtime_status"], "")
        self.assertEqual(provenance["candidate_tracked_status"], "")
        self.assertEqual(provenance["generator_sha256"], _sha256(GENERATOR))
        self.assertTrue(provenance["python_version"].startswith("3.12."))
        self.assertEqual(RUN["fixture_copy_changed"], [])

    def test_writer_summary_agrees_with_the_expected_shapes(self):
        shapes = RUN["provenance"]["shapes"]
        for shape, (state, outcome, phase, plan_status) in CANDIDATE_EXPECTED.items():
            with self.subTest(shape=shape):
                self.assertEqual((shapes[shape]["task_state"], shapes[shape]["terminal_outcome"],
                                  shapes[shape]["dispatch_phase"], shapes[shape]["plan_status"]),
                                 (state, outcome, phase, plan_status))
                self.assertIs(shapes[shape]["provider_dispatch_occurred"],
                              phase not in NOT_DISPATCHED_PHASES)
        for shape, (state, outcome) in TYPED_EXPECTED.items():
            with self.subTest(shape=shape):
                self.assertEqual((shapes[shape]["state"], shapes[shape]["terminal_outcome"]),
                                 (state, outcome))


class Beta8ReaderIdentityTests(unittest.TestCase):
    def test_reader_is_the_exact_beta8_commit(self):
        self.assertEqual(RUN["archive_commit"], BETA8_COMMIT)
        result = RUN["result"]
        self.assertTrue(result["reader_is_source"], result["reader_package"])
        if RUN["tag_object_type"] is not None:
            self.assertEqual(RUN["tag_object_type"], "tag")
            self.assertIn(f"object {BETA8_COMMIT}", RUN["tag_object"])
            self.assertIn(f"tag {BETA8_TAG}", RUN["tag_object"])

    def test_sdk_version_gate_shim_is_disclosed(self):
        sdk = RUN["result"]["mcp_sdk"]
        self.assertEqual(sdk["reader_pin"], BETA8_MCP_PIN)
        self.assertIs(sdk["version_gate_shimmed"], sdk["installed"] != BETA8_MCP_PIN)


class Beta8DowngradeTests(unittest.TestCase):
    """The section 4.3 item 3 guarantees that beta.8 does meet."""

    def shapes(self, table):
        for shape in table:
            with self.subTest(shape=shape):
                yield shape, RUN["result"]["shapes"][shape]

    def test_startup_and_restart_succeed_with_f3_readiness_intact(self):
        for _shape, observed in self.shapes(CANDIDATE_EXPECTED):
            self.assertIsNone(observed.get("startup_error"))
            self.assertIsNone(observed.get("restart_error"))
            for phase in ("startup", "after_duplicate", "restart"):
                snapshot = observed[phase]
                self.assertEqual(snapshot["readiness"]["faults"], [], phase)
                self.assertTrue(snapshot["readiness"]["execution_ready"], phase)
                self.assertTrue(snapshot["runtime_execution_ready"], phase)
                self.assertEqual(snapshot["runtime_corrupt_record_count"], 0, phase)
                self.assertEqual(snapshot["runtime_nonterminal_execution_count"], 0, phase)
                self.assertEqual(snapshot["task_corruption_count"], 0, phase)
        for _shape, observed in self.shapes(TYPED_EXPECTED):
            self.assertIsNone(observed.get("startup_error"))
            self.assertIsNone(observed.get("restart_error"))
            for phase in ("startup", "after_duplicate", "restart"):
                health = observed[phase]["health"]
                self.assertEqual(health["recovery_failures"], 0, phase)
                self.assertEqual(health["nonterminal_tasks"], 0, phase)
                self.assertEqual(health["active_executions"], 0, phase)
                self.assertEqual(observed[phase]["quarantined"], [], phase)

    def test_no_execution_record_corruption_or_storage_fault(self):
        for _shape, observed in self.shapes({**CANDIDATE_EXPECTED, **TYPED_EXPECTED}):
            text = json.dumps([_errors(observed),
                               [observed[p].get("readiness") for p in ("startup", "restart")]])
            for marker in FORBIDDEN_FAULT_MARKERS:
                self.assertNotIn(marker, text)

    def test_execution_task_reads_return_each_record(self):
        for shape, observed in self.shapes(CANDIDATE_EXPECTED):
            state, outcome, _phase, _plan_status = CANDIDATE_EXPECTED[shape]
            expected = {"task_id": observed["task_id"], "plan_id": observed["plan_id"],
                        "state": state, "terminal_outcome": outcome}
            read = observed["reads"]["get_execution_task"]
            self.assertEqual({key: read.get(key) for key in expected}, expected)
            (listed,) = observed["reads"]["list_execution_tasks"]["listed"]
            self.assertEqual({key: listed.get(key) for key in expected}, expected)

    def test_task_and_child_records_are_not_rewritten(self):
        for shape, observed in self.shapes(CANDIDATE_EXPECTED):
            plan = _plan_file(shape, observed["plan_id"])
            for files in (observed["startup_files"], observed["final_files"]):
                self.assertEqual(files["modified"], [])
                self.assertEqual(files["removed"], [plan])
                self.assertTrue(all(path == "f3-child-execution-v1/.recovery-declaration-cursor.json"
                                    or "quarantine/" in path for path in files["added"]),
                                files["added"])

    def test_restart_and_reconciliation_make_zero_provider_calls(self):
        for _shape, observed in self.shapes(CANDIDATE_EXPECTED):
            self.assertEqual(_calls(observed["startup"]["provider_calls"]), [])
            self.assertEqual(_calls(observed["after_reads_calls"]), [])
            self.assertEqual(_calls(observed["after_duplicate"]["provider_calls"]), [])
            self.assertEqual(_calls(observed["restart"]["provider_calls"]), [])
            for phase in ("startup", "after_duplicate", "restart"):
                transport = observed[phase]["transport"]
                for key, value in transport.items():
                    self.assertFalse(value, f"{phase} {key}={value}")

    def test_typed_records_meet_section_4_3_item_3(self):
        for shape, observed in self.shapes(TYPED_EXPECTED):
            state, outcome = TYPED_EXPECTED[shape]
            expected = {"task_id": observed["task_id"], "state": state,
                        "terminal_outcome": outcome}
            self.assertEqual(observed["load"], {"ok": True, "value": True})
            self.assertEqual(observed["receipt"], expected)
            self.assertEqual(observed["get_execution_task"], {"success": True, "data": expected})
            # A duplicate control request returns the stored receipt and sends nothing.
            self.assertEqual(observed["duplicate_control"], {"ok": True, "result": expected})
            for phase in ("startup", "after_duplicate", "restart"):
                self.assertEqual(observed[phase]["transport_calls"], [], phase)
                self.assertEqual(observed[phase]["transport_writes"], 0, phase)
                self.assertEqual(observed[phase]["method_calls"], [], phase)
            for files in (observed["startup_files"], observed["final_files"]):
                self.assertEqual(files, {"added": [], "modified": [], "removed": []})


class Beta8KnownContradictionTests(unittest.TestCase):
    """Observed beta.8 behaviour that contradicts section 4.3 item 3.

    These assertions pin the exact behaviour so any change is noticed. They do
    not accept it: the matching section 4.3 expectations are the expected
    failures below.
    """

    def test_beta8_quarantines_every_candidate_change_plan(self):
        for shape in CANDIDATE_EXPECTED:
            with self.subTest(shape=shape):
                observed = RUN["result"]["shapes"][shape]
                plan = _plan_file(shape, observed["plan_id"])
                decoded = observed["plan_decode"]
                self.assertEqual(set(decoded), {plan})
                error_type, message = BETA8_PLAN_DECODE[shape]
                self.assertEqual(decoded[plan]["type"], error_type)
                self.assertIn(message, decoded[plan]["message"])
                (quarantined,) = observed["startup"]["quarantined"]
                namespace = "operational-administration-v3/" if shape in OPERATIONAL_NAMESPACE else ""
                self.assertTrue(quarantined.startswith(f"{namespace}quarantine/{observed['plan_id']}."))
                self.assertTrue(quarantined.endswith(".corrupt"))
                self.assertEqual(observed["startup"]["plan_corruption_count"], 1)
                self.assertEqual(observed["startup"]["storage_corruption_count"], 1)
                self.assertEqual(observed["reads"]["get_change_plan"]["error"]["code"],
                                 "change_plan_not_found")
                self.assertEqual(observed["reads"]["list_change_plans"], {"listed": []})
                # The duplicate apply still fails closed, without a provider call,
                # but as an unknown plan rather than a duplicate.
                duplicate = observed["duplicate_apply"]
                self.assertFalse(duplicate["ok"])
                self.assertEqual(duplicate["error"]["code"], "change_plan_not_found")

    @unittest.expectedFailure
    def test_section_4_3_nothing_quarantined_in_any_namespace(self):
        for shape in CANDIDATE_EXPECTED:
            observed = RUN["result"]["shapes"][shape]
            self.assertEqual(observed["startup"]["quarantined"], [], shape)
            self.assertEqual(observed["startup"]["plan_corruption_count"], 0, shape)

    @unittest.expectedFailure
    def test_section_4_3_change_plan_reads_return_each_record(self):
        for shape, (_state, _outcome, _phase, plan_status) in CANDIDATE_EXPECTED.items():
            observed = RUN["result"]["shapes"][shape]
            self.assertEqual(observed["reads"]["get_change_plan"],
                             {"plan_id": observed["plan_id"], "status": plan_status}, shape)
            self.assertEqual(len(observed["reads"]["list_change_plans"]["listed"]), 1, shape)

    @unittest.expectedFailure
    def test_section_4_3_duplicate_apply_refused_as_duplicate(self):
        for shape in CANDIDATE_EXPECTED:
            duplicate = RUN["result"]["shapes"][shape]["duplicate_apply"]
            self.assertNotEqual(duplicate.get("error", {}).get("code"), "change_plan_not_found",
                                shape)


class CandidateCurrentReaderTests(unittest.IsolatedAsyncioTestCase):
    """The current reader projects every candidate record and rewrites nothing."""

    async def open(self, shape: str):
        from datetime import datetime, timedelta

        if str(ROOT / "hass_mcp_engineering_beta") not in sys.path:
            sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
        import tests.test_dev14_configuration_plans as dev14
        import tests.test_f3_runtime_integration as f3_integration
        from ha_mcp_engineering.audit import AuditLogger
        from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration
        from ha_mcp_engineering.governance.service import ChangeGovernanceService
        from ha_mcp_engineering.governance.storage import ChangePlanRepository
        from ha_mcp_engineering.request_context import begin_request, end_request

        prefix = f"{shape}/store/"
        hashes = {key[len(prefix):]: value
                  for key, value in RUN["provenance"]["files"].items() if key.startswith(prefix)}
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        store = root / "plans"
        shutil.copytree(FIXTURES / shape / "store", store)
        (task_path,) = (store / "execution-tasks-v1").glob("*.json")
        stored = json.loads(task_path.read_text(encoding="utf-8"))
        now = datetime.fromisoformat(stored["updated_at"]) + timedelta(minutes=1)
        gateway = dev14.FakeConfigurationGateway()
        service = ChangeGovernanceService(
            ChangePlanRepository(store), gateway,
            AuditLogger(str(root / "audit.jsonl"), "synthetic-hamcp181-candidate-reader"),
            now=lambda: now)
        _telemetry, context = begin_request("synthetic-hamcp181-candidate-reader")
        self.addCleanup(end_request, context)
        runtime = F3RuntimeIntegration(
            service=service, storage_root=str(store),
            configuration_gateway=f3_integration._ExactFakeConfigurationGateway(gateway),
            backup_gateway=None, lifecycle_gateway=None,
            provider_identity_reader=f3_integration._provider_identity, retention_days=90)
        service.f3_runtime = runtime
        await runtime.recover_once("startup")
        await service.reconcile_execution_tasks(trigger="startup")
        await service.reconcile_operational_plans(trigger="startup")

        def unchanged():
            self.assertEqual(gateway.calls, [])
            for relative, digest in hashes.items():
                self.assertEqual(_sha256(store / relative), digest, f"{shape}: {relative} was rewritten")
            self.assertEqual(sorted(str(p.relative_to(store)) for p in store.rglob("*")
                                    if p.is_file() and ("quarantine" in p.parts
                                                        or p.name.endswith(".corrupt"))), [])

        return service, runtime, stored, unchanged

    async def test_every_candidate_shape_projects_its_t2_outcome(self):
        for shape, (state, outcome, phase, plan_status) in CANDIDATE_EXPECTED.items():
            with self.subTest(shape=shape):
                service, runtime, stored, unchanged = await self.open(shape)
                self.assertEqual(runtime.readiness_state()["faults"], [])
                self.assertEqual(service.repository.health()["corruption_count"], 0)
                self.assertEqual(service.task_repository.health()["corruption_count"], 0)
                self.assertEqual((stored["state"], stored["terminal_outcome"]), (state, outcome))
                view = service.get_execution_task(stored["task_id"])
                listed = [item for item in service.list_execution_tasks()["tasks"]
                          if item["task_id"] == stored["task_id"]]
                self.assertEqual(len(listed), 1)
                for surface in (view, *listed):
                    self.assertEqual(surface["state"], state)
                    self.assertEqual(surface["terminal_outcome"], outcome)
                    self.assertEqual(surface["dispatch_phase"], phase)
                    self.assertIs(surface["provider_dispatch_occurred"],
                                  phase not in NOT_DISPATCHED_PHASES)
                plan = service.get_plan(stored["plan_id"])
                self.assertEqual(plan["status"], plan_status)
                self.assertEqual(plan["execution_task"]["dispatch_phase"], phase)
                self.assertEqual(plan["execution_task"]["terminal_outcome"], outcome)
                listed_plans = [item for item in service.list_plans(limit=100)["plans"]
                                if item["plan_id"] == stored["plan_id"]]
                self.assertEqual(len(listed_plans), 1)
                unchanged()

    async def test_typed_records_load_under_the_current_reader(self):
        from ha_mcp_engineering.fan.authority import FanCoreAuthority
        from ha_mcp_engineering.fan.service import FanService
        from ha_mcp_engineering.power.service import PowerCoreAuthority, PowerService
        from ha_mcp_engineering.providers.upstream_fan import FanProvider
        from ha_mcp_engineering.providers.upstream_power import PowerProvider
        import tests.test_typed_fan as typed_fan
        import tests.test_typed_power as typed_power

        builders = {
            "fan_sent_verified": lambda root, transport: FanService(
                root, FanProvider(transport, lambda: transport.authority),
                FanCoreAuthority(typed_fan.Core()), now=typed_fan.Clock()),
            "power_sent_verified": lambda root, transport: PowerService(
                root, PowerProvider(transport, lambda *args: transport.authority),
                PowerCoreAuthority(typed_power.Core()), now=typed_power.Clock()),
        }
        transports = {"fan_sent_verified": typed_fan.Transport,
                      "power_sent_verified": lambda: typed_power.PowerTransport(
                          typed_power.PowerTests.version)}
        for shape, (state, outcome) in TYPED_EXPECTED.items():
            with self.subTest(shape=shape):
                prefix = f"{shape}/store/"
                hashes = {key[len(prefix):]: value for key, value
                          in RUN["provenance"]["files"].items() if key.startswith(prefix)}
                with tempfile.TemporaryDirectory() as temp:
                    store = Path(temp) / "store"
                    shutil.copytree(FIXTURES / shape / "store", store)
                    transport = transports[shape]()
                    service = builders[shape](str(store), transport)
                    try:
                        await service.recover_once()
                        task_id = RUN["provenance"]["shapes"][shape]["task_id"]
                        self.assertIsNotNone(service.load(task_id))
                        receipt = service.receipt(task_id)
                        self.assertEqual((receipt["state"], receipt["terminal_outcome"]),
                                         (state, outcome))
                        self.assertEqual(service.health()["recovery_failures"], 0)
                        self.assertEqual(transport.calls, [])
                        self.assertEqual(transport.writes, 0)
                    finally:
                        await service.close()
                    for relative, digest in hashes.items():
                        self.assertEqual(_sha256(store / relative), digest, relative)


if __name__ == "__main__":
    unittest.main()
