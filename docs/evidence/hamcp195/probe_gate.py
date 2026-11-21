"""HAMCP-195 design-validation probe (not runtime code, not a test).

Builds the shipped dependency index (HAMCP-209) over synthetic and anonymized
automations, computes the shipped ``helper-dependency-risk-v13`` binding for
each input_boolean, and evaluates the two candidate fast-path gates from
docs/HAMCP_195_GATED_HELPER_TOGGLE_DESIGN.md section 4:

* ``strict``: the binding is what the governed policy classifies as
  STANDARD_ADMIN (complete, exact, physical_consequence ``none``).
* ``owner``: complete and exact, no ``unknown`` or ``safety_critical``
  downstream automation, and every ``direct`` effect target literal and not a
  protected object under HAMCP-069B OD-4.

Everything is offline. Nothing contacts Home Assistant.

    PYTHONPATH=tests python docs/evidence/hamcp195/probe_gate.py [--dump ENTITY]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DirectHaDependencyProvider,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    HelperDependencyRiskService,
)
from ha_mcp_engineering.governance.physical_effects import (  # noqa: E402
    protected_object_reason,
)
import tests.hamcp209_fixtures as fixtures  # noqa: E402
from ha_mcp_engineering.dependency.semantic_registry import (  # noqa: E402
    supported_home_assistant_versions,
)
from tests.hamcp209_fixtures import SyntheticHomeAssistant, record  # noqa: E402

# The synthetic default (2026.10.1) is admitted only with in-memory reviewed
# Core semantics; pin the newest compiled release so admission is not the gate.
fixtures.SYNTHETIC_VERSION = supported_home_assistant_versions()[-1]

PR001 = ROOT / "tests" / "fixtures" / "hamcp213_pr001"

# Registry device classes a Part 2 implementation would read from the entity
# registry. Synthetic.
DEVICE_CLASSES = {
    "cover.garage_main": "garage",
    "cover.synthetic_side_gate": "gate",
    "cover.synthetic_blind": "blind",
}
PROTECTED_COVER_CLASSES = {"garage", "gate", "door"}
PROTECTED_DOMAINS = {"lock", "alarm_control_panel", "valve", "siren"}

EXTRA_ENTITIES = [
    "cover.garage_main", "cover.synthetic_side_gate", "cover.synthetic_blind",
    "lock.synthetic_front", "timer.alert_snooze", "person.resident_a",
    "binary_sensor.garage_occupancy", "input_number.departure_resident_a_pending_until",
    "script.synthetic_routine", "media_player.synthetic_speaker",
]
HELPERS = [
    "input_boolean.visitor_mode", "input_boolean.service_visit_mode",
    "input_boolean.synthetic_quiet_notify", "input_boolean.synthetic_lights_mode",
    "input_boolean.synthetic_blind_mode", "input_boolean.synthetic_gate_mode",
    "input_boolean.synthetic_script_mode", "input_boolean.synthetic_lock_mode",
    "input_boolean.synthetic_opaque_mode", "input_boolean.synthetic_unused",
]
SCENARIOS = ("pr001_house", "op4_is_number_as_test", "no_opaque_consumers")


class Home(SyntheticHomeAssistant):
    def states(self):
        rows = super().states()
        for entity_id in EXTRA_ENTITIES + HELPERS:
            rows.append({"entity_id": entity_id, "state": "off",
                         "attributes": {"friendly_name": entity_id}})
        return rows


def _automation(object_id, trigger_entity, helper, actions):
    return record(f"automation.{object_id}", {
        "id": object_id, "alias": object_id,
        "triggers": [{"trigger": "state", "entity_id": trigger_entity}],
        "conditions": [{"condition": "state", "entity_id": helper, "state": "on"}],
        "actions": actions,
    })


def automations(scenario: str):
    items = []
    for name in ("op1_after", "op2_after", "op4_after"):
        config = yaml.safe_load((PR001 / f"{name}.yaml").read_text(encoding="utf-8"))
        if name == "op4_after":
            if scenario == "no_opaque_consumers":
                continue
            if scenario == "op4_is_number_as_test":
                # Cause isolation only: the same check written as a Jinja test
                # instead of the global call the semantic registry lacks.
                template = config["conditions"][2]["value_template"]
                assert "is_number(opened_at)" in template
                config["conditions"][2]["value_template"] = template.replace(
                    "is_number(opened_at)", "(opened_at is number)")
        items.append(record(f"automation.{config['id']}", config))
    motion = "binary_sensor.synthetic_motion"
    items += [
        _automation("synthetic_notify", motion, "input_boolean.synthetic_quiet_notify",
                    [{"action": "notify.mobile_app_owner_phone", "data": {"message": "Motion"}}]),
        _automation("synthetic_lights", motion, "input_boolean.synthetic_lights_mode",
                    [{"action": "light.turn_on", "target": {"entity_id": "light.synthetic_kitchen"}}]),
        _automation("synthetic_blind", motion, "input_boolean.synthetic_blind_mode",
                    [{"action": "cover.close_cover", "target": {"entity_id": "cover.synthetic_blind"}}]),
        _automation("synthetic_gate", motion, "input_boolean.synthetic_gate_mode",
                    [{"action": "cover.close_cover", "target": {"entity_id": "cover.synthetic_side_gate"}}]),
        _automation("synthetic_script", motion, "input_boolean.synthetic_script_mode",
                    [{"action": "script.turn_on", "target": {"entity_id": "script.synthetic_routine"}}]),
        _automation("synthetic_lock", motion, "input_boolean.synthetic_lock_mode",
                    [{"action": "lock.unlock", "target": {"entity_id": "lock.synthetic_front"}}]),
    ]
    if scenario == "no_opaque_consumers":
        return items
    items += [
        record("automation.synthetic_opaque", {
            "id": "synthetic_opaque", "alias": "synthetic_opaque",
            "triggers": [{"trigger": "state", "entity_id": motion}],
            "conditions": [{"condition": "template", "value_template":
                            "{{ is_state('input_boolean.' ~ states('input_text.synthetic_target'), 'on') }}"}],
            "actions": [{"action": "notify.mobile_app_owner_phone", "data": {"message": "x"}}],
        }),
    ]
    return items


def _protected_target(entity_id: str) -> str | None:
    reason = protected_object_reason(entity_id)
    if reason:
        return reason
    domain = entity_id.split(".", 1)[0]
    if domain in PROTECTED_DOMAINS:
        return f"protected_domain_{domain}"
    if domain == "cover":
        device_class = DEVICE_CLASSES.get(entity_id)
        if device_class is None:
            return "cover_device_class_unresolved"
        if device_class in PROTECTED_COVER_CLASSES:
            return f"cover_device_class_{device_class}"
    return None


def _evidence_reasons(binding: dict) -> list[str]:
    reasons = []
    if binding.get("model") != "helper-dependency-risk-v13":
        reasons.append("model_not_current")
    for key in ("execution_contract_complete", "execution_eligible",
                "coverage_complete", "consequence_evidence_complete"):
        if binding.get(key) is not True:
            reasons.append(f"{key}_false")
    if binding.get("execution_block_reason_codes"):
        reasons.append("execution_blocked")
    if binding.get("semantic_precision") != "exact":
        reasons.append(f"semantic_precision_{binding.get('semantic_precision')}")
    return reasons


def gate_strict(binding: dict) -> list[str]:
    reasons = _evidence_reasons(binding)
    if binding.get("physical_consequence") != "none":
        reasons.append(f"consequence_{binding.get('physical_consequence')}")
    return reasons


def gate_owner(binding: dict) -> list[str]:
    reasons = _evidence_reasons(binding)
    for profile in binding.get("downstream_profiles") or []:
        source = profile.get("automation_id") or "?"
        consequence = profile.get("physical_consequence")
        if consequence in ("unknown", "safety_critical", None):
            reasons.append(f"consumer_{consequence}:{source}")
            continue
        if consequence == "direct":
            # Each entry is "<config path>|<field>|<literal entity id>".
            entries = profile.get("effect_targets") or []
            targets = [e.rsplit("|", 1)[-1] for e in entries if isinstance(e, str) and e.count("|") == 2]
            if (not entries or len(targets) != len(entries)
                    or profile.get("effect_target_count") != len(entries)
                    or any("." not in t for t in targets)):
                reasons.append(f"consumer_direct_target_unresolved:{source}")
            for target in targets:
                protected = _protected_target(target)
                if protected:
                    reasons.append(f"consumer_protected_target:{source}:{target}:{protected}")
    return reasons


async def run(scenario: str, dump: str | None) -> list[dict]:
    ha = Home(automations(scenario))
    index = DependencyIndex(DirectHaDependencyProvider(ha.rest, ha.websocket,
                                                       secret="synthetic-not-a-credential"))
    await index.get(refresh=True)
    service = HelperDependencyRiskService(index)
    rows = []
    for helper in HELPERS:
        evidence = await service.assess(helper, refresh=False)
        binding = evidence["binding"]
        if dump == helper:
            print(json.dumps(binding, indent=1, sort_keys=True, default=str))
            raise SystemExit(0)
        rows.append({
            "helper": helper,
            "model": binding.get("model"),
            "physical_consequence": binding.get("physical_consequence"),
            "coverage_failure_reason_codes": binding.get("coverage_failure_reason_codes"),
            "semantic_precision": binding.get("semantic_precision"),
            "coverage_complete": binding.get("coverage_complete"),
            "consumers": sorted(binding.get("relevant_downstream_object_ids") or []),
            "strict": gate_strict(binding) or "QUALIFIES",
            "owner": gate_owner(binding) or "QUALIFIES",
        })
    return rows


async def replay_beta51() -> list[dict]:
    """Sanitized Beta 51 production replay (HAMCP-089), unchanged."""

    from tests.test_beta50_helper_production_target_scope import (
        CapturedBeta50ReplayRest,
    )
    from tests.test_beta52_helper_label_target_scope import (
        REPLAY,
        Beta51LabelScopeReplayWebSocket,
    )

    fixture = json.loads(REPLAY.read_text(encoding="utf-8"))
    rest = CapturedBeta50ReplayRest(fixture)
    websocket = Beta51LabelScopeReplayWebSocket(fixture, rest.ids)
    index = DependencyIndex(DirectHaDependencyProvider(rest, websocket, concurrency=4))
    await index.get(refresh=True)
    service = HelperDependencyRiskService(index)
    rows = []
    for helper in ("input_boolean.target_standard", "input_boolean.entity_001"):
        binding = (await service.assess(helper, refresh=False))["binding"]
        rows.append({
            "helper": helper,
            "model": binding.get("model"),
            "physical_consequence": binding.get("physical_consequence"),
            "semantic_precision": binding.get("semantic_precision"),
            "coverage_complete": binding.get("coverage_complete"),
            "consumers": sorted(binding.get("relevant_downstream_object_ids") or []),
            "strict": gate_strict(binding) or "QUALIFIES",
            "owner": gate_owner(binding) or "QUALIFIES",
        })
    return rows


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=SCENARIOS)
    parser.add_argument("--dump")
    args = parser.parse_args()
    out = {"home_assistant_version": fixtures.SYNTHETIC_VERSION}
    for scenario in SCENARIOS:
        if args.scenario and scenario != args.scenario:
            continue
        out[scenario] = await run(scenario, args.dump)
    if not args.scenario:
        out["beta51_production_replay"] = await replay_beta51()
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
