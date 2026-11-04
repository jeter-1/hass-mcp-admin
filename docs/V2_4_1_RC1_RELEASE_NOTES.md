# Engineering 2.4.1-rc.1 release notes

Engineering 2.4.1-rc.1 (HAMCP-302) promotes the accepted 2.4.1 line to release
candidate status. Preparation base is protected `main`
`58ce6b81b2b504221e2bfcde26b43f1842e93b60`, tree
`815c5a8c962819dcfb9085d49b8027406c88c6c8`: the 2.4.1-beta.63 runtime plus
the HAMCP-303 documentation consolidation. There is **no runtime change since
beta.63**. This release changes the three Engineering version declarations,
documentation and the add-on store README only; no tool, schema, provider,
route, signed profile, persisted format, dependency or workflow changes.

Acceptance: [`V2_4_1_RC1_ACCEPTANCE.md`](V2_4_1_RC1_ACCEPTANCE.md).
Per-beta notes and acceptance contracts: [release index](RELEASES.md).

## What the 2.4.1 line delivers

Each capability below is described in full by the release records linked from
the [release index](RELEASES.md); this list does not repeat them.

- **Signed Core release registry and the `registry` branch.** Core-backed
  capabilities are admitted only for an exact Core version named by a signed
  registry entry. Since 2.4.1-beta.58 (HAMCP-278) Engineering reads the registry
  from the `registry` branch; the cutover ran at sequence 15 (Core 2026.10.3).
  See [the Core release registry](CORE_RELEASE_REGISTRY.md).
- **One-tap owner signing.** The `core-release-registry-publish` workflow
  verifies, signs after owner approval, reverifies and publishes without force
  to `registry`. It is repository tooling, not part of the image. See the
  [owner runbook](CORE_REGISTRY_ONE_TAP_RUNBOOK.md).
- **Helper value operations.** `set_input_select_option` (2.4.1-beta.59,
  HAMCP-274) and `set_input_boolean_state` (2.4.1-beta.61, HAMCP-285) decide
  relevance per target from evidence, name bounded consumers, disclose opaque
  ones, and take precise locks or the coarse automation fence. On beta.61 the
  architect's live acceptance (2026-11-03) executed approved helper writes and
  verified them by readback; see HAMCP-301 below for the planning label those
  writes still received.
- **Reference disclosure before approval.** Governed plans disclose scene,
  group and dashboard references before approval (2.4.1-beta.60, HAMCP-270),
  informational only and never part of a plan binding.
- **Dashboard scan budget.** The dependency index reads dashboards after the
  other sources, inside its own phase budget, with a bounded per-dashboard read
  (2.4.1-beta.62, HAMCP-277).
- **Record inspection and acknowledgement.** The read-only
  `inspect_governance_record` tool explains why one exact plan or task is
  stuck. An administrator can acknowledge a stuck record, or revoke that
  acknowledgement, on the Ingress **Stuck records** page only
  (2.4.1-beta.63, HAMCP-253).
- **HACS to Marketplace evidence.** Core 2026.11 replaces HACS with the
  built-in Marketplace. This project's analysis of the migration on this
  installation, its effect on installed HACS items and the `ha_get_hacs_info`
  effect is recorded in
  [HAMCP-290](evidence/hamcp290/HAMCP-290-hacs-marketplace-migration.md) and the
  [HAMCP-251 Core 2026.11 beta report](evidence/hamcp251/HAMCP-251-part-b1-core-2026.11-beta.md).
  Those findings were read from Core 2026.11 beta source (2026.11.0b4 for
  HAMCP-290, 2026.11.0b3 for HAMCP-251 Part B1) and are unverified against
  the final 2026.11.0 release until the HAMCP-251 Part B2 and HAMCP-280 work. The analysis changes no runtime behavior and selects no
  disposition.

## Same installation

Continue using **HA MCP Engineering Server Beta**, directory and slug
`hass_mcp_engineering_beta`, image repository
`ghcr.io/jeter-1/hass-mcp-engineering-beta`, and the existing single public MCP
connector. Ports, ingress, options, persistent paths and the technical Beta
identity are unchanged. No second installation, data migration or new
signature is required for this promotion.

The add-on store page (`hass_mcp_engineering_beta/README.md`) is now a short
pointer to the root README and the release index. Retired v1.1.2 under
`hass_mcp_admin/` is unchanged and is not an Engineering rollback.

## Catalog

The source baseline is unchanged from beta.63: 80 static tools plus 28
automatic delegated reads, 108 expected connector tools, counted by
`scripts/codex-context.py`. The optional `ha_config_get_yaml` reader is
admitted per exact ha-mcp version, so an installation can show 109. A fresh
client catalog establishes what a running system exposes.

## Known issues carried

Documented, not fixed in this release:

- HAMCP-301 (High): unreferenced helpers still plan as `elevated_admin` on
  Josh's installation; the HAMCP-274 low-friction path is not reached live.
- HAMCP-288 (High, security queue): the production ha-mcp 8.5.0 provider writes
  its secret-path URL to its logs; exposure is unknown.
- HAMCP-300: stored plan and task identifiers are not yet screened in every
  remaining output.
- HAMCP-304 (Low): prohibited operational plans that were never approvable are
  reported as projection failures (`approval_sequence_inconsistent`).
- HAMCP-305 (Low): the Evidence secret scanner reports a false positive on one
  ADR filename.

## Upgrade and rollback

**Upgrade** from 2.4.1-beta.63 is a version-only change: update the existing
add-on in place. Runtime code, storage, plan and task formats, the
acknowledgement ledger and options are the same.

**Rollback** to 2.4.1-beta.63 is safe: it has the same runtime and storage
contracts. Do not treat 2.4.1-beta.57 or earlier as a safe rollback. Plans
written under the beta.61 helper bindings are refused by earlier releases, and
beta.57 and earlier read the frozen `main` registry copy and learn no later
sequence or revocation. An image rollback does not restore household
configuration or physical state.

## After rc.1

Publication, deployment and installed acceptance are separate steps with
separate authorization, under the
[RC1 acceptance contract](V2_4_1_RC1_ACCEPTANCE.md). Any later code change is
a new release, not part of this version-only promotion.

Development continues as 2.4.2-beta.1; no stable 2.4.1 is planned (Josh,
2026-11-03, recorded in HAMCP-302).
