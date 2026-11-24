"""HAMCP-319 section 5.1 case runners for the 91_only lane (offline).

Exercises ``scripts/exact_image_admission_91_runners.py`` and the lane job in
``.github/workflows/ci.yml`` without Docker, network or any live system:

* every listed case of each active configuration is produced by a workflow
  step conditioned on that configuration, exactly once, by a real runner;
* the 9.1 positive endpoint is the full ``/synthetic-upstream-secret/mcp``
  path followed by ``/catalog/full`` and the short form is refused;
* the side_by_side lane, its beta.7 rollback stage and its persisted-data
  transitions are retired (HAMCP-437, HAMCP-405);
* a runner that does not execute, a wrong endpoint form, and a dormant or
  identity-only lane never produce a passing lane or an ACCEPTED gate.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
if str(ROOT / "tests") not in sys.path:
    sys.path.append(str(ROOT / "tests"))
from support import governed91_evidence  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lanes = _load("exact_image_admission_lanes")
runners = _load("exact_image_admission_91_runners")
typed = _load("exact_image_admission_91_typed")

WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
JOB = "exact-image-admission-lanes"
RUNNER_CONFIGURATIONS = ("91_only",)
STRICT = "http://abcdef12-ha-mcp:18086/synthetic-upstream-secret/mcp/catalog/full"
SHORT_STRICT = "http://abcdef12-ha-mcp:18086/synthetic-upstream-secret/catalog/full"
LEGACY = "http://abcdef12-ha-mcp:18086/synthetic-upstream-secret/mcp"
PEER_STRICT = "http://127.0.0.1:18086/synthetic-upstream-secret/mcp/catalog/full"
PEER_LEGACY = "http://127.0.0.1:18086/synthetic-upstream-secret/mcp"
LANE_VERSION = "2.4.2-beta.13"
PROTOCOL = "2025-03-26"
COMMIT = "a" * 40


def workflow_job() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"][JOB]


def step_configurations(step: dict) -> set[str]:
    condition = str(step.get("if", ""))
    if "matrix.state == 'active'" not in condition:
        return set()
    equal = re.findall(r"matrix\.configuration == '([0-9a-z_]+)'", condition)
    unequal = re.findall(r"matrix\.configuration != '([0-9a-z_]+)'", condition)
    configurations = set(equal) if equal else set(lanes.CASES)
    return configurations - set(unequal)


def produced_cases(step: dict, configuration: str) -> list[str]:
    text = str(step.get("run", ""))
    produced = re.findall(r"admission-cases/([0-9a-z_]+)\.json", text)
    if "exact_image_admission_lanes.py identity" in text:
        produced += [f"image_identity_{role}" for role in lanes.ROLES[configuration]]
    if "exact_image_admission_91_typed.py" in text:
        produced.append(typed.CASE)
    if "exact_image_admission_91_governed.py" in text:
        produced += list(governed91_evidence.governed.CASES_BY_CONFIGURATION.get(configuration, ()))
    return produced


def health(version: str = LANE_VERSION, **view) -> dict:
    return {"success": True, "operation": "get_server_health",
            "data": {"server": {"version": version}, "upstream_read_gateway": view}}


# The reviewed 9.2.0 capture the runtime's strict-full fingerprint is bound to,
# and the five optional filesystem descriptors as the 9.2.0 image advertises
# them with the toggle enabled (HAMCP-371 S8 standalone fs-on capture).
REVIEWED_91 = runners.load_reviewed_91_capture()
OPTIONAL_91 = [tool for tool in json.loads((ROOT / "docs" / "evidence" / "hamcp371" / "s8" / "profiles-2"
                                            / "out" / "standalone-9.2.0-fs-on" / "capture.json")
                                           .read_text(encoding="utf-8"))["tools"]
               if tool["name"] in runners.OPTIONAL_NAMES]
AUTOMATIC_READS = 27


def peer_tools_91() -> list:
    return json.loads(json.dumps(REVIEWED_91[:40] + OPTIONAL_91 + REVIEWED_91[40:]))


def strict_91_view(**overrides) -> dict:
    view = {
        "initialized": True, "admission_status": "admitted_exact", "version_status": "reviewed_exact",
        "observed_identity_status": "accepted", "observed_upstream_server_version": "9.2.0",
        "selected_compatibility_entry_id": "ha-mcp-v9.2.0-reviewed",
        "reviewed_strict_full_contract_fingerprint": runners.canonical_fingerprint({"tools": REVIEWED_91}),
        "observed_strict_full_contract_fingerprint": runners.canonical_fingerprint({"tools": peer_tools_91()}),
        "catalog_admission_mode": "reviewed_per_tool_contract_subset",
        "catalog_comparison_status": "exact", "runtime_contract_mismatch_count": 0,
        "output_contract_mismatch_count": 0, "schema_mismatch_count": 0,
        "schema_mismatched_automatic_read_count": 0, "description_semantics_mismatch_count": 0,
        "annotation_mismatch_count": 0, "reviewed_tool_accounting_valid": True,
        "reviewed_automatic_read_count": AUTOMATIC_READS,
        "policy_classifications": {"automatic_read": AUTOMATIC_READS, "persistent_write": 32},
        "exact_matched_automatic_read_count": AUTOMATIC_READS, "accounted_automatic_read_count": AUTOMATIC_READS,
        "automatic_read_accounting_valid": True, "admission_complete": True,
        "quarantined_automatic_read_count": 0, "quarantine_reason_counts": {},
        "missing_reviewed_read_count": 0, "missing_automatic_read_count": 0,
        "unreviewed_tool_count": 0, "unreviewed_observed_tool_count": 0, "catalog_diff_field_counts": {},
        "optional_tools": {runners.YAML_READER: "admitted",
                           **{name: "prohibited_withheld" for name in runners.YAML_SIBLINGS}},
        "optional_sibling_drift": [], "optional_reviewed_accounting_valid": True,
        "writes_allowed": False, "direct_ha_fallback_allowed": False, "stock_catalog_match_is_informational": True,
        "reviewed_accounted_tool_count": 77, "reviewed_policy_entry_count": 77, "upstream_advertised_tool_count": 82,
        "dynamically_exposed_count": AUTOMATIC_READS + 1, "core_withheld_read_count": 0,
        "observed_protocol_version": PROTOCOL,
    }
    view.update(overrides)
    return view


def served_probe(version: str = "9.2.0") -> dict:
    return {"success": True, "metadata": {"provider": "upstream_read_gateway", "upstream_version": version,
                                          "upstream_dispatch_occurred": True}}


def refused_probe() -> dict:
    return {"success": False, "error_code": "upstream_unavailable",
            "details": {"failure_category": runners.CATALOG_MODE_UNADMITTED},
            "metadata": {"upstream_dispatch_occurred": False}}


FIXTURE = {"rest_reads": 10, "websocket_reads": 4, "http_mutations": 0, "websocket_mutations": 0}


def health_case(expect: str, **overrides):
    configuration, name = runners.EXPECTATIONS[expect]
    arguments = dict(
        configured_url=STRICT if lanes.case_form(configuration, name) == "strict_full" else LEGACY,
        engineering_version=LANE_VERSION, container_image=runners.LANE_ENGINEERING_IMAGE,
        tool_names=[], probe=refused_probe(), fixture_before=dict(FIXTURE), fixture_after=dict(FIXTURE),
        peer_requests=[],
    )
    if expect == "admitted_strict_91":
        arguments.update(health_before=health(**strict_91_view()), health_after=health(**strict_91_view()),
                         tool_names=[runners.PROBE_TOOL], probe=served_probe(), peer_requests=None,
                         peer_tools=peer_tools_91(), reviewed_tools=REVIEWED_91)
    elif expect == "catalog_mode_unadmitted_at_startup":
        view = {"initialized": False, "admission_status": "pending_discovery", "dynamically_exposed_count": 0,
                "observed_upstream_server_version": None, "discovery_attempt_count": 2,
                "last_discovery_failure_category": runners.CATALOG_MODE_UNADMITTED,
                "operator_remediation": "set the value ending in /catalog/full"}
        arguments.update(health_before=health(**view), health_after=health(**view),
                         peer_requests=[(runners.PEER_SECRET_PATH, 200)] * 3)
    arguments.update(overrides)
    return runners.evaluate_health_case(expect, **arguments)


def gateway_source(version: str, optional: str | None) -> dict:
    source = {"result": "PASS", "upstream_version": version, "dynamic_tool_count": 27,
              "observed_catalog_fingerprint": "f" * 64}
    if optional == "admitted":
        source.update(observed_optional_tools=list(runners.OPTIONAL_NAMES),
                      optional_exposed_tools=[runners.YAML_READER])
    elif optional == "absent":
        source.update(observed_optional_tools=[], optional_exposed_tools=[])
    return source


def gateway_health(version: str, optional: str | None, engineering: str = LANE_VERSION) -> dict:
    view = {"admission_status": "admitted_exact", "observed_upstream_server_version": version,
            "selected_compatibility_entry_id": f"ha-mcp-v{version}-reviewed", "observed_protocol_version": PROTOCOL}
    if optional == "admitted":
        view["optional_tools"] = {runners.YAML_READER: "admitted",
                                  **{name: "prohibited_withheld" for name in runners.YAML_SIBLINGS}}
    elif optional == "absent":
        view["optional_tools"] = {name: "optional_absent" for name in runners.OPTIONAL_NAMES}
    return health(engineering, **view)


def gateway_case(configuration: str, name: str, **overrides):
    version, optional = runners.GATEWAY_CASES[(configuration, name)]
    form = lanes.case_form(configuration, name)
    arguments = dict(
        source=gateway_source(version, optional),
        configured_url=STRICT if form == "strict_full" else LEGACY,
        upstream_endpoint=PEER_STRICT if form == "strict_full" else PEER_LEGACY,
        engineering_version=LANE_VERSION, container_image=runners.LANE_ENGINEERING_IMAGE,
        health=gateway_health(version, optional),
    )
    arguments.update(overrides)
    return runners.evaluate_gateway_case(configuration, name, **arguments)


PEER_VERSIONS = {"candidate": "9.2.0"}


def synthetic_peer(role: str) -> dict:
    seed = "b" if role == "baseline" else "c"
    return {"upstream_version": PEER_VERSIONS[role], "image_index_digest": "sha256:" + seed * 64,
            "architecture_image_digests": {"linux/amd64": "sha256:" + seed + "1" * 63,
                                           "linux/arm64": "sha256:" + seed + "2" * 63}}


def lane_row(configuration: str, state: str = "active", architecture: str = "amd64") -> dict:
    row = {"configuration": configuration, "architecture": architecture, "state": state,
           "runner": "ubuntu-24.04" if architecture == "amd64" else "ubuntu-24.04-arm",
           "peers": {role: synthetic_peer(role) for role in lanes.ROLES[configuration]}}
    if state == "dormant":
        row["dormant_reason"] = "synthetic dormant reason"
    return row


def passing_results(configuration: str, architecture: str = "amd64") -> dict:
    results = {}
    for case in lanes.CASES[configuration]:
        observed = {"negotiated_protocol": lanes.EXPECTED_PROTOCOL}
        if case.name.startswith("image_identity_"):
            role = case.name.removeprefix("image_identity_")
            peer = synthetic_peer(role)
            observed = {"role": role, "version": peer["upstream_version"], "image_digest": peer["image_index_digest"],
                        "manifest_digest": peer["architecture_image_digests"]["linux/" + architecture],
                        "config_digest": "sha256:" + "e" * 64}
        if case.name in governed91_evidence.governed.REQUIRED_OPERATIONS:
            observed = governed91_evidence.passing_observed(case.name)
        results[case.name] = lanes.case_result(case.name, "passed", form=case.endpoint_form, observed=observed)
    return results


class WorkflowCaseCoverageTests(unittest.TestCase):
    def test_every_listed_case_has_exactly_one_runner_step_per_configuration(self) -> None:
        job = workflow_job()
        for configuration in RUNNER_CONFIGURATIONS:
            produced: list[str] = []
            for step in job["steps"]:
                if configuration in step_configurations(step):
                    produced += produced_cases(step, configuration)
            with self.subTest(configuration=configuration):
                self.assertEqual(sorted(produced), sorted(lanes.case_names(configuration)))

    def test_runner_steps_write_only_their_own_configuration_cases(self) -> None:
        for step in workflow_job()["steps"]:
            configurations = step_configurations(step)
            if len(configurations) != 1:
                continue
            (configuration,) = configurations
            for name in produced_cases(step, configuration):
                with self.subTest(step=step.get("name"), case=name):
                    self.assertIn(name, lanes.case_names(configuration))

    def test_no_placeholder_or_skip_path_remains_for_active_lanes(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("Active lane without an established case runner", text)
        for step in workflow_job()["steps"]:
            with self.subTest(step=step.get("name")):
                self.assertNotIn("continue-on-error", step)
                self.assertNotIn("--status skipped", str(step.get("run", "")))

    def test_manifest_and_cleanup_always_run_and_gate_job_is_unchanged_in_shape(self) -> None:
        steps = {step.get("name"): step for step in workflow_job()["steps"]}
        self.assertEqual(steps["Write section 5.1 acceptance manifest"]["if"], "always()")
        cleanup = steps["Remove disposable exact-image admission environment"]["run"]
        for container in ("engineering-admission-91-strict", "engineering-admission-91-legacy",
                          "engineering-admission-91-fs", "exact-admission-upstream"):
            self.assertIn(container, cleanup)
        self.assertIn("label=io.hass-mcp.typed91", cleanup)

    def test_lane_runners_stay_on_reviewed_hosted_images_without_new_permissions(self) -> None:
        job = workflow_job()
        self.assertEqual(job["runs-on"], "${{ matrix.runner || 'ubuntu-24.04' }}")
        self.assertNotIn("permissions", job)
        self.assertNotIn("secrets.", WORKFLOW.read_text(encoding="utf-8").split(f"  {JOB}:", 1)[1]
                         .split("\n  exact-image-admission-manifest-gate:", 1)[0])


class StrictFullEndpointTests(unittest.TestCase):
    def test_workflow_values_use_the_full_secret_path_followed_by_catalog_full(self) -> None:
        env = workflow_job()["env"]
        self.assertEqual(env["STRICT_FULL_UPSTREAM_URL"], STRICT)
        self.assertEqual(env["PEER_STRICT_FULL_URL"], PEER_STRICT)
        self.assertEqual(env["LEGACY_UPSTREAM_URL"], LEGACY)
        self.assertEqual(env["PEER_LEGACY_URL"], PEER_LEGACY)

    def test_short_catalog_full_form_appears_nowhere_in_the_lane_workflow(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("synthetic-upstream-secret/catalog/full", text)

    def test_endpoint_problem_accepts_only_the_exact_lane_values(self) -> None:
        self.assertIsNone(runners.endpoint_problem(STRICT, "strict_full"))
        self.assertIsNone(runners.endpoint_problem(PEER_STRICT, "strict_full"))
        self.assertIsNone(runners.endpoint_problem(LEGACY, "legacy"))
        self.assertEqual(runners.endpoint_problem(SHORT_STRICT, "strict_full"), "endpoint_path_not_lane_value")
        self.assertEqual(runners.endpoint_problem(STRICT, "legacy"), "endpoint_form_mismatch")
        self.assertEqual(runners.endpoint_problem(LEGACY, "strict_full"), "endpoint_form_mismatch")
        self.assertEqual(runners.endpoint_problem(STRICT + "?x=1", "strict_full"), "endpoint_not_canonical")
        self.assertEqual(runners.endpoint_problem(STRICT.replace("mcp/", "mc%70/"), "strict_full"),
                         "endpoint_not_canonical")
        self.assertEqual(runners.endpoint_problem("https://abcdef12-ha-mcp:18086/synthetic-upstream-secret/mcp",
                                                  "legacy"), "endpoint_not_loopback_peer")
        self.assertEqual(runners.endpoint_problem("http://example.com:18086/synthetic-upstream-secret/mcp",
                                                  "legacy"), "endpoint_not_loopback_peer")

    def test_strict_cases_with_the_short_form_fail(self) -> None:
        status, reason, _ = health_case("admitted_strict_91", configured_url=SHORT_STRICT)
        self.assertEqual((status, reason), ("failed", "endpoint_path_not_lane_value"))
        status, reason, _ = gateway_case("91_only", "91_parity_read_gateway_acceptance",
                                         configured_url=SHORT_STRICT)
        self.assertEqual((status, reason), ("failed", "endpoint_path_not_lane_value"))
        status, reason, _ = gateway_case("91_only", "91_parity_read_gateway_acceptance",
                                         upstream_endpoint="http://127.0.0.1:18086/synthetic-upstream-secret/catalog/full")
        self.assertEqual((status, reason), ("failed", "endpoint_path_not_lane_value"))

    def test_wrong_endpoint_form_for_the_case_fails(self) -> None:
        status, reason, _ = health_case("admitted_strict_91", configured_url=LEGACY)
        self.assertEqual((status, reason), ("failed", "endpoint_form_mismatch"))
        status, reason, _ = gateway_case("91_only", "91_parity_read_gateway_acceptance",
                                         configured_url=LEGACY, upstream_endpoint=PEER_LEGACY)
        self.assertEqual((status, reason), ("failed", "endpoint_form_mismatch"))

    def test_engineering_command_refuses_the_short_form_and_unlisted_inputs(self) -> None:
        base = dict(name="engineering-admission-x", image=runners.LANE_ENGINEERING_IMAGE, data_dir="/tmp/d",
                    mcp_port=18100, approval_port=18110)
        command = runners.engineering_command(upstream_url=STRICT, **base)
        self.assertIn(f"UPSTREAM_DASHBOARD_MCP_URL={STRICT}", command)
        self.assertEqual(command[-1], runners.LANE_ENGINEERING_IMAGE)
        cases = {
            "engineering_upstream_url_not_lane_value": dict(upstream_url=SHORT_STRICT),
            "engineering_image_not_allowed": dict(upstream_url=STRICT, image="ghcr.io/jeter-1/hass-mcp-engineering-beta:2.4.2-beta.7"),
            "engineering_container_name_invalid": dict(upstream_url=STRICT, name="other-container"),
            "engineering_port_out_of_range": dict(upstream_url=STRICT, mcp_port=8099),
        }
        for category, changes in cases.items():
            with self.subTest(category=category):
                arguments = {**base, **changes}
                with self.assertRaisesRegex(lanes.LaneFailure, category):
                    runners.engineering_command(**arguments)


class PeerCommandTests(unittest.TestCase):
    def row(self, version: str = "9.2.0") -> dict:
        digest = "sha256:" + "d" * 64
        return {"upstream_image": f"ghcr.io/homeassistant-ai/ha-mcp@{digest}", "image_index_digest": digest,
                "upstream_version": version}

    def test_candidate_peer_carries_the_contract_app_options_and_toggle(self) -> None:
        enabled = runners.peer_command(self.row(), "candidate", filesystem="enabled")
        disabled = runners.peer_command(self.row(), "candidate", filesystem="disabled")
        for option in ("ENABLE_TOOL_SEARCH=true", "ENABLE_BETA_FEATURES=true", "READ_ONLY_MODE=false",
                       f"MCP_SECRET_PATH={runners.PEER_SECRET_PATH}", "MCP_HOST=127.0.0.1"):
            self.assertIn(option, enabled)
        self.assertIn("HAMCP_ENABLE_FILESYSTEM_TOOLS=true", enabled)
        self.assertIn("HAMCP_ENABLE_FILESYSTEM_TOOLS=false", disabled)
        self.assertEqual(enabled[-2], self.row()["upstream_image"])

    def test_peer_must_be_digest_pinned_from_the_reviewed_row(self) -> None:
        row = self.row()
        row["upstream_image"] = "ghcr.io/homeassistant-ai/ha-mcp:9.2.0"
        with self.assertRaisesRegex(lanes.LaneFailure, "peer_row_not_digest_pinned"):
            runners.peer_command(row, "candidate", filesystem="enabled")
        with self.assertRaisesRegex(lanes.LaneFailure, "peer_row_version_unexpected"):
            runners.peer_command(self.row("8.5.0"), "candidate", filesystem="enabled")
        # HAMCP-437: the 8.5.0 baseline peer retired with the 85_only and side_by_side lanes.
        with self.assertRaisesRegex(lanes.LaneFailure, "peer_role_unexpected"):
            runners.peer_command(self.row(), "baseline", filesystem="disabled")

    def test_workflow_starts_peers_only_from_the_lane_row(self) -> None:
        for step in workflow_job()["steps"]:
            text = str(step.get("run", ""))
            if "start-peer" in text:
                with self.subTest(step=step.get("name")):
                    self.assertIn('--peers "$LANE_PEERS"', text)
                    self.assertNotRegex(text, r"ghcr\.io/homeassistant-ai")


class HealthCaseTests(unittest.TestCase):
    def test_each_expectation_passes_on_its_observed_contract(self) -> None:
        for expect in runners.EXPECTATIONS:
            with self.subTest(expect=expect):
                self.assertEqual(health_case(expect)[:2], ("passed", ""))

    def test_strict_admission_records_one_protocol_and_both_fingerprints(self) -> None:
        _, _, observed = health_case("admitted_strict_91")
        self.assertEqual(observed["negotiated_protocol"], PROTOCOL)
        self.assertEqual(observed["reviewed_strict_full_contract_fingerprint"],
                         strict_91_view()["reviewed_strict_full_contract_fingerprint"])
        self.assertEqual(observed["observed_strict_full_contract_fingerprint"],
                         strict_91_view()["observed_strict_full_contract_fingerprint"])
        self.assertNotEqual(observed["reviewed_strict_full_contract_fingerprint"],
                            observed["observed_strict_full_contract_fingerprint"])
        self.assertEqual(observed["peer_catalog"]["advertised"], 82)
        self.assertEqual(observed["peer_catalog"]["reviewed_exact"], 77)
        self.assertEqual(observed["peer_catalog"]["optional_on_wire"], sorted(runners.OPTIONAL_NAMES))

    def test_strict_admission_negatives(self) -> None:
        negatives = {
            "admission_not_exact": dict(admission_status="pending_discovery"),
            "observed_version_not_91": dict(observed_upstream_server_version="8.5.0"),
            "selected_entry_not_91": dict(selected_compatibility_entry_id="ha-mcp-v8.5.0-reviewed"),
            "catalog_comparison_not_exact": dict(catalog_comparison_status="drift"),
            "contract_mismatch_observed": dict(runtime_contract_mismatch_count=1),
            "reviewed_strict_full_contract_fingerprint_missing": dict(reviewed_strict_full_contract_fingerprint=None),
            "no_delegated_reads_exposed": dict(dynamically_exposed_count=0),
            "protocol_not_observed": dict(observed_protocol_version=""),
        }
        for category, changes in negatives.items():
            with self.subTest(category=category):
                status, reason, _ = health_case("admitted_strict_91", health_after=health(**strict_91_view(**changes)))
                self.assertEqual((status, reason), ("failed", category))
        self.assertEqual(health_case("admitted_strict_91", probe=served_probe("8.5.0"))[:2],
                         ("failed", "probe_not_served_by_91"))
        self.assertEqual(health_case("admitted_strict_91", tool_names=[])[:2], ("failed", "probe_tool_not_listed"))
        self.assertEqual(health_case("admitted_strict_91", probe=None)[:2], ("failed", "probe_read_not_useful"))

    def test_legacy_refusal_negatives(self) -> None:
        self.assertEqual(health_case("catalog_mode_unadmitted_at_startup",
                                     peer_requests=[(runners.PEER_SECRET_PATH, 200), (runners.PEER_SECRET_PATH, 202)])[:2],
                         ("failed", "peer_session_established"))
        self.assertEqual(health_case("catalog_mode_unadmitted_at_startup",
                                     peer_requests=[(runners.PEER_SECRET_PATH, 200)] * 4)[:2],
                         ("failed", "peer_requests_exceed_refused_sessions"))
        self.assertEqual(health_case("catalog_mode_unadmitted_at_startup", peer_requests=None)[:2],
                         ("failed", "peer_requests_not_observed"))
        self.assertEqual(health_case("catalog_mode_unadmitted_at_startup",
                                     tool_names=[runners.PROBE_TOOL])[:2],
                         ("failed", "refused_form_listed_delegated_read"))
        # HAMCP-437: the peer-change refusal retired with side_by_side; the startup refusal keeps
        # its served-read and fixture-mutation guards.
        self.assertEqual(health_case("catalog_mode_unadmitted_at_startup", probe=served_probe())[:2],
                         ("failed", "refused_form_served_a_read"))
        self.assertEqual(health_case("catalog_mode_unadmitted_at_startup",
                                     fixture_after={**FIXTURE, "websocket_mutations": 1})[:2],
                         ("failed", "fixture_mutation_observed"))


class GatewayCaseTests(unittest.TestCase):
    def test_each_gateway_case_passes_on_its_observed_contract(self) -> None:
        for configuration, name in runners.GATEWAY_CASES:
            with self.subTest(case=name):
                status, reason, observed = gateway_case(configuration, name)
                self.assertEqual((status, reason), ("passed", ""))
                self.assertEqual(observed["negotiated_protocol"], PROTOCOL)

    def test_gateway_negatives(self) -> None:
        name = ("91_only", "91_parity_read_gateway_acceptance")
        self.assertEqual(gateway_case(*name, source=None)[:2], ("failed", "acceptance_result_missing"))
        self.assertEqual(gateway_case(*name, source={**gateway_source("9.2.0", "admitted"), "result": "FAIL"})[:2],
                         ("failed", "acceptance_result_not_pass"))
        self.assertEqual(gateway_case(*name, health=None)[:2], ("failed", "health_missing"))
        self.assertEqual(gateway_case(*name, source=gateway_source("9.2.0", "absent"))[:2],
                         ("failed", "filesystem_tools_not_on_wire"))
        self.assertEqual(gateway_case("91_only", "91_filesystem_disabled_variant",
                                      source=gateway_source("9.2.0", "admitted"))[:2],
                         ("failed", "filesystem_disabled_tools_on_wire"))
        self.assertEqual(gateway_case(*name, health=gateway_health("8.5.0", "admitted"))[:2],
                         ("failed", "health_version_mismatch"))
        status, reason, _ = runners.evaluate_gateway_case(
            "85_only", "unlisted_case", source={}, configured_url=LEGACY, upstream_endpoint=PEER_LEGACY,
            engineering_version=LANE_VERSION, container_image=None, health=None)
        self.assertEqual((status, reason), ("failed", "gateway_case_unlisted"))


class NonExecutionTests(unittest.TestCase):
    def args(self, directory: str, **overrides) -> argparse.Namespace:
        values = dict(configuration="91_only", case="91_parity_read_gateway_acceptance",
                      source=str(Path(directory) / "missing.json"), engineering_endpoint="http://127.0.0.1:18100/x/mcp",
                      configured_url=STRICT, upstream_endpoint=PEER_STRICT, expected_engineering_version=LANE_VERSION,
                      engineering_container="engineering-admission-91-strict",
                      output=str(Path(directory) / "case.json"))
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_gateway_runner_without_an_acceptance_result_writes_failed_and_never_calls_engineering(self) -> None:
        def session(*_args, **_kwargs):
            raise AssertionError("no acceptance result: Engineering must not be consulted")

        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(runners, "container_image", return_value=runners.LANE_ENGINEERING_IMAGE), \
                redirect_stdout(io.StringIO()):
            args = self.args(directory)
            self.assertEqual(runners.command_gateway_case(args, session=session), 1)
            value = json.loads(Path(args.output).read_text())
        self.assertEqual((value["status"], value["reason"]), ("failed", "acceptance_result_missing"))
        self.assertEqual(value["endpoint_form"], "strict_full")

    def test_health_runner_observation_error_writes_failed(self) -> None:
        async def session(*_args, **_kwargs):
            raise ConnectionRefusedError

        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            output = Path(directory) / "case.json"
            args = argparse.Namespace(
                configuration="91_only", case="91_strict_admission_positive", expect="admitted_strict_91",
                engineering_endpoint="http://127.0.0.1:18100/x/mcp", configured_url=STRICT,
                expected_engineering_version=LANE_VERSION, engineering_container="engineering-admission-91-strict",
                fixture_stats_url=None, peer_container=None, mark=None, wait_seconds=0,
                upstream_endpoint=PEER_STRICT, output=str(output))
            self.assertEqual(runners.command_health_case(args, session=session), 1)
            value = json.loads(output.read_text())
        self.assertEqual(value["status"], "failed")
        self.assertTrue(value["reason"].startswith("observation_failed"))

    def test_runner_never_writes_skipped(self) -> None:
        source = (ROOT / "scripts" / "exact_image_admission_91_runners.py").read_text(encoding="utf-8")
        self.assertNotIn('"skipped"', source)

    def test_unexecuted_case_fails_the_lane(self) -> None:
        for configuration in RUNNER_CONFIGURATIONS:
            for missing in lanes.case_names(configuration):
                results = passing_results(configuration)
                del results[missing]
                with self.subTest(configuration=configuration, missing=missing):
                    manifest = lanes.build_manifest(lane_row(configuration), COMMIT, results)
                    self.assertEqual(manifest["result"], "FAIL")
                    statuses = {case["name"]: case["status"] for case in manifest["cases"]}
                    self.assertEqual(statuses[missing], "not_executed")

    def test_complete_passing_results_pass_the_lane(self) -> None:
        for configuration in RUNNER_CONFIGURATIONS:
            manifest = lanes.build_manifest(lane_row(configuration), COMMIT, passing_results(configuration))
            self.assertEqual(manifest["result"], "PASS")
            self.assertEqual(manifest["executed_count"], len(lanes.case_names(configuration)))

    def test_failed_or_skipped_case_fails_the_lane(self) -> None:
        for status in ("failed", "skipped"):
            results = passing_results("91_only")
            results["91_parity_read_gateway_acceptance"] = lanes.case_result(
                "91_parity_read_gateway_acceptance", status, form="strict_full", reason="synthetic")
            with self.subTest(status=status):
                self.assertEqual(lanes.build_manifest(lane_row("91_only"), COMMIT, results)["result"], "FAIL")

    def test_wrong_endpoint_form_in_a_case_result_is_refused(self) -> None:
        results = passing_results("91_only")
        results["91_strict_admission_positive"] = lanes.case_result(
            "91_strict_admission_positive", "passed", form="legacy", observed={"negotiated_protocol": PROTOCOL})
        with self.assertRaisesRegex(lanes.LaneFailure, "manifest_case_form_mismatch"):
            lanes.build_manifest(lane_row("91_only"), COMMIT, results)

    def test_identity_only_lane_is_never_a_pass(self) -> None:
        for configuration in RUNNER_CONFIGURATIONS:
            results = {name: value for name, value in passing_results(configuration).items()
                       if name.startswith("image_identity_")}
            with self.subTest(configuration=configuration):
                manifest = lanes.build_manifest(lane_row(configuration), COMMIT, results)
                self.assertEqual(manifest["result"], "FAIL")

    def test_dormant_lane_is_not_executed_and_never_accepted(self) -> None:
        for configuration in RUNNER_CONFIGURATIONS:
            manifest = lanes.build_manifest(lane_row(configuration, "dormant"), COMMIT, {})
            self.assertEqual(manifest["result"], "NOT_EXECUTED")
            with self.assertRaisesRegex(lanes.LaneFailure, "manifest_dormant_lane_has_case_results"):
                lanes.build_manifest(lane_row(configuration, "dormant"), COMMIT, passing_results(configuration))

    def test_gate_accepts_a_configuration_only_when_both_architectures_pass(self) -> None:
        rows = [lane_row(configuration, architecture=architecture)
                for configuration in lanes.CASES for architecture in lanes.ARCHITECTURES]
        matrix = {"include": rows}

        def manifests(override):
            values = []
            for row in rows:
                results = passing_results(row["configuration"], row["architecture"])
                state_row = dict(row)
                if (row["configuration"], row["architecture"]) in override:
                    kind = override[(row["configuration"], row["architecture"])]
                    if kind == "dormant":
                        state_row = lane_row(row["configuration"], "dormant", row["architecture"])
                        results = {}
                    elif kind == "identity_only":
                        results = {k: v for k, v in results.items() if k.startswith("image_identity_")}
                    elif kind == "unexecuted":
                        results.pop(lanes.case_names(row["configuration"])[-1])
                values.append(lanes.build_manifest(state_row, COMMIT, results))
            return values

        acceptance, errors = lanes.verify_manifests(manifests({}), matrix, COMMIT)
        self.assertEqual(errors, [])
        for configuration in RUNNER_CONFIGURATIONS:
            self.assertEqual(acceptance[configuration], "ACCEPTED")
        for kind in ("dormant", "identity_only", "unexecuted"):
            for configuration in RUNNER_CONFIGURATIONS:
                with self.subTest(kind=kind, configuration=configuration):
                    acceptance, _ = lanes.verify_manifests(
                        manifests({(configuration, "arm64"): kind}), matrix, COMMIT)
                    self.assertNotEqual(acceptance[configuration], "ACCEPTED")
        for configuration in RUNNER_CONFIGURATIONS:
            dormant_rows = {(configuration, architecture): "dormant" for architecture in lanes.ARCHITECTURES}
            acceptance, _ = lanes.verify_manifests(manifests(dormant_rows), matrix, COMMIT)
            self.assertNotEqual(acceptance[configuration], "ACCEPTED")


class StrictCatalogExactnessTests(unittest.TestCase):
    """Josh, 2026-11-07 (ruling 3): every tool exact on every field, exact set, no duplicates."""

    def strict(self, peer=None, **view):
        tools = peer if peer is not None else peer_tools_91()
        return health_case("admitted_strict_91", peer_tools=tools,
                           health_after=health(**strict_91_view(**view)))[:2]

    def test_reviewed_capture_is_the_exact_9_1_contract(self) -> None:
        self.assertEqual(len(REVIEWED_91), 77)
        self.assertEqual(sorted(tool["name"] for tool in OPTIONAL_91), sorted(runners.OPTIONAL_NAMES))
        self.assertEqual(self.strict(), ("passed", ""))

    def test_every_descriptor_field_of_every_reviewed_tool_is_compared(self) -> None:
        # Not only the automatic-read routes: a write tool, a physical action,
        # and the held canary tool are compared field by field too.
        for name in ("ha_get_state", "ha_restart", "ha_config_set_automation", "ha_get_operation_status"):
            index = next(i for i, tool in enumerate(peer_tools_91()) if tool["name"] == name)
            for field in runners.DESCRIPTOR_FIELDS[1:]:
                with self.subTest(name=name, field=field):
                    peer = peer_tools_91()
                    if field not in peer[index]:
                        continue
                    peer[index][field] = {"changed": True} if isinstance(peer[index][field], dict) else "changed"
                    self.assertEqual(self.strict(peer), ("failed", f"peer_catalog_{field.strip('_')}_mismatch"))

    def test_nested_and_type_level_differences_are_mismatches(self) -> None:
        peer = peer_tools_91()
        tool = next(tool for tool in peer if tool["name"] == "ha_restart")
        tool["annotations"]["destructiveHint"] = 1 if tool["annotations"].get("destructiveHint") is True else True
        self.assertEqual(self.strict(peer), ("failed", "peer_catalog_annotations_mismatch"))
        peer = peer_tools_91()
        tool = next(tool for tool in peer if tool["name"] == "ha_get_state")
        tool["inputSchema"].setdefault("properties", {})["extra"] = {"type": "string"}
        self.assertEqual(self.strict(peer), ("failed", "peer_catalog_inputSchema_mismatch"))
        peer = peer_tools_91()
        tool = next(tool for tool in peer if tool["name"] == "ha_get_state")
        tool["description"] += " "
        self.assertEqual(self.strict(peer), ("failed", "peer_catalog_description_mismatch"))

    def test_added_or_removed_descriptor_keys_are_mismatches(self) -> None:
        peer = peer_tools_91()
        peer[0]["icons"] = []
        self.assertEqual(self.strict(peer), ("failed", "peer_catalog_descriptor_keys_mismatch"))
        peer = peer_tools_91()
        tool = next(tool for tool in peer if "outputSchema" in tool and tool["name"] in
                    {item["name"] for item in REVIEWED_91})
        del tool["outputSchema"]
        self.assertEqual(self.strict(peer), ("failed", "peer_catalog_descriptor_keys_mismatch"))

    def test_set_must_equal_reviewed_plus_exactly_the_optional_tools(self) -> None:
        peer = [tool for tool in peer_tools_91() if tool["name"] != "ha_restart"]
        self.assertEqual(self.strict(peer), ("failed", "peer_catalog_missing_reviewed_tool"))
        peer = peer_tools_91()
        next(tool for tool in peer if tool["name"] == "ha_get_state")["name"] = "ha_get_state_v2"
        self.assertEqual(self.strict(peer), ("failed", "peer_catalog_missing_reviewed_tool"))
        peer = peer_tools_91() + [{**OPTIONAL_91[0], "name": "ha_unreviewed_tool"}]
        self.assertEqual(self.strict(peer), ("failed", "peer_catalog_extra_tool"))
        peer = [tool for tool in peer_tools_91() if tool["name"] != "ha_write_file"]
        self.assertEqual(self.strict(peer), ("failed", "peer_catalog_optional_tool_missing"))
        peer = [tool for tool in peer_tools_91() if tool["name"] not in runners.OPTIONAL_NAMES]
        self.assertEqual(self.strict(peer), ("failed", "peer_catalog_optional_tool_missing"))

    def test_duplicates_fail(self) -> None:
        for name in ("ha_get_state", "ha_restart", runners.YAML_READER, "ha_delete_file"):
            with self.subTest(name=name):
                peer = peer_tools_91()
                peer.append(json.loads(json.dumps(next(tool for tool in peer if tool["name"] == name))))
                self.assertEqual(self.strict(peer), ("failed", "peer_catalog_duplicate_tool"))

    def test_wire_order_is_not_bound_but_the_observation_is(self) -> None:
        # D1 (#407 correction round): the comparison is an order-insensitive
        # exact set; the optional tools may sit anywhere in the wire order.
        peer = peer_tools_91()
        moved = [tool for tool in peer if tool["name"] in runners.OPTIONAL_NAMES]
        peer = [tool for tool in peer if tool["name"] not in runners.OPTIONAL_NAMES] + moved
        self.assertEqual(self.strict(peer, observed_strict_full_contract_fingerprint=runners.canonical_fingerprint(
            {"tools": peer})), ("passed", ""))
        self.assertEqual(self.strict(observed_strict_full_contract_fingerprint="8" * 64),
                         ("failed", "peer_catalog_not_engineering_observation"))

    def test_unreadable_or_unobserved_catalog_fails(self) -> None:
        self.assertEqual(health_case("admitted_strict_91", peer_tools=None)[:2], ("failed", "peer_catalog_unobserved"))
        self.assertEqual(health_case("admitted_strict_91", reviewed_tools=None)[:2],
                         ("failed", "reviewed_capture_missing"))
        self.assertEqual(self.strict([{"name": 1}]), ("failed", "peer_catalog_unreadable"))
        self.assertEqual(self.strict({"tools": []}), ("failed", "peer_catalog_unreadable"))

    def test_runtime_completeness_fields_are_required(self) -> None:
        negatives = {
            "contract_mismatch_observed": [dict(schema_mismatch_count=1), dict(description_semantics_mismatch_count=1),
                                           dict(annotation_mismatch_count=1), dict(output_contract_mismatch_count=1),
                                           dict(schema_mismatched_automatic_read_count=1)],
            "automatic_reads_not_all_exact": [dict(exact_matched_automatic_read_count=26),
                                              dict(accounted_automatic_read_count=26),
                                              dict(automatic_read_accounting_valid=False),
                                              dict(admission_complete=False), dict(reviewed_automatic_read_count=0),
                                              dict(policy_classifications={"automatic_read": 26})],
            "automatic_read_quarantined": [dict(quarantined_automatic_read_count=1),
                                           dict(quarantine_reason_counts={"catalog_order_mismatch": 27})],
            "reviewed_tool_missing": [dict(missing_reviewed_read_count=1), dict(missing_automatic_read_count=1)],
            "unreviewed_tool_observed": [dict(unreviewed_tool_count=1), dict(unreviewed_observed_tool_count=1)],
            "catalog_diff_observed": [dict(catalog_diff_field_counts={"/tool_descriptor_count": 1})],
            "optional_tools_not_reviewed_exact": [
                dict(optional_sibling_drift=[["ha_write_file", "descriptor"]]),
                dict(optional_reviewed_accounting_valid=False),
                dict(optional_tools={runners.YAML_READER: "quarantined:descriptor_mismatch",
                                     **{name: "prohibited_withheld" for name in runners.YAML_SIBLINGS}}),
                dict(optional_tools={runners.YAML_READER: "admitted", "ha_write_file": "admitted"})],
            "gateway_safety_flags_unexpected": [dict(writes_allowed=True), dict(direct_ha_fallback_allowed=True),
                                                dict(stock_catalog_match_is_informational=False)],
            "catalog_size_not_reviewed": [dict(upstream_advertised_tool_count=83), dict(reviewed_accounted_tool_count=76),
                                          dict(reviewed_policy_entry_count=78)],
            "no_delegated_reads_exposed": [dict(dynamically_exposed_count=27), dict(core_withheld_read_count=1)],
        }
        for category, changes in negatives.items():
            for change in changes:
                with self.subTest(category=category, change=change):
                    self.assertEqual(self.strict(**change), ("failed", category))

    def test_load_reviewed_capture_fails_closed(self) -> None:
        capture = json.loads(runners.REVIEWED_91_CAPTURE.read_text(encoding="utf-8"))
        bad = {
            "reviewed_capture_not_91": {**capture, "server_version": "8.5.0"},
            "reviewed_capture_unreadable": {**capture, "tool_count": 76},
            "reviewed_capture_invalid": {**capture, "tools": capture["tools"] + [capture["tools"][0]],
                                         "tool_count": 78},
        }
        with tempfile.TemporaryDirectory() as directory:
            for category, value in bad.items():
                with self.subTest(category=category):
                    path = Path(directory) / "capture.json"
                    path.write_text(json.dumps(value))
                    with self.assertRaisesRegex(runners.LaneFailure, category):
                        runners.load_reviewed_91_capture(path)

    def test_health_runner_lists_the_peer_only_for_strict_admission(self) -> None:
        async def session(_endpoint, *, probe):
            return health(**strict_91_view()), [runners.PROBE_TOOL], served_probe() if probe else None

        listed = []

        async def catalog(endpoint):
            listed.append(endpoint)
            return peer_tools_91()

        def args(directory, **overrides):
            values = dict(configuration="91_only", case="91_strict_admission_positive", expect="admitted_strict_91",
                          engineering_endpoint="http://127.0.0.1:18100/x/mcp", configured_url=STRICT,
                          expected_engineering_version=LANE_VERSION,
                          engineering_container="engineering-admission-91-strict",
                          fixture_stats_url=None, peer_container=None, mark=None, wait_seconds=0,
                          upstream_endpoint=PEER_STRICT, output=str(Path(directory) / "case.json"))
            values.update(overrides)
            return argparse.Namespace(**values)

        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()), \
                mock.patch.object(runners, "container_image", return_value=runners.LANE_ENGINEERING_IMAGE), \
                mock.patch.object(runners, "snapshot", return_value={}):
            self.assertEqual(runners.command_health_case(args(directory), session=session, catalog=catalog), 0)
            self.assertEqual(listed, [PEER_STRICT])
            for overrides, reason in ((dict(upstream_endpoint=None), "peer_endpoint_missing"),
                                      (dict(upstream_endpoint=PEER_LEGACY), "endpoint_form_mismatch"),
                                      (dict(upstream_endpoint=STRICT.replace("abcdef12-ha-mcp", "example.com")),
                                       "endpoint_not_loopback_peer")):
                with self.subTest(reason=reason):
                    self.assertEqual(runners.command_health_case(args(directory, **overrides), session=session,
                                                                 catalog=catalog), 1)
                    value = json.loads(Path(args(directory).output).read_text())
                    self.assertEqual((value["status"], value["reason"]), ("failed", reason))
            self.assertEqual(listed, [PEER_STRICT])

    def test_workflow_lists_the_strict_peer_with_the_filesystem_tools_enabled(self) -> None:
        steps = workflow_job()["steps"]
        step = next(step for step in steps if "--expect admitted_strict_91" in step.get("run", ""))
        self.assertIn('--upstream-endpoint "$PEER_STRICT_FULL_URL"', step["run"])
        index = steps.index(step)
        peers = [s.get("run", "") for s in steps[:index + 1] if "start-peer" in s.get("run", "")
                 and "91_only" in str(s.get("if", ""))]
        self.assertTrue(peers and "--filesystem enabled" in peers[-1])
        self.assertEqual([s for s in steps if "--upstream-endpoint" in s.get("run", "")
                          and "health-case" in s.get("run", "")], [step])


class StrictCatalogOrderInsensitiveTests(unittest.TestCase):
    """D1: the catalog is an exact, order-insensitive set; fingerprints are diagnostic, never a gate."""

    def strict(self, peer, **view):
        view.setdefault("observed_strict_full_contract_fingerprint", runners.canonical_fingerprint({"tools": peer}))
        return health_case("admitted_strict_91", peer_tools=peer, health_after=health(**strict_91_view(**view)))

    def test_reordered_identical_catalog_passes(self) -> None:
        peer = peer_tools_91()
        peer[0], peer[1] = peer[1], peer[0]
        self.assertEqual(sorted(peer, key=lambda tool: tool["name"]),
                         sorted(peer_tools_91(), key=lambda tool: tool["name"]))
        self.assertEqual(self.strict(peer)[:2], ("passed", ""))
        self.assertEqual(self.strict(list(reversed(peer_tools_91())))[:2], ("passed", ""))

    def test_reordering_does_not_hide_missing_extra_duplicate_or_drift(self) -> None:
        base = list(reversed(peer_tools_91()))
        cases = {
            "peer_catalog_missing_reviewed_tool": [tool for tool in base if tool["name"] != "ha_restart"],
            "peer_catalog_extra_tool": base + [{**OPTIONAL_91[0], "name": "ha_unreviewed_tool"}],
            "peer_catalog_duplicate_tool": base + [json.loads(json.dumps(base[3]))],
            "peer_catalog_optional_tool_missing": [tool for tool in base if tool["name"] != "ha_write_file"],
        }
        drift = json.loads(json.dumps(base))
        next(tool for tool in drift if tool["name"] == "ha_get_state")["description"] += " changed"
        cases["peer_catalog_description_mismatch"] = drift
        keys = json.loads(json.dumps(base))
        next(tool for tool in keys if tool["name"] == "ha_get_state")["icons"] = []
        cases["peer_catalog_descriptor_keys_mismatch"] = keys
        for reason, peer in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(self.strict(peer)[:2], ("failed", reason))

    def test_runner_observation_must_still_be_the_engineering_observation(self) -> None:
        peer = list(reversed(peer_tools_91()))
        stale = runners.canonical_fingerprint({"tools": peer_tools_91()})
        self.assertEqual(self.strict(peer, observed_strict_full_contract_fingerprint=stale)[:2],
                         ("failed", "peer_catalog_not_engineering_observation"))

    def test_reviewed_fingerprint_is_recorded_not_gated(self) -> None:
        status, reason, observed = self.strict(peer_tools_91(), reviewed_strict_full_contract_fingerprint="5" * 64)
        self.assertEqual((status, reason), ("passed", ""))
        self.assertEqual(observed["reviewed_strict_full_contract_fingerprint"], "5" * 64)
        self.assertIs(observed["peer_catalog"]["reviewed_capture_fingerprint_matches_runtime"], False)
        _, _, observed = self.strict(peer_tools_91())
        self.assertIs(observed["peer_catalog"]["reviewed_capture_fingerprint_matches_runtime"], True)


if __name__ == "__main__":
    unittest.main()
