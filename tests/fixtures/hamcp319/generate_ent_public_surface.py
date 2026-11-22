"""Generate ent-public-surface-8.5.0.json (HAMCP-437 I2a-D, ANSWER 20261122-0931-01 case 2).

Engineering-published surface generated from the 8.5.0 row at 310c6a0e; not
upstream evidence. The surface is what Engineering's own UpstreamReadGateway
publishes for the ten ENT tools when it is built on the committed ha-mcp 8.5.0
catalog capture, with the construction test_92_parity_rows_public_surface_matches_85
used at 310c6a0e. Run it against a checkout of that commit, where the 8.5.0 row
still exists:

    git clone --shared <repo> <checkout> && git -C <checkout> checkout 310c6a0e
    python tests/fixtures/hamcp319/generate_ent_public_surface.py --repo <checkout>

Offline; no live Home Assistant, no network.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / 'ent-public-surface-8.5.0.json'
PROVENANCE = HERE / 'provenance.json'
SOURCE_COMMIT = '310c6a0e6b5b0abf9ea20d849d97ddaaf14c7933'
LABEL = 'Engineering-published surface generated from the 8.5.0 row at 310c6a0e; not upstream evidence'
ENT_TOOLS = (
    'ha_get_entity', 'ha_get_entity_exposure', 'ha_get_overview',
    'ha_get_skill_guide', 'ha_get_state', 'ha_get_todo', 'ha_get_zone',
    'ha_list_floors_areas', 'ha_list_services', 'ha_search',
)
# The case never compares this description (its 9.2.0 text is pinned separately).
NO_DESCRIPTION = 'ha_get_skill_guide'


def tool_record(tool) -> dict:
    record = {
        'name': tool.name,
        'inputSchema': tool.inputSchema,
        'annotations': tool.annotations.model_dump(mode='json') if tool.annotations else None,
    }
    if tool.name != NO_DESCRIPTION:
        record['description'] = tool.description
    return record


async def surface(repo: Path) -> list[dict]:
    sys.path[:0] = [str(repo / 'hass_mcp_engineering_beta'), str(repo / 'tests')]
    from mcp.server.fastmcp import FastMCP
    from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
    from test_ha_mcp_production_readmission import _GatewayTransport, _settings
    import support.ha_mcp_captures as previous

    transport = _GatewayTransport(previous.capture('8.5.0')['tools'], version='8.5.0')
    gateway = UpstreamReadGateway()
    gateway.configure(replace(_settings('synthetic-no-key'), ha_mcp_release_registry_enabled=False),
                      transport=transport)
    server = FastMCP('synthetic-hamcp319-ent-85')
    await gateway.initialize(server)
    tools = {t.name: t for t in await server.list_tools() if t.name in ENT_TOOLS}
    if set(tools) != set(ENT_TOOLS):
        raise SystemExit(f'ENT tools missing from the 8.5.0 surface: {sorted(set(ENT_TOOLS) - set(tools))}')
    return [tool_record(tools[name]) for name in ENT_TOOLS]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--repo', type=Path, required=True, help='checkout of 310c6a0e')
    args = parser.parse_args()
    repo = args.repo.resolve()
    head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], check=True,
                          capture_output=True, text=True).stdout.strip()
    if head != SOURCE_COMMIT:
        raise SystemExit(f'--repo is at {head}, not {SOURCE_COMMIT}')
    tools = asyncio.run(surface(repo))
    body = json.dumps({'label': LABEL, 'source_commit': SOURCE_COMMIT, 'ha_mcp_row': '8.5.0',
                       'tools': tools}, indent=2, sort_keys=True) + '\n'
    OUTPUT.write_text(body, encoding='utf-8')
    PROVENANCE.write_text(json.dumps({
        'label': LABEL,
        'fixture': 'tests/fixtures/hamcp319/ent-public-surface-8.5.0.json',
        'sha256': hashlib.sha256(body.encode('utf-8')).hexdigest(),
        'source_commit': SOURCE_COMMIT,
        'generator': 'tests/fixtures/hamcp319/generate_ent_public_surface.py',
        'command': ('python tests/fixtures/hamcp319/generate_ent_public_surface.py '
                    '--repo <checkout of 310c6a0e>'),
        'decision': 'ANSWER 20261122-0931-01 case 2 (HAMCP-437 I2a-D)',
    }, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
