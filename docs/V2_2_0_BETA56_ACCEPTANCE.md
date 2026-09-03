# Engineering 2.2.0-beta.56 acceptance

Beta 56 is the materialized source candidate for exact ha-mcp 8.4.1 compatibility,
based on protected main
`27d687f15a0337924b216d7009e87320d887db6b`. The advertised Engineering
release is 2.2.0-beta.56. Stable remains 1.1.2.

This document is the exact materialized release acceptance authority. Merge,
publication, deployment, production registry signing or trust
activation, Nabu Casa operation, live Home Assistant access, and canary work
remain outside this source boundary.

## Exact upstream authority

The candidate binds official `homeassistant-ai/ha-mcp` v8.4.1 tag object
`030d1437462b2cdf24b274d1463510dea6c472e1`, source commit
`701a7c26ac0e2309c7883a627d31873ab1510077`, source tree
`1f782b05b51919b86d4fc72fd46c65bf5b77f349`, and OCI revision
`10cd3d1207f8270ae6e35c0c40d7fc6dc411e9e3`.

The controlling standalone image index is
`sha256:7823b36587a6e62efed271b26f3f72380b49f47364e5385580584e7ab2c60722`.
Its amd64 and arm64 manifests are respectively
`sha256:5b1641a073ba3ab0696e41402e85b621d23b53912bc36849220eec1f2b25db13`
and
`sha256:d25d6defb4f87e9ce5c3cd62f166e3f34cb7a9e6a2dc04db4ceb3de124e9a965`.
The exact Home Assistant App indexes and manifests are independently bound in
the reviewed release entry and artifact evidence.

The published image advertises 78 tools with raw catalog fingerprint
`4303ead3f32c46658530a422ae37eec0d34d3f2e494a2122a7011593a568bf59`.
The source-checkout-only fingerprint is not release authority. The only
admitted protocol remains `2025-03-26`.

## Read admission

The exact compiled 8.4.1 profile independently admits 25 reviewed automatic
reads. Twenty-one retain their complete 8.2.0 descriptors. The changed
`ha_config_list_helpers`, `ha_get_overview`, `ha_get_skill_guide`, and
`ha_search` descriptors are admitted only under the exact 8.4.1 binary-owned
profile after individual input, description, annotation, output, runtime,
argument, bounded-response, and no-write review.

With this exact profile, the client catalog is 51 static tools (25 canonical
and 26 Engineering-native) plus 25 delegated reads, for 76 total.
`ha_get_operation_status` remains held and
unregistered. `ha_get_app`, `ha_manage_app`, `ha_call_service`, every mixed,
action, write, destructive, prohibited, unsupported, unknown, and generic
forwarding capability remains unreachable. Fallback remains zero.

Malformed, duplicate, incomplete, oversized, or canonically reordered 8.4.1
catalogs fail closed. Unknown valid additions stay unreachable without
disabling independently matching reviewed reads. Identity, version, protocol,
source, artifact, revision, catalog, session, generation, lease, expiry,
revocation, and pre-dispatch drift remain mandatory authority boundaries.

## Capability-scoped error contracts

Beta 55 compared one aggregate error-contract fingerprint and therefore could
withhold all delegated reads when only one reviewed probe changed. Beta 56
keeps the aggregate fingerprint as evidence but binds each reviewed error probe
to the capabilities whose dispatch contract it constrains:

- `invalid_search` binds `ha_search`;
- `missing_state` binds `ha_get_state`;
- `missing_automation` binds `ha_config_get_automation`; and
- `missing_registry_entity` binds `ha_get_entity`.

The exact 8.4.1 aggregate is
`03000635a7b0a506c12a6f99ce86433a09683693a0e61d4265b1f11ec52b2d46`.
Its changed validation-envelope shape is accepted only for `ha_search` under
the compiled 8.4.1 error adapter. Structured codes alone are insufficient:
bounded response shape, suggestions, and compiled normalization remain
contract evidence. Missing, malformed, conflicting, unknown, or unbound error
evidence withholds the affected capabilities and grants no fallback.

## Dashboard and other provider surfaces

Dashboard authority is explicitly quarantined for 8.4.1. Its getter and setter
descriptors changed, and Beta 56 does not bundle the separate provider-identity
defect or infer write authority from the read profile. Backup and lifecycle
provider surfaces are likewise held. Their isolation does not suppress exact
delegated reads.

The `ha_get_device` delegated-read descriptor is admitted, but the distinct
Engineering-owned Home Assistant 2026.8 composite-device response adapter is
not applied to 8.4.1. Its prior exact evidence ends at ha-mcp 8.2.0; Beta 56
therefore returns the exact upstream 8.4.1 read result without the older
response transformation.

The upstream add-on-to-App terminology and tool transition grants no authority
to newly named app-management capabilities. Beta 56 adds no public tool,
schema, provider route, write path, fallback, list-change notification, or
stable-v1 behavior.

## Required evidence

Acceptance requires the baseline Beta 55 falsification, exact generated 8.4.1
catalog and error evidence, capability-scoped partial-admission tests, all four
changed-read decisions, held and prohibited negative reachability, same-session
final validation, single-use lease refusal, signed-registry lifecycle tests,
historical 8.0.0 through 8.2.0 preservation, and explicit dashboard quarantine.

It also requires complete unit discovery; Fast, protected Full, and clean-head
Evidence; promotion-candidate validation without applying promotion; exact
8.4.1 standalone and App image lanes; existing exact-image and disposable Home
Assistant lanes; packaging and architecture builds; compilation and structured
file validation; dependency and strict vulnerability audits; secret and
whitespace checks; stable-v1 comparison; and exact tool-count, task-schema,
approval-authority, route, and fallback checks. Sandbox-denied or unavailable
network/container checks are not passes and must run in the authorized CI path.

The source PR must remain draft, unmerged, unpublished, and undeployed pending
manual independent exact-head review and separate owner authorization.
