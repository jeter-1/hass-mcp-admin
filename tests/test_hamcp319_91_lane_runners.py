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
import subprocess
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
    if "exact_image_admission_91_governed.py" in text:
        produced += list(governed91_evidence.governed.CASES_BY_CONFIGURATION.get(configuration, ()))
    return produced


def health(version: str = LANE_VERSION, **view) -> dict:
    return {"success": True, "operation": "get_server_health",
            "data": {"server": {"version": version}, "upstream_read_gateway": view}}


# The reviewed 9.1.0 capture the runtime's strict-full fingerprint is bound to,
# and the five optional filesystem descriptors as the 9.1.0 image advertises
# them with the toggle enabled (HAMCP-319 phase 1 capture).
REVIEWED_91 = runners.load_reviewed_91_capture()
OPTIONAL_91 = [tool for tool in json.loads((ROOT / "docs" / "evidence" / "hamcp319" / "phase1" / "catalogs"
                                            / "full-1.json").read_text(encoding="utf-8"))["tools"]
               if tool["name"] in runners.OPTIONAL_NAMES]
AUTOMATIC_READS = 27


def peer_tools_91() -> list:
    return json.loads(json.dumps(REVIEWED_91[:40] + OPTIONAL_91 + REVIEWED_91[40:]))


def strict_91_view(**overrides) -> dict:
    view = {
        "initialized": True, "admission_status": "admitted_exact", "version_status": "reviewed_exact",
        "observed_identity_status": "accepted", "observed_upstream_server_version": "9.1.0",
        "selected_compatibility_entry_id": "ha-mcp-v9.1.0-reviewed",
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


# A sample of 2.4.2-beta.6's Engineering-native public tools; none is delegated
# (every ha-mcp tool name, and so every delegated read, starts with "ha_").
ROLLBACK_NATIVE_TOOLS = ["get_change_plan", "get_server_health", "list_capabilities"]


def rollback_refused_probe() -> dict:
    """2.4.2-beta.6's refusal of an unlisted delegated read (routing.py, af5ec837), as the runner records it."""

    return {"success": False, "operation": runners.PROBE_TOOL, "error": "CoreRequirementUnmapped",
            "error_code": "unsupported_operation",
            "details": {"reason": "core_requirement_unmapped", "reason_code": "core_requirement_unmapped",
                        "core_authority_acquired": False, "provider_dispatch_occurred": False,
                        "upstream_attempted": False, "home_assistant_request_count": 0,
                        "upstream_request_count": 0},
            "mcp_is_error": True}


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
                         tool_names=[runners.PROBE_TOOL], probe=served_probe(), peer_requests=None,
                         peer_tools=peer_tools_91(), reviewed_tools=REVIEWED_91)
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
        arguments.update(health_before=health(version, **view), health_after=health(version, **view),
                         tool_names=list(ROLLBACK_NATIVE_TOOLS), probe=rollback_refused_probe())
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
        persisted=persisted_ok(name) if name in runners.PERSISTED_CASES else None,
    )
    arguments.update(overrides)
    return runners.evaluate_gateway_case(configuration, name, **arguments)


MOUNT = "/home/runner/work/_temp/admission-data-sbs"
STORE = "governance/change_plans"
PLAN_A = f"{STORE}/{'a' * 32}.json"
PLAN_B = f"{STORE}/{'b' * 32}.json"
SEED_TASK_ID = "d" * 32
SEED_SNAPSHOT_ID = "e" * 32
TASK_A = f"{STORE}/execution-tasks-v1/{'a' * 32}.{SEED_TASK_ID}.json"
TRACE_INDEX = f"{STORE}/trace-snapshots-v1/index.json"
TRACE_RECORD = f"{STORE}/trace-snapshots-v1/records/{SEED_SNAPSHOT_ID}.json"
CORE_VERSION = "2026.7.2"
AUDIT_BYTES = b'{"event":"tool_call"}\n{"event":"tool_call"}\n'
ZERO_COUNTERS = {key: 0 for key in runners.DISPATCH_COUNTERS}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def synthetic_plan(plan_id: str, status: str, writer: str) -> dict:
    """An inventory plan summary, in the shape INVENTORY_SCRIPT's real reader emits."""

    events = [["change_plan_created", sha(b"created-" + plan_id.encode())]]
    if status == "superseded":
        events.append(["change_plan_superseded", sha(b"superseded-" + plan_id.encode())])
    return {"plan_id": plan_id, "status": status,
            "approval_state": "invalidated" if status == "superseded" else "required",
            "contract_version": 2, "plan_family": None, "operation": "configuration_plan",
            "identity_sha256": sha(b"identity-" + plan_id.encode()),
            "content_sha256": sha(b"content-" + plan_id.encode()),
            "approval_sha256": sha(b"approval-" + status.encode() + plan_id.encode()),
            "events": events, "provider": None, "dispatch_sha256": None, "writer_versions": [writer]}


def previous_release_entries(writer: str = runners.ROLLBACK_ENGINEERING_VERSION) -> dict:
    """What the outgoing release left in the lane data directory, read by its own readers (offline shape)."""

    return {
        runners.READER_KEY: {"kind": "reader", "version": writer},
        "options.json": {"kind": "file", "size": 288, "sha256": sha(b"options")},
        "audit.jsonl": {"kind": "file", "size": len(AUDIT_BYTES), "sha256": sha(AUDIT_BYTES), "records": 2,
                        "server_versions": {writer: 2}},
        "audit.jsonl.lock": {"kind": "file", "size": 0, "sha256": sha(b"")},
        "governance": {"kind": "dir", "listable": True},
        STORE: {"kind": "dir", "listable": True},
        PLAN_A: {"kind": "file", "size": 4623, "sha256": sha(b"plan-a"),
                 "plan": synthetic_plan("a" * 32, "superseded", writer)},
        PLAN_B: {"kind": "file", "size": 4196, "sha256": sha(b"plan-b"),
                 "plan": synthetic_plan("b" * 32, "awaiting_approval", writer)},
        f"{STORE}/execution-tasks-v1": {"kind": "dir", "listable": True},
        TASK_A: {"kind": "file", "size": 3699, "sha256": sha(b"task-a"),
                 "task": {"task_id": SEED_TASK_ID, "plan_id": "a" * 32, "state": "cancelled_pre_dispatch",
                          "dispatched": False, "provider_attempts": 0, "events": 2, "writer_versions": []}},
        f"{STORE}/trace-snapshots-v1": {"kind": "dir", "listable": True},
        f"{STORE}/trace-snapshots-v1/.transaction.lock": {"kind": "file", "size": 0, "sha256": sha(b"")},
        f"{STORE}/trace-snapshots-v1/records": {"kind": "dir", "listable": True},
        TRACE_RECORD: {"kind": "file", "size": 198, "sha256": sha(b"trace")},
        TRACE_INDEX: {"kind": "file", "size": 500, "sha256": sha(b"index"),
                      "trace": {"snapshots": {SEED_SNAPSHOT_ID: {
                          "sha256": sha(b"trace"), "bytes": 198, "automation_id": "gateway_fixture",
                          "run_id": "run-1", "core_version": CORE_VERSION, "writer_versions": [writer]}},
                          "watches": 0}},
    }


def restarted_entries(writer: str = runners.ROLLBACK_ENGINEERING_VERSION, reader: str = LANE_VERSION,
                      *, accepted: bool = True) -> dict:
    """The restarted release's own-reader view: untouched at restart; audit grown and plan B superseded after."""

    entries = json.loads(json.dumps(previous_release_entries(writer)))
    entries[runners.READER_KEY]["version"] = reader
    entries["audit.jsonl"]["prefix_sha256"] = sha(AUDIT_BYTES)
    if accepted:
        grown = AUDIT_BYTES + b'{"event":"tool_call"}\n'
        entries["audit.jsonl"].update(size=len(grown), sha256=sha(grown))
        superseded = synthetic_plan("b" * 32, "superseded", writer)
        entries[PLAN_B].update(sha256=sha(b"plan-b-superseded"), plan=superseded)
        entries[f"{STORE}/{'c' * 32}.json"] = {"kind": "file", "size": 4196, "sha256": sha(b"plan-c"),
                                               "plan": synthetic_plan("c" * 32, "awaiting_approval", reader)}
        del entries["audit.jsonl.lock"]
    return entries


def synthetic_seed() -> dict:
    return {"task_id": SEED_TASK_ID, "plan_id": "a" * 32, "snapshot_id": SEED_SNAPSHOT_ID,
            "automation_id": "gateway_fixture"}


def display_of(snapshot: dict, restarted: dict) -> dict:
    """What the restarted release's public reads return for the outgoing release's records (as recorded)."""

    seed = snapshot["seed"]
    stored_trace = restarted[TRACE_INDEX]["trace"]["snapshots"][seed["snapshot_id"]]
    return {
        "audit_captures": [{"server_version": snapshot["writer_version"], "snapshot_ids": [seed["snapshot_id"]]}],
        "task": {"task_id": seed["task_id"], "plan_id": seed["plan_id"], "state": "cancelled_pre_dispatch"},
        "plans": {plan_id: {"status": restarted[f"{STORE}/{plan_id}.json"]["plan"]["status"]}
                  for plan_id in runners.displayed_plan_ids(snapshot["entries"])},
        "trace": {"snapshot_id": seed["snapshot_id"], "automation_id": seed["automation_id"],
                  "core_version": stored_trace["core_version"]},
    }


def persisted_snapshot_ok(case: str = runners.ROLL_FORWARD_CASE) -> dict:
    image = runners.PERSISTED_CASES[case][0]
    writer = runners.ROLLBACK_ENGINEERING_VERSION if image == runners.ROLLBACK_ENGINEERING_IMAGE else LANE_VERSION
    return {"status": "passed", "image": image, "mount_source": MOUNT, "writer_version": writer,
            "seed": synthetic_seed(), "counters": dict(ZERO_COUNTERS), "entries": previous_release_entries(writer)}


def persisted_ok(case: str = runners.ROLL_FORWARD_CASE) -> dict:
    """``{"snapshot", "restart", "current", "mount_source"}`` for one transition case (offline)."""

    snapshot = persisted_snapshot_ok(case)
    writer = snapshot["writer_version"]
    reader = LANE_VERSION if writer == runners.ROLLBACK_ENGINEERING_VERSION else runners.ROLLBACK_ENGINEERING_VERSION
    at_restart = restarted_entries(writer, reader, accepted=False)
    restart = {"status": "passed", "image": runners.PERSISTED_CASES[case][1], "mount_source": MOUNT,
               "counters": dict(ZERO_COUNTERS), "entries": at_restart, "display": display_of(snapshot, at_restart)}
    return {"snapshot": snapshot, "restart": restart, "current": restarted_entries(writer, reader),
            "mount_source": MOUNT}


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
        status, reason, _ = gateway_case("91_only", "91_parity_read_gateway_acceptance",
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
            if '--image "$ROLLBACK_ENGINEERING_IMAGE"' in text:
                self.assertIn(name, ("Case side-by-side rollback, previous release 2.4.2-beta.6 with 8.5",
                                     "Case side-by-side previous release refuses 9.1 on the strict-full value"))
            elif "ROLLBACK_ENGINEERING_IMAGE" in text:
                # Stage A again only names it as the outgoing writer it verifies; it never runs it.
                self.assertEqual(name, "Case side-by-side stage A again, current build with 8.5 on the legacy value")
                self.assertEqual(text.count("ROLLBACK_ENGINEERING_IMAGE"), 1)
                self.assertIn('--writer-image "$ROLLBACK_ENGINEERING_IMAGE"', text)

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
        name = ("91_only", "91_parity_read_gateway_acceptance")
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
        values = dict(configuration="91_only", case="91_parity_read_gateway_acceptance",
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
                          engineering_container="engineering-admission-91-strict", rollback_identity=None,
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
            refused = args(directory, configuration="side_by_side", case="sbs_engineering_rollback_91_refused",
                           expect="unsupported_version_rollback")
            self.assertEqual(runners.command_health_case(refused, session=session, catalog=catalog), 1)
            value = json.loads(Path(refused.output).read_text())
            self.assertEqual(value["reason"], "peer_catalog_outside_strict_admission")
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


# ---------------------------------------------------------------------------
# HAMCP-319 #407 F3: transitions between releases on the same persisted data.
# Records are generated by each release's real writers, read by each actual
# release's readers in both directions (this checkout's runtime package and
# 2.4.2-beta.6's, extracted from its exact commit), and the oracle allows
# only the legitimate awaiting_approval -> superseded plan transition.
# ---------------------------------------------------------------------------

W6 = runners.ROLLBACK_ENGINEERING_VERSION
W7 = LANE_VERSION
DIRECTIONS = {runners.ROLL_FORWARD_CASE: (W6, W7), runners.ROLLBACK_PERSISTED_CASE: (W7, W6)}
FIXTURE_A = ROOT / "tests" / "fixtures" / "beta6_prohibited_superseded_contract_v2_a.json"
FIXTURE_B = ROOT / "tests" / "fixtures" / "beta6_prohibited_superseded_contract_v2_b.json"
# Both shipped fixtures record this request as their change_plan_created event.
FIXTURE_REQUEST = "beta6-compat-fixture-request"
REAL_PLAN_A = f"{STORE}/a1000000000000000000000000000001.json"
REAL_PLAN_B = f"{STORE}/b2000000000000000000000000000001.json"

# Test-local writer: every record goes through the given release's own
# writer classes (AuditLogger, ChangePlanRepository, ExecutionTaskRepository,
# TraceSnapshotStore), the way that release's runtime writes them.
WRITER_SCRIPT = r"""
import json, os, sys, uuid
from datetime import datetime, timezone
config = json.loads(sys.argv[1])
sys.path.insert(0, config["package_root"])
from ha_mcp_engineering.version import SERVER_VERSION
from ha_mcp_engineering.audit import AuditLogger, AuditRecord
from ha_mcp_engineering.governance.models import ChangePlan
from ha_mcp_engineering.governance.service import ChangeGovernanceService as Service
from ha_mcp_engineering.governance.storage import ChangePlanRepository
from ha_mcp_engineering.governance.task_models import ExecutionTaskState, new_execution_task
from ha_mcp_engineering.governance.task_storage import ExecutionTaskRepository
from ha_mcp_engineering.trace_snapshots import contracts as c
from ha_mcp_engineering.trace_snapshots.store import TraceSnapshotStore, empty_index
root = config["root"]
store = os.path.join(root, "governance", "change_plans")
os.makedirs(store, exist_ok=True)
plans = ChangePlanRepository(store)
for raw in config.get("plans", []):
    plans.save(ChangePlan.from_dict(raw))
logger = AuditLogger(os.path.join(root, "audit.jsonl"), "synthetic-access-secret")
for record in config.get("audit", []):
    if not logger.write(AuditRecord(**record)):
        raise SystemExit("audit_write_failed")
written = {"writer_version": SERVER_VERSION, "tasks": []}
for plan_id in config.get("tasks", []):
    plan = plans.get(plan_id)
    plan_hash = Service.plan_hash(plan)
    now = datetime.now(timezone.utc).isoformat()
    task = new_execution_task(
        task_id=uuid.uuid4().hex, plan_id=plan.plan_id, plan_hash=plan_hash,
        operation=plan.operation.value, target=Service._task_target(plan), timestamp=now,
        execution_request_id="synthetic-task-request-" + uuid.uuid4().hex[:16],
        idempotency_key=Service._task_idempotency_key(Service, plan, plan_hash),
        approval_reference=Service._task_approval_reference(plan),
        legacy_projection={"record_kind": "f1_execution_task", "plan_status": plan.status.value,
                           "execution_outcome": plan.execution_outcome})
    tasks = ExecutionTaskRepository(store)
    tasks.save(task)
    task.append_event("task_cancelled_pre_dispatch", now, new_state=ExecutionTaskState.CANCELLED_PRE_DISPATCH,
                      changes={"completed_at": now, "terminal_outcome": "cancelled_pre_dispatch",
                               "legacy_projection": {**task.legacy_projection, "task_state": "cancelled_pre_dispatch"}})
    tasks.save(task)
    written["tasks"].append([plan.plan_id, task.task_id])
traces = TraceSnapshotStore(store)
if config.get("empty_trace_store"):
    with traces.transaction():
        traces.write_index(empty_index())
for trace in config.get("traces", []):
    raw = c.canonical({"model": c.RECORD_MODEL, "snapshot_id": trace["snapshot_id"],
                       "automation_id": trace["automation_id"], "run_id": trace["run_id"],
                       "core": {"version": trace["core_version"]}, "trace": {"state": "stopped"}})
    with traces.transaction():
        traces.write_record(trace["snapshot_id"], raw)
        index = traces.load_index()
        index["snapshots"].append({
            "snapshot_id": trace["snapshot_id"], "automation_id": trace["automation_id"],
            "run_id": trace["run_id"], "origin": "manual", "watch_id": None,
            "captured_at_ms": 1785000000000, "captured_by": "synthetic-caller", "completeness": "complete",
            "bytes": len(raw), "sha256": c.sha256(raw), "state": "stopped",
            "script_execution": "finished", "started_at": "2026-07-31T12:00:00+00:00"})
        traces.write_index(index)
print(json.dumps(written, sort_keys=True))
"""


def literal_inventory_config(root: str, prefix: dict, package_root: str) -> dict:
    """INVENTORY_SCRIPT's configuration, spelled out (a superset of every earlier revision's keys)."""

    return {"root": root, "prefix": prefix, "package_root": package_root, "audit": "audit.jsonl",
            "plan_file": r"governance/change_plans/(?:operational-administration-v3/)?[0-9a-f]{32}\.json",
            "task_file": r"governance/change_plans/execution-tasks-v1/[0-9a-f]{32}\.[0-9a-f]{32}\.json",
            "plan_identity": ["plan_id", "created_at", "operation", "target", "proposed_config_hash"],
            "plan_lifecycle": ["status", "updated_at", "approval", "events"], "event_limit": 256,
            "capture_tool": "capture_automation_trace", "governance_root": "governance/change_plans",
            "trace_index": "governance/change_plans/trace-snapshots-v1/index.json"}


def run_script(script: str, config: dict) -> str:
    completed = subprocess.run([sys.executable, "-I", "-c", script, json.dumps(config)],
                               capture_output=True, text=True, timeout=300, check=False)
    if completed.returncode != 0:
        raise AssertionError(completed.stdout[-1000:] + completed.stderr[-3000:])
    return completed.stdout


def read_inventory(root: Path, package: str, prefix: dict | None = None) -> dict:
    """``INVENTORY_SCRIPT`` with one release's own reader classes, exactly as the container runs it."""

    return json.loads(run_script(runners.INVENTORY_SCRIPT, literal_inventory_config(str(root), prefix or {}, package)))


def write_records(root: Path, package: str, **records) -> dict:
    return json.loads(run_script(WRITER_SCRIPT, {"root": str(root), "package_root": package, **records}))


def fixture_plan(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def awaiting_fixture_b() -> dict:
    """Fixture B before its supersession: only the lifecycle fields differ from the shipped record."""

    plan = fixture_plan(FIXTURE_B)
    plan["status"] = "awaiting_approval"
    plan["approval"].update(state="required", bundle_state="pending_plan_approval")
    plan["events"] = plan["events"][:1]
    return plan


def audit_call(tool: str, request_id: str, **extra) -> dict:
    return dict(request_id=request_id, tool_name=tool, capability_classification=None, operation_category=None,
                access=None, authenticated=True, caller_id="synthetic-caller", result_status="success", **extra)


def rewrite_json(path: Path, change) -> None:
    """Replace a stored record with still-valid JSON that differs only by ``change``."""

    value = json.loads(path.read_text(encoding="utf-8"))
    change(value)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")


_PACKAGES: dict[str, str] = {}
_SCRATCH: list[str] = []


def _cleanup_scratch() -> None:
    import shutil
    for directory in _SCRATCH:
        shutil.rmtree(directory, ignore_errors=True)


def scratch(prefix: str) -> Path:
    if not _SCRATCH:
        import atexit
        atexit.register(_cleanup_scratch)
    directory = tempfile.mkdtemp(prefix=prefix)
    _SCRATCH.append(directory)
    return Path(directory)


def release_packages() -> dict[str, str]:
    """Each release's runtime package root: this checkout's, and 2.4.2-beta.6's from its exact commit."""

    if _PACKAGES:
        return _PACKAGES
    import os
    import tarfile
    revision = runners.ROLLBACK_ENGINEERING_REVISION
    present = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", revision + "^{commit}"],
                             capture_output=True, check=False).returncode == 0
    if not present:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise AssertionError("the 2.4.2-beta.6 commit is absent from the CI checkout; its readers cannot run")
        raise unittest.SkipTest("2.4.2-beta.6 commit not available in this clone")
    archive = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", revision,
                              "hass_mcp_engineering_beta/ha_mcp_engineering"],
                             capture_output=True, check=True, timeout=300).stdout
    directory = scratch("hamcp319-beta6-")
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(directory, filter="data")
    _PACKAGES.update({W6: str(directory / "hass_mcp_engineering_beta"), W7: str(ROOT / "hass_mcp_engineering_beta")})
    return _PACKAGES


def build_transition(case: str, *, traces: bool = True) -> dict:
    """One transition on one data directory, as the lane runs it, with real writers and readers.

    The outgoing release writes plans, audit, an execution task and a trace
    record; its own readers take the snapshot. The restarted release's
    readers take the restart record; then, as its acceptance run does, it
    appends to the audit log and supersedes the awaiting plan with its own
    writer, and its readers take the current inventory.
    """

    import shutil
    writer, reader = DIRECTIONS[case]
    packages = release_packages()
    root = scratch("hamcp319-persisted-") / "data"
    root.mkdir()
    snapshot_id = hashlib.sha256(case.encode()).hexdigest()[:32]
    written = write_records(
        root, packages[writer], plans=[fixture_plan(FIXTURE_A), awaiting_fixture_b()],
        audit=[audit_call("create_change_plan", FIXTURE_REQUEST),
               audit_call(runners.CAPTURE_TOOL, "synthetic-capture-request",
                          analysis_summary={"snapshot_ids": [snapshot_id]})],
        tasks=["a1000000000000000000000000000001"],
        traces=[{"snapshot_id": snapshot_id, "automation_id": "gateway_fixture", "run_id": "run-1",
                 "core_version": CORE_VERSION}] if traces else [],
        empty_trace_store=not traces)
    assert written["writer_version"] == writer, written
    (plan_id, task_id), = written["tasks"]
    seed = {"task_id": task_id, "plan_id": plan_id, "snapshot_id": snapshot_id, "automation_id": "gateway_fixture"}
    entries = read_inventory(root, packages[writer])
    before = root.parent / "before"
    shutil.copytree(root, before)
    snapshot = {"status": "passed", "image": runners.PERSISTED_CASES[case][0], "mount_source": MOUNT,
                "writer_version": writer, "seed": seed, "counters": dict(ZERO_COUNTERS), "entries": entries}
    prefix = runners.prefix_sizes(entries)
    at_restart = read_inventory(root, packages[reader], prefix)
    accepted = write_records(root, packages[reader], plans=[fixture_plan(FIXTURE_B)],
                             audit=[audit_call("get_server_health", "synthetic-acceptance-request")])
    assert accepted["writer_version"] == reader, accepted
    current = read_inventory(root, packages[reader], prefix)
    display = display_of(snapshot, at_restart) if traces else {}
    restart = {"status": "passed", "image": runners.PERSISTED_CASES[case][1], "mount_source": MOUNT,
               "counters": dict(ZERO_COUNTERS), "entries": at_restart, "display": display}
    return {"root": root, "before": before, "writer": writer, "reader": reader, "prefix": prefix,
            "persisted": {"snapshot": snapshot, "restart": restart, "current": current, "mount_source": MOUNT}}


class PersistedTransitionReadersTests(unittest.TestCase):
    """Codex #407 F3: real writers before the transition, each actual release's readers after, both directions."""

    layouts: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.layouts = {case: build_transition(case) for case in DIRECTIONS}

    def persisted(self, case: str) -> dict:
        return json.loads(json.dumps(self.layouts[case]["persisted"]))

    def reread(self, case: str, mutate, *, before: bool = False) -> tuple[dict, dict]:
        """Mutate a copy of the data directory and read it again with the relevant release's own readers."""

        import shutil
        layout = self.layouts[case]
        copy = scratch("hamcp319-mutated-") / "data"
        shutil.copytree(layout["before"] if before else layout["root"], copy)
        mutate(copy)
        persisted = self.persisted(case)
        if before:
            entries = read_inventory(copy, release_packages()[layout["writer"]])
            persisted["snapshot"]["entries"] = entries
            return entries, persisted
        current = read_inventory(copy, release_packages()[layout["reader"]], layout["prefix"])
        persisted["current"] = current
        return current, persisted

    def case(self, case: str, persisted) -> tuple:
        return gateway_case("side_by_side", case, persisted=persisted)[:2]

    def test_codex_f3_synthetic_invalid_status_is_unreadable_and_fails_the_case(self) -> None:
        # Codex's counterexample: only a shipped-code plan's status changed to
        # a value the actual ChangePlan.from_dict rejects (still valid JSON).
        for case in DIRECTIONS:
            with self.subTest(case=case):
                current, persisted = self.reread(case, lambda root: rewrite_json(
                    root / REAL_PLAN_A, lambda plan: plan.update(status="synthetic_invalid_status")))
                self.assertEqual(current[REAL_PLAN_A].get("error"), "ValueError")
                self.assertNotIn("plan", current[REAL_PLAN_A])
                self.assertEqual(self.case(case, persisted), ("failed", "persisted_entry_unreadable"))
                self.assertEqual(self.case(case, self.persisted(case)), ("passed", ""))

    def test_inventory_configuration_is_the_literal_reader_configuration(self) -> None:
        self.assertEqual(runners.inventory_config("/data", {"audit.jsonl": 3}, "/app"),
                         literal_inventory_config("/data", {"audit.jsonl": 3}, "/app"))
        self.assertEqual((runners.PERSISTED_ROOT, runners.PERSISTED_PACKAGE_ROOT), ("/data", "/app"))
        self.assertEqual(runners.seed_config("/data", "/app"),
                         {"root": "/data", "package_root": "/app", "plan_store": "governance/change_plans",
                          "request_prefix": "hamcp319-persisted-seed-"})

    def test_each_release_reads_the_other_releases_records_in_both_directions(self) -> None:
        for case, (writer, reader) in DIRECTIONS.items():
            with self.subTest(case=case):
                persisted = self.persisted(case)
                snapshot, restart, current = persisted["snapshot"], persisted["restart"], persisted["current"]
                self.assertEqual(snapshot["entries"][runners.READER_KEY], {"kind": "reader", "version": writer})
                self.assertEqual(restart["entries"][runners.READER_KEY], {"kind": "reader", "version": reader})
                self.assertEqual(current[runners.READER_KEY], {"kind": "reader", "version": reader})
                for inventory in (snapshot["entries"], restart["entries"], current):
                    self.assertFalse([path for path, entry in inventory.items() if "error" in entry])
                    for path in (REAL_PLAN_A, REAL_PLAN_B):
                        plan = inventory[path]["plan"]
                        self.assertEqual(sorted(plan), sorted(synthetic_plan("a" * 32, "superseded", writer)))
                        self.assertEqual((plan["writer_versions"], plan["provider"], plan["contract_version"]),
                                         ([writer], None, 2))
                self.assertEqual(snapshot["entries"]["audit.jsonl"]["server_versions"], {writer: 2})
                self.assertEqual(current["audit.jsonl"]["server_versions"], {writer: 2})
                task_path = f"{STORE}/execution-tasks-v1/{snapshot['seed']['plan_id']}.{snapshot['seed']['task_id']}.json"
                self.assertEqual(current[task_path]["task"]["state"], "cancelled_pre_dispatch")
                self.assertEqual((current[task_path]["task"]["dispatched"],
                                  current[task_path]["task"]["provider_attempts"]), (False, 0))
                traces = current[TRACE_INDEX]["trace"]["snapshots"]
                self.assertEqual(list(traces), [snapshot["seed"]["snapshot_id"]])
                self.assertEqual(traces[snapshot["seed"]["snapshot_id"]]["writer_versions"], [writer])
                self.assertEqual(traces[snapshot["seed"]["snapshot_id"]]["core_version"], CORE_VERSION)
                status, reason, observed = gateway_case("side_by_side", case, persisted=persisted)
                self.assertEqual((status, reason), ("passed", ""))
                summary = observed["persisted_transition"]
                self.assertEqual(summary["direction"], [writer, reader])
                self.assertEqual(summary["written_trace_records"], 1)
                self.assertEqual(summary["plan_status_transitions"],
                                 {"b2000000000000000000000000000001": ["awaiting_approval", "superseded"]})
                self.assertEqual(summary["dispatch_counters"], ZERO_COUNTERS)

    def test_awaiting_to_superseded_is_allowed_only_as_the_shipped_writer_records_it(self) -> None:
        # The legitimate transition (shipped bytes of fixture B) passes above;
        # a reader-valid status other than superseded is refused, and an
        # unreadable approval is never accepted as part of the transition.
        for case in DIRECTIONS:
            with self.subTest(case=case, change="applied"):
                current, persisted = self.reread(case, lambda root: rewrite_json(
                    root / REAL_PLAN_B, lambda plan: plan.update(status="expired")))
                self.assertNotIn("error", current[REAL_PLAN_B])
                self.assertEqual(self.case(case, persisted), ("failed", "persisted_plan_status_transition_refused"))
            with self.subTest(case=case, change="invalid approval"):
                current, persisted = self.reread(case, lambda root: rewrite_json(
                    root / REAL_PLAN_B, lambda plan: plan["approval"].update(state="synthetic_invalid_state")))
                self.assertIn("error", current[REAL_PLAN_B])
                self.assertEqual(self.case(case, persisted), ("failed", "persisted_entry_unreadable"))
            with self.subTest(case=case, change="superseded without its event"):
                current, persisted = self.reread(case, lambda root: rewrite_json(
                    root / REAL_PLAN_B, lambda plan: plan.update(events=plan["events"][:1])))
                self.assertEqual(self.case(case, persisted), ("failed", "persisted_plan_transition_unrecorded"))
            with self.subTest(case=case, change="superseded plan revived"):
                current, persisted = self.reread(case, lambda root: rewrite_json(
                    root / REAL_PLAN_A, lambda plan: plan.update(status="awaiting_approval")))
                self.assertEqual(self.case(case, persisted), ("failed", "persisted_plan_status_transition_refused"))

    def test_malformed_but_valid_json_records_are_unreadable(self) -> None:
        def task_file(root: Path) -> Path:
            return next((root / STORE / "execution-tasks-v1").glob("*.json"))

        def wrong_model(root: Path) -> None:
            records = root / STORE / "trace-snapshots-v1"
            record = next((records / "records").glob("*.json"))
            raw = json.dumps({**json.loads(record.read_bytes()), "model": "synthetic-other-model"}).encode()
            record.write_bytes(raw)
            rewrite_json(records / "index.json", lambda index: index["snapshots"][0].update(
                bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))

        def damaged(root: Path) -> None:
            record = next((root / STORE / "trace-snapshots-v1" / "records").glob("*.json"))
            record.write_bytes(record.read_bytes().replace(b"stopped", b"running"))

        mutations = {
            "task state": (lambda root: rewrite_json(task_file(root),
                                                     lambda task: task.update(state="synthetic_invalid_state")),
                           lambda root: str(task_file(root).relative_to(root))),
            "trace record model": (wrong_model, lambda _root: TRACE_INDEX),
            "trace record digest": (damaged, lambda _root: TRACE_INDEX),
            "plan status": (lambda root: rewrite_json(root / REAL_PLAN_B,
                                                      lambda plan: plan.update(status="synthetic_invalid_status")),
                            lambda _root: REAL_PLAN_B),
        }
        for case in DIRECTIONS:
            for label, (mutate, path) in mutations.items():
                with self.subTest(case=case, record=label, side="restarted release"):
                    seen = {}
                    current, persisted = self.reread(case, lambda root: (mutate(root), seen.update(path=path(root))))
                    self.assertIn("error", current[seen["path"]])
                    self.assertEqual(self.case(case, persisted)[0], "failed")
                with self.subTest(case=case, record=label, side="outgoing release"):
                    seen = {}
                    entries, persisted = self.reread(
                        case, lambda root: (mutate(root), seen.update(path=path(root))), before=True)
                    self.assertIn("error", entries[seen["path"]])
                    self.assertEqual(self.case(case, persisted), ("failed", "previous_release_data_unreadable"))
            with self.subTest(case=case, record="audit line that is a JSON array"):
                def array_line(root: Path) -> None:
                    with open(root / "audit.jsonl", "a", encoding="utf-8") as handle:
                        handle.write("[]\n")

                entries, persisted = self.reread(case, array_line, before=True)
                self.assertEqual(entries["audit.jsonl"].get("error"), "ValueError")
                self.assertEqual(self.case(case, persisted), ("failed", "previous_release_data_unreadable"))

    def test_an_empty_trace_store_is_not_trace_coverage(self) -> None:
        # CI's earlier A-again evidence: trace store present, zero records.
        empty = build_transition(runners.ROLL_FORWARD_CASE, traces=False)["persisted"]
        self.assertEqual(empty["snapshot"]["entries"][TRACE_INDEX]["trace"]["snapshots"], {})
        self.assertEqual(self.case(runners.ROLL_FORWARD_CASE, empty),
                         ("failed", "previous_release_trace_records_absent"))

    def test_the_real_seed_script_writes_a_terminal_never_dispatched_task_with_each_writer(self) -> None:
        for case, (writer, _reader) in DIRECTIONS.items():
            with self.subTest(writer=writer):
                root = scratch("hamcp319-seed-") / "data"
                write_records(root, release_packages()[writer],
                              plans=[fixture_plan(FIXTURE_A), awaiting_fixture_b()])
                config = runners.seed_config(str(root), release_packages()[writer])
                seeded = json.loads(run_script(runners.SEED_SCRIPT, config))
                self.assertEqual((seeded["writer_version"], seeded["task_state"], seeded["plan_id"]),
                                 (writer, "cancelled_pre_dispatch", "a1000000000000000000000000000001"))
                again = json.loads(run_script(runners.SEED_SCRIPT, config))
                self.assertEqual(again["plan_id"], "b2000000000000000000000000000001")
                with self.assertRaises(AssertionError):
                    run_script(runners.SEED_SCRIPT, config)  # every stored plan already has a task
                task = read_inventory(root, release_packages()[writer])[
                    f"{STORE}/execution-tasks-v1/{seeded['plan_id']}.{seeded['task_id']}.json"]["task"]
                self.assertEqual((task["state"], task["dispatched"], task["provider_attempts"]),
                                 ("cancelled_pre_dispatch", False, 0))


PERSISTED_MUTATIONS = {
    # reason: mutation of an otherwise passing persisted record (offline shape)
    "persisted_snapshot_not_previous_release": lambda p: p["snapshot"].update(image=runners.LANE_ENGINEERING_IMAGE),
    "previous_release_reader_not_writer": lambda p: p["snapshot"]["entries"][runners.READER_KEY].update(version=W7),
    "previous_release_audit_version_unrecorded": lambda p: p["snapshot"]["entries"]["audit.jsonl"].update(
        server_versions={W7: 2}),
    "previous_release_plan_writer_unrecorded": lambda p: [p["snapshot"]["entries"][path]["plan"].update(
        writer_versions=[]) for path in (PLAN_A, PLAN_B)],
    "previous_release_tasks_absent": lambda p: p["snapshot"]["entries"].pop(TASK_A),
    "previous_release_trace_records_absent": lambda p: p["snapshot"]["entries"][TRACE_INDEX]["trace"].update(
        snapshots={}),
    "previous_release_trace_unparseable": lambda p: p["snapshot"]["entries"][TRACE_RECORD].update(sha256=sha(b"x")),
    "previous_release_seed_unobserved": lambda p: p["snapshot"]["entries"][TASK_A]["task"].update(dispatched=True),
    "persisted_restart_missing": lambda p: p.update(restart=None),
    "persisted_restart_not_restarted_release": lambda p: p["restart"].update(image=runners.ROLLBACK_ENGINEERING_IMAGE),
    "persisted_transition_not_between_releases": lambda p: p["restart"]["entries"][runners.READER_KEY].update(
        version=W6),
    "persisted_data_not_same_mount": lambda p: p["restart"].update(mount_source="/home/runner/work/_temp/fresh"),
    "persisted_dispatch_counters_unobserved": lambda p: p["restart"]["counters"].pop("http_mutations"),
    "persisted_transition_redispatched": lambda p: p["restart"]["counters"].update(approval_notification_calls=1),
    "persisted_reader_not_restarted_release": lambda p: p["current"][runners.READER_KEY].update(version="2.4.2"),
    "persisted_entry_unreadable": lambda p: p["current"][PLAN_A].update(error="ValueError"),
    "persisted_audit_not_preserved": lambda p: p["current"]["audit.jsonl"].update(server_versions={W7: 2}),
    "persisted_plan_provider_changed": lambda p: p["current"][PLAN_A]["plan"].update(provider="upstream_read_gateway"),
    "persisted_plan_redispatched": lambda p: p["current"][PLAN_A]["plan"].update(dispatch_sha256=sha(b"dispatch")),
    "persisted_plan_content_changed": lambda p: p["current"][PLAN_A]["plan"].update(content_sha256=sha(b"x")),
    "persisted_plan_lifecycle_changed": lambda p: p["current"][PLAN_A]["plan"].update(approval_sha256=sha(b"x")),
    "persisted_plan_status_transition_refused": lambda p: p["current"][PLAN_B]["plan"].update(status="applied"),
    "persisted_plan_history_rewritten": lambda p: p["current"][PLAN_B]["plan"]["events"].pop(0),
    "persisted_plan_transition_unrecorded": lambda p: p["current"][PLAN_B]["plan"]["events"].pop(),
    "persisted_plan_approval_not_invalidated": lambda p: p["current"][PLAN_B]["plan"].update(approval_state="required"),
    "persisted_task_redispatched": lambda p: p["current"][TASK_A].update(
        sha256=sha(b"x"), task={**p["current"][TASK_A]["task"], "provider_attempts": 1}),
    "persisted_task_changed": lambda p: p["current"][TASK_A].update(sha256=sha(b"x")),
    "persisted_trace_record_lost": lambda p: p["current"][TRACE_INDEX]["trace"].update(snapshots={}),
    "persisted_trace_record_changed": lambda p: p["current"][TRACE_INDEX]["trace"]["snapshots"][
        SEED_SNAPSHOT_ID].update(core_version="2026.8.0"),
    "persisted_display_unavailable": lambda p: p["restart"].update(display=None),
    "persisted_audit_not_displayed": lambda p: p["restart"]["display"].update(audit_captures=[]),
    "persisted_task_not_displayed": lambda p: p["restart"]["display"]["task"].update(state="dispatched"),
    "persisted_plan_not_displayed": lambda p: p["restart"]["display"]["plans"].pop("b" * 32),
    "persisted_trace_not_displayed": lambda p: p["restart"]["display"]["trace"].update(automation_id="other"),
}


class PersistedTransitionOracleTests(unittest.TestCase):
    """The transition oracle, offline: every guard has a counterexample, in both directions."""

    def test_the_offline_transition_passes_in_both_directions(self) -> None:
        for case in runners.PERSISTED_CASES:
            with self.subTest(case=case):
                self.assertIsNone(runners.persisted_data_problem(persisted_ok(case), case=case))
                status, reason, observed = gateway_case("side_by_side", case)
                self.assertEqual((status, reason), ("passed", ""))
                self.assertEqual(observed["persisted_transition"]["plan_status_transitions"],
                                 {"b" * 32: ["awaiting_approval", "superseded"]})

    def test_each_guard_refuses_its_counterexample(self) -> None:
        for case in runners.PERSISTED_CASES:
            for reason, mutate in PERSISTED_MUTATIONS.items():
                with self.subTest(case=case, reason=reason):
                    persisted = persisted_ok(case)
                    if reason in ("previous_release_reader_not_writer", "persisted_transition_not_between_releases"):
                        mutate = {"previous_release_reader_not_writer": lambda p: p["snapshot"]["entries"][
                                      runners.READER_KEY].update(version="2.4.2-beta.5"),
                                  "persisted_transition_not_between_releases": lambda p: p["restart"]["entries"][
                                      runners.READER_KEY].update(version=p["snapshot"]["writer_version"])}[reason]
                    if reason == "persisted_snapshot_not_previous_release":
                        mutate = lambda p, c=case: p["snapshot"].update(image=runners.PERSISTED_CASES[c][1])
                    if reason == "persisted_restart_not_restarted_release":
                        mutate = lambda p, c=case: p["restart"].update(image=runners.PERSISTED_CASES[c][0])
                    if reason == "previous_release_audit_version_unrecorded":
                        mutate = lambda p: p["snapshot"]["entries"]["audit.jsonl"].update(server_versions={"x": 2})
                    if reason == "persisted_audit_not_preserved":
                        mutate = lambda p: p["current"]["audit.jsonl"].update(server_versions={"x": 2})
                    mutate(persisted)
                    self.assertEqual(runners.persisted_data_problem(persisted, case=case), reason)
                    self.assertEqual(gateway_case("side_by_side", case, persisted=persisted)[:2], ("failed", reason))

    def test_same_mount_and_byte_preservation_checks_are_retained(self) -> None:
        for change, reason in (
                (lambda p: p["current"].pop(PLAN_A), "persisted_entry_missing"),
                (lambda p: p["current"].pop(TRACE_RECORD), "persisted_entry_missing"),
                (lambda p: p["current"].update({PLAN_A: {"kind": "dir", "listable": True}}),
                 "persisted_entry_kind_changed"),
                (lambda p: p["current"]["audit.jsonl"].update(prefix_sha256=sha(b"rewritten")),
                 "persisted_audit_not_preserved"),
                (lambda p: p["current"]["audit.jsonl"].update(size=3), "persisted_audit_not_preserved"),
                (lambda p: p["current"]["audit.jsonl"].update(records=1), "persisted_audit_not_preserved"),
                (lambda p: p["current"][TRACE_RECORD].update(sha256=sha(b"other")), "persisted_record_changed"),
                (lambda p: p["current"].update({"<walk>": {"kind": "dir", "listable": False, "error": "OSError"}}),
                 "persisted_data_unreadable_after_restart"),
                (lambda p: p["restart"]["entries"].pop(PLAN_B), "persisted_entry_missing"),
                (lambda p: p.update(mount_source=None), "persisted_data_not_same_mount"),
                (lambda p: p["snapshot"].update(mount_source=None), "persisted_data_not_same_mount")):
            with self.subTest(reason=reason):
                persisted = persisted_ok()
                change(persisted)
                self.assertEqual(runners.persisted_data_problem(persisted, case=runners.ROLL_FORWARD_CASE), reason)

    def test_seed_must_be_terminal_pre_dispatch_and_its_trace_attributable(self) -> None:
        for change in (lambda p: p["snapshot"].update(seed=None),
                       lambda p: p["snapshot"]["seed"].update(task_id="f" * 32),
                       lambda p: p["snapshot"]["entries"][TASK_A]["task"].update(state="dispatched"),
                       lambda p: p["snapshot"]["entries"][TASK_A]["task"].update(provider_attempts=1),
                       lambda p: p["snapshot"]["seed"].update(automation_id="gateway_fixture_unreadable"),
                       lambda p: p["snapshot"]["entries"][TRACE_INDEX]["trace"]["snapshots"][
                           SEED_SNAPSHOT_ID].update(writer_versions=[])):
            persisted = persisted_ok()
            change(persisted)
            self.assertEqual(runners.persisted_data_problem(persisted, case=runners.ROLL_FORWARD_CASE),
                             "previous_release_seed_unobserved")

    def test_both_transition_cases_require_the_check_and_no_other_case_carries_it(self) -> None:
        for case in runners.PERSISTED_CASES:
            self.assertEqual(gateway_case("side_by_side", case, persisted=None)[:2],
                             ("failed", "persisted_snapshot_missing"))
        self.assertEqual(set(runners.PERSISTED_CASES), {runners.ROLL_FORWARD_CASE, runners.ROLLBACK_PERSISTED_CASE})
        self.assertEqual(runners.PERSISTED_CASES[runners.ROLLBACK_PERSISTED_CASE],
                         (runners.LANE_ENGINEERING_IMAGE, runners.ROLLBACK_ENGINEERING_IMAGE))
        self.assertEqual(runners.PERSISTED_CASES[runners.ROLL_FORWARD_CASE],
                         (runners.ROLLBACK_ENGINEERING_IMAGE, runners.LANE_ENGINEERING_IMAGE))
        self.assertEqual(gateway_case("side_by_side", "sbs_stage_a_85_legacy_useful", persisted=persisted_ok())[:2],
                         ("failed", "persisted_check_outside_transition"))
        swapped = persisted_ok(runners.ROLLBACK_PERSISTED_CASE)
        self.assertEqual(gateway_case("side_by_side", runners.ROLL_FORWARD_CASE, persisted=swapped)[:2],
                         ("failed", "persisted_snapshot_not_previous_release"))

    def test_dispatch_counters_are_counted_independently_from_the_synthetic_core(self) -> None:
        stats = {"http_mutations": {"POST /api/x": 2}, "websocket_mutations": {}, "operational_backup_creates": [],
                 "approval_notification_calls": [{"id": 1}], "rest_reads": {"GET /api/": 9}}
        self.assertEqual(runners.dispatch_counters(stats), {"http_mutations": 2, "websocket_mutations": 0,
                                                            "operational_backup_creates": 0,
                                                            "approval_notification_calls": 1})
        for broken in ({k: v for k, v in stats.items() if k != "approval_notification_calls"},
                       {**stats, "http_mutations": 3}, {**stats, "websocket_mutations": {"x": "1"}}):
            with self.assertRaises(runners.LaneFailure):
                runners.dispatch_counters(broken)
        with self.assertRaises(runners.LaneFailure):
            runners.dispatch_counters(None)


class _FakeCalls:
    def __init__(self, results):
        self.results, self.seen = results, []

    async def __call__(self, endpoint, calls):
        self.seen.append((endpoint, calls))
        return [self.results(name, arguments) for name, arguments in calls]


class PersistedTransitionCommandTests(unittest.TestCase):
    """The snapshot and restart commands: real writers first, then own-reader records; fail closed."""

    ENDPOINT = "http://127.0.0.1:18300/x/mcp"

    def snapshot_args(self, directory: str, **overrides) -> argparse.Namespace:
        values = dict(engineering_container="engineering-admission-sbs", engineering_endpoint=self.ENDPOINT,
                      data_dir=MOUNT, writer_image=runners.ROLLBACK_ENGINEERING_IMAGE,
                      expected_engineering_version=W6, seed_automation="gateway_fixture",
                      fixture_stats_url="http://127.0.0.1:18123/__stats",
                      output=str(Path(directory) / "snapshot.json"))
        values.update(overrides)
        return argparse.Namespace(**values)

    def run_snapshot(self, *, image=runners.ROLLBACK_ENGINEERING_IMAGE, mount=MOUNT, task=None, entries=None,
                     stats=None, capture=None, **overrides):
        calls = []

        def seed_task(container):
            calls.append("seed")
            return task if task is not None else {"writer_version": W6, "task_id": SEED_TASK_ID,
                                                  "plan_id": "a" * 32, "task_state": "cancelled_pre_dispatch"}

        def captured(endpoint, automation):
            calls.append(("capture", automation))
            if capture is not None:
                raise capture
            return SEED_SNAPSHOT_ID

        def inventory(container, prefix):
            calls.append(("inventory", prefix))
            return entries if entries is not None else previous_release_entries(W6)

        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            args = self.snapshot_args(directory, **overrides)
            code = runners.command_persisted_snapshot(
                args, inventory=inventory, mount=lambda _c: mount, image=lambda _c: image, seed_task=seed_task,
                capture=captured, stats=lambda _url: stats if stats is not None else {
                    key: [] for key in runners.DISPATCH_COUNTERS})
            return code, json.loads(Path(args.output).read_text()), calls

    def test_snapshot_writes_with_the_outgoing_release_then_records_its_own_reader_view(self) -> None:
        code, value, calls = self.run_snapshot()
        self.assertEqual((code, value["status"]), (0, "passed"))
        self.assertEqual(calls, [("capture", "gateway_fixture"), "seed", ("inventory", {})])
        self.assertEqual(value["seed"], synthetic_seed())
        self.assertEqual((value["writer_version"], value["image"], value["mount_source"], value["counters"]),
                         (W6, runners.ROLLBACK_ENGINEERING_IMAGE, MOUNT, ZERO_COUNTERS))

    def test_snapshot_fails_closed(self) -> None:
        for overrides, reason in (
                (dict(writer_image="ghcr.io/other/image:latest"), "persisted_writer_image_unlisted"),
                (dict(image=runners.LANE_ENGINEERING_IMAGE), "persisted_snapshot_not_previous_release"),
                (dict(mount=None), "persisted_data_mount_missing"),
                (dict(data_dir="/home/runner/work/_temp/fresh"), "persisted_data_not_lane_data_dir"),
                (dict(seed_automation="other_automation"), "persisted_seed_automation_unlisted"),
                (dict(capture=runners.LaneFailure("persisted_seed_trace_not_captured")),
                 "persisted_seed_trace_not_captured"),
                (dict(task={"writer_version": W7, "task_id": SEED_TASK_ID, "plan_id": "a" * 32,
                            "task_state": "cancelled_pre_dispatch"}), "persisted_writer_version_mismatch"),
                (dict(task={"writer_version": W6, "task_id": SEED_TASK_ID, "plan_id": "a" * 32,
                            "task_state": "pending"}), "persisted_seed_task_not_terminal"),
                (dict(stats={"http_mutations": []}), "fixture_dispatch_counter_missing"),
                (dict(entries={}), "previous_release_data_unobserved"),
                (dict(expected_engineering_version=W7, task={"writer_version": W7, "task_id": SEED_TASK_ID,
                                                             "plan_id": "a" * 32,
                                                             "task_state": "cancelled_pre_dispatch"}),
                 "previous_release_reader_not_writer")):
            with self.subTest(reason=reason):
                code, value, _ = self.run_snapshot(**overrides)
                self.assertEqual((code, value["status"], value.get("reason")), (1, "failed", reason))
                self.assertNotIn("entries", value)

    def test_restart_counts_dispatch_before_anything_else_and_never_passes_by_itself(self) -> None:
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            snapshot = Path(directory) / "snapshot.json"
            snapshot.write_text(json.dumps(persisted_snapshot_ok()))
            calls = []

            def run(snapshot_path=str(snapshot), display_error=None):
                def display(endpoint, value):
                    calls.append("display")
                    if display_error:
                        raise display_error
                    return {"plans": {}}

                args = argparse.Namespace(engineering_container="engineering-admission-sbs",
                                          engineering_endpoint=self.ENDPOINT, persisted_snapshot=snapshot_path,
                                          fixture_stats_url="http://127.0.0.1:18123/__stats",
                                          output=str(Path(directory) / "restart.json"))
                code = runners.command_persisted_restart(
                    args, inventory=lambda c, prefix: calls.append(("inventory", prefix)) or {"x": 1},
                    mount=lambda _c: MOUNT, image=lambda _c: runners.LANE_ENGINEERING_IMAGE,
                    stats=lambda _url: calls.append("stats") or {key: [] for key in runners.DISPATCH_COUNTERS},
                    display=display)
                return code, json.loads(Path(args.output).read_text())

            code, value = run()
            self.assertEqual((code, value["status"]), (0, "passed"))
            self.assertEqual(calls, ["stats", ("inventory", runners.prefix_sizes(previous_release_entries())),
                                     "display"])
            self.assertEqual(value["counters"], ZERO_COUNTERS)
            code, value = run(snapshot_path=str(Path(directory) / "absent.json"))
            self.assertEqual((code, value["status"], value["reason"]),
                             (0, "failed", "persisted_snapshot_not_previous_release"))
            code, value = run(display_error=RuntimeError("boom"))
            self.assertEqual((code, value["status"]), (0, "failed"))
            self.assertTrue(value["reason"].startswith("observation_failed"))
            # A failed restart record fails the case.
            persisted = persisted_ok()
            persisted["restart"] = value
            self.assertEqual(runners.persisted_data_problem(persisted, case=runners.ROLL_FORWARD_CASE),
                             "persisted_restart_missing")

    def test_capture_requires_exactly_one_new_snapshot(self) -> None:
        def calls(runs):
            return _FakeCalls(lambda name, arguments: {"success": True, "data": {"runs": runs}})

        fake = calls([{"run_id": "r1", "outcome": "captured", "snapshot_id": SEED_SNAPSHOT_ID}])
        self.assertEqual(runners.capture_seed_trace(self.ENDPOINT, "gateway_fixture", calls=fake), SEED_SNAPSHOT_ID)
        self.assertEqual(fake.seen, [(self.ENDPOINT, [(runners.CAPTURE_TOOL, {"automation_id": "gateway_fixture"})])])
        for runs in ([], [{"run_id": "r1", "outcome": "already_captured", "snapshot_id": SEED_SNAPSHOT_ID}],
                     [{"outcome": "captured", "snapshot_id": "e" * 32}, {"outcome": "captured", "snapshot_id": "f" * 32}],
                     [{"outcome": "captured", "snapshot_id": "not-hex"}]):
            with self.subTest(runs=runs), self.assertRaises(runners.LaneFailure):
                runners.capture_seed_trace(self.ENDPOINT, "gateway_fixture", calls=calls(runs))
        with self.assertRaises(runners.LaneFailure):
            runners.capture_seed_trace(self.ENDPOINT, "gateway_fixture", calls=_FakeCalls(lambda n, a: None))

    def test_display_reads_each_outgoing_record_through_the_public_tools(self) -> None:
        snapshot = persisted_snapshot_ok()
        audit_line = json.dumps({"tool_name": runners.CAPTURE_TOOL, "server_version": W6,
                                 "analysis_summary": {"snapshot_ids": [SEED_SNAPSHOT_ID]}})

        def results(name, arguments):
            if name == "get_audit_log":
                return {"text": "not json\n" + audit_line + "\n"}
            if name == "get_execution_task":
                return {"success": True, "data": {"task_id": arguments["task_id"], "plan_id": "a" * 32,
                                                  "state": "cancelled_pre_dispatch", "other": "x"}}
            if name == "get_trace_snapshot":
                return {"success": True, "data": {"snapshot": {"snapshot_id": arguments["snapshot_id"],
                                                               "automation_id": "gateway_fixture"},
                                                  "core": {"version": CORE_VERSION}}}
            if arguments["plan_id"] == "b" * 32:
                return {"success": False, "error_code": "plan_not_found"}
            return {"success": True, "data": {"canonical_summary": {"plan_id": arguments["plan_id"],
                                                                    "status": "superseded"}}}

        fake = _FakeCalls(results)
        shown = runners.persisted_display(self.ENDPOINT, snapshot, calls=fake)
        self.assertEqual([name for name, _ in fake.seen[0][1]],
                         ["get_audit_log", "get_execution_task", "get_trace_snapshot", "get_change_plan",
                          "get_change_plan"])
        self.assertEqual(shown["audit_captures"], [{"server_version": W6, "snapshot_ids": [SEED_SNAPSHOT_ID]}])
        self.assertEqual(shown["task"], {"task_id": SEED_TASK_ID, "plan_id": "a" * 32,
                                         "state": "cancelled_pre_dispatch"})
        self.assertEqual(shown["trace"], {"snapshot_id": SEED_SNAPSHOT_ID, "automation_id": "gateway_fixture",
                                          "core_version": CORE_VERSION})
        self.assertEqual(shown["plans"], {"a" * 32: {"status": "superseded"}, "b" * 32: {"error": "plan_not_found"}})
        persisted = persisted_ok()
        persisted["restart"]["display"] = shown
        self.assertEqual(runners.persisted_data_problem(persisted, case=runners.ROLL_FORWARD_CASE),
                         "persisted_plan_not_displayed")

    def test_gateway_runner_observes_the_restarted_container_against_both_records(self) -> None:
        async def session(*_args, **_kwargs):
            return gateway_health("8.5.0", None, LANE_VERSION), [], None

        persisted = persisted_ok()
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            source = Path(directory) / "gw.json"
            source.write_text(json.dumps(gateway_source("8.5.0", None)))
            snapshot = Path(directory) / "snapshot.json"
            snapshot.write_text(json.dumps(persisted["snapshot"]))
            restart = Path(directory) / "restart.json"
            restart.write_text(json.dumps(persisted["restart"]))

            def args(**overrides):
                values = dict(configuration="side_by_side", case=runners.ROLL_FORWARD_CASE, source=str(source),
                              engineering_endpoint=self.ENDPOINT, configured_url=LEGACY,
                              upstream_endpoint=PEER_LEGACY, expected_engineering_version=LANE_VERSION,
                              engineering_container="engineering-admission-sbs", rollback_identity=None,
                              persisted_snapshot=str(snapshot), persisted_restart=str(restart),
                              output=str(Path(directory) / "case.json"))
                values.update(overrides)
                return argparse.Namespace(**values)

            seen = {}

            def inventory(container, prefix):
                seen.update(container=container, prefix=prefix)
                return persisted["current"]

            def result():
                value = json.loads(Path(args().output).read_text())
                return value["status"], value.get("reason", "")

            with mock.patch.object(runners, "container_image", return_value=runners.LANE_ENGINEERING_IMAGE), \
                    mock.patch.object(runners, "container_inventory", side_effect=inventory), \
                    mock.patch.object(runners, "container_data_mount", return_value=MOUNT):
                self.assertEqual(runners.command_gateway_case(args(), session=session), 0, result())
                self.assertEqual(seen["prefix"]["audit.jsonl"], len(AUDIT_BYTES))
                self.assertEqual(runners.command_gateway_case(args(persisted_restart=None), session=session), 1)
                self.assertEqual(result(), ("failed", "persisted_restart_missing"))
                self.assertEqual(runners.command_gateway_case(args(persisted_snapshot=None), session=session), 1)
                self.assertEqual(result(), ("failed", "persisted_snapshot_missing"))
                snapshot.unlink()
                self.assertEqual(runners.command_gateway_case(args(), session=session), 1)
                self.assertEqual(result(), ("failed", "persisted_snapshot_not_previous_release"))

    def test_parser_requires_every_transition_argument(self) -> None:
        parser = runners.parser()
        snapshot = ["persisted-snapshot", "--engineering-container", "engineering-admission-sbs",
                    "--engineering-endpoint", self.ENDPOINT, "--data-dir", MOUNT,
                    "--writer-image", runners.LANE_ENGINEERING_IMAGE, "--expected-engineering-version", W7,
                    "--seed-automation", "gateway_fixture", "--fixture-stats-url", "http://127.0.0.1:18123/__stats",
                    "--output", "/tmp/x.json"]
        self.assertEqual(parser.parse_args(snapshot).seed_automation, "gateway_fixture")
        for index in range(1, len(snapshot), 2):
            with self.subTest(missing=snapshot[index]), redirect_stdout(io.StringIO()), \
                    mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(snapshot[:index] + snapshot[index + 2:])
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args([*snapshot[:-4], "--seed-automation", "other", "--output", "/tmp/x.json"])
        restart = parser.parse_args(["persisted-restart", "--engineering-container", "engineering-admission-sbs",
                                     "--engineering-endpoint", self.ENDPOINT, "--persisted-snapshot", "/tmp/s.json",
                                     "--fixture-stats-url", "http://127.0.0.1:18123/__stats",
                                     "--output", "/tmp/r.json"])
        self.assertEqual(restart.persisted_snapshot, "/tmp/s.json")


class PersistedTransitionWorkflowTests(unittest.TestCase):
    """Both transitions run on the same data directory, with each record taken at its boundary."""

    TRANSITIONS = {
        "Case side-by-side rollback, previous release 2.4.2-beta.6 with 8.5": dict(
            case=runners.ROLLBACK_PERSISTED_CASE, writer='--writer-image engineering-admission-lane:test',
            version='--expected-engineering-version "$ENGINEERING_LANE_VERSION"', automation="gateway_fixture",
            snapshot="admission-persisted-current-release.json", restart="admission-persisted-rollback-restart.json"),
        "Case side-by-side stage A again, current build with 8.5 on the legacy value": dict(
            case=runners.ROLL_FORWARD_CASE, writer='--writer-image "$ROLLBACK_ENGINEERING_IMAGE"',
            version='--expected-engineering-version "$ROLLBACK_ENGINEERING_VERSION"',
            automation="gateway_fixture_unreadable", snapshot="admission-persisted-previous-release.json",
            restart="admission-persisted-roll-forward-restart.json"),
    }

    def test_each_transition_snapshots_before_and_records_after_the_restart(self) -> None:
        steps = {step.get("name", ""): step.get("run", "") for step in workflow_job()["steps"]}
        endpoint = '--engineering-endpoint "http://127.0.0.1:18300/${ENGINEERING_ACCESS_SECRET}/mcp"'
        for name, expected in self.TRANSITIONS.items():
            with self.subTest(step=name):
                script = steps[name]
                snap = script.index("persisted-snapshot")
                restart = script.index("persisted-restart")
                engineering = script.index("start-engineering")
                acceptance = script.index("exact_image_read_gateway_acceptance.py")
                case = script.index("gateway-case")
                self.assertLess(snap, script.index("start-peer"))
                self.assertLess(snap, engineering)
                self.assertLess(engineering, restart)
                self.assertLess(restart, acceptance)
                self.assertLess(acceptance, case)
                snapshot_args = script[snap:script.index("start-peer")]
                for text in (endpoint, '--data-dir "$RUNNER_TEMP/admission-data-sbs"', expected["writer"],
                             expected["version"], f'--seed-automation {expected["automation"]}',
                             '--fixture-stats-url "$FIXTURE_STATS_URL"', "--engineering-container engineering-admission-sbs",
                             f'--output "$RUNNER_TEMP/{expected["snapshot"]}"'):
                    self.assertIn(text, snapshot_args)
                restart_args = script[restart:acceptance]
                for text in (endpoint, f'--persisted-snapshot "$RUNNER_TEMP/{expected["snapshot"]}"',
                             '--fixture-stats-url "$FIXTURE_STATS_URL"',
                             f'--output "$RUNNER_TEMP/{expected["restart"]}"'):
                    self.assertIn(text, restart_args)
                case_args = script[case:]
                self.assertIn(f"--case {expected['case']}", case_args)
                self.assertIn(f'--persisted-snapshot "$RUNNER_TEMP/{expected["snapshot"]}"', case_args)
                self.assertIn(f'--persisted-restart "$RUNNER_TEMP/{expected["restart"]}"', case_args)

    def test_the_data_directory_is_never_reset_across_the_side_by_side_stages(self) -> None:
        steps = workflow_job()["steps"]
        names = [step.get("name", "") for step in steps]
        again = next(i for i, name in enumerate(names) if "stage A again" in name)
        refusal = next(i for i, name in enumerate(names) if "previous release refuses 9.1" in name)
        rollback = next(i for i, name in enumerate(names) if "side-by-side rollback" in name)
        self.assertEqual(again, refusal + 1, "no step may run between the beta.6 stage and stage A again")
        self.assertEqual(refusal, rollback + 1)
        for step in steps:
            if "side_by_side" in str(step.get("if", "")):
                for match in re.finditer(r"--data-dir \"([^\"]+)\"", step.get("run", "")):
                    self.assertEqual(match.group(1), "$RUNNER_TEMP/admission-data-sbs")
        for step in steps[:again + 1]:
            self.assertNotRegex(step.get("run", ""), r"rm[^\n]*admission-data")
        for name in names:
            if name not in self.TRANSITIONS:
                run = steps[names.index(name)].get("run", "")
                self.assertNotIn("persisted-snapshot", run)
                self.assertNotIn("persisted-restart", run)


# ---------------------------------------------------------------------------
# HAMCP-319 #407 correction round (Codex delta review of db04ca41).
# Each Codex demonstration is a regression test that failed on db04ca41.
# ---------------------------------------------------------------------------

ROLLBACK_CASE = "sbs_engineering_rollback_91_refused"


class _Text:
    def __init__(self, text: str) -> None:
        self.text = text


class _Result:
    def __init__(self, payload, *, is_error: bool = False) -> None:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        self.content = [_Text(text)]
        self.isError = is_error


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


def fake_mcp(*, tools, probe, health_result):
    """A stand-in ``mcp`` package: the runner's real ``engineering_session`` talks to it.

    ``probe`` and ``health_result`` are ``_Result`` objects, or an exception
    instance the call raises.
    """

    import contextlib
    import types

    class Session:
        def __init__(self, read, write) -> None:
            del read, write

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def initialize(self):
            return None

        async def list_tools(self):
            return types.SimpleNamespace(tools=[_Tool(name) for name in tools])

        async def call_tool(self, name, arguments):
            del arguments
            value = probe if name == runners.PROBE_TOOL else health_result
            if isinstance(value, BaseException):
                raise value
            return value

    @contextlib.asynccontextmanager
    async def client(endpoint):
        del endpoint
        yield None, None, None

    package = types.ModuleType("mcp")
    package.ClientSession = Session
    client_package = types.ModuleType("mcp.client")
    transport = types.ModuleType("mcp.client.streamable_http")
    transport.streamablehttp_client = client
    return {"mcp": package, "mcp.client": client_package, "mcp.client.streamable_http": transport}


def rollback_health_result(**changes) -> _Result:
    payload = health(runners.ROLLBACK_ENGINEERING_VERSION, **rollback_view())
    payload.update(changes)
    return _Result(payload)


def run_rollback_through_gate(*, tools=None, probe=None, health_result=None):
    """command -> case file -> manifest (both architectures) -> gate, through the real MCP session code.

    Returns ``(case_status, case_reason, side_by_side_acceptance, gate_errors)``.
    """

    tools = list(ROLLBACK_NATIVE_TOOLS) if tools is None else tools
    probe = _Result(rollback_refused_probe_payload(), is_error=True) if probe is None else probe
    health_result = rollback_health_result() if health_result is None else health_result
    stats = {key: {"synthetic": value} for key, value in FIXTURE.items()}
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / f"{ROLLBACK_CASE}.json"
        args = argparse.Namespace(
            configuration="side_by_side", case=ROLLBACK_CASE, expect="unsupported_version_rollback",
            engineering_endpoint="http://127.0.0.1:18300/synthetic/mcp", configured_url=STRICT,
            expected_engineering_version=runners.ROLLBACK_ENGINEERING_VERSION,
            engineering_container="engineering-admission-sbs", rollback_identity="synthetic-identity",
            fixture_stats_url="http://127.0.0.1:18123/__fixture__/stats", peer_container=None,
            mark=None, wait_seconds=0, output=str(output))
        with mock.patch.dict(sys.modules, fake_mcp(tools=tools, probe=probe, health_result=health_result)), \
                mock.patch.object(runners, "container_image", return_value=runners.ROLLBACK_ENGINEERING_IMAGE), \
                mock.patch.object(runners, "load_identity", return_value=rollback_identity_ok()), \
                mock.patch.object(runners, "http_json", return_value=stats), redirect_stdout(io.StringIO()):
            runners.command_health_case(args)
        loaded = lanes.load_case_results(Path(directory))
    case = loaded[ROLLBACK_CASE]
    rows = [lane_row(configuration, architecture=architecture)
            for configuration in lanes.CASES for architecture in lanes.ARCHITECTURES]
    manifests = []
    for row in rows:
        values = passing_results(row["configuration"], row["architecture"])
        if row["configuration"] == "side_by_side":
            values[ROLLBACK_CASE] = json.loads(json.dumps(case))
        manifests.append(lanes.build_manifest(row, COMMIT, values))
    acceptance, errors = lanes.verify_manifests(manifests, {"include": rows}, COMMIT)
    return case["status"], case["reason"], acceptance.get("side_by_side"), errors


def rollback_refused_probe_payload() -> dict:
    payload = rollback_refused_probe()
    del payload["mcp_is_error"]
    return payload


class RollbackRefusalOracleTests(unittest.TestCase):
    """F1: no path to ACCEPTED when any rollback evidence is missing, errored, unexpected or inconsistent."""

    def assert_refused_end_to_end(self, reason_prefix: str, **session) -> None:
        status, reason, acceptance, _ = run_rollback_through_gate(**session)
        self.assertEqual(status, "failed", reason)
        self.assertTrue(reason.startswith(reason_prefix), reason)
        self.assertNotEqual(acceptance, "ACCEPTED")

    def test_positive_control_is_accepted_end_to_end(self) -> None:
        status, reason, acceptance, errors = run_rollback_through_gate()
        self.assertEqual((status, reason), ("passed", ""))
        self.assertEqual(acceptance, "ACCEPTED", errors)

    def test_unexpected_delegated_history_read_fails_end_to_end(self) -> None:
        self.assert_refused_end_to_end("rollback_delegated_surface_not_empty",
                                       tools=ROLLBACK_NATIVE_TOOLS + ["ha_get_history"])

    def test_unexpected_call_service_fails_end_to_end(self) -> None:
        self.assert_refused_end_to_end("rollback_delegated_surface_not_empty",
                                       tools=ROLLBACK_NATIVE_TOOLS + ["ha_call_service"])

    def test_missing_probe_evidence_fails_end_to_end(self) -> None:
        # The probe answer is not decodable: there is no refusal evidence at all.
        self.assert_refused_end_to_end("rollback_probe_evidence_missing", probe=_Result("not json", is_error=True))

    def test_health_success_false_or_error_bearing_fails_end_to_end(self) -> None:
        self.assert_refused_end_to_end("health_not_successful",
                                       health_result=rollback_health_result(success=False,
                                                                            error_code="synthetic_error"))
        self.assert_refused_end_to_end("health_reports_error",
                                       health_result=rollback_health_result(error="SyntheticError"))
        self.assert_refused_end_to_end("health_reports_error",
                                       health_result=rollback_health_result(error_code="synthetic_error"))
        self.assert_refused_end_to_end("health_operation_unexpected",
                                       health_result=rollback_health_result(operation="get_audit_log"))

    def test_mcp_is_error_health_with_plausible_data_fails_end_to_end(self) -> None:
        payload = health(runners.ROLLBACK_ENGINEERING_VERSION, **rollback_view())
        self.assert_refused_end_to_end("health_unreadable", health_result=_Result(payload, is_error=True))

    def test_tool_text_treats_mcp_is_error_as_an_error(self) -> None:
        payload = health(runners.ROLLBACK_ENGINEERING_VERSION, **rollback_view())
        self.assertIsNone(runners.tool_text(_Result(payload, is_error=True)))
        self.assertIsNone(runners.tool_text(_Result(payload, is_error="yes")))
        self.assertEqual(runners.tool_text(_Result(payload)), payload)
        decoded = runners.tool_text(_Result(payload, is_error=True))
        self.assertEqual(health_case("unsupported_version_rollback", health_after=decoded)[:2],
                         ("failed", "health_missing"))

    def test_unattributable_refusals_fail_end_to_end(self) -> None:
        # A refusal must be beta.6's own unmapped-route refusal with no
        # dispatch: a transport error, a generic failure or a dispatching
        # refusal is not evidence that the previous release refused 9.1.
        self.assert_refused_end_to_end("rollback_refusal_not_attributable", probe=RuntimeError("transport"))
        generic = {"success": False, "error_code": "provider_unavailable"}
        self.assert_refused_end_to_end("rollback_refusal_not_attributable", probe=_Result(generic, is_error=True))
        for key, value in (("provider_dispatch_occurred", True), ("upstream_attempted", True),
                           ("upstream_request_count", 1), ("home_assistant_request_count", 1),
                           ("reason_code", "core_requirement_admitted")):
            with self.subTest(key=key):
                payload = rollback_refused_probe_payload()
                payload["details"][key] = value
                self.assert_refused_end_to_end("rollback_refusal_not_attributable",
                                               probe=_Result(payload, is_error=True))
        # The refusal is an MCP error; a non-error result carrying it is inconsistent.
        self.assert_refused_end_to_end("rollback_refusal_not_attributable",
                                       probe=_Result(rollback_refused_probe_payload(), is_error=False))

    def test_unobserved_tool_list_fails(self) -> None:
        self.assert_refused_end_to_end("engineering_tool_list_unobserved", tools=[])
        self.assertEqual(health_case("unsupported_version_rollback", tool_names=None)[:2],
                         ("failed", "engineering_tool_list_unobserved"))

    def test_case_level_counterexamples_from_the_delta_probe(self) -> None:
        expect = "unsupported_version_rollback"
        good = health(runners.ROLLBACK_ENGINEERING_VERSION, **rollback_view())
        cases = {
            "rollback_delegated_surface_not_empty": [dict(tool_names=ROLLBACK_NATIVE_TOOLS + ["ha_get_history"]),
                                                     dict(tool_names=ROLLBACK_NATIVE_TOOLS + ["ha_call_service"])],
            "rollback_probe_evidence_missing": [dict(probe=None)],
            "health_not_successful": [dict(health_after={**good, "success": False, "error_code": "synthetic_error"}),
                                      dict(health_before={**good, "success": False})],
            "health_missing": [dict(health_after=None), dict(health_before=None)],
        }
        for reason, variants in cases.items():
            for overrides in variants:
                with self.subTest(reason=reason, overrides=sorted(overrides)):
                    self.assertEqual(health_case(expect, **overrides)[:2], ("failed", reason))
        # The positive, empty-health and sampled negative controls are kept.
        self.assertEqual(health_case(expect)[:2], ("passed", ""))
        self.assertEqual(health_case(expect, health_after={})[0], "failed")
        self.assertEqual(health_case(expect, tool_names=[runners.PROBE_TOOL])[:2],
                         ("failed", "rollback_listed_delegated_read"))

    def test_the_85_rollback_gateway_case_requires_successful_health(self) -> None:
        name = "sbs_rollback_85_legacy_useful"
        bad = gateway_health("8.5.0", None, runners.ROLLBACK_ENGINEERING_VERSION)
        for changes, reason in ((dict(success=False), "health_not_successful"),
                                (dict(error_code="synthetic_error"), "health_reports_error")):
            with self.subTest(reason=reason):
                self.assertEqual(gateway_case("side_by_side", name, health={**bad, **changes})[:2],
                                 ("failed", reason))


def _mutant(replacements: list[tuple[str, str]]):
    """The runner module with each ``old`` text (present exactly once) replaced."""

    source = (ROOT / "scripts" / "exact_image_admission_91_runners.py").read_text(encoding="utf-8")
    for old, new in replacements:
        assert source.count(old) == 1, old
        source = source.replace(old, new)
    module = type(sys)("exact_image_admission_91_runners_mutant")
    module.__file__ = str(ROOT / "scripts" / "exact_image_admission_91_runners.py")
    sys.modules[module.__name__] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)  # noqa: S102 - local mutation testing
    return module


def _disable_guard(reason: str) -> tuple[str, str]:
    """Disable the one guard that reports ``reason``.

    A single-line ``require(<condition>, "<reason>")`` becomes always true; a
    ``return "<reason>"`` becomes ``pass``.
    """

    source = (ROOT / "scripts" / "exact_image_admission_91_runners.py").read_text(encoding="utf-8")
    lines = [line for line in source.splitlines()
             if (f'"{reason}")' in line and "require(" in line) or line.strip() == f'return "{reason}"']
    assert len(lines) == 1, (reason, lines)
    line = lines[0]
    if "require(" in line:
        return line + "\n", line.replace("require(", "require(True or ", 1) + "\n"
    return line + "\n", line.replace(f'return "{reason}"', "pass") + "\n"


class RollbackRefusalOracleMutationTests(unittest.TestCase):
    """F1: removing any one rollback guard lets its counterexample through, so each guard is killed by a test."""

    MUTANTS = {
        "rollback_delegated_surface_not_empty": dict(tools=ROLLBACK_NATIVE_TOOLS + ["ha_call_service"]),
        "rollback_probe_evidence_missing": dict(probe=_Result("not json", is_error=True)),
        "rollback_refusal_not_attributable": dict(probe=_Result({"success": False,
                                                                 "error_code": "provider_unavailable"},
                                                                is_error=True)),
        "health_not_successful": dict(health_result=rollback_health_result(success=False)),
        "health_reports_error": dict(health_result=rollback_health_result(error_code="synthetic_error")),
        "health_operation_unexpected": dict(health_result=rollback_health_result(operation="get_audit_log")),
        "engineering_tool_list_unobserved": dict(tools=[]),
    }

    # Removing any other guard lets its counterexample reach an ACCEPTED gate.
    # These two are backed by a later guard, so without them the case still
    # fails, under a different reason (the mutant is still killed).
    BACKED = {"rollback_probe_evidence_missing": "rollback_refusal_not_attributable",
              "engineering_tool_list_unobserved": "rollback_delegated_surface_not_empty"}

    def test_each_guard_is_load_bearing(self) -> None:
        global runners
        original = runners
        for reason, session in self.MUTANTS.items():
            with self.subTest(guard=reason):
                status, observed_reason, acceptance, _ = run_rollback_through_gate(**session)
                self.assertEqual((status, observed_reason), ("failed", reason))
                self.assertNotEqual(acceptance, "ACCEPTED")
                runners = _mutant([_disable_guard(reason)])
                try:
                    status, observed_reason, acceptance, _ = run_rollback_through_gate(**session)
                finally:
                    runners = original
                if reason in self.BACKED:
                    self.assertEqual((status, observed_reason), ("failed", self.BACKED[reason]))
                else:
                    self.assertEqual((status, acceptance), ("passed", "ACCEPTED"),
                                     f"{reason}: the counterexample is caught by another guard too")

    def test_the_is_error_guard_is_load_bearing(self) -> None:
        global runners
        original = runners
        payload = health(runners.ROLLBACK_ENGINEERING_VERSION, **rollback_view())
        session = dict(health_result=_Result(payload, is_error=True))
        self.assertEqual(run_rollback_through_gate(**session)[:2], ("failed", "health_unreadable"))
        runners = _mutant([('    if getattr(result, "isError", False) is not False:\n        return None\n', "")])
        try:
            status, _, acceptance, _ = run_rollback_through_gate(**session)
        finally:
            runners = original
        self.assertEqual((status, acceptance), ("passed", "ACCEPTED"))


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


def _disable_every_guard(reason: str):
    """The runner module with every guard that reports ``reason`` disabled."""

    source = (ROOT / "scripts" / "exact_image_admission_91_runners.py").read_text(encoding="utf-8")
    lines = [line for line in source.splitlines()
             if (f'"{reason}")' in line and "require(" in line) or line.strip() == f'return "{reason}"']
    assert lines, reason
    for line in set(lines):
        new = (line.replace("require(", "require(True or ", 1) if "require(" in line
               else line.replace(f'return "{reason}"', "pass"))
        source = source.replace(line + "\n", new + "\n")
    module = type(sys)("exact_image_admission_91_runners_persisted_mutant")
    module.__file__ = str(ROOT / "scripts" / "exact_image_admission_91_runners.py")
    sys.modules[module.__name__] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)  # noqa: S102 - local mutation testing
    return module


class PersistedTransitionMutationTests(unittest.TestCase):
    """F3: removing any one transition guard changes the verdict on its counterexample."""

    def test_each_transition_guard_is_load_bearing(self) -> None:
        for reason, mutate in PERSISTED_MUTATIONS.items():
            with self.subTest(guard=reason):
                persisted = persisted_ok()
                if reason == "persisted_snapshot_not_previous_release":
                    mutate = lambda p: p["snapshot"].update(image=runners.LANE_ENGINEERING_IMAGE)  # noqa: E731
                elif reason == "previous_release_reader_not_writer":
                    mutate = lambda p: p["snapshot"]["entries"][runners.READER_KEY].update(  # noqa: E731
                        version="2.4.2-beta.5")
                elif reason == "persisted_transition_not_between_releases":
                    mutate = lambda p: p["restart"]["entries"][runners.READER_KEY].update(version=W6)  # noqa: E731
                elif reason in ("previous_release_audit_version_unrecorded", "persisted_audit_not_preserved"):
                    side = "snapshot" if reason.startswith("previous") else "current"
                    mutate = lambda p, s=side: (p[s]["entries"] if s == "snapshot" else p[s])[  # noqa: E731
                        "audit.jsonl"].update(server_versions={"x": 2})
                mutate(persisted)
                self.assertEqual(runners.persisted_data_problem(persisted, case=runners.ROLL_FORWARD_CASE), reason)
                mutant = _disable_every_guard(reason)
                try:
                    verdict = mutant.persisted_data_problem(persisted, case=runners.ROLL_FORWARD_CASE)
                except Exception as error:  # noqa: BLE001 - a crashing mutant is killed too
                    verdict = f"raised {type(error).__name__}"
                self.assertNotEqual(verdict, reason)

    def test_codex_counterexample_passes_once_the_readability_guard_is_removed(self) -> None:
        # The real-reader record of Codex's synthetic_invalid_status plan is
        # refused only by the readability guard: without it the case passes,
        # which is exactly the pre-correction behavior Codex demonstrated.
        layout = build_transition(runners.ROLL_FORWARD_CASE)
        rewrite_json(layout["root"] / REAL_PLAN_A, lambda plan: plan.update(status="synthetic_invalid_status"))
        persisted = json.loads(json.dumps(layout["persisted"]))
        persisted["current"] = read_inventory(layout["root"], release_packages()[W7], layout["prefix"])
        self.assertEqual(runners.persisted_data_problem(persisted, case=runners.ROLL_FORWARD_CASE),
                         "persisted_entry_unreadable")
        mutant = _disable_every_guard("persisted_entry_unreadable")
        self.assertNotEqual(mutant.persisted_data_problem(persisted, case=runners.ROLL_FORWARD_CASE),
                            "persisted_entry_unreadable")
