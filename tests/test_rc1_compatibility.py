import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA_ROOT = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_ROOT))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.capabilities import (  # noqa: E402
    BETA_NATIVE_CAPABILITIES,
    CAPABILITIES,
    PLANNED_CAPABILITIES,
)
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalState,
    ChangeApproval,
    ChangeEvent,
    ChangeOperation,
    ChangePlan,
    ChangeRiskAssessment,
    ChangeRollback,
    ChangeSnapshot,
    ChangeTarget,
    PlanStatus,
    RiskLevel,
)
from ha_mcp_engineering.governance.normalize import (  # noqa: E402
    AUTOMATION_NORMALIZATION_VERSION,
    normalize_automation,
    stable_hash,
    state_fingerprint,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    APPROVAL_AUTHORITY_VERSION,
    APPROVAL_CHANNEL,
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    TERMINAL_STATUSES,
    ChangePlanRepository,
)
from ha_mcp_engineering.providers.routing import (  # noqa: E402
    DIRECT_HA_READ_POLICIES,
    DIRECT_HA_TOOL_EXCEPTIONS,
    TOOL_CAPABILITY_POLICY,
    routing_for_tool,
)
from ha_mcp_engineering.tools import get_registered_server  # noqa: E402
from ha_mcp_engineering.version import SCHEMA_VERSION  # noqa: E402


BETA26_BASELINE_SHA = "b64db57ddffc5108b9078717ce720440f5361412"
BETA26_SCHEMA_SHA256 = "eeec35d49f6d8c59fb1215694e54314b21bb6fd4a723d65e956e8e438699876a"
BETA26_SEARCH_ENTITIES_SCHEMA_SHA256 = "050632a52cb5baf438cfe71edfdb59ad2a715013d7684d2958cc40f4a2d00850"
BETA26_ENUM_SHA256 = "465924bf56992b93019184e30b5a322582e9d2789ca670fc3742004e8daa0cfb"
BETA26_CLASSIFICATION_SHA256 = "1a9dd62cd6a5c737b4cc65265b6f4f03e5532881f6d38b0e6f79994fa90a9684"
BETA26_ROUTING_SHA256 = "3fb2e0444b4d2af078499e4cdf2305067982438b3e68fdd9597edc0fcbad268e"
BETA26_DIRECT_POLICY_SHA256 = "f2719a8a6b78d9f5ec080cf79c1bf62498cdf95189daa0c50590b44978390850"
BETA26_TOOL_NAMES = (
    "apply_change_plan",
    "approve_change_plan",
    "automation_reliability_analysis",
    "call_service",
    "change_impact_analysis",
    "check_config",
    "configuration_integrity_analysis",
    "create_change_plan",
    "delete_automation",
    "entity_dependency_analysis",
    "get_audit_log",
    "get_automation_config",
    "get_automation_trace",
    "get_blueprint",
    "get_change_plan",
    "get_entity",
    "get_error_log",
    "get_history",
    "get_logbook",
    "get_server_health",
    "handoff_generation",
    "incident_correlation",
    "list_areas",
    "list_automation_traces",
    "list_automations",
    "list_blueprints",
    "list_capabilities",
    "list_change_plans",
    "list_devices",
    "list_entity_registry",
    "list_services",
    "reload_domain",
    "render_template",
    "rollback_change",
    "search_entities",
    "search_services",
    "server_info",
    "upsert_automation",
)
RC3A_ADDITIVE_TOOL_NAMES = (
    "get_dashboard_config",
    "list_dashboards",
)
DEV14_ADDITIVE_TOOL_NAMES = (
    "create_configuration_plan",
)

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
FUTURE = (NOW + timedelta(hours=4)).isoformat()
PAST = (NOW - timedelta(minutes=5)).isoformat()
CURRENT = {
    "alias": "RC1 persisted compatibility fixture",
    "description": "Before",
    "trigger": [{"platform": "event", "event_type": "rc1_fixture"}],
    "condition": [],
    "action": [{"service": "notify.fixture", "data": {"message": "Fixture"}}],
    "mode": "single",
}


def canonical_sha256(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def enum_rows(value, path=""):
    rows = []
    if isinstance(value, dict):
        if "enum" in value:
            rows.append([path, value["enum"]])
        for key, child in sorted(value.items()):
            rows.extend(enum_rows(child, f"{path}/{key}" if path else key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            rows.extend(enum_rows(child, f"{path}/{index}"))
    return rows


class RC1PublicContractTests(unittest.TestCase):
    def test_exact_beta26_tool_names_schemas_and_enums_are_frozen(self):
        tools = get_registered_server()._tool_manager.list_tools()
        names = tuple(sorted(tool.name for tool in tools))
        schemas = {tool.name: tool.parameters for tool in tools}
        beta26_schemas = {
            name: schemas[name]
            for name in BETA26_TOOL_NAMES
        }
        enums = []
        for name, schema in sorted(beta26_schemas.items()):
            enums.extend(enum_rows(schema, name))
        self.assertEqual(
            names,
            tuple(
                sorted(
                    (
                        *BETA26_TOOL_NAMES,
                        *RC3A_ADDITIVE_TOOL_NAMES,
                        *DEV14_ADDITIVE_TOOL_NAMES,
                    )
                )
            ),
        )
        self.assertEqual(canonical_sha256(beta26_schemas), BETA26_SCHEMA_SHA256)
        self.assertEqual(
            canonical_sha256(schemas["search_entities"]),
            BETA26_SEARCH_ENTITIES_SCHEMA_SHA256,
        )
        self.assertEqual(canonical_sha256(enums), BETA26_ENUM_SHA256)
        self.assertEqual(len(enums), 13)

    def test_rc2_routing_is_frozen_and_later_tools_are_additive(self):
        classifications = {
            item["tool"]: dict(item) for item in (*CAPABILITIES, *BETA_NATIVE_CAPABILITIES)
        }
        routing = {}
        for tool_name in sorted(TOOL_CAPABILITY_POLICY):
            decision = routing_for_tool(tool_name)
            routing[tool_name] = {
                "capability": TOOL_CAPABILITY_POLICY[tool_name].value,
                "route": decision.route.value,
                "preferred_provider": decision.preferred_provider,
                "fallback_providers": list(decision.fallback_providers),
                "explicit_direct_fallback_allowed": decision.explicit_direct_fallback_allowed,
            }
        self.assertEqual(
            classifications["search_entities"],
            {
                "tool": "search_entities",
                "category": "discovery",
                "status": "transitional",
                "routing": "transitional_direct",
                "provider": "direct_ha_api",
                "risk": "read",
            },
        )
        self.assertEqual(
            routing["search_entities"],
            {
                "capability": "broad_entity_search",
                "route": "transitional_direct",
                "preferred_provider": "direct_ha_api",
                "fallback_providers": [],
                "explicit_direct_fallback_allowed": False,
            },
        )
        self.assertIn("search_entities", DIRECT_HA_TOOL_EXCEPTIONS)
        self.assertEqual(
            DIRECT_HA_READ_POLICIES["search_entities"],
            {
                "policy_id": "bounded_entity_state_search",
                "capability": "broad_entity_search",
                "access": "read",
                "justification": "Bounded entity discovery requires one read-only Home Assistant state inventory while Standard HA MCP delegation is unavailable.",
            },
        )
        self.assertEqual(
            classifications["list_dashboards"],
            {
                "tool": "list_dashboards",
                "category": "discovery",
                "status": "beta_native",
                "risk": "read",
                "additive": True,
                "routing": "upstream_dashboard",
                "provider": "upstream_dashboard",
                "policy": "dashboard_inventory_read",
                "fallback": "none",
                "trust_mode": "reviewed_argument_constrained",
                "trust_profile": "ha_mcp_dashboard_read_v2",
            },
        )
        self.assertEqual(
            classifications["get_dashboard_config"],
            {
                "tool": "get_dashboard_config",
                "category": "evidence",
                "status": "beta_native",
                "risk": "read",
                "additive": True,
                "routing": "upstream_dashboard",
                "provider": "upstream_dashboard",
                "policy": "exact_dashboard_configuration_read",
                "fallback": "none",
                "trust_mode": "reviewed_argument_constrained",
                "trust_profile": "ha_mcp_dashboard_read_v2",
            },
        )
        for tool_name, capability in (
            ("list_dashboards", "dashboard_inventory"),
            ("get_dashboard_config", "dashboard_configuration_evidence"),
        ):
            self.assertEqual(
                routing[tool_name],
                {
                    "capability": capability,
                    "route": "upstream_dashboard",
                    "preferred_provider": "upstream_dashboard",
                    "fallback_providers": [],
                    "explicit_direct_fallback_allowed": False,
                },
            )
        self.assertEqual(
            classifications["create_configuration_plan"],
            {
                "tool": "create_configuration_plan",
                "category": "governance",
                "status": "beta_native",
                "risk": "behavioral_write",
                "additive": True,
                "operation_class": "proposal",
                "routing": "engineering_native",
                "provider": "engineering",
                "policy": "bounded_ordered_configuration_plan_proposal",
                "fallback": "none",
                "supported_resources": [
                    "automation",
                    "script",
                    "input_boolean",
                    "input_number",
                ],
                "direct_write_allowed": False,
            },
        )
        self.assertEqual(
            routing["create_configuration_plan"],
            {
                "capability": "risk_assessment",
                "route": "engineering_native",
                "preferred_provider": "engineering",
                "fallback_providers": [],
                "explicit_direct_fallback_allowed": False,
            },
        )

        baseline_classifications = {
            name: dict(classifications[name]) for name in BETA26_TOOL_NAMES
        }
        for item in baseline_classifications.values():
            item.pop("operation_class", None)
        baseline_classifications["search_entities"].pop("routing")
        baseline_classifications["search_entities"].pop("provider")
        baseline_classifications.update(
            {
                "upsert_automation": {
                    "tool": "upsert_automation",
                    "category": "configuration",
                    "status": "transitional",
                    "risk": "behavioral_write",
                },
                "delete_automation": {
                    "tool": "delete_automation",
                    "category": "configuration",
                    "status": "deprecated",
                    "delegate": "ha-mcp",
                    "risk": "destructive",
                },
                "call_service": {
                    "tool": "call_service",
                    "category": "execution",
                    "status": "deprecated",
                    "delegate": "ha-mcp",
                    "risk": "physical_action",
                },
                "reload_domain": {
                    "tool": "reload_domain",
                    "category": "execution",
                    "status": "deprecated",
                    "delegate": "ha-mcp",
                    "risk": "infrastructure",
                },
            }
        )
        baseline_routing = {
            name: dict(routing[name]) for name in BETA26_TOOL_NAMES
        }
        baseline_routing["search_entities"].update(
            {
                "route": "standard_mcp_preferred",
                "preferred_provider": "standard_ha_mcp",
            }
        )
        baseline_routing["upsert_automation"].update(
            {
                "route": "transitional_direct",
                "preferred_provider": "direct_ha_api",
            }
        )
        baseline_direct_policy = {
            "exceptions": sorted(
                (set(DIRECT_HA_TOOL_EXCEPTIONS) - {"search_entities"})
                | {"upsert_automation"}
            ),
            "policies": {
                name: dict(policy)
                for name, policy in DIRECT_HA_READ_POLICIES.items()
                if name != "search_entities"
            },
        }
        self.assertEqual(
            canonical_sha256(baseline_classifications),
            BETA26_CLASSIFICATION_SHA256,
        )
        self.assertEqual(canonical_sha256(baseline_routing), BETA26_ROUTING_SHA256)
        self.assertEqual(
            canonical_sha256(baseline_direct_policy),
            BETA26_DIRECT_POLICY_SHA256,
        )
        self.assertEqual(len(CAPABILITIES), 25)
        self.assertEqual(len(classifications), 41)
        self.assertEqual(PLANNED_CAPABILITIES, ())
        self.assertEqual(SCHEMA_VERSION, "1")


class FakeGateway:
    def __init__(self):
        self.reads = 0
        self.writes = 0

    async def get(self, automation_id):
        self.reads += 1
        return {**copy.deepcopy(CURRENT), "id": automation_id}

    async def write(self, automation_id, config):
        self.writes += 1
        return {"result": "ok"}

    async def validate(self):
        return {"result": "valid", "errors": None}


def persisted_plan(
    index,
    name,
    status,
    approval_state,
    *,
    authority_version=APPROVAL_AUTHORITY_VERSION,
    approval_kind="apply",
    expires_at=FUTURE,
):
    current = {**copy.deepcopy(CURRENT), "id": name}
    proposed = copy.deepcopy(CURRENT)
    proposed["description"] = f"After {name}"
    normalized_current = normalize_automation(current)
    normalized_proposed = normalize_automation(proposed)
    plan = ChangePlan(
        plan_id=f"{index:032x}",
        plan_version=1,
        created_at=NOW.isoformat(),
        updated_at=NOW.isoformat(),
        expires_at=expires_at,
        status=status,
        title=f"Persisted {name}",
        description="Beta 26 persisted lifecycle fixture",
        requested_by="beta26-mcp-caller",
        target=ChangeTarget("automation", name),
        operation=ChangeOperation.UPDATE_AUTOMATION,
        proposed_config=proposed,
        current_config=current,
        normalized_proposed_config=normalized_proposed,
        normalized_current_config=normalized_current,
        current_state_fingerprint=state_fingerprint(current),
        proposed_config_hash=stable_hash(normalized_proposed),
        risk=ChangeRiskAssessment(level=RiskLevel.LOW, reasons=["description-only"]),
        normalization_version=AUTOMATION_NORMALIZATION_VERSION,
        validation_results={"valid": True, "errors": []},
        dry_run_results={
            "has_changes": True,
            "changed_fields": [
                {"field": "description", "before": "Before", "after": f"After {name}"}
            ],
        },
        approval=ChangeApproval(
            state=approval_state,
            authority_version=authority_version,
            channel=APPROVAL_CHANNEL if approval_state != ApprovalState.REQUIRED else None,
            approver_principal=(
                "home_assistant_admin_ingress:fixture"
                if approval_state in {ApprovalState.APPROVED, ApprovalState.CONSUMED}
                else None
            ),
            principal_separation_enforced=approval_state
            in {ApprovalState.APPROVED, ApprovalState.CONSUMED},
            approved_at=(NOW - timedelta(minutes=2)).isoformat()
            if approval_state in {ApprovalState.APPROVED, ApprovalState.CONSUMED}
            else None,
            approval_kind=approval_kind,
            approval_expires_at=FUTURE
            if approval_state in {ApprovalState.APPROVED, ApprovalState.CONSUMED}
            else None,
            consumed_at=(NOW - timedelta(minutes=1)).isoformat()
            if approval_state == ApprovalState.CONSUMED
            else None,
        ),
        rollback=ChangeRollback(available=False, status="not_yet_available"),
        events=[
            ChangeEvent(
                event=f"fixture_{name}",
                timestamp=NOW.isoformat(),
                request_id=f"beta26-{name}",
                caller_id="beta26-fixture",
                result_status="success",
            )
        ],
    )
    if status == PlanStatus.EXPIRED:
        plan.events.append(
            ChangeEvent(
                event="change_plan_expired",
                timestamp=NOW.isoformat(),
                request_id="beta26-expired",
                caller_id="system",
                result_status="rejected",
                error_code=ErrorCode.CHANGE_PLAN_EXPIRED.value,
            )
        )
    if approval_state == ApprovalState.EXPIRED:
        plan.events.append(
            ChangeEvent(
                event="external_approval_expired",
                timestamp=NOW.isoformat(),
                request_id="beta26-challenge-expired",
                caller_id="system",
                result_status="rejected",
                error_code=ErrorCode.EXTERNAL_APPROVAL_EXPIRED.value,
            )
        )
    if status in {PlanStatus.APPLIED, PlanStatus.ROLLED_BACK, PlanStatus.ROLLBACK_PENDING}:
        snapshot = copy.deepcopy(current)
        fingerprint = state_fingerprint(snapshot)
        plan.applied_at = (NOW - timedelta(minutes=3)).isoformat()
        plan.post_apply_fingerprint = fingerprint
        plan.snapshot = ChangeSnapshot(NOW.isoformat(), snapshot, fingerprint)
        plan.rollback = ChangeRollback(
            available=True,
            status=("awaiting_approval" if status == PlanStatus.ROLLBACK_PENDING else status.value),
            requested_at=NOW.isoformat() if status == PlanStatus.ROLLBACK_PENDING else None,
            expected_current_fingerprint=fingerprint,
            rolled_back_at=NOW.isoformat() if status == PlanStatus.ROLLED_BACK else None,
        )
    if approval_state == ApprovalState.EXTERNAL_PENDING:
        plan.approval.challenge_id = f"challenge-{name}"
        plan.approval.challenge_requested_at = NOW.isoformat()
        plan.approval.challenge_expires_at = FUTURE
        plan.approval.challenge_plan_version = plan.plan_version
        plan.approval.challenge_target_type = plan.target_type
        plan.approval.challenge_target_id = plan.target_id
        plan.approval.challenge_operation = plan.operation.value
        plan.approval.challenge_risk_level = plan.risk.level.value
    plan.approval.bound_plan_hash = ChangeGovernanceService.plan_hash(plan)
    return plan


def create_beta26_repository(root):
    repository = ChangePlanRepository(root)
    cases = (
        persisted_plan(1, "expired-plan", PlanStatus.EXPIRED, ApprovalState.INVALIDATED, expires_at=PAST),
        persisted_plan(2, "pending-challenge", PlanStatus.AWAITING_APPROVAL, ApprovalState.EXTERNAL_PENDING),
        persisted_plan(3, "expired-challenge", PlanStatus.AWAITING_APPROVAL, ApprovalState.EXPIRED),
        persisted_plan(4, "approved-plan", PlanStatus.APPROVED, ApprovalState.APPROVED),
        persisted_plan(5, "consumed-approval", PlanStatus.APPROVED, ApprovalState.CONSUMED),
        persisted_plan(6, "applied-plan", PlanStatus.APPLIED, ApprovalState.CONSUMED),
        persisted_plan(7, "rollback-pending", PlanStatus.ROLLBACK_PENDING, ApprovalState.EXTERNAL_PENDING, approval_kind="rollback"),
        persisted_plan(8, "rolled-back", PlanStatus.ROLLED_BACK, ApprovalState.CONSUMED, approval_kind="rollback"),
        persisted_plan(9, "rejected-plan", PlanStatus.REJECTED, ApprovalState.REJECTED),
        persisted_plan(10, "legacy-terminal", PlanStatus.APPLIED, ApprovalState.CONSUMED, authority_version=1),
        persisted_plan(11, "legacy-active", PlanStatus.APPROVED, ApprovalState.APPROVED, authority_version=1),
    )
    for plan in cases:
        repository.save(plan)
    return {plan.target_id: plan.plan_id for plan in cases}


def repository_snapshot(root):
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(Path(root).glob("*.json"))
    }


class RC1InitializationTests(unittest.IsolatedAsyncioTestCase):
    async def test_clean_initialization_is_empty_healthy_and_write_free(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plans"
            audit_path = Path(directory) / "audit.jsonl"
            repository = ChangePlanRepository(root)
            service = ChangeGovernanceService(
                repository,
                FakeGateway(),
                AuditLogger(str(audit_path), "rc1-clean-init-secret"),
                now=lambda: NOW,
            )
            self.assertEqual(repository.list(), [])
            self.assertEqual(service.pending_external_reviews(), [])
            health = service.health_summary()
            self.assertEqual(health["storage_status"], "healthy")
            self.assertEqual(health["total_plans"], 0)
            for field in (
                "pending_challenge_count",
                "granted_approval_count",
                "rejected_approval_count",
                "expired_challenge_count",
                "invalidated_challenge_count",
                "approval_consumption_count",
                "rejected_plans",
                "expired_plans",
                "active_apply_operations",
                "failed_apply_count",
                "rollback_pending_count",
            ):
                self.assertEqual(health[field], 0, field)
            self.assertFalse(audit_path.exists())
            self.assertEqual(list(root.glob("*.json")), [])

    async def test_beta26_upgrade_is_readable_idempotent_and_not_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plans"
            ids = create_beta26_repository(root)
            before = repository_snapshot(root)
            raw_before = {
                name: json.loads((root / filename).read_text(encoding="utf-8"))
                for name, filename in (
                    (name, f"{plan_id}.json") for name, plan_id in ids.items()
                )
            }
            event_counts = {
                name: len(value["events"]) for name, value in raw_before.items()
            }
            hashes = {
                name: (
                    value["current_state_fingerprint"],
                    value["proposed_config_hash"],
                    value["approval"].get("bound_plan_hash"),
                    (value.get("snapshot") or {}).get("fingerprint"),
                )
                for name, value in raw_before.items()
            }
            gateway = FakeGateway()
            service = ChangeGovernanceService(
                ChangePlanRepository(root),
                gateway,
                AuditLogger(str(Path(directory) / "audit.jsonl"), "rc1-upgrade-secret"),
                now=lambda: NOW,
            )
            self.assertEqual(repository_snapshot(root), before)
            for _ in range(3):
                for plan_id in ids.values():
                    self.assertEqual(service.get_plan(plan_id)["plan_id"], plan_id)
                service.list_plans(limit=100)
                service.health_summary()
                service.pending_external_reviews()
            self.assertEqual(repository_snapshot(root), before)
            after = {
                name: service.repository.get(plan_id).to_dict()
                for name, plan_id in ids.items()
            }
            self.assertEqual(
                {name: len(value["events"]) for name, value in after.items()},
                event_counts,
            )
            self.assertEqual(
                {
                    name: (
                        value["current_state_fingerprint"],
                        value["proposed_config_hash"],
                        value["approval"].get("bound_plan_hash"),
                        (value.get("snapshot") or {}).get("fingerprint"),
                    )
                    for name, value in after.items()
                },
                hashes,
            )
            self.assertEqual(
                {name: value["status"] for name, value in after.items()},
                {name: value["status"] for name, value in raw_before.items()},
            )
            self.assertEqual(
                {name: value["approval"]["state"] for name, value in after.items()},
                {name: value["approval"]["state"] for name, value in raw_before.items()},
            )
            self.assertEqual(after["legacy-terminal"]["approval"]["authority_version"], 1)
            self.assertEqual(after["legacy-active"]["approval"]["authority_version"], 1)
            self.assertEqual(
                {item["plan_id"] for item in service.pending_external_reviews()},
                {ids["pending-challenge"], ids["rollback-pending"]},
            )
            self.assertTrue(service.get_plan(ids["approved-plan"])["apply_allowed"])
            self.assertFalse(service.get_plan(ids["expired-challenge"])["apply_allowed"])
            self.assertEqual(gateway.reads, 0)
            self.assertEqual(gateway.writes, 0)
            for name in (
                "expired-plan",
                "applied-plan",
                "rolled-back",
                "rejected-plan",
                "legacy-terminal",
            ):
                self.assertIn(PlanStatus(after[name]["status"]), TERMINAL_STATUSES)

    async def test_executable_legacy_authority_fails_closed_without_provider_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plans"
            ids = create_beta26_repository(root)
            gateway = FakeGateway()
            service = ChangeGovernanceService(
                ChangePlanRepository(root), gateway, now=lambda: NOW
            )
            plan = service.repository.get(ids["legacy-active"])
            with self.assertRaises(GovernanceError) as raised:
                await service.apply(plan.plan_id, service.plan_hash(plan))
            self.assertEqual(raised.exception.code, ErrorCode.APPROVAL_AUTHORITY_MISMATCH)
            self.assertEqual(gateway.reads, 0)
            self.assertEqual(gateway.writes, 0)
            rejected = service.repository.get(plan.plan_id)
            self.assertEqual(rejected.status, PlanStatus.APPROVED)
            self.assertEqual(rejected.approval.authority_version, 1)
            self.assertEqual(rejected.events[-1].event, "change_apply_rejected")


if __name__ == "__main__":
    unittest.main()
