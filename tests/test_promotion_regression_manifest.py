"""Offline tests for the promotion regression manifest (HAMCP-089 #22)."""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
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
CONTRACT_PATH = (
    ROOT / "tests" / "fixtures" / "promotion_regression" / "deficiency22_contract_v1.json"
)
PROJECTION_FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "promotion_regression" / "projection_sources_v1.json"
)
CAPABILITIES_PATH = (
    ROOT
    / "hass_mcp_engineering_beta"
    / "ha_mcp_engineering"
    / "capabilities.py"
)
UPSTREAM_POLICY_PATH = (
    ROOT
    / "hass_mcp_engineering_beta"
    / "ha_mcp_engineering"
    / "upstream_tool_policy_8_2_0.json"
)

def _load_checker():
    specification = importlib.util.spec_from_file_location(
        "promotion_regression_check", CHECKER_PATH
    )
    module = importlib.util.module_from_spec(specification)
    assert specification.loader is not None
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


CHECKER = _load_checker()


def load_manifest() -> dict:
    return CHECKER.load_manifest(MANIFEST_PATH)


def load_schema() -> dict:
    return CHECKER.load_json(SCHEMA_PATH, "schema")


def load_fixture() -> dict:
    return CHECKER.load_json(FIXTURE_PATH, "fixture")


def load_contract() -> dict:
    return CHECKER.load_json(CONTRACT_PATH, "deficiency 22 contract")


def load_projection_fixture() -> dict:
    return CHECKER.load_json(PROJECTION_FIXTURE_PATH, "projection fixture")


def evaluate(capture: dict):
    return CHECKER.evaluate(load_manifest(), capture, load_schema())


def result_for(report, sentinel_id: str):
    return next(item for item in report.results if item.sentinel_id == sentinel_id)


def observation(capture: dict, identifier: str) -> dict:
    return capture["observations"][identifier]


def mark_not_captured(capture: dict, identifier: str) -> None:
    entry = observation(capture, identifier)
    entry.pop("evidence", None)
    entry.pop("absent_paths", None)
    entry["status"] = "not_captured"
    entry["not_recorded_reason"] = "read-only observation unavailable"


class ManifestStructureTests(unittest.TestCase):
    def test_manifest_and_capture_validate(self):
        manifest, schema, capture = load_manifest(), load_schema(), load_fixture()
        self.assertEqual(CHECKER.validate_manifest(manifest, schema), [])
        self.assertEqual(CHECKER.validate_capture(manifest, capture, schema), [])

    def test_exact_target_and_manifest_identity(self):
        manifest, capture = load_manifest(), load_fixture()
        self.assertEqual(manifest["manifest_version"], 2)
        self.assertEqual(manifest["target"]["release"], "2.2.0-beta.39")
        self.assertEqual(
            manifest["target"]["build_sha"],
            "bf4236cc99c9515325d7cba0fd8d2f909d3573cb",
        )
        self.assertEqual(capture["manifest_digest"], CHECKER.canonical_manifest_digest(manifest))
        self.assertEqual(capture["target"], {
            "release": manifest["target"]["release"],
            "build_sha": manifest["target"]["build_sha"],
        })

    def test_required_sentinels_and_only_bounded_known_failures(self):
        contract = load_contract()
        sentinels = {item["id"]: item for item in load_manifest()["sentinels"]}
        expected_by_section = {
            section: set(contract[section])
            for section in (
                "minimum_permanent_sentinels",
                "confirmed_regression_passes",
            )
        }
        actual_by_section = {
            section: {
                item["id"]
                for item in sentinels.values()
                if item["register_section"] == section
            }
            for section in expected_by_section
        }
        self.assertEqual(actual_by_section, expected_by_section)
        actual = {
            identifier: item["deficiency"]["register_item"]
            for identifier, item in sentinels.items()
            if item["expected_status"] == "expected_fail"
        }
        self.assertEqual(actual, contract["known_failures"])
        self.assertNotIn(3, actual.values())
        self.assertEqual(
            sentinels["f3-ready-locks-and-recovery"]["deficiency"][
                "related_register_items"
            ],
            [14],
        )
        for item in sentinels.values():
            if item["expected_status"] == "expected_fail":
                self.assertTrue(item["desired_checks"])
                self.assertTrue(item["known_failure_checks"])
            else:
                self.assertNotIn("known_failure_checks", item)

    def test_default_manifest_contains_only_authoritatively_read_only_tools(self):
        tree = ast.parse(CAPABILITIES_PATH.read_text(encoding="utf-8"))
        declarations: list[dict] = []
        for node in tree.body:
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
                continue
            if node.target.id in {"CAPABILITIES", "BETA_NATIVE_CAPABILITIES"}:
                declarations.extend(ast.literal_eval(node.value))
        native_risk = {item["tool"]: item.get("risk") for item in declarations}
        upstream = json.loads(UPSTREAM_POLICY_PATH.read_text(encoding="utf-8"))
        upstream_tools = {item["exposed_name"]: item for item in upstream["tools"]}

        for entry in load_manifest()["observations"]:
            with self.subTest(tool=entry["tool"]):
                self.assertEqual(entry["effect_class"], "read_only")
                if entry["tool"] in native_risk:
                    self.assertEqual(native_risk[entry["tool"]], "read")
                else:
                    policy = upstream_tools[entry["tool"]]
                    self.assertEqual(policy["classification"], "automatic_read")
                    self.assertIs(policy["reviewed_annotations"]["readOnlyHint"], True)
                    self.assertIs(policy["reviewed_annotations"]["destructiveHint"], False)

        tools = {item["tool"] for item in load_manifest()["observations"]}
        self.assertNotIn("create_helper_state_plan", tools)
        canaries = {
            item["id"]: item
            for item in load_manifest()["separately_authorized_canaries"]
        }
        self.assertEqual(
            set(canaries), set(load_contract()["separately_authorized_canaries"])
        )
        helper = canaries["helper-no-change-path"]
        self.assertEqual(helper["tool"], "create_helper_state_plan")
        self.assertIs(helper["included_in_default_plan"], False)
        self.assertIs(helper["separate_authorization_required"], True)
        self.assertNotIn("create_helper_state_plan", CHECKER.render_plan(load_manifest()))
        self.assertNotIn(
            "create_helper_state_plan", CHECKER.render_template(load_manifest())
        )

    def test_manifest_uses_real_fidelity_contracts(self):
        manifest = load_manifest()
        observations = {item["id"]: item for item in manifest["observations"]}
        self.assertIs(
            observations["native_dependency_read"]["arguments"]["refresh_index"], True
        )
        self.assertEqual(
            observations["home_assistant_configuration_validation"]["tool"],
            "check_config",
        )
        sentinels = {item["id"]: item for item in manifest["sentinels"]}
        wait_checks = sentinels["automation-long-wait-template-readable"]["desired_checks"]
        self.assertIn(
            {
                "path": "projection.wait_template_semantic_digest",
                "operator": "equals",
                "value": "sha256:a4640bd7bfb8ecc56cc805322192168901234d6492e86906f63104fb5f0d2286",
            },
            wait_checks,
        )
        self.assertNotEqual(
            sentinels["held-read-remains-held"]["expected_status"],
            sentinels["held-read-top-level-not-found-taxonomy"]["id"],
        )

    def test_readme_is_truthful_about_manual_operator_attested_evidence(self):
        text = README_PATH.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        for phrase in (
            "operator-attested evidence",
            "not cryptographic provenance",
            "not wired into GitHub Actions",
            "separate authorization",
            "Do not retain complete",
            "KNOWN_FAILING",
            "UNEXPECTED_PASS",
            "NOT_CAPTURED",
        ):
            self.assertIn(phrase, normalized)
        self.assertIn("create_helper_state_plan", text)
        self.assertIn("does not contain", text)


class ClassificationTests(unittest.TestCase):
    def test_exact_classification_table(self):
        cases = (
            ("expected_pass", True, None, True, CHECKER.CONFIRMED),
            ("expected_pass", False, None, True, CHECKER.REGRESSION),
            ("expected_fail", True, False, True, CHECKER.UNEXPECTED_PASS),
            ("expected_fail", False, True, True, CHECKER.KNOWN_FAILING),
            ("expected_fail", False, False, True, CHECKER.REGRESSION),
            ("expected_pass", True, None, False, CHECKER.NOT_CAPTURED),
            ("expected_fail", False, True, False, CHECKER.NOT_CAPTURED),
        )
        for expected, desired, known, conclusive, outcome in cases:
            with self.subTest(outcome=outcome, expected=expected):
                self.assertEqual(
                    CHECKER.classify(
                        expected, desired, known, conclusive=conclusive
                    ),
                    outcome,
                )

    def test_current_fixture_is_truthful(self):
        report = evaluate(load_fixture())
        self.assertEqual(
            report.counts,
            {
                CHECKER.REGRESSION: 0,
                CHECKER.NOT_CAPTURED: 0,
                CHECKER.UNEXPECTED_PASS: 0,
                CHECKER.KNOWN_FAILING: 4,
                CHECKER.CONFIRMED: 26,
            },
        )
        self.assertEqual(
            {
                item.sentinel_id
                for item in report.by_outcome(CHECKER.KNOWN_FAILING)
            },
            set(load_contract()["known_failures"]),
        )

    def test_materially_worse_known_failure_is_a_regression(self):
        capture = load_fixture()
        evidence = observation(capture, "native_dependency_read")["evidence"]
        evidence["data.source_coverage[source_type=blueprint].failed_item_count"] = 3
        evidence[
            "data.source_coverage[source_type=blueprint].obligation_ledger_failed_item_count"
        ] = 3
        evidence["projection.failed_obligation_count"] = 3
        self.assertEqual(
            result_for(evaluate(capture), "dependency-index-complete-coverage").outcome,
            CHECKER.REGRESSION,
        )

    def test_missing_required_evidence_is_not_captured(self):
        capture = load_fixture()
        observation(capture, "native_dependency_read")["evidence"].pop(
            "data.index.refreshed"
        )
        self.assertEqual(
            result_for(evaluate(capture), "dependency-index-complete-coverage").outcome,
            CHECKER.NOT_CAPTURED,
        )

    def test_unexpected_pass_does_not_change_manifest_status(self):
        capture = load_fixture()
        evidence = observation(capture, "native_dependency_read")["evidence"]
        evidence["data.overview.coverage_complete"] = True
        evidence["data.source_coverage[source_type=blueprint].completeness"] = "complete"
        evidence["data.source_coverage[source_type=blueprint].failed_item_count"] = 0
        evidence[
            "data.source_coverage[source_type=blueprint].obligation_ledger_failed_item_count"
        ] = 0
        report = evaluate(capture)
        self.assertEqual(
            result_for(report, "dependency-index-complete-coverage").outcome,
            CHECKER.UNEXPECTED_PASS,
        )
        sentinel = next(
            item
            for item in load_manifest()["sentinels"]
            if item["id"] == "dependency-index-complete-coverage"
        )
        self.assertEqual(sentinel["expected_status"], "expected_fail")

    def test_unexpected_pass_does_not_require_obsolete_failure_fields(self):
        capture = load_fixture()
        evidence = observation(capture, "native_dependency_read")["evidence"]
        evidence["data.overview.coverage_complete"] = True
        evidence["data.source_coverage[source_type=blueprint].completeness"] = "complete"
        evidence["data.source_coverage[source_type=blueprint].failed_item_count"] = 0
        evidence[
            "data.source_coverage[source_type=blueprint].obligation_ledger_failed_item_count"
        ] = 0
        evidence.pop("projection.failed_obligation_signature")
        evidence.pop("projection.failed_obligation_set_fingerprint")
        self.assertEqual(
            result_for(evaluate(capture), "dependency-index-complete-coverage").outcome,
            CHECKER.UNEXPECTED_PASS,
        )

    def test_expected_pass_failure_is_a_regression(self):
        capture = load_fixture()
        observation(capture, "server_identity")["evidence"]["data.server.version"] = (
            "2.2.0-beta.38"
        )
        self.assertEqual(
            result_for(evaluate(capture), "runtime-server-version").outcome,
            CHECKER.REGRESSION,
        )

    def test_new_permanent_contracts_regress_independently(self):
        mutations = (
            (
                "governance-approval-authority-version",
                "server_health",
                "data.governance.approval_authority_version",
                2,
            ),
            (
                "execution-task-schema-version",
                "f3_orphan_task",
                "data.task_schema_version",
                2,
            ),
            (
                "historical-beta31-map-update-succeeded",
                "historical_beta31_map_update_task",
                "data.state",
                "failed_pre_dispatch",
            ),
            (
                "historical-beta31-map-cleanup-succeeded",
                "historical_beta31_map_cleanup_task",
                "data.state",
                "failed_pre_dispatch",
            ),
            (
                "historical-long-template-execution-succeeded",
                "historical_long_template_execution_task",
                "data.state",
                "failed_pre_dispatch",
            ),
        )
        for sentinel_id, observation_id, path, value in mutations:
            with self.subTest(sentinel=sentinel_id):
                capture = load_fixture()
                observation(capture, observation_id)["evidence"][path] = value
                self.assertEqual(
                    result_for(evaluate(capture), sentinel_id).outcome,
                    CHECKER.REGRESSION,
                )

    def test_missing_historical_execution_evidence_is_not_captured(self):
        cases = (
            (
                "historical_beta31_map_update_task",
                "historical-beta31-map-update-succeeded",
            ),
            (
                "historical_beta31_map_cleanup_task",
                "historical-beta31-map-cleanup-succeeded",
            ),
            (
                "historical_long_template_execution_task",
                "historical-long-template-execution-succeeded",
            ),
        )
        for observation_id, sentinel_id in cases:
            with self.subTest(sentinel=sentinel_id):
                capture = load_fixture()
                mark_not_captured(capture, observation_id)
                observation(capture, observation_id)["arguments"].pop("task_id")
                report = evaluate(capture)
                self.assertEqual(
                    result_for(report, sentinel_id).outcome,
                    CHECKER.NOT_CAPTURED,
                )
                self.assertEqual(CHECKER.exit_code(report), CHECKER.EXIT_INCOMPLETE)

    def test_missing_new_permanent_fields_are_not_captured(self):
        cases = (
            (
                "server_health",
                "data.governance.approval_authority_version",
                "governance-approval-authority-version",
            ),
            (
                "f3_orphan_task",
                "data.task_schema_version",
                "execution-task-schema-version",
            ),
            (
                "historical_beta31_map_update_task",
                "data.state",
                "historical-beta31-map-update-succeeded",
            ),
            (
                "historical_beta31_map_cleanup_task",
                "data.state",
                "historical-beta31-map-cleanup-succeeded",
            ),
            (
                "historical_long_template_execution_task",
                "data.state",
                "historical-long-template-execution-succeeded",
            ),
        )
        for observation_id, path, sentinel_id in cases:
            with self.subTest(sentinel=sentinel_id):
                capture = load_fixture()
                observation(capture, observation_id)["evidence"].pop(path)
                self.assertEqual(
                    result_for(evaluate(capture), sentinel_id).outcome,
                    CHECKER.NOT_CAPTURED,
                )


class CaptureIdentityTests(unittest.TestCase):
    def assertCaptureInvalid(self, capture: dict, fragment: str) -> None:  # noqa: N802
        with self.assertRaises(CHECKER.CheckerError) as raised:
            evaluate(capture)
        self.assertIn(fragment, str(raised.exception))

    def test_wrong_target_manifest_tool_or_fixed_arguments_are_rejected(self):
        mutations = (
            (lambda value: value["target"].__setitem__("release", "2.2.0-beta.38"), "target/release"),
            (lambda value: value["target"].__setitem__("build_sha", "0" * 40), "target/build_sha"),
            (lambda value: value.__setitem__("manifest_digest", "sha256:" + "0" * 64), "manifest_digest"),
            (lambda value: observation(value, "server_identity").__setitem__("tool", "get_server_health"), "tool does not match"),
            (lambda value: observation(value, "server_identity")["arguments"].__setitem__("check_ha", False), "fixed argument mismatch"),
        )
        for mutate, fragment in mutations:
            with self.subTest(fragment=fragment):
                capture = load_fixture()
                mutate(capture)
                self.assertCaptureInvalid(capture, fragment)

    def test_placeholder_and_malformed_metadata_are_rejected(self):
        for field, value in (
            ("captured_at", "REPLACE-WITH-UTC-TIMESTAMP"),
            ("captured_at", "2026-08-19"),
            ("captured_by", "TODO"),
            ("session_id", "placeholder"),
        ):
            with self.subTest(field=field, value=value):
                capture = load_fixture()
                capture[field] = value
                self.assertCaptureInvalid(capture, field)

    def test_operator_local_arguments_are_exactly_bound(self):
        capture = load_fixture()
        entry = observation(capture, "stale_state_canary_task")
        entry["arguments"].pop("task_id")
        self.assertCaptureInvalid(capture, "invocation arguments must contain")

        capture = load_fixture()
        observation(capture, "stale_state_canary_task")["arguments"]["task_id"] = "bad"
        self.assertCaptureInvalid(capture, "does not match")

        capture = load_fixture()
        observation(capture, "stale_state_canary_task")["evidence"]["data.task_id"] = (
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        )
        self.assertEqual(
            result_for(evaluate(capture), "stale-state-canary-held-pre-dispatch").outcome,
            CHECKER.REGRESSION,
        )

    def test_unresolved_operator_local_argument_can_remain_not_captured(self):
        capture = load_fixture()
        mark_not_captured(capture, "long_wait_template_automation_read")
        observation(capture, "long_wait_template_automation_read")["arguments"].pop(
            "automation_id"
        )
        report = evaluate(capture)
        self.assertEqual(
            result_for(report, "automation-long-wait-template-readable").outcome,
            CHECKER.NOT_CAPTURED,
        )
        self.assertEqual(CHECKER.exit_code(report), CHECKER.EXIT_INCOMPLETE)

        capture = load_fixture()
        observation(capture, "long_wait_template_automation_read")["arguments"].pop(
            "automation_id"
        )
        self.assertCaptureInvalid(capture, "invocation arguments must contain")

        template = json.loads(CHECKER.render_template(load_manifest()))
        self.assertEqual(
            template["observations"]["long_wait_template_automation_read"][
                "arguments"
            ],
            {},
        )
        self.assertEqual(
            template["observations"]["server_identity"]["arguments"],
            {"check_ha": True},
        )

    def test_unknown_conflicting_and_duplicate_observation_evidence_is_rejected(self):
        capture = load_fixture()
        capture["observations"]["undeclared"] = copy.deepcopy(
            observation(capture, "server_identity")
        )
        self.assertCaptureInvalid(capture, "undeclared observation ids")

        capture = load_fixture()
        entry = observation(capture, "server_identity")
        entry["absent_paths"].append("success")
        self.assertCaptureInvalid(capture, "both present and absent")

        raw = '{"capture_schema_version":1,"capture_schema_version":1}'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text(raw, encoding="utf-8")
            with self.assertRaises(CHECKER.CheckerError) as raised:
                CHECKER.load_json(path, "capture")
        self.assertIn("duplicate key", str(raised.exception))

        capture = load_fixture()
        duplicate = observation(capture, "historical_beta31_map_update_task")[
            "arguments"
        ]["task_id"]
        entry = observation(capture, "historical_beta31_map_cleanup_task")
        entry["arguments"]["task_id"] = duplicate
        entry["evidence"]["data.task_id"] = duplicate
        self.assertCaptureInvalid(capture, "duplicates captured task identity")


class SentinelFidelityTests(unittest.TestCase):
    def test_cached_dependency_evidence_cannot_satisfy_fresh_scan(self):
        capture = load_fixture()
        evidence = observation(capture, "native_dependency_read")["evidence"]
        evidence["data.index.refreshed"] = False
        evidence["data.index.cache_hit"] = True
        self.assertEqual(
            result_for(evaluate(capture), "dependency-index-complete-coverage").outcome,
            CHECKER.REGRESSION,
        )

    def test_unrelated_pre_dispatch_failure_cannot_satisfy_stale_state(self):
        capture = load_fixture()
        evidence = observation(capture, "stale_state_canary_task")["evidence"]
        evidence["data.last_error.error_code"] = "authentication_failed"
        evidence["data.last_error.reason"] = "provider_credentials_unavailable"
        self.assertEqual(
            result_for(evaluate(capture), "stale-state-canary-held-pre-dispatch").outcome,
            CHECKER.REGRESSION,
        )

    def test_missing_wait_template_cannot_satisfy_long_automation_sentinel(self):
        capture = load_fixture()
        evidence = observation(capture, "long_wait_template_automation_read")["evidence"]
        evidence["projection.wait_template_count"] = 0
        evidence["projection.wait_template_length"] = 0
        evidence["projection.wait_template_semantic_digest"] = "sha256:" + "0" * 64
        self.assertEqual(
            result_for(evaluate(capture), "automation-long-wait-template-readable").outcome,
            CHECKER.REGRESSION,
        )

    def test_engineering_health_cannot_substitute_for_ha_configuration_validation(self):
        capture = load_fixture()
        mark_not_captured(capture, "home_assistant_configuration_validation")
        report = evaluate(capture)
        self.assertEqual(
            result_for(report, "configuration-validation").outcome,
            CHECKER.NOT_CAPTURED,
        )
        self.assertEqual(
            result_for(report, "home-assistant-connectivity").outcome,
            CHECKER.CONFIRMED,
        )

    def test_held_status_and_top_level_taxonomy_are_independent(self):
        capture = load_fixture()
        evidence = observation(capture, "held_read_canary_not_found")["evidence"]
        evidence["error_code"] = "RESOURCE_NOT_FOUND"
        evidence["details.failure_category"] = "resource_not_found"
        evidence["retryable"] = False
        report = evaluate(capture)
        self.assertEqual(
            result_for(report, "held-read-remains-held").outcome,
            CHECKER.CONFIRMED,
        )
        self.assertEqual(
            result_for(report, "held-read-top-level-not-found-taxonomy").outcome,
            CHECKER.UNEXPECTED_PASS,
        )

        capture = load_fixture()
        evidence = observation(capture, "held_read_canary_not_found")["evidence"]
        evidence["details.canary_evidence.promotion_performed"] = True
        report = evaluate(capture)
        self.assertEqual(
            result_for(report, "held-read-remains-held").outcome,
            CHECKER.REGRESSION,
        )
        self.assertEqual(
            result_for(report, "held-read-top-level-not-found-taxonomy").outcome,
            CHECKER.KNOWN_FAILING,
        )

    def test_changed_known_failure_identity_is_a_regression(self):
        capture = load_fixture()
        evidence = observation(capture, "native_dependency_read")["evidence"]
        evidence["projection.failed_obligation_signature"] = (
            "blueprint:Other/example.yaml:provider_failure:count=2"
        )
        evidence["projection.failed_obligation_set_fingerprint"] = "sha256:" + "1" * 64
        self.assertEqual(
            result_for(evaluate(capture), "dependency-index-complete-coverage").outcome,
            CHECKER.REGRESSION,
        )


class BoundsAndDeterminismTests(unittest.TestCase):
    def test_projection_derivation_matches_sanitized_source_fixtures(self):
        manifest = load_manifest()
        for case in load_projection_fixture()["cases"]:
            with self.subTest(observation=case["observation"]):
                first = CHECKER.derive_projection(
                    manifest, case["observation"], copy.deepcopy(case["source"])
                )
                second = CHECKER.derive_projection(
                    manifest, case["observation"], copy.deepcopy(case["source"])
                )
                self.assertEqual(first, case["expected"])
                self.assertEqual(first, second)
                self.assertEqual(
                    json.dumps(first, sort_keys=True, separators=(",", ":")),
                    json.dumps(second, sort_keys=True, separators=(",", ":")),
                )

    def test_material_projection_sources_change_the_projection(self):
        manifest = load_manifest()
        cases = {
            item["observation"]: item
            for item in load_projection_fixture()["cases"]
        }
        dependency = cases["native_dependency_read"]
        changed_dependency = copy.deepcopy(dependency["source"])
        changed_dependency["failed_obligations"][0]["source_identity"] = (
            "Blackshome/changed-sensor-light.yaml"
        )
        self.assertNotEqual(
            CHECKER.derive_projection(
                manifest, dependency["observation"], changed_dependency
            ),
            dependency["expected"],
        )

        wait = cases["long_wait_template_automation_read"]
        changed_wait = copy.deepcopy(wait["source"])
        changed_wait["action"][2]["wait_template"] += "\n"
        changed_projection = CHECKER.derive_projection(
            manifest, wait["observation"], changed_wait
        )
        self.assertNotEqual(
            changed_projection["projection.wait_template_semantic_digest"],
            wait["expected"]["projection.wait_template_semantic_digest"],
        )
        moved_wait = copy.deepcopy(wait["source"])
        moved_wait["action"].insert(0, {"delay": "00:00:02"})
        moved_projection = CHECKER.derive_projection(
            manifest, wait["observation"], moved_wait
        )
        self.assertEqual(
            moved_projection["projection.wait_template_action_path"], "/action/3"
        )
        self.assertNotEqual(
            moved_projection["projection.wait_template_semantic_digest"],
            wait["expected"]["projection.wait_template_semantic_digest"],
        )

    def test_projection_sources_are_bounded_and_sanitized(self):
        manifest = load_manifest()
        source = {
            "failed_obligations": [
                {
                    "source_type": "blueprint",
                    "source_identity": "x" * (CHECKER.MAX_VALUE_BYTES + 1),
                    "reason_code": "failure",
                    "count": 1,
                }
            ],
            "unique_dependency_source_count": 0,
        }
        with self.assertRaises(CHECKER.CheckerError) as raised:
            CHECKER.derive_projection(manifest, "native_dependency_read", source)
        self.assertIn("string exceeds", str(raised.exception))

        source["failed_obligations"][0]["source_identity"] = (
            "Bearer abcdefghijklmnopqrstuvwxyz"
        )
        with self.assertRaises(CHECKER.CheckerError) as raised:
            CHECKER.derive_projection(manifest, "native_dependency_read", source)
        self.assertIn("credential material", str(raised.exception))

    def test_oversized_and_sensitive_capture_content_is_rejected(self):
        capture = load_fixture()
        observation(capture, "home_assistant_configuration_validation")["evidence"][
            "data.result"
        ] = "x" * (CHECKER.MAX_VALUE_BYTES + 1)
        with self.assertRaises(CHECKER.CheckerError) as raised:
            evaluate(capture)
        self.assertIn("string exceeds", str(raised.exception))

        capture = load_fixture()
        capture["captured_by"] = "Bearer abcdefghijklmnopqrstuvwxyz"
        with self.assertRaises(CHECKER.CheckerError) as raised:
            evaluate(capture)
        self.assertIn("credential material", str(raised.exception))

    def test_undeclared_raw_response_and_sensitive_field_are_rejected(self):
        capture = load_fixture()
        observation(capture, "direct_entity_read")["evidence"]["raw_response"] = {
            "attributes": {"friendly_name": "test"}
        }
        self.assertCaptureInvalid(capture, "not allowlisted")

        capture = load_fixture()
        observation(capture, "direct_entity_read")["evidence"]["api_token"] = "redacted"
        self.assertCaptureInvalid(capture, "sensitive field names")

    def assertCaptureInvalid(self, capture: dict, fragment: str) -> None:  # noqa: N802
        with self.assertRaises(CHECKER.CheckerError) as raised:
            evaluate(capture)
        self.assertIn(fragment, str(raised.exception))

    def test_reports_and_failure_diagnostics_remain_bounded(self):
        report = evaluate(load_fixture())
        self.assertLessEqual(
            len(CHECKER.render_text(report).encode("utf-8")), CHECKER.MAX_REPORT_BYTES
        )
        self.assertLessEqual(
            len(CHECKER.render_json(report).encode("utf-8")), CHECKER.MAX_REPORT_BYTES
        )
        self.assertNotIn("sensor-light.yaml" * 20, CHECKER.render_text(report))

    def test_manifest_template_and_fixture_serializations_are_deterministic(self):
        manifest = load_manifest()
        self.assertEqual(
            CHECKER.canonical_manifest_digest(manifest),
            CHECKER.canonical_manifest_digest(load_manifest()),
        )
        self.assertEqual(CHECKER.render_template(manifest), CHECKER.render_template(manifest))
        self.assertEqual(CHECKER.render_plan(manifest), CHECKER.render_plan(manifest))
        fixture = load_fixture()
        first = json.dumps(fixture, sort_keys=True, separators=(",", ":"))
        second = json.dumps(load_fixture(), sort_keys=True, separators=(",", ":"))
        self.assertEqual(first, second)


class OfflineCheckerBoundaryTests(unittest.TestCase):
    def test_checker_has_no_transport_subprocess_credentials_or_write_path(self):
        tree = ast.parse(CHECKER_PATH.read_text(encoding="utf-8"))
        forbidden_import_roots = {
            "aiohttp",
            "httpx",
            "mcp",
            "requests",
            "socket",
            "subprocess",
            "urllib",
            "websockets",
        }
        forbidden_calls = {
            "eval",
            "exec",
            "compile",
            "__import__",
            "open",
            "input",
            "write_text",
            "write_bytes",
            "unlink",
            "rename",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], forbidden_import_roots)
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn((node.module or "").split(".")[0], forbidden_import_roots)
            elif isinstance(node, ast.Call):
                name = None
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name and isinstance(node.func, ast.Name):
                    self.assertNotIn(name, forbidden_calls)
                if isinstance(node.func, ast.Attribute):
                    self.assertNotIn(name, forbidden_calls - {"compile"})

    def test_template_is_deliberately_incomplete_until_operator_attests(self):
        template = CHECKER.render_template(load_manifest())
        capture = json.loads(template)
        self.assertIn("REPLACE-WITH", capture["captured_at"])
        with self.assertRaises(CHECKER.CheckerError):
            evaluate(capture)

    def test_cli_exit_codes_distinguish_regression_and_incomplete(self):
        self.assertEqual(CHECKER.exit_code(evaluate(load_fixture())), CHECKER.EXIT_OK)

        regression = load_fixture()
        observation(regression, "server_identity")["evidence"]["data.server.version"] = "bad"
        self.assertEqual(CHECKER.exit_code(evaluate(regression)), CHECKER.EXIT_REGRESSION)

        incomplete = load_fixture()
        mark_not_captured(incomplete, "direct_entity_read")
        self.assertEqual(CHECKER.exit_code(evaluate(incomplete)), CHECKER.EXIT_INCOMPLETE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
