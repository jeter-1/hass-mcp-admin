"""Two exact 8.6 search descriptors; no generic variant or observation authority.

Generated bindings derive from the public captures in
ha-mcp-8.6.0-component-search.json. All runtime comparisons are binary-owned.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, replace
from typing import Any

ADAPTER = 'ha-mcp-8.6.0-search-descriptor-pair-v1'
SOURCE = 'fc54437a804858732e4bc927add98e202d879a09'
PAIR_DIGEST = 'c8f9bcb835da016fd775bed195bb97c402e8b99fed5f0f6cb46edeeee19d2422'
POLICY_SHA256 = 'sha256:f6c41752941cebb36f8dc27bc4e0a19f0a6c4749c9c2089fe4a646c8c118dd0b'
ENTRY_FINGERPRINT = '1a2c8d027adf055c0053c6903db21b2589ea265d7dd94158ad0ae25c8f6ef03b'
REFERENCE_CONTRACT_FINGERPRINT = '332c6b826e2b71152d14b81ffc7ec54caaca144d18dc917499e9c7306bdb278b'
PUBLIC_SCHEMA_FINGERPRINT = 'c6bd01c71eea48b7fbe75c8d86f762b2336ab685e99b3e16acd91bed73e3d5bf'
ALLOWED_PROTOCOLS = ('2025-03-26',)

@dataclass(frozen=True)
class SearchVariant:
    name: str
    catalog_fingerprint: str
    input_schema_fingerprint: str
    description_fingerprint: str
    runtime_contract_fingerprint: str
    runtime_fields: tuple[tuple[str, str], ...]

    def entry(self, original):
        return replace(original, input_schema_fingerprint=self.input_schema_fingerprint)

    def contract(self, original):
        return replace(original, input_schema_fingerprint=self.input_schema_fingerprint,
                       description_fingerprint=self.description_fingerprint,
                       runtime_contract_fingerprint=self.runtime_contract_fingerprint,
                       runtime_contract_field_fingerprints=self.runtime_fields)

VARIANTS = (
    SearchVariant(*('reference',
 '47151934530bc8b51a04c611cfb8abeb92601fd9325cac78269bad04bea83ec8',
 '58ed5cc91f928d9dc33437a97ad0ae46641b17891dfed2f7657f2658c83276c5',
 '595678d4544680c10cc6fead7da5224497786ac36290b75a10dd41969547643e',
 'a58dd6354865e41b162d0d91fd1584f067ae8c81d2ecdd3ed0f7ea4c24c72a3b',
 (('/_meta/fastmcp/tags', '31f40081fc2073a9a0823ed0eb7d88eea76661129c1a45ca228fd861beb6bc2f'),
  ('/_meta/ha_mcp/llm_api_exposed',
   'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/pinned', 'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/policy/deployment',
   'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/policy/enabled',
   'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/policy/live', 'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/policy/rules',
   'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/annotations/title', '69ae8316d19e36c9c7808c8047c904828afba173a4936b7877d25ab6f4528bf6'),
  ('/title', '69ae8316d19e36c9c7808c8047c904828afba173a4936b7877d25ab6f4528bf6')))),
    SearchVariant(*('component_unified',
 '1374a6b5c4e84100f2404b6c3fcf3824c1ead10695d67f967cda74ec80833f71',
 'cd5b40c5bb10fdd92f6b0a254d3cae8d6b783230349ed1c8068dbaf6c114ab55',
 '6f85b53cb197e14ebd587e1c0d560bd066b9c239380b05d2cdb40e14f6363981',
 'c60c73308bf81a67d4bb9dd2ed876184d64122ac67bf59a8f235a31024b3d970',
 (('/_meta/fastmcp/tags', '31f40081fc2073a9a0823ed0eb7d88eea76661129c1a45ca228fd861beb6bc2f'),
  ('/_meta/ha_mcp/llm_api_exposed',
   'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/pinned', 'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/policy/deployment',
   'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/policy/enabled',
   'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/policy/live', 'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/_meta/ha_mcp/policy/rules',
   'f35a0cf0ef896a2236d8419cac8a2d85bfb33d12859af555f9cf7825dc785109'),
  ('/annotations/title', '69ae8316d19e36c9c7808c8047c904828afba173a4936b7877d25ab6f4528bf6'),
  ('/title', '69ae8316d19e36c9c7808c8047c904828afba173a4936b7877d25ab6f4528bf6')))),

)


def is_search_entry(entry) -> bool:
    """Recognize only the complete compiled entry, including its selected input."""
    from ..upstream_tool_policy import schema_fingerprint
    try:
        if entry.input_schema_fingerprint not in {v.input_schema_fingerprint for v in VARIANTS}:
            return False
        original = replace(entry, input_schema_fingerprint=VARIANTS[0].input_schema_fingerprint)
        return schema_fingerprint(asdict(original)) == ENTRY_FINGERPRINT
    except (AttributeError, TypeError, ValueError):
        return False


def _trusted_release(release, entry) -> bool:
    from ..upstream_tool_policy import schema_fingerprint
    from .upstream_reads_8_6 import PUBLIC_SCHEMAS
    if release is None or not is_search_entry(entry):
        return False
    policy = release.policy
    reference = policy.by_name.get("ha_search")
    contract = release.tool_contracts_by_name.get("ha_search")
    return (
        release.server_name == "ha-mcp" and release.version == "8.6.0"
        and release.source_commit == SOURCE and not release.revoked
        and release.allowed_protocol_versions == ALLOWED_PROTOCOLS
        and release.provider_disposition("read_gateway") != "held"
        and release.policy_resource == "upstream_tool_policy_8_6_0.json"
        and release.policy_sha256 == POLICY_SHA256
        and policy.reviewed_upstream_version == "8.6.0"
        and policy.reviewed_source_commit == SOURCE
        and reference is not None and is_search_entry(reference)
        and reference.input_schema_fingerprint == VARIANTS[0].input_schema_fingerprint
        and contract is not None
        and schema_fingerprint(asdict(contract)) == REFERENCE_CONTRACT_FINGERPRINT
        and schema_fingerprint(PUBLIC_SCHEMAS["ha_search"]) == PUBLIC_SCHEMA_FINGERPRINT
    )


def resolve_search_variant(release, policy_entry, observed_tool) -> SearchVariant | None:
    """Match full canonical descriptor bytes only after exact compiled authority."""
    from ..upstream_tool_policy import schema_fingerprint
    if not _trusted_release(release, policy_entry):
        return None
    try:
        fingerprint = schema_fingerprint(observed_tool)
    except (TypeError, ValueError, RecursionError, OverflowError):
        return None
    return next((v for v in VARIANTS if v.runtime_contract_fingerprint == fingerprint), None)


def search_capability_contract(release, policy_entry) -> str | None:
    """Explicit authority for the pair, not a replacement raw observation."""
    from ..upstream_tool_policy import schema_fingerprint
    if not _trusted_release(release, policy_entry):
        return None
    return "sha256:" + schema_fingerprint({
        "model": ADAPTER, "source": SOURCE, "version": "8.6.0",
        "descriptor_pair": PAIR_DIGEST, "policy_entry": ENTRY_FINGERPRINT,
        "public_schema": PUBLIC_SCHEMA_FINGERPRINT,
        "semantics": "bounded-sanitized-search-partial-v1-no-engineering-fallback",
    })


def search_catalog_view(release, tools):
    """Ephemeral expectations; stored release and raw observations are untouched."""
    if release is None:
        return release
    entry = release.policy.by_name.get("ha_search")
    matches = [t for t in tools if isinstance(t, dict) and t.get("name") == "ha_search"]
    if len(matches) != 1:
        return release
    variant = resolve_search_variant(release, entry, matches[0])
    if variant is None or variant.name == "reference":
        return release
    policy = release.policy
    descriptions = dict(policy.reviewed_runtime_description_fingerprints)
    descriptions["ha_search"] = variant.description_fingerprint
    view_policy = replace(policy,
        tools=tuple(variant.entry(e) if e.upstream_name == "ha_search" else e for e in policy.tools),
        reviewed_runtime_description_fingerprints=tuple(sorted(descriptions.items())),
        reviewed_stock_catalog_fingerprint=variant.catalog_fingerprint)
    return replace(release, policy=view_policy, catalog_fingerprint=variant.catalog_fingerprint,
        tool_contracts=tuple((name, variant.contract(c) if name == "ha_search" else c)
                             for name, c in release.tool_contracts))
