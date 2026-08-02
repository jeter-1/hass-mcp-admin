#!/usr/bin/env python3
"""Generate neutral prohibited-plan fixtures through the shipped Beta 6 code."""

from __future__ import annotations

import argparse
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from unittest.mock import patch


BETA6_SOURCE_COMMIT = "5c7eebf962837f85f2309b1b5099401fb075cd6e"
FIXED_TIME = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
CONTRACT_V2_FIXTURE_NAMES = (
    "beta6_prohibited_superseded_contract_v2_a.json",
    "beta6_prohibited_superseded_contract_v2_b.json",
)
LEGACY_FIXTURE_NAMES = (
    "beta6_legacy_prohibited_expired_automation_a.json",
    "beta6_legacy_prohibited_expired_automation_b.json",
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git_output(worktree: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(worktree), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _verify_beta6_worktree(worktree: Path) -> None:
    if _git_output(worktree, "rev-parse", "HEAD") != BETA6_SOURCE_COMMIT:
        raise SystemExit(
            "The supplied worktree is not the exact shipped Beta 6 source"
        )
    if _git_output(worktree, "status", "--porcelain"):
        raise SystemExit("The supplied Beta 6 worktree must be clean")
    package = worktree / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
    if not package.is_dir():
        raise SystemExit("The supplied Beta 6 worktree is incomplete")


class _Clock:
    def __init__(self) -> None:
        self.value = FIXED_TIME

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


class _Gateway:
    def __init__(self, configs: dict[str, dict[str, object]]) -> None:
        self.configs = copy.deepcopy(configs)
        self.writes = 0

    async def read(
        self, resource_type: str, resource_id: str
    ) -> dict[str, object] | None:
        if resource_type != "automation":
            return None
        value = self.configs.get(resource_id)
        return copy.deepcopy(value) if value is not None else None

    async def get(self, automation_id: str) -> dict[str, object] | None:
        return await self.read("automation", automation_id)

    async def write(self, _automation_id: str, _config: dict[str, object]):
        self.writes += 1
        raise AssertionError("fixture generation must not dispatch a provider")

    async def validate(self) -> dict[str, object]:
        return {"result": "valid", "errors": None}


def _baseline(
    automation_id: str, *, action: dict[str, object], guarded: bool
) -> dict[str, object]:
    return {
        "id": automation_id,
        "alias": f"Beta 6 compatibility {automation_id}",
        "description": "Neutral historical fixture baseline",
        "trigger": [
            {
                "platform": "event",
                "event_type": f"{automation_id}_event",
            }
        ],
        "condition": (
            [
                {
                    "condition": "state",
                    "entity_id": "input_boolean.beta6_compat_guard",
                    "state": "off",
                }
            ]
            if guarded
            else []
        ),
        "action": [copy.deepcopy(action)],
        "mode": "single",
    }


def _operation(
    operation_id: str,
    automation_id: str,
    proposed: dict[str, object],
) -> dict[str, object]:
    value = copy.deepcopy(proposed)
    value.pop("id", None)
    return {
        "operation_id": operation_id,
        "resource_type": "automation",
        "action": "update",
        "target_id": automation_id,
        "proposed_config": value,
    }


async def _generate_contract_v2_records(worktree: Path) -> list[bytes]:
    beta_package = worktree / "hass_mcp_engineering_beta"
    sys.path.insert(0, str(beta_package))

    from ha_mcp_engineering.governance.service import (  # type: ignore[import-not-found]
        ChangeGovernanceService,
    )
    from ha_mcp_engineering.governance.storage import (  # type: ignore[import-not-found]
        ChangePlanRepository,
    )
    from ha_mcp_engineering.request_context import (  # type: ignore[import-not-found]
        begin_request,
        end_request,
    )

    first_action = {
        "service": "lock.unlock",
        "target": {"device_id": "beta6_compat_neutral_lock_a"},
    }
    second_action = {
        "service": "notify.beta6_compat_fixture",
        "data": {"message": "Neutral fixture only"},
    }
    baselines = {
        "beta6_compat_condition": _baseline(
            "beta6_compat_condition", action=first_action, guarded=True
        ),
        "beta6_compat_lock": _baseline(
            "beta6_compat_lock", action=second_action, guarded=False
        ),
    }
    gateway = _Gateway(baselines)
    fixed_ids = iter(
        uuid.UUID(hex=value)
        for value in (
            "a1000000000000000000000000000001",
            "a1000000000000000000000000000002",
            "b2000000000000000000000000000001",
            "b2000000000000000000000000000002",
        )
    )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "plans"
        repository = ChangePlanRepository(root)
        service = ChangeGovernanceService(
            repository,
            gateway,
            now=_Clock(),
        )
        telemetry, context = begin_request("beta6-compat-fixture-request")
        telemetry.caller_id = "beta6-compat-fixture-caller"
        try:
            with patch(
                "ha_mcp_engineering.governance.service.uuid.uuid4",
                side_effect=lambda: next(fixed_ids),
            ):
                proposed_a = copy.deepcopy(baselines["beta6_compat_condition"])
                proposed_a.pop("id", None)
                proposed_a["condition"] = []
                created_a = await service.create_configuration_plan(
                    title="Beta 6 prohibited condition fixture",
                    description=(
                        "Neutral safety-reducing condition compatibility record"
                    ),
                    operations=[
                        _operation(
                            "beta6_condition_update",
                            "beta6_compat_condition",
                            proposed_a,
                        )
                    ],
                )
                if created_a["policy_decision"]["policy_class"] != "prohibited":
                    raise AssertionError("fixture A was not classified prohibited")

                replacement_a = copy.deepcopy(
                    baselines["beta6_compat_condition"]
                )
                replacement_a["description"] = "Neutral replacement A"
                await service.create_configuration_plan(
                    title="Beta 6 replacement condition fixture",
                    description="Supersedes the prohibited condition fixture",
                    operations=[
                        _operation(
                            "beta6_condition_replacement",
                            "beta6_compat_condition",
                            replacement_a,
                        )
                    ],
                )

                proposed_b = copy.deepcopy(baselines["beta6_compat_lock"])
                proposed_b.pop("id", None)
                proposed_b["action"] = [
                    {
                        "service": "lock.unlock",
                        "target": {
                            "device_id": "beta6_compat_neutral_lock_b"
                        },
                    }
                ]
                created_b = await service.create_configuration_plan(
                    title="Beta 6 prohibited lock fixture",
                    description="Neutral lock policy compatibility record",
                    operations=[
                        _operation(
                            "beta6_lock_update",
                            "beta6_compat_lock",
                            proposed_b,
                        )
                    ],
                )
                if created_b["policy_decision"]["policy_class"] != "prohibited":
                    raise AssertionError("fixture B was not classified prohibited")

                replacement_b = copy.deepcopy(baselines["beta6_compat_lock"])
                replacement_b["description"] = "Neutral replacement B"
                await service.create_configuration_plan(
                    title="Beta 6 replacement lock fixture",
                    description="Supersedes the prohibited lock fixture",
                    operations=[
                        _operation(
                            "beta6_lock_replacement",
                            "beta6_compat_lock",
                            replacement_b,
                        )
                    ],
                )
        finally:
            end_request(context)

        if gateway.writes:
            raise AssertionError("fixture generation dispatched a provider")
        records = []
        for plan_id in (created_a["plan_id"], created_b["plan_id"]):
            path = root / f"{plan_id}.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("contract_version") != 2:
                raise AssertionError("Beta 6 did not persist contract version 2")
            if payload.get("status") != "superseded":
                raise AssertionError("Beta 6 did not supersede the fixture")
            if not payload.get("operations"):
                raise AssertionError("Beta 6 fixture omitted operations")
            records.append(path.read_bytes())
        return records


async def _generate_legacy_expired_records(worktree: Path) -> list[bytes]:
    beta_package = worktree / "hass_mcp_engineering_beta"
    if str(beta_package) not in sys.path:
        sys.path.insert(0, str(beta_package))

    from ha_mcp_engineering.errors import (  # type: ignore[import-not-found]
        ErrorCode,
        GovernanceError,
    )
    from ha_mcp_engineering.governance.service import (  # type: ignore[import-not-found]
        ChangeGovernanceService,
    )
    from ha_mcp_engineering.governance.storage import (  # type: ignore[import-not-found]
        ChangePlanRepository,
    )
    from ha_mcp_engineering.request_context import (  # type: ignore[import-not-found]
        begin_request,
        end_request,
    )

    automation_ids = (
        "beta6_legacy_expired_a",
        "beta6_legacy_expired_b",
    )
    baseline_action = {
        "service": "notify.beta6_compat_fixture",
        "data": {"message": "Neutral fixture only"},
    }
    baselines = {
        automation_id: _baseline(
            automation_id,
            action=baseline_action,
            guarded=False,
        )
        for automation_id in automation_ids
    }
    gateway = _Gateway(baselines)
    clock = _Clock()
    fixed_ids = iter(
        uuid.UUID(hex=value)
        for value in (
            "c3000000000000000000000000000001",
            "d4000000000000000000000000000001",
        )
    )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "plans"
        repository = ChangePlanRepository(root)
        service = ChangeGovernanceService(
            repository,
            gateway,
            now=clock,
        )
        telemetry, context = begin_request(
            "beta6-legacy-expired-fixture-request"
        )
        telemetry.caller_id = "beta6-legacy-expired-fixture-caller"
        created: list[dict[str, object]] = []
        try:
            with patch(
                "ha_mcp_engineering.governance.service.uuid.uuid4",
                side_effect=lambda: next(fixed_ids),
            ):
                for index, automation_id in enumerate(automation_ids):
                    proposed = copy.deepcopy(baselines[automation_id])
                    proposed.pop("id", None)
                    proposed["action"] = [
                        {
                            "service": "lock.unlock",
                            "target": {
                                "device_id": (
                                    f"beta6_legacy_neutral_lock_{index + 1}"
                                )
                            },
                        }
                    ]
                    result = await service.create_plan(
                        title=f"Beta 6 legacy expired fixture {index + 1}",
                        description=(
                            "Neutral legacy prohibited automation fixture"
                        ),
                        operation="update_automation",
                        automation_id=automation_id,
                        proposed_config=proposed,
                        expiration_minutes=5,
                    )
                    if (
                        result["policy_decision"]["policy_class"]
                        != "prohibited"
                    ):
                        raise AssertionError(
                            "legacy fixture was not classified prohibited"
                        )
                    created.append(result)

                    if index == 1:
                        try:
                            service.approve(
                                str(result["plan_id"]),
                                str(result["plan_hash"]),
                            )
                        except GovernanceError as exc:
                            if exc.code != ErrorCode.PROHIBITED_CHANGE:
                                raise
                        else:
                            raise AssertionError(
                                "Beta 6 approved a prohibited legacy fixture"
                            )
                        try:
                            await service.apply(
                                str(result["plan_id"]),
                                str(result["plan_hash"]),
                            )
                        except GovernanceError as exc:
                            if exc.code != ErrorCode.PROHIBITED_CHANGE:
                                raise
                        else:
                            raise AssertionError(
                                "Beta 6 applied a prohibited legacy fixture"
                            )

                    clock.advance(minutes=6)
                    expired = service.get_plan(str(result["plan_id"]))
                    if expired["status"] != "expired":
                        raise AssertionError(
                            "Beta 6 did not expire the legacy fixture"
                        )
        finally:
            end_request(context)

        if gateway.writes:
            raise AssertionError("fixture generation dispatched a provider")
        if service.task_repository.list():
            raise AssertionError("fixture generation created an execution task")

        expected_sequences = (
            (
                ("change_plan_created", "success", None),
                (
                    "change_plan_expired",
                    "rejected",
                    ErrorCode.CHANGE_PLAN_EXPIRED.value,
                ),
            ),
            (
                ("change_plan_created", "success", None),
                (
                    "policy_approval_rejected",
                    "rejected",
                    ErrorCode.PROHIBITED_CHANGE.value,
                ),
                (
                    "change_apply_rejected",
                    "rejected",
                    ErrorCode.PROHIBITED_CHANGE.value,
                ),
                (
                    "change_plan_expired",
                    "rejected",
                    ErrorCode.CHANGE_PLAN_EXPIRED.value,
                ),
            ),
        )
        records = []
        for result, automation_id, expected_events in zip(
            created,
            automation_ids,
            expected_sequences,
            strict=True,
        ):
            path = root / f"{result['plan_id']}.json"
            raw = path.read_bytes()
            payload = json.loads(raw)
            if "contract_version" in payload:
                raise AssertionError(
                    "Beta 6 serialized contract_version on a legacy plan"
                )
            if "operations" in payload:
                raise AssertionError(
                    "Beta 6 serialized operations on a legacy plan"
                )
            if payload.get("operation") != "update_automation":
                raise AssertionError("legacy fixture operation changed")
            if payload.get("target") != {
                "target_type": "automation",
                "target_id": automation_id,
            }:
                raise AssertionError("legacy fixture target changed")
            if payload.get("status") != "expired":
                raise AssertionError("legacy fixture is not expired")
            observed_events = tuple(
                (
                    event.get("event"),
                    event.get("result_status"),
                    event.get("error_code"),
                )
                for event in payload.get("events", [])
            )
            if observed_events != expected_events:
                raise AssertionError(
                    "legacy fixture event sequence does not match Beta 6"
                )
            records.append(raw)
        return records


def _contract_v2_provenance(
    fixture_bytes: list[bytes], *, generated_at: str
) -> dict[str, object]:
    script_bytes = Path(__file__).read_bytes()
    fixtures = []
    for name, raw in zip(
        CONTRACT_V2_FIXTURE_NAMES, fixture_bytes, strict=True
    ):
        payload = json.loads(raw)
        fixtures.append(
            {
                "path": f"tests/fixtures/{name}",
                "sha256": _sha256(raw),
                "contract_version": payload["contract_version"],
                "operation_count": len(payload["operations"]),
                "operation_execution_status_values": sorted(
                    {
                        operation["execution_status"]
                        for operation in payload["operations"]
                    }
                ),
            }
        )
    return {
        "schema_version": 1,
        "historical_source_commit": BETA6_SOURCE_COMMIT,
        "generator": (
            "scripts/generate_beta6_prohibited_compatibility_fixtures.py"
        ),
        "generator_sha256": _sha256(script_bytes),
        "generator_command": (
            "python3 scripts/generate_beta6_prohibited_compatibility_fixtures.py "
            "--beta6-worktree <exact-beta6-worktree> "
            "--output-dir tests/fixtures "
            f"--generated-at {generated_at}"
        ),
        "generation_timestamp": generated_at,
        "fixtures": fixtures,
    }


def _legacy_provenance(
    fixture_bytes: list[bytes], *, generated_at: str
) -> dict[str, object]:
    script_bytes = Path(__file__).read_bytes()
    fixtures = []
    for name, raw in zip(LEGACY_FIXTURE_NAMES, fixture_bytes, strict=True):
        payload = json.loads(raw)
        fixtures.append(
            {
                "path": f"tests/fixtures/{name}",
                "sha256": _sha256(raw),
                "contract_version_key_present": (
                    "contract_version" in payload
                ),
                "deserialized_contract_version": 1,
                "operation": payload["operation"],
                "target_type": payload["target"]["target_type"],
                "status": payload["status"],
                "operations_key_present": "operations" in payload,
                "expected_event_sequence": [
                    {
                        "event": event["event"],
                        "result_status": event["result_status"],
                        "error_code": event.get("error_code"),
                    }
                    for event in payload["events"]
                ],
            }
        )
    return {
        "schema_version": 1,
        "historical_source_commit": BETA6_SOURCE_COMMIT,
        "generator": (
            "scripts/generate_beta6_prohibited_compatibility_fixtures.py"
        ),
        "generator_sha256": _sha256(script_bytes),
        "generator_command": (
            "python3 scripts/generate_beta6_prohibited_compatibility_fixtures.py "
            "--beta6-worktree <exact-beta6-worktree> "
            "--output-dir tests/fixtures "
            f"--generated-at {generated_at}"
        ),
        "generation_timestamp": generated_at,
        "writer_path": "ChangeGovernanceService.create_plan",
        "terminal_lifecycle": "ChangeGovernanceService._expire_if_needed",
        "fixtures": fixtures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--beta6-worktree", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--generated-at", required=True)
    args = parser.parse_args()

    try:
        datetime.fromisoformat(args.generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit("--generated-at must be an ISO-8601 timestamp") from exc
    worktree = args.beta6_worktree.resolve()
    _verify_beta6_worktree(worktree)
    contract_v2_records = asyncio.run(
        _generate_contract_v2_records(worktree)
    )
    legacy_records = asyncio.run(_generate_legacy_expired_records(worktree))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, raw in zip(
        CONTRACT_V2_FIXTURE_NAMES,
        contract_v2_records,
        strict=True,
    ):
        (args.output_dir / name).write_bytes(raw)
    for name, raw in zip(LEGACY_FIXTURE_NAMES, legacy_records, strict=True):
        (args.output_dir / name).write_bytes(raw)
    provenance = _contract_v2_provenance(
        contract_v2_records,
        generated_at=args.generated_at,
    )
    provenance_path = (
        args.output_dir
        / "beta6_prohibited_superseded_contract_v2_provenance.json"
    )
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    legacy_provenance = _legacy_provenance(
        legacy_records,
        generated_at=args.generated_at,
    )
    legacy_provenance_path = (
        args.output_dir
        / "beta6_legacy_prohibited_expired_automation_provenance.json"
    )
    legacy_provenance_path.write_text(
        json.dumps(legacy_provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
