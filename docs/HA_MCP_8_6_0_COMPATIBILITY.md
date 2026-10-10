# ha-mcp 8.6.0 compiled compatibility candidate

This beta.3 candidate adds the exact 8.6.0 provider profile to Engineering on
beta.2's October Core support. Local release metadata is materialized as
2.4.1-beta.3. This does not upgrade an installed provider, publish or sign
admission, or release a withheld operation. The advertised upstream reference
remains 8.5.0; the compiled registry's historical default remains 7.14.1.

## Exact authority

- Upstream tag `v8.6.0`, source `fc54437a804858732e4bc927add98e202d879a09`,
  tree `6b726b7501e914ad0a98e77c0fbaf8089b9ce950`.
- Standalone index
  `sha256:7426fb169440aff789e4ee942012b03317ba0b68e1d463efa19c37088dddd8bc`.
- Compiled entry `ha-mcp-v8.6.0-7426fb16`; protocol `2025-03-26`.
- With the optional component unconfigured, both pinned amd64 packaging variants
  advertise the same complete 77-tool reference catalog, fingerprint
  `47151934530bc8b51a04c611cfb8abeb92601fd9325cac78269bad04bea83ec8`.
- All standalone/add-on platform identities and complete descriptor contracts
  are bound in `upstream_release_registry.json` and the committed evidence
  under `docs/evidence/upstream-read-compatibility/ha-mcp-8.6.0*`.

The October 9 assessment captured each actual amd64 catalog twice. It verified
the 633 tracked Python package files in both images against the tagged source.
The source-to-image revision difference affected release metadata only. This
does not claim native arm64 execution, complete dependency/resource provenance,
reproducible builds or cryptographic image-attestation verification.

## Public compatibility and withheld behavior

Engineering exposes the same 25 reads. Existing descriptions, annotations and
input schemas are retained through explicit 8.6 projections, with one
owner-approved migration: `ha_get_skill_guide` now accepts `file`, defaulting to
`SKILL.md`, and returns the bundled document. The old `skill` selector and
no-argument inventory behavior remain available on the exact 8.5 provider.
Relative bundled paths, including referenced YAML files, remain reads; traversal,
absolute paths and URLs are refused. The upstream verifies bundle membership.

Scenes still require one exact `scene_id`; new scene search/list inputs cannot
dispatch. Templates remain template-only: condition, variables and strict inputs
are withheld. The existing public timeout schema is unchanged; 8.6 values outside
1–60 seconds fail before upstream I/O, without clamping or retry. Rendering uses
the prior defaults explicitly. Blueprint reads construct only list/get with a
validated installed path. Import, save, delete and substitution remain unavailable.

The 8.6 private `ha_mcp` metadata is absent. Complete descriptors bind that absence;
old releases keep their original policy-state checks. The dashboard family permits
absence only for the full exact 8.6 getter descriptor. Catalog equality does not
establish identical configured policy behavior.

The existing single-target fan and light/switch operations retain their fixed
arguments, independent state verification, durable ownership and recovery. F3
dashboard updates retain external approval, exact preread, full-config setter,
separate readback and restoration. They still require the bounded no-concurrent-
editor policy: Core's hash check and save are not one atomic operation. Historical
contracts and persisted fixtures are unchanged; old approvals do not authorize
a different provider identity.

Backup, lifecycle and operation-status capabilities remain held. Runtime controls
are not automatically delegated. The exact child-device provider binding changes
no signed Core profile or fingerprint. This work does not admit HAMCP-151 restart
or provide Core activation.

## Exact search descriptor pair

With the pinned `ha_mcp_tools` 2.2.1 component enabled, only the search descriptor
changes. The complete 77-tool catalog fingerprint is
`1374a6b5c4e84100f2404b6c3fcf3824c1ead10695d67f967cda74ec80833f71`.
One binary-owned pair binds both full descriptors, exact 8.6/source/policy/protocol
and the existing public search schema, description and annotations. It is not a
generic variant registry or authority derived from observations.

Search retains `search_types`, `include_config` and `config_time_budget` even
though the component descriptor omits them; the exact upstream callable still
accepts them. The last controls legacy per-id config fetching, not component work
or a global deadline. Engineering retains its existing 60-second deadline.
The changed policy hash produces a new automatic-readmission profile; old 8.6
policy authority cannot silently acquire this alternate contract. Signed denial
and retained revocation remain controlling. Each route pins its selected raw
descriptor. Switching variants refuses a stale search before dispatch; unchanged
sibling reads remain useful and fresh discovery may admit the new exact variant.

Pinned upstream may internally fall back from component search to legacy reads.
Unknown-command transitions can be silent; other failure paths retain warnings.
Engineering makes one provider attempt and adds no direct-HA fallback or retry.
Pagination retains `has_more`/offsets; a completely covered page can have
`completeness=complete`. Missing/malformed `partial` remains incomplete. If any
member is excluded by visibility, the pinned component withholds the entire
membership list while retaining `is_group`; it does not expose a filtered list.

## Validation state

The local implementation packet is retained at
`.artifacts/ha-mcp-8.6.0-implementation-20261009` in the parent workspace. It
contains commands, candidate identities, source comparisons and disposable
cleanup evidence; its final RESULT is the authority for completed checks.

The original 309-test focused campaign and six signed-authority checks remain
retained. The original Full run completed 4,856 tests: 4,825 passed, five failed,
two errored and 24 skipped. The failures were historical version assertions and
context accounting; the errors used an outdated local PyJWT. The metadata gate
also refused the unchanged Engineering version. These receipts are preserved.

The approved correction bundle adapts only the fixed dashboard-guide request
for the exact 8.6 handshake, preserves strict acknowledgement parsing and the
8.5 selector, and corrects offline blueprint counting and historical tests.
The correction packet `corrections-20261009T183338Z` records 274 passing focused
cases, including 19 new transport/context regressions. Four synthetic relay
cases initially encountered sandbox socket restrictions and passed with approved
local socket access. Tests used a process-local, hash-verified four-wheel overlay;
all 39 declared runtime dependency versions matched. The other 35 distributions'
installed content hashes were not reverified.

Both pinned amd64 image variants passed the disposable reference-profile lane
against Core 2026.9.4: useful reads and refusals, fan/power operations and recovery,
and two governed dashboard operations each (change and exact restoration), with
independent readback and duplicate suppression. Relay evidence recorded four
dashboard saves, zero native-component edits and zero retained WebSocket sessions.
Containers and the private network were cleaned up. This is synthetic local
acceptance, not physical or installed-household acceptance.

The component-enabled lane also passed on both exact amd64 images, with Core
2026.9.4, pinned component 2.2.1 and only the two retained public dependency
mounts. Each variant executed 14 nonempty search cases, the 12 changed-read cases,
closed blueprint checks, fan/power recovery and two governed dashboard operations.
There were four native dashboard edits, 22 service posts, zero remaining relay
sessions and settled provider/Core ownership. All four containers and the private
network were removed. The first attempt's fixture-assumption failure and cleanup
remain retained; no product behavior was weakened to accommodate the fixture.

The 28 actual search results have separate committed provenance. Controlled
partial/error/timeout/cancellation tests are offline fixtures, not actual Core
failure injection. Local focused checks cover both descriptor surfaces, signed
controls, exact negative cases, stale handles and in-flight settlement. The final
Full/Evidence and independent-review receipts belong to
`component-implementation-20261009T191834Z`; consult its RESULT for their status.
Those original receipts remain unchanged. The release preparation below resolves
the version metadata locally; native arm64, exact-head CI and installed acceptance
remain separate gates.

## October reconciliation and release validation

The release checkout begins at protected beta.2 source
`9871f4b7ad7ed0f9fba6bf49bf5f8603c84636b8`. All 37 reviewed compatibility files
were replayed byte-for-byte at `25d712dba1260e4b5f98cd7ac3ead63825df892d`.
The original branch and sealed evidence were preserved. Beta.2's October parser,
probe and source-selector bytes are retained. An additional exact binding permits
8.6.0 with Core 2026.10.0 and the unchanged October profile; it does not extend
that pair to later patches or alter signed Core authority.

The combined source baseline passed 155 tests. The corrected October-focused
campaign passed 27 tests, including both catalogs, useful reads, authority expiry,
zero-I/O refusals and unchanged profile fingerprints. The first test-fixture
sequence mistake remains recorded; anti-replay was not changed. Historical CI
regressions passed 76 tests after granting the loopback access needed by four
synthetic relay tests. The initial sandbox refusals remain preserved.

The exact Core image index is
`sha256:1b64d38f38d922bf9d59336451fd6453e1d614f934456af4ee3d2a51061be3a4`,
source `6a811d3359c7b2076dc9e1cf900843a129c044af`. Retained raw index,
platform and configuration bytes were verified for both architectures; execution
here was native amd64 only. The two local October runs are retained beneath
`.artifacts/ha-mcp-8.6.0-release-20261010T041145Z` in the parent workspace:

- `disposable-reference-20261010T043029Z`: both exact images, twice-captured
  77-tool catalogs, 25 admitted reads, 17 changed/October read cases per image,
  closed blueprint checks, three fan and four light/switch operations per image,
  typed fault/recovery checks and two governed dashboard operations per image.
  Relay settlement: 22 service posts, four legacy dashboard saves, zero retained
  WebSockets; providers, locks and Core leases/commits settled.
- `disposable-component-20261010T043216Z`: the same contracts plus 14 nonempty
  search cases per image under exact component 2.2.1. Relay settlement: 22 service
  posts, four native dashboard edits, zero legacy saves and retained WebSockets.
  Both runs restored the fixed fixtures; all four containers and each internal
  network were removed, with zero container exit codes and no OOM kill.

The first reference attempt correctly refused a device lookup for a synthetic
fan without a device. Its complete cleanup and failure receipt are retained; the
positive test now selects the existing device-backed switch by exact registry
identity. No product behavior was weakened. These runs used the candidate input
hashes captured before execution, atop the recorded Git head; the head alone does
not describe those then-uncommitted bytes. The final packet reconciles them with
the committed release. Only synthetic local data, cached exact images and two
hash-verified pure wheels were used. No new household observation occurred.

This focused lane selected 19 ephemeral test profiles and exercises the delegated
provider boundary. It is not a repeat of beta.2's all-21 native October campaign.
The unchanged native parser and capability evidence remains described in
[the beta.2 October summary](CORE_2026_10_COMPATIBILITY_EVIDENCE.md).
Neither campaign establishes native arm64, actual Supervisor lifecycle, process
crash/restart, physical feedback, later Core patches or installed 8.6 acceptance.
The final RESULT in the new packet records Full/Evidence and independent-review
status; pending checks must not be inferred from the disposable results.

## Admission, upgrade and recovery handoff

1. Use the final stable-candidate Full/Evidence and independent review receipts.
   Both search profiles and the fixed guide transport have local disposable
   acceptance; exact authority and strict BPS remain required.
2. Complete the beta.3 integration/release review and exact-head CI. The local
   workflow includes both native architectures, Core versions and profiles; each
   row runs both packaging forms. Permissions and historical rows are preserved.
   Local workflow preparation does not trigger CI or establish its results.
3. Build/publish only after separate authorization. Establish the exact installed
   Engineering and upstream artifact identities through the governed route.
   Preserve the working 8.5 artifact/configuration and the signed authority journal
   as recovery inputs. Do not assume currently installed identities from source.
4. Install compatible Engineering before considering upstream 8.6 activation.
   Prepare exact admission matching the compiled profile and intended immutable
   artifact. Signing can select that profile; it cannot implement new behavior.
5. Approve the upstream upgrade separately with its recovery target and acceptance
   window. The 8.6 lifespan can refresh installed HACS repository information in
   background, outside tool dispatch. Synthetic starts without matching HACS
   repositories do not establish zero household side effects. Assess that
   consequence explicitly; do not silently disable it or equate tool admission
   with permission for background operations.
6. Installed acceptance must check exact image/profile/Core authority, complete
   catalog, useful governed reads/actions, withheld capabilities, errors,
   verification, restoration and settled ownership. Household/physical effects
   require their own authorized acceptance. On failure, inspect uncertain outcomes
   without repeating a write; recover the exact prior upstream artifact and
   re-establish matching authority. Never rewrite journals or relabel stale plans.

Local beta.3 version materialization is part of the approved release preparation.
No push, PR, CI trigger, publication, signing, household access, installed change,
merge or deployment is included. With October already installed, beta.1 alone is
not a compatible recovery target; retain the working beta.2/8.5 pair and exact
journals/ownership. See the [beta.3 acceptance contract](V2_4_1_BETA3_ACCEPTANCE.md).
