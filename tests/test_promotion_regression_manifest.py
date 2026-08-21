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

AUTHORITATIVE_DEFICIENCY_22 = {
    "minimum_permanent_sentinels": {
        "configuration-validation", "dependency-index-complete-coverage",
        "execution-task-schema-version", "f3-ready-locks-and-recovery",
        "f3-safety-health-invariants",
        "governance-approval-authority-version", "governance-historical-projection-health",
        "governance-plan-storage-healthy", "governance-task-storage-healthy",
        "held-read-remains-held", "held-read-top-level-not-found-taxonomy",
        "helper-provider-attribution", "home-assistant-connectivity",
        "home-assistant-version-agreement", "provider-routing-zero-fallback",
        "runtime-build-provenance", "runtime-server-version", "runtime-tool-accounting",
        "runtime-tool-accounting-agreement", "upstream-dashboard-zero-fallback",
        "upstream-ha-mcp-exact-admission", "upstream-read-gateway-zero-fallback",
    },
    "confirmed_regression_passes": {
        "automation-long-wait-template-readable", "dashboard-hyphenless-map-read",
        "historical-beta31-map-cleanup-succeeded", "historical-beta31-map-update-succeeded",
        "historical-long-template-execution-succeeded", "read-delegated-upstream",
        "read-direct-home-assistant", "read-engineering-native",
        "stale-state-canary-held-pre-dispatch",
    },
    "known_failures": {
        "dependency-index-complete-coverage": 1,
        "f3-ready-locks-and-recovery": 2,
        "governance-historical-projection-health": 4,
        "held-read-top-level-not-found-taxonomy": 19,
    },
    "promotion_dispositions": {
        "dependency-index-complete-coverage": "blocking",
        "f3-ready-locks-and-recovery": "blocking",
        "governance-historical-projection-health": "blocking",
        "held-read-top-level-not-found-taxonomy": "tracked_nonblocking",
    },
    "separately_authorized_canaries": {
        "helper-no-change-path": "executable",
        "beta39-jinja-helper-dependency-semantics": "unavailable_pending_reviewed_protocol",
        "approval-consumption-pre-dispatch-recovery": "unavailable_pending_reviewed_protocol",
        "cover-open-cover-composition-proof": "unavailable_pending_reviewed_protocol",
    },
    "required_canary_dispositions": {
        "helper-no-change-path": "blocking",
        "beta39-jinja-helper-dependency-semantics": "blocking",
        "approval-consumption-pre-dispatch-recovery": "blocking",
        "cover-open-cover-composition-proof": "blocking",
    },
    "jinja_family": [".get()", ".items()", ".keys()", ".values()", "literal bracket access"],
}

PRODUCT_AUTHORITY_BLOCKERS = {
    1: {
        "representation": "sentinel",
        "id": "dependency-index-complete-coverage",
        "related_items": [],
        "promotion_disposition": "blocking",
    },
    2: {
        "representation": "sentinel",
        "id": "f3-ready-locks-and-recovery",
        "related_items": [14],
        "promotion_disposition": "blocking",
    },
    3: {
        "representation": "required_canary",
        "id": "approval-consumption-pre-dispatch-recovery",
        "related_items": [],
        "promotion_disposition": "blocking",
    },
    4: {
        "representation": "sentinel",
        "id": "governance-historical-projection-health",
        "related_items": [],
        "promotion_disposition": "blocking",
    },
    5: {
        "representation": "required_canary",
        "id": "cover-open-cover-composition-proof",
        "related_items": [],
        "promotion_disposition": "blocking",
    },
}
PRODUCT_AUTHORITY_TRACKED = {
    19: {
        "representation": "sentinel",
        "id": "held-read-top-level-not-found-taxonomy",
        "related_items": [],
        "promotion_disposition": "tracked_nonblocking",
    }
}


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


def projection_case(observation_id: str) -> dict:
    return next(
        item
        for item in load_projection_fixture()["cases"]
        if item["observation"] == observation_id
    )


def set_projection(capture: dict, observation_id: str, projection: dict) -> None:
    observation(capture, observation_id)["evidence"].update(projection)


def evaluate(capture: dict, manifest: dict | None = None):
    return CHECKER.evaluate(manifest or load_manifest(), capture, load_schema())


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


def synthetic_f3_context() -> tuple[dict, dict, dict, dict]:
    """Create test-only exact F3 evidence without claiming live child identities."""
    manifest = copy.deepcopy(load_manifest())
    contract = next(
        item
        for item in manifest["projection_contracts"]
        if item["observation"] == "f3_orphan_task"
    )
    contract["evidence_availability"] = "available"
    contract.pop("unavailable_reason")
    case = projection_case("f3_orphan_task")
    current = CHECKER.derive_projection(
        manifest, "f3_orphan_task", copy.deepcopy(case["source"])
    )
    recovered_source = copy.deepcopy(case["source"])
    for child in recovered_source["data"]["f3_children"]:
        child.update({
            "state": "cancelled_pre_dispatch",
            "normalized_outcome": "cancelled_pre_dispatch",
        })
    recovered = CHECKER.derive_projection(
        manifest, "f3_orphan_task", recovered_source
    )
    sentinel = next(
        item
        for item in manifest["sentinels"]
        if item["id"] == "f3-ready-locks-and-recovery"
    )
    for checks, expected in (
        (sentinel["known_failure_checks"], current),
        (sentinel["desired_checks"], recovered),
    ):
        check = next(
            item
            for item in checks
            if item["path"] == "projection.f3_child_lifecycle_fingerprint"
        )
        check.update({"operator": "equals", "value": expected[check["path"]]})
    capture = load_fixture()
    entry = observation(capture, "f3_orphan_task")
    entry["status"] = "captured"
    entry.pop("not_recorded_reason")
    entry["evidence"] = {
        "data.task_id": "ab8d7cd12aad48aab8307ca819c794ca",
        "data.task_schema_version": 1,
        "data.state": "failed_pre_dispatch",
        "data.terminal_outcome": "failed_pre_dispatch",
        "data.provider_attempt_count": 0,
        "data.dispatched_at": None,
        "data.last_error.error_code": "pre_dispatch_authority_invalid",
        **current,
    }
    entry["absent_paths"] = []
    capture["manifest_digest"] = CHECKER.canonical_manifest_digest(manifest)
    return manifest, capture, case["source"], recovered_source


class ManifestStructureTests(unittest.TestCase):
    def test_product_authority_snapshot_covers_every_current_blocker_once(self):
        manifest = load_manifest()
        snapshot = manifest["product_authority_snapshot"]
        self.assertEqual(
            snapshot["source_document"],
            "ha_mcp_engineering_consolidated_deficiencies.md",
        )
        self.assertEqual(snapshot["source_date"], "2026-08-19")
        blockers = {
            item["register_item"]: {
                "representation": item["representation"],
                "id": item["id"],
                "related_items": item.get("related_register_items", []),
                "promotion_disposition": item["promotion_disposition"],
            }
            for item in snapshot["promotion_blockers"]
        }
        tracked = {
            item["register_item"]: {
                "representation": item["representation"],
                "id": item["id"],
                "related_items": item.get("related_register_items", []),
                "promotion_disposition": item["promotion_disposition"],
            }
            for item in snapshot["tracked_nonblocking"]
        }
        self.assertEqual(blockers, PRODUCT_AUTHORITY_BLOCKERS)
        self.assertEqual(tracked, PRODUCT_AUTHORITY_TRACKED)
        self.assertNotEqual(blockers[3]["id"], blockers[2]["id"])

    def test_product_authority_inventory_rejects_missing_unknown_duplicate_or_conflict(self):
        manifest, schema = load_manifest(), load_schema()
        mutations = (
            lambda value: value["product_authority_snapshot"][
                "promotion_blockers"
            ].pop(),
            lambda value: value["product_authority_snapshot"][
                "promotion_blockers"
            ][0].__setitem__("register_item", 99),
            lambda value: value["product_authority_snapshot"][
                "promotion_blockers"
            ].append(
                copy.deepcopy(
                    value["product_authority_snapshot"]["promotion_blockers"][0]
                )
            ),
            lambda value: value["product_authority_snapshot"][
                "promotion_blockers"
            ][0].__setitem__("promotion_disposition", "tracked_nonblocking"),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                changed = copy.deepcopy(manifest)
                mutate(changed)
                self.assertTrue(CHECKER.validate_manifest(changed, schema))

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
        self.assertEqual(
            set(contract["minimum_permanent_sentinels"]),
            AUTHORITATIVE_DEFICIENCY_22["minimum_permanent_sentinels"],
        )
        self.assertEqual(
            set(contract["confirmed_regression_passes"]),
            AUTHORITATIVE_DEFICIENCY_22["confirmed_regression_passes"],
        )
        self.assertEqual(contract["known_failures"], AUTHORITATIVE_DEFICIENCY_22["known_failures"])
        self.assertEqual(
            contract["promotion_dispositions"],
            AUTHORITATIVE_DEFICIENCY_22["promotion_dispositions"],
        )
        self.assertEqual(
            contract["separately_authorized_canaries"],
            AUTHORITATIVE_DEFICIENCY_22["separately_authorized_canaries"],
        )
        self.assertEqual(
            contract["required_canary_dispositions"],
            AUTHORITATIVE_DEFICIENCY_22["required_canary_dispositions"],
        )
        self.assertEqual(contract["source"], "ha_mcp_engineering_consolidated_deficiencies.md")
        self.assertEqual(contract["source_date"], "2026-08-19")
        self.assertEqual(
            contract["source_sha256"],
            "ff17d026a98ad4f55c8ddc8c3f6131ebbb59bccce6920e1372211c9f9f458ca1",
        )
        self.assertEqual(
            {
                item["register_item"]: {
                    "representation": item["representation"],
                    "id": item["id"],
                    "related_items": item.get("related_register_items", []),
                    "promotion_disposition": item["promotion_disposition"],
                }
                for item in contract["promotion_blockers"]
            },
            PRODUCT_AUTHORITY_BLOCKERS,
        )
        self.assertEqual(
            {
                item["register_item"]: {
                    "representation": item["representation"],
                    "id": item["id"],
                    "related_items": item.get("related_register_items", []),
                    "promotion_disposition": item["promotion_disposition"],
                }
                for item in contract["tracked_nonblocking"]
            },
            PRODUCT_AUTHORITY_TRACKED,
        )
        self.assertEqual(
            contract["required_regression_families"]["beta39_jinja_helper_dependency_semantics"],
            AUTHORITATIVE_DEFICIENCY_22["jinja_family"],
        )
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
        self.assertEqual(
            {
                identifier: item["promotion_disposition"]
                for identifier, item in sentinels.items()
                if item["expected_status"] == "expected_fail"
            },
            contract["promotion_dispositions"],
        )
        self.assertNotIn(3, actual.values())
        canaries = {
            item["id"]: item
            for item in load_manifest()["separately_authorized_canaries"]
        }
        for register_item in (3, 5):
            requirement = PRODUCT_AUTHORITY_BLOCKERS[register_item]
            canary = canaries[requirement["id"]]
            self.assertEqual(canary["deficiency"]["register_item"], register_item)
            self.assertEqual(canary["promotion_disposition"], CHECKER.BLOCKING)
            self.assertEqual(
                canary["availability"], "unavailable_pending_reviewed_protocol"
            )
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
                self.assertIn(
                    item["promotion_disposition"],
                    {CHECKER.BLOCKING, CHECKER.TRACKED_NONBLOCKING},
                )
            else:
                self.assertNotIn("known_failure_checks", item)
                self.assertNotIn("promotion_disposition", item)

    def test_promotion_disposition_schema_is_closed_and_conditional(self):
        schema = load_schema()
        manifest = load_manifest()
        failing = next(
            item for item in manifest["sentinels"]
            if item["expected_status"] == "expected_fail"
        )
        passing = next(
            item for item in manifest["sentinels"]
            if item["expected_status"] == "expected_pass"
        )

        missing = copy.deepcopy(manifest)
        next(
            item for item in missing["sentinels"] if item["id"] == failing["id"]
        ).pop("promotion_disposition")
        self.assertTrue(CHECKER.validate_manifest(missing, schema))

        invalid = copy.deepcopy(manifest)
        next(
            item for item in invalid["sentinels"] if item["id"] == failing["id"]
        )["promotion_disposition"] = "informational"
        self.assertTrue(CHECKER.validate_manifest(invalid, schema))

        forbidden = copy.deepcopy(manifest)
        next(
            item for item in forbidden["sentinels"] if item["id"] == passing["id"]
        )["promotion_disposition"] = "blocking"
        self.assertTrue(CHECKER.validate_manifest(forbidden, schema))

        canary = manifest["separately_authorized_canaries"][0]
        missing_canary = copy.deepcopy(manifest)
        next(
            item
            for item in missing_canary["separately_authorized_canaries"]
            if item["id"] == canary["id"]
        ).pop("promotion_disposition")
        self.assertTrue(CHECKER.validate_manifest(missing_canary, schema))

        invalid_canary = copy.deepcopy(manifest)
        next(
            item
            for item in invalid_canary["separately_authorized_canaries"]
            if item["id"] == canary["id"]
        )["promotion_disposition"] = "informational"
        self.assertTrue(CHECKER.validate_manifest(invalid_canary, schema))

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
            {key: value["availability"] for key, value in canaries.items()},
            load_contract()["separately_authorized_canaries"],
        )
        self.assertEqual(
            {key: value["promotion_disposition"] for key, value in canaries.items()},
            load_contract()["required_canary_dispositions"],
        )
        helper = canaries["helper-no-change-path"]
        self.assertEqual(helper["tool"], "create_helper_state_plan")
        self.assertIs(helper["included_in_default_plan"], False)
        self.assertIs(helper["separate_authorization_required"], True)
        self.assertNotIn("create_helper_state_plan", CHECKER.render_plan(load_manifest()))
        template = json.loads(CHECKER.render_template(load_manifest()))
        self.assertNotIn("create_helper_state_plan", {
            item["tool"] for item in template["observations"].values()
        })
        self.assertEqual(
            load_contract()["required_regression_families"][
                "beta39_jinja_helper_dependency_semantics"
            ],
            [".get()", ".items()", ".keys()", ".values()", "literal bracket access"],
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
    def test_missing_deficiency_3_or_5_canary_independently_blocks(self):
        manifest, schema = load_manifest(), load_schema()
        for canary_id in (
            "approval-consumption-pre-dispatch-recovery",
            "cover-open-cover-composition-proof",
        ):
            with self.subTest(canary=canary_id):
                capture = load_fixture()
                for other in manifest["separately_authorized_canaries"]:
                    entry = capture["canaries"][other["id"]]
                    if other["id"] == canary_id:
                        self.assertEqual(entry["status"], "not_captured")
                        continue
                    if other["availability"] != "executable":
                        continue
                    entry["status"] = "captured"
                    entry.pop("not_recorded_reason", None)
                    entry["arguments"].update({
                        key: (
                            "off"
                            if key == "desired_state"
                            else "sha256:" + "1" * 64
                        )
                        for key in other["operator_arguments"]
                    })
                    entry["evidence"] = {
                        check["path"]: check.get("value")
                        for check in other.get("desired_checks", [])
                        if check["operator"] == "equals"
                    }
                    entry["absent_paths"] = []
                report = CHECKER.evaluate(manifest, capture, schema)
                result = result_for(report, "canary:" + canary_id)
                self.assertEqual(result.outcome, CHECKER.NOT_CAPTURED)
                self.assertEqual(result.promotion_disposition, CHECKER.BLOCKING)
                self.assertFalse(report.promotion_eligible)
                self.assertEqual(CHECKER.exit_code(report), CHECKER.EXIT_INCOMPLETE)
                isolated = CHECKER.Report(
                    {},
                    {},
                    [
                        CHECKER.SentinelResult(
                            sentinel_id="confirmed",
                            title="otherwise passing",
                            outcome=CHECKER.CONFIRMED,
                            expected_status="expected_pass",
                            observation="test",
                        ),
                        copy.deepcopy(result),
                    ],
                )
                self.assertFalse(isolated.promotion_eligible)
                self.assertEqual(
                    isolated.blocking_unverified_requirement_count, 1
                )
                self.assertEqual(
                    json.loads(CHECKER.render_json(isolated))[
                        "blocking_unverified_requirement_ids"
                    ],
                    ["canary:" + canary_id],
                )

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
                CHECKER.NOT_CAPTURED: 5,
                CHECKER.UNEXPECTED_PASS: 0,
                CHECKER.KNOWN_FAILING: 3,
                CHECKER.CONFIRMED: 27,
            },
        )
        self.assertFalse(report.evidence_complete)
        self.assertFalse(report.promotion_eligible)
        self.assertTrue(report.blocking_known_failure_present)
        self.assertEqual(report.blocking_known_failure_count, 2)
        self.assertEqual(report.blocking_unverified_requirement_count, 4)
        self.assertEqual(
            {item.sentinel_id for item in report.blocking_unverified_requirements},
            {
                "f3-ready-locks-and-recovery",
                "canary:beta39-jinja-helper-dependency-semantics",
                "canary:approval-consumption-pre-dispatch-recovery",
                "canary:cover-open-cover-composition-proof",
            },
        )
        self.assertIn(
            "child execution IDs",
            result_for(report, "f3-ready-locks-and-recovery").note,
        )
        self.assertEqual(
            {
                item.sentinel_id
                for item in report.by_outcome(CHECKER.KNOWN_FAILING)
            },
            set(load_contract()["known_failures"]) - {"f3-ready-locks-and-recovery"},
        )
        rendered = json.loads(CHECKER.render_json(report))
        by_id = {item["id"]: item for item in rendered["sentinels"]}
        for sentinel_id, register_item in (
            ("canary:approval-consumption-pre-dispatch-recovery", 3),
            ("canary:cover-open-cover-composition-proof", 5),
        ):
            item = by_id[sentinel_id]
            self.assertEqual(item["outcome"], CHECKER.NOT_CAPTURED)
            self.assertEqual(item["promotion_disposition"], CHECKER.BLOCKING)
            self.assertEqual(item["availability"], "unavailable_pending_reviewed_protocol")
            self.assertEqual(item["deficiency"]["register_item"], register_item)
            self.assertTrue(item["independently_prevents_promotion"])
            self.assertTrue(item["note"])
        text = CHECKER.render_text(report)
        self.assertIn("BLOCKING_UNVERIFIED", text)
        self.assertIn("deficiency #3", text)
        self.assertIn("deficiency #5", text)

    def test_known_failure_disposition_is_separate_from_classification(self):
        blocking = CHECKER.SentinelResult(
            sentinel_id="blocking-deficiency",
            title="blocking",
            outcome=CHECKER.KNOWN_FAILING,
            expected_status="expected_fail",
            observation="test",
            promotion_disposition=CHECKER.BLOCKING,
        )
        report = CHECKER.Report(
            manifest_target={},
            capture_metadata={},
            results=[
                CHECKER.SentinelResult(
                    sentinel_id="confirmed",
                    title="confirmed",
                    outcome=CHECKER.CONFIRMED,
                    expected_status="expected_pass",
                    observation="test",
                ),
                blocking,
            ],
        )
        self.assertEqual(blocking.outcome, CHECKER.KNOWN_FAILING)
        self.assertTrue(report.evidence_complete)
        self.assertTrue(report.blocking_known_failure_present)
        self.assertEqual(report.blocking_known_failure_count, 1)
        self.assertFalse(report.regression_present)
        self.assertFalse(report.promotion_eligible)
        self.assertEqual(CHECKER.exit_code(report), CHECKER.EXIT_INCOMPLETE)
        rendered = json.loads(CHECKER.render_json(report))
        self.assertEqual(rendered["blocking_known_failure_ids"], ["blocking-deficiency"])
        self.assertTrue(rendered["promotion_blocked"])
        self.assertIn("known promotion blockers remain", CHECKER.render_text(report))

        tracked = copy.deepcopy(blocking)
        tracked.sentinel_id = "deficiency-19"
        tracked.promotion_disposition = CHECKER.TRACKED_NONBLOCKING
        report = CHECKER.Report({}, {}, [tracked])
        self.assertFalse(report.blocking_known_failure_present)
        self.assertTrue(report.promotion_eligible)
        self.assertEqual(CHECKER.exit_code(report), CHECKER.EXIT_OK)

    def test_each_authoritative_known_failure_has_its_independent_policy(self):
        production = evaluate(load_fixture())
        synthetic_manifest, synthetic_capture, _, _ = synthetic_f3_context()
        synthetic = evaluate(synthetic_capture, synthetic_manifest)
        cases = (
            (production, "dependency-index-complete-coverage", CHECKER.BLOCKING),
            (synthetic, "f3-ready-locks-and-recovery", CHECKER.BLOCKING),
            (production, "governance-historical-projection-health", CHECKER.BLOCKING),
            (production, "held-read-top-level-not-found-taxonomy", CHECKER.TRACKED_NONBLOCKING),
        )
        for source_report, sentinel_id, disposition in cases:
            with self.subTest(sentinel=sentinel_id):
                result = copy.deepcopy(result_for(source_report, sentinel_id))
                self.assertEqual(result.outcome, CHECKER.KNOWN_FAILING)
                self.assertEqual(result.promotion_disposition, disposition)
                isolated = CHECKER.Report({}, {}, [result])
                self.assertEqual(
                    isolated.promotion_eligible,
                    disposition == CHECKER.TRACKED_NONBLOCKING,
                )

    def test_materially_worse_known_failure_is_a_regression(self):
        capture = load_fixture()
        evidence = observation(capture, "native_dependency_read")["evidence"]
        evidence["data.source_coverage[source_type=blueprint].failed_item_count"] = 3
        evidence[
            "data.source_coverage[source_type=blueprint].obligation_ledger_failed_item_count"
        ] = 3
        evidence["projection.observable_coverage_summary"] = (
            "automation=complete/failed:0/ledger_failed:0/fallback:false; "
            "blueprint=partial/failed:3/ledger_failed:3/fallback:false"
        )
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
            "data.source_coverage[source_type=blueprint].obligation_ledger_completeness"
        ] = "complete"
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
            "data.source_coverage[source_type=blueprint].obligation_ledger_completeness"
        ] = "complete"
        evidence[
            "data.source_coverage[source_type=blueprint].obligation_ledger_failed_item_count"
        ] = 0
        evidence.pop("projection.observable_coverage_summary")
        evidence.pop("projection.observable_evidence_fingerprint")
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
                if observation_id == "f3_orphan_task":
                    manifest, capture, _, _ = synthetic_f3_context()
                else:
                    manifest, capture = load_manifest(), load_fixture()
                observation(capture, observation_id)["evidence"][path] = value
                self.assertEqual(
                    result_for(evaluate(capture, manifest), sentinel_id).outcome,
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
                if observation_id == "f3_orphan_task":
                    manifest, capture, _, _ = synthetic_f3_context()
                else:
                    manifest, capture = load_manifest(), load_fixture()
                observation(capture, observation_id)["evidence"].pop(path)
                self.assertEqual(
                    result_for(evaluate(capture, manifest), sentinel_id).outcome,
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

    def test_synthetic_f3_identity_cannot_be_claimed_as_live_capture(self):
        capture = load_fixture()
        entry = observation(capture, "f3_orphan_task")
        entry["status"] = "captured"
        entry.pop("not_recorded_reason")
        entry["evidence"] = {
            "data.task_id": "ab8d7cd12aad48aab8307ca819c794ca",
            "data.task_schema_version": 1,
            "data.state": "failed_pre_dispatch",
            "data.terminal_outcome": "failed_pre_dispatch",
            "data.provider_attempt_count": 0,
            "data.dispatched_at": None,
            "data.last_error.error_code": "pre_dispatch_authority_invalid",
            **projection_case("f3_orphan_task")["expected"],
        }
        entry["absent_paths"] = []
        self.assertCaptureInvalid(capture, "authoritative projection identity is unavailable")

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

    def test_changed_observable_dependency_signature_is_a_regression(self):
        capture = load_fixture()
        evidence = observation(capture, "native_dependency_read")["evidence"]
        evidence["projection.observable_coverage_summary"] = "different observable coverage"
        evidence["projection.observable_evidence_fingerprint"] = "sha256:" + "1" * 64
        self.assertEqual(
            result_for(evaluate(capture), "dependency-index-complete-coverage").outcome,
            CHECKER.REGRESSION,
        )

    def test_historical_orphan_does_not_absorb_new_f3_failures(self):
        cases = (
            ("data.governance.f3.active_normal_lock_count", 1),
            ("data.governance.f3.corrupt_record_count", 1),
            ("data.governance.f3.recovery_coordinator_status", "recovering"),
            ("data.governance.f3.recovery_failures", 1),
            ("data.governance.f3.fallback_count", 1),
        )
        for path, value in cases:
            with self.subTest(path=path):
                capture = load_fixture()
                observation(capture, "server_health")["evidence"][path] = value
                self.assertEqual(
                    result_for(
                        evaluate(capture), "f3-safety-health-invariants"
                    ).outcome,
                    CHECKER.REGRESSION,
                )
                self.assertEqual(
                    result_for(
                        evaluate(capture), "f3-ready-locks-and-recovery"
                    ).outcome,
                    CHECKER.NOT_CAPTURED,
                )

    def test_live_f3_is_not_captured_without_authoritative_child_ids(self):
        result = result_for(evaluate(load_fixture()), "f3-ready-locks-and-recovery")
        self.assertEqual(result.outcome, CHECKER.NOT_CAPTURED)
        self.assertEqual(result.promotion_disposition, CHECKER.BLOCKING)
        self.assertEqual(
            result.availability, "not_captured"
        )

    def test_synthetic_exact_f3_orphan_lifecycle_is_known_failing(self):
        manifest, capture, _, _ = synthetic_f3_context()
        result = result_for(
            evaluate(capture, manifest), "f3-ready-locks-and-recovery"
        )
        self.assertEqual(result.outcome, CHECKER.KNOWN_FAILING)
        self.assertEqual(result.promotion_disposition, CHECKER.BLOCKING)

    def test_f3_parent_mutations_are_regressions(self):
        cases = (
            ("data.state", "succeeded_verified"),
            ("data.terminal_outcome", "succeeded_verified"),
            ("data.provider_attempt_count", 1),
            ("data.dispatched_at", "2026-08-19T12:00:01Z"),
            ("data.last_error.error_code", "different_failure"),
        )
        for path, value in cases:
            with self.subTest(path=path):
                manifest, capture, _, _ = synthetic_f3_context()
                observation(capture, "f3_orphan_task")["evidence"][path] = value
                if path == "data.state":
                    observation(capture, "execution_task_inventory")["evidence"][
                        "data.tasks[task_id=ab8d7cd12aad48aab8307ca819c794ca].state"
                    ] = value
                self.assertEqual(
                    result_for(
                        evaluate(capture, manifest), "f3-ready-locks-and-recovery"
                    ).outcome,
                    CHECKER.REGRESSION,
                )

    def test_f3_child_identity_lifecycle_and_dispatch_mutations_regress(self):
        manifest, base_capture, source, _ = synthetic_f3_context()
        mutations = (
            lambda children: children[0].__setitem__(
                "child_execution_id", "3" * 64
            ),
            lambda children: children[0].__setitem__("operation_id", "different_operation"),
            lambda children: (
                children[0].__setitem__("ordinal", 1),
                children[1].__setitem__("ordinal", 0),
            ),
            lambda children: children[0].__setitem__("state", "created"),
            lambda children: children[0].__setitem__(
                "normalized_outcome", "failed_pre_dispatch"
            ),
            lambda children: children[0].__setitem__("dispatch_count", 1),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                changed_source = copy.deepcopy(source)
                mutate(changed_source["data"]["f3_children"])
                capture = copy.deepcopy(base_capture)
                set_projection(
                    capture,
                    "f3_orphan_task",
                    CHECKER.derive_projection(
                        manifest, "f3_orphan_task", changed_source
                    ),
                )
                self.assertEqual(
                    result_for(
                        evaluate(capture, manifest), "f3-ready-locks-and-recovery"
                    ).outcome,
                    CHECKER.REGRESSION,
                )

    def test_f3_child_count_and_complete_recovery_are_exact(self):
        manifest, base_capture, source, recovered = synthetic_f3_context()

        removed = copy.deepcopy(source)
        removed["data"]["f3_children"].pop()
        with self.assertRaises(CHECKER.CheckerError):
            CHECKER.derive_projection(manifest, "f3_orphan_task", removed)

        added = copy.deepcopy(source)
        added["data"]["f3_children"].append({
            "child_execution_id": "3" * 64,
            "operation_id": "unexpected_operation",
            "ordinal": 2,
            "state": "not_started",
            "normalized_outcome": None,
            "dispatch_count": 0,
        })
        with self.assertRaises(CHECKER.CheckerError):
            CHECKER.derive_projection(manifest, "f3_orphan_task", added)

        one_terminal = copy.deepcopy(source)
        one_terminal["data"]["f3_children"][0].update({
            "state": "cancelled_pre_dispatch",
            "normalized_outcome": "cancelled_pre_dispatch",
        })
        capture = copy.deepcopy(base_capture)
        set_projection(
            capture,
            "f3_orphan_task",
            CHECKER.derive_projection(manifest, "f3_orphan_task", one_terminal),
        )
        self.assertEqual(
            result_for(
                evaluate(capture, manifest), "f3-ready-locks-and-recovery"
            ).outcome,
            CHECKER.REGRESSION,
        )

        capture = copy.deepcopy(base_capture)
        set_projection(
            capture,
            "f3_orphan_task",
            CHECKER.derive_projection(manifest, "f3_orphan_task", recovered),
        )
        health = observation(capture, "server_health")["evidence"]
        health["data.governance.f3.status"] = "ready"
        health["data.governance.f3.nonterminal_execution_count"] = 0
        result = result_for(
            evaluate(capture, manifest), "f3-ready-locks-and-recovery"
        )
        self.assertEqual(result.outcome, CHECKER.UNEXPECTED_PASS)

    def test_f3_child_order_is_canonical_but_identity_is_material(self):
        manifest, _, source, recovered = synthetic_f3_context()
        self.assertEqual(
            [item["child_execution_id"] for item in source["data"]["f3_children"]],
            [
                item["child_execution_id"]
                for item in recovered["data"]["f3_children"]
            ],
        )
        baseline = CHECKER.derive_projection(manifest, "f3_orphan_task", source)
        reordered = copy.deepcopy(source)
        reordered["data"]["f3_children"].reverse()
        self.assertEqual(
            CHECKER.derive_projection(manifest, "f3_orphan_task", reordered),
            baseline,
        )
        changed = copy.deepcopy(source)
        changed["data"]["f3_children"][0]["child_execution_id"] = "3" * 64
        self.assertNotEqual(
            CHECKER.derive_projection(manifest, "f3_orphan_task", changed)[
                "projection.f3_child_lifecycle_fingerprint"
            ],
            baseline["projection.f3_child_lifecycle_fingerprint"],
        )

    def test_f3_projection_rejects_incomplete_or_malformed_child_lists(self):
        case = projection_case("f3_orphan_task")
        for mutate in (
            lambda value: value["data"].pop("f3_children"),
            lambda value: value["data"].__setitem__(
                "task_id", "0" * 32
            ),
            lambda value: value["data"].__setitem__("f3_children", []),
            lambda value: value["data"].__setitem__(
                "f3_children", value["data"]["f3_children"] * 5
            ),
            lambda value: value["data"]["f3_children"][0].pop("dispatch_count"),
            lambda value: value["data"]["f3_children"][0].__setitem__("state", "unknown"),
            lambda value: value["data"]["f3_children"][1].__setitem__("ordinal", 0),
        ):
            source = copy.deepcopy(case["source"])
            mutate(source)
            with self.assertRaises(CHECKER.CheckerError):
                CHECKER.derive_projection(load_manifest(), "f3_orphan_task", source)

    def test_f3_projection_requires_success_and_exact_unique_child_ids(self):
        case = projection_case("f3_orphan_task")
        source_with_ids = copy.deepcopy(case["source"])
        for index, child in enumerate(source_with_ids["data"]["f3_children"], start=1):
            child["child_execution_id"] = str(index) * 64
        mutations = (
            lambda value: value.pop("success"),
            lambda value: value.__setitem__("success", False),
            lambda value: value["data"]["f3_children"][0].pop("child_execution_id"),
            lambda value: value["data"]["f3_children"][0].__setitem__(
                "child_execution_id", "not-a-child-id"
            ),
            lambda value: value["data"]["f3_children"][1].__setitem__(
                "child_execution_id",
                value["data"]["f3_children"][0].get("child_execution_id", "1" * 64),
            ),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                source = copy.deepcopy(source_with_ids)
                mutate(source)
                with self.assertRaises(CHECKER.CheckerError):
                    CHECKER.derive_projection(
                        load_manifest(), "f3_orphan_task", source
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
        changed_dependency["data"]["source_coverage"][0]["failed_item_count"] = 3
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
        unrelated_wait = copy.deepcopy(wait["source"])
        unrelated_wait["variables"]["wait_template"] = "changed unrelated value"
        self.assertEqual(
            CHECKER.derive_projection(manifest, wait["observation"], unrelated_wait),
            wait["expected"],
        )
        moved_wait = copy.deepcopy(wait["source"])
        moved_wait["action"].insert(0, {"delay": "00:00:02"})
        with self.assertRaises(CHECKER.CheckerError):
            CHECKER.derive_projection(manifest, wait["observation"], moved_wait)

    def test_projection_sources_are_bounded_and_sanitized(self):
        manifest = load_manifest()
        source = copy.deepcopy(load_projection_fixture()["cases"][0]["source"])
        source["warnings"] = ["x" * (CHECKER.MAX_VALUE_BYTES + 1)]
        with self.assertRaises(CHECKER.CheckerError) as raised:
            CHECKER.derive_projection(manifest, "native_dependency_read", source)
        self.assertIn("string exceeds", str(raised.exception))

        source["warnings"] = ["Bearer abcdefghijklmnopqrstuvwxyz"]
        with self.assertRaises(CHECKER.CheckerError) as raised:
            CHECKER.derive_projection(manifest, "native_dependency_read", source)
        self.assertIn("credential material", str(raised.exception))

    def test_dependency_projection_is_public_shaped_canonical_and_unambiguous(self):
        manifest = load_manifest()
        source = copy.deepcopy(load_projection_fixture()["cases"][0]["source"])
        expected = CHECKER.derive_projection(manifest, "native_dependency_read", source)
        reordered = json.loads(json.dumps(source, sort_keys=True))
        reordered["warnings"] = ["different nonmaterial presentation"]
        reordered["metadata"]["partial"] = False
        self.assertEqual(
            CHECKER.derive_projection(manifest, "native_dependency_read", reordered),
            expected,
        )
        missing = copy.deepcopy(source)
        missing["data"]["overview"].pop("unique_source_count")
        with self.assertRaises(CHECKER.CheckerError):
            CHECKER.derive_projection(manifest, "native_dependency_read", missing)
        duplicate = copy.deepcopy(source)
        duplicate["data"]["source_coverage"].append(
            copy.deepcopy(duplicate["data"]["source_coverage"][0])
        )
        with self.assertRaises(CHECKER.CheckerError):
            CHECKER.derive_projection(manifest, "native_dependency_read", duplicate)
        self.assertNotEqual(
            CHECKER._canonical_fingerprint(
                "collision-test", {"identity": "A:B", "reason": "C"}
            ),
            CHECKER._canonical_fingerprint(
                "collision-test", {"identity": "A", "reason": "B:C"}
            ),
        )

    def test_dependency_projection_binds_each_obligation_ledger_contract(self):
        case = projection_case("native_dependency_read")
        baseline = CHECKER.derive_projection(
            load_manifest(), case["observation"], copy.deepcopy(case["source"])
        )
        for source_type in ("automation", "blueprint"):
            with self.subTest(source_type=source_type):
                changed = copy.deepcopy(case["source"])
                coverage = next(
                    item for item in changed["data"]["source_coverage"]
                    if item["source_type"] == source_type
                )
                coverage["obligation_ledger_completeness"] = (
                    "partial"
                    if coverage["obligation_ledger_completeness"] == "complete"
                    else "complete"
                )
                projected = CHECKER.derive_projection(
                    load_manifest(), case["observation"], changed
                )
                self.assertNotEqual(
                    projected["projection.observable_evidence_fingerprint"],
                    baseline["projection.observable_evidence_fingerprint"],
                )

        for field in (
            "obligation_ledger_completeness",
            "obligation_ledger_failed_item_count",
        ):
            missing = copy.deepcopy(case["source"])
            missing["data"]["source_coverage"][0].pop(field)
            with self.assertRaises(CHECKER.CheckerError):
                CHECKER.derive_projection(load_manifest(), case["observation"], missing)

        unsupported = copy.deepcopy(case["source"])
        unsupported["data"]["source_coverage"][0][
            "obligation_ledger_completeness"
        ] = "unknown"
        with self.assertRaises(CHECKER.CheckerError):
            CHECKER.derive_projection(load_manifest(), case["observation"], unsupported)

    def test_legacy_and_ledger_failure_counts_are_independent(self):
        cases = (
            ("automation", "failed_item_count", 1),
            ("automation", "obligation_ledger_failed_item_count", 1),
            ("blueprint", "failed_item_count", 3),
            ("blueprint", "obligation_ledger_failed_item_count", 3),
        )
        projection = projection_case("native_dependency_read")
        for source_type, field, value in cases:
            with self.subTest(source_type=source_type, field=field):
                source = copy.deepcopy(projection["source"])
                next(
                    item for item in source["data"]["source_coverage"]
                    if item["source_type"] == source_type
                )[field] = value
                capture = load_fixture()
                path = f"data.source_coverage[source_type={source_type}].{field}"
                observation(capture, "native_dependency_read")["evidence"][path] = value
                set_projection(
                    capture,
                    "native_dependency_read",
                    CHECKER.derive_projection(
                        load_manifest(), "native_dependency_read", source
                    ),
                )
                self.assertEqual(
                    result_for(
                        evaluate(capture), "dependency-index-complete-coverage"
                    ).outcome,
                    CHECKER.REGRESSION,
                )

    def test_partial_ledger_with_zero_failures_is_not_complete(self):
        case = projection_case("native_dependency_read")
        source = copy.deepcopy(case["source"])
        automation = next(
            item for item in source["data"]["source_coverage"]
            if item["source_type"] == "automation"
        )
        automation["obligation_ledger_completeness"] = "partial"
        automation["obligation_ledger_failed_item_count"] = 0
        capture = load_fixture()
        evidence = observation(capture, "native_dependency_read")["evidence"]
        evidence[
            "data.source_coverage[source_type=automation].obligation_ledger_completeness"
        ] = "partial"
        set_projection(
            capture,
            "native_dependency_read",
            CHECKER.derive_projection(load_manifest(), "native_dependency_read", source),
        )
        self.assertEqual(
            result_for(evaluate(capture), "dependency-index-complete-coverage").outcome,
            CHECKER.REGRESSION,
        )

    def test_wait_projection_uses_only_declared_action_pointer(self):
        manifest = load_manifest()
        case = projection_case("long_wait_template_automation_read")
        expected = case["expected"]
        source = copy.deepcopy(case["source"])
        source["variables"]["nested"] = {
            "wait_template": "spoofed unrelated mapping"
        }
        self.assertEqual(
            CHECKER.derive_projection(manifest, case["observation"], source),
            expected,
        )
        for mutate in (
            lambda value: value["action"].pop(2),
            lambda value: value.__setitem__("action", {"2": value["action"][2]}),
            lambda value: value["action"][2].__setitem__("wait_template", 42),
            lambda value: value["action"].append({"wait_template": "duplicate"}),
            lambda value: value["action"][2].__setitem__(
                "wait_template", "x" * (CHECKER.MAX_WAIT_TEMPLATE_BYTES + 1)
            ),
        ):
            malformed = copy.deepcopy(case["source"])
            mutate(malformed)
            with self.assertRaises(CHECKER.CheckerError):
                CHECKER.derive_projection(manifest, case["observation"], malformed)

        malformed_manifest = copy.deepcopy(manifest)
        contract = next(
            item
            for item in malformed_manifest["projection_contracts"]
            if item["observation"] == case["observation"]
        )
        contract["expected_action_pointer"] = "/variables/wait_template"
        self.assertTrue(CHECKER.validate_manifest(malformed_manifest, load_schema()))
        with self.assertRaises(CHECKER.CheckerError):
            CHECKER.derive_projection(
                malformed_manifest, case["observation"], copy.deepcopy(case["source"])
            )

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
        self.assertEqual(CHECKER.exit_code(evaluate(load_fixture())), CHECKER.EXIT_INCOMPLETE)

        regression = load_fixture()
        observation(regression, "server_identity")["evidence"]["data.server.version"] = "bad"
        self.assertEqual(CHECKER.exit_code(evaluate(regression)), CHECKER.EXIT_REGRESSION)

        incomplete = load_fixture()
        mark_not_captured(incomplete, "direct_entity_read")
        self.assertEqual(CHECKER.exit_code(evaluate(incomplete)), CHECKER.EXIT_INCOMPLETE)

    def test_decision_fields_are_noncontradictory(self):
        def report(*outcomes):
            return CHECKER.Report(
                manifest_target={},
                capture_metadata={},
                results=[
                    CHECKER.SentinelResult(
                        sentinel_id=str(index), title="test", outcome=outcome,
                        expected_status="expected_pass", observation="test"
                    )
                    for index, outcome in enumerate(outcomes)
                ],
            )

        cases = (
            ((CHECKER.CONFIRMED,), (False, True, False, True, CHECKER.EXIT_OK)),
            ((CHECKER.CONFIRMED, CHECKER.KNOWN_FAILING), (False, True, False, True, CHECKER.EXIT_OK)),
            ((CHECKER.REGRESSION,), (True, True, False, False, CHECKER.EXIT_REGRESSION)),
            ((CHECKER.NOT_CAPTURED,), (False, False, False, False, CHECKER.EXIT_INCOMPLETE)),
            ((CHECKER.UNEXPECTED_PASS,), (False, True, True, False, CHECKER.EXIT_INCOMPLETE)),
            ((CHECKER.REGRESSION, CHECKER.NOT_CAPTURED, CHECKER.UNEXPECTED_PASS), (True, False, True, False, CHECKER.EXIT_REGRESSION)),
        )
        for outcomes, expected in cases:
            value = report(*outcomes)
            observed = (
                value.regression_present, value.evidence_complete,
                value.review_required, value.promotion_eligible,
                CHECKER.exit_code(value),
            )
            self.assertEqual(observed, expected)
            rendered = json.loads(CHECKER.render_json(value))
            self.assertEqual(rendered["promotion_eligible"], expected[3])
            self.assertEqual(rendered["promotion_blocked"], not expected[3])
            text = CHECKER.render_text(value)
            if expected[3]:
                self.assertIn("promotion eligible", text)
            else:
                self.assertIn("promotion is not eligible", text.lower())

    def test_missing_required_separate_canary_is_never_promotion_eligible(self):
        report = evaluate(load_fixture())
        self.assertEqual(
            result_for(
                report, "canary:beta39-jinja-helper-dependency-semantics"
            ).outcome,
            CHECKER.NOT_CAPTURED,
        )
        self.assertFalse(report.promotion_eligible)
        self.assertEqual(CHECKER.exit_code(report), CHECKER.EXIT_INCOMPLETE)
        rendered = json.loads(CHECKER.render_json(report))
        self.assertFalse(rendered["evidence_complete"])
        self.assertFalse(rendered["promotion_eligible"])
        self.assertTrue(rendered["promotion_blocked"])

        capture = load_fixture()
        unavailable = capture["canaries"]["beta39-jinja-helper-dependency-semantics"]
        unavailable["status"] = "captured"
        unavailable.pop("not_recorded_reason")
        unavailable["arguments"].update({
            "entity_id": "input_boolean.synthetic_jinja_canary",
            "desired_state": "off",
            "fixture_contract_digest": "sha256:" + "1" * 64,
        })
        unavailable["evidence"] = {}
        unavailable["absent_paths"] = []
        with self.assertRaises(CHECKER.CheckerError) as raised:
            evaluate(capture)
        self.assertIn("unavailable requirement cannot be captured", str(raised.exception))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
