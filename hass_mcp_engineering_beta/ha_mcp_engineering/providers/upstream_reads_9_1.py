"""Closed 9.1.0 public reads over the exact 9.1 wire (HAMCP-319 Phase 2).

The public schema of every read is the exact shipped 8.5.0 public schema (the
8.6 module's binary-owned copies, bound below by fingerprint), so a 9.1 caller
sees today's 8.5 surface. Arguments are rebuilt by Engineering into the exact
9.1 wire shape (design section 7); upstream data never selects a key.
``ha_get_hacs_info`` is not in this adapter: the 9.1 policy classifies it
``unsupported`` (ledger row 15, HAMCP-290), so it has no read route.
"""
from copy import deepcopy
import re

from .upstream_reads_8_6 import PUBLIC_SCHEMAS as _PUBLIC_SCHEMAS_8_6

VERSION = "9.1.0"
ADAPTER = "ha-mcp-9.1.0-public-reads-v1"
SOURCE = "d2e3f4b0647d815fb32d530fed15a6a99b1d4b33"
# Exact 9.1 wire input-schema fingerprints from
# docs/evidence/upstream-read-compatibility/ha-mcp-9.1.0.json.
INPUT_FINGERPRINTS = {'ha_config_get_automation': 'a4d7a28e64518ed0d8233a48310f08994bce5a39d3336684ea0ccb8f3269a7d0',
 'ha_config_get_calendar_events': '756f64f9ecfd7932522df435b7a152545c107aa7eed4b7c9146f1e7b681fc3b2',
 'ha_config_get_category': '0ed4ad9eec4d162099f9ad7b80c5bb0d9bbc25912b4f6f19c553ff08ed3c377b',
 'ha_config_get_label': 'e9e05e1cd301936b4c7a7d5b609744602268f3ff19a7871678f7ea5fcb8ae7b5',
 'ha_config_get_scene': 'e471799f1d1761b7112fbe6af84f2769497e1d4a3b6dc476a2ce268a467a00ef',
 'ha_config_get_script': '941d56128c6a1cfc9352c9f6f69db4248431149bb2458b6fd5d9c960886d6dc2',
 'ha_config_list_dashboard_resources': '044e1dce4b2d66dc95dee4166eb08de8b7bf3a5a54277359eb126bcf8b7437ad',
 'ha_config_list_groups': '329191e6416743be5cf024e6062f3f00cc858b0ce5ddef87771ea52af88c37e3',
 'ha_config_list_helpers': '7a122c1436813859e3a8306798beaeceeda3fe3eb032b379701fee50c75bd504',
 'ha_eval_template': '1cd46b20bfefc8bd2489de654de78bdb225f7efb42a210f3cfcf7e97040c9a86',
 'ha_get_automation_traces': 'f22754974ac6f44022b7bee3ece027b02e8edb47db76531431adb148f76b68e9',
 'ha_get_device': 'c6700885a55d150e6a7286189c9f00be611fb3bf82b9d37ef103d14b13f4b2f1',
 'ha_get_entity': '503e365334782564ae14bf9672c683033e8a27c6706ba52c559a5882abbe0cbb',
 'ha_get_entity_exposure': '596b9a5acf1ba8028ffe67c56c8de52245c63269e3cedb67dde8f7e43eb2c047',
 'ha_get_history': 'b5b22eccdb18eb05151a30fff736fe85cc682a385b7922f58d39848f4bb415fa',
 'ha_get_overview': 'd287494e2dcacb47275acc04905cf63568ad5eee7576285a998be506d36149e3',
 'ha_get_skill_guide': 'b9088e1e95822b05f2d1205f2979e9756d9fc303db1e1023597cfb56c0ae1133',
 'ha_get_state': '079c4fa378e1f5c2b19f8c46bea46d5f572bc5b3724cb605794ff59edb942226',
 'ha_get_todo': '12e8313054860017feb901c9b96fceb00cae71c2dbd78afb90d50aaa933f8a70',
 'ha_get_zone': '70fdd40ad93278a5685654e046bb3cbac27c15cf8a6eba30b166c6f1d89470b2',
 'ha_list_floors_areas': 'b81375d58db7ba1a03224194d3e054d4124e4261b99d76e54b7dda41cb38b55a',
 'ha_list_services': 'da65206809eafce40f77be1ef87045a4155fd4c085b149b85a41c7d5db280ce1',
 'ha_search': '58ed5cc91f928d9dc33437a97ad0ae46641b17891dfed2f7657f2658c83276c5'}
# Exact shipped 8.5.0 wire schema (no listing tier is synthesized; R4).
SKILL_GUIDE_PUBLIC_SCHEMA = {'additionalProperties': False,
 'properties': {'file': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                         'default': None,
                         'description': 'Reference file path within the skill, relative to the '
                                        "skill directory (e.g., 'SKILL.md' or "
                                        "'references/automation-patterns.md'). Requires skill to "
                                        'be set.'},
                'skill': {'anyOf': [{'type': 'string'}, {'type': 'null'}],
                          'default': None,
                          'description': 'Skill name from the no-args listing (e.g., '
                                         "'home-assistant-best-practices')."}},
 'type': 'object'}
BEST_PRACTICES_SKILL = "home-assistant-best-practices"
# Binds the imported 8.5 public copies; an edit to the 8.6 module that would
# change the 9.1 public surface fails closed at import.
_PUBLIC_SCHEMAS_8_6_SUBSET_FINGERPRINT = (
    "627acd5eb9efe48c2118b2e22a46281b3a754becbd388cb44a041a87ae1aaec6"
)
PUBLIC_SCHEMAS_FINGERPRINT = (
    "ffc228db1e65e8fa47174637ac41e8ba150a11565446880be1f00c970a24a10f"
)


def _build_public_schemas() -> dict:
    from ..upstream_tool_policy import schema_fingerprint
    subset = {
        name: deepcopy(_PUBLIC_SCHEMAS_8_6[name])
        for name in INPUT_FINGERPRINTS if name != "ha_get_skill_guide"
    }
    if schema_fingerprint(subset) != _PUBLIC_SCHEMAS_8_6_SUBSET_FINGERPRINT:
        raise RuntimeError("upstream_reads_9_1_public_schema_drift")
    schemas = {**subset, "ha_get_skill_guide": deepcopy(SKILL_GUIDE_PUBLIC_SCHEMA)}
    if schema_fingerprint(schemas) != PUBLIC_SCHEMAS_FINGERPRINT:
        raise RuntimeError("upstream_reads_9_1_public_schema_drift")
    return schemas


PUBLIC_SCHEMAS = _build_public_schemas()


class ReadTierUnavailable(ValueError):
    """A shipped 8.5 public tier that 9.1 cannot serve; never synthesized (R4)."""


def is_adapter(entry) -> bool:
    return (
        entry.classification == "automatic_read"
        and entry.exposed_name == entry.upstream_name
        and entry.argument_restrictions == (ADAPTER,)
        and entry.input_schema_fingerprint == INPUT_FINGERPRINTS.get(entry.upstream_name)
        and entry.reviewed_annotations.read_only
        and not entry.reviewed_annotations.destructive
    )


def public_schema(entry, observed: dict) -> dict:
    return deepcopy(PUBLIC_SCHEMAS[entry.upstream_name] if is_adapter(entry) else observed)


def _skill_file(value) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 512
            or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", value)
            or any(part in {".", ".."} for part in value.split("/"))):
        raise ValueError("skill_file_invalid")
    return value


def read_arguments(entry, arguments: dict) -> dict:
    """Build the exact 9.1 wire arguments from validated 8.5 public arguments."""

    if not is_adapter(entry):
        return dict(arguments)
    name = entry.upstream_name
    if not isinstance(arguments, dict) or set(arguments) - set(
        PUBLIC_SCHEMAS[name].get("properties", {})
    ):
        raise ValueError("read_arguments_invalid")
    if name == "ha_config_get_automation":
        # Row 1: public ``identifier`` -> 9.1 ``entity_id`` (same identity).
        identifier = arguments.get("identifier")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("automation_identity_required")
        return {"entity_id": identifier}
    if name == "ha_config_get_scene":
        # Row 5: discovery (query/search_in_config/limit/offset) never runs.
        scene = arguments.get("scene_id")
        if not isinstance(scene, str) or not scene.strip():
            raise ValueError("scene_identity_required")
        return {"scene_id": scene}
    if name == "ha_config_list_dashboard_resources":
        # Row 7: include_hash is never sent.
        return dict(arguments)
    if name == "ha_config_list_helpers":
        # Row 9: list mode only; describe pinned off, describe-only keys unset.
        return {**arguments, "describe": False}
    if name == "ha_eval_template":
        # Row 10: render only; no condition executor, variables or strict mode.
        timeout = arguments.get("timeout", 3)
        if type(timeout) is not int or not 1 <= timeout <= 60:
            raise ValueError("template_timeout_invalid")
        return {
            "template": arguments["template"], "timeout": timeout,
            "report_errors": arguments.get("report_errors", True),
            "condition": None, "variables": None, "strict": False,
        }
    if name == "ha_get_history":
        # Row 16 / rule 3: no summary, schema discovery or native Core options.
        return {
            **arguments, "summary": False, "include_schema": False,
            "core_options": None,
        }
    if name == "ha_get_skill_guide":
        # Row 18 / R4: file-only; inventory and file-list tiers unavailable.
        skill, file = arguments.get("skill"), arguments.get("file")
        if skill is None:
            if file is not None:
                raise ValueError("skill_required_for_file")
            raise ReadTierUnavailable("skill_inventory_unavailable")
        if skill != BEST_PRACTICES_SKILL:
            raise ValueError("skill_unknown")
        if file is None:
            raise ReadTierUnavailable("skill_file_list_unavailable")
        return {"file": _skill_file(file)}
    return dict(arguments)


def _projector_tables():
    # Per-area compiled projection modules (disjoint ledger rows). Each maps
    # upstream tool name -> (project(payload, arguments), completeness(payload)).
    from . import upstream_history_9_1, upstream_reads_9_1_config, upstream_reads_9_1_entities
    tables = {}
    for module in (upstream_reads_9_1_config, upstream_reads_9_1_entities, upstream_history_9_1):
        for name, pair in module.PROJECTIONS.items():
            if name in tables or name not in INPUT_FINGERPRINTS:
                raise RuntimeError("upstream_reads_9_1_projection_table_invalid")
            tables[name] = pair
    return tables


# Section 6.5: ha_get_device is projected by the compiled device allowlist
# after the composite adapter (upstream_device_projection), not here.
_DEVICE = "ha_get_device"


def project_result(entry, payload, arguments: dict):
    """Compiled 9.1 -> shipped 8.5 public result projection (design section 7).

    Raises ``ValueError`` (``invalid_response``) on an unrecognized shape and
    for any adapter row without a compiled projection (fail closed).
    """

    if not is_adapter(entry):
        return payload
    if entry.upstream_name == _DEVICE:
        return payload
    pair = _projector_tables().get(entry.upstream_name)
    if pair is None:
        raise ValueError("invalid_response")
    return pair[0](payload, arguments)


def completeness(entry, payload) -> tuple[bool, list[str]]:
    """Compiled 9.1 completeness and availability (design F3).

    Returns ``(partial, bounded fixed warnings)``; never upstream text. An
    adapter row without a compiled rule reports partial (fail closed).
    """

    if not is_adapter(entry):
        return False, []
    if entry.upstream_name == _DEVICE:
        return False, []
    pair = _projector_tables().get(entry.upstream_name)
    if pair is None:
        return True, ["The upstream result completeness could not be verified."]
    return pair[1](payload)
