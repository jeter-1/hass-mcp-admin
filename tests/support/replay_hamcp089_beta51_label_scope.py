"""Replay the sanitized HAMCP-089 capture through an exact source checkout.

This command is intentionally runtime-version agnostic: the production
provider, dependency index, and helper-risk service are imported only from the
detached source tree supplied by ``--source-root``.  The replay transport is
synthetic and offline; it never contacts Home Assistant or an MCP endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _fingerprinted(value: dict, field: str) -> dict:
    projected = copy.deepcopy(value)
    projected[field] = hashlib.sha256(_canonical(value)).hexdigest()
    return projected


def _captured_source(value: str) -> str:
    """Return the fixture's pseudonym without inventing runtime identity."""

    return value.removeprefix("automation.")


def _source_commit(source_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _require_source_imports(
    source_root: Path, values: tuple[object, ...]
) -> None:
    for value in values:
        module = sys.modules[value.__module__]
        module_path = Path(module.__file__).resolve()
        if not module_path.is_relative_to(source_root):
            raise AssertionError(
                "historical replay imported outside source root: "
                f"{module_path}"
            )


async def _replay(source_root: Path, fixture_path: Path) -> dict:
    source_root = source_root.resolve()
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    # Insert the detached source tree ahead of this command's candidate tree
    # before importing any shipped Engineering or replay-adapter module.
    sys.path.insert(0, str(source_root))
    sys.path.insert(0, str(source_root / "hass_mcp_engineering_beta"))

    from ha_mcp_engineering.dependency.index import DependencyIndex
    from ha_mcp_engineering.dependency.provider import (
        DirectHaDependencyProvider,
    )
    from ha_mcp_engineering.governance.helper_dependency import (
        HelperDependencyRiskService,
        helper_dependency_risk_assessment,
    )
    from tests.test_beta50_helper_production_target_scope import (
        CapturedBeta50ReplayRest,
        CapturedBeta50ReplayWebSocket,
    )

    _require_source_imports(
        source_root,
        (
            DependencyIndex,
            DirectHaDependencyProvider,
            HelperDependencyRiskService,
            CapturedBeta50ReplayRest,
            CapturedBeta50ReplayWebSocket,
        ),
    )

    class LabelScopeReplayWebSocket(CapturedBeta50ReplayWebSocket):
        async def command(self, payload: dict):
            result = await super().command(payload)
            if payload == {"type": "config/entity_registry/list"}:
                result = list(result)
                result.append(
                    copy.deepcopy(
                        fixture["registry_boundary_reproducer"]
                    )
                )
            return result

    rest = CapturedBeta50ReplayRest(fixture)
    websocket = LabelScopeReplayWebSocket(fixture, rest.ids)
    index = DependencyIndex(
        DirectHaDependencyProvider(rest, websocket, concurrency=4)
    )
    snapshot, rebuilt, _lookup_ms = await index.get(refresh=True)
    if not rebuilt:
        raise AssertionError("historical replay did not build a fresh index")

    target = fixture["target_entity_id"]
    evidence = await HelperDependencyRiskService(index).assess(
        target, refresh=False
    )
    binding = evidence["binding"]
    assessment = helper_dependency_risk_assessment(evidence)

    obligations: list[dict] = []
    for projected in binding["obligation_evidence"]:
        obligations.append(
            _fingerprinted(
                {
                    "source": _captured_source(
                        projected["source_object_id"]
                    ),
                    "configuration_path": projected[
                        "configuration_path"
                    ],
                    "relation": projected["relation"],
                    "ledger_outcome": projected["ledger_outcome"],
                    "target_outcome": projected["target_outcome"],
                    "obligation_kind": projected["obligation_kind"],
                    "reason_code": projected["reason_code"],
                    "semantic_category": projected[
                        "semantic_category"
                    ],
                    "candidate_entity_ids": projected[
                        "candidate_entity_ids"
                    ],
                    "possible_entity_domains": projected[
                        "possible_entity_domains"
                    ],
                    "literal_selectors": projected[
                        "literal_selectors"
                    ],
                    "context_provenance": projected[
                        "context_provenance"
                    ],
                    "limit_exceeded": projected["limit_exceeded"],
                    "lock_projection": projected["lock_projection"],
                    "target_selector_scope": projected[
                        "target_selector_scope"
                    ],
                },
                "sanitized_obligation_fingerprint",
            )
        )

    profiles = [
        _fingerprinted(
            {
                "source": _captured_source(item["automation_id"]),
                "relationships": item["relationships"],
                "physical_consequence": item["physical_consequence"],
                "complete": item["complete"],
                "analysis_complete": item["analysis_complete"],
                "semantic_complete": item["semantic_complete"],
                "processing_limit_exceeded": item[
                    "processing_limit_exceeded"
                ],
                "action_domains": item["action_domains"],
                "services": item["services"],
                "reason_codes": item["reason_codes"],
            },
            "sanitized_profile_fingerprint",
        )
        for item in binding["downstream_profiles"]
    ]

    reason_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for item in obligations:
        reason = item["reason_code"]
        source = item["source"]
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        source_counts[source] = source_counts.get(source, 0) + 1

    write_requests = [
        (method, path)
        for method, path in rest.calls
        if method.upper() not in {"GET", "HEAD", "OPTIONS"}
    ]

    return {
        "source_release_commit": _source_commit(source_root),
        "source_imports_verified": True,
        "risk_model": binding["model"],
        "exact_dependency_count": binding[
            "exact_dependency_obligation_count"
        ],
        "target_capable_opaque_obligation_count": binding[
            "opaque_obligation_count"
        ],
        "downstream_profile_count": len(binding["downstream_profiles"]),
        "coverage_complete": binding["coverage_complete"],
        "evidence_complete": binding["evidence_complete"],
        "execution_eligible": binding["execution_eligible"],
        "approval_actionable": assessment.apply_allowed,
        "physical_consequence": binding["physical_consequence"],
        "semantic_precision": binding["semantic_precision"],
        "obligation_reason_counts": dict(sorted(reason_counts.items())),
        "source_obligation_counts": dict(sorted(source_counts.items())),
        "obligations": obligations,
        "downstream_profiles": profiles,
        "provider_dispatch_count": len(write_requests),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    arguments = parser.parse_args()
    result = asyncio.run(
        _replay(arguments.source_root, arguments.fixture.resolve())
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
