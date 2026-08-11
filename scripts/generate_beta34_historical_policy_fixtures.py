#!/usr/bin/env python3
"""Generate exact Beta 32/Beta 33 policy-transition plan fixtures."""

from __future__ import annotations

import argparse
import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from unittest.mock import patch


BETA32_SOURCE_COMMIT = "f9d660499a05edef6af7fd9a590d7827b5983e3a"
BETA33_INITIAL_SOURCE_COMMIT = (
    "5b149b04cb12ee42abf19fc6a37ec2017c8bb0bf"
)
FIXED_TIME = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
FIXTURE_NAMES = {
    "beta32": "beta32_retained_effect_prohibited_plan.json",
    "beta33-initial": (
        "beta33_initial_retained_effect_reason_plan.json"
    ),
    "beta33-initial-consumed": (
        "beta33_initial_retained_effect_consumed_plan.json"
    ),
}
SOURCE_COMMITS = {
    "beta32": BETA32_SOURCE_COMMIT,
    "beta33-initial": BETA33_INITIAL_SOURCE_COMMIT,
    "beta33-initial-consumed": BETA33_INITIAL_SOURCE_COMMIT,
}


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


def _verify_source_worktree(profile: str, worktree: Path) -> None:
    expected = SOURCE_COMMITS[profile]
    if _git_output(worktree, "rev-parse", "HEAD") != expected:
        raise SystemExit(
            f"{profile} source worktree is not exact commit {expected}"
        )
    if _git_output(worktree, "status", "--porcelain"):
        raise SystemExit(f"{profile} source worktree must be clean")
    package = worktree / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
    if not package.is_dir():
        raise SystemExit(f"{profile} source package is incomplete")


class _Gateway:
    def __init__(self, baseline: dict[str, object]) -> None:
        self.baseline = copy.deepcopy(baseline)
        self.writes = 0

    async def read(
        self, resource_type: str, resource_id: str
    ) -> dict[str, object] | None:
        if (
            resource_type != "automation"
            or resource_id != self.baseline.get("id")
        ):
            return None
        return copy.deepcopy(self.baseline)

    async def get(
        self, automation_id: str
    ) -> dict[str, object] | None:
        return await self.read("automation", automation_id)

    async def write(
        self, *_args: object, **_kwargs: object
    ) -> dict[str, str]:
        self.writes += 1
        if len(_args) == 4 and not _kwargs:
            action, resource_type, automation_id, config = _args
            if action != "update" or resource_type != "automation":
                raise AssertionError(
                    "historical provider operation changed"
                )
        elif len(_args) == 2 and not _kwargs:
            automation_id, config = _args
        else:
            raise AssertionError("unexpected historical provider arguments")
        if automation_id != self.baseline.get("id"):
            raise AssertionError("historical provider target changed")
        if not isinstance(config, dict):
            raise AssertionError("historical provider config is malformed")
        self.baseline = {
            **copy.deepcopy(config),
            "id": automation_id,
        }
        return {"result": "ok"}

    async def validate(self) -> dict[str, object]:
        return {"result": "valid", "errors": None}


def _baseline() -> dict[str, object]:
    return {
        "id": "beta34_historical_garage_guard",
        "alias": "Beta 34 historical garage guard",
        "description": "Synthetic policy-transition evidence",
        "mode": "single",
        "trigger": [
            {
                "platform": "state",
                "entity_id": "binary_sensor.synthetic_garage_open",
                "to": "on",
                "for": "00:10:00",
            }
        ],
        "condition": [
            {
                "condition": "state",
                "entity_id": "input_boolean.synthetic_garage_enabled",
                "state": "on",
            },
            {
                "condition": "state",
                "entity_id": "binary_sensor.synthetic_garage_obstruction",
                "state": "off",
            },
            {
                "condition": "state",
                "entity_id": "binary_sensor.synthetic_garage_vehicle",
                "state": "off",
            },
            {
                "condition": "state",
                "entity_id": "binary_sensor.synthetic_garage_motion",
                "state": "off",
            },
        ],
        "action": [
            {"delay": "00:00:01"},
            {
                "service": "cover.close_cover",
                "target": {
                    "entity_id": "cover.synthetic_garage_door"
                },
            },
        ],
    }


def _operation(proposed: dict[str, object]) -> dict[str, object]:
    return {
        "operation_id": "append_presence_guard",
        "resource_type": "automation",
        "action": "update",
        "target_id": "beta34_historical_garage_guard",
        "depends_on": [],
        "proposed_config": proposed,
    }


async def _generate_child(
    *, profile: str, source_worktree: Path, output: Path
) -> None:
    _verify_source_worktree(profile, source_worktree)
    source_package = source_worktree / "hass_mcp_engineering_beta"
    sys.path.insert(0, str(source_package))

    from ha_mcp_engineering.governance.service import (  # type: ignore[import-not-found]
        ChangeGovernanceService,
    )
    from ha_mcp_engineering.governance.storage import (  # type: ignore[import-not-found]
        ChangePlanRepository,
        is_terminal_plan,
    )
    from ha_mcp_engineering.request_context import (  # type: ignore[import-not-found]
        begin_request,
        end_request,
    )

    baseline = _baseline()
    gateway = _Gateway(baseline)
    fixed_id_base = {
        "beta32": int("b3420000000000000000000000000001", 16),
        "beta33-initial": int(
            "b3430000000000000000000000000001", 16
        ),
        "beta33-initial-consumed": int(
            "b3431000000000000000000000000001", 16
        ),
    }[profile]
    fixed_ids = (
        uuid.UUID(int=value)
        for value in itertools.count(fixed_id_base)
    )
    fixed_tokens = (
        f"beta34-{profile}-token-{value:02d}"
        for value in itertools.count(1)
    )
    monotonic_values = itertools.count(1_000, 0.001)
    performance_values = itertools.count(2_000, 0.001)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "plans"
        repository = ChangePlanRepository(root)
        service = ChangeGovernanceService(
            repository,
            gateway,
            now=lambda: FIXED_TIME,
        )
        telemetry, context = begin_request(
            f"beta34-{profile}-policy-fixture"
        )
        telemetry.caller_id = "beta34-historical-policy-fixture"
        try:
            proposed = copy.deepcopy(baseline)
            proposed.pop("id", None)
            conditions = proposed.get("condition")
            assert isinstance(conditions, list)
            conditions.append(
                {
                    "condition": "state",
                    "entity_id": (
                        "binary_sensor.synthetic_garage_presence"
                    ),
                    "state": "off",
                }
            )
            with (
                patch(
                    "ha_mcp_engineering.governance.service.uuid.uuid4",
                    side_effect=lambda: next(fixed_ids),
                ),
                patch(
                    "ha_mcp_engineering.governance.service.secrets."
                    "token_urlsafe",
                    side_effect=lambda _size: next(fixed_tokens),
                ),
                patch(
                    "ha_mcp_engineering.governance.service.time.monotonic",
                    side_effect=lambda: next(monotonic_values),
                ),
                patch(
                    "ha_mcp_engineering.governance.service.time.perf_counter",
                    side_effect=lambda: next(performance_values),
                ),
            ):
                created = await service.create_configuration_plan(
                    title="Append synthetic garage presence guard",
                    description=(
                        "Source-authentic historical policy projection fixture"
                    ),
                    operations=[_operation(proposed)],
                    expiration_minutes=120,
                )
                expected_class = (
                    "prohibited" if profile == "beta32" else "elevated_admin"
                )
                if (
                    created["policy_decision"]["policy_class"]
                    != expected_class
                ):
                    raise AssertionError(
                        f"{profile} source produced an unexpected policy"
                    )

                if profile == "beta33-initial":
                    replacement = copy.deepcopy(baseline)
                    replacement.pop("id", None)
                    replacement["description"] = (
                        "Synthetic superseding historical fixture"
                    )
                    await service.create_configuration_plan(
                        title="Supersede synthetic historical guard plan",
                        description=(
                            "Makes the old policy snapshot terminal"
                        ),
                        operations=[_operation(replacement)],
                        expiration_minutes=120,
                    )
                elif profile == "beta33-initial-consumed":
                    pending = service.approve(
                        str(created["plan_id"]),
                        str(created["plan_hash"]),
                    )
                    principal = (
                        "home_assistant_admin_ingress:"
                        "beta34-historical-fixture-admin"
                    )
                    while pending.get("status") == "approval_pending":
                        _, csrf = await service.issue_external_csrf(
                            str(created["plan_id"]),
                            str(pending["challenge_id"]),
                        )
                        pending = await service.decide_external_approval(
                            plan_id=str(created["plan_id"]),
                            challenge_id=str(pending["challenge_id"]),
                            expected_plan_hash=str(created["plan_hash"]),
                            approval_kind="apply",
                            approval_action=str(
                                pending["approval_action"]
                            ),
                            csrf_nonce=csrf,
                            decision="approve",
                            approver_principal=principal,
                        )
                    applied = await service.apply(
                        str(created["plan_id"]),
                        str(created["plan_hash"]),
                    )
                    if applied.get("status") != "applied":
                        raise AssertionError(
                            "historical consumed fixture did not apply"
                        )
        finally:
            end_request(context)

        expected_writes = (
            1 if profile == "beta33-initial-consumed" else 0
        )
        if gateway.writes != expected_writes:
            raise AssertionError("historical fixture provider count changed")
        expected_tasks = expected_writes
        if len(service.task_repository.list()) != expected_tasks:
            raise AssertionError("historical fixture task count changed")
        plan = repository.get(str(created["plan_id"]))
        if plan is None or not is_terminal_plan(plan):
            raise AssertionError("historical fixture is not terminal")
        if profile == "beta32":
            if (
                plan.status.value != "awaiting_approval"
                or plan.approval.bundle_state != "prohibited"
            ):
                raise AssertionError("Beta 32 terminal profile changed")
        elif (
            profile == "beta33-initial"
            and (
                plan.status.value != "superseded"
                or plan.approval.bundle_state != "invalidated"
                or plan.approval.elevated_risk_acknowledgement is None
                or plan.approval.elevated_risk_acknowledgement.state.value
                != "invalidated"
            )
        ):
            raise AssertionError("initial Beta 33 terminal profile changed")
        elif profile == "beta33-initial-consumed" and (
            plan.status.value != "applied"
            or plan.approval.bundle_state != "consumed"
            or plan.approval.consumed_at is None
            or plan.approval.elevated_risk_acknowledgement is None
            or plan.approval.elevated_risk_acknowledgement.consumed_at is None
        ):
            raise AssertionError(
                "initial Beta 33 consumed profile changed"
            )
        output.write_bytes(
            (root / f"{created['plan_id']}.json").read_bytes()
        )


def _run_child(
    *, profile: str, source_worktree: Path, output: Path
) -> None:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--child-profile",
            profile,
            "--source-worktree",
            str(source_worktree),
            "--child-output",
            str(output),
        ],
        check=False,
        env=environment,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"exact {profile} source fixture generation failed"
        )


def _provenance(
    fixture_bytes: dict[str, bytes],
    source_worktrees: dict[str, Path],
    *,
    generated_at: str,
) -> dict[str, object]:
    script = Path(__file__).read_bytes()
    fixtures = []
    sources = []
    for profile in (
        "beta32",
        "beta33-initial",
        "beta33-initial-consumed",
    ):
        raw = fixture_bytes[profile]
        payload = json.loads(raw)
        source = source_worktrees[profile]
        policy_path = (
            source
            / "hass_mcp_engineering_beta"
            / "ha_mcp_engineering"
            / "governance"
            / "policy.py"
        )
        sources.append(
            {
                "profile": profile,
                "commit": SOURCE_COMMITS[profile],
                "policy_source_sha256": _sha256(
                    policy_path.read_bytes()
                ),
            }
        )
        fixtures.append(
            {
                "profile": profile,
                "path": f"tests/fixtures/{FIXTURE_NAMES[profile]}",
                "sha256": _sha256(raw),
                "source_commit": SOURCE_COMMITS[profile],
                "contract_version": payload["contract_version"],
                "status": payload["status"],
                "approval_bundle_state": payload["approval"][
                    "bundle_state"
                ],
                "policy_decision": payload["policy_decision"],
                "provider_write_count": (
                    1 if profile == "beta33-initial-consumed" else 0
                ),
                "execution_task_count": (
                    1 if profile == "beta33-initial-consumed" else 0
                ),
            }
        )
    return {
        "schema_version": 1,
        "compatibility_model": (
            "beta34-historical-policy-projection-v1"
        ),
        "generator": (
            "scripts/generate_beta34_historical_policy_fixtures.py"
        ),
        "generator_sha256": _sha256(script),
        "generator_command": (
            "python3 scripts/generate_beta34_historical_policy_fixtures.py "
            "--beta32-worktree <exact-beta32-worktree> "
            "--beta33-initial-worktree <exact-initial-beta33-worktree> "
            "--output-dir tests/fixtures "
            f"--generated-at {generated_at}"
        ),
        "generation_timestamp": generated_at,
        "writer_path": (
            "ChangeGovernanceService.create_configuration_plan"
        ),
        "terminal_lifecycle": {
            "beta32": "prohibited_policy_bundle",
            "beta33-initial": "same_target_plan_supersession",
            "beta33-initial-consumed": (
                "externally_approved_and_applied"
            ),
        },
        "sources": sources,
        "fixtures": fixtures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--beta32-worktree", type=Path)
    parser.add_argument("--beta33-initial-worktree", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--generated-at")
    parser.add_argument(
        "--child-profile",
        choices=(
            "beta32",
            "beta33-initial",
            "beta33-initial-consumed",
        ),
    )
    parser.add_argument("--source-worktree", type=Path)
    parser.add_argument("--child-output", type=Path)
    args = parser.parse_args()

    if args.child_profile:
        if args.source_worktree is None or args.child_output is None:
            raise SystemExit("child generation arguments are incomplete")
        asyncio.run(
            _generate_child(
                profile=args.child_profile,
                source_worktree=args.source_worktree.resolve(),
                output=args.child_output.resolve(),
            )
        )
        return 0

    if any(
        value is None
        for value in (
            args.beta32_worktree,
            args.beta33_initial_worktree,
            args.output_dir,
            args.generated_at,
        )
    ):
        raise SystemExit("fixture generation arguments are incomplete")
    try:
        datetime.fromisoformat(str(args.generated_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(
            "--generated-at must be an ISO-8601 timestamp"
        ) from exc

    source_worktrees = {
        "beta32": args.beta32_worktree.resolve(),
        "beta33-initial": args.beta33_initial_worktree.resolve(),
        "beta33-initial-consumed": (
            args.beta33_initial_worktree.resolve()
        ),
    }
    for profile, worktree in source_worktrees.items():
        _verify_source_worktree(profile, worktree)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture_bytes: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        temporary_root = Path(temporary)
        for profile in (
            "beta32",
            "beta33-initial",
            "beta33-initial-consumed",
        ):
            child_output = temporary_root / FIXTURE_NAMES[profile]
            _run_child(
                profile=profile,
                source_worktree=source_worktrees[profile],
                output=child_output,
            )
            raw = child_output.read_bytes()
            fixture_bytes[profile] = raw
            (output_dir / FIXTURE_NAMES[profile]).write_bytes(raw)

    provenance = _provenance(
        fixture_bytes,
        source_worktrees,
        generated_at=str(args.generated_at),
    )
    provenance_path = (
        output_dir / "beta34_historical_policy_provenance.json"
    )
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
