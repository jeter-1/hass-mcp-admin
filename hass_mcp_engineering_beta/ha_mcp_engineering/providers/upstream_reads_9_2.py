"""Closed 9.2.0 public reads over the exact 9.2.0 wire (HAMCP-371 S8).

Exact ha-mcp 9.2.0 (51e3aac7) is bound by its own adapter token, source commit
and wire input fingerprints, never by analogy to 9.1. The fingerprints below
are computed from the committed registry capture
``docs/evidence/upstream-read-compatibility/ha-mcp-9.2.0.json``. Of the 23
adapter reads, three changed their wire input schema, each in a property
description only (``docs/evidence/hamcp371/s4/descriptor-diff-9.1.2-to-9.2.0.json``):
``ha_get_history`` (``start_time``), ``ha_list_services`` (``detail_level``,
``service_fields``) and ``ha_search`` (``result_fields``). The other 20 equal
9.1.2's.

The public schema of every read is the 9.1 public surface (the shipped 8.5.0
schemas), bound below by the same fingerprint, so a 9.2.0 caller sees the same
surface as a 9.1 caller. ``upstream_reads_9_1.py`` is not edited and gives this
module nothing but its public schemas, its skill-file check, its refusal class
and, until S10 closes the 9.2.0 key sets, its compiled projections.

S8 interim (S9 replaces it): ``ha_get_history`` refuses before dispatch. 9.2.0
resolves an ISO time without an offset in HA local time, where 9.1 forced UTC
(``tools_history.py`` at 51e3aac7), and the 9.2.0 history module that handles
that change is S9. No history request reaches 9.2.0 until then.
"""
from copy import deepcopy

from . import upstream_reads_9_1 as _reads_9_1

VERSION = "9.2.0"
ADAPTER = "ha-mcp-9.2.0-public-reads-v1"
SOURCE = "51e3aac788482a081807591cd5d58d07dc4e540b"
# Exact 9.2.0 wire input-schema fingerprints from
# docs/evidence/upstream-read-compatibility/ha-mcp-9.2.0.json.
INPUT_FINGERPRINTS = {
    "ha_config_get_automation": "a4d7a28e64518ed0d8233a48310f08994bce5a39d3336684ea0ccb8f3269a7d0",
    "ha_config_get_calendar_events": "756f64f9ecfd7932522df435b7a152545c107aa7eed4b7c9146f1e7b681fc3b2",
    "ha_config_get_category": "0ed4ad9eec4d162099f9ad7b80c5bb0d9bbc25912b4f6f19c553ff08ed3c377b",
    "ha_config_get_label": "e9e05e1cd301936b4c7a7d5b609744602268f3ff19a7871678f7ea5fcb8ae7b5",
    "ha_config_get_scene": "e471799f1d1761b7112fbe6af84f2769497e1d4a3b6dc476a2ce268a467a00ef",
    "ha_config_get_script": "941d56128c6a1cfc9352c9f6f69db4248431149bb2458b6fd5d9c960886d6dc2",
    "ha_config_list_dashboard_resources": "044e1dce4b2d66dc95dee4166eb08de8b7bf3a5a54277359eb126bcf8b7437ad",
    "ha_config_list_groups": "329191e6416743be5cf024e6062f3f00cc858b0ce5ddef87771ea52af88c37e3",
    "ha_config_list_helpers": "5d2f988dc6f34ea574d4576c55e099cfd17df738a7ec24cc301dd53754e7d16f",
    "ha_eval_template": "1cd46b20bfefc8bd2489de654de78bdb225f7efb42a210f3cfcf7e97040c9a86",
    "ha_get_automation_traces": "f22754974ac6f44022b7bee3ece027b02e8edb47db76531431adb148f76b68e9",
    "ha_get_device": "c6700885a55d150e6a7286189c9f00be611fb3bf82b9d37ef103d14b13f4b2f1",
    "ha_get_entity": "503e365334782564ae14bf9672c683033e8a27c6706ba52c559a5882abbe0cbb",
    "ha_get_entity_exposure": "596b9a5acf1ba8028ffe67c56c8de52245c63269e3cedb67dde8f7e43eb2c047",
    "ha_get_history": "ef8db9b239fea7cda6abfe438799bcc5fb5240a54b0a02157a48a983078e19c2",
    "ha_get_overview": "d287494e2dcacb47275acc04905cf63568ad5eee7576285a998be506d36149e3",
    "ha_get_skill_guide": "b9088e1e95822b05f2d1205f2979e9756d9fc303db1e1023597cfb56c0ae1133",
    "ha_get_state": "079c4fa378e1f5c2b19f8c46bea46d5f572bc5b3724cb605794ff59edb942226",
    "ha_get_todo": "12e8313054860017feb901c9b96fceb00cae71c2dbd78afb90d50aaa933f8a70",
    "ha_get_zone": "70fdd40ad93278a5685654e046bb3cbac27c15cf8a6eba30b166c6f1d89470b2",
    "ha_list_floors_areas": "b81375d58db7ba1a03224194d3e054d4124e4261b99d76e54b7dda41cb38b55a",
    "ha_list_services": "c6d7591f741e487dc77c9934d076b8646c49dc7847013c572784df110a865da8",
    "ha_search": "2258e36ce603a00a654ed3459dd78f2bfa46c21451b3b2cac90a23395eb754a2",
}
# version -> (adapter token, source commit, exact wire input fingerprints)
RELEASES = {VERSION: (ADAPTER, SOURCE, INPUT_FINGERPRINTS)}
_RELEASE_BY_ADAPTER = {adapter: version for version, (adapter, _, _) in RELEASES.items()}
# The same 23 reads as the 9.1 adapter, and no token or release shared with it,
# so a policy for one module can never satisfy the other's adapter.
if (len(_RELEASE_BY_ADAPTER) != len(RELEASES)
        or any(set(fps) != set(_reads_9_1.INPUT_FINGERPRINTS) for _, _, fps in RELEASES.values())
        or set(_RELEASE_BY_ADAPTER) & set(_reads_9_1._RELEASE_BY_ADAPTER)
        or set(RELEASES) & set(_reads_9_1.RELEASES)):
    raise RuntimeError("upstream_reads_9_2_release_table_invalid")

# S8 interim: refused before dispatch until the S9 history module exists.
_HISTORY = "ha_get_history"
# Section 6.5: ha_get_device is projected by the compiled device allowlist
# after the composite adapter (upstream_device_projection), not here.
_DEVICE = "ha_get_device"

ReadTierUnavailable = _reads_9_1.ReadTierUnavailable


def _build_public_schemas() -> dict:
    from ..upstream_tool_policy import schema_fingerprint
    schemas = deepcopy(_reads_9_1.PUBLIC_SCHEMAS)
    if (set(schemas) != set(INPUT_FINGERPRINTS)
            or schema_fingerprint(schemas) != _reads_9_1.PUBLIC_SCHEMAS_FINGERPRINT
            or _reads_9_1.PUBLIC_SCHEMAS_FINGERPRINT
            != "ffc228db1e65e8fa47174637ac41e8ba150a11565446880be1f00c970a24a10f"):
        raise RuntimeError("upstream_reads_9_2_public_schema_drift")
    return schemas


PUBLIC_SCHEMAS = _build_public_schemas()


def adapter_release(entry) -> str | None:
    """The exact 9.2 release whose compiled adapter this entry satisfies, or None."""

    restrictions = entry.argument_restrictions
    if len(restrictions) != 1:
        return None
    version = _RELEASE_BY_ADAPTER.get(restrictions[0])
    if version is None:
        return None
    fingerprints = RELEASES[version][2]
    if not (
        entry.classification == "automatic_read"
        and entry.exposed_name == entry.upstream_name
        and entry.input_schema_fingerprint == fingerprints.get(entry.upstream_name)
        and entry.reviewed_annotations.read_only
        and not entry.reviewed_annotations.destructive
    ):
        return None
    return version


def is_adapter(entry) -> bool:
    return adapter_release(entry) is not None


def names_adapter(entry) -> str | None:
    """The 9.2 release named by any adapter token the entry carries, or None."""

    for token in entry.argument_restrictions:
        if token in _RELEASE_BY_ADAPTER:
            return _RELEASE_BY_ADAPTER[token]
    return None


def adapter_token(entry) -> str:
    return RELEASES[adapter_release(entry)][0]


def public_schema(entry, observed: dict) -> dict:
    return deepcopy(PUBLIC_SCHEMAS[entry.upstream_name] if is_adapter(entry) else observed)


def read_arguments(entry, arguments: dict) -> dict:
    """Build the exact 9.2.0 wire arguments from validated 8.5 public arguments.

    Each row is the 9.1 row for the same read: the 20 wire schemas it targets
    are fingerprint-equal at 9.2.0, and ``ha_list_services`` and ``ha_search``
    changed descriptions only, so the arguments Engineering builds still
    validate against the exact 9.2.0 schemas.
    """

    if not is_adapter(entry):
        return dict(arguments)
    name = entry.upstream_name
    if not isinstance(arguments, dict) or set(arguments) - set(
        PUBLIC_SCHEMAS[name].get("properties", {})
    ):
        raise ValueError("read_arguments_invalid")
    if name == _HISTORY:
        raise ReadTierUnavailable("history_9_2_unreviewed")
    if name == "ha_config_get_automation":
        # Row 1: public ``identifier`` -> wire ``entity_id`` (same identity).
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
    if name == "ha_get_skill_guide":
        # Row 18 / R4: file-only; inventory and file-list tiers unavailable.
        skill, file = arguments.get("skill"), arguments.get("file")
        if skill is None:
            if file is not None:
                raise ValueError("skill_required_for_file")
            raise ReadTierUnavailable("skill_inventory_unavailable")
        if skill != _reads_9_1.BEST_PRACTICES_SKILL:
            raise ValueError("skill_unknown")
        if file is None:
            raise ReadTierUnavailable("skill_file_list_unavailable")
        return {"file": _reads_9_1._skill_file(file)}
    return dict(arguments)


def _projection(name):
    # S8 interim: the compiled 9.1 projections; S10 replaces them with the
    # closed 9.2.0 key sets. History has no 9.2.0 projection before S9.
    if name == _HISTORY:
        return None
    return _reads_9_1._projector_tables().get(name)


def project_result(entry, payload, arguments: dict):
    """Compiled 9.2.0 -> shipped 8.5 public result projection.

    Raises ``ValueError`` (``invalid_response``) on an unrecognized shape and
    for any adapter row without a compiled projection (fail closed).
    """

    if not is_adapter(entry):
        return payload
    if entry.upstream_name == _DEVICE:
        return payload
    pair = _projection(entry.upstream_name)
    if pair is None:
        raise ValueError("invalid_response")
    return pair[0](payload, arguments)


def completeness(entry, payload) -> tuple[bool, list[str]]:
    """Compiled 9.2.0 completeness and availability.

    Returns ``(partial, bounded fixed warnings)``; never upstream text. An
    adapter row without a compiled rule reports partial (fail closed).
    """

    if not is_adapter(entry):
        return False, []
    if entry.upstream_name == _DEVICE:
        return False, []
    pair = _projection(entry.upstream_name)
    if pair is None:
        return True, ["The upstream result completeness could not be verified."]
    return pair[1](payload)
