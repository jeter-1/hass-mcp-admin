"""HAMCP-209 cross-tree extraction parity (synthetic, no live system).

Builds the dependency index from the HAMCP-209 fixtures with the provider of
the tree under test and writes everything the index consumes (findings,
dynamic references, obligations, risk profiles, read failures, label
evidence, coverage minus timing/policy/warnings) plus the snapshot
fingerprint. Run it against the base tree (REST-only, pre-HAMCP-209) and the
head tree (loaded source), then compare:

    python docs/evidence/hamcp209/parity.py --tree <base tree> --output base.json
    python docs/evidence/hamcp209/parity.py --tree . --output head.json
    python docs/evidence/hamcp209/parity.py --compare base.json head.json
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
BLUEPRINT_READER = "ha_mcp_engineering.dependency.provider._read_blueprint_with_status"


def _rows(items):
    return sorted(json.dumps(asdict(item), sort_keys=True, default=str) for item in items)


def index_view(result) -> dict:
    return {
        "findings": _rows(result.findings),
        "dynamic_references": _rows(result.dynamic_references),
        "obligations": _rows(result.obligations),
        "automation_action_profiles": _rows(result.automation_action_profiles),
        "automation_read_failures": _rows(result.automation_read_failures),
        "target_metadata": result.target_metadata,
        "label_memberships": result.label_memberships,
        "label_membership_fingerprints": result.label_membership_fingerprints,
        "label_membership_complete": result.label_membership_complete,
        "label_registry_complete": result.label_registry_complete,
        "label_selector_authority": {
            key: asdict(value) for key, value in result.label_selector_authority.items()
        },
        "coverage": [
            {key: value for key, value in item.public().items()
             if key not in {"duration_ms", "policy", "warnings"}}
            for item in result.coverage
        ],
        "home_assistant_version": result.home_assistant_version,
    }


async def build(fixture):
    from ha_mcp_engineering.dependency.index import DependencyIndex
    from ha_mcp_engineering.dependency.provider import DirectHaDependencyProvider
    from tests.hamcp209_fixtures import SyntheticHomeAssistant, read_blueprint

    ha = SyntheticHomeAssistant(fixture)
    provider = DirectHaDependencyProvider(ha.rest, ha.websocket, secret="synthetic-not-a-credential")
    scans = []

    class Capture:
        provider_id = provider.provider_id

        async def scan(self):
            scans.append(await provider.scan())
            return scans[-1]

    with patch(BLUEPRINT_READER, side_effect=read_blueprint):
        snapshot, _, _ = await DependencyIndex(Capture()).get(refresh=True)
    view = index_view(scans[0])
    encoded = json.dumps(view, sort_keys=True, default=str).encode("utf-8")
    return {
        "view_sha256": hashlib.sha256(encoded).hexdigest(),
        "snapshot_fingerprint": snapshot.fingerprint,
        "finding_count": len(scans[0].findings),
        "obligation_count": len(scans[0].obligations),
        "dynamic_reference_count": len(scans[0].dynamic_references),
        "automation_config_rest_reads": ha.rest_counts["automation_config_read"],
        "automation_config_source": scans[0].profile.get("automation_config_source", "stored_rest (pre-HAMCP-209)"),
        "view": view,
    }


async def run(tree: Path) -> dict:
    sys.path.insert(0, str(tree.resolve() / "hass_mcp_engineering_beta"))
    sys.path.insert(1, str(ROOT))
    from tests.hamcp209_fixtures import generated_automations, representative_automations

    return {
        "representative": await build(representative_automations()),
        "generated_113": await build(generated_automations(113)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tree", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare", nargs=2, type=Path)
    arguments = parser.parse_args()
    if arguments.compare:
        left, right = (json.loads(path.read_text(encoding="utf-8")) for path in arguments.compare)
        identical = True
        for name in left:
            same_view = left[name]["view"] == right[name]["view"]
            same_fingerprint = left[name]["snapshot_fingerprint"] == right[name]["snapshot_fingerprint"]
            identical = identical and same_view and same_fingerprint
            print(f"{name}: view_identical={same_view} fingerprint_identical={same_fingerprint} "
                  f"rest_reads {left[name]['automation_config_rest_reads']} -> {right[name]['automation_config_rest_reads']}")
        print("IDENTICAL" if identical else "DIFFERENT")
        return 0 if identical else 1
    report = asyncio.run(run(arguments.tree))
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
