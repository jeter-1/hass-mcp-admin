"""HAMCP-515: the two collision indicators agree.

The beta.13 delegated-usage evidence (U7) saw ``ha_get_blueprint`` published
with ``collision: true`` while the gateway's ``collision_count`` was 0. The
per-tool flag compared the exposed name with the upstream name, so the one
reviewed rename (``ha_manage_blueprints`` -> ``ha_get_blueprint``) read as a
collision. A collision is an alias forced by a name clash, the
``collision_mappings`` rows; the flag now means exactly that.

Offline and deterministic: the stock 9.2.0 registry capture, no live Home
Assistant, no network.
"""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]

from mcp.server.fastmcp import FastMCP  # noqa: E402

from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    ALIAS_PREFIX,
    UpstreamReadGateway,
)
from ha_mcp_engineering.providers import upstream_yaml_reader  # noqa: E402
import test_hamcp371_real_core_replay as replay  # noqa: E402
from tests.support.process_isolation import Guard  # noqa: E402

VERSION = "9.2.0"
RENAMED = "ha_get_blueprint"
UPSTREAM = "ha_manage_blueprints"
OPTIONAL = upstream_yaml_reader.TOOL_NAME

_BEFORE = None


def setUpModule():
    global _BEFORE
    _BEFORE = Guard().capture()


def tearDownModule():
    changes = Guard.changes(_BEFORE)
    if changes:
        raise AssertionError(f"{__name__} left process-wide state: " + "; ".join(changes))


def _yaml_catalog():
    """The exact filesystem-on 9.2.0 catalog the YAML reader binds, hash-checked."""

    _source, path, digest = upstream_yaml_reader._CAPTURES[VERSION]
    if replay.sha(ROOT / path) != digest:
        raise AssertionError(f"{path} does not match its bound sha256")
    return json.loads((ROOT / path).read_text(encoding="utf-8"))["tools"]


async def _admit(server, tools):
    """The 9.2.0 gateway on ``tools`` (default: the stock capture), initialized on ``server``."""

    tools = tools if tools is not None else replay.previous.capture(VERSION)["tools"]
    gateway = UpstreamReadGateway()
    gateway.configure(
        replace(replay._settings("synthetic-no-key"), ha_mcp_release_registry_enabled=False,
                upstream_dashboard_mcp_url=replay.REPLAY_URL),
        transport=replay._Transport(tools, VERSION),
    )
    await gateway.initialize(server)
    rows = {row["tool"]: row for row in gateway._dynamic_capabilities}
    return rows, gateway.health_snapshot()


def admit(server=None, tools=None):
    return asyncio.run(_admit(server or FastMCP("hamcp515"), tools))


class CollisionIndicators(unittest.TestCase):

    def assert_agree(self, rows, health):
        """Every row's flag is membership in ``collision_mappings``."""

        mapped = {item["exposed_name"]: item["upstream_name"] for item in health["collision_mappings"]}
        self.assertEqual(health["collision_count"], len(mapped))
        for name, row in rows.items():
            with self.subTest(tool=name):
                self.assertIs(row["collision"], name in mapped)
                if name in mapped:
                    self.assertEqual(row["upstream_tool"], mapped[name])
        self.assertLessEqual(set(mapped), set(rows))

    def test_reviewed_rename_is_not_a_collision(self):
        rows, health = admit()
        row = rows[RENAMED]
        self.assertEqual(row["upstream_tool"], UPSTREAM)
        self.assertIs(row["collision"], False)
        self.assertEqual(health["collision_count"], 0)
        self.assertEqual(health["collision_mappings"], [])
        self.assertNotIn(UPSTREAM, rows)
        # The stock 9.2.0 catalog has no clash, so no row is a collision.
        self.assertEqual([name for name, item in rows.items() if item["collision"]], [])
        self.assert_agree(rows, health)

    def test_forced_alias_is_a_collision(self):
        server = FastMCP("hamcp515-clash")

        async def existing(path: str):
            return {"implementation": "engineering", "path": path}

        server.tool(name=RENAMED)(existing)
        rows, health = admit(server)
        alias = f"{ALIAS_PREFIX}{UPSTREAM}"
        self.assertNotIn(RENAMED, rows)
        self.assertEqual(rows[alias]["upstream_tool"], UPSTREAM)
        self.assertIs(rows[alias]["collision"], True)
        self.assertEqual(health["collision_count"], 1)
        self.assertEqual(health["collision_mappings"], [{"upstream_name": UPSTREAM, "exposed_name": alias}])
        # Only the alias is a collision; every other row is not.
        self.assertEqual([name for name, item in rows.items() if item["collision"]], [alias])
        self.assert_agree(rows, health)

    def test_optional_read_follows_the_same_rule(self):
        rows, health = admit(tools=_yaml_catalog())
        self.assertIs(rows[OPTIONAL]["optional"], True)
        self.assertIs(rows[OPTIONAL]["collision"], False)
        self.assertEqual(health["collision_count"], 0)
        self.assert_agree(rows, health)

        server = FastMCP("hamcp515-optional-clash")

        async def existing(path: str):
            return {"implementation": "engineering", "path": path}

        server.tool(name=OPTIONAL)(existing)
        rows, health = admit(server, _yaml_catalog())
        alias = f"{ALIAS_PREFIX}{OPTIONAL}"
        self.assertIs(rows[alias]["optional"], True)
        self.assertIs(rows[alias]["collision"], True)
        self.assertEqual(health["collision_mappings"], [{"upstream_name": OPTIONAL, "exposed_name": alias}])
        self.assert_agree(rows, health)


if __name__ == "__main__":
    unittest.main()
