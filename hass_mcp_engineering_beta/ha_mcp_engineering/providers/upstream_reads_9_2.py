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
and its compiled projections, which run after the closed 9.2.0 key sets of
``upstream_reads_9_2_keys`` (S10).

``ha_get_history`` (S9) is the 9.2.0 history module ``upstream_history_9_2``:
9.2.0 resolves an ISO time without an offset in HA local time, where 9.1
forced UTC (``tools_history.py`` at 51e3aac7), so such a time refuses before
dispatch, and the result has its own closed key set.
"""
from copy import deepcopy

from . import upstream_reads_9_1 as _reads_9_1
from .upstream_search_8_6 import SearchVariant as _SearchVariant

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

# Row 16 on 9.2.0: the S9 history module (time check, closed key set).
_HISTORY = "ha_get_history"
# Section 6.5: ha_get_device is projected by the compiled device allowlist
# after the composite adapter (upstream_device_projection), not here.
_DEVICE = "ha_get_device"

ReadTierUnavailable = _reads_9_1.ReadTierUnavailable


# D6 (architect rulings 2026-11-15 22:26 CST): the exact 9.2.0 ha_search pair.
# ``ComponentSearchSchemaTransform`` (src/ha_mcp/transforms/component_search.py
# at 51e3aac7) advertises a trimmed ``unified`` ha_search when the component
# reports ``search``, ``device_registry_child_semantics`` and ``search_unified``,
# else the reviewed ``legacy`` one; the callable is the same function either
# way. Every value below is generated by docs/evidence/hamcp371/sr/d6_search_pair.py
# into d6-search-pair-9.2.0.json (legacy from the reviewed capture, unified from
# the S6 profile B receipt c707f5f7) and a test asserts equality. The design is
# the 8.6 pair's (upstream_search_8_6, not edited): a call-time flip between the
# two refuses the pinned route before dispatch and quarantines it, and the next
# fresh discovery admits the other variant; any other descriptor refuses.
SEARCH_ADAPTER = "ha-mcp-9.2.0-search-descriptor-pair-v1"
SEARCH_POLICY_RESOURCE = "upstream_tool_policy_9_2_0.json"
SEARCH_ALLOWED_PROTOCOLS = ("2025-03-26",)
SEARCH_PAIR_DIGEST = '25fcab57a9cc60f0b6451b2ed316b3aaaf77da167ee69306da126f2801353838'
SEARCH_POLICY_SHA256 = 'sha256:7f5ff2beb22ed667a3171906d3d0af48e8199552117d64b19eca9d812c97ed23'
SEARCH_ENTRY_FINGERPRINT = 'b5c80944f69335fb0e9a5994a371946eae6d28b87e6d9855ad71d23d1cb23dfd'
SEARCH_REFERENCE_CONTRACT_FINGERPRINT = 'd79d7ad6578340c40ee1c8e94df7b5c1dc9d6f794f9ed880ca295130ecf63a4a'
SEARCH_PUBLIC_SCHEMA_FINGERPRINT = 'c6bd01c71eea48b7fbe75c8d86f762b2336ab685e99b3e16acd91bed73e3d5bf'
SEARCH_VARIANTS = (
    _SearchVariant(*('legacy',
 '0f1a2c75a766a111a83053733c6803ca14d60357137f4ecc7bc7978061982147',
 '7a3ac5105326cb85bf30381c88d26215ed658ac19ba7e49b65ab27b6e58e3b34',
 '2258e36ce603a00a654ed3459dd78f2bfa46c21451b3b2cac90a23395eb754a2',
 'ace3d49c125ccf0fe51270e8fce5e96083d51718aa6ff2fe00985e2087f2159f',
 '36f5a9032ebf23247df8188ff316ac11d2dffd8517d6875d0e4c2d336d97ab7c',
 (('/_meta/fastmcp/tags', '31f40081fc2073a9a0823ed0eb7d88eea76661129c1a45ca228fd861beb6bc2f'),
  ('/_meta/ha_mcp/llm_api_exposed',
   'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/pinned', 'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/policy/deployment',
   'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/policy/enabled',
   'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/policy/live',
   'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/policy/rules',
   'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/annotations/title', '69ae8316d19e36c9c7808c8047c904828afba173a4936b7877d25ab6f4528bf6'),
  ('/title', '69ae8316d19e36c9c7808c8047c904828afba173a4936b7877d25ab6f4528bf6')))),
    _SearchVariant(*('unified',
 'b8670993447f9112bd0e5e3a835e2cc9f7a6b7870efb7c803b87e622eb35a5eb',
 '2e96801755a2ddef892558788f7a4be82cf7831556448f41bac7913e1a9c26fa',
 'c1ca76bd2f762cceb1697bec9e0cee25e0d3806cc2815e62dd06f5132a19a83b',
 '462a9d45a5780cd6982054454162cecfeb309d66ef47bcb52f6b74fd617f2418',
 '215f7b9adf2ae9fb35a3c76d195c36e3aa8987f2db12ffe2c92700a410378abb',
 (('/_meta/fastmcp/tags', '31f40081fc2073a9a0823ed0eb7d88eea76661129c1a45ca228fd861beb6bc2f'),
  ('/_meta/ha_mcp/llm_api_exposed',
   'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/pinned', 'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/policy/deployment',
   'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/policy/enabled',
   'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/policy/live',
   'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/policy/rules',
   'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/annotations/title', '69ae8316d19e36c9c7808c8047c904828afba173a4936b7877d25ab6f4528bf6'),
  ('/title', '69ae8316d19e36c9c7808c8047c904828afba173a4936b7877d25ab6f4528bf6')))),
)
# R-D6-3: fixed text, only when the unified descriptor is active and the
# caller supplied config_time_budget (forwarded unchanged under both, R-D6-1).
SEARCH_BUDGET_WARNING_CODE = "search_budget_legacy_path_only"
SEARCH_BUDGET_WARNING = (
    "config_time_budget is applied only when upstream serves this search on its "
    "legacy path; with the component's unified search it may not be applied."
)


def is_search_entry(entry) -> bool:
    """Recognize only the complete compiled 9.2.0 entry, with either input."""
    from dataclasses import asdict, replace
    from ..upstream_tool_policy import schema_fingerprint
    try:
        if entry.input_schema_fingerprint not in {
                v.input_schema_fingerprint for v in SEARCH_VARIANTS}:
            return False
        original = replace(
            entry, input_schema_fingerprint=SEARCH_VARIANTS[0].input_schema_fingerprint)
        return schema_fingerprint(asdict(original)) == SEARCH_ENTRY_FINGERPRINT
    except (AttributeError, TypeError, ValueError):
        return False


def _search_trusted_release(release, entry) -> bool:
    from dataclasses import asdict
    from ..upstream_tool_policy import schema_fingerprint
    if release is None or not is_search_entry(entry):
        return False
    policy = release.policy
    reference = policy.by_name.get("ha_search")
    contract = release.tool_contracts_by_name.get("ha_search")
    return (
        release.server_name == "ha-mcp" and release.version == VERSION
        and release.source_commit == SOURCE and not release.revoked
        and release.allowed_protocol_versions == SEARCH_ALLOWED_PROTOCOLS
        and release.provider_disposition("read_gateway") != "held"
        and release.policy_resource == SEARCH_POLICY_RESOURCE
        and release.policy_sha256 == SEARCH_POLICY_SHA256
        and policy.reviewed_upstream_version == VERSION
        and policy.reviewed_source_commit == SOURCE
        and reference is not None and is_search_entry(reference)
        and reference.input_schema_fingerprint == SEARCH_VARIANTS[0].input_schema_fingerprint
        and contract is not None
        and schema_fingerprint(asdict(contract)) == SEARCH_REFERENCE_CONTRACT_FINGERPRINT
        and schema_fingerprint(PUBLIC_SCHEMAS["ha_search"]) == SEARCH_PUBLIC_SCHEMA_FINGERPRINT
    )


def resolve_search_variant(release, policy_entry, observed_tool):
    """Match full canonical descriptor bytes only after exact compiled authority."""
    from ..upstream_tool_policy import schema_fingerprint
    if not _search_trusted_release(release, policy_entry):
        return None
    try:
        fingerprint = schema_fingerprint(observed_tool)
    except (TypeError, ValueError, RecursionError, OverflowError):
        return None
    return next((v for v in SEARCH_VARIANTS if v.runtime_contract_fingerprint == fingerprint),
                None)


def search_capability_contract(release, policy_entry) -> str | None:
    """Explicit authority for the pair, not a replacement raw observation."""
    from ..upstream_tool_policy import schema_fingerprint
    if not _search_trusted_release(release, policy_entry):
        return None
    return "sha256:" + schema_fingerprint({
        "model": SEARCH_ADAPTER, "source": SOURCE, "version": VERSION,
        "descriptor_pair": SEARCH_PAIR_DIGEST, "policy_entry": SEARCH_ENTRY_FINGERPRINT,
        "public_schema": SEARCH_PUBLIC_SCHEMA_FINGERPRINT,
        "semantics": "closed-9.2.0-key-set-projection-no-engineering-fallback",
    })


def search_catalog_view(release, tools):
    """Ephemeral expectations; stored release and raw observations are untouched."""
    from dataclasses import replace
    if release is None:
        return release
    entry = release.policy.by_name.get("ha_search")
    matches = [t for t in tools if isinstance(t, dict) and t.get("name") == "ha_search"]
    if len(matches) != 1:
        return release
    variant = resolve_search_variant(release, entry, matches[0])
    if variant is None or variant.name == "legacy":
        return release
    policy = release.policy
    descriptions = dict(policy.reviewed_runtime_description_fingerprints)
    descriptions["ha_search"] = variant.description_fingerprint
    view_policy = replace(policy,
        tools=tuple(variant.entry(e) if e.upstream_name == "ha_search" else e
                    for e in policy.tools),
        reviewed_runtime_description_fingerprints=tuple(sorted(descriptions.items())),
        reviewed_stock_catalog_fingerprint=variant.catalog_fingerprint)
    return replace(release, policy=view_policy, catalog_fingerprint=variant.catalog_fingerprint,
        strict_full_contract_fingerprint=variant.strict_full_contract_fingerprint,
        tool_contracts=tuple((name, variant.contract(c) if name == "ha_search" else c)
                             for name, c in release.tool_contracts))


def search_variant_name(entry, runtime_contract_fingerprint) -> str | None:
    """The pair variant a 9.2.0 ha_search route dispatched, or None.

    Both the route's compiled input and the runtime descriptor fingerprint it
    pinned, and that the live catalog matched before dispatch, must name the
    same variant.
    """
    if names_adapter(entry) != VERSION or not is_search_entry(entry):
        return None
    return next((v.name for v in SEARCH_VARIANTS
                 if v.input_schema_fingerprint == entry.input_schema_fingerprint
                 and v.runtime_contract_fingerprint == runtime_contract_fingerprint), None)


def search_warning_codes(variant: str | None, arguments) -> tuple[str, ...]:
    """R-D6-3: the budget warning code under unified with the budget supplied."""
    if variant == "unified" and isinstance(arguments, dict) and (
            "config_time_budget" in arguments):
        return (SEARCH_BUDGET_WARNING_CODE,)
    return ()


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
        and (entry.input_schema_fingerprint == fingerprints.get(entry.upstream_name)
             # D6: the unified ha_search input, on the exact compiled entry only.
             or (entry.upstream_name == "ha_search"
                 and entry.input_schema_fingerprint
                 == SEARCH_VARIANTS[1].input_schema_fingerprint
                 and is_search_entry(entry)))
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
        # Row 16 / rule 3 plus the 9.2.0 time check (21:36 record, item 6).
        from .upstream_history_9_2 import dispatch_arguments
        return dispatch_arguments(arguments)
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
    # S10: the closed 9.2.0 key set, then the compiled 9.1 projection or the
    # 9.2.0 zone and services override. History has its own module (S9).
    if name == _HISTORY:
        from .upstream_history_9_2 import PROJECTIONS
        return PROJECTIONS[_HISTORY]
    from . import upstream_reads_9_2_entities as _entities, upstream_reads_9_2_keys as _keys
    if name not in _keys.ROWS:
        return None
    inner, rule = _entities.PROJECTIONS.get(name) or _reads_9_1._projector_tables()[name]
    return _keys.projection(name, inner), _keys.completeness(rule, _keys.TEXTS)


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
