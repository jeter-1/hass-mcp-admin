# HA MCP Engineering Server Beta

**Engineering 2.2.2** adds private, dormant topology observation on this existing
installation path to support Host/Origin hardening. The technical Beta name is
retained deliberately.
See [2.2.2 acceptance](../docs/V2_2_2_ACCEPTANCE.md) and
[2.2.2 release notes](../docs/V2_2_2_RELEASE_NOTES.md).

## Retained installation identity

| Surface | Value |
| --- | --- |
| Add-on name | HA MCP Engineering Server Beta |
| Directory and slug | `hass_mcp_engineering_beta` |
| Image repository | `ghcr.io/jeter-1/hass-mcp-engineering-beta` |
| Architectures for 2.2.2 | `amd64`, `aarch64` |
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
2.2.2 preserves ordinary Engineering behavior, configuration schema/defaults,
existing persistence formats, ports, ingress and image repository. The new
private diagnostic arm, consumed claim and report follow the
[observer contract](../docs/INBOUND_TOPOLOGY_CAPTURE.md). Observation is disabled
by default and requires separate authorization and source-bound arming. It
adds no public tool or endpoint and does not close issue #62.

## Unified catalog and exact compatibility

Clients use the existing Engineering Nabu Casa connector. Engineering selects
suitable admitted ha-mcp or native providers, enforcing provider admission,
target binding, authority, dispatch, attribution and verification internally.

The healthy reviewed **ha-mcp 8.4.3** pairing requires **17 Core capabilities**
and exposes **51 static plus 25 delegated tools, 76 total**.
`ha_get_operation_status` stays held. Fresh raw descriptor comparison is
required catalog evidence; a cached inventory or count alone is insufficient.
Provider unavailability grants no fallback, arbitrary forwarding or alternate
provider retry.

Accepted RC9 evidence includes **Core 2026.9.2 / ha-mcp 8.4.3** under
separately configured, valid signed Core authority. Installing 2.2.2 does not
activate trust or admit an uncompiled Core version. Existing compiled pairings
retain their behavior, and no newer ha-mcp version is adopted.

The [Core registry contract](../docs/CORE_RELEASE_REGISTRY.md) preserves
separate Core trust, signatures, exact compiled capability/probe references,
expiry, retained denials and authority retirement. Unknown or changed
contracts need review and possibly code changes. Future releases are not
automatically compatible.

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

## Acceptance and history

Retained RC9 and 2.2.0 source/build, installed-image, catalog and live receipts
remain bound to their original source, pairing and observation time. They do
not establish 2.2.2 installed acceptance. The
[2.2.2 contract](../docs/V2_2_2_ACCEPTANCE.md) separately
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
