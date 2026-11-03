"""HAMCP-285 R1-H1: reader-output digests for helpers that are in no group.

The group-membership hold must leave a non-member's dependency reader output
byte-identical to 2630d901.  ``nonmember_digests`` builds a real
``DependencyIndex`` over synthetic installations (minimal, or with two
unrelated noise automations; no group, or an unrelated group; no consumer, a
literal consumer, or a consumer of the unrelated group) and returns the
SHA-256 of each canonical reader result, observational clocks removed.

Regenerate the pinned digests from an exact source tree:

    python tests/hamcp285_nonmember_digests.py <tree-at-2630d901>

The tree's packages are placed first on ``sys.path``; this file reads only
``tests/test_hamcp274_input_select_relevance.py`` from that tree, which exists
unchanged at 2630d901.  Synthetic and offline.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


OBSERVATIONAL = frozenset({"built_at", "evidence_age_seconds", "lookup_duration_ms"})
LOCK_STATE = {"entity_id": "lock.synthetic_door", "state": "unlocked",
              "attributes": {}}
DOMAINS = ("input_boolean", "input_select")


def canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()
                if key not in OBSERVATIONAL}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def cases() -> list[tuple[str, bool, str, str]]:
    return [
        (domain, noise, groups, relation)
        for domain in DOMAINS
        for noise in (False, True)
        for groups in ("no_group", "unrelated_group")
        for relation in ("none", "literal", "group_other")
        if not (relation == "group_other" and groups == "no_group")
    ]


def case_key(domain: str, noise: bool, groups: str, relation: str) -> str:
    return f"{domain}/{'noise' if noise else 'minimal'}/{groups}/{relation}"


async def nonmember_digests() -> dict[str, str]:
    from ha_mcp_engineering.dependency.index import DependencyIndex
    from ha_mcp_engineering.dependency.provider import DirectHaDependencyProvider
    from ha_mcp_engineering.governance.helper_dependency import (
        HelperDependencyRiskService,
    )
    from ha_mcp_engineering.governance.input_boolean_dependency import (
        build_input_boolean_dependency_binding,
    )
    from ha_mcp_engineering.governance.input_select_dependency import (
        build_input_select_dependency_binding,
    )
    from tests.test_hamcp274_input_select_relevance import (
        SUPPORTED_HA_VERSION,
        installation_configs,
        installation_registry,
    )

    class Rest:
        def __init__(self, configs: dict[str, Any], states: list[dict[str, Any]]):
            self.configs, self.states = configs, states

        async def request(self, method: str, path: str, **_: Any) -> Any:
            if method != "GET":
                raise AssertionError(f"unreviewed request: {method} {path}")
            if path == "/config":
                return {"version": SUPPORTED_HA_VERSION}
            if path == "/states":
                return deepcopy(self.states) + [
                    {"entity_id": f"automation.{key}", "state": "on",
                     "attributes": {"id": key, "friendly_name": value["alias"]}}
                    for key, value in self.configs.items()
                ]
            return deepcopy(
                self.configs[path.removeprefix("/config/automation/config/")]
            )

    class WebSocket:
        async def command(self, payload: dict[str, Any]) -> Any:
            if payload == {"type": "config/entity_registry/list"}:
                return installation_registry()
            if payload == {"type": "config/label_registry/list"}:
                return [{"label_id": "kitchen", "name": "Kitchen"}]
            raise AssertionError(payload)

    builders = {
        "input_boolean": build_input_boolean_dependency_binding,
        "input_select": build_input_select_dependency_binding,
    }
    digests: dict[str, str] = {}
    for domain, noise, groups, relation in cases():
        entity = f"{domain}.review_mode"
        configs = (
            {key: value for key, value in installation_configs().items()
             if key in ("room_00", "kitchen_0")}
            if noise else {}
        )
        automation: dict[str, Any] = {
            "alias": "Synthetic consumer",
            "triggers": [{"trigger": "time_pattern", "minutes": "/5"}],
            "conditions": [],
            "actions": [{"action": "lock.lock",
                         "target": {"entity_id": "lock.synthetic_door"}}],
        }
        if relation == "literal":
            automation["triggers"] = [
                {"trigger": "state", "entity_id": entity, "to": "on"}
            ]
        elif relation == "group_other":
            automation["triggers"] = [
                {"trigger": "state", "entity_id": "group.other", "to": "on"}
            ]
        configs["consumer"] = automation
        states = [
            {"entity_id": entity, "state": "off",
             "attributes": {"options": ["off", "on"]}},
            dict(LOCK_STATE),
        ]
        if groups == "unrelated_group":
            states.append({
                "entity_id": "group.other", "state": "off",
                "attributes": {"entity_id": ["light.room_1",
                                             f"{domain}.other_helper"]},
            })
        index = DependencyIndex(
            DirectHaDependencyProvider(Rest(configs, states), WebSocket(),
                                       concurrency=1)
        )
        result = await HelperDependencyRiskService(
            index, binding_builder=builders[domain]
        ).assess(entity, refresh=True, fenced=False)
        text = json.dumps(canonical(result), sort_keys=True, separators=(",", ":"))
        digests[case_key(domain, noise, groups, relation)] = hashlib.sha256(
            text.encode("utf-8")
        ).hexdigest()
    return digests


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    tree = Path(argv[1]).resolve()
    sys.path[:0] = [str(tree / "hass_mcp_engineering_beta"), str(tree)]
    for key, digest in asyncio.run(nonmember_digests()).items():
        print(key, digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
