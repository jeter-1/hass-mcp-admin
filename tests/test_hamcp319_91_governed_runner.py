"""HAMCP-319 #407 Part C (F2): governed dashboard and public typed evidence (offline).

Exercises ``scripts/exact_image_admission_91_governed.py`` with injected
runners only: no Docker, no network, no live system. Every required operation
of ``91_governed_dashboard_verified_readback`` and
``91_typed_public_verified_readback`` is broken one at a time -- omitted,
failed, readback mismatch, and (for the dashboard) missing guide read -- and
each break is followed end to end: runner command -> case file -> lane
manifest -> gate, which must report the case failed, the 91_only lane FAIL and
91_only NOT ACCEPTED. Forged ``passed`` case files and forged manifests are
refused by the manifest writer and the gate respectively.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "tests", ROOT / "scripts", ROOT / "hass_mcp_engineering_beta"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from support import governed91_evidence as ev  # noqa: E402
from support.support_window_matrix import registry_matrix, support_window  # noqa: E402

import exact_image_admission_lanes as lanes  # noqa: E402

governed = ev.governed
COMMIT = "e" * 40
DASH, TYPED = governed.CASE_DASHBOARD, governed.CASE_TYPED

# One "the operation itself failed" break per required operation.
DASHBOARD_FAILED = {
    "dashboard_list": {"success": False},
    "dashboard_get": {"success": False},
    "dashboard_plan": {"success": False},
    "dashboard_apply_before_approval_refused": {"success": True, "error_code": None},
    "dashboard_approval_request": {"success": False},
    "dashboard_ingress_non_supervisor_refused": {"http_status": 200},
    "dashboard_ingress_approval": {"rounds": [{"get_status": 200, "post_status": 200, "decision": "denied"}]},
    "dashboard_apply": {"success": False},
    "dashboard_bps_guide_read": None,  # failure is in the peer log: no setter call happened
    "dashboard_verify": {"task_state": "verification_failed"},
    "dashboard_core_readback": None,  # failure: Core readback unavailable
}
# One readback that disagrees with what the operation claims (digest recomputed, so it binds).
DASHBOARD_MISMATCH = {
    "dashboard_list": ["energy"],
    "dashboard_get": copy.deepcopy(governed.PATCHED_CONFIG),
    "dashboard_plan": {"plan_status": "approved"},
    "dashboard_apply_before_approval_refused": {"plan_status": "awaiting_approval", "setter_calls": 1},
    "dashboard_approval_request": {"plan_status": "approved"},
    "dashboard_ingress_non_supervisor_refused": {"plan_status": "approved"},
    "dashboard_ingress_approval": {"plan_status": "awaiting_approval"},
    "dashboard_apply": {"setter_calls": 2},
    "dashboard_bps_guide_read": ev.bps_log(setters=2),
    "dashboard_verify": copy.deepcopy(governed.SEED_CONFIG),
    "dashboard_core_readback": copy.deepcopy(governed.SEED_CONFIG),
}


def rebuild(case: str, specs: dict, core_version: str) -> list[dict]:
    return ev.records(case, specs, core_version=core_version)


def omitted(case: str, op: str, core_version: str) -> list[dict]:
    return [r for r in ev.records(case, core_version=core_version) if r["id"] != op]


def is_typed(case: str) -> bool:
    return case not in governed.DASHBOARD_CASES


def failed(case: str, op: str, core_version: str) -> list[dict]:
    specs = ev.specs_for(case, core_version)
    if is_typed(case):
        specs[op]["facts"]["success"] = False
    elif op == "dashboard_bps_guide_read":
        specs[op]["readback"] = ev.bps_log(setters=0)
    elif op == "dashboard_core_readback":
        specs[op]["readback"] = None
    else:
        specs[op]["facts"].update(copy.deepcopy(DASHBOARD_FAILED[op]))
    return rebuild(case, specs, core_version)


def mismatched(case: str, op: str, core_version: str) -> list[dict]:
    specs = ev.specs_for(case, core_version)
    if is_typed(case):
        specs[op]["readback"] = dict(specs[op]["readback"], state="unavailable")
    else:
        specs[op]["readback"] = copy.deepcopy(DASHBOARD_MISMATCH[op])
    return rebuild(case, specs, core_version)


def no_guide(core_version: str, case: str = DASH, **log) -> list[dict]:
    specs = ev.specs_for(case, core_version)
    log = {"guide": False} if not log else log
    specs["dashboard_bps_guide_read"]["readback"] = ev.bps_log(version=ev.version_of(case), **log)
    return rebuild(case, specs, core_version)


def other_results(row: dict) -> dict[str, dict]:
    """Every non-governed case of an active row, passed with matching identity."""

    results = {}
    for case in lanes.CASES[row["configuration"]]:
        if case.name in governed.REQUIRED_OPERATIONS:
            continue
        observed = {"negotiated_protocol": lanes.EXPECTED_PROTOCOL}
        if case.name.startswith("image_identity_"):
            role = case.name.removeprefix("image_identity_")
            peer = row["peers"][role]
            observed = {"role": role, "version": peer["upstream_version"],
                        "image_digest": peer["image_index_digest"],
                        "manifest_digest": peer["architecture_image_digests"]["linux/" + row["architecture"]],
                        "config_digest": "sha256:" + "d" * 64}
        results[case.name] = lanes.case_result(case.name, "passed", form=case.endpoint_form, observed=observed)
    return results


class Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sw = support_window()
        cls.matrix = sw.admission_matrix(sw.load(ROOT), registry_matrix())
        cls.core_version = __import__("exact_image_admission_91_typed").core_image()["version"]

    def setUp(self):
        # Re-entrant: sub-tests call setUp/tearDown for a fresh directory per break.
        if getattr(self, "temp", None) is not None:
            self.temp.cleanup()
        self.temp = tempfile.TemporaryDirectory(prefix="governed91-test-")
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def row(self, configuration="91_only", architecture="amd64") -> dict:
        return next(r for r in self.matrix["include"]
                    if (r["configuration"], r["architecture"]) == (configuration, architecture))

    def runner(self, evidence: dict, raise_exc: BaseException | None = None):
        async def run(plan):
            for key, value in copy.deepcopy(evidence).items():
                if isinstance(value, dict) and key in plan.evidence:
                    plan.evidence[key].update(value)
                else:
                    plan.evidence[key] = value
            if raise_exc is not None:
                raise raise_exc
        return run

    def command(self, evidence: dict, cases: Path, *, configuration="91_only", architecture="amd64",
                raise_exc=None, argv_extra=(), rollback=True) -> int:
        row = self.row(configuration, architecture)
        argv = ["--peers", json.dumps(row["peers"]), "--architecture", architecture, "--cases-dir", str(cases),
                "--configuration", configuration, "--expected-engineering-version", ev.ENGINEERING_VERSION,
                "--work-dir", str(self.base / "work"), *argv_extra]
        if configuration == "side_by_side" and rollback:
            path = self.base / f"rollback-identity-{architecture}.json"
            path.write_text(json.dumps(ev.rollback_identity(architecture)))
            argv += ["--rollback-identity", str(path)]
        with redirect_stdout(io.StringIO()):
            return governed.main(argv, runner=self.runner(evidence, raise_exc), environ={})

    def good(self, configuration="91_only", **changes) -> dict:
        return ev.evidence(configuration, core_version=self.core_version, **changes)

    def evidence_for(self, evidence: dict, configuration: str, row: dict) -> dict:
        """The evidence under test for its configuration (architecture-bound); good evidence elsewhere."""

        if row["configuration"] != configuration:
            return self.good(row["configuration"], architecture=row["architecture"])
        evidence = copy.deepcopy(evidence)
        for section in evidence.get("stages", {}).values():
            ident = section.get("identity")
            # The synthetic identity names amd64; on the arm64 row the same deployment names arm64.
            if isinstance(ident, dict) and ident.get("architecture") == ev.ARCHITECTURE \
                    and row["architecture"] != ev.ARCHITECTURE:
                ident["architecture"] = row["architecture"]
                if isinstance(ident.get("rollback_identity"), dict) \
                        and ident["rollback_identity"].get("architecture") == ev.ARCHITECTURE:
                    ident["rollback_identity"] = ev.rollback_identity(row["architecture"])
        return evidence

    def pipeline(self, evidence: dict, *, configuration="91_only", forge=None) -> dict:
        """command -> case files -> write-manifest (every lane) -> gate; returns everything observed.

        ``evidence`` is the runner evidence of ``configuration``; the other
        governed configuration runs with fully passing evidence.
        """

        manifests_dir = self.base / "manifests"
        manifests_dir.mkdir(exist_ok=True)
        codes = {}
        case_files = {}
        for row in self.matrix["include"]:
            key = f"{row['configuration']}-{row['architecture']}"
            cases = self.base / "cases" / key
            cases.mkdir(parents=True)
            for name, value in other_results(row).items():
                lanes.write_json(cases / f"{name}.json", value)
            if row["configuration"] in governed.CONFIGURATIONS:
                code = self.command(self.evidence_for(evidence, configuration, row), cases,
                                    configuration=row["configuration"], architecture=row["architecture"])
                if row["configuration"] == configuration:
                    codes[key] = code
                    case_files[key] = {c: json.loads((cases / f"{c}.json").read_text())
                                       for c in governed.CASES_BY_CONFIGURATION[configuration]}
                    if forge is not None:
                        forge(cases)
            out = manifests_dir / f"{key}.json"
            with redirect_stdout(io.StringIO()):
                lanes.main(["write-manifest", "--row", json.dumps(row), "--engineering-commit", COMMIT,
                            "--cases-dir", str(cases), "--output", str(out)])
        summary = self.base / "gate.json"
        with redirect_stdout(io.StringIO()):
            gate_code = lanes.main(["gate", "--manifests-dir", str(manifests_dir), "--admission-matrix",
                                    json.dumps(self.matrix), "--engineering-commit", COMMIT,
                                    "--output", str(summary)])
        manifest = json.loads((manifests_dir / f"{configuration}-amd64.json").read_text())
        return {"codes": codes, "cases": case_files[f"{configuration}-amd64"], "manifest": manifest,
                "gate_code": gate_code, "gate": json.loads(summary.read_text()), "configuration": configuration}

    def assert_rejected(self, result: dict, case: str, fragment: str = ""):
        configuration = governed.CASE_STAGE[case].configuration
        self.assertEqual(result["configuration"], configuration)
        file = result["cases"][case]
        self.assertEqual(file["status"], "failed", file["reason"])
        self.assertIn(fragment, file["reason"])
        self.assertNotEqual(result["codes"][f"{configuration}-amd64"], 0)
        entry = next(c for c in result["manifest"]["cases"] if c["name"] == case)
        self.assertEqual(entry["status"], "failed")
        self.assertEqual(result["manifest"]["result"], "FAIL")
        self.assertEqual(result["gate"]["acceptance"][configuration], "NOT ACCEPTED")
        self.assertNotEqual(result["gate_code"], 0)


class PositiveTests(Harness):
    def test_every_required_operation_verified_is_accepted_end_to_end(self):
        result = self.pipeline(self.good())
        self.assertEqual(result["codes"], {"91_only-amd64": 0, "91_only-arm64": 0})
        self.assertEqual(result["gate"]["acceptance"]["side_by_side"], "ACCEPTED")
        for case in governed.CASES:
            self.assertEqual(result["cases"][case]["status"], "passed", result["cases"][case]["reason"])
            self.assertEqual(result["cases"][case]["endpoint_form"], "strict_full")
            self.assertEqual(result["cases"][case]["observed"]["negotiated_protocol"], ev.PROTOCOL)
            entry = next(c for c in result["manifest"]["cases"] if c["name"] == case)
            self.assertEqual([o["id"] for o in entry["operations"]], list(governed.REQUIRED_OPERATIONS[case]))
            self.assertTrue(all(o["verified"] for o in entry["operations"]))
        self.assertEqual(result["manifest"]["result"], "PASS")
        self.assertEqual(result["gate"]["errors"], [])
        self.assertEqual(set(result["gate"]["acceptance"].values()), {"ACCEPTED"})
        self.assertEqual(result["gate_code"], 0)

    def test_required_case_rows_are_listed_in_the_91_only_lane(self):
        names = lanes.case_names("91_only")
        for case in governed.CASES:
            self.assertIn(case, names)
            self.assertEqual(lanes.case_form("91_only", case), "strict_full")
        self.assertNotIn("91_parity_dashboard_gateway_acceptance", names)
        self.assertIn("91_parity_read_gateway_acceptance", names)

    def test_missing_governed_case_file_is_not_executed_and_not_accepted(self):
        def drop(cases):
            (cases / f"{TYPED}.json").unlink()
        result = self.pipeline(self.good(), forge=drop)
        entry = next(c for c in result["manifest"]["cases"] if c["name"] == TYPED)
        self.assertEqual(entry["status"], "not_executed")
        self.assertEqual(result["gate"]["acceptance"]["91_only"], "NOT ACCEPTED")
        self.assertTrue(any("listed but not executed" in e for e in result["gate"]["errors"]))

    def test_high_consequence_helper_confirmation_still_actionable_when_exact(self):
        # A safety-critical confirmation is disclosed and acknowledged; the exact operation
        # remains actionable and is verified on its own execution contract.
        specs = ev.specs_for(TYPED, self.core_version)
        specs["helper_input_number_set_value"]["facts"]["confirmation_reason"] = "safety_critical_helper"
        result = self.pipeline(self.good(typed=rebuild(TYPED, specs, self.core_version)))
        self.assertEqual(result["cases"][TYPED]["status"], "passed", result["cases"][TYPED]["reason"])


class RequiredOperationNegativeTests(Harness):
    """(a) omitted, (b) failed, (c) readback mismatch for EVERY required operation, end to end."""

    def run_break(self, case: str, operations: list[dict]) -> dict:
        changes = {"dashboard": operations} if case == DASH else {"typed": operations}
        return self.pipeline(self.good(**changes))

    def test_each_omitted_operation_fails_the_case_and_the_gate(self):
        for case in governed.CASES:
            for op in governed.REQUIRED_OPERATIONS[case]:
                with self.subTest(case=case, op=op):
                    self.setUp()
                    try:
                        result = self.run_break(case, omitted(case, op, self.core_version))
                        self.assert_rejected(result, case, f"operation {op} was not executed")
                    finally:
                        self.tearDown()

    def test_each_failed_operation_fails_the_case_and_the_gate(self):
        for case in governed.CASES:
            for op in governed.REQUIRED_OPERATIONS[case]:
                with self.subTest(case=case, op=op):
                    self.setUp()
                    try:
                        result = self.run_break(case, failed(case, op, self.core_version))
                        self.assert_rejected(result, case, f"operation {op}:")
                    finally:
                        self.tearDown()

    def test_each_readback_mismatch_fails_the_case_and_the_gate(self):
        for case in governed.CASES:
            for op in governed.REQUIRED_OPERATIONS[case]:
                with self.subTest(case=case, op=op):
                    self.setUp()
                    try:
                        result = self.run_break(case, mismatched(case, op, self.core_version))
                        self.assert_rejected(result, case, f"operation {op}:")
                    finally:
                        self.tearDown()

    def test_missing_guide_read_fails_the_dashboard_case_and_the_gate(self):
        variants = {
            "absent": ({}, "missing guide read"),
            "wrong file": ({"arguments": {"file": "references/automation-guide.md"}}, "guide read arguments"),
            "extra argument": ({"arguments": {"file": "references/dashboard-guide.md", "section": "x"}},
                               "guide read arguments"),
            "key not the receipt": ({"key": "f" * 64}, "BestPracticeKey"),
            "no key": ({"key": None}, "BestPracticeKey"),
        }
        for label, (log, fragment) in variants.items():
            with self.subTest(variant=label):
                self.setUp()
                try:
                    result = self.run_break(DASH, no_guide(self.core_version, **log))
                    self.assert_rejected(result, DASH, fragment)
                finally:
                    self.tearDown()

    def test_guide_read_after_the_setter_does_not_count(self):
        specs = ev.specs_for(DASH, self.core_version)
        log = ev.bps_log()
        specs["dashboard_bps_guide_read"]["readback"] = [log[0], log[2], log[1]]
        result = self.run_break(DASH, rebuild(DASH, specs, self.core_version))
        self.assert_rejected(result, DASH, "missing guide read")

    def test_duplicated_or_unknown_operation_fails(self):
        records = ev.records(TYPED, core_version=self.core_version)
        result = self.run_break(TYPED, records + [copy.deepcopy(records[0])])
        self.assert_rejected(result, TYPED, "recorded more than once")
        self.tearDown()
        self.setUp()
        extra = copy.deepcopy(records[0])
        extra["id"] = "power_light_toggle"
        result = self.run_break(TYPED, records + [extra])
        self.assert_rejected(result, TYPED, "unexpected operation")


class BindingNegativeTests(Harness):
    def test_readback_digest_must_bind_the_recorded_readback(self):
        records = ev.records(DASH, core_version=self.core_version)
        target = next(r for r in records if r["id"] == "dashboard_core_readback")
        target["readback"] = copy.deepcopy(governed.SEED_CONFIG)  # digest left as the patched config's
        result = self.pipeline(self.good(dashboard=records))
        self.assert_rejected(result, DASH, "readback digest does not bind")

    def test_request_and_response_digests_are_required(self):
        for key in ("request_sha256", "response_sha256", "readback_sha256"):
            with self.subTest(key=key):
                self.setUp()
                try:
                    records = ev.records(TYPED, core_version=self.core_version)
                    records[3][key] = "not-a-digest"
                    result = self.pipeline(self.good(typed=records))
                    self.assert_rejected(result, TYPED, key)
                finally:
                    self.tearDown()

    def test_typed_receipt_bound_to_another_core_fails(self):
        result = self.pipeline(self.good(typed=ev.records(TYPED, core_version="2000.1.0-other")))
        self.assert_rejected(result, TYPED, "disposable Core version")

    def test_typed_binding_must_name_the_lane_core_not_its_own_choice(self):
        # Records and binding agree with each other on another Core; the lane's Core decides.
        result = self.pipeline(ev.evidence(core_version="2000.1.0-other"))
        self.assert_rejected(result, TYPED, "disposable Core version")

    def test_typed_wrong_provider_contract_duplicate_or_extra_dispatch_fails(self):
        breaks = {
            "provider": ("upstream_read_gateway", "reviewed 9.1.0 provider"),
            "provider_contract": (governed.PROVIDER_CONTRACTS["8.5.0"]["control_power"], "reviewed 9.1.0 provider"),
            "provider_attempt_count": (2, "single recorded attempt"),
            "upstream_dispatches": (2, "exactly one ha_call_service"),
            "duplicate_dispatches": (1, "duplicate request"),
            "duplicate_same": (False, "duplicate request"),
            "arguments": ({"entity_id": "light.other", "action": "turn_on"}, "public tool or arguments"),
        }
        for field, (value, fragment) in breaks.items():
            with self.subTest(field=field):
                self.setUp()
                try:
                    specs = ev.specs_for(TYPED, self.core_version)
                    specs["power_light_turn_on"]["facts"][field] = value
                    result = self.pipeline(self.good(typed=rebuild(TYPED, specs, self.core_version)))
                    self.assert_rejected(result, TYPED, fragment)
                finally:
                    self.tearDown()

    def test_dashboard_plan_hash_must_bind_the_challenge(self):
        specs = ev.specs_for(DASH, self.core_version)
        specs["dashboard_approval_request"]["facts"]["bound_plan_hash"] = "d" * 64
        result = self.pipeline(self.good(dashboard=rebuild(DASH, specs, self.core_version)))
        self.assert_rejected(result, DASH, "exact plan hash")

    def test_approval_from_a_non_ingress_peer_fails(self):
        specs = ev.specs_for(DASH, self.core_version)
        specs["dashboard_ingress_approval"]["facts"]["peer"] = "127.0.0.1"
        result = self.pipeline(self.good(dashboard=rebuild(DASH, specs, self.core_version)))
        self.assert_rejected(result, DASH, "Supervisor Ingress peer")

    def test_peer_log_disagreement_or_wrong_protocol_fails_both_cases(self):
        for changes, fragment in (({"peer_log": {"forwarded": 40, "peer_posts": 39}}, "peer access log"),
                                  ({"peer_log": {"forwarded": 0, "peer_posts": 0}}, "peer access log"),
                                  ({"protocols": ["2025-06-18"]}, "2025-03-26"),
                                  ({"protocols": [ev.PROTOCOL, "2025-06-18"]}, "2025-03-26"),
                                  ({"protocols": []}, "2025-03-26")):
            with self.subTest(changes=changes):
                self.setUp()
                try:
                    result = self.pipeline(self.good(**changes))
                    for case in governed.CASES:
                        self.assert_rejected(result, case, fragment)
                finally:
                    self.tearDown()

    def test_lane_failure_after_full_evidence_still_fails(self):
        cases = self.base / "cases"
        code = self.command(self.good(), cases, raise_exc=governed.LaneFailure("peer_package_version_mismatch"))
        self.assertEqual(code, 1)
        for case in governed.CASES:
            value = json.loads((cases / f"{case}.json").read_text())
            self.assertEqual(value["status"], "failed")
            self.assertIn("peer_package_version_mismatch", value["reason"])

    def test_unexpected_exception_reports_only_its_category(self):
        cases = self.base / "cases"
        code = self.command(self.good(), cases, raise_exc=RuntimeError("synthetic-secret-token"))
        self.assertEqual(code, 1)
        for case in governed.CASES:
            text = (cases / f"{case}.json").read_text()
            self.assertNotIn("synthetic-secret-token", text)
            self.assertIn("lane error: RuntimeError", json.loads(text)["reason"])

    def test_never_writes_skipped_and_always_writes_both_cases(self):
        cases = self.base / "cases"
        self.command({}, cases)
        for case in governed.CASES:
            value = json.loads((cases / f"{case}.json").read_text())
            self.assertEqual(value["status"], "failed")
            self.assertEqual(value["endpoint_form"], "strict_full")

    def test_engineering_image_must_be_the_lane_image(self):
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), \
                unittest.mock.patch("sys.stderr", io.StringIO()):
            self.command(self.good(), self.base / "cases", argv_extra=("--engineering-image", "ghcr.io/x:latest"))
        with self.assertRaises(governed.LaneFailure):
            plan = governed.Plan("amd64", {}, {}, "ghcr.io/x:latest", "1", self.base, "governed91-1-amd64")
            governed.engineering_command(plan, governed.STAGES[0], self.base, self.base / "env")

    def test_ci_job_gate(self):
        cases = self.base / "cases"
        with redirect_stdout(io.StringIO()):
            code = governed.main(["--peers", json.dumps(self.row()["peers"]), "--architecture", "amd64",
                                  "--cases-dir", str(cases), "--configuration", "91_only",
                                  "--expected-engineering-version", "1"],
                                 runner=self.runner(self.good()),
                                 environ={"GITHUB_ACTIONS": "true", "GITHUB_JOB": "other"})
        self.assertEqual(code, 1)
        self.assertIn("not_exact_image_admission_lanes_job",
                      json.loads((cases / f"{DASH}.json").read_text())["reason"])


class ForgeryTests(Harness):
    """A ``passed`` case file or manifest that does not carry the evidence is refused."""

    def test_forged_passed_case_file_is_flipped_by_the_manifest_writer(self):
        forgeries = {
            "no operations": lambda o: o.update(operations=[]),
            "unverified record": lambda o: o["operations"][2]["facts"].update(success=False),
            "no peer log": lambda o: o.update(peer_log={}),
        }
        for label, forge_observed in forgeries.items():
            with self.subTest(forgery=label):
                self.setUp()
                try:
                    def forge(cases):
                        for case in governed.CASES:
                            path = cases / f"{case}.json"
                            value = json.loads(path.read_text())
                            forge_observed(value["observed"])
                            value.update(status="passed", reason="")
                            path.write_text(json.dumps(value))
                    result = self.pipeline(self.good(), forge=forge)
                    for case in governed.CASES:
                        entry = next(c for c in result["manifest"]["cases"] if c["name"] == case)
                        self.assertEqual(entry["status"], "failed")
                        self.assertTrue(entry["reason"].startswith("evidence: "), entry["reason"])
                    self.assertEqual(result["manifest"]["result"], "FAIL")
                    self.assertEqual(result["gate"]["acceptance"]["91_only"], "NOT ACCEPTED")
                finally:
                    self.tearDown()

    def test_forged_passed_case_with_failed_runner_verdict_is_flipped(self):
        def forge(cases):
            path = cases / f"{DASH}.json"
            value = json.loads(path.read_text())
            value.update(status="passed", reason="")
            path.write_text(json.dumps(value))
        breaks = omitted(DASH, "dashboard_bps_guide_read", self.core_version)
        result = self.pipeline(self.good(dashboard=breaks), forge=forge)
        self.assertEqual(result["cases"][DASH]["status"], "failed")
        entry = next(c for c in result["manifest"]["cases"] if c["name"] == DASH)
        self.assertEqual(entry["status"], "failed")
        self.assertIn("dashboard_bps_guide_read", entry["reason"])
        self.assertEqual(result["gate"]["acceptance"]["91_only"], "NOT ACCEPTED")

    def forged_gate(self, mutate) -> tuple[dict, list[str]]:
        result = self.pipeline(self.good())
        manifests = [json.loads(p.read_text()) for p in sorted((self.base / "manifests").glob("*.json"))]
        for manifest in manifests:
            if manifest["configuration"] == "91_only":
                for case in manifest["cases"]:
                    if case["name"] in governed.REQUIRED_OPERATIONS:
                        mutate(case)
        self.assertEqual(result["gate"]["acceptance"]["91_only"], "ACCEPTED")
        return lanes.verify_manifests(manifests, self.matrix, COMMIT)

    def test_forged_manifest_without_operation_evidence_is_not_accepted(self):
        forgeries = {
            "operations removed": lambda c: c.pop("operations"),
            "operation dropped": lambda c: c["operations"].pop(),
            "operation duplicated": lambda c: c["operations"].append(dict(c["operations"][0])),
            "verified false": lambda c: c["operations"][0].update(verified=False),
            "verified string": lambda c: c["operations"][0].update(verified="true"),
            "digest malformed": lambda c: c["operations"][0].update(readback_sha256="0" * 63),
            "extra field": lambda c: c["operations"][0].update(note="trust me"),
        }
        for label, mutate in forgeries.items():
            with self.subTest(forgery=label):
                self.setUp()
                try:
                    acceptance, errors = self.forged_gate(mutate)
                    self.assertEqual(acceptance["91_only"], "NOT ACCEPTED")
                    self.assertTrue(any("governed evidence" in e for e in errors), errors)
                finally:
                    self.tearDown()

    def test_manifest_verified_flag_is_recomputed_not_copied(self):
        records = ev.records(TYPED, core_version=self.core_version)
        records[0]["verified"] = True
        records[0]["facts"]["success"] = False
        compact = governed.compact_operations(TYPED, {"operations": records,
                                                      "binding": ev.binding_for(TYPED, self.core_version),
                                                      "core_version": self.core_version})
        self.assertFalse(compact[0]["verified"])


class ProxyLogTests(unittest.TestCase):
    def test_guide_receipt_and_setter_key_are_recorded_only_as_digests(self):
        receipt = ev.RECEIPT
        body = json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"content": [
            {"type": "text", "text": "guide ... " + receipt}]}}).encode()
        guide = governed.log_entry({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": governed.GUIDE_TOOL, "arguments": dict(governed.GUIDE_ARGUMENTS)}}, 200, "application/json", body)
        self.assertEqual(guide["receipt_sha256"], [hashlib.sha256(receipt.encode()).hexdigest()])
        self.assertEqual(guide["arguments"], governed.GUIDE_ARGUMENTS)
        self.assertNotIn(receipt, json.dumps(guide))
        setter = governed.log_entry({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
            "name": governed.SETTER_TOOL, "arguments": {"url_path": "hamcp-governed", "config": {},
                                                         "config_hash": "0" * 16, "MandatoryBPS": False,
                                                         "return_screenshot": False,
                                                         "BestPracticeKey": receipt}}}, 200, "application/json", b"{}")
        self.assertEqual(setter["best_practice_key_sha256"], guide["receipt_sha256"][0])
        self.assertNotIn(receipt, json.dumps(setter))
        self.assertIsNone(governed.bps_problem([guide, setter]))

    def test_initialize_protocol_is_read_from_sse(self):
        body = b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-03-26"}}\n\n'
        entry = governed.log_entry({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, 200,
                                   "text/event-stream", body)
        self.assertEqual(entry["protocol"], "2025-03-26")

    def test_recording_proxy_forwards_and_logs_over_loopback(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import urllib.request

        class Peer(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": [
                    {"type": "text", "text": ev.RECEIPT}]}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        peer = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        threading.Thread(target=peer.serve_forever, daemon=True).start()
        proxy = governed.RecordingProxy(0, peer.server_address[1])
        proxy.start()
        try:
            port = proxy.server.server_address[1]
            payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": governed.GUIDE_TOOL, "arguments": dict(governed.GUIDE_ARGUMENTS)}}).encode()
            request = urllib.request.Request(f"http://127.0.0.1:{port}/x", data=payload, method="POST",
                                             headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - loopback
                self.assertIn(ev.RECEIPT, response.read().decode())
            self.assertEqual(proxy.forwarded, 1)
            self.assertEqual(governed.calls(proxy.since(0), governed.GUIDE_TOOL), 1)
            self.assertEqual(proxy.since(0)[0]["receipt_sha256"], [ev.RECEIPT_SHA256])
        finally:
            proxy.stop()
            peer.shutdown()
            peer.server_close()



class WorkflowStepTests(unittest.TestCase):
    """The governed runner is a bounded 91_only step in the lanes job; nothing else changes."""

    @classmethod
    def setUpClass(cls):
        import yaml
        cls.job = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))[
            "jobs"]["exact-image-admission-lanes"]

    def step(self, prefix: str) -> dict:
        matches = [s for s in self.job["steps"] if str(s.get("name", "")).startswith(prefix)]
        self.assertEqual(len(matches), 1, prefix)
        return matches[0]

    def test_governed_step_runs_only_active_91_only_with_the_lane_image(self):
        step = self.step("Cases 9.1 governed dashboard and public typed controls")
        self.assertEqual(step["if"], "${{ !cancelled() && matrix.state == 'active' && "
                                     "matrix.configuration == '91_only' }}")
        self.assertEqual(step["timeout-minutes"], 20)
        self.assertNotIn("uses", step)
        self.assertNotIn("env", step)
        self.assertNotIn("continue-on-error", step)
        run = step["run"]
        self.assertIn("scripts/exact_image_admission_91_governed.py", run)
        self.assertIn("--engineering-image engineering-admission-lane:test", run)
        self.assertNotIn("|| true", run)
        self.assertNotIn("secrets.", run)

    def test_cleanup_removes_every_governed_container_network_and_work_dir(self):
        run = self.step("Remove disposable exact-image admission environment")["run"]
        self.assertIn("docker ps -aq --filter label=io.hass-mcp.governed91", run)
        self.assertIn("docker network ls -q --filter label=io.hass-mcp.governed91", run)
        self.assertIn('"$RUNNER_TEMP/governed91"', run)
        self.assertEqual(governed.LABEL, "io.hass-mcp.governed91")

    def test_misleading_parity_promotion_is_retired(self):
        text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("91_parity_dashboard_gateway_acceptance", text)
        self.assertNotIn("dashboard gateway acceptance", text)
        self.assertNotIn("91_parity_dashboard_gateway_acceptance", lanes.case_names("91_only"))

SBS = "side_by_side"
SBS_CASES = governed.CASES_BY_CONFIGURATION[SBS]
STAGE = {s.key: s for s in governed.STAGES}
ROLLBACK_CASES = STAGE["rollback"].cases


class SideBySideStageTests(Harness):
    """Stage A, the beta.6 rollback and stage A again, each on the 8.5.0 peer's legacy value."""

    def sbs(self, **changes) -> dict:
        return self.pipeline(self.good(SBS, **changes), configuration=SBS)

    def sbs_break(self, case: str, operations: list[dict]) -> dict:
        return self.sbs(operations={case: operations})

    def test_every_stage_verified_is_accepted_end_to_end(self):
        result = self.sbs()
        self.assertEqual(result["codes"], {"side_by_side-amd64": 0, "side_by_side-arm64": 0})
        for case in SBS_CASES:
            with self.subTest(case=case):
                file = result["cases"][case]
                self.assertEqual(file["status"], "passed", file["reason"])
                self.assertEqual(file["endpoint_form"], "legacy")
                stage = governed.CASE_STAGE[case]
                self.assertEqual(file["observed"]["stage"]["key"], stage.key)
                self.assertEqual(file["observed"]["upstream_version"], "8.5.0")
                self.assertEqual(file["observed"]["endpoint_path"], governed.SECRET_PATH)
                entry = next(c for c in result["manifest"]["cases"] if c["name"] == case)
                self.assertEqual([o["id"] for o in entry["operations"]], list(governed.REQUIRED_OPERATIONS[case]))
                self.assertTrue(all(o["verified"] for o in entry["operations"]))
        self.assertEqual(result["manifest"]["result"], "PASS")
        self.assertEqual(result["gate"]["errors"], [])
        self.assertEqual(set(result["gate"]["acceptance"].values()), {"ACCEPTED"})

    def test_stage_table_covers_stage_a_beta6_rollback_and_stage_a_again(self):
        stages = governed.STAGES_BY_CONFIGURATION[SBS]
        self.assertEqual([s.key for s in stages], ["stage_a", "rollback", "stage_a_again"])
        self.assertEqual([s.rollback for s in stages], [False, True, False])
        self.assertTrue(all(s.upstream_version == "8.5.0" and s.endpoint_form == "legacy" for s in stages))
        self.assertEqual(governed.ROLLBACK_VERSION, "2.4.2-beta.6")
        plan = governed.Plan("amd64", {}, {}, governed.LANE_IMAGE, "x", self.base, "governed91-1-amd64",
                             configuration=SBS)
        self.assertEqual(governed.stage_image(plan, STAGE["rollback"]), governed.runners.ROLLBACK_ENGINEERING_IMAGE)
        self.assertEqual(governed.stage_image(plan, STAGE["stage_a"]), governed.LANE_IMAGE)
        with self.assertRaises(governed.LaneFailure):
            # A stage of another configuration never runs in this lane.
            governed.stage_image(plan, STAGE["91_only"])
        names = lanes.case_names(SBS)
        for case in SBS_CASES:
            self.assertIn(case, names)
            self.assertEqual(lanes.case_form(SBS, case), "legacy")
            self.assertNotIn(case, lanes.case_names("91_only"))
            self.assertNotIn(case, lanes.case_names("85_only"))
        self.assertEqual(set(governed.REQUIRED_OPERATIONS), set(governed.ALL_CASES))

    def test_each_omitted_failed_or_mismatched_operation_fails_its_case_and_the_gate(self):
        for case in SBS_CASES:
            for op in governed.REQUIRED_OPERATIONS[case]:
                for label, broken, fragment in (
                        ("omitted", omitted(case, op, self.core_version), f"operation {op} was not executed"),
                        ("failed", failed(case, op, self.core_version), f"operation {op}:"),
                        ("mismatch", mismatched(case, op, self.core_version), f"operation {op}:")):
                    with self.subTest(case=case, op=op, kind=label):
                        self.setUp()
                        try:
                            result = self.sbs_break(case, broken)
                            self.assert_rejected(result, case, fragment)
                            for other in SBS_CASES:
                                if other != case:
                                    self.assertEqual(result["cases"][other]["status"], "passed")
                        finally:
                            self.tearDown()

    def test_missing_guide_read_fails_every_sbs_dashboard_case(self):
        for stage in governed.STAGES_BY_CONFIGURATION[SBS]:
            case = stage.dashboard_case
            for label, log, fragment in (("absent", {}, "missing guide read"),
                                         ("key not the receipt", {"key": "f" * 64}, "BestPracticeKey")):
                with self.subTest(case=case, variant=label):
                    self.setUp()
                    try:
                        result = self.sbs_break(case, no_guide(self.core_version, case, **log))
                        self.assert_rejected(result, case, fragment)
                    finally:
                        self.tearDown()

    def test_9_1_guide_read_or_contract_on_the_8_5_peer_fails(self):
        for stage in governed.STAGES_BY_CONFIGURATION[SBS]:
            with self.subTest(stage=stage.key, kind="guide"):
                self.setUp()
                try:
                    result = self.sbs_break(stage.dashboard_case,
                                            no_guide(self.core_version, stage.dashboard_case,
                                                     arguments=governed.GUIDE_ARGUMENTS_BY_VERSION["9.1.0"]))
                    self.assert_rejected(result, stage.dashboard_case, "guide read arguments")
                finally:
                    self.tearDown()
            with self.subTest(stage=stage.key, kind="contract"):
                self.setUp()
                try:
                    specs = ev.typed_specs(self.core_version, "9.1.0")
                    result = self.sbs_break(stage.typed_case, rebuild(stage.typed_case, specs, self.core_version))
                    self.assert_rejected(result, stage.typed_case, "reviewed 8.5.0 provider")
                finally:
                    self.tearDown()
        # And the reverse: an 8.5.0 guide read or contract does not satisfy the 9.1 stage.
        self.setUp()
        result = self.pipeline(self.good(dashboard=no_guide(self.core_version, DASH,
                                                             arguments=governed.GUIDE_ARGUMENTS_BY_VERSION["8.5.0"])))
        self.assert_rejected(result, DASH, "guide read arguments")
        self.tearDown()
        self.setUp()
        result = self.pipeline(self.good(typed=rebuild(TYPED, ev.typed_specs(self.core_version, "8.5.0"),
                                                       self.core_version)))
        self.assert_rejected(result, TYPED, "reviewed 9.1.0 provider")

    def identity_break(self, stage: str, mutate) -> dict:
        ident = ev.identity_for(STAGE[stage])
        ident = mutate(ident)
        return self.sbs(identities={stage: ident})

    def assert_only_stage_rejected(self, result: dict, stage: str, fragment: str):
        for case in STAGE[stage].cases:
            self.assert_rejected(result, case, fragment)
        for case in SBS_CASES:
            if case not in STAGE[stage].cases:
                self.assertEqual(result["cases"][case]["status"], "passed", result["cases"][case]["reason"])

    def test_rollback_stage_must_run_the_pinned_beta6_release_with_verified_identity(self):
        breaks = {
            "lane image instead": (lambda i: dict(i, engineering_image=governed.LANE_IMAGE),
                                   "did not run the pinned 2.4.2-beta.6"),
            "lane version instead": (lambda i: dict(i, engineering_version=ev.ENGINEERING_VERSION),
                                     "did not run the pinned 2.4.2-beta.6"),
            "identity missing": (lambda i: {k: v for k, v in i.items() if k != "rollback_identity"},
                                 "rollback identity: rollback_identity_not_verified"),
            "identity not passed": (lambda i: dict(i, rollback_identity=dict(i["rollback_identity"],
                                                                            status="failed")),
                                    "rollback identity: rollback_identity_not_verified"),
            "identity digest mismatch": (lambda i: dict(i, rollback_identity=dict(
                i["rollback_identity"], manifest_digest="sha256:" + "0" * 64)),
                "rollback identity: rollback_identity_mismatch"),
            "identity of another architecture": (lambda i: dict(i, rollback_identity=ev.rollback_identity("arm64")),
                                                 "verified for another architecture"),
        }
        for label, (mutate, fragment) in breaks.items():
            with self.subTest(case=label):
                self.setUp()
                try:
                    self.assert_only_stage_rejected(self.identity_break("rollback", mutate), "rollback", fragment)
                finally:
                    self.tearDown()

    def test_current_build_stages_must_run_the_lane_image_at_the_expected_version(self):
        for stage in ("stage_a", "stage_a_again"):
            breaks = {
                "beta6 image": (lambda i: dict(i, engineering_image=governed.ROLLBACK_IMAGE),
                                "did not run the lane-built Engineering image"),
                "beta6 version": (lambda i: dict(i, engineering_version=governed.ROLLBACK_VERSION),
                                  "not the lane build's expected version"),
                "other version": (lambda i: dict(i, engineering_version="2.4.2-beta.5"),
                                  "not the lane build's expected version"),
                "no version": (lambda i: {k: v for k, v in i.items() if k != "engineering_version"},
                               "not the lane build's expected version"),
            }
            for label, (mutate, fragment) in breaks.items():
                with self.subTest(stage=stage, case=label):
                    self.setUp()
                    try:
                        self.assert_only_stage_rejected(self.identity_break(stage, mutate), stage, fragment)
                    finally:
                        self.tearDown()

    def test_stage_identity_must_name_its_own_stage_peer_endpoint_and_architecture(self):
        breaks = {
            "missing": (lambda i: None, "stage identity was not recorded"),
            "other stage": (lambda i: dict(i, key="91_only" if i["key"] != "91_only" else "stage_a"),
                            "evidence belongs to another stage"),
            "9.1 peer": (lambda i: dict(i, upstream_version="9.1.0"), "stage peer is not ha-mcp 8.5.0"),
            "strict endpoint": (lambda i: dict(i, endpoint_path=governed.STRICT_PATH),
                                "stage endpoint is not the legacy value"),
            "other architecture": (lambda i: dict(i, architecture="arm64"), "stage architecture"),
        }
        for stage in ("stage_a", "rollback", "stage_a_again"):
            for label, (mutate, fragment) in breaks.items():
                with self.subTest(stage=stage, case=label):
                    self.setUp()
                    try:
                        self.assert_only_stage_rejected(self.identity_break(stage, mutate), stage, fragment)
                    finally:
                        self.tearDown()

    def test_91_only_identity_must_name_the_strict_9_1_stage(self):
        for label, mutate, fragment in (
                ("legacy endpoint", lambda i: dict(i, endpoint_path=governed.SECRET_PATH), "strict_full value"),
                ("8.5 peer", lambda i: dict(i, upstream_version="8.5.0"), "stage peer is not ha-mcp 9.1.0"),
                ("rollback image", lambda i: dict(i, engineering_image=governed.ROLLBACK_IMAGE), "lane-built")):
            with self.subTest(case=label):
                self.setUp()
                try:
                    ident = mutate(ev.identity_for(STAGE["91_only"]))
                    result = self.pipeline(self.good(identities={"91_only": ident}))
                    for case in governed.CASES:
                        self.assert_rejected(result, case, fragment)
                finally:
                    self.tearDown()

    def test_a_stage_failure_fails_only_that_stage(self):
        evidence = self.good(SBS)
        evidence["stages"]["rollback"]["failure"] = "engineering_version_mismatch"
        result = self.pipeline(evidence, configuration=SBS)
        self.assert_only_stage_rejected(result, "rollback", "stage refused: engineering_version_mismatch")

    def test_stage_peer_log_disagreement_fails_only_that_stage(self):
        evidence = self.good(SBS)
        evidence["stages"]["stage_a_again"]["peer_log"] = {"forwarded": 12, "peer_posts": 11}
        self.assert_only_stage_rejected(self.pipeline(evidence, configuration=SBS), "stage_a_again",
                                        "peer access log")

    def test_lane_failure_fails_every_sbs_case(self):
        cases = self.base / "cases"
        code = self.command(self.good(SBS), cases, configuration=SBS,
                            raise_exc=governed.LaneFailure("peer_role_unexpected"))
        self.assertEqual(code, 1)
        for case in SBS_CASES:
            value = json.loads((cases / f"{case}.json").read_text())
            self.assertEqual(value["status"], "failed")
            self.assertEqual(value["endpoint_form"], "legacy")
            self.assertIn("peer_role_unexpected", value["reason"])

    def test_sbs_command_writes_exactly_its_six_cases(self):
        cases = self.base / "cases"
        self.command({}, cases, configuration=SBS)
        self.assertEqual(sorted(p.stem for p in cases.glob("*.json")), sorted(SBS_CASES))
        for case in SBS_CASES:
            self.assertEqual(json.loads((cases / f"{case}.json").read_text())["status"], "failed")

    def test_lane_image_claiming_the_rollback_version_is_refused(self):
        cases = self.base / "cases"
        argv = ["--peers", json.dumps(self.row(SBS)["peers"]), "--architecture", "amd64", "--cases-dir", str(cases),
                "--configuration", SBS, "--expected-engineering-version", governed.ROLLBACK_VERSION]
        with redirect_stdout(io.StringIO()):
            code = governed.main(argv, runner=self.runner(self.good(SBS, engineering_version=governed.ROLLBACK_VERSION)),
                                 environ={})
        self.assertEqual(code, 1)
        for case in SBS_CASES:
            self.assertIn("engineering_version_is_the_rollback_release",
                          json.loads((cases / f"{case}.json").read_text())["reason"])

    def test_rollback_stage_refuses_to_start_without_a_verified_identity(self):
        lane = unittest.mock.MagicMock()
        for identity, fragment in ((None, "rollback_identity_not_verified"),
                                   (dict(ev.rollback_identity(), status="failed"), "rollback_identity_not_verified"),
                                   (ev.rollback_identity("arm64"), "rollback_identity_architecture_mismatch")):
            with self.subTest(identity=identity and identity.get("status")):
                plan = governed.Plan("amd64", {}, {}, governed.LANE_IMAGE, ev.ENGINEERING_VERSION, self.base,
                                     "governed91-1-amd64", governed.empty_evidence(SBS), SBS, identity)
                with unittest.mock.patch.object(governed, "docker", side_effect=AssertionError("docker reached")):
                    with self.assertRaises(governed.LaneFailure) as raised:
                        __import__("asyncio").run(governed.run_stage(plan, STAGE["rollback"], lane))
                self.assertIn(fragment, str(raised.exception))
                self.assertEqual(plan.evidence["stages"]["rollback"]["identity"], {})


class SideBySideSourceBindingTests(unittest.TestCase):
    def test_peer_selection_is_the_reviewed_row_for_each_configuration(self):
        sw = support_window()
        matrix = sw.admission_matrix(sw.load(ROOT), registry_matrix())
        typed = __import__("exact_image_admission_91_typed")
        for configuration, role, version in ((SBS, "baseline", "8.5.0"), ("91_only", "candidate", "9.1.0")):
            row = next(r for r in matrix["include"] if r["configuration"] == configuration)
            peer = governed.select_peer(row["peers"], configuration, "amd64", typed.reviewed_release)
            self.assertEqual((peer["role"], peer["version"]), (role, version))
            self.assertEqual(peer["image"], row["peers"][role]["upstream_image"])
            with self.assertRaises(governed.LaneFailure):
                bad = copy.deepcopy(row["peers"])
                bad[role]["image_index_digest"] = "sha256:" + "0" * 64
                governed.select_peer(bad, configuration, "amd64", typed.reviewed_release)
        with self.assertRaises(governed.LaneFailure):
            row = next(r for r in matrix["include"] if r["configuration"] == "91_only")
            governed.select_peer(row["peers"], SBS, "amd64", typed.reviewed_release)

    def test_version_bound_guide_reads_and_contracts_equal_the_runtime_source(self):
        from ha_mcp_engineering.clients import mcp as client
        from ha_mcp_engineering.fan.contracts import FAN_SEMANTIC_CONTRACTS
        from ha_mcp_engineering.helper_value.contracts import HELPER_SEMANTIC_CONTRACTS
        from ha_mcp_engineering.power.contracts import POWER_SEMANTIC_CONTRACTS
        self.assertEqual(governed.GUIDE_ARGUMENTS_BY_VERSION["8.5.0"], dict(client.BEST_PRACTICES_GUIDE_ARGUMENTS))
        self.assertNotIn(("8.5.0", governed.GUIDE_TOOL), client._BEST_PRACTICES_ARGUMENT_REWRITES)
        self.assertEqual(governed.GUIDE_ARGUMENTS_BY_VERSION["9.1.0"],
                         dict(client._BEST_PRACTICES_ARGUMENT_REWRITES[("9.1.0", governed.GUIDE_TOOL)]))
        for version in ("8.5.0", "9.1.0"):
            self.assertEqual(governed.PROVIDER_CONTRACTS[version], {
                "control_power": POWER_SEMANTIC_CONTRACTS[version], "control_fan": FAN_SEMANTIC_CONTRACTS[version],
                "control_helper": HELPER_SEMANTIC_CONTRACTS[version]})

    def test_peer_access_log_pattern_matches_only_the_stage_endpoint(self):
        legacy = governed.peer_post_pattern(governed.SECRET_PATH)
        strict = governed.peer_post_pattern(governed.STRICT_PATH)
        line = '"POST {} HTTP/1.1" 200'
        self.assertTrue(legacy.search(line.format(governed.SECRET_PATH)))
        self.assertTrue(legacy.search(line.format(governed.SECRET_PATH + "/")))
        self.assertFalse(legacy.search(line.format(governed.STRICT_PATH)))
        self.assertTrue(strict.search(line.format(governed.STRICT_PATH)))
        self.assertFalse(strict.search(line.format(governed.SECRET_PATH)))

    def test_side_by_side_workflow_step_runs_after_stage_a_again_with_the_rollback_identity(self):
        import yaml
        steps = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))[
            "jobs"]["exact-image-admission-lanes"]["steps"]
        names = [str(s.get("name", "")) for s in steps]
        index = names.index("Cases side-by-side governed dashboard and public typed controls "
                            "(stage A, beta.6 rollback, stage A again)")
        step = steps[index]
        self.assertGreater(index, names.index("Case side-by-side stage A again, current build with 8.5 on the "
                                              "legacy value"))
        self.assertGreater(index, names.index("Verify the previous Engineering release by digest"))
        self.assertLess(index, names.index("Write section 5.1 acceptance manifest"))
        self.assertEqual(step["if"], "${{ !cancelled() && matrix.state == 'active' && "
                                     "matrix.configuration == 'side_by_side' }}")
        self.assertEqual(step["timeout-minutes"], 20)
        for key in ("uses", "env", "continue-on-error"):
            self.assertNotIn(key, step)
        run = step["run"]
        for fragment in ("--configuration side_by_side", "--engineering-image engineering-admission-lane:test",
                         '--rollback-identity "$RUNNER_TEMP/admission-rollback-identity.json"',
                         '--expected-engineering-version "$ENGINEERING_LANE_VERSION"'):
            self.assertIn(fragment, run)
        self.assertNotIn("|| true", run)
        self.assertNotIn("secrets.", run)
        ninety_one = steps[names.index("Cases 9.1 governed dashboard and public typed controls through the built "
                                       "image")]
        self.assertIn("--configuration 91_only", ninety_one["run"])
        self.assertNotIn("--rollback-identity", ninety_one["run"])


import unittest.mock  # noqa: E402  (used by test_engineering_image_must_be_the_lane_image)

if __name__ == "__main__":
    unittest.main()
