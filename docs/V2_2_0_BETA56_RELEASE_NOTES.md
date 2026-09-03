# Engineering 2.2.0-beta.56 release notes

Beta 56 is materialized for exact ha-mcp 8.4.1 compatibility. The advertised
Engineering release is 2.2.0-beta.56, and stable remains 1.1.2. This source
candidate does not merge,
publish, deploy, access live Home Assistant, operate Nabu Casa, or activate a
production registry.

## Exact 8.4.1 delegated reads

Engineering now has a binary-owned profile for the exact official 8.4.1 image.
It binds the annotated source tag, commit and tree, standalone and Home
Assistant App OCI identities, image revision, exact 78-tool catalog, four error
probes, and reviewed per-tool contracts.

All 21 delegated reads whose complete descriptors remain identical to 8.2.0
return. Four changed reads—`ha_config_list_helpers`, `ha_get_overview`,
`ha_get_skill_guide`, and `ha_search`—were reviewed independently and are
admitted only by the exact 8.4.1 profile. The resulting catalog remains 51
static tools (25 canonical and 26 Engineering-native) plus 25 delegated reads,
or 76 total.

`ha_get_operation_status` remains held. New App management tools, generic
service forwarding, and every mixed, action, write, destructive, unknown, or
unreviewed capability remain unreachable. Fallback remains zero.

## Per-capability error compatibility

Error compatibility is no longer an all-or-nothing release gate. The changed
8.4.1 search validation envelope affects `ha_search` only; unchanged missing
state, automation, and registry-entity envelopes independently retain authority
for their bound reads. Matching still requires exact binary-known bounded
response shapes and semantics, not only structured error codes.

Missing, malformed, conflicting, or unknown error evidence withholds the
affected capability. It cannot authorize an adapter, route, action, write, or
fallback.

## Preserved boundaries

Exact 8.0.0, 8.1.0, 8.1.1, and 8.2.0 profiles remain unchanged. Exact 8.4.1
catalog order, descriptors, identity, protocol, release artifacts, session,
generation, and single-use lease are revalidated before dispatch. Duplicate or
stale lease commit remains impossible.

Dashboard, backup, and lifecycle provider authority is explicitly quarantined
for 8.4.1. The dashboard provider-identity defect is not bundled. There is no
public-schema, provider-write, workflow-permission, deployment, container,
stable-v1, or fallback expansion.

The delegated `ha_get_device` read remains admitted, while the separate
Engineering-owned Home Assistant 2026.8 composite-device response adapter is
not extended beyond its exact ha-mcp 8.1.0–8.2.0 evidence. Beta 56 does not
infer 8.4.1 response-transform authority from descriptor compatibility alone.
