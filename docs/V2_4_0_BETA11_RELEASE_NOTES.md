# Engineering 2.4.0-beta.11 release notes

Beta.11 prepares the bounded native Alarmo configuration inspector on beta.10
base `bf50a45518c764d499c6681c018a145818aad095`. This document describes the
release candidate; it does not establish publication, deployment or activation.

`get_integration_inspection` reads configured membership, modes, per-area delays
and selected options for one exact Alarmo panel. It exposes positively selected,
sanitized evidence with explicit unknown/partial results and page-local references.
Configured eligibility is not proof of arming, readiness or actual protection.
The adapter reviews Alarmo 1.10.19 at immutable source
`169e134f4b70d87aae36ba54a72a398ecc960afd`; installed source assurance remains a
separate acceptance gate. See [behavior and bounds](INTEGRATION_INSPECTION.md).

The healthy catalog becomes **81 tools: 56 static plus 25 delegated reads**.
Existing 80 descriptors and all 19 prior Core capability fingerprints remain
unchanged. One additional semantic Core read profile is compiled; existing
signed 19-reference entries withhold the new inspector. Installing beta.11 does
not activate that profile or update Core. Runtime admission uses reviewed signed
applicability, not an additional fixed Core version pin.

The registry preparer/signing validator adds the reviewed `extend-capabilities`
operation for a strict capability superset bound to its exact predecessor,
preserving sibling entries and denial history. Production preparation, review,
owner signing and activation are separate decisions. No production journal or
trust key changes in this release. A required disposable Core 2026.9.4 / ha-mcp
8.5.0 / Alarmo 1.10.19 lane proves withheld dispatch, same-instance 19-to-20
admission, useful paginated reads, independent interval observation and cleanup.
Its pinned pairing is a test fixture, not blanket installed compatibility.

PyJWT changes from 2.14.0 to 2.15.0 in both hash locks and build-input inventory
for [GHSA-42vr-xj54-vc7v](https://github.com/advisories/GHSA-42vr-xj54-vc7v).
The confirmed defect is an unhandled recursive payload-parser exception on
pre-verification paths; this release does not claim a demonstrated Engineering
exploit or process crash. Patched behavior and normal signed-token verification
have explicit offline regressions. All other package versions, Python/base
image, installer policy and supported architectures remain unchanged.

No Alarmo arm/disarm, options-flow, service, store write, generic integration
forwarding, fallback, household configuration or new execution authority is
exposed. Existing verification, lock recovery, lifecycle analysis, health and
logbook corrections remain. Stable-v1, installed options, ports and storage
formats are unchanged. Removing the inspector requires a reviewed source revert;
returning to beta.10 also reintroduces the affected dependency. After authority
activation, withdrawal/recovery requires its separately reviewed procedure;
never roll back the signed journal or restore stale execution records.

Follow [beta.11 acceptance](V2_4_0_BETA11_ACCEPTANCE.md). Keep completed prior
acceptance, the F027 historical exception, read/audit correlation limitations
and independent household holds intact. No prior device or dashboard cycle is
required for this read-only increment.
