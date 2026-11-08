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


def failed(case: str, op: str, core_version: str) -> list[dict]:
    specs = ev.specs_for(case, core_version)
    if case == TYPED:
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
    if case == TYPED:
        specs[op]["readback"] = dict(specs[op]["readback"], state="unavailable")
    else:
        specs[op]["readback"] = copy.deepcopy(DASHBOARD_MISMATCH[op])
    return rebuild(case, specs, core_version)


def no_guide(core_version: str, **log) -> list[dict]:
    specs = ev.specs_for(DASH, core_version)
    specs["dashboard_bps_guide_read"]["readback"] = ev.bps_log(**({"guide": False} if not log else log))
    return rebuild(DASH, specs, core_version)


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

    def command(self, evidence: dict, cases: Path, *, architecture="amd64", raise_exc=None, argv_extra=()) -> int:
        row = self.row("91_only", architecture)
        argv = ["--peers", json.dumps(row["peers"]), "--architecture", architecture, "--cases-dir", str(cases),
                "--expected-engineering-version", "9.9.9-synthetic", "--work-dir", str(self.base / "work"),
                *argv_extra]
        with redirect_stdout(io.StringIO()):
            return governed.main(argv, runner=self.runner(evidence, raise_exc), environ={})

    def good(self, **changes) -> dict:
        return ev.evidence(core_version=self.core_version, **changes)

    def pipeline(self, evidence: dict, *, forge=None) -> dict:
        """command -> case files -> write-manifest (every lane) -> gate; returns everything observed."""

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
            if row["configuration"] == "91_only":
                codes[key] = self.command(evidence, cases, architecture=row["architecture"])
                case_files[key] = {c: json.loads((cases / f"{c}.json").read_text()) for c in governed.CASES}
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
        manifest = json.loads((manifests_dir / "91_only-amd64.json").read_text())
        return {"codes": codes, "cases": case_files["91_only-amd64"], "manifest": manifest,
                "gate_code": gate_code, "gate": json.loads(summary.read_text())}

    def assert_rejected(self, result: dict, case: str, fragment: str = ""):
        file = result["cases"][case]
        self.assertEqual(file["status"], "failed", file["reason"])
        self.assertIn(fragment, file["reason"])
        self.assertNotEqual(result["codes"]["91_only-amd64"], 0)
        entry = next(c for c in result["manifest"]["cases"] if c["name"] == case)
        self.assertEqual(entry["status"], "failed")
        self.assertEqual(result["manifest"]["result"], "FAIL")
        self.assertEqual(result["gate"]["acceptance"]["91_only"], "NOT ACCEPTED")
        self.assertNotEqual(result["gate_code"], 0)


class PositiveTests(Harness):
    def test_every_required_operation_verified_is_accepted_end_to_end(self):
        result = self.pipeline(self.good())
        self.assertEqual(result["codes"], {"91_only-amd64": 0, "91_only-arm64": 0})
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
            "provider_contract_exact": (False, "reviewed 9.1.0 provider"),
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
            governed.engineering_command(plan, self.base, self.base / "env")

    def test_ci_job_gate(self):
        cases = self.base / "cases"
        with redirect_stdout(io.StringIO()):
            code = governed.main(["--peers", json.dumps(self.row()["peers"]), "--architecture", "amd64",
                                  "--cases-dir", str(cases), "--expected-engineering-version", "1"],
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

import unittest.mock  # noqa: E402  (used by test_engineering_image_must_be_the_lane_image)

if __name__ == "__main__":
    unittest.main()
