#!/usr/bin/env python3
"""Generate exact legacy retained-effect fixtures from reviewed writers."""

from __future__ import annotations

import argparse
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from unittest.mock import patch


BETA6_SOURCE_COMMIT = "5c7eebf962837f85f2309b1b5099401fb075cd6e"
BETA32_SOURCE_COMMIT = "f9d660499a05edef6af7fd9a590d7827b5983e3a"
SOURCE_COMMITS = {
    "beta6-expired": BETA6_SOURCE_COMMIT,
    "beta32-prohibited": BETA32_SOURCE_COMMIT,
}
FIXTURE_NAMES = {
    "beta6-expired": (
        "beta6_legacy_retained_effect_expired_plan.json"
    ),
    "beta32-prohibited": (
        "beta32_legacy_retained_effect_prohibited_plan.json"
    ),
}
FIXED_TIME = datetime(2026, 8, 11, 18, 0, tzinfo=timezone.utc)
EXPECTED_REASON_CODES = (
    "safety_critical_effect_not_reviewed",
    "supported_configuration_change",
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


class _Clock:
    def __init__(self) -> None:
        self.value = FIXED_TIME

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


class _Gateway:
    def __init__(self, baseline: dict[str, object]) -> None:
        self.baseline = copy.deepcopy(baseline)
        self.writes = 0

    async def get(
        self, automation_id: str
    ) -> dict[str, object] | None:
        if automation_id != self.baseline.get("id"):
            return None
        return copy.deepcopy(self.baseline)

    async def read(
        self, resource_type: str, resource_id: str
    ) -> dict[str, object] | None:
        if resource_type != "automation":
            return None
        return await self.get(resource_id)

    async def write(self, *_args: object, **_kwargs: object) -> None:
        self.writes += 1
        raise AssertionError("historical fixture generation must not write")

    async def validate(self) -> dict[str, object]:
        return {"result": "valid", "errors": None}


def _baseline() -> dict[str, object]:
    return {
        "id": "beta34_legacy_retained_effect",
        "alias": "Beta 34 legacy retained-effect fixture",
        "description": "Synthetic source-writer evidence",
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
                "target": {"entity_id": "cover.synthetic_garage_door"},
            },
        ],
    }


async def _generate_child(
    *, profile: str, source_worktree: Path, output: Path
) -> None:
    _verify_source_worktree(profile, source_worktree)
    sys.path.insert(0, str(source_worktree / "hass_mcp_engineering_beta"))

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
    proposed = copy.deepcopy(baseline)
    proposed.pop("id", None)
    conditions = proposed.get("condition")
    assert isinstance(conditions, list)
    conditions.append(
        {
            "condition": "state",
            "entity_id": "binary_sensor.synthetic_garage_presence",
            "state": "off",
        }
    )
    clock = _Clock()
    gateway = _Gateway(baseline)
    fixed_id = uuid.UUID(
        hex=(
            "b3460000000000000000000000000001"
            if profile == "beta32-prohibited"
            else "b3460000000000000000000000000002"
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
            f"beta34-{profile}-legacy-policy-fixture"
        )
        telemetry.caller_id = "beta34-historical-policy-fixture"
        try:
            with patch(
                "ha_mcp_engineering.governance.service.uuid.uuid4",
                return_value=fixed_id,
            ):
                created = await service.create_plan(
                    title="Append synthetic garage presence guard",
                    description=(
                        "Source-authentic legacy policy projection fixture"
                    ),
                    operation="update_automation",
                    automation_id="beta34_legacy_retained_effect",
                    proposed_config=proposed,
                    expiration_minutes=5,
                )
            decision = created["policy_decision"]
            if (
                decision["policy_class"] != "prohibited"
                or decision["risk_delta"] != "high"
                or decision["physical_consequence"] != "safety_critical"
                or tuple(decision["reason_codes"])
                != EXPECTED_REASON_CODES
            ):
                raise AssertionError(
                    f"{profile} source produced an unexpected policy"
                )
            if profile == "beta6-expired":
                clock.advance(minutes=6)
                expired = service.get_plan(str(created["plan_id"]))
                if expired["status"] != "expired":
                    raise AssertionError(
                        "Beta 6 source did not expire the fixture"
                    )
        finally:
            end_request(context)

        if gateway.writes:
            raise AssertionError("historical fixture dispatched a provider")
        if service.task_repository.list():
            raise AssertionError("historical fixture created an execution task")
        plan = repository.get(str(created["plan_id"]))
        if plan is None or not is_terminal_plan(plan):
            raise AssertionError("historical fixture is not terminal")
        if plan.contract_version != 1 or plan.operations:
            raise AssertionError("legacy fixture contract changed")
        if profile == "beta32-prohibited":
            if (
                plan.status.value != "awaiting_approval"
                or plan.approval.state.value != "required"
                or plan.approval.bundle_state != "prohibited"
            ):
                raise AssertionError("Beta 32 prohibited shape changed")
        elif (
            plan.status.value != "expired"
            or plan.approval.state.value != "invalidated"
            or plan.approval.bundle_state != "invalidated"
        ):
            raise AssertionError("Beta 6 expired shape changed")
        persisted = root / f"{created['plan_id']}.json"
        raw = persisted.read_bytes()
        payload = json.loads(raw)
        if "contract_version" in payload or "operations" in payload:
            raise AssertionError("legacy writer emitted additive v2 fields")
        output.write_bytes(raw)


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
        raise SystemExit(f"exact {profile} source generation failed")


def _provenance(
    fixture_bytes: dict[str, bytes],
    source_worktrees: dict[str, Path],
    *,
    generated_at: str,
) -> dict[str, object]:
    script = Path(__file__).read_bytes()
    fixtures = []
    sources = []
    for profile in ("beta32-prohibited", "beta6-expired"):
        raw = fixture_bytes[profile]
        payload = json.loads(raw)
        source = source_worktrees[profile]
        sources.append(
            {
                "profile": profile,
                "commit": SOURCE_COMMITS[profile],
                "policy_source_sha256": _sha256(
                    (
                        source
                        / "hass_mcp_engineering_beta"
                        / "ha_mcp_engineering"
                        / "governance"
                        / "policy.py"
                    ).read_bytes()
                ),
                "service_source_sha256": _sha256(
                    (
                        source
                        / "hass_mcp_engineering_beta"
                        / "ha_mcp_engineering"
                        / "governance"
                        / "service.py"
                    ).read_bytes()
                ),
            }
        )
        fixtures.append(
            {
                "profile": profile,
                "path": f"tests/fixtures/{FIXTURE_NAMES[profile]}",
                "sha256": _sha256(raw),
                "source_commit": SOURCE_COMMITS[profile],
                "contract_version_key_present": (
                    "contract_version" in payload
                ),
                "deserialized_contract_version": 1,
                "operation": payload["operation"],
                "target_type": payload["target"]["target_type"],
                "status": payload["status"],
                "approval_state": payload["approval"]["state"],
                "approval_bundle_state": payload["approval"][
                    "bundle_state"
                ],
                "policy_decision": payload["policy_decision"],
                "provider_write_count": 0,
                "execution_task_count": 0,
            }
        )
    return {
        "schema_version": 1,
        "compatibility_model": "beta34-historical-policy-projection-v1",
        "generator": (
            "scripts/generate_beta34_legacy_retained_effect_fixtures.py"
        ),
        "generator_sha256": _sha256(script),
        "generator_command": (
            "python3 scripts/"
            "generate_beta34_legacy_retained_effect_fixtures.py "
            "--beta6-worktree <exact-beta6-worktree> "
            "--beta32-worktree <exact-beta32-worktree> "
            "--output-dir tests/fixtures "
            f"--generated-at {generated_at}"
        ),
        "generation_timestamp": generated_at,
        "writer_path": "ChangeGovernanceService.create_plan",
        "terminal_lifecycle": {
            "beta32-prohibited": "prohibited_policy_bundle",
            "beta6-expired": "ChangeGovernanceService._expire_if_needed",
        },
        "sources": sources,
        "fixtures": fixtures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--beta6-worktree", type=Path)
    parser.add_argument("--beta32-worktree", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--generated-at")
    parser.add_argument(
        "--child-profile",
        choices=("beta6-expired", "beta32-prohibited"),
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
            args.beta6_worktree,
            args.beta32_worktree,
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
        "beta6-expired": args.beta6_worktree.resolve(),
        "beta32-prohibited": args.beta32_worktree.resolve(),
    }
    for profile, worktree in source_worktrees.items():
        _verify_source_worktree(profile, worktree)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture_bytes: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        temporary_root = Path(temporary)
        for profile in ("beta32-prohibited", "beta6-expired"):
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
    (output_dir / "beta34_legacy_retained_effect_provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
