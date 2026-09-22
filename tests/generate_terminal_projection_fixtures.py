"""Generate synthetic terminal records with exact shipped, unmodified writers.

Run with --beta38-worktree, --beta4-worktree and a new --output directory.
Each writer runs in its own interpreter; no persisted bytes are edited afterward.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import itertools
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import uuid
from unittest.mock import patch

SOURCES = {
    "expired_helper": "171e4769faef9a188e0a57cfa024f401f6313e1a",
    "invalid_automation_f2_v1": "171e4769faef9a188e0a57cfa024f401f6313e1a",
    "invalid_automation_f2_v2": "c07bc54ae0cf9d52a3bd205f2a24de704f7b8296",
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def git(source, *args):
    return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()


def refuse(*args, **kwargs):
    raise AssertionError("fixture generation cannot use a network")


async def child(profile, source, output):
    assert git(source, "rev-parse", "HEAD") == SOURCES[profile]
    assert not git(source, "status", "--porcelain")
    sys.path[:0] = [str(source), str(source / "hass_mcp_engineering_beta")]
    from ha_mcp_engineering.errors import GovernanceError
    from ha_mcp_engineering.governance.service import ChangeGovernanceService
    from ha_mcp_engineering.governance.storage import ChangePlanRepository
    from ha_mcp_engineering.request_context import begin_request, end_request

    with tempfile.TemporaryDirectory() as temporary:
        plan_root = Path(temporary) / "plans"
        telemetry, token = begin_request("synthetic-terminal-projection")
        telemetry.caller_id = "synthetic-terminal-fixture"
        try:
            creation_error = None
            if profile == "expired_helper":
                from tests.test_beta37_exact_helper_state import (
                    Clock, FakeHelperStateGateway, FakeDependencyRiskReader, UnusedLegacyGateway,
                )
                clock = Clock()
                clock.value = datetime(2026, 8, 15, 1, tzinfo=timezone.utc)
                gateway = FakeHelperStateGateway()
                gateway.entity_id = "input_boolean.synthetic_terminal_helper"
                dependency = FakeDependencyRiskReader(gateway.entity_id)
                dependency.complete = False
                service = ChangeGovernanceService(
                    ChangePlanRepository(plan_root), UnusedLegacyGateway(), now=clock,
                    helper_state_gateway=gateway, helper_dependency_risk_reader=dependency,
                )
                result = await service.create_helper_state_plan(entity_id=gateway.entity_id, desired_state="on")
                plan_id = result["plan"]["plan_id"]
                clock.advance(seconds=3 * 3600)
                service.get_plan(plan_id)  # The original writer persists its expiry.
                path = plan_root / "operational-administration-v3" / (plan_id + ".json")
                dispatches = gateway.dispatch_count
            else:
                from tests.test_governance import Clock, FakeGateway, CURRENT
                clock = Clock()
                clock.value = datetime(2026, 8, 17, 18, tzinfo=timezone.utc)
                gateway = FakeGateway()
                service = ChangeGovernanceService(ChangePlanRepository(plan_root), gateway, now=clock)
                proposed = copy.deepcopy(CURRENT)
                proposed["id"] = "synthetic_mismatched_id"
                proposed["action"] = [{"service": "cover.open_cover", "target": {"entity_id": "cover.synthetic_garage_door"}}]
                try:
                    await service.create_plan(
                        title="Synthetic rejected automation", description="Offline terminal history fixture",
                        operation="update_automation", automation_id="synthetic-invalid-target!", proposed_config=proposed,
                    )
                    raise AssertionError("invalid proposal was accepted")
                except GovernanceError as exc:
                    creation_error = exc.code.value
                    assert creation_error == "automation_validation_failed"
                plans = service.repository.list()
                assert len(plans) == 1
                plan_id = plans[0].plan_id
                path = plan_root / (plan_id + ".json")
                dispatches = gateway.write_calls
            try:
                service.get_plan(plan_id)
                historical_read_error = None
            except GovernanceError as exc:
                historical_read_error = exc.code.value
            assert dispatches == 0 and service.task_repository.get_for_plan(plan_id) is None
            raw = path.read_bytes()
            filename = "terminal_" + profile + ".json"
            (output / filename).write_bytes(raw)
            plan = service.repository.get(plan_id)
            receipt = {
                "profile": profile, "source_commit": SOURCES[profile], "fixture": filename,
                "sha256": digest(raw), "writer": "create_helper_state_plan" if profile == "expired_helper" else "create_plan",
                "creation_error": creation_error, "historical_read_error": historical_read_error,
                "status": plan.status.value, "policy_version": plan.policy_decision.policy_version,
                "provider_dispatch_count": dispatches, "execution_task_count": 0,
                "sanitized_before_writer": True, "persisted_bytes_edited": False,
            }
            (output / (profile + "-receipt.json")).write_text(json.dumps(receipt, indent=2) + "\n")
        finally:
            end_request(token)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--beta38-worktree", type=Path)
    parser.add_argument("--beta4-worktree", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--child", choices=SOURCES)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    if args.child:
        counter = itertools.count(1 + list(SOURCES).index(args.child) * 100)
        with patch.object(socket.socket, "connect", refuse), patch.object(socket, "create_connection", refuse), \
                patch.object(uuid, "uuid4", side_effect=lambda: uuid.UUID(int=next(counter))):
            asyncio.run(child(args.child, args.source.resolve(), args.output.resolve()))
        return
    if args.beta38_worktree is None or args.beta4_worktree is None:
        parser.error("both exact historical source worktrees are required")
    args.output.mkdir(parents=True, exist_ok=False)
    receipts = []
    for profile in SOURCES:
        source = args.beta4_worktree if profile.endswith("v2") else args.beta38_worktree
        subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--child", profile,
                        "--source", str(source.resolve()), "--output", str(args.output.resolve())], check=True)
        receipts.append(json.loads((args.output / (profile + "-receipt.json")).read_text()))
    provenance = {"generator": "tests/generate_terminal_projection_fixtures.py",
                  "generator_sha256": digest(Path(__file__).read_bytes()), "fixtures": receipts,
                  "process": "Fresh interpreter per exact clean shipped writer; deterministic UUIDs/clock and synthetic inputs before hashing; unchanged persisted bytes.",
                  "network": "denied", "production_records": False}
    (args.output / "terminal_projection_provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")


if __name__ == "__main__":
    main()
