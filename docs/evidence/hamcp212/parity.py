"""HAMCP-212 cross-tree script parity (synthetic, no live system).

Builds the dependency index from the HAMCP-209 automation fixtures and the
HAMCP-212 script fixtures with the provider of the tree under test, and
writes what the index and ``entity_dependency_analysis`` consume:

* the HAMCP-209 automation index view and the DependencyIndex snapshot
  fingerprint (scripts are a separate partition, outside that fingerprint);
* the script view: findings, dynamic references, script call findings, call
  gaps and coverage minus timing, policy and provider;
* the analysis output for each fixture target minus the ``index`` block
  (timing, generation and the query fingerprint, which folds in the script
  fingerprint) and the script provider attribution and policy. Those fields
  change by design; they are written under ``attribution`` and reported, not
  compared.

Run it against the base tree (every script read delegated, pre-HAMCP-212)
and the head tree (loaded script source), then compare:

    python docs/evidence/hamcp212/parity.py --tree <base tree> --output base.json
    python docs/evidence/hamcp212/parity.py --tree . --output head.json
    python docs/evidence/hamcp212/parity.py --compare base.json head.json
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
SECRET = "synthetic-not-a-credential"
VOLATILE = {"duration_ms", "index_build_duration_ms", "policy", "provider"}
BY_DESIGN = {"index", "configuration_providers", "script_provider_policy"}
TARGETS = (
    "light.synthetic_kitchen",
    "light.synthetic_hall",
    "climate.synthetic_hall",
    "input_boolean.synthetic_guest",
    "script.synthetic_script_plain",
    "script.synthetic_script_templated",
    "script.synthetic_script_blueprint",
    "light.synthetic_bulk_0000",
    "light.synthetic_bulk_0004",
)


def _rows(items):
    return sorted(json.dumps(asdict(item), sort_keys=True, default=str) for item in items)


def script_view(diagnostics) -> dict:
    coverage = diagnostics.coverage.public()
    return {
        "findings": _rows(diagnostics.findings),
        "dynamic_references": _rows(diagnostics.dynamic_references),
        "call_findings": _rows(diagnostics.call_findings),
        "call_gaps": list(diagnostics.call_gaps),
        "coverage": {key: value for key, value in coverage.items() if key not in VOLATILE},
    }


def _strip(value):
    if isinstance(value, dict):
        return {key: _strip(item) for key, item in value.items() if key not in VOLATILE | BY_DESIGN}
    if isinstance(value, list):
        return [_strip(item) for item in value]
    return value


def _attribution(data: dict) -> dict:
    graph = data.get("script_call_graph") or {}
    return {
        "configuration_providers": graph.get("configuration_providers"),
        "script_provider_policy": graph.get("script_provider_policy"),
        "path_evidence_providers": sorted({
            item.get("provider") for item in graph.get("path_evidence", []) if item.get("provider")
        }),
    }


async def build(automations, scripts):
    sys.path.insert(0, str(ROOT / "docs" / "evidence" / "hamcp209"))
    from parity import index_view

    from ha_mcp_engineering.dependency.index import DependencyIndex
    from ha_mcp_engineering.dependency.provider import DirectHaDependencyProvider
    from ha_mcp_engineering.dependency.service import EntityDependencyAnalysisService
    from tests.hamcp209_fixtures import read_blueprint
    from tests.hamcp212_fixtures import SyntheticScriptHome, SyntheticScriptReader

    ha = SyntheticScriptHome(automations, scripts)
    reader = SyntheticScriptReader(ha)
    provider = DirectHaDependencyProvider(
        ha.rest, ha.websocket, secret=SECRET, script_reader_factory=lambda: reader,
    )
    scans = []

    class Capture:
        provider_id = provider.provider_id

        async def scan(self):
            scans.append(await provider.scan())
            return scans[-1]

    analyses = {}
    attribution = {}
    with patch(BLUEPRINT_READER, side_effect=read_blueprint):
        index = DependencyIndex(Capture())
        snapshot, _, _ = await index.get(refresh=True)
        service = EntityDependencyAnalysisService(index)
        for target in TARGETS:
            result = await service.analyze(entity_id=target, include_indirect=True)
            data = json.loads(json.dumps(result.data, sort_keys=True, default=str))
            analyses[target] = _strip(data)
            attribution[target] = _attribution(data)
    scan = scans[0]
    view = {
        "automation_index": index_view(scan),
        "scripts": script_view(scan.script_diagnostics),
        "analysis": analyses,
    }
    encoded = json.dumps(view, sort_keys=True, default=str).encode("utf-8")
    return {
        "view_sha256": hashlib.sha256(encoded).hexdigest(),
        "snapshot_fingerprint": snapshot.fingerprint,
        "script_finding_count": len(scan.script_diagnostics.findings),
        "script_call_finding_count": len(scan.script_diagnostics.call_findings),
        "script_dynamic_reference_count": len(scan.script_diagnostics.dynamic_references),
        "delegated_script_reads": len(reader.calls),
        "delegated_script_reads_by_entity": dict(sorted(ha.script_reads.items())),
        "script_config_source_counts": scan.profile.get(
            "script_config_source_counts", "stored_delegated only (pre-HAMCP-212)"),
        "script_stored_config_fallback_reasons": scan.profile.get("script_stored_config_fallback_reasons", {}),
        "script_coverage_provider": scan.script_diagnostics.coverage.provider,
        "attribution": attribution,
        "view": view,
    }


async def run(tree: Path) -> dict:
    sys.path.insert(0, str(tree.resolve() / "hass_mcp_engineering_beta"))
    sys.path.insert(1, str(ROOT))
    from tests.hamcp209_fixtures import generated_automations, representative_automations
    from tests.hamcp212_fixtures import generated_scripts, representative_scripts

    return {
        "representative": await build(representative_automations(), representative_scripts()),
        "generated_113_5": await build(generated_automations(113), generated_scripts(5)),
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
            parts = {
                part: left[name]["view"][part] == right[name]["view"][part]
                for part in left[name]["view"]
            }
            same_fingerprint = left[name]["snapshot_fingerprint"] == right[name]["snapshot_fingerprint"]
            identical = identical and all(parts.values()) and same_fingerprint
            print(f"{name}: " + " ".join(f"{part}_identical={same}" for part, same in parts.items())
                  + f" fingerprint_identical={same_fingerprint} delegated_script_reads "
                  f"{left[name]['delegated_script_reads']} -> {right[name]['delegated_script_reads']}")
        print("IDENTICAL" if identical else "DIFFERENT")
        return 0 if identical else 1
    report = asyncio.run(run(arguments.tree))
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
