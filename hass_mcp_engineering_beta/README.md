# HA MCP Engineering Server Beta

**Engineering 2.3.0-rc.2** restores bounded reads of expired helper and rejected
automation history before stable 2.3.0, without reviving approval or execution
authority. Typed fan/light/switch operations, signed Core applicability,
Host/Origin enforcement, secret-path
authentication and the technical Beta installation identity remain unchanged.
See [2.3.0-rc.2 acceptance](../docs/V2_3_0_RC2_ACCEPTANCE.md) and
[2.3.0-rc.2 release notes](../docs/V2_3_0_RC2_RELEASE_NOTES.md).

Read the [Host/Origin policy](../docs/INBOUND_SECURITY.md) before an authorized
2.3.0-rc.2 update. Configure exact aliases for MCP paths beyond its loopback-authority
defaults; explicit host lists replace those defaults. Native clients may omit
Origin; browser MCP clients require an explicit origin. Approval ingress retains
its separate authentication. Invalid options refuse startup, so retain independent
management access. Source version declarations do not establish publication or
installed acceptance.

## Retained installation identity

| Surface | Value |
| --- | --- |
| Add-on name | HA MCP Engineering Server Beta |
| Directory and slug | `hass_mcp_engineering_beta` |
| Image repository | `ghcr.io/jeter-1/hass-mcp-engineering-beta` |
| Architectures for 2.3.0-rc.2 | `amd64`, `aarch64` |
| MCP port | `8100/tcp` |
| Admin-only approval ingress | Internal port `8110`, panel HA MCP Approval |
| Server ID | `hass-mcp-engineering-beta` |

Published 2.2.0 retains its three-platform images. 2.2.1 retired
32-bit `armv7`, following [Home Assistant OS support](https://github.com/home-assistant/operating-system/releases/tag/17.0).
An affected installation needs an independently planned migration to supported
64-bit hardware/OS; changing this package does not migrate its data.

The controlled build introduced in 2.2.1 uses a digest-pinned Python base and
complete hash-locked runtime wheels. See [build inputs](../docs/BUILD_INPUTS.md) for maintenance, both
architecture smoke checks and final-digest inventory verification.

Update the same add-on after authorized publication/deployment; preserve
settings, connector, data and ingress. New installations select this add-on
using the [root installation guidance](../README.md#install-or-update-engineering).
No slug rename, second installation, connection change or persistent-data move
belongs to this milestone.

Historical v1.1.2 in `hass_mcp_admin/` is frozen and operationally retired,
outside the Engineering dependency audit and not an Engineering rollback.
2.3.0-rc.2 preserves existing options, persistence formats, ports, ingress and image
repository. The existing options, `mcp_allowed_hosts` and `mcp_allowed_origins`,
enforce the received-header boundary before ordinary MCP behavior. The private
observer retains its [existing contract](../docs/INBOUND_TOPOLOGY_CAPTURE.md),
disabled by default with separate authorization and source-bound arming. The observer grants no
provider authority and installation does not arm it.

## Unified catalog and exact compatibility

Clients use the existing Engineering Nabu Casa connector. Engineering selects
suitable admitted ha-mcp or native providers, enforcing provider admission,
target binding, authority, dispatch, attribution and verification internally.

The healthy reviewed **ha-mcp 8.4.3 or 8.5.0** pairing exposes **53 static plus
25 delegated tools, 78 total**. The Core inventory represents **19 profiles**,
whose admission depends on exact current Core authority.
`ha_get_operation_status` stays held. Fresh raw descriptor comparison is
required catalog evidence; a cached inventory or count alone is insufficient.
Provider unavailability grants no fallback, arbitrary forwarding or alternate
provider retry.

The RC installed-acceptance target is **Core 2026.9.3 / ha-mcp 8.5.0**, with
reviewed signed applicability for all 19 profiles. Core 2026.9.2 retains its
immutable 17-reference entry and historical typed fan/power route; the two signed
typed references are intentionally unavailable on that entry. Installation does
not activate trust or update Core. Existing compiled pairings retain their behavior.

The [8.5.0 compatibility contract](../docs/HA_MCP_8_5_0_COMPATIBILITY.md)
preserves the public blueprint getter through a closed list/get adapter.
Metadata-only results remain partial; installed configuration and source-download
provenance stay distinct. Generic blueprint writes remain unavailable.
Historical acceptance does not establish the new installed pairing.

The [Core registry contract](../docs/CORE_RELEASE_REGISTRY.md) preserves
separate Core trust, signatures, exact compiled capability/probe references,
expiry, retained denials and authority retirement. Unknown or changed
contracts need review and possibly code changes. Future releases are not
automatically compatible.

## Ordinary fan control

The additive `control_fan` tool uses ordinary authenticated connector authority
for one exact `turn_on`, `turn_off` or `set_percentage` request. The assistant
manages the operation ID automatically and reuses it for reconciliation. No
configuration plan or panel approval is created. Generic services and fallback
remain unavailable. Operations require exact Core authority and admitted ha-mcp
8.4.3 or 8.5.0. Core 2026.9.3 uses the reviewed signed typed applicability;
Core 2026.9.2 retains its historical route. A generic read grant cannot authorize
typed actions, and other Core versions need separately reviewed applicability.

At most one mutation may dispatch per operation. Verify authoritative task state
and exact HA state/percentage, retaining uncertainty and incomplete consumer
coverage. Lost acknowledgements and recovery never grant redispatch. Unresolved
outcomes hold only the affected target after active dependency locks settle;
receipts and bounded audit retain provider and original-request attribution.
There is no automatic restoration, independent physical feedback or hold override.
See [typed fan control](../docs/TYPED_FAN_CONTROL.md) for the full contract.

## Ordinary light and switch control

`control_power` adds exact light/switch ON/OFF with assistant-managed operation IDs
and ordinary authenticated connector authority. It creates no configuration plan,
notification or panel approval. Exact Core authority and admitted ha-mcp 8.4.3
or 8.5.0 are required, including the signed Core .3 and historical .2 routes above.
Generic services, toggle, brightness/color, bulk selectors
and other domains remain closed.

At-most-once dispatch, independent HA state verification, target-local uncertainty
holds and bounded audit reuse the existing execution machinery. Power records
use a separate namespace without changing historical fan receipts. Consequence
coverage remains incomplete: a switch may power a critical load, and a light
may restore integration defaults. HA state is not independent physical feedback.
See [typed power control](../docs/TYPED_POWER_CONTROL.md) for reconciliation,
separate restoration and rollback limitations.

## Execution, evidence and recovery

Governed execution requires current exact targets, fresh evidence, an immutable
plan, its authenticated panel approval and current execution authority.
`approve_change_plan` requests approval; each approved plan still needs one
apply. Inspect authoritative task/child evidence and independently verify the
target. Keep incomplete consumer coverage visible when disclosing consequences.

Bounded JSON responses retain reconciliation identities and available facts.
Use supported detail selections and pagination for omitted evidence. Attempt
count alone is not dispatch proof. Reconcile uncertain execution before further
mutation; recovery cannot redispatch after durable intent or override an active
owner. Never reuse failed approval material or overwrite unexpected state.

Dashboard saves remain non-atomic against outside editors. A canary needs an
edit-free window, exact readbacks and a separately approved inverse plan.

Shared dependency builds own their authority independently of callers.
The cooperative build deadline stays **300 seconds**, with **600-second soft**
and **3600-second hard** evidence TTLs. Source fences and semantic applicability
remain enforced. Cancellation and deadlines do not guarantee remote abort.
Dependency coverage remains partial: reliable script, scene, group, template and
dashboard configuration coverage is unavailable; dynamic references remain opaque.

Beta.3 cannot read beta.4's new Core-bound fan/power records. Preserve current
execution IDs, receipts, holds and uncertain work. Binary downgrade alone is not
storage recovery; a compatible artifact and exact recovery plan are required.

- [Governance](../docs/CHANGE_GOVERNANCE.md) and
  [external approval](../docs/EXTERNAL_APPROVAL.md)
- [Execution recovery](../docs/GOVERNED_EXECUTION_RECOVERY.md)
- [Response bounds](../docs/TOKEN_EFFICIENCY.md)
- [Integrity analysis](../docs/CONFIGURATION_INTEGRITY_ANALYSIS.md)
- [Change impact](../docs/CHANGE_IMPACT_ANALYSIS.md)
- [Audit](../docs/AUDIT_LOG.md) and [rate limiting](../docs/RATE_LIMITING.md)

## Development and release validation

Read applicable root/subtree instructions and
[docs/CODEX_WORKFLOW.md](../docs/CODEX_WORKFLOW.md). Use isolated development
environments and repository-declared dependencies. Source work does not
authorize live endpoints or production credentials.

```sh
python scripts/codex-context.py --format json
python -m unittest discover -s tests -v
```

Full/Evidence includes full discovery, metadata and exact scope checks.
A passing Evidence full-suite run need not be duplicated. Candidate CI also
runs architecture builds, supported disposable Core pairings, exact-image
gateway checks and exact add-on runtime acceptance.

Materialize versions in the same PR with the shipped promotion tool. Keep the
candidate draft for review. A version-changing main merge can automatically
publish on the retained image path; owner authorization must cover that
consequence. Deployment and verification of the final installation are separate.
See the [current roadmap](../docs/2_1_ROADMAP.md#current-direction) for the
RC-to-stable gates and subsequent feature priorities.

## Acceptance and history

Retained RC9, 2.2.0 and beta.3/beta.4 source/build, installed-image, catalog and
live receipts remain bound to their original source, pairing, time and attribution.
The beta.3 raw-catalog runtime-bracket qualification remains unchanged. They do
not establish 2.3.0-rc.2 installed acceptance. The
[2.3.0-rc.2 contract](../docs/V2_3_0_RC2_ACCEPTANCE.md) separately
requires final publication/image identity, fresh catalog and bounded installed
verification.

Complete Android navigation, cache-only startup during an outage and independent
backup-content verification were not established. Notification clearing is an
operator observation with narrower coverage than approval navigation.

[2.2.0 notes](../docs/V2_2_0_RELEASE_NOTES.md),
[2.2.0 acceptance](../docs/V2_2_0_ACCEPTANCE.md),
[RC9 notes](../docs/V2_2_0_RC9_RELEASE_NOTES.md),
[RC9 acceptance](../docs/V2_2_0_RC9_ACCEPTANCE.md),
[architecture history](../V2_BETA_ARCHITECTURE.md) and
[historical beta deployment guidance](../docs/BETA_DEPLOYMENT.md) preserve their
original scope. Historical rollback versions cannot replace a recovery plan
covering compatible Core, configuration/database state and usable backups.
