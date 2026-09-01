#!/usr/bin/env python3
"""Generate exact shipped v2 helper-risk evidence for Beta 54 recovery.

The fixture is produced by the exact Beta 37 dependency-risk writer.  It is
not a reconstructed approximation of the historical persisted binding.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


SOURCE_TAG = "v2.2.0-beta.37"
SOURCE_COMMIT = "c2f4d9d7e72e59f1ade6e982979bddbf5ef16f21"
ENTITY_ID = "input_boolean.beta37_exact_action"
AUTOMATION_ID = "automation.beta37_benign_dependency"
AUTOMATION_RESOURCE_ID = "beta37_benign_dependency"
FIXTURE_NAME = "beta37_helper_dependency_risk_v2_binding.json"
PROVENANCE_NAME = "beta54_v2_helper_recovery_provenance.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _source_commit(source_root: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        text=True,
    ).strip()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--generated-at", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    source_root = args.source_root.resolve()
    output_dir = args.output_dir.resolve()
    if _source_commit(source_root) != SOURCE_COMMIT:
        raise SystemExit("source root is not exact shipped Beta 37")

    engineering_root = source_root / "hass_mcp_engineering_beta"
    sys.path.insert(0, str(engineering_root))
    from ha_mcp_engineering.dependency.models import (  # noqa: PLC0415
        AutomationActionRiskProfile,
        DependencyFinding,
        DependencyIndexSnapshot,
        SourceCoverageItem,
    )
    from ha_mcp_engineering.governance.helper_dependency import (  # noqa: PLC0415
        build_helper_dependency_risk_binding,
        helper_dependency_risk_assessment,
    )
    from ha_mcp_engineering.governance.risk import (  # noqa: PLC0415
        automation_action_consequence_profile,
    )

    imported = Path(sys.modules[
        "ha_mcp_engineering.governance.helper_dependency"
    ].__file__).resolve()
    if not imported.is_relative_to(engineering_root):
        raise SystemExit("historical writer was not imported from source root")

    effect = automation_action_consequence_profile(
        {
            "action": [
                {
                    "service": "notify.notify",
                    "data": {"message": "synthetic bounded evidence"},
                }
            ]
        }
    )
    profile = AutomationActionRiskProfile(
        source_id=AUTOMATION_RESOURCE_ID,
        source_entity_id=AUTOMATION_ID,
        risk_level=effect["risk_level"],
        physical_consequence=effect["physical_consequence"],
        complete=effect["complete"],
        truncated=effect["truncated"],
        action_domains=tuple(effect["action_domains"]),
        services=tuple(effect["services"]),
        reason_codes=tuple(effect["reason_codes"]),
        effect_projection_model=effect["effect_projection_model"],
        effect_targets=tuple(effect["effect_targets"]),
        effect_data=tuple(effect["effect_data"]),
        effect_structure_fingerprint=effect[
            "effect_structure_fingerprint"
        ],
        effect_projection_fingerprint=effect[
            "effect_projection_fingerprint"
        ],
        effect_projection_clipped=effect["effect_projection_clipped"],
        evidence_fingerprint=effect["evidence_fingerprint"],
    )
    snapshot = DependencyIndexSnapshot(
        fingerprint="a" * 64,
        generation=7,
        built_at_monotonic=1.0,
        built_at="2026-08-13T12:00:00+00:00",
        findings=(
            DependencyFinding(
                evidence_id="ev_000000000000000000000001",
                target_entity_id=ENTITY_ID,
                source_type="automation",
                source_id=AUTOMATION_RESOURCE_ID,
                source_entity_id=AUTOMATION_ID,
                source_name=None,
                relation="trigger",
                config_path="$.trigger[0].entity_id",
            ),
        ),
        dynamic_references=(),
        target_metadata={},
        coverage=(
            SourceCoverageItem(
                "automation",
                "direct_ha_api",
                "automation_config",
                "complete",
            ),
            SourceCoverageItem(
                "blueprint",
                "direct_ha_api",
                "blueprint_source",
                "complete",
            ),
        ),
        automation_action_profiles=(profile,),
    )
    binding = build_helper_dependency_risk_binding(
        snapshot,
        entity_id=ENTITY_ID,
        index_metadata={
            "freshness": "current",
            "evidence_stale": False,
            "invalidated": False,
        },
    )
    if binding.get("model") != "helper-dependency-risk-v2":
        raise SystemExit("historical writer did not emit risk model v2")
    if "dependency_lock_projection" in binding:
        raise SystemExit("historical writer unexpectedly emitted v3 locks")
    if binding.get("downstream_automation_resource_ids") != [
        AUTOMATION_RESOURCE_ID
    ]:
        raise SystemExit("historical writer changed bounded resources")
    evidence = {
        "binding": binding,
        "provenance": {
            "provider": "dependency_index",
            "completeness": binding["completeness"],
            "generation": 7,
            "fingerprint": "a" * 64,
            "freshness": "current",
            "fallback": "none",
            "fallback_occurred": False,
        },
    }
    risk = helper_dependency_risk_assessment(evidence)

    output_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = output_dir / FIXTURE_NAME
    provenance_path = output_dir / PROVENANCE_NAME
    _write_json(
        fixture_path,
        {
            "fixture_model": "shipped-helper-dependency-risk-v2-binding-v1",
            "source_commit": SOURCE_COMMIT,
            "source_tag": SOURCE_TAG,
            "writer": "build_helper_dependency_risk_binding",
            "binding": binding,
            "risk": {
                "level": risk.level.value,
                "reasons": risk.reasons,
                "apply_allowed": risk.apply_allowed,
                "evidence": risk.evidence,
                "warnings": risk.warnings,
            },
        },
    )

    helper_source = (
        engineering_root
        / "ha_mcp_engineering"
        / "governance"
        / "helper_dependency.py"
    )
    lock_source = (
        engineering_root
        / "ha_mcp_engineering"
        / "f3"
        / "operational_locks.py"
    )
    _write_json(
        provenance_path,
        {
            "schema_version": 1,
            "compatibility_model": (
                "beta54-shipped-v2-post-intent-readback-v1"
            ),
            "generated_at": args.generated_at,
            "generator": (
                "scripts/generate_beta54_v2_helper_recovery_fixture.py"
            ),
            "generator_command": (
                "python3 scripts/generate_beta54_v2_helper_recovery_fixture.py "
                "--source-root <exact-beta37-worktree> "
                "--output-dir tests/fixtures "
                f"--generated-at {args.generated_at}"
            ),
            "generator_sha256": _sha256(Path(__file__).resolve()),
            "source": {
                "tag": SOURCE_TAG,
                "commit": SOURCE_COMMIT,
                "initial_v2_producer_commit": (
                    "2433be7815db17c328635cc5f02113abc1dccd58"
                ),
                "helper_dependency_source_sha256": _sha256(helper_source),
                "operational_locks_source_sha256": _sha256(lock_source),
            },
            "writer": "build_helper_dependency_risk_binding",
            "fixture": {
                "path": f"tests/fixtures/{FIXTURE_NAME}",
                "sha256": _sha256(fixture_path),
                "provider_write_count": 0,
                "model": binding["model"],
                "dependency_lock_projection_present": False,
                "downstream_automation_resource_count": len(
                    binding["downstream_automation_resource_ids"]
                ),
            },
            "recovery_boundary": (
                "The exact shipped binding is used to initialize a synthetic "
                "durable post-intent record; current recovery may observe and "
                "verify it but may never redispatch it."
            ),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
