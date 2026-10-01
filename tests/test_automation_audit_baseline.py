"""Offline automation audit-baseline validation and comparison tests."""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.audit_baseline import (  # noqa: E402
    BaselineValidationError,
    Classification,
    canonical_configuration_digest,
    compare_baselines,
    load_baseline,
    render_bounded_report,
)
from ha_mcp_engineering.audit_baseline.models import (  # noqa: E402
    BASELINE_SCHEMA,
    FINGERPRINT_MODEL,
    FINGERPRINT_SERIALIZATION,
    LEGACY_DECLARED_FINGERPRINT_MODEL,
    LEGACY_UNRESOLVED_FINGERPRINT_MODEL,
)
from ha_mcp_engineering.audit_baseline.validation import (  # noqa: E402
    MAX_INPUT_BYTES,
    canonical_configuration_bytes,
)


SENTINEL = "SYNTHETIC_SECRET_SENTINEL_DO_NOT_ECHO"


def fingerprint_contract(model: str = FINGERPRINT_MODEL) -> dict:
    return {
        "model": model,
        "algorithm": "sha256",
        "serialization": (
            FINGERPRINT_SERIALIZATION
            if model == FINGERPRINT_MODEL
            else "synthetic-json-v2"
        ),
        "input_scope": "configuration_data_object_only",
        "object_key_order": "lexicographic",
        "array_order": "preserved",
        "unicode_escaping": "ensure_ascii=true",
        "number_serialization": (
            "Python json.dumps finite-number encoding; integer and float JSON types are "
            "preserved (for example 1 differs from 1.0)"
        ),
        "non_finite_numbers": "rejected",
        "excluded_fields": [
            "automation operational enabled/on-off state",
            "last_triggered and other runtime entity-state metadata",
            "entity-registry metadata",
            "provider/timing/request envelope metadata",
        ],
        "raw_configuration_persisted": False,
    }


def record(
    configuration_id: str,
    entity_id: str,
    *,
    config=None,
    digest: str | None = None,
    model: str = FINGERPRINT_MODEL,
    mapping_status: str = "verified",
    configuration_status: str = "readable",
    completeness: str = "complete",
    fallback: bool = False,
    warning_count: int = 0,
    redacted: bool = False,
    truncated: bool = False,
    omitted: bool = False,
    enabled: str = "on",
) -> dict:
    if digest is None and configuration_status == "readable":
        digest = canonical_configuration_digest(
            config if config is not None else {"id": configuration_id, "mode": "single"}
        )
    return {
        "configuration_id": configuration_id,
        "entity_id": entity_id,
        "mapping_status": mapping_status,
        "configuration": {
            "status": configuration_status,
            "digest": digest,
            "fingerprint_model": model if digest is not None else None,
            "collected_at": "2026-10-01T14:10:00+00:00",
            "collection_time_status": "observed",
            "provider": "direct_ha_api",
            "coverage": {
                "completeness": completeness,
                "fallback_occurred": fallback,
                "warning_count": warning_count,
                "redacted": redacted,
                "truncated": truncated,
                "omitted": omitted,
            },
        },
        "enabled_state": {
            "state": enabled,
            "collected_at": "2026-10-01T14:09:59+00:00",
            "collection_time_status": "observed",
        },
    }


def baseline(
    records: list[dict],
    *,
    baseline_id: str = "baseline-a",
    installation_id: str | None = "installation-test-1",
    installation_status: str = "established",
    inventory_completeness: str = "complete",
    inventory_limit_reached: bool = False,
    inventory_scope: str = "home_assistant_runtime_automations",
    model: str = FINGERPRINT_MODEL,
    inventory_drift: str = "none_observed_at_capture_fences",
    authority_drift: str = "none_observed_at_capture_fences",
) -> dict:
    if installation_status == "unestablished":
        installation_id = None
    return {
        "schema": BASELINE_SCHEMA,
        "baseline_id": baseline_id,
        "source_artifact": None,
        "capture": {
            "started_at": "2026-10-01T14:00:00+00:00",
            "ended_at": "2026-10-01T14:05:00+00:00",
            "non_atomic": True,
        },
        "installation": {
            "status": installation_status,
            "installation_id": installation_id,
            "method": "synthetic_fixture",
            "limitations": [],
        },
        "inventory": {
            "scope": inventory_scope,
            "discovery_method": "synthetic_fixture",
            "completeness": inventory_completeness,
            "declared_count": len(records),
            "limit": 1000,
            "limit_reached": inventory_limit_reached,
            "omitted_count": 0 if inventory_completeness == "complete" else None,
            "limitations": [],
        },
        "fingerprint_contract": fingerprint_contract(model),
        "records": records,
        "consistency": {
            "inventory_drift": inventory_drift,
            "authority_drift": authority_drift,
            "limitations": ["synthetic_non_atomic_capture"],
        },
        "authority": {
            "status": "synthetic_fixture",
            "home_assistant_core": None,
            "generation": None,
            "registry_sequence": None,
            "compatible_count": None,
            "fallback_count": 0,
            "verification_failure_count": 0,
            "retirement_count": 0,
            "limitations": [],
        },
        "limitations": [],
        "structural_assertions": {
            "source_internal_material_digest_verified": None,
            "configuration_hashes_recomputed": True,
            "record_count_verified": True,
            "fingerprint_model_assignment": "synthetic_fixture",
        },
    }


def legacy_baseline(rows: list[dict]) -> dict:
    value = {
        "authority": {"continuity_result": "unchanged_across_baseline_capture"},
        "baseline_id": "pending",
        "baseline_sha256": "0" * 64,
        "capture": {
            "collection_start_inventory_at_utc": "2026-10-01T14:00:00+00:00",
            "collection_end_inventory_at_utc": "2026-10-01T14:05:00+00:00",
            "configuration_read_count": len(rows),
            "configuration_read_success_count": len(rows),
            "configuration_read_failure_count": 0,
        },
        "coverage": {
            "established_live_inventory": len(rows),
            "captured_complete": len(rows),
            "unknown": 0,
            "fallback_records": 0,
            "warning_records": 0,
            "redaction_or_truncation_records": 0,
            "operational_state_on": len(rows),
            "operational_state_off": 0,
            "disclosed_discovery_gaps": [
                "The public inventory tool is bounded at 100 and returned 100 with provider completeness=complete; this baseline relies on that provider completeness claim."
            ] if len(rows) == 100 else [],
        },
        "fingerprint_contract": {
            "algorithm": "sha256",
            "canonicalization": "recursive object-key lexical sort; array order preserved; compact JSON; JSON scalar types preserved; UTF-8 bytes",
            "coverage_rule": "synthetic",
            "excluded": [
                "automation operational enabled/on-off state",
                "last_triggered and other runtime entity-state metadata",
                "entity-registry metadata",
                "provider/timing/request envelope metadata",
            ],
            "included": "synthetic",
            "input": "entire data object returned by Engineering get_automation_config",
            "model": LEGACY_DECLARED_FINGERPRINT_MODEL,
            "raw_configuration_persisted": False,
        },
        "mapping": {
            "automation_inventory_count": len(rows),
            "entity_registry_inventory_count": len(rows),
            "entity_to_configuration_id_matches": len(rows),
            "missing_mappings": [],
            "contradictory_mappings": [],
        },
        "consistency": {
            "start_inventory_count": len(rows),
            "end_inventory_count": len(rows),
            "added_during_capture": [],
            "removed_during_capture": [],
            "enabled_state_changes_during_capture": [],
            "mapping_drift_detected": False,
            "authority_drift_detected": False,
            "atomic_snapshot": False,
        },
        "records": rows,
        "schema": "ha-automation-configuration-baseline-v1",
    }
    material = {
        "fingerprint_contract": value["fingerprint_contract"],
        "records": sorted(rows, key=lambda row: row["entity_id"]),
        "capture_interval": {
            "start": value["capture"]["collection_start_inventory_at_utc"],
            "end": value["capture"]["collection_end_inventory_at_utc"],
        },
        "authority": value["authority"],
    }
    import hashlib
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    value["baseline_sha256"] = digest
    value["baseline_id"] = "haab-20261001-" + digest[:16]
    return value


def legacy_record(configuration_id: str, entity_id: str) -> dict:
    return {
        "completeness": "complete",
        "configuration_fingerprint": canonical_configuration_digest({"id": configuration_id}),
        "configuration_id": configuration_id,
        "enabled_state": "on",
        "entity_id": entity_id,
        "fallback_occurred": False,
        "provider": "direct_ha_api",
        "redaction_or_truncation_marker_present": False,
        "status": "captured",
        "warning_count": 0,
    }


class BaselineTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name: str, value: dict | str | bytes) -> Path:
        path = self.root / name
        if isinstance(value, bytes):
            path.write_bytes(value)
        elif isinstance(value, str):
            path.write_text(value, encoding="utf-8")
        else:
            path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def load_pair(self, earlier: dict, later: dict):
        a = load_baseline(self.write("a.json", earlier))
        b = load_baseline(self.write("b.json", later))
        return a, b


class ComparisonSemanticsTests(BaselineTestCase):
    def test_identical_baselines(self):
        rows = [record("one", "automation.one")]
        a, b = self.load_pair(baseline(rows), baseline(copy.deepcopy(rows), baseline_id="baseline-b"))
        report = compare_baselines(a, b)
        self.assertEqual(report.counts["UNCHANGED"], 1)
        self.assertEqual(report.counts["TOTAL"], 1)

    def test_one_changed_configuration(self):
        a_rows = [record("one", "automation.one", config={"id": "one", "mode": "single"})]
        b_rows = [record("one", "automation.one", config={"id": "one", "mode": "restart"})]
        a, b = self.load_pair(baseline(a_rows), baseline(b_rows, baseline_id="baseline-b"))
        result = compare_baselines(a, b).records[0]
        self.assertEqual(result.classification, Classification.CHANGED)

    def test_added_and_removed_with_complete_inventory(self):
        a_rows = [record("one", "automation.one"), record("removed", "automation.removed")]
        b_rows = [record("one", "automation.one"), record("added", "automation.added")]
        a, b = self.load_pair(baseline(a_rows), baseline(b_rows, baseline_id="baseline-b"))
        by_id = {item.configuration_id: item for item in compare_baselines(a, b).records}
        self.assertEqual(by_id["added"].classification, Classification.ADDED)
        self.assertEqual(by_id["removed"].classification, Classification.REMOVED)

    def test_missing_objects_under_incomplete_inventory_are_unknown(self):
        a_rows = [record("one", "automation.one")]
        b_rows = [record("one", "automation.one"), record("later", "automation.later")]
        a, b = self.load_pair(
            baseline(a_rows, inventory_completeness="partial"),
            baseline(b_rows, baseline_id="baseline-b"),
        )
        by_id = {item.configuration_id: item for item in compare_baselines(a, b).records}
        self.assertEqual(by_id["later"].classification, Classification.UNKNOWN)
        self.assertIn("earlier_inventory_absence_not_authoritative", by_id["later"].reasons)

    def test_read_failure_is_unknown_not_removed(self):
        a_rows = [record("one", "automation.one")]
        b_rows = [record(
            "one", "automation.one", configuration_status="unreadable",
            completeness="failed", digest=None,
        )]
        a, b = self.load_pair(baseline(a_rows), baseline(b_rows, baseline_id="baseline-b"))
        result = compare_baselines(a, b).records[0]
        self.assertEqual(result.classification, Classification.UNKNOWN)
        self.assertNotEqual(result.classification, Classification.REMOVED)

    def test_entity_rename_separate_from_configuration_change(self):
        digest = canonical_configuration_digest({"id": "one", "mode": "single"})
        a_rows = [record("one", "automation.old_name", digest=digest)]
        b_rows = [record("one", "automation.new_name", digest=digest)]
        a, b = self.load_pair(baseline(a_rows), baseline(b_rows, baseline_id="baseline-b"))
        result = compare_baselines(a, b).records[0]
        self.assertEqual(result.classification, Classification.UNCHANGED)
        self.assertTrue(result.entity_id_changed)

    def test_enabled_state_change_is_separate(self):
        digest = canonical_configuration_digest({"id": "one"})
        a_rows = [record("one", "automation.one", digest=digest, enabled="on")]
        b_rows = [record("one", "automation.one", digest=digest, enabled="off")]
        a, b = self.load_pair(baseline(a_rows), baseline(b_rows, baseline_id="baseline-b"))
        result = compare_baselines(a, b).records[0]
        self.assertEqual(result.classification, Classification.UNCHANGED)
        self.assertTrue(result.enabled_state_changed)

    def test_unknown_operational_state_is_not_reported_as_enabled_change(self):
        digest = canonical_configuration_digest({"id": "one"})
        a_rows = [record("one", "automation.one", digest=digest, enabled="unknown")]
        b_rows = [record("one", "automation.one", digest=digest, enabled="on")]
        a, b = self.load_pair(baseline(a_rows), baseline(b_rows, baseline_id="baseline-b"))
        result = compare_baselines(a, b).records[0]
        self.assertEqual(result.classification, Classification.UNCHANGED)
        self.assertFalse(result.enabled_state_changed)

    def test_shared_record_with_inventory_scope_mismatch_is_unknown(self):
        rows = [record("one", "automation.one")]
        a, b = self.load_pair(
            baseline(rows, inventory_scope="home_assistant_runtime_automations"),
            baseline(
                copy.deepcopy(rows),
                baseline_id="baseline-b",
                inventory_scope="synthetic_other_scope",
            ),
        )
        result = compare_baselines(a, b).records[0]
        self.assertEqual(result.classification, Classification.UNKNOWN)
        self.assertIn("inventory_scope_mismatch", result.reasons)

    def test_mapping_contradiction_makes_record_unknown(self):
        a_rows = [record("one", "automation.one")]
        b_rows = [record("one", "automation.one", mapping_status="contradictory")]
        a, b = self.load_pair(baseline(a_rows), baseline(b_rows, baseline_id="baseline-b"))
        result = compare_baselines(a, b).records[0]
        self.assertEqual(result.classification, Classification.UNKNOWN)
        self.assertIn("later_mapping_unverified", result.reasons)

    def test_cross_installation_is_unknown(self):
        rows = [record("one", "automation.one")]
        a, b = self.load_pair(
            baseline(rows, installation_id="installation-a"),
            baseline(copy.deepcopy(rows), baseline_id="baseline-b", installation_id="installation-b"),
        )
        result = compare_baselines(a, b).records[0]
        self.assertEqual(result.classification, Classification.UNKNOWN)
        self.assertIn("installation_identity_mismatch", result.reasons)

    def test_unestablished_installation_is_unknown(self):
        rows = [record("one", "automation.one")]
        a, b = self.load_pair(
            baseline(rows, installation_status="unestablished"),
            baseline(copy.deepcopy(rows), baseline_id="baseline-b"),
        )
        self.assertEqual(compare_baselines(a, b).records[0].classification, Classification.UNKNOWN)

    def test_fingerprint_version_mismatch_is_unknown(self):
        a_rows = [record("one", "automation.one", model=FINGERPRINT_MODEL)]
        b_rows = [record("one", "automation.one", model="synthetic-fingerprint-v2")]
        a, b = self.load_pair(
            baseline(a_rows, model=FINGERPRINT_MODEL),
            baseline(b_rows, baseline_id="baseline-b", model="synthetic-fingerprint-v2"),
        )
        result = compare_baselines(a, b).records[0]
        self.assertEqual(result.classification, Classification.UNKNOWN)
        self.assertIn("fingerprint_contract_incompatible", result.reasons)

    def test_authority_drift_blocks_definitive_shared_comparison(self):
        rows = [record("one", "automation.one")]
        a, b = self.load_pair(
            baseline(rows, authority_drift="detected"),
            baseline(copy.deepcopy(rows), baseline_id="baseline-b"),
        )
        result = compare_baselines(a, b).records[0]
        self.assertEqual(result.classification, Classification.UNKNOWN)
        self.assertIn("earlier_authority_continuity_unestablished", result.reasons)

    def test_inventory_drift_blocks_absence_classification(self):
        a_rows = [record("one", "automation.one")]
        b_rows = [record("one", "automation.one"), record("later", "automation.later")]
        a, b = self.load_pair(
            baseline(a_rows, inventory_drift="detected"),
            baseline(b_rows, baseline_id="baseline-b"),
        )
        by_id = {item.configuration_id: item for item in compare_baselines(a, b).records}
        self.assertEqual(by_id["later"].classification, Classification.UNKNOWN)
        self.assertIn("earlier_inventory_absence_not_authoritative", by_id["later"].reasons)

    def test_redacted_truncated_and_warning_records_are_unknown(self):
        cases = [
            {"redacted": True},
            {"truncated": True},
            {"warning_count": 1},
            {"fallback": True},
            {"completeness": "partial"},
        ]
        for index, kwargs in enumerate(cases):
            with self.subTest(index=index):
                a_rows = [record("one", "automation.one")]
                b_rows = [record("one", "automation.one", **kwargs)]
                a, b = self.load_pair(
                    baseline(a_rows), baseline(b_rows, baseline_id=f"baseline-b{index}")
                )
                self.assertEqual(
                    compare_baselines(a, b).records[0].classification,
                    Classification.UNKNOWN,
                )

    def test_partial_results_preserve_other_positive_comparisons(self):
        a_rows = [record("good", "automation.good"), record("bad", "automation.bad")]
        b_rows = [
            record("good", "automation.good"),
            record("bad", "automation.bad", completeness="partial"),
        ]
        a, b = self.load_pair(baseline(a_rows), baseline(b_rows, baseline_id="baseline-b"))
        by_id = {item.configuration_id: item for item in compare_baselines(a, b).records}
        self.assertEqual(by_id["good"].classification, Classification.UNCHANGED)
        self.assertEqual(by_id["bad"].classification, Classification.UNKNOWN)

    def test_deterministic_order_and_count_reconciliation(self):
        a_rows = [record("z", "automation.z"), record("a", "automation.a")]
        b_rows = [record("a", "automation.a"), record("m", "automation.m")]
        a, b = self.load_pair(baseline(a_rows), baseline(b_rows, baseline_id="baseline-b"))
        report = compare_baselines(a, b)
        self.assertEqual([item.configuration_id for item in report.records], ["a", "m", "z"])
        self.assertEqual(sum(report.counts[key] for key in ("UNCHANGED", "CHANGED", "ADDED", "REMOVED", "UNKNOWN")), report.counts["TOTAL"])


class FingerprintControlTests(unittest.TestCase):
    def test_object_key_order_is_insensitive_but_array_order_is_not(self):
        self.assertEqual(
            canonical_configuration_digest({"b": 2, "a": 1}),
            canonical_configuration_digest({"a": 1, "b": 2}),
        )
        self.assertNotEqual(
            canonical_configuration_digest({"a": [1, 2]}),
            canonical_configuration_digest({"a": [2, 1]}),
        )

    def test_unicode_uses_python_ensure_ascii_true(self):
        self.assertEqual(canonical_configuration_bytes({"x": "é"}), b'{"x":"\\u00e9"}')

    def test_numeric_serialization_preserves_int_float_distinction(self):
        self.assertEqual(canonical_configuration_bytes({"a": 1, "b": 1.0}), b'{"a":1,"b":1.0}')
        self.assertNotEqual(
            canonical_configuration_digest({"a": 1}),
            canonical_configuration_digest({"a": 1.0}),
        )

    def test_non_finite_configuration_number_is_rejected(self):
        with self.assertRaisesRegex(BaselineValidationError, "non_finite_number"):
            canonical_configuration_digest({"x": float("nan")})


class ValidationAndBoundsTests(BaselineTestCase):

    def test_legacy_baseline_normalizes_without_upgrading_missing_assurance(self):
        value = legacy_baseline([legacy_record("one", "automation.one")])
        loaded = load_baseline(self.write("legacy.json", value))
        self.assertEqual(loaded.installation.status, "unestablished")
        self.assertEqual(loaded.inventory.completeness, "unknown")
        self.assertFalse(loaded.structural_assertions["configuration_hashes_recomputed"])
        self.assertTrue(loaded.structural_assertions["source_internal_material_digest_verified"])
        self.assertEqual(
            loaded.fingerprint_contract.model, LEGACY_UNRESOLVED_FINGERPRINT_MODEL
        )
        self.assertNotEqual(loaded.fingerprint_contract.model, FINGERPRINT_MODEL)

    def test_legacy_internal_material_digest_mismatch_rejected(self):
        value = legacy_baseline([legacy_record("one", "automation.one")])
        value["baseline_sha256"] = "0" * 64
        with self.assertRaisesRegex(BaselineValidationError, "legacy_material_digest_mismatch"):
            load_baseline(self.write("legacy-bad.json", value))

    def test_duplicate_json_keys_rejected(self):
        path = self.write("dup.json", '{"schema":"x","schema":"y"}')
        with self.assertRaisesRegex(BaselineValidationError, "duplicate_json_key"):
            load_baseline(path)

    def test_duplicate_canonical_identity_rejected(self):
        rows = [record("one", "automation.one"), record("one", "automation.two")]
        path = self.write("dup-id.json", baseline(rows))
        with self.assertRaisesRegex(BaselineValidationError, "duplicate_canonical_identity"):
            load_baseline(path)

    def test_contradictory_verified_entity_mapping_rejected(self):
        rows = [record("one", "automation.same"), record("two", "automation.same")]
        path = self.write("entity-conflict.json", baseline(rows))
        with self.assertRaisesRegex(BaselineValidationError, "contradictory_entity_mapping"):
            load_baseline(path)

    def test_invalid_digest_rejected_without_echo(self):
        row = record("one", "automation.one")
        row["configuration"]["digest"] = "sha256:" + SENTINEL
        path = self.write("bad-digest.json", baseline([row]))
        with self.assertRaises(BaselineValidationError) as ctx:
            load_baseline(path)
        self.assertNotIn(SENTINEL, str(ctx.exception))

    def test_complete_inventory_cannot_claim_reached_limit(self):
        value = baseline([record("one", "automation.one")], inventory_limit_reached=True)
        with self.assertRaisesRegex(BaselineValidationError, "inventory_completeness_contradiction"):
            load_baseline(self.write("inventory-contradiction.json", value))

    def test_record_count_limit(self):
        rows = [record(f"id{i}", f"automation.id{i}") for i in range(1001)]
        value = baseline(rows)
        with self.assertRaisesRegex(BaselineValidationError, "record_limit_exceeded"):
            load_baseline(self.write("too-many-records.json", value))

    def test_string_limit(self):
        value = baseline([record("one", "automation.one")])
        value["installation"]["method"] = "x" * 17000
        with self.assertRaisesRegex(BaselineValidationError, "json_string_limit_exceeded"):
            load_baseline(self.write("long-string.json", value))

    def test_authority_fields_are_closed_and_diagnostic_is_bounded(self):
        value = baseline([record("one", "automation.one")])
        value["authority"][SENTINEL] = SENTINEL
        with self.assertRaises(BaselineValidationError) as ctx:
            load_baseline(self.write("authority-extra.json", value))
        self.assertEqual(str(ctx.exception), "authority_fields_invalid")
        self.assertNotIn(SENTINEL, str(ctx.exception))

    def test_raw_synthetic_configuration_content_is_not_in_comparison_output(self):
        config = {"id": "one", "description": SENTINEL, "action": [{"service": "light.turn_on"}]}
        rows_a = [record("one", "automation.one", config=config)]
        rows_b = [record("one", "automation.one", config=copy.deepcopy(config))]
        a, b = self.load_pair(baseline(rows_a), baseline(rows_b, baseline_id="baseline-b"))
        rendered = render_bounded_report(compare_baselines(a, b))
        self.assertNotIn(SENTINEL.encode(), rendered)

    def test_unsupported_schema_rejected(self):
        path = self.write("bad-schema.json", {"schema": "automation-audit-baseline-v999"})
        with self.assertRaisesRegex(BaselineValidationError, "baseline_schema_unsupported"):
            load_baseline(path)

    def test_malformed_json_rejected(self):
        path = self.write("bad-json.json", "{not json")
        with self.assertRaisesRegex(BaselineValidationError, "invalid_json"):
            load_baseline(path)

    def test_input_byte_limit(self):
        path = self.write("large.json", b"{" + b" " * MAX_INPUT_BYTES + b"}")
        with self.assertRaisesRegex(BaselineValidationError, "input_size_limit_exceeded"):
            load_baseline(path)

    def test_depth_limit(self):
        value = "0"
        for _ in range(70):
            value = "[" + value + "]"
        path = self.write("deep.json", value)
        with self.assertRaisesRegex(BaselineValidationError, "json_depth_limit_exceeded"):
            load_baseline(path)

    def test_output_bound_truncates_details_but_not_counts(self):
        rows = [record(f"id{i:03d}", f"automation.id{i:03d}") for i in range(40)]
        a, b = self.load_pair(baseline(rows), baseline(copy.deepcopy(rows), baseline_id="baseline-b"))
        report = compare_baselines(a, b)
        rendered = render_bounded_report(report, max_bytes=3_000, max_details=1000)
        self.assertLessEqual(len(rendered), 3_000)
        parsed = json.loads(rendered)
        self.assertTrue(parsed["details_truncated"])
        self.assertGreater(parsed["omitted_detail_count"], 0)
        self.assertEqual(parsed["counts"]["TOTAL"], 40)
        self.assertEqual(parsed["counts"]["UNCHANGED"], 40)

    def test_full_output_fits_at_its_exact_rendered_size(self):
        rows = [record("one", "automation.one")]
        a, b = self.load_pair(
            baseline(rows), baseline(copy.deepcopy(rows), baseline_id="baseline-b")
        )
        report = compare_baselines(a, b)
        full = render_bounded_report(report, max_bytes=1_000_000, max_details=1000)
        exact = render_bounded_report(report, max_bytes=len(full), max_details=1000)
        self.assertEqual(exact, full)
        self.assertFalse(json.loads(exact)["details_truncated"])

    def test_invalid_input_diagnostic_never_echoes_sensitive_sentinel(self):
        path = self.write("sensitive.json", '{"schema":"' + SENTINEL + '"}')
        with self.assertRaises(BaselineValidationError) as ctx:
            load_baseline(path)
        self.assertNotIn(SENTINEL, str(ctx.exception))

    def test_no_network_or_subprocess_access(self):
        rows = [record("one", "automation.one")]
        a_path = self.write("a.json", baseline(rows))
        b_path = self.write("b.json", baseline(copy.deepcopy(rows), baseline_id="baseline-b"))
        with patch.object(socket, "socket", side_effect=AssertionError("network access")), patch.object(
            subprocess, "Popen", side_effect=AssertionError("subprocess access")
        ):
            report = compare_baselines(load_baseline(a_path), load_baseline(b_path))
            self.assertEqual(report.counts["UNCHANGED"], 1)


if __name__ == "__main__":
    unittest.main()
