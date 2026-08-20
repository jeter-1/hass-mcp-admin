"""Offline coverage for the versioned promotion regression manifest (HAMCP-089 #22).

These tests prove three things without any live access:

1. the committed manifest is structurally valid and every currently-failing
   sentinel names the open deficiency that explains it;
2. the checker classifies CONFIRMED, REGRESSION, KNOWN_FAILING,
   UNEXPECTED_PASS, and NOT_CAPTURED correctly, and never conflates a
   regression with an already-tracked failure; and
3. the checker cannot write anything, to the live target or to disk.

The capture fixture is a synthetic offline record built from the values the
register captured during two manual live regression passes. It is not a
production record and no endpoint was contacted to produce it.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import io
import json
import contextlib
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = ROOT / "scripts" / "promotion_regression_check.py"
MANIFEST_PATH = ROOT / "promotion" / "promotion_regression_manifest.yaml"
SCHEMA_PATH = ROOT / "promotion" / "manifest_schema.json"
README_PATH = ROOT / "promotion" / "README.md"
FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "promotion_regression" / "capture_beta39_current_state.json"
)

# Register topics that must remain encoded. Losing one of these silently is the
# exact failure this manifest exists to prevent.
REQUIRED_SENTINEL_IDS = frozenset(
    {
        "runtime-server-version",
        "runtime-build-provenance",
        "runtime-tool-accounting",
        "runtime-tool-accounting-agreement",
        "home-assistant-connectivity",
        "home-assistant-version-agreement",
        "upstream-ha-mcp-exact-admission",
        "upstream-read-gateway-zero-fallback",
        "upstream-dashboard-zero-fallback",
        "provider-routing-zero-fallback",
        "governance-plan-storage-healthy",
        "governance-task-storage-healthy",
        "f3-ready-locks-and-recovery",
        "governance-historical-projection-health",
        "helper-provider-attribution",
        "configuration-validation",
        "read-direct-home-assistant",
        "read-engineering-native",
        "read-delegated-upstream",
        "dashboard-hyphenless-map-read",
        "automation-long-wait-template-readable",
        "stale-state-canary-held-pre-dispatch",
        "helper-no-change-path",
        "dependency-index-complete-coverage",
        "held-read-contract-and-not-found-behavior",
    }
)

EXPECTED_FAIL_DEFICIENCIES = {
    "f3-ready-locks-and-recovery": 2,
    "governance-historical-projection-health": 4,
    "dependency-index-complete-coverage": 1,
}

# Nothing in this checker may reach a network, a shell, or a dynamic evaluator.
ALLOWED_IMPORTS = frozenset(
    {
        "argparse",
        "dataclasses",
        "json",
        "jsonschema",
        "pathlib",
        "re",
        "sys",
        "typing",
        "yaml",
        "__future__",
    }
)
FORBIDDEN_CALL_NAMES = frozenset(
    {"eval", "exec", "compile", "__import__", "open", "input"}
)


def load_checker():
    specification = importlib.util.spec_from_file_location(
        "promotion_regression_check", CHECKER_PATH
    )
    module = importlib.util.module_from_spec(specification)
    assert specification.loader is not None
    # Registering first keeps dataclass field resolution working for a module
    # loaded straight from a path.
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


CHECKER = load_checker()


def load_manifest() -> dict:
    return CHECKER.load_manifest(MANIFEST_PATH)


def load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def run_command(arguments: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = CHECKER.main(arguments)
    return code, out.getvalue(), err.getvalue()


def evaluate_capture(capture: dict) -> object:
    return CHECKER.evaluate(load_manifest(), capture)


def outcome_for(report, sentinel_id: str) -> str:
    for result in report.results:
        if result.sentinel_id == sentinel_id:
            return result.outcome
    raise AssertionError(f"sentinel {sentinel_id!r} is not in the report")


class ManifestStructureTests(unittest.TestCase):
    def test_manifest_validates_against_its_schema(self):
        manifest = load_manifest()
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(CHECKER.validate_manifest(manifest, schema), [])

    def test_manifest_declares_the_promoted_release_and_build(self):
        target = load_manifest()["target"]
        self.assertEqual(target["release"], "2.2.0-beta.39")
        self.assertEqual(target["tag"], "v2.2.0-beta.39")
        self.assertEqual(
            target["build_sha"], "bf4236cc99c9515325d7cba0fd8d2f909d3573cb"
        )

    def test_every_required_register_sentinel_is_encoded(self):
        identifiers = {item["id"] for item in load_manifest()["sentinels"]}
        self.assertEqual(REQUIRED_SENTINEL_IDS - identifiers, set())

    def test_expected_fail_sentinels_name_their_open_deficiency(self):
        manifest = load_manifest()
        observed = {}
        for sentinel in manifest["sentinels"]:
            if sentinel["expected_status"] != "expected_fail":
                self.assertNotIn(
                    "deficiency",
                    sentinel,
                    msg=f"{sentinel['id']} is expected_pass but carries a deficiency",
                )
                continue
            deficiency = sentinel["deficiency"]
            observed[sentinel["id"]] = deficiency["register_item"]
            self.assertTrue(deficiency["summary"].strip())
            self.assertTrue(deficiency["observed_evidence"].strip())
        self.assertEqual(observed, EXPECTED_FAIL_DEFICIENCIES)

    def test_approval_consumption_is_not_double_counted(self):
        # Register item #3 is the same orphaned task as #2 seen differently. It
        # must appear only as a related item, never as its own sentinel.
        manifest = load_manifest()
        primary = [
            sentinel["deficiency"]["register_item"]
            for sentinel in manifest["sentinels"]
            if sentinel["expected_status"] == "expected_fail"
        ]
        self.assertNotIn(3, primary)
        f3 = next(
            sentinel
            for sentinel in manifest["sentinels"]
            if sentinel["id"] == "f3-ready-locks-and-recovery"
        )
        self.assertIn(3, f3["deficiency"]["related_register_items"])

    def test_only_the_documented_probe_is_not_read_only(self):
        manifest = load_manifest()
        non_read_only = [
            item for item in manifest["observations"] if item["effect_class"] != "read_only"
        ]
        self.assertEqual([item["id"] for item in non_read_only], ["helper_no_change_probe"])
        self.assertEqual(non_read_only[0]["effect_class"], "no_change_probe")
        self.assertIn("off", non_read_only[0]["precondition"])

    def test_operator_local_targets_are_not_committed(self):
        # Instance-specific identifiers must stay in the operator's capture
        # file, never in the repository.
        identifying = {"automation_id", "task_id", "expected_compatibility_entry_id"}
        for observation in load_manifest()["observations"]:
            if observation.get("target_binding") != "operator_local":
                continue
            self.assertTrue(observation["target_note"].strip())
            self.assertEqual(
                identifying & set(observation.get("arguments") or {}),
                set(),
                msg=f"{observation['id']} commits an operator-specific identifier",
            )

    def test_readme_documents_every_classification_and_the_flip_procedure(self):
        text = README_PATH.read_text(encoding="utf-8")
        for outcome in (
            CHECKER.CONFIRMED,
            CHECKER.REGRESSION,
            CHECKER.KNOWN_FAILING,
            CHECKER.UNEXPECTED_PASS,
            CHECKER.NOT_CAPTURED,
        ):
            self.assertIn(outcome, text)
        self.assertIn("expected_fail", text)
        self.assertIn("expected_pass", text)


class PathResolutionTests(unittest.TestCase):
    def test_resolves_nested_fields(self):
        resolution = CHECKER.resolve_path({"a": {"b": {"c": 3}}}, "a.b.c")
        self.assertTrue(resolution.found)
        self.assertEqual(resolution.value, 3)

    def test_resolves_list_index(self):
        resolution = CHECKER.resolve_path({"a": [10, 20, 30]}, "a.1")
        self.assertTrue(resolution.found)
        self.assertEqual(resolution.value, 20)

    def test_resolves_list_selector(self):
        document = {"coverage": [{"source_type": "automation", "n": 1}, {"source_type": "blueprint", "n": 2}]}
        resolution = CHECKER.resolve_path(document, "coverage[source_type=blueprint].n")
        self.assertTrue(resolution.found)
        self.assertEqual(resolution.value, 2)

    def test_missing_field_is_unresolved_rather_than_none(self):
        resolution = CHECKER.resolve_path({"a": {}}, "a.b")
        self.assertFalse(resolution.found)
        self.assertIn("no field at a.b", resolution.reason)

    def test_null_value_still_resolves(self):
        resolution = CHECKER.resolve_path({"a": None}, "a")
        self.assertTrue(resolution.found)
        self.assertIsNone(resolution.value)

    def test_ambiguous_selector_is_refused(self):
        document = {"coverage": [{"k": "x"}, {"k": "x"}]}
        resolution = CHECKER.resolve_path(document, "coverage[k=x]")
        self.assertFalse(resolution.found)
        self.assertIn("2 list items match", resolution.reason)


class PredicateTests(unittest.TestCase):
    def test_booleans_never_satisfy_numeric_expectations(self):
        self.assertFalse(CHECKER.strict_equal(1, True))
        self.assertFalse(CHECKER.strict_equal(0, False))
        self.assertFalse(CHECKER.strict_equal(True, 1))
        self.assertTrue(CHECKER.strict_equal(True, True))
        self.assertTrue(CHECKER.strict_equal(0, 0))

    def _check(self, check: dict, response: dict, others: dict | None = None):
        return CHECKER.evaluate_check(check, response, others or {})

    def test_equals_rejects_a_counter_masquerading_as_true(self):
        result = self._check(
            {"path": "n", "operator": "equals", "value": True}, {"n": 1}
        )
        self.assertFalse(result.passed)

    def test_unresolved_path_fails_every_operator_except_absent(self):
        for operator, extra in (
            ("equals", {"value": 1}),
            ("present", {}),
            ("one_of", {"values": [1]}),
            ("gte", {"value": 1}),
            ("matches", {"value": "x"}),
        ):
            with self.subTest(operator=operator):
                result = self._check({"path": "missing", "operator": operator, **extra}, {})
                self.assertFalse(result.passed)
        absent = self._check({"path": "missing", "operator": "absent"}, {})
        self.assertTrue(absent.passed)

    def test_present_rejects_an_explicit_null(self):
        self.assertFalse(self._check({"path": "a", "operator": "present"}, {"a": None}).passed)
        self.assertTrue(self._check({"path": "a", "operator": "absent"}, {"a": None}).passed)

    def test_one_of_and_comparison_operators(self):
        self.assertTrue(
            self._check(
                {"path": "s", "operator": "one_of", "values": ["on", "off"]}, {"s": "off"}
            ).passed
        )
        self.assertFalse(
            self._check(
                {"path": "s", "operator": "one_of", "values": ["on", "off"]},
                {"s": "unavailable"},
            ).passed
        )
        self.assertTrue(self._check({"path": "n", "operator": "lte", "value": 5}, {"n": 5}).passed)
        self.assertFalse(self._check({"path": "n", "operator": "gt", "value": 5}, {"n": 5}).passed)

    def test_comparison_refuses_non_numeric_values(self):
        result = self._check({"path": "n", "operator": "gte", "value": 1}, {"n": "many"})
        self.assertFalse(result.passed)
        self.assertIn("needs two numbers", result.detail)

    def test_matches_uses_full_match(self):
        self.assertTrue(
            self._check({"path": "v", "operator": "matches", "value": r"\d+"}, {"v": "42"}).passed
        )
        self.assertFalse(
            self._check({"path": "v", "operator": "matches", "value": r"\d+"}, {"v": "42a"}).passed
        )

    def test_cross_observation_reference(self):
        check = {
            "path": "a",
            "operator": "equals_observation_path",
            "observation": "other",
            "reference_path": "b",
        }
        self.assertTrue(self._check(check, {"a": 7}, {"other": {"b": 7}}).passed)
        self.assertFalse(self._check(check, {"a": 7}, {"other": {"b": 8}}).passed)
        missing = self._check(check, {"a": 7}, {})
        self.assertFalse(missing.passed)
        self.assertIn("was not captured", missing.detail)

    def test_unsupported_operator_is_refused(self):
        with self.assertRaises(CHECKER.CheckerError):
            self._check({"path": "a", "operator": "approximately"}, {"a": 1})


class ClassificationTests(unittest.TestCase):
    def test_classification_table(self):
        self.assertEqual(CHECKER.classify("expected_pass", True), CHECKER.CONFIRMED)
        self.assertEqual(CHECKER.classify("expected_pass", False), CHECKER.REGRESSION)
        self.assertEqual(CHECKER.classify("expected_fail", False), CHECKER.KNOWN_FAILING)
        self.assertEqual(CHECKER.classify("expected_fail", True), CHECKER.UNEXPECTED_PASS)

    def test_unknown_expected_status_is_refused(self):
        with self.assertRaises(CHECKER.CheckerError):
            CHECKER.classify("probably_fine", True)


class CurrentStateCaptureTests(unittest.TestCase):
    """The captured live state of 2.2.0-beta.39 as the register recorded it."""

    def test_current_state_yields_only_known_failures(self):
        report = evaluate_capture(load_fixture())
        counts = report.counts
        self.assertEqual(counts[CHECKER.REGRESSION], 0)
        self.assertEqual(counts[CHECKER.NOT_CAPTURED], 0)
        self.assertEqual(counts[CHECKER.UNEXPECTED_PASS], 0)
        self.assertEqual(counts[CHECKER.KNOWN_FAILING], 3)
        self.assertEqual(counts[CHECKER.CONFIRMED], 22)
        self.assertEqual(CHECKER.exit_code(report), CHECKER.EXIT_OK)

    def test_the_three_open_deficiencies_are_the_known_failures(self):
        report = evaluate_capture(load_fixture())
        failing = {item.sentinel_id for item in report.by_outcome(CHECKER.KNOWN_FAILING)}
        self.assertEqual(failing, set(EXPECTED_FAIL_DEFICIENCIES))

    def test_f3_known_failure_reports_the_accounting_disagreement(self):
        report = evaluate_capture(load_fixture())
        result = next(
            item for item in report.results if item.sentinel_id == "f3-ready-locks-and-recovery"
        )
        details = " ".join(check.detail for check in result.failed_checks)
        self.assertIn("recovering", details)
        self.assertIn("observed 1 against", details)

    def test_fixture_records_no_governance_plan_identifiers(self):
        text = FIXTURE_PATH.read_text(encoding="utf-8")
        for plan_id in ("00dbf0dfbd7f4f3e84d01a30d18ffdde", "72cd49adeeec492c91679ef2c78bc325"):
            self.assertNotIn(plan_id, text)

    def test_fixture_declares_that_it_is_not_a_live_capture(self):
        provenance = load_fixture()["_provenance"]
        self.assertTrue(provenance["not_a_production_record"])
        self.assertIn("No Home Assistant", provenance["not_built_from"])


class RegressionDetectionTests(unittest.TestCase):
    def test_a_broken_accepted_behavior_is_a_regression(self):
        capture = load_fixture()
        gateway = capture["observations"]["server_health"]["response"]["data"][
            "upstream_read_gateway"
        ]
        gateway["fallback_count"] = 1
        report = evaluate_capture(capture)
        self.assertEqual(
            outcome_for(report, "upstream-read-gateway-zero-fallback"), CHECKER.REGRESSION
        )
        self.assertEqual(report.counts[CHECKER.REGRESSION], 1)
        self.assertEqual(CHECKER.exit_code(report), CHECKER.EXIT_REGRESSION)

    def test_a_stale_build_sha_is_a_regression(self):
        capture = load_fixture()
        capture["observations"]["server_identity"]["response"]["data"]["server"][
            "build_sha"
        ] = "0" * 40
        report = evaluate_capture(capture)
        self.assertEqual(outcome_for(report, "runtime-build-provenance"), CHECKER.REGRESSION)

    def test_a_dispatched_stale_state_canary_is_a_regression(self):
        capture = load_fixture()
        task = capture["observations"]["stale_state_canary_task"]["response"]["data"]
        task["provider_attempt_count"] = 1
        task["dispatched_at"] = "2026-08-17T00:00:01+00:00"
        report = evaluate_capture(capture)
        self.assertEqual(
            outcome_for(report, "stale-state-canary-held-pre-dispatch"), CHECKER.REGRESSION
        )

    def test_a_promoted_held_read_is_a_regression(self):
        capture = load_fixture()
        evidence = capture["observations"]["held_read_canary_not_found"]["response"][
            "details"
        ]["canary_evidence"]
        evidence["reviewed_classification_after"] = "automatic_read"
        evidence["promotion_performed"] = True
        report = evaluate_capture(capture)
        self.assertEqual(
            outcome_for(report, "held-read-contract-and-not-found-behavior"),
            CHECKER.REGRESSION,
        )

    def test_regression_and_known_failing_are_never_merged_in_the_summary(self):
        capture = load_fixture()
        capture["observations"]["server_health"]["response"]["data"]["configuration"][
            "valid"
        ] = False
        report = evaluate_capture(capture)
        text = CHECKER.render_text(report)
        self.assertEqual(report.counts[CHECKER.REGRESSION], 1)
        self.assertEqual(report.counts[CHECKER.KNOWN_FAILING], 3)
        self.assertIn("REGRESSION        1", text)
        self.assertIn("KNOWN_FAILING     3", text)
        regression_block = text.split("--- REGRESSION")[1].split("--- ")[0]
        self.assertIn("configuration-validation", regression_block)
        for known in EXPECTED_FAIL_DEFICIENCIES:
            self.assertNotIn(known, regression_block)
        self.assertIn("Do not promote", text)


class UnexpectedPassTests(unittest.TestCase):
    def _fixed_capture(self) -> dict:
        capture = load_fixture()
        health = capture["observations"]["server_health"]["response"]["data"]
        health["governance"]["f3"]["status"] = "ready"
        health["governance"]["f3"]["nonterminal_execution_count"] = 0
        health["governance"]["projection_failure_count"] = 0
        health["governance"]["policy_snapshot_mismatches"] = 0
        health["governance"]["approval_sequence_failures"] = 0
        dependency = capture["observations"]["native_dependency_read"]["response"]["data"]
        dependency["overview"]["coverage_complete"] = True
        blueprint = next(
            item
            for item in dependency["source_coverage"]
            if item["source_type"] == "blueprint"
        )
        blueprint["completeness"] = "complete"
        blueprint["failed_item_count"] = 0
        return capture

    def test_fixed_deficiencies_surface_as_unexpected_pass(self):
        report = evaluate_capture(self._fixed_capture())
        self.assertEqual(report.counts[CHECKER.UNEXPECTED_PASS], 3)
        self.assertEqual(report.counts[CHECKER.KNOWN_FAILING], 0)
        self.assertEqual(report.counts[CHECKER.REGRESSION], 0)
        self.assertEqual(
            {item.sentinel_id for item in report.by_outcome(CHECKER.UNEXPECTED_PASS)},
            set(EXPECTED_FAIL_DEFICIENCIES),
        )

    def test_unexpected_pass_does_not_block_promotion_but_asks_for_a_human(self):
        report = evaluate_capture(self._fixed_capture())
        self.assertEqual(CHECKER.exit_code(report), CHECKER.EXIT_OK)
        self.assertIn("flip its expected_status", CHECKER.render_text(report))

    def test_the_checker_never_rewrites_the_manifest(self):
        before = MANIFEST_PATH.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            capture_path = Path(directory) / "capture.json"
            capture_path.write_text(json.dumps(self._fixed_capture()), encoding="utf-8")
            code, _, _ = run_command(["evaluate", "--capture", str(capture_path)])
        self.assertEqual(code, CHECKER.EXIT_OK)
        self.assertEqual(MANIFEST_PATH.read_bytes(), before)


class IncompleteRunTests(unittest.TestCase):
    def test_a_missing_observation_is_not_captured_rather_than_a_regression(self):
        capture = load_fixture()
        del capture["observations"]["dashboard_map_read"]
        report = evaluate_capture(capture)
        self.assertEqual(
            outcome_for(report, "dashboard-hyphenless-map-read"), CHECKER.NOT_CAPTURED
        )
        self.assertEqual(report.counts[CHECKER.REGRESSION], 0)
        self.assertEqual(CHECKER.exit_code(report), CHECKER.EXIT_INCOMPLETE)
        self.assertIn("run is incomplete", CHECKER.render_text(report))

    def test_a_skipped_probe_reports_the_recorded_reason(self):
        capture = load_fixture()
        capture["observations"]["helper_no_change_probe"] = {
            "tool": "create_helper_state_plan",
            "response": None,
            "not_recorded_reason": "precondition not met: the helper was on",
        }
        report = evaluate_capture(capture)
        result = next(
            item for item in report.results if item.sentinel_id == "helper-no-change-path"
        )
        self.assertEqual(result.outcome, CHECKER.NOT_CAPTURED)
        self.assertIn("precondition not met", result.note)

    def test_an_unresolvable_cross_reference_is_not_captured(self):
        capture = load_fixture()
        del capture["observations"]["server_identity"]
        report = evaluate_capture(capture)
        self.assertEqual(
            outcome_for(report, "runtime-tool-accounting-agreement"), CHECKER.NOT_CAPTURED
        )
        self.assertEqual(report.counts[CHECKER.REGRESSION], 0)

    def test_unknown_capture_ids_are_reported_not_silently_ignored(self):
        capture = load_fixture()
        capture["observations"]["typo_observation"] = {"response": {}}
        report = evaluate_capture(capture)
        self.assertEqual(report.unknown_observations, ["typo_observation"])
        self.assertIn("typo_observation", CHECKER.render_text(report))


class CaptureParsingTests(unittest.TestCase):
    def test_a_raw_json_string_response_is_accepted(self):
        capture = load_fixture()
        entry = capture["observations"]["server_identity"]
        entry["response"] = json.dumps(entry["response"])
        report = evaluate_capture(capture)
        self.assertEqual(outcome_for(report, "runtime-server-version"), CHECKER.CONFIRMED)

    def test_an_unparseable_response_string_is_refused(self):
        capture = load_fixture()
        capture["observations"]["server_identity"]["response"] = "{not json"
        with self.assertRaises(CHECKER.CheckerError):
            evaluate_capture(capture)

    def test_a_capture_without_observations_is_refused(self):
        with self.assertRaises(CHECKER.CheckerError):
            evaluate_capture({"capture_version": 1})


class CommandLineTests(unittest.TestCase):
    def test_validate_accepts_the_committed_manifest(self):
        code, out, _ = run_command(["validate"])
        self.assertEqual(code, CHECKER.EXIT_OK)
        self.assertIn("25 sentinels", out)

    def test_validate_rejects_a_manifest_with_a_dangling_observation(self):
        manifest = load_manifest()
        manifest["sentinels"][0]["observation"] = "not_declared"
        with tempfile.TemporaryDirectory() as directory:
            import yaml

            path = Path(directory) / "manifest.yaml"
            path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
            code, _, err = run_command(["--manifest", str(path), "validate"])
        self.assertEqual(code, CHECKER.EXIT_USAGE)
        self.assertIn("is not declared", err)

    def test_validate_rejects_an_expected_fail_without_a_deficiency(self):
        manifest = load_manifest()
        sentinel = next(
            item for item in manifest["sentinels"] if item["id"] == "configuration-validation"
        )
        sentinel["expected_status"] = "expected_fail"
        with tempfile.TemporaryDirectory() as directory:
            import yaml

            path = Path(directory) / "manifest.yaml"
            path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
            code, _, err = run_command(["--manifest", str(path), "validate"])
        self.assertEqual(code, CHECKER.EXIT_USAGE)
        self.assertIn("deficiency", err)

    def test_plan_lists_every_observation_and_flags_the_precondition(self):
        code, out, _ = run_command(["plan"])
        self.assertEqual(code, CHECKER.EXIT_OK)
        for observation in load_manifest()["observations"]:
            self.assertIn(observation["id"], out)
        self.assertIn("PRECONDITION", out)

    def test_template_produces_an_empty_capture_for_every_observation(self):
        code, out, _ = run_command(["template"])
        self.assertEqual(code, CHECKER.EXIT_OK)
        template = json.loads(out)
        self.assertEqual(
            set(template["observations"]),
            {item["id"] for item in load_manifest()["observations"]},
        )
        for entry in template["observations"].values():
            self.assertIsNone(entry["response"])

    def test_evaluate_emits_machine_readable_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.json"
            path.write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            code, out, _ = run_command(
                ["evaluate", "--capture", str(path), "--format", "json"]
            )
        self.assertEqual(code, CHECKER.EXIT_OK)
        payload = json.loads(out)
        self.assertFalse(payload["promotion_blocked"])
        self.assertTrue(payload["run_complete"])
        self.assertEqual(payload["counts"][CHECKER.KNOWN_FAILING], 3)
        self.assertEqual(len(payload["sentinels"]), 25)

    def test_evaluate_reports_a_missing_capture_file_without_traceback(self):
        code, _, err = run_command(["evaluate", "--capture", "no/such/capture.json"])
        self.assertEqual(code, CHECKER.EXIT_USAGE)
        self.assertIn("Cannot read capture", err)


class ReadOnlyByConstructionTests(unittest.TestCase):
    """The checker must not be able to reach the target or the filesystem."""

    def setUp(self):
        self.tree = ast.parse(CHECKER_PATH.read_text(encoding="utf-8"))

    def test_only_allowlisted_modules_are_imported(self):
        imported: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(
            imported - ALLOWED_IMPORTS,
            set(),
            msg="the checker imported a module outside the read-only allowlist",
        )

    def test_no_transport_or_process_module_is_referenced(self):
        source = CHECKER_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "socket",
            "urllib",
            "http.client",
            "requests",
            "aiohttp",
            "httpx",
            "subprocess",
            "os.system",
            "asyncio",
        ):
            self.assertNotIn(forbidden, source, msg=f"{forbidden} must not appear")

    def test_no_dynamic_evaluation_or_raw_file_handles(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, FORBIDDEN_CALL_NAMES)

    def test_no_write_capable_path_operation(self):
        writers = {"write_text", "write_bytes", "mkdir", "unlink", "rmdir", "touch", "rename"}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(
                    node.func.attr,
                    writers,
                    msg=f"the checker calls {node.func.attr}, which can write",
                )

    def test_running_every_command_leaves_the_repository_unchanged(self):
        watched = [CHECKER_PATH, MANIFEST_PATH, SCHEMA_PATH, FIXTURE_PATH]
        before = {path: path.read_bytes() for path in watched}
        run_command(["validate"])
        run_command(["plan"])
        run_command(["template"])
        run_command(["evaluate", "--capture", str(FIXTURE_PATH)])
        for path in watched:
            self.assertEqual(path.read_bytes(), before[path])


class ManifestAndCheckerAgreementTests(unittest.TestCase):
    def test_every_operator_used_by_the_manifest_is_implemented(self):
        used = {
            check["operator"]
            for sentinel in load_manifest()["sentinels"]
            for check in sentinel["checks"]
        }
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        declared = set(schema["$defs"]["check"]["properties"]["operator"]["enum"])
        self.assertEqual(used - declared, set())
        response = {"probe": 1}
        for operator in used:
            with self.subTest(operator=operator):
                check = {"path": "probe", "operator": operator}
                if operator in {"equals", "not_equals", "gte", "lte", "gt", "lt", "matches"}:
                    check["value"] = 1
                if operator == "one_of":
                    check["values"] = [1]
                if operator == "equals_observation_path":
                    check["observation"] = "other"
                    check["reference_path"] = "probe"
                # Only asserting that the operator is implemented, not its verdict.
                CHECKER.evaluate_check(check, response, {"other": response})

    def test_every_sentinel_check_path_resolves_against_the_fixture(self):
        # A path that no longer resolves would silently turn into a REGRESSION
        # on the next live run, so the fixture must exercise all of them.
        capture = load_fixture()
        manifest = load_manifest()
        responses = {
            key: entry["response"]
            for key, entry in capture["observations"].items()
            if entry.get("response") is not None
        }
        unresolved = []
        for sentinel in manifest["sentinels"]:
            response = responses[sentinel["observation"]]
            for check in sentinel["checks"]:
                if check["operator"] == "absent":
                    continue
                if not CHECKER.resolve_path(response, check["path"]).found:
                    unresolved.append(f"{sentinel['id']}:{check['path']}")
        self.assertEqual(unresolved, [])

    def test_the_fixture_covers_every_declared_observation(self):
        capture = load_fixture()
        self.assertEqual(
            set(capture["observations"]),
            {item["id"] for item in load_manifest()["observations"]},
        )

    def test_evaluation_does_not_mutate_the_supplied_capture(self):
        capture = load_fixture()
        original = copy.deepcopy(capture)
        evaluate_capture(capture)
        self.assertEqual(capture, original)


if __name__ == "__main__":  # pragma: no cover - direct invocation
    unittest.main()
