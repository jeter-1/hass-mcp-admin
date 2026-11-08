"""HAMCP-319 section 5.1 case runners for the 91_only and side_by_side lanes (offline).

Exercises ``scripts/exact_image_admission_91_runners.py`` and the lane job in
``.github/workflows/ci.yml`` without Docker, network or any live system:

* every listed case of each active configuration is produced by a workflow
  step conditioned on that configuration, exactly once, by a real runner;
* the 9.1 positive endpoint is the full ``/synthetic-upstream-secret/mcp``
  path followed by ``/catalog/full`` and the short form is refused;
* the Engineering rollback stage runs the previous release 2.4.2-beta.6 by
  its published index digest, and only the rollback cases may run it;
* a runner that does not execute, a wrong endpoint form, and a dormant or
  identity-only lane never produce a passing lane or an ACCEPTED gate.
"""

from __future__ import annotations

import argparse
import hashlib
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
RUNNER_CONFIGURATIONS = ("91_only", "side_by_side")
STRICT = "http://abcdef12-ha-mcp:18086/synthetic-upstream-secret/mcp/catalog/full"
SHORT_STRICT = "http://abcdef12-ha-mcp:18086/synthetic-upstream-secret/catalog/full"
LEGACY = "http://abcdef12-ha-mcp:18086/synthetic-upstream-secret/mcp"
PEER_STRICT = "http://127.0.0.1:18086/synthetic-upstream-secret/mcp/catalog/full"
PEER_LEGACY = "http://127.0.0.1:18086/synthetic-upstream-secret/mcp"
LANE_VERSION = "2.4.2-beta.7"
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
    return produced


def health(version: str = LANE_VERSION, **view) -> dict:
    return {"success": True, "data": {"server": {"version": version}, "upstream_read_gateway": view}}


def strict_91_view(**overrides) -> dict:
    view = {
        "initialized": True, "admission_status": "admitted_exact", "version_status": "reviewed_exact",
        "observed_identity_status": "accepted", "observed_upstream_server_version": "9.1.0",
        "selected_compatibility_entry_id": "ha-mcp-v9.1.0-reviewed",
        "reviewed_strict_full_contract_fingerprint": "5" * 64,
        "observed_strict_full_contract_fingerprint": "8" * 64,
        "catalog_admission_mode": "reviewed_per_tool_contract_subset",
        "catalog_comparison_status": "exact", "runtime_contract_mismatch_count": 0,
        "output_contract_mismatch_count": 0, "reviewed_tool_accounting_valid": True,
        "dynamically_exposed_count": 28, "observed_protocol_version": PROTOCOL,
    }
    view.update(overrides)
    return view


def served_probe(version: str = "9.1.0") -> dict:
    return {"success": True, "metadata": {"provider": "upstream_read_gateway", "upstream_version": version,
                                          "upstream_dispatch_occurred": True}}


def refused_probe() -> dict:
    return {"success": False, "error_code": "upstream_unavailable",
            "details": {"failure_category": runners.CATALOG_MODE_UNADMITTED},
            "metadata": {"upstream_dispatch_occurred": False}}


def rollback_identity_ok(architecture: str = "amd64") -> dict:
    return {"status": "passed", "architecture": architecture, "image": runners.ROLLBACK_ENGINEERING_IMAGE,
            "image_digest": runners.ROLLBACK_ENGINEERING_INDEX_DIGEST,
            "manifest_digest": runners.ROLLBACK_ENGINEERING_PLATFORM_DIGESTS[architecture],
            "config_digest": "sha256:" + "c" * 64, "version": runners.ROLLBACK_ENGINEERING_VERSION,
            "revision": runners.ROLLBACK_ENGINEERING_REVISION}


def rollback_view(**overrides) -> dict:
    view = {
        "initialized": False, "admission_status": "blocked_incompatible_upstream",
        "last_failure_category": runners.VERSION_MISMATCH, "observed_upstream_server_version": "9.1.0",
        "observed_identity_status": "rejected", "version_status": "rejected_unreviewed",
        "reviewed_supported_versions": ["8.5.0"], "dynamically_exposed_count": 0,
    }
    view.update(overrides)
    return view


FIXTURE = {"rest_reads": 10, "websocket_reads": 4, "http_mutations": 0, "websocket_mutations": 0}


def health_case(expect: str, **overrides):
    configuration, name = runners.EXPECTATIONS[expect]
    rollback = name in runners.ROLLBACK_CASES
    arguments = dict(
        configured_url=STRICT if lanes.case_form(configuration, name) == "strict_full" else LEGACY,
        engineering_version=runners.ROLLBACK_ENGINEERING_VERSION if rollback else LANE_VERSION,
        container_image=runners.ROLLBACK_ENGINEERING_IMAGE if rollback else runners.LANE_ENGINEERING_IMAGE,
        rollback_identity=rollback_identity_ok() if rollback else None,
        tool_names=[], probe=refused_probe(), fixture_before=dict(FIXTURE), fixture_after=dict(FIXTURE),
        peer_requests=[],
    )
    if expect == "admitted_strict_91":
        arguments.update(health_before=health(**strict_91_view()), health_after=health(**strict_91_view()),
                         tool_names=[runners.PROBE_TOOL], probe=served_probe(), peer_requests=None)
    elif expect == "catalog_mode_unadmitted_at_startup":
        view = {"initialized": False, "admission_status": "pending_discovery", "dynamically_exposed_count": 0,
                "observed_upstream_server_version": None, "discovery_attempt_count": 2,
                "last_discovery_failure_category": runners.CATALOG_MODE_UNADMITTED,
                "operator_remediation": "set the value ending in /catalog/full"}
        arguments.update(health_before=health(**view), health_after=health(**view),
                         peer_requests=[(runners.PEER_SECRET_PATH, 200)] * 3)
    elif expect == "catalog_mode_unadmitted_after_peer_change":
        before = {"discovery_attempt_count": 1}
        after = {"discovery_attempt_count": 2, "last_call_failure_category": runners.CATALOG_MODE_UNADMITTED,
                 "operator_remediation": "set the value ending in /catalog/full"}
        arguments.update(health_before=health(**before), health_after=health(**after),
                         tool_names=[runners.PROBE_TOOL], peer_requests=[(runners.PEER_SECRET_PATH, 200)] * 2)
    else:
        view = rollback_view()
        version = runners.ROLLBACK_ENGINEERING_VERSION
        arguments.update(health_before=health(version, **view), health_after=health(version, **view))
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
    rollback = name in runners.ROLLBACK_CASES
    form = lanes.case_form(configuration, name)
    engineering = runners.ROLLBACK_ENGINEERING_VERSION if rollback else LANE_VERSION
    arguments = dict(
        source=gateway_source(version, optional),
        configured_url=STRICT if form == "strict_full" else LEGACY,
        upstream_endpoint=PEER_STRICT if form == "strict_full" else PEER_LEGACY,
        engineering_version=engineering,
        container_image=runners.ROLLBACK_ENGINEERING_IMAGE if rollback else runners.LANE_ENGINEERING_IMAGE,
        rollback_identity=rollback_identity_ok() if rollback else None,
        health=gateway_health(version, optional, engineering),
    )
    arguments.update(overrides)
    return runners.evaluate_gateway_case(configuration, name, **arguments)


PEER_VERSIONS = {"baseline": "8.5.0", "candidate": "9.1.0"}


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
                          "engineering-admission-91-fs", "engineering-admission-sbs", "exact-admission-upstream"):
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
        status, reason, _ = gateway_case("91_only", "91_parity_dashboard_gateway_acceptance",
                                         configured_url=SHORT_STRICT)
        self.assertEqual((status, reason), ("failed", "endpoint_path_not_lane_value"))
        status, reason, _ = gateway_case("side_by_side", "sbs_stage_b2_91_strict_full_useful",
                                         upstream_endpoint="http://127.0.0.1:18086/synthetic-upstream-secret/catalog/full")
        self.assertEqual((status, reason), ("failed", "endpoint_path_not_lane_value"))

    def test_wrong_endpoint_form_for_the_case_fails(self) -> None:
        status, reason, _ = health_case("admitted_strict_91", configured_url=LEGACY)
        self.assertEqual((status, reason), ("failed", "endpoint_form_mismatch"))
        status, reason, _ = gateway_case("side_by_side", "sbs_stage_a_85_legacy_useful",
                                         configured_url=STRICT, upstream_endpoint=PEER_STRICT)
        self.assertEqual((status, reason), ("failed", "endpoint_form_mismatch"))

    def test_engineering_command_refuses_the_short_form_and_unlisted_inputs(self) -> None:
        base = dict(name="engineering-admission-x", image=runners.LANE_ENGINEERING_IMAGE, data_dir="/tmp/d",
                    mcp_port=18100, approval_port=18110)
        command = runners.engineering_command(upstream_url=STRICT, **base)
        self.assertIn(f"UPSTREAM_DASHBOARD_MCP_URL={STRICT}", command)
        self.assertEqual(command[-1], runners.LANE_ENGINEERING_IMAGE)
        cases = {
            "engineering_upstream_url_not_lane_value": dict(upstream_url=SHORT_STRICT),
            "engineering_image_not_allowed": dict(upstream_url=STRICT, image="ghcr.io/jeter-1/hass-mcp-engineering-beta:2.4.2-beta.6"),
            "engineering_container_name_invalid": dict(upstream_url=STRICT, name="other-container"),
            "engineering_port_out_of_range": dict(upstream_url=STRICT, mcp_port=8099),
        }
        for category, changes in cases.items():
            with self.subTest(category=category):
                arguments = {**base, **changes}
                with self.assertRaisesRegex(lanes.LaneFailure, category):
                    runners.engineering_command(**arguments)


class PeerCommandTests(unittest.TestCase):
    def row(self, version: str = "9.1.0") -> dict:
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
        row["upstream_image"] = "ghcr.io/homeassistant-ai/ha-mcp:9.1.0"
        with self.assertRaisesRegex(lanes.LaneFailure, "peer_row_not_digest_pinned"):
            runners.peer_command(row, "candidate", filesystem="enabled")
        with self.assertRaisesRegex(lanes.LaneFailure, "peer_row_version_unexpected"):
            runners.peer_command(self.row("8.5.0"), "candidate", filesystem="enabled")
        with self.assertRaisesRegex(lanes.LaneFailure, "peer_filesystem_toggle_candidate_only"):
            runners.peer_command(self.row("8.5.0"), "baseline", filesystem="disabled")

    def test_workflow_starts_peers_only_from_the_lane_row(self) -> None:
        for step in workflow_job()["steps"]:
            text = str(step.get("run", ""))
            if "start-peer" in text:
                with self.subTest(step=step.get("name")):
                    self.assertIn('--peers "$LANE_PEERS"', text)
                    self.assertNotRegex(text, r"ghcr\.io/homeassistant-ai")


class RollbackStageTests(unittest.TestCase):
    def test_workflow_pins_the_previous_release_by_its_published_index_digest(self) -> None:
        env = workflow_job()["env"]
        self.assertEqual(env["ROLLBACK_ENGINEERING_IMAGE"], runners.ROLLBACK_ENGINEERING_IMAGE)
        self.assertEqual(env["ROLLBACK_ENGINEERING_VERSION"], runners.ROLLBACK_ENGINEERING_VERSION)
        self.assertEqual(runners.ROLLBACK_ENGINEERING_VERSION, "2.4.2-beta.6")
        self.assertRegex(runners.ROLLBACK_ENGINEERING_IMAGE,
                         r"^ghcr\.io/jeter-1/hass-mcp-engineering-beta@sha256:[0-9a-f]{64}$")
        self.assertEqual(set(runners.ROLLBACK_ENGINEERING_PLATFORM_DIGESTS), {"amd64", "arm64"})
        self.assertRegex(runners.ROLLBACK_ENGINEERING_REVISION, r"^[0-9a-f]{40}$")

    def test_rollback_cases_are_listed_side_by_side_cases(self) -> None:
        self.assertLessEqual(runners.ROLLBACK_CASES, set(lanes.case_names("side_by_side")))
        self.assertEqual(runners.ROLLBACK_CASES,
                         {"sbs_rollback_85_legacy_useful", "sbs_engineering_rollback_91_refused"})
        # No "current build minus the 9.1 entry" variant is listed or counted.
        for configuration in lanes.CASES:
            for name in lanes.case_names(configuration):
                self.assertNotIn("minus", name)

    def test_rollback_steps_run_the_pinned_image_with_its_verified_identity(self) -> None:
        steps = {step.get("name"): str(step.get("run", "")) for step in workflow_job()["steps"]}
        identity = steps["Verify the previous Engineering release by digest"]
        self.assertIn("rollback-identity", identity)
        for name, case in (("Case side-by-side rollback, previous release 2.4.2-beta.6 with 8.5",
                            "sbs_rollback_85_legacy_useful"),
                           ("Case side-by-side previous release refuses 9.1 on the strict-full value",
                            "sbs_engineering_rollback_91_refused")):
            with self.subTest(case=case):
                text = steps[name]
                self.assertIn('--image "$ROLLBACK_ENGINEERING_IMAGE"', text)
                self.assertIn('--expected-engineering-version "$ROLLBACK_ENGINEERING_VERSION"', text)
                self.assertIn("--rollback-identity", text)
                self.assertIn(f"--case {case}", text)
                self.assertIn('--data-dir "$RUNNER_TEMP/admission-data-sbs"', text)
        rollback = steps["Case side-by-side rollback, previous release 2.4.2-beta.6 with 8.5"]
        self.assertIn("--role baseline", rollback)
        self.assertIn('--upstream-url "$LEGACY_UPSTREAM_URL"', rollback)
        for name, text in steps.items():
            if "ROLLBACK_ENGINEERING_IMAGE" in text:
                self.assertIn(name, ("Case side-by-side rollback, previous release 2.4.2-beta.6 with 8.5",
                                     "Case side-by-side previous release refuses 9.1 on the strict-full value"))

    def test_rollback_acceptance_passes_only_on_the_previous_release(self) -> None:
        status, reason, observed = gateway_case("side_by_side", "sbs_rollback_85_legacy_useful")
        self.assertEqual((status, reason), ("passed", ""))
        self.assertEqual(observed["engineering_version"], "2.4.2-beta.6")
        self.assertEqual(observed["engineering_image"], runners.ROLLBACK_ENGINEERING_IMAGE)
        negatives = {
            "rollback_case_not_previous_release": dict(engineering_version=LANE_VERSION,
                                                       health=gateway_health("8.5.0", None, LANE_VERSION)),
            "engineering_image_not_case_image": dict(container_image=runners.LANE_ENGINEERING_IMAGE),
            "rollback_identity_not_verified": dict(rollback_identity=None),
            "engineering_version_mismatch": dict(health=gateway_health("8.5.0", None, LANE_VERSION)),
        }
        for category, changes in negatives.items():
            with self.subTest(category=category):
                status, reason, _ = gateway_case("side_by_side", "sbs_rollback_85_legacy_useful", **changes)
                self.assertEqual((status, reason), ("failed", category))

    def test_rollback_refusal_of_91_requires_the_previous_release_and_no_reads(self) -> None:
        self.assertEqual(health_case("unsupported_version_rollback")[:2], ("passed", ""))
        negatives = {
            "rollback_engineering_has_91_entry": dict(health_after=health(
                runners.ROLLBACK_ENGINEERING_VERSION, **rollback_view(reviewed_supported_versions=["8.5.0", "9.1.0"]))),
            "rollback_not_version_mismatch": dict(health_after=health(
                runners.ROLLBACK_ENGINEERING_VERSION, **rollback_view(last_failure_category=None))),
            "fixture_read_observed": dict(fixture_after={**FIXTURE, "rest_reads": 11}),
            "fixture_mutation_observed": dict(fixture_after={**FIXTURE, "http_mutations": 1}),
            "engineering_image_not_case_image": dict(container_image=runners.LANE_ENGINEERING_IMAGE),
            "rollback_identity_mismatch": dict(rollback_identity={**rollback_identity_ok(), "manifest_digest":
                                                                  runners.ROLLBACK_ENGINEERING_PLATFORM_DIGESTS["arm64"]}),
        }
        for category, changes in negatives.items():
            with self.subTest(category=category):
                self.assertEqual(health_case("unsupported_version_rollback", **changes)[:2], ("failed", category))

    def test_previous_release_outside_rollback_cases_is_refused(self) -> None:
        status, reason, _ = gateway_case("side_by_side", "sbs_stage_a_again_85_legacy_useful",
                                         engineering_version=runners.ROLLBACK_ENGINEERING_VERSION,
                                         health=gateway_health("8.5.0", None, runners.ROLLBACK_ENGINEERING_VERSION))
        self.assertEqual((status, reason), ("failed", "previous_release_outside_rollback_case"))
        status, reason, _ = gateway_case("side_by_side", "sbs_stage_a_again_85_legacy_useful",
                                         container_image=runners.ROLLBACK_ENGINEERING_IMAGE)
        self.assertEqual((status, reason), ("failed", "engineering_image_not_case_image"))

    def test_rollback_identity_problem_checks_every_pinned_field(self) -> None:
        self.assertIsNone(runners.rollback_identity_problem(rollback_identity_ok("amd64")))
        self.assertIsNone(runners.rollback_identity_problem(rollback_identity_ok("arm64")))
        self.assertEqual(runners.rollback_identity_problem({**rollback_identity_ok(), "status": "failed"}),
                         "rollback_identity_not_verified")
        for key, value in (("image", "ghcr.io/jeter-1/hass-mcp-engineering-beta:2.4.2-beta.6"),
                           ("image_digest", "sha256:" + "0" * 64), ("version", LANE_VERSION),
                           ("revision", "0" * 40), ("architecture", "riscv64")):
            with self.subTest(key=key):
                self.assertEqual(runners.rollback_identity_problem({**rollback_identity_ok(), key: value}),
                                 "rollback_identity_mismatch")

    def test_rollback_identity_requires_registry_index_bytes_to_hash_to_the_pin(self) -> None:
        def no_run(argv):
            raise AssertionError("must not pull after an index mismatch")

        with self.assertRaisesRegex(lanes.LaneFailure, "rollback_index_bytes_mismatch"):
            runners.rollback_identity("amd64", no_run, raw_index=lambda: b'{"manifests": []}')
        with self.assertRaisesRegex(lanes.LaneFailure, "rollback_architecture_unpinned"):
            runners.rollback_identity("riscv64", no_run, raw_index=lambda: b"")

    def test_rollback_identity_verifies_platform_labels_and_architecture(self) -> None:
        platforms = {"amd64": "sha256:" + "1" * 64, "arm64": "sha256:" + "2" * 64}
        raw = json.dumps({"manifests": [
            {"digest": platforms["amd64"], "platform": {"os": "linux", "architecture": "amd64"}},
            {"digest": platforms["arm64"], "platform": {"os": "linux", "architecture": "arm64"}},
            {"digest": "sha256:" + "3" * 64, "platform": {"os": "unknown", "architecture": "unknown"}},
        ]}).encode()
        index = "sha256:" + hashlib.sha256(raw).hexdigest()
        image = f"{runners.ROLLBACK_ENGINEERING_REPOSITORY}@{index}"
        labels = {"org.opencontainers.image.version": runners.ROLLBACK_ENGINEERING_VERSION,
                  "org.opencontainers.image.revision": runners.ROLLBACK_ENGINEERING_REVISION}

        def run(argv, labels=labels, arch="amd64"):
            if argv[:4] == ["docker", "buildx", "imagetools", "inspect"] and argv[-1] != "--raw":
                return f"Name: {argv[4]}\nDigest: {index}\n"
            if argv[-1] == "--raw" and argv[4] == image:
                return raw.decode()
            if argv[-1] == "--raw":
                return json.dumps({"config": {"digest": "sha256:" + "4" * 64}})
            if argv[:2] == ["docker", "pull"]:
                return ""
            if "{{json .Config.Labels}}" in argv:
                return json.dumps(labels)
            if "{{.Architecture}}" in argv:
                return arch + "\n"
            raise AssertionError(argv)

        with mock.patch.multiple(runners, ROLLBACK_ENGINEERING_INDEX_DIGEST=index, ROLLBACK_ENGINEERING_IMAGE=image,
                                 ROLLBACK_ENGINEERING_PLATFORM_DIGESTS=platforms):
            result = runners.rollback_identity("amd64", run, raw_index=lambda: raw)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["manifest_digest"], platforms["amd64"])
            with self.assertRaisesRegex(lanes.LaneFailure, "identity_version_label_mismatch"):
                runners.rollback_identity("amd64", lambda argv: run(argv, labels={
                    **labels, "org.opencontainers.image.version": LANE_VERSION}), raw_index=lambda: raw)
            with self.assertRaisesRegex(lanes.LaneFailure, "identity_local_architecture_mismatch"):
                runners.rollback_identity("amd64", lambda argv: run(argv, arch="arm64"), raw_index=lambda: raw)

    def test_rollback_identity_command_writes_failed_on_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "identity.json"
            args = argparse.Namespace(architecture="amd64", output=str(output))
            with mock.patch.object(runners, "rollback_identity", side_effect=lanes.LaneFailure("rollback_index_bytes_mismatch")), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(runners.command_rollback_identity(args), 1)
            value = json.loads(output.read_text())
            self.assertEqual(value["status"], "failed")
            self.assertIsNotNone(runners.rollback_identity_problem(value))


class HealthCaseTests(unittest.TestCase):
    def test_each_expectation_passes_on_its_observed_contract(self) -> None:
        for expect in runners.EXPECTATIONS:
            with self.subTest(expect=expect):
                self.assertEqual(health_case(expect)[:2], ("passed", ""))

    def test_strict_admission_records_one_protocol_and_both_fingerprints(self) -> None:
        _, _, observed = health_case("admitted_strict_91")
        self.assertEqual(observed["negotiated_protocol"], PROTOCOL)
        self.assertEqual(observed["reviewed_strict_full_contract_fingerprint"], "5" * 64)
        self.assertEqual(observed["observed_strict_full_contract_fingerprint"], "8" * 64)

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
        self.assertEqual(health_case("catalog_mode_unadmitted_after_peer_change",
                                     fixture_after={**FIXTURE, "websocket_reads": 5})[:2],
                         ("failed", "fixture_read_observed"))
        self.assertEqual(health_case("catalog_mode_unadmitted_after_peer_change",
                                     probe={**refused_probe(), "metadata": {"upstream_dispatch_occurred": True}})[:2],
                         ("failed", "probe_dispatched_upstream"))
        self.assertEqual(health_case("catalog_mode_unadmitted_after_peer_change", probe=served_probe())[:2],
                         ("failed", "refused_form_served_a_read"))


class GatewayCaseTests(unittest.TestCase):
    def test_each_gateway_case_passes_on_its_observed_contract(self) -> None:
        for configuration, name in runners.GATEWAY_CASES:
            with self.subTest(case=name):
                status, reason, observed = gateway_case(configuration, name)
                self.assertEqual((status, reason), ("passed", ""))
                self.assertEqual(observed["negotiated_protocol"], PROTOCOL)

    def test_gateway_negatives(self) -> None:
        name = ("91_only", "91_parity_dashboard_gateway_acceptance")
        self.assertEqual(gateway_case(*name, source=None)[:2], ("failed", "acceptance_result_missing"))
        self.assertEqual(gateway_case(*name, source={**gateway_source("9.1.0", "admitted"), "result": "FAIL"})[:2],
                         ("failed", "acceptance_result_not_pass"))
        self.assertEqual(gateway_case(*name, health=None)[:2], ("failed", "health_missing"))
        self.assertEqual(gateway_case(*name, source=gateway_source("9.1.0", "absent"))[:2],
                         ("failed", "filesystem_tools_not_on_wire"))
        self.assertEqual(gateway_case("91_only", "91_filesystem_disabled_variant",
                                      source=gateway_source("9.1.0", "admitted"))[:2],
                         ("failed", "filesystem_disabled_tools_on_wire"))
        self.assertEqual(gateway_case(*name, health=gateway_health("8.5.0", "admitted"))[:2],
                         ("failed", "health_version_mismatch"))
        status, reason, _ = runners.evaluate_gateway_case(
            "85_only", "unlisted_case", source={}, configured_url=LEGACY, upstream_endpoint=PEER_LEGACY,
            engineering_version=LANE_VERSION, container_image=None, rollback_identity=None, health=None)
        self.assertEqual((status, reason), ("failed", "gateway_case_unlisted"))


class NonExecutionTests(unittest.TestCase):
    def args(self, directory: str, **overrides) -> argparse.Namespace:
        values = dict(configuration="91_only", case="91_parity_dashboard_gateway_acceptance",
                      source=str(Path(directory) / "missing.json"), engineering_endpoint="http://127.0.0.1:18100/x/mcp",
                      configured_url=STRICT, upstream_endpoint=PEER_STRICT, expected_engineering_version=LANE_VERSION,
                      engineering_container="engineering-admission-91-strict", rollback_identity=None,
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
                rollback_identity=None, fixture_stats_url=None, peer_container=None, mark=None, wait_seconds=0,
                output=str(output))
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
            results = passing_results("side_by_side")
            results["sbs_rollback_85_legacy_useful"] = lanes.case_result(
                "sbs_rollback_85_legacy_useful", status, form="legacy", reason="synthetic")
            with self.subTest(status=status):
                self.assertEqual(lanes.build_manifest(lane_row("side_by_side"), COMMIT, results)["result"], "FAIL")

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


if __name__ == "__main__":
    unittest.main()
