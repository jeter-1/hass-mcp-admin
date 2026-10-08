# Engineering 2.4.0-rc.1 release notes

Engineering 2.4.0-rc.1 promotes the accepted beta.16 implementation to release
candidate status. Preparation base is protected beta.16 source
`fce97c66848796d68d989f12efcf53fa43ddf32d`, tree
`d63280b0d0730b559b3f7cc9fa1f2f9deefe14f1`. This release changes version metadata,
documentation and one dependency. Engineering runtime logic is unchanged; no
provider or household operation is added.

## Dependency security correction

The RC dependency audit refused the inherited multidict 6.8.0 pin for
[CVE-2026-104874](https://github.com/aio-libs/multidict/security/advisories/GHSA-54p9-h82j-f925).
RC1 updates it to the maintainer's fixed 6.9.1 release in both hash locks and the
declared build inventory. The defect leaks references during particular C-extension
items-view set operations. No exploitation or installed impact is established by
that audit result.

All other package versions, wheel hashes and base-image inputs remain unchanged.
The full 6.9.1 dependency release is included, not a locally cherry-picked patch;
its upstream fixes and additive API changes require compatibility validation.
The original metadata-only RC review does not attest this corrected payload.
Require fresh locked-input/audit checks, Engineering tests, architecture packaging,
catalog comparison and focused independent delta review before Ready.

## Preserved 2.4 capability

The healthy catalog remains **83 tools: 58 Engineering-native and 25 admitted
delegated reads**, with exact internal ha-mcp 8.5.0. The 21 compiled Core profiles,
their fingerprints and signed applicability remain unchanged. The retained
beta.16 installed acceptance used Core 2026.9.4 with registry sequence 5; those
are dated observations, not a new version pin or an instruction to change trust.

The release retains bounded lifecycle and dependency analysis, Alarmo inspection,
automation-baseline capture and offline comparison, dashboard integrity analysis,
logbook processing, configuration re-verification, F3 readiness/reconciliation and
the exact garage configuration eligibility introduced during the 2.4 beta series.
Public descriptors, routing, approval meaning, one-dispatch ownership, persisted
formats, workflows and zero-fallback behavior are preserved. Controlled inputs
change only for the multidict correction above. Existing partial coverage and
uncertainty remain visible.

The garage forward path still requires proof of the exact minimal transformation
or full five-object caller-owned retry composition. All 11 positive guards in the
full composition, its response protocol, waits and modes remain unchanged.
Configuration eligibility is not permission to apply a household change.

## Same installation and separate trust

Continue using **HA MCP Engineering Server Beta**, directory/slug
`hass_mcp_engineering_beta`, image repository
`ghcr.io/jeter-1/hass-mcp-engineering-beta`, and the existing single public MCP
connector. Ports, ingress, options, persistent paths and technical Beta identity
are unchanged. No second installation or data migration is required.

Historical v1.1.2 under `hass_mcp_admin/` remains frozen and operationally retired;
it is not the target for Engineering GA or an Engineering recovery image.
The Engineering RC/GA version does not rename or replace that package.

No new signature or capability activation is required solely for this promotion.
Core applicability must still be current, valid and applicable at runtime.
Unknown pairings remain subject to their existing review/admission rules.
ha-mcp 8.6.0 preparation is separate and no upstream upgrade is included.

## Evidence and qualifications

The October 4 beta.16 closure reconciled source/release and installed read-only
acceptance as PASS, including image binding and all 83 raw public descriptors.
Its source campaign ran 4,725 tests: 4,701 passed and 24 skipped. The retained
garage Core evidence contains 296 executed checks / 289 distinct cases.
These are attributed beta.16 results, not claimed RC executions or proof of the
new dependency's compatibility. RC source/CI, publication and installed results
must be recorded against the actual RC source
and image under the [RC1 acceptance contract](V2_4_0_RC1_ACCEPTANCE.md).

Carry earlier acceptance qualifications and evidence provenance forward. The
beta.16 native/delegated/history continuity closure includes a supplied smoke
report; it does not manufacture missing raw responses. Historical F027 recovery
evidence limitations, diagnostic/coverage limits and any unexplained historical
latency remain disclosed. This promotion adds no latency SLA or proof that all
audit findings are remediated.

Garage **D2 remains open**. The original script inverse remains prohibited and
caller-only restoration is partial. The independently developed governed-rollback
candidate and its pending lock correction are not included. Source locks do not
make multi-object saves atomic against external editors or household runs;
readback does not establish completed reloads or physical success. Govee package
administration and HAMCP-151 add-on restart work remain separate unreleased work.

## RC to GA

The intended progression is `2.4.0-beta.16 -> 2.4.0-rc.1 -> 2.4.0` after the RC
gates pass. Preserve the accepted RC functional payload for GA; new implementation
changes require their own review and appropriate release sequencing. Do not fold
unfinished streams into a version-only promotion or describe GA as closure of D2.
Merge/publication, deployment and household actions retain their distinct controls.
