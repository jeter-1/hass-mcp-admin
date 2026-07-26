"""Governed 2.1A backup planning, dispatch, verification, and recovery."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.clients.upstream_read import (  # noqa: E402
    BeforeDispatchFailure,
    McpReadCatalog,
)
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.config_validation import (  # noqa: E402
    normalize_configuration_validation,
)
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalState,
    PlanStatus,
)
from ha_mcp_engineering.governance.operational import (  # noqa: E402
    BackupAdministrationGateway,
    OperationalGatewayError,
    normalize_backup_name,
)
from ha_mcp_engineering.governance.service import ChangeGovernanceService  # noqa: E402
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
    ChangePlanStorageError,
)
from ha_mcp_engineering.providers.operational_backup import (  # noqa: E402
    ReviewedOperationalBackupProvider,
)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    load_reviewed_upstream_release_registry,
)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs) -> None:
        self.value += timedelta(**kwargs)


def provider_evidence(version: str = "7.14.2") -> dict:
    release = load_reviewed_upstream_release_registry().by_version[version]
    contract = release.tool_contracts_by_name["ha_manage_backup"]
    return {
        "provider": "upstream_operational_backup",
        "server_name": "ha-mcp",
        "server_version": version,
        "protocol_version": "2025-03-26",
        "compatibility_entry_id": release.entry_id,
        "reviewed_source_commit": release.source_commit,
        "reviewed_image_index_digest": release.image_index_digest,
        "catalog_fingerprint": release.catalog_fingerprint,
        "tool_contract_fingerprint": contract.runtime_contract_fingerprint,
        "argument_constraints": {
            "scope": "snapshot",
            "action": "create",
            "name": "bounded_engineering_value",
            "restore_allowed": False,
            "delete_allowed": False,
            "arbitrary_arguments_allowed": False,
        },
        "runtime_artifact_observed": False,
        "fallback": "none",
    }


class LegacyGateway:
    async def get(self, _automation_id):
        return None

    async def write(self, *_args):
        return {"result": "ok"}

    async def validate(self):
        return {"result": "valid", "errors": None}

    async def read(self, _resource_type, _resource_id):
        return None

    async def validate_all(self):
        return {"result": "valid", "errors": None}


class FakeOperationalGateway:
    def __init__(self) -> None:
        self.dispatch_count = 0
        self.planning_count = 0
        self.mode = "success"
        self.verification = "verified"
        self.evidence = provider_evidence()
        self.baseline = {
            "inventory_readable": True,
            "inventory_count": 1,
            "backup_ids": ["existing"],
            "backups": [
                {
                    "backup_id": "existing",
                    "name": "Existing",
                    "date": "2026-07-25T12:00:00+00:00",
                    "size_bytes": 20,
                }
            ],
            "operation_state": "idle",
            "last_action_event": {
                "state": "completed",
                "backup_id": "existing",
            },
        }

    async def planning_evidence(self):
        self.planning_count += 1
        if self.mode == "unavailable":
            raise OperationalGatewayError("provider_unavailable")
        return {
            "provider": deepcopy(self.evidence),
            "baseline": deepcopy(self.baseline),
        }

    async def create_full_backup(self, name, *, before_dispatch):
        if self.mode == "reject_before_dispatch":
            raise OperationalGatewayError("provider_unavailable")
        await before_dispatch()
        self.dispatch_count += 1
        if self.mode == "ambiguous":
            raise OperationalGatewayError(
                "indeterminate_dispatch", dispatched=True
            )
        if self.mode == "operation_timeout":
            raise OperationalGatewayError(
                "provider_timeout", dispatched=True
            )
        if self.mode == "permission":
            raise OperationalGatewayError(
                "permission_failure", dispatched=True
            )
        if self.mode == "rejected":
            raise OperationalGatewayError(
                "backup_rejected", dispatched=True
            )
        if self.mode == "failed":
            raise OperationalGatewayError("backup_failed", dispatched=True)
        return SimpleNamespace(
            backup_id="backup-new",
            operation_id="job-new",
            name=name,
        )

    async def verify_full_backup(self, **_kwargs):
        if self.verification == "unavailable":
            raise OperationalGatewayError("verification_timeout")
        if self.verification == "pending":
            return {
                "status": "pending",
                "operation_completed": False,
                "inventory_readable": True,
                "mismatch_fields": ["operation_not_completed"],
                "evidence": {"redispatch_performed": False},
            }
        if self.verification == "failed":
            return {
                "status": "failed",
                "operation_completed": True,
                "inventory_readable": True,
                "mismatch_fields": ["backup_size"],
                "evidence": {"backup_id": "backup-new"},
            }
        return {
            "status": "verified",
            "operation_completed": True,
            "inventory_readable": True,
            "mismatch_fields": [],
            "evidence": {
                "backup_id": "backup-new",
                "provider_operation_id": "job-new",
                "name": "Nightly safe backup",
                "date": "2026-07-26T12:00:01+00:00",
                "size_bytes": 100,
                "new_relative_to_baseline": True,
                "archive_integrity_validated": False,
            },
        }

    def health_snapshot(self):
        return {
            "configured": True,
            "operational_status": "available",
            "dispatch_count": self.dispatch_count,
            "fallback_count": 0,
            "fallback_policy": "none",
        }


class OperationalBackupLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.gateway = FakeOperationalGateway()
        self.repository = ChangePlanRepository(
            Path(self.temp.name) / "plans"
        )
        self.audit_path = Path(self.temp.name) / "audit.jsonl"
        self.service = ChangeGovernanceService(
            self.repository,
            LegacyGateway(),
            AuditLogger(
                str(self.audit_path), "operational-test-access-secret"
            ),
            now=self.clock,
            sensitive_values=(
                "operational-test-access-secret",
                "supervisor-test-token",
            ),
            operational_gateway=self.gateway,
        )
        self.telemetry, self.context = begin_request("operational-request")
        self.telemetry.caller_id = "mcp-requester"

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    def test_configuration_validation_is_reusable_bounded_and_strict(self):
        status, details = normalize_configuration_validation(
            {"result": "valid", "errors": None},
            known_secrets=("synthetic-secret",),
        )
        self.assertEqual(status, "valid")
        self.assertEqual(details["reason"], "explicit_valid_result")
        for result, reason in (
            ({"result": "invalid", "errors": "synthetic-secret"}, "configuration_invalid"),
            ({"result": "valid"}, "missing_errors"),
            ("valid", "malformed_response"),
        ):
            with self.subTest(reason=reason):
                status, details = normalize_configuration_validation(
                    result,
                    known_secrets=("synthetic-secret",),
                )
                self.assertEqual(status, "failed")
                self.assertEqual(details["reason"], reason)
                self.assertNotIn("synthetic-secret", str(details))

    async def create(self, name="Nightly safe backup"):
        return await self.service.create_backup_plan(backup_name=name)

    async def grant(self, created):
        plan = created["plan"]
        pending = self.service.approve(
            plan["plan_id"], plan["plan_hash"]
        )
        _, csrf = await self.service.issue_external_csrf(
            plan["plan_id"], pending["challenge_id"]
        )
        await self.service.decide_external_approval(
            plan_id=plan["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=plan["plan_hash"],
            approval_kind="apply",
            csrf_nonce=csrf,
            decision="approve",
            approver_principal="home_assistant_admin_ingress:fixture",
        )
        return plan

    async def test_planning_is_proposal_only_and_hash_is_deterministic(self):
        created = await self.create()
        plan = created["plan"]
        self.assertTrue(created["proposal_only"])
        self.assertFalse(created["provider_dispatch_occurred"])
        self.assertEqual(self.gateway.dispatch_count, 0)
        persisted = self.repository.get(plan["plan_id"])
        self.assertEqual(
            self.service.plan_hash(persisted), plan["plan_hash"]
        )
        self.assertEqual(persisted.risk.level.value, "medium")
        self.assertFalse(persisted.rollback.available)
        self.assertFalse(persisted.operational.rollback_available)
        persisted.operational.requested_name = "Different"
        self.assertNotEqual(
            self.service.plan_hash(persisted), plan["plan_hash"]
        )

    async def test_existing_plan_contracts_do_not_gain_operational_fields(self):
        legacy = await self.service.create_plan(
            title="Legacy",
            description="Compatibility fixture",
            operation="create_automation",
            automation_id="legacy_fixture",
            proposed_config={
                "alias": "Legacy",
                "trigger": [],
                "condition": [],
                "action": [],
            },
        )
        legacy_record = self.repository.get(legacy["plan_id"]).to_dict()
        self.assertNotIn("operational", legacy_record)
        self.assertNotIn("plan_family", legacy_record)
        configuration = await self.service.create_configuration_plan(
            title="Configuration",
            description="Compatibility fixture",
            operations=[
                {
                    "operation_id": "helper_create",
                    "resource_type": "helper",
                    "helper_type": "input_boolean",
                    "action": "create",
                    "target_id": "input_boolean.compatibility",
                    "proposed_config": {
                        "name": "Compatibility",
                        "icon": "mdi:toggle-switch",
                    },
                }
            ],
        )
        configuration_record = self.repository.get(
            configuration["plan_id"]
        ).to_dict()
        self.assertEqual(configuration_record["contract_version"], 2)
        self.assertNotIn("operational", configuration_record)
        self.assertNotIn("plan_family", configuration_record)

    async def test_safe_generated_and_invalid_names(self):
        created = await self.create("")
        self.assertRegex(
            created["plan"]["operational"]["requested_name"],
            r"^Engineering_Backup_",
        )
        for value in (
            "../escape",
            " leading",
            "trailing ",
            "bad/control\n",
            "x" * 97,
            "{{ secret }}",
        ):
            with self.subTest(value=value):
                with self.assertRaises(GovernanceError) as raised:
                    await self.create(value)
                self.assertEqual(raised.exception.code, ErrorCode.INVALID_REQUEST)

    async def test_apply_requires_exact_external_approval(self):
        created = await self.create()
        plan = created["plan"]
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(
            raised.exception.code, ErrorCode.EXTERNAL_APPROVAL_REQUIRED
        )
        self.assertEqual(self.gateway.dispatch_count, 0)
        await self.grant(created)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(plan["plan_id"], "")
        self.assertEqual(
            raised.exception.code, ErrorCode.APPROVAL_HASH_MISMATCH
        )
        self.assertEqual(self.gateway.dispatch_count, 0)

    async def test_expired_and_rejected_approvals_never_dispatch(self):
        expired = await self.create("Expires safely")
        expired_plan = await self.grant(expired)
        self.clock.advance(hours=2)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                expired_plan["plan_id"], expired_plan["plan_hash"]
            )
        self.assertEqual(raised.exception.code, ErrorCode.CHANGE_PLAN_EXPIRED)

        self.clock.value = datetime(
            2026, 7, 26, 15, 0, tzinfo=timezone.utc
        )
        rejected = await self.create("Rejected safely")
        plan = rejected["plan"]
        pending = self.service.approve(
            plan["plan_id"], plan["plan_hash"]
        )
        _, csrf = await self.service.issue_external_csrf(
            plan["plan_id"], pending["challenge_id"]
        )
        await self.service.decide_external_approval(
            plan_id=plan["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=plan["plan_hash"],
            approval_kind="apply",
            csrf_nonce=csrf,
            decision="reject",
            approver_principal="home_assistant_admin_ingress:fixture",
        )
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(
            raised.exception.code, ErrorCode.CHANGE_PLAN_REJECTED
        )
        self.assertEqual(self.gateway.dispatch_count, 0)

    async def test_plan_mutation_invalidates_pending_external_challenge(self):
        created = await self.create()
        plan = created["plan"]
        pending = self.service.approve(
            plan["plan_id"], plan["plan_hash"]
        )
        _, csrf = await self.service.issue_external_csrf(
            plan["plan_id"], pending["challenge_id"]
        )
        persisted = self.repository.get(plan["plan_id"])
        persisted.operational.requested_name = "Mutated"
        self.repository.save(persisted)
        with self.assertRaises(GovernanceError) as raised:
            await self.service.decide_external_approval(
                plan_id=plan["plan_id"],
                challenge_id=pending["challenge_id"],
                expected_plan_hash=plan["plan_hash"],
                approval_kind="apply",
                csrf_nonce=csrf,
                decision="approve",
                approver_principal=(
                    "home_assistant_admin_ingress:fixture"
                ),
            )
        self.assertEqual(
            raised.exception.code, ErrorCode.EXTERNAL_APPROVAL_INVALID
        )
        self.assertEqual(self.gateway.dispatch_count, 0)

    async def test_preapply_inventory_drift_fails_stale_without_dispatch(self):
        created = await self.create()
        plan = await self.grant(created)
        self.gateway.baseline["backup_ids"].append("external-new")
        self.gateway.baseline["backups"].append(
            {
                "backup_id": "external-new",
                "name": "External",
                "date": "2026-07-26T11:59:00+00:00",
                "size_bytes": 10,
            }
        )
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                plan["plan_id"], plan["plan_hash"]
            )
        self.assertEqual(raised.exception.code, ErrorCode.STALE_TARGET_STATE)
        self.assertEqual(self.gateway.dispatch_count, 0)

    async def test_busy_backup_provider_is_rejected_during_plan_and_apply(self):
        self.gateway.baseline["operation_state"] = "create_backup"
        with self.assertRaises(GovernanceError) as planning:
            await self.create("Busy planning")
        self.assertEqual(
            planning.exception.code, ErrorCode.BACKUP_PROVIDER_UNAVAILABLE
        )

        self.gateway.baseline["operation_state"] = "idle"
        created = await self.create("Busy apply")
        plan = await self.grant(created)
        self.gateway.baseline["operation_state"] = "create_backup"
        with self.assertRaises(GovernanceError) as applying:
            await self.service.apply(
                plan["plan_id"], plan["plan_hash"]
            )
        self.assertEqual(applying.exception.code, ErrorCode.STALE_TARGET_STATE)
        self.assertEqual(self.gateway.dispatch_count, 0)

    async def test_positive_apply_dispatches_once_and_verifies(self):
        created = await self.create()
        plan = await self.grant(created)
        result = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.gateway.dispatch_count, 1)
        persisted = self.repository.get(plan["plan_id"])
        self.assertEqual(persisted.status, PlanStatus.APPLIED)
        self.assertEqual(
            persisted.approval.state, ApprovalState.CONSUMED
        )
        self.assertEqual(
            persisted.operational.dispatch["attempt_count"], 1
        )
        self.assertFalse(
            persisted.operational.verification.archive_integrity_validated
        )
        replay = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(replay["status"], "already_applied")
        self.assertEqual(self.gateway.dispatch_count, 1)

    async def test_ambiguous_dispatch_resolves_without_redispatch(self):
        created = await self.create()
        plan = await self.grant(created)
        self.gateway.mode = "ambiguous"
        result = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.gateway.dispatch_count, 1)
        self.assertFalse(result["redispatch_performed"])

    async def test_definitive_provider_failure_is_terminal_without_verification(self):
        for index, (mode, code) in enumerate(
            (
                ("permission", ErrorCode.BACKUP_PERMISSION_FAILURE),
                ("rejected", ErrorCode.BACKUP_CREATION_REJECTED),
                ("failed", ErrorCode.BACKUP_CREATION_FAILED),
            )
        ):
            with self.subTest(mode=mode):
                created = await self.create(f"Terminal {index}")
                plan = await self.grant(created)
                self.gateway.mode = mode
                before = self.gateway.dispatch_count
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.apply(
                        plan["plan_id"], plan["plan_hash"]
                    )
                self.assertEqual(raised.exception.code, code)
                persisted = self.repository.get(plan["plan_id"])
                self.assertEqual(persisted.status, PlanStatus.FAILED)
                self.assertEqual(
                    persisted.approval.state, ApprovalState.CONSUMED
                )
                self.assertEqual(
                    persisted.operational.verification.attempt_count, 0
                )
                self.assertEqual(
                    self.gateway.dispatch_count - before, 1
                )
                with self.assertRaises(GovernanceError) as replay:
                    await self.service.apply(
                        plan["plan_id"], plan["plan_hash"]
                    )
                self.assertEqual(
                    replay.exception.code,
                    ErrorCode.DUPLICATE_APPLY_ATTEMPT,
                )
                self.assertEqual(
                    self.gateway.dispatch_count - before, 1
                )
                self.gateway.mode = "success"

    async def test_provider_unavailable_before_dispatch_preserves_approval(self):
        created = await self.create()
        plan = await self.grant(created)
        self.gateway.mode = "unavailable"
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                plan["plan_id"], plan["plan_hash"]
            )
        self.assertEqual(
            raised.exception.code, ErrorCode.BACKUP_PROVIDER_UNAVAILABLE
        )
        persisted = self.repository.get(plan["plan_id"])
        self.assertEqual(persisted.status, PlanStatus.APPROVED)
        self.assertEqual(persisted.approval.state, ApprovalState.APPROVED)
        self.assertEqual(self.gateway.dispatch_count, 0)

    async def test_pending_ambiguous_dispatch_never_blind_retries(self):
        created = await self.create()
        plan = await self.grant(created)
        self.gateway.mode = "ambiguous"
        self.gateway.verification = "pending"
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                plan["plan_id"], plan["plan_hash"]
            )
        self.assertEqual(
            raised.exception.code, ErrorCode.BACKUP_DISPATCH_INDETERMINATE
        )
        self.assertEqual(self.gateway.dispatch_count, 1)
        persisted = self.repository.get(plan["plan_id"])
        self.assertEqual(
            persisted.status, PlanStatus.VERIFICATION_REQUIRED
        )
        self.gateway.verification = "verified"
        resolved = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(resolved["status"], "applied")
        self.assertEqual(self.gateway.dispatch_count, 1)

    async def test_operation_timeout_requires_verification_without_redispatch(self):
        created = await self.create()
        plan = await self.grant(created)
        self.gateway.mode = "operation_timeout"
        self.gateway.verification = "pending"
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                plan["plan_id"], plan["plan_hash"]
            )
        self.assertEqual(
            raised.exception.code, ErrorCode.BACKUP_DISPATCH_INDETERMINATE
        )
        self.assertEqual(self.gateway.dispatch_count, 1)
        self.gateway.verification = "verified"
        resolved = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(resolved["status"], "applied")
        self.assertEqual(self.gateway.dispatch_count, 1)

    async def test_verification_timeout_is_retryable_only_as_readback(self):
        created = await self.create()
        plan = await self.grant(created)
        self.gateway.verification = "unavailable"
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                plan["plan_id"], plan["plan_hash"]
            )
        self.assertEqual(
            raised.exception.code, ErrorCode.BACKUP_VERIFICATION_TIMEOUT
        )
        self.assertEqual(self.gateway.dispatch_count, 1)
        self.gateway.verification = "verified"
        resolved = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(resolved["status"], "applied")
        self.assertEqual(self.gateway.dispatch_count, 1)

    async def test_expiration_after_dispatch_does_not_block_read_only_recovery(self):
        created = await self.create()
        plan = await self.grant(created)
        self.gateway.mode = "ambiguous"
        self.gateway.verification = "pending"
        with self.assertRaises(GovernanceError):
            await self.service.apply(
                plan["plan_id"], plan["plan_hash"]
            )
        self.clock.advance(hours=2)
        self.gateway.verification = "verified"
        resolved = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(resolved["status"], "applied")
        self.assertEqual(self.gateway.dispatch_count, 1)

    async def test_concurrent_apply_dispatches_once(self):
        created = await self.create()
        plan = await self.grant(created)
        first, second = await asyncio.gather(
            self.service.apply(plan["plan_id"], plan["plan_hash"]),
            self.service.apply(plan["plan_id"], plan["plan_hash"]),
        )
        self.assertEqual(
            {first["status"], second["status"]},
            {"applied", "already_applied"},
        )
        self.assertEqual(self.gateway.dispatch_count, 1)

    async def test_restart_recovery_requires_verification_without_dispatch(self):
        created = await self.create()
        plan = await self.grant(created)
        persisted = self.repository.get(plan["plan_id"])
        persisted.status = PlanStatus.APPLYING
        persisted.approval.state = ApprovalState.CONSUMED
        persisted.operational.dispatch.update(
            {
                "attempt_count": 1,
                "dispatched": True,
                "attempted_at": self.clock().isoformat(),
            }
        )
        self.repository.save(persisted)
        recovered = ChangePlanRepository(self.repository.root)
        reloaded = ChangeGovernanceService(
            recovered,
            LegacyGateway(),
            now=self.clock,
            operational_gateway=self.gateway,
        )
        after = recovered.get(plan["plan_id"])
        self.assertEqual(after.status, PlanStatus.VERIFICATION_REQUIRED)
        result = await reloaded.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.gateway.dispatch_count, 0)

    async def test_verification_failure_is_terminal_and_not_rollbackable(self):
        created = await self.create()
        plan = await self.grant(created)
        self.gateway.verification = "failed"
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(
                plan["plan_id"], plan["plan_hash"]
            )
        self.assertEqual(
            raised.exception.code, ErrorCode.BACKUP_VERIFICATION_FAILED
        )
        self.assertEqual(
            self.repository.get(plan["plan_id"]).status,
            PlanStatus.VERIFICATION_FAILED,
        )
        with self.assertRaises(GovernanceError) as rollback:
            await self.service.rollback_change(
                plan["plan_id"], plan["plan_hash"]
            )
        self.assertEqual(
            rollback.exception.code, ErrorCode.ROLLBACK_NOT_AVAILABLE
        )

    async def test_dispatch_evidence_storage_failure_prevents_provider_call(self):
        created = await self.create()
        plan = await self.grant(created)
        with patch.object(
            self.repository,
            "save",
            side_effect=ChangePlanStorageError("synthetic write failure"),
        ):
            with self.assertRaises(GovernanceError) as raised:
                await self.service.apply(
                    plan["plan_id"], plan["plan_hash"]
                )
        self.assertEqual(
            raised.exception.code, ErrorCode.CHANGE_PLAN_STORAGE_ERROR
        )
        self.assertEqual(self.gateway.dispatch_count, 0)
        persisted = self.repository.get(plan["plan_id"])
        self.assertEqual(persisted.status, PlanStatus.APPROVED)
        self.assertEqual(persisted.approval.state, ApprovalState.APPROVED)

    async def test_post_dispatch_storage_failure_recovers_by_readback_only(self):
        created = await self.create()
        plan = await self.grant(created)
        original_save = self.repository.save
        save_count = 0

        def fail_after_dispatch(value):
            nonlocal save_count
            save_count += 1
            if save_count == 2:
                raise ChangePlanStorageError(
                    "synthetic post-dispatch write failure"
                )
            original_save(value)

        with patch.object(self.repository, "save", side_effect=fail_after_dispatch):
            with self.assertRaises(GovernanceError) as raised:
                await self.service.apply(
                    plan["plan_id"], plan["plan_hash"]
                )
        self.assertEqual(
            raised.exception.code, ErrorCode.CHANGE_PLAN_STORAGE_ERROR
        )
        self.assertEqual(self.gateway.dispatch_count, 1)
        persisted = self.repository.get(plan["plan_id"])
        self.assertEqual(persisted.status, PlanStatus.APPLYING)
        self.assertEqual(
            persisted.operational.dispatch["attempt_count"], 1
        )

        resolved = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(resolved["status"], "applied")
        self.assertFalse(resolved["redispatch_performed"])
        self.assertEqual(self.gateway.dispatch_count, 1)

    async def test_audit_write_failure_does_not_erase_persisted_outcome_or_redispatch(self):
        created = await self.create()
        plan = await self.grant(created)
        self.service.audit.path = self.temp.name
        result = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(result["status"], "applied")
        self.assertGreater(self.service.audit.write_failures, 0)
        self.assertEqual(
            self.service.audit.last_error, "IsADirectoryError"
        )
        self.assertEqual(
            self.repository.get(plan["plan_id"]).status,
            PlanStatus.APPLIED,
        )
        replay = await self.service.apply(
            plan["plan_id"], plan["plan_hash"]
        )
        self.assertEqual(replay["status"], "already_applied")
        self.assertEqual(self.gateway.dispatch_count, 1)

    async def test_health_and_audit_are_bounded_and_truthful(self):
        created = await self.create()
        plan = await self.grant(created)
        await self.service.apply(plan["plan_id"], plan["plan_hash"])
        health = self.service.health_summary()[
            "operational_administration"
        ]
        self.assertEqual(health["backup_plans_created"], 1)
        self.assertEqual(health["backup_applies_attempted"], 1)
        self.assertEqual(health["successful_backups"], 1)
        self.assertEqual(health["fallback_count"], 0)
        self.assertEqual(
            self.service.health_summary()["approval_consumption_count"], 1
        )
        records = [
            json.loads(line)
            for line in self.audit_path.read_text().splitlines()
        ]
        dispatched = next(
            item
            for item in records
            if item["event"]
            == "operational_backup_dispatch_recorded"
        )
        self.assertTrue(dispatched["provider_dispatch_occurred"])
        self.assertEqual(dispatched["fallback"], "none")
        self.assertFalse(dispatched["rollback_available"])
        self.assertNotIn("supervisor-test-token", json.dumps(records))


class BackupNameTests(unittest.TestCase):
    def test_name_contract_has_no_path_or_template_interpretation(self):
        now = datetime(2026, 7, 26, tzinfo=timezone.utc)
        self.assertEqual(
            normalize_backup_name("Safe.name-1", generated_at=now),
            "Safe.name-1",
        )
        for value in ("../x", "a/b", "{{x}}", "a\x00b"):
            with self.assertRaises(ValueError):
                normalize_backup_name(value, generated_at=now)


class InventoryWebSocket:
    def __init__(self, payload):
        self.payload = payload

    async def command(self, value):
        if value != {"type": "backup/info"}:
            raise AssertionError("unexpected command")
        return deepcopy(self.payload)


class BackupVerificationContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_planning_evidence_is_bounded_and_omits_backup_metadata(self):
        class ProbeProvider:
            async def probe(self):
                return SimpleNamespace(as_dict=lambda: {"provider": "exact"})

        gateway = BackupAdministrationGateway(
            ProbeProvider(),
            InventoryWebSocket(
                {
                    "state": "idle",
                    "backups": [
                        {
                            "backup_id": "existing",
                            "name": "Sensitive descriptive name",
                            "date": "2026-07-25T12:00:00+00:00",
                        }
                    ],
                    "last_action_event": {
                        "state": "completed",
                        "backup_id": "existing",
                    },
                }
            ),
        )
        evidence = await gateway.planning_evidence()
        self.assertEqual(evidence["baseline"]["backup_ids"], ["existing"])
        self.assertNotIn("backups", evidence["baseline"])
        self.assertNotIn("Sensitive descriptive name", str(evidence))

    async def test_completed_operation_without_matching_backup_fails(self):
        gateway = BackupAdministrationGateway(
            SimpleNamespace(),
            InventoryWebSocket(
                {
                    "state": "idle",
                    "backups": [],
                    "last_action_event": {
                        "state": "completed",
                        "backup_id": "missing",
                    },
                }
            ),
        )
        result = await gateway.verify_full_backup(
            requested_name="Expected",
            baseline_ids=[],
            apply_started_at="2026-07-26T12:00:00+00:00",
            backup_id="missing",
            operation_id="job",
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["mismatch_fields"], ["backup_missing"])

    async def test_pending_operation_without_backup_remains_verification_required(self):
        gateway = BackupAdministrationGateway(
            SimpleNamespace(),
            InventoryWebSocket(
                {
                    "state": "create_backup",
                    "backups": [],
                    "last_action_event": {"state": "in_progress"},
                }
            ),
        )
        result = await gateway.verify_full_backup(
            requested_name="Expected",
            baseline_ids=[],
            apply_started_at="2026-07-26T12:00:00+00:00",
            backup_id=None,
            operation_id="job",
        )
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["mismatch_fields"], ["backup_missing"])


class FakeTransport:
    def __init__(self, catalog: McpReadCatalog):
        self.catalog = catalog
        self.arguments = None
        self.tool_name = None

    async def discover(self):
        return self.catalog

    async def execute_read(
        self,
        tool_name,
        arguments,
        *,
        timeout_seconds,
        catalog_validator,
        before_dispatch,
    ):
        catalog_validator(self.catalog)
        await before_dispatch()
        self.tool_name = tool_name
        self.arguments = deepcopy(arguments)
        return SimpleNamespace(
            call_result={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "success": True,
                                "backup_id": "backup-new",
                                "backup_job_id": "job-new",
                                "name": arguments["name"],
                                "date": "2026-07-26T12:00:00+00:00",
                                "size_bytes": 100,
                            }
                        ),
                    }
                ],
                "isError": False,
            }
        )


def exact_catalog(version: str) -> McpReadCatalog:
    release = load_reviewed_upstream_release_registry().by_version[version]
    capture = json.loads(
        (ROOT / release.capture_resource).read_text()
    )
    return McpReadCatalog(
        protocol_version="2025-03-26",
        server_name="ha-mcp",
        server_version=version,
        tools=tuple(capture["tools"]),
        connection_latency_ms=1.0,
    )


class OperationalProviderContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_reviewed_releases_admit_only_constructed_create(self):
        for version in ("7.14.1", "7.14.2"):
            with self.subTest(version=version):
                transport = FakeTransport(exact_catalog(version))
                provider = ReviewedOperationalBackupProvider()
                provider._transport = transport
                provider._state.configured = True
                dispatched = 0

                async def before():
                    nonlocal dispatched
                    dispatched += 1

                result = await provider.create_full_backup(
                    "Exact backup", before_dispatch=before
                )
                self.assertEqual(result.backup_id, "backup-new")
                self.assertEqual(transport.tool_name, "ha_manage_backup")
                self.assertEqual(
                    transport.arguments,
                    {
                        "scope": "snapshot",
                        "action": "create",
                        "name": "Exact backup",
                    },
                )
                self.assertEqual(dispatched, 1)
                self.assertEqual(
                    result.provider_evidence.server_version, version
                )
                self.assertEqual(
                    provider.health_snapshot()["fallback_count"], 0
                )

    async def test_unknown_version_and_contract_drift_fail_closed(self):
        unknown = exact_catalog("7.14.2")
        unknown = McpReadCatalog(
            **{**unknown.__dict__, "server_version": "7.14.99"}
        )
        provider = ReviewedOperationalBackupProvider()
        provider._transport = FakeTransport(unknown)
        with self.assertRaises(Exception) as raised:
            await provider.probe()
        self.assertEqual(
            getattr(raised.exception, "category", None),
            "upstream_version_mismatch",
        )

        drift = exact_catalog("7.14.2")
        tools = [deepcopy(item) for item in drift.tools]
        target = next(
            item for item in tools if item["name"] == "ha_manage_backup"
        )
        target["inputSchema"]["properties"]["action"]["enum"].append(
            "unexpected"
        )
        drift = McpReadCatalog(
            **{**drift.__dict__, "tools": tuple(tools)}
        )
        provider._transport = FakeTransport(drift)
        with self.assertRaises(Exception) as raised:
            await provider.probe()
        self.assertIn(
            getattr(raised.exception, "category", None),
            {"catalog_mismatch", "reviewed_contract_mismatch"},
        )

    async def test_strict_response_decoder_rejects_ambiguous_json(self):
        provider = ReviewedOperationalBackupProvider()
        for text in (
            '{"success":true,"success":false}',
            '{"success":true,"size_bytes":NaN}',
            '{"success":true,"nested":{"x":1,"x":2}}',
        ):
            with self.subTest(text=text):
                with self.assertRaises(Exception) as raised:
                    provider._decode_result(
                        {
                            "content": [{"type": "text", "text": text}],
                            "isError": False,
                        }
                    )
                self.assertEqual(
                    getattr(raised.exception, "category", None),
                    "invalid_response",
                )

    async def test_provider_rejects_unsafe_name_before_catalog_or_dispatch(self):
        provider = ReviewedOperationalBackupProvider()
        for name in ("../escape", "bad/name", "{{template}}", "x" * 97):
            with self.subTest(name=name):
                with self.assertRaises(Exception) as raised:
                    await provider.create_full_backup(
                        name, before_dispatch=lambda: None
                    )
                self.assertEqual(
                    getattr(raised.exception, "category", None),
                    "invalid_request",
                )
        self.assertEqual(provider.health_snapshot()["dispatch_count"], 0)

    async def test_provider_preserves_local_pre_dispatch_failure(self):
        local_failure = ChangePlanStorageError("synthetic persistence failure")

        class FailingTransport(FakeTransport):
            async def execute_read(
                self,
                tool_name,
                arguments,
                *,
                timeout_seconds,
                catalog_validator,
                before_dispatch,
            ):
                del tool_name, arguments, timeout_seconds
                catalog_validator(self.catalog)
                try:
                    await before_dispatch()
                except BaseException as exc:
                    raise BeforeDispatchFailure(exc) from None
                raise AssertionError("provider call must remain unreachable")

        provider = ReviewedOperationalBackupProvider()
        provider._transport = FailingTransport(exact_catalog("7.14.2"))

        async def before_dispatch():
            raise local_failure

        with self.assertRaises(ChangePlanStorageError) as caught:
            await provider.create_full_backup(
                "Exact backup", before_dispatch=before_dispatch
            )
        self.assertIs(caught.exception, local_failure)
        self.assertEqual(provider.health_snapshot()["dispatch_count"], 0)

    async def test_provider_health_separates_probes_dispatches_and_operation_failures(self):
        transport = FakeTransport(exact_catalog("7.14.2"))
        provider = ReviewedOperationalBackupProvider()
        provider._transport = transport
        provider._state.configured = True
        await provider.probe()
        await provider.create_full_backup(
            "Exact backup", before_dispatch=lambda: None
        )
        health = provider.health_snapshot()
        self.assertEqual(health["probe_count"], 1)
        self.assertEqual(health["probe_success_count"], 1)
        self.assertEqual(health["dispatch_count"], 1)
        self.assertEqual(health["dispatch_success_count"], 1)

        with self.assertRaises(Exception):
            provider._fail("backup_failed", dispatched=True)
        health = provider.health_snapshot()
        self.assertEqual(health["operational_status"], "available")
        self.assertEqual(health["failure_counts"]["backup_failed"], 1)
