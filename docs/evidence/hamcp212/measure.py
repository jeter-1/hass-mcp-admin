"""HAMCP-212 offline index-build measurement (synthetic, no live system).

Runs one dependency-index build against the synthetic model in
``tests/hamcp212_fixtures.py``: N automations (HAMCP-209 loaded source) plus
M scripts. The delegated ha-mcp stored script read costs
``--delegated-seconds-per-read`` (default 1.86 s, the beta.23 live average:
5 reads, 9.3 s). ``--serialize-delegated`` models the route as one-at-a-time,
which matches the live build wall time better than the 8 concurrent workers.
WebSocket commands cost ``--websocket-seconds-per-command`` each.

Usage (the tree under test supplies ``ha_mcp_engineering``):
    python docs/evidence/hamcp212/measure.py --tree <tree> --label <name> --output out.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
BLUEPRINT_READER = "ha_mcp_engineering.dependency.provider._read_blueprint_with_status"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--automations", type=int, default=113)
    parser.add_argument("--scripts", type=int, default=5)
    parser.add_argument("--delegated-seconds-per-read", type=float, default=1.86)
    parser.add_argument("--serialize-delegated", action="store_true")
    parser.add_argument("--websocket-seconds-per-command", type=float, default=0.0005)
    arguments = parser.parse_args()
    sys.path.insert(0, str(arguments.tree.resolve() / "hass_mcp_engineering_beta"))
    sys.path.insert(1, str(ROOT))
    report = asyncio.run(measure(arguments))
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "label", "build_wall_ms", "delegated_script_reads", "websocket_batch_commands")}))
    return 0


async def measure(arguments) -> dict:
    from ha_mcp_engineering.dependency.index import DependencyIndex
    from ha_mcp_engineering.dependency.provider import DirectHaDependencyProvider
    from tests.hamcp209_fixtures import generated_automations, read_blueprint
    from tests.hamcp212_fixtures import SyntheticScriptHome, SyntheticScriptReader, generated_scripts

    ha = SyntheticScriptHome(
        generated_automations(arguments.automations), generated_scripts(arguments.scripts),
        delegated_seconds_per_read=arguments.delegated_seconds_per_read,
        serialize_delegated_reads=arguments.serialize_delegated,
        websocket_seconds_per_command=arguments.websocket_seconds_per_command,
    )
    reader = SyntheticScriptReader(ha)
    provider = DirectHaDependencyProvider(
        ha.rest, ha.websocket, secret="synthetic-not-a-credential", script_reader_factory=lambda: reader,
    )
    scans = []

    class Capture:
        provider_id = provider.provider_id

        async def scan(self):
            scans.append(await provider.scan())
            return scans[-1]

    with patch(BLUEPRINT_READER, side_effect=read_blueprint):
        started = time.perf_counter()
        await DependencyIndex(Capture()).get(refresh=True)
        build_wall_ms = (time.perf_counter() - started) * 1000
    profile = scans[0].profile
    script_coverage = scans[0].script_diagnostics.coverage
    return {
        "label": arguments.label,
        "synthetic": True,
        "automations": arguments.automations,
        "scripts": arguments.scripts,
        "delegated_seconds_per_read": arguments.delegated_seconds_per_read,
        "delegated_reads_serialized": arguments.serialize_delegated,
        "websocket_seconds_per_command": arguments.websocket_seconds_per_command,
        "build_wall_ms": round(build_wall_ms, 1),
        "scan_wall_ms": profile.get("scan_wall_time_ms"),
        "request_count": profile.get("request_count"),
        "request_count_by_operation": profile.get("request_count_by_operation"),
        "request_time_ms_by_operation": profile.get("request_time_ms_by_operation"),
        "delegated_script_reads": len(reader.calls),
        "websocket_batch_connections": len(ha.batch_calls),
        "websocket_batch_commands": sum(len(item) for item in ha.batch_calls),
        "loaded_script_command_count": profile.get("loaded_script_command_count", 0),
        "script_config_source": profile.get("script_config_source", "stored_delegated (pre-HAMCP-212)"),
        "script_config_source_counts": profile.get("script_config_source_counts"),
        "script_stored_config_fallback_reasons": profile.get("script_stored_config_fallback_reasons"),
        "script_coverage": {
            "provider": script_coverage.provider,
            "completeness": script_coverage.completeness,
            "failed_item_count": script_coverage.failed_item_count,
            "evidence_count": script_coverage.evidence_count,
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
