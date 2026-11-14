# Core compatibility updates without rebuilding Engineering

This completes the Core portion of ADR-020's existing direction. The 2.2 feature
scope ends here; remaining work is review, release preparation and acceptance.
Historical RC receipts remain evidence for their exact source/Core pairing.
This document is an implementation and operator contract, not release authority.

## What changes for a later Core release

A compatible Core release can be admitted by reviewed, signed data while the
Engineering executable stays unchanged. A reviewer must establish applicability
of existing contracts to that exact release. New semantics, adapters or action
families still require implementation and a new Engineering release. Version
prefixes, successful probes and a patch number never create authority.

The runtime has three independent inputs:

1. Compiled capability and probe profiles define the only executable contracts.
2. Compiled exact authority or a verified Core registry record selects those
   contracts for an exact release. A signed denial takes precedence over either.
3. Two fresh authenticated Core observations must meet the selected contracts.

All three remain necessary. Registry data cannot add a URL, tool, provider,
argument or code. Core authority is independent of ha-mcp read admission and
dashboard attestation. The child-device probe profile still requires the exact
corrected ha-mcp 8.4.3 device adapter at catalog publication and final acquisition.
Signed references to unknown capabilities/profiles or mismatched fingerprints
do not enable them; a mismatch affects only capabilities whose prerequisites
fail. An unknown probe profile supplies no new-release authority.

Governed helper planning also consumes this exact semantic applicability.
The consumed dependency-build authority issues in-memory evidence binding the
observed Core version, selected compiled probe and template/dependency contracts
to the actual compiled semantic registry. Each snapshot carries that evidence;
publication, cached reuse and helper admission check its original Core generation
and current signed selection. It grants no transport or mutation authority and
does not keep the completed build's lease/commit alive. Retirement, expiry,
revocation, a mismatched observed version or changed compiled semantics refuses
reuse; a replacement scan must acquire its own current authority.

For an uncompiled release, helper approval material includes the exact version
and stable semantic contract identity. Operational generations and source epochs
remain separate, so an identical post-lock refresh can preserve a valid approval.
Compiled-version approval material remains compatible. Source fences, evidence
freshness, external approval, current target state and final Core dispatch checks
remain necessary. No additional version list, version substitution or caller
admission flag can grant semantic applicability. RC8's capability-count success
did not establish this consumer path; its failed disposable helper evidence is
preserved, and RC9 requires the complete pinned runtime lane to pass.

The observation interface authenticates Core's reported version and contract
responses; it does not expose the running Core OCI digest. Recorded release
source/image provenance is therefore separate from installed-image evidence.
Do not describe version agreement as a cryptographic running-image proof.

## Ordinary fan and power applicability

Decision (2026-09-19): Josh directed restoring signed applicability for these
existing typed operations instead of adding another compiled Core version pair.
This supersedes the future-release pinning approach, while preserving exact
legacy contracts and the separation between implementation and release authority.

The executable additionally defines `core.typed_fan_operation` and
`core.typed_power_operation`. An exact signed release may explicitly select these
compiled semantic profiles through the existing `capabilities` reference schema.
The original 17 references and their fingerprints are unchanged. Read compatibility,
a version prefix, or successful structural probes cannot select either operation.
Registry preparation recognizes the new references but does not insert them into
an existing entry automatically. Existing entry-renewal immutability still applies.

The Core runtime inventories 19 profiles. A journal containing only the original
17 references leaves the two additional profiles unavailable; it does not acquire
new write grants. Historical Core 2026.9.2 fan/power contracts retain their separately
compiled applicability and original receipt format. This compatibility exception
must not become a list of future releases. Existing compiled Core release entries
also do not acquire these new profiles automatically.

For registry-selected operations, the prepared hash binds the observed exact Core
version and compiled semantic fingerprint, independently of the exact reviewed
ha-mcp provider contract. Preflight, dispatch acquisition and readback require that
same version and current signed applicability. Lease/commit generation fences,
expiry, revocation, target locks and at-most-once dispatch remain in force. A Core
change cannot relabel an existing operation or authorize its redispatch. Recovery
is read-only under the original binding and existing deadlines; unavailable or
changed authority retains uncertainty and follows the existing hold/manual-review
behavior. A settled historical receipt remains retrievable without live authority.

Only new registry-selected declarations use the closed `ordinary-fan-v1-core-bound-v2`
or `ordinary-power-v1-core-bound-v2` record model. They add `provider_contract` and
`core_binding` to the existing fields; the binding participates in the prepared
hash. Old declarations are read without rewriting their bytes, hashes or contracts.
Their public receipts remain unchanged. New receipts additionally expose the
bound version and semantic fingerprint in `core_binding`. Tool input schemas,
registration, provider arguments and routes do not change.

Before deploying this storage reader/writer, preserve an application-consistent
backup and settle active work. Beta.3 cannot read new-model declarations: a binary
downgrade alone is not a storage rollback. Do not delete receipts or restore an old
execution store after a possibly dispatched action to regain availability; that
could lose dispatch identities. Keep the compatible reader for reconciliation, or
use a separately reviewed restoration plan accounting for all later operations.

`tests/test_typed_core_continuity.py` exercises ephemeral signed entries and real
Engineering authority/provider/execution code with synthetic I/O. This proves the
data-driven mechanism, not compatibility of a real Core release. Release admission
still requires exact upstream review, disposable integration validation, separate
registry review/signing/publication and authorized installed acceptance. No new
production registry entry, release version or deployment is supplied by this change.

## Runtime lifecycle

Core reuses the existing bounded signed-journal verification, durable cache,
checkpoint, replay/rollback refusal, expiry and retained-revocation lifecycle.
It has a distinct identity (`ha-core-reviewed-releases`), key ID
(`ha-core-release-registry-v1`), entry schema and cache. The code-owned URL is:

`https://raw.githubusercontent.com/jeter-1/hass-mcp-admin/registry/upstream-trust/ha-core-release-registry.json`

HAMCP-278 changed only the branch segment, from `main` to `registry`. The
path, verifier, key and cache are unchanged, and there is still no URL option
or override. See [One-tap signing and registry-branch publication](#one-tap-signing-and-registry-branch-publication-hamcp-278).

The cache is `/data/ha-core-release-registry-cache.json`; its companion lifecycle
files are owned by the existing registry implementation. Do not edit/delete them
to clear a refusal. Their format remains the shared existing journal-cache
format; Core entries cannot be interpreted as ha-mcp entries.

Refresh follows the existing bounded registry cadence and a throttled exact
missing-release lookup during reconciliation. An observed new Core version
retires preceding authority before waiting for external data. A successful
missing-release lookup permits one additional bounded collection. Registry
changes across collection refuse mixed evidence. Network failure cannot create
new positive authority; unexpired previously verified data can remain usable.
An unaffected compiled pairing remains usable when the registry is unavailable.
Malformed cache or an incomplete persistence transaction may deny the whole
Core surface to prevent reactivation of possibly revoked authority.

Inherited transport bounds are 5 seconds to connect, 15 seconds overall and
32 MiB for a journal/cache. Periodic refresh is six hours; the missing-release
lookup is throttled to 60 seconds and is driven by Core reconciliation. Runtime
chains are bounded to 64 envelopes and eight retained denial-source envelopes;
the preparation utility compacts its positive chain to 32 envelopes.

Acquisition, consumption and each dispatch revalidation synchronously check
whether the selected authority has expired, changed or been revoked. They only
withdraw authority; reconciliation alone can publish a replacement. Leases,
target binding and one-use commit rules stay intact. Dependency evidence is
invalidated on material authority change and rebuilt through its normal owned
build scope. F3 retains separate pre-dispatch reconciliation, external approval,
durable intent and readback rules. New authority does not rescue a stale plan.

Registry refresh is polling, so remote revocation becomes effective when a
verified refresh is received; local expiry is checked at use. Core health adds
the bounded `release_registry` summary, including sequence, current status and
failure category. Public output does not contain keys, URLs or raw probe data.
Catalog consumers must re-list or reconnect after a material change; this work
does not add MCP `tools.listChanged` support.

## One-time owner activation

Defaults are `ha_core_release_registry_enabled: false` and
`ha_core_release_registry_public_key: ""`. Existing installations retain their
compiled behavior. No production key or registry is shipped by this change.

After independent source review, release publication and deployment, the owner
must provision an independent Ed25519 Core signing identity in the existing
protected signing environment. The matching base64 **public** key goes in
Engineering's Core registry option, then the owner enables the registry through
the existing add-on configuration surface. The private key remains exclusively
in the signing owner. This is a separately authorized live configuration action,
including any add-on restart required to load options. Do not reuse the ha-mcp
private key, put private values in chat, or delegate credential-store access.

Owner setup must verify the public-key fingerprint, initial journal publication,
fixed URL, cache persistence and currently installed pairing before planning a
Core upgrade. Until that happens, this source feature is not an active household
update facility. Disabling the registry removes signed positive authority and
also disables its denial source; it is an owner policy change, not routine
recovery from a denial. Trust-anchor rotation is likewise a separate owner
operation; no downloaded record can rotate its own key.

## Reviewable data update

Use Codex to prepare the artifacts and review; the protected signing owner
performs the signing step. The commands below define the supported utility
interface, not a requirement that Josh use a manual terminal.

For each exact stable Core release, preserve:

- Official tag, source commit/tree and hash of the immutable source archive.
- OCI index and amd64/arm64 manifests, with source/provenance binding and
  limitations of attestation verification stated.
- Relevant upstream source changes and the comparison with an existing probe
  profile; include integration-generated registry data risks.
- Exact disposable Core/ha-mcp contract results, negative controls, transition,
  dependency/F3 resource fences and independent review of applicability.
- The unchanged Engineering source/tree/image under test. A same-process
  synthetic proof is not an installed-image or live update receipt.

Prepare a bounded safe review-evidence file and SHA256 it. Populate a
`CoreReleaseEntry` JSON using that hash, actual provenance, one known probe
profile ID/fingerprint and the exact reviewed subset of compiled capability
references. `registry_models.py` defines the closed format; `probe_profiles.py`
and `profiles.py` provide its compiled references. Do not infer approval or
invent provenance from a successful runtime response. The `.2` disposable
fixture is test provenance, not a production entry or a signed approval.

Run the preparation utility with the current **verified** journal and public key:

```sh
python scripts/prepare_core_release_registry.py add \
  --entry core-entry.json --evidence core-review-evidence.json \
  --public-key core-public-key.txt --previous current-core-journal.json \
  --output core-candidate.json
```

Omit `--previous` only for the owner's first bootstrap. Preparation verifies the
existing journal, known contracts and evidence hash, then produces unsigned
review material. It does not fetch, sign, publish, contact HA or change the repo.
An identical release can renew its 90-day authority after review with updated
evidence; renewal cannot silently change source, image or contracts. A revoked
release cannot be re-added. All output paths must be new.

Independently review the exact candidate bytes and record their SHA256. In the
protected signing environment only, the owner supplies
`HA_CORE_RELEASE_REGISTRY_SIGNING_KEY` using its existing secret facility and runs:

```sh
python scripts/prepare_core_release_registry.py sign \
  --candidate core-candidate.json --expected-candidate-sha256 REVIEWED_SHA256 \
  --previous current-core-journal.json --output signed-core-journal.json
```

The utility checks the reviewed hash, Core public-key fingerprint, exact prior
journal, successor chain, closed contracts and current expiry before output.
It compacts to 32 positive-chain envelopes while retaining signed denial sources
within existing bounds. Bound exhaustion refuses and requires an explicit owner
lifecycle decision. It never overwrites prior evidence or publishes automatically.

Before the `registry` cutover, a separate authorized data-only PR placed the
signed output at `upstream-trust/ha-core-release-registry.json` on `main` with
safe review evidence. After the cutover the signed output is published to the
`registry` branch instead, through the one-tap workflow below, or through the
[local fallback](#local-fallback-publication) that reuses the same guarded
publication script. Either way,
review the exact signature/chain/content against the installed public key. No
Engineering version bump, new image or runtime code change is needed for a
compatible profile selection.

HAMCP-278 adds one signing workflow,
`.github/workflows/core-release-registry-publish.yml`, and one GitHub permission
use: its publish job holds the built-in Actions token with `contents: write`
and pushes to `registry` only. The signing job holds Josh's Core key through a
protected environment secret and has only `contents: read`. The signing owner
remains a required independent trust boundary: nothing signs until Josh
approves that environment, and automation must not turn probe success into
self-approval.

### Explicit capability extension

`add` remains an identical-contract renewal or new-release operation. To append
reviewed capabilities to an **existing current non-revoked release**, use
`extend-capabilities` with the same CLI entry/evidence/previous/public-key/output
arguments. This creates `core-registry-extension-candidate-v1` review material;
the runtime envelope and signed journal schemas are unchanged. Old add/revoke
candidate shapes remain supported. Production signing/publication are separate
owner decisions; CI uses ephemeral test keys only.

The evidence file is exact canonical JSON (sorted keys, compact separators, no
trailing newline), with precisely:

- `model`: `core-capability-extension-evidence-v1`;
- `version`: the existing release;
- `previous_entry_sha256`: SHA-256 of canonical complete predecessor entry;
- `previous_capabilities`: the exact ordered original reference list;
- `added_capabilities`: one or more new compiled, eligible references;
- `review_artifacts_sha256`: 1–32 distinct SHA-256 hashes of reviewed evidence.

The candidate entry's `evidence_sha256` hashes those exact bytes. The previous
references remain an unchanged ordered prefix; duplicates, removals, unknown
contracts, changed source/image/architecture/probe identity, siblings or denial
history refuse. Both preparation and signing independently validate the full
transition and embedded evidence. Relabeling an extension as add or revoke does
not bypass these checks. Signing also binds the exact reviewed candidate, key,
current journal bytes, sequence and predecessor; stale or expired predecessors
refuse extension. Existing identical renewal remains the separate expiry-recovery
operation. Do not roll a published journal backwards.

Review must account for the changed entry fingerprint and any outstanding
plans/leases before activation. This operation is additive; it introduces no
capability-removal or production recovery authorization. Withdraw/recovery
scope must be decided separately rather than inferred from a successful test.

For withdrawal use `revoke` with the exact entry, hashed review evidence and
`--reason`, then the same review/sign/publish process. This also permits a
reviewed denial of a compiled pairing. Preserve tombstones across renewal and
compaction. Do not restore an old journal or delete a cache as rollback.

Withdrawal does not require the writer to understand a newer positive profile.
The denial-only path may remove positives and retain exact unchanged siblings;
it cannot add or alter a positive entry. This allows an older writer to withdraw
newer-profile authority without an Engineering code change.

## One-tap signing and registry-branch publication (HAMCP-278)

### Josh's decisions (2026-11-02)

Josh, as product owner, decided the following on 2026-11-02:

- **Split.** HAMCP-278 (278a) covers the runtime URL change to the `registry`
  branch, gated one-tap signing, a separate publish job, these docs and the
  owner cutover runbook. Review evidence and the candidate remain inputs.
  Automated evidence (O3 binding inheritance, release-parameterized lanes,
  lane-group withholding, dormant seq-13 lanes) moves to HAMCP-279.
- **Push credential.** The publish job uses the built-in Actions token with
  `contents: write`, in a job separate from signing. There is no deploy key and
  no new credential.
- **Branch ruleset.** The `registry` ruleset, which Josh configures, blocks
  force-push and deletion only.
- **Unchanged.** Key custody stays option A: an environment secret with Josh as
  the required reviewer. Publication stays P2: the `registry` branch at the same
  path, with only the URL branch segment changed. CI signing without Josh's
  approval stays rejected.

### What changed in the trust boundary

Before HAMCP-278, Josh's Core key never left his machine, and every journal
reached the runtime through a reviewed pull request merged to `main`. Now:

- **The key lives in GitHub.** The base64 raw seed is stored as the
  `HA_CORE_RELEASE_REGISTRY_SIGNING_KEY` secret of the
  `core-release-registry-signing` environment. Only the `sign` job of the
  workflow can use it, and only after Josh approves that run. Anyone who can
  change that workflow on `main`, and get Josh to approve a run, can have it
  sign. That is why the environment allows `main` only, and why workflow
  changes stay protected-path changes reviewed on `main`.
- **Publication no longer goes through a pull request.** The publish job pushes
  one commit straight to `registry` with the Actions token. Review moves to
  before dispatch: the candidate and evidence SHA-256 values Josh compares in
  the approval summary. The runtime still trusts only signatures from the
  committed Core key, so a push to `registry` without that signature cannot
  create authority.
- **The `registry` branch is now canonical.** The ruleset blocks force-push and
  deletion, so the published history only moves forward.

Signing and publishing stay in separate jobs. The signing job holds the secret
and no write token. The publish job holds the write token and no secret, and
runs no Python or package installer: besides the pinned checkout and
artifact-download actions it uses only `bash`, `git`, `sha256sum` and `gh` as
the git credential helper. A read-only `reverify` job sits between
them: it checks the signed journal against the committed public key with the
runtime verifier before anything is pushed.

### Canonical source, frozen `main` copy and rollback

After cutover, `registry` is the only canonical Core journal. The copy at
`upstream-trust/ha-core-release-registry.json` on `main` is **frozen** at the
cutover sequence: do not update it and do not delete it. It exists so that
`registry` can be created byte-identical to it, and for history.

A rollback to an Engineering version older than HAMCP-278 reads the frozen
`main` copy. It keeps its cached authority until that authority expires, but it
learns no sequence published after the cutover, including revocations. Treat
such a rollback as temporary, and roll forward before cached entries expire.

**No Home Assistant Core upgrade until the cutover is complete**, and after it,
none until the exact target release is on `registry`: a release present only on
`main` does not count. Observing a new Core version retires the prior authority,
and the verified cache does not authorize a new version. The ordered gate is in
the [runbook](CORE_REGISTRY_ONE_TAP_RUNBOOK.md#0-stop-no-home-assistant-core-upgrade-before-the-gate).

### Manual flow for each publication

1. Prepare and review the candidate with `add` (or `extend-capabilities` or
   `revoke`) against the current `registry` journal, as described above.
2. Push `core-candidate.json` and `core-review-evidence.json` (regular files,
   at the branch root) to a new branch `registry-candidates/<version>`.
   For an extension of an already published version, use the new permanent
   branch `registry-candidates/<version>-seq<sequence>` instead (for example,
   `registry-candidates/2026.11.0-seq17`). Never reuse, force-push or delete the
   original branch. An explicit task-specific byte-review gate must be completed
   before this push; the seq-17 contract requires approval of both SHA-256 values.
3. Review those exact bytes and record both SHA-256 values.
4. Dispatch **Core registry one-tap signing and publication** from `main` with
   `core_version`, `candidate_ref`, `candidate_sha256` and `evidence_sha256`.
   Run once with `dry_run: true` first.
5. The `verify` job (no environment, no secret, read-only) checks both digests,
   refuses before cutover when `registry` does not exist, runs the utility's
   non-signing checks and writes the **approval summary**. The summary shows the
   predecessor and new sequence, both SHA-256 values, expiry, the profiles
   carried and added, and a prominent **WITHHELD** block for any profile in the
   predecessor's entry that the candidate drops. It ends with:
   *Approving signs this exact candidate with Josh's Core registry key; a
   separate job then pushes it to the registry branch.*
6. Josh compares the summary's candidate and evidence SHA-256 values with the
   reviewed values. Only if they match does he approve the
   `core-release-registry-signing` environment.
7. `sign` re-checks the digests, signs with `--expected-candidate-sha256`, and
   checks the result against the committed public key with the runtime
   verifier. `reverify` repeats that check in a job without the secret.
   `publish` checks that `registry` still holds the exact predecessor `verify`
   saw, makes one commit touching only the journal and the new evidence file,
   and pushes it to `registry` without force.

A dry run replaces `sign` with an ephemeral test key. It never references the
real secret, never asks for approval and pushes nothing. Its publish job builds
and checks the same commit and stops.

The publish job reports one of three outcomes, in its log and job summary:

- **PUBLISHED** (exit 0): a remote read shows the new commit on `registry`. If
  the push client reported a failure but the read shows the new commit, it is
  still PUBLISHED, with a warning. Do not publish it again.
- **UNCHANGED** (exit 1): a refusal before the push (predecessor drift, path
  set, digest or verification), or a failed push where one remote read shows
  the verified predecessor. Only this outcome leaves `registry` unchanged. Fix
  the cause and dispatch a new run. If `registry` moved after `verify`, the
  publish job refuses; prepare a new candidate against the new tip.
- **UNCERTAIN** (exit 3): the push was dispatched and its result is not
  confirmed, because the remote read failed or showed another commit. The
  summary names the attempted commit and any observed tip. Inspect `registry`
  (`scripts/check_core_registry_branch.sh last-publish`) before any further
  publication; do not dispatch again until you know which commit it holds.

The script pushes at most once and never retries.

### Local fallback publication

If the workflow cannot run, Josh may sign locally and publish with the same
guarded script. `main` stays frozen and untouched. Run the steps in one
terminal, from a checkout of `main`, with the repository's locked Python
environment:

```bash
# fallback step 0: set these first
V=<Core version>                                   # e.g. 2026.10.4
CANDIDATE=<path>/core-candidate.json               # the reviewed bytes
EVIDENCE=<path>/core-review-evidence.json
REVIEWED_SHA256=<reviewed candidate SHA-256>
PY=<locked-env>/bin/python
REMOTE=https://github.com/jeter-1/hass-mcp-admin.git
FB="$(mktemp -d)"
```

```bash
# fallback step 1: fresh predecessor from the current registry tip
git init --quiet --bare -- "$FB/fetch"
git -C "$FB/fetch" fetch --quiet --no-tags --depth=1 -- "$REMOTE" +refs/heads/registry:refs/heads/registry
git -C "$FB/fetch" cat-file blob refs/heads/registry:upstream-trust/ha-core-release-registry.json > "$FB/predecessor.json"
```

```bash
# fallback step 2: sign that exact successor, then verify with the runtime verifier
# (HA_CORE_RELEASE_REGISTRY_SIGNING_KEY supplied by its existing secret facility)
"$PY" scripts/prepare_core_release_registry.py sign \
  --candidate "$CANDIDATE" --expected-candidate-sha256 "$REVIEWED_SHA256" \
  --previous "$FB/predecessor.json" --output "$FB/signed-core-journal.json"
"$PY" scripts/core_registry_one_tap.py verify-signed \
  --journal "$FB/signed-core-journal.json" --predecessor "$FB/predecessor.json" \
  --candidate "$CANDIDATE" --public-key docs/evidence/hamcp222/core-public-key.txt
```

```bash
# fallback step 3: stage exactly the journal and evidence, then the guarded publication
EV_SHA="$(sha256sum -- "$EVIDENCE" | cut -d ' ' -f 1)"
J=upstream-trust/ha-core-release-registry.json
E="upstream-trust/evidence/ha-core-${V}-${EV_SHA}.json"
install -D -m 0644 -- "$FB/signed-core-journal.json" "$FB/signed/files/$J"
install -D -m 0644 -- "$EVIDENCE" "$FB/signed/files/$E"
(cd "$FB/signed/files" && sha256sum -- "$E" "$J") > "$FB/signed/SHA256SUMS"
M="$(sha256sum -- "$FB/signed/SHA256SUMS" | cut -d ' ' -f 1)"
SIGNED_DIR="$FB/signed" MANIFEST_SHA256="$M" REVERIFIED_MANIFEST_SHA256="$M" \
  PREDECESSOR_SHA256="$(sha256sum -- "$FB/predecessor.json" | cut -d ' ' -f 1)" \
  CORE_VERSION="$V" EVIDENCE_SHA256="$EV_SHA" REMOTE_URL="$REMOTE" WORK_DIR="$FB/publish" \
  bash scripts/core_registry_publish.sh publish
```

Step 3 fetches the `registry` tip again and refuses unless it still holds the
exact predecessor of step 1, so a concurrent publication is never overwritten.
It makes one child commit changing only the journal and adding the evidence
file, pushes it once without force to `registry`, and reports PUBLISHED,
UNCHANGED or UNCERTAIN as above. On a refusal or UNCHANGED, start again from
step 1; on UNCERTAIN, inspect `registry` first. Then run step 10 of the runbook.

Until HAMCP-279, each new Core release still needs its binding PR on `main`
before its candidate can be prepared. HAMCP-278 does not change the HAMCP-182
signing tools, sequence 16 or its manual flow. The owner setup and cutover steps
are in [`CORE_REGISTRY_ONE_TAP_RUNBOOK.md`](CORE_REGISTRY_ONE_TAP_RUNBOOK.md).

> Superseded 2026-11-03 (HAMCP-303): the cutover used sequence 15 (Core
> 2026.10.3). Sequence 16 (Core 2026.11.0) will be the first publication on
> `registry` (HAMCP-280). The text above is preserved as written for HAMCP-278.

## Validation and release completion

Offline tests cover signed new-release admission, unknown/unsigned refusal,
expired and revoked leases/commits, exact device pairing, useful delegated
reads, target binding, dependency build/invalidation, failed cache persistence,
cross-registry refusal, source/registry races and signing/review fences.
Shared ha-mcp tests remain regression controls for the extracted parser binding.

The prepared disposable CI .2 lane uses its exact immutable Core image and
ha-mcp 8.4.3. It first withholds .2, then supplies an ephemeral signed record to
the same Core runtime instance and executes the existing actual-device contract.
It exercises source under the CI interpreter, not an installed Engineering OCI
image, and does not issue production authority. CI and later exact-image/live
acceptance must be recorded separately; no passing run is implied by this doc.

Before completing the release: close release metadata gates, independently
review the final candidate, run exact-head CI and image validation, publish and
deploy with authorization, activate the owner facility, and test the unchanged
installed Engineering image across the chosen Core update. Verify 17 Core
capabilities, 25 admitted delegated reads, the fresh complete public catalog,
five device comparisons, dependency freshness, approved canaries and settled
resources. The Core-only update still needs compatibility review, selected
backup/recovery evidence and separate owner authorization. This feature changes
how compatibility authority is delivered; it does not authorize the update.

## October device-registry wire profile

Core `2026.10.0` at `6a811d3359c7b2076dc9e1cf900843a129c044af`
adds `next_name_part` to both regular and child device records. The earlier
child-device profile deliberately rejects this extra field. Signing that release
with the earlier probe profile therefore cannot establish device-registry,
delegated effective-area, or dependency/helper-planning evidence.

The code-owned `core-probes-child-device-name-parts-v1` probe selects a separate
closed schema and the existing ha-mcp `8.5.0` adapter. Regular devices require
`next_name_part` to be `area` when an explicit area exists and null otherwise.
Child devices require `area` with an explicit area and `parent_device` otherwise.
The field is checked as naming metadata; it never supplies an effective area or
changes the existing entity → device → parent inheritance rules. Missing fields,
unreviewed extra fields, inconsistent naming metadata, invalid references,
cycles, duplicate identities and existing collection bounds still fail closed.

Existing probe IDs, fingerprints and September schema defaults remain unchanged.
The new probe fingerprint additionally binds its device wire schema. The existing
21 capability contracts, public tool descriptors, transport commands, signed
journal schema and production journal are unchanged. No compiled October release
authority is added. The exact observed version must independently select this
profile through reviewed signed applicability; a version prefix, field presence,
successful probe, or caller flag cannot select it. Signing remains contingent on
complete exact-release compatibility evidence, not this schema correction alone.

The default device validator and its entity/delegated projection helpers continue
to use the September shape unless an internal caller supplies the selected schema.
Runtime probe collection obtains that selection from the existing authenticated
registry path. `tests/test_core_2026_10_device_registry.py` covers the new and old
shapes, malformed/refusal cases, effective-area projections, unchanged historical
fingerprints and ephemeral signed selection/expiry/revocation. Its synthetic
21-capability result proves the selection mechanism, not assembled October Core.

The committed wire fixture is generated from the exact source archive by
`scripts/generate_core_2026_10_device_fixture.py`. Its provenance binds the archive,
member hashes, verbatim getter locations/hashes and output digest. It executes
only named pure getters on synthetic objects, not a full Core installation.
Regeneration requires the pinned archive and a new output directory; compare both
JSON files byte-for-byte with the committed fixture. No household records are used.

Before production applicability: independently review the correction and execute
the exact October disposable lane, keeping ha-mcp `8.5.0` fixed. This must include
schema/auth/error controls, parent/child registry semantics, configuration and
trace reads, template/script continuation semantics, governed verification and
uncertain outcomes, typed operations, Alarmo inspection and native baseline
capture/continuation. The completed exact native-amd64 campaign and independent
receipt review are bound in
[October compatibility evidence](CORE_2026_10_COMPATIBILITY_EVIDENCE.md).
That local evidence establishes the named 21 contracts within its disclosed
synthetic bounds; installed custom-integration compatibility, production signing,
release publication and upgrade permission remain separate. An older
September-only disposable receipt cannot be relabeled as October acceptance.

## Automated release evidence (HAMCP-279)

Josh's 2026-11-02 **O3** decision, amended by Josh on **2026-11-05** (HAMCP-315),
compares one union manifest across Core source layouts. A path **absent in both
releases compares equal**; present on exactly one side is a **difference requiring
a source judgment**, just like modified content. Files present in both must be
**byte-identical**. Semantic compatibility is insufficient. A changed or one-sided
missing file, moved tag, unreviewed predecessor binding or unknown profile produces no
candidate and reports **binding PR required**. Main bindings remain deliberate
review decisions. The existing preparation utility and journal format are unchanged.

The accompanying **existence guard** requires every path in
`ci/core-contract-read-files.json` to exist in **at least one bound Core release**,
as witnessed by the committed per-release source-hash records under
`docs/evidence/hamcp315/core-sources/`. Record versions, commits and archive hashes
are checked against committed reviewed release bindings/provenance. CI fails and
names each unwitnessed path, so a typo cannot disappear through two absences.
`manifest()` runs this validation in evidence preparation and proof verification;
the existing complete CI test discovery also exercises the committed manifest and
the typo failure case. No workflow permission or signing boundary changes.

`core-release-evidence.yml` detects a single new stable release on protected main
(daily or manual). It excludes prereleases, already published releases and prior
pending/successful attempts; a retained candidate branch prevents redispatch after
artifact expiry. Production runs serialize and each lane job also has a release
concurrency group. Failed or ambiguous publication requires inspection of the
original run; automatic force pushes and blind dispatch retries are absent.

The source gate compares full SHA-256 hashes from commit-pinned Core archives,
checks archive/tag identity, and records every path, both hashes and commit SHAs
in `core-review-evidence.json`. OCI index, amd64/arm64 manifests, configurations,
attestation subjects, source revision, builder and base-image bindings follow the
existing HAMCP-222 provenance method. The disposable execution remains amd64;
arm64 provenance verification does not claim arm64 execution.

The exact list lives in `ci/core-contract-read-files.json`; profiles map to lane
groups in `ci/core-evidence-groups.json`. Its 535 paths expand the historical
HAMCP-222 relied-on scopes and the later helper/options, input-select, trace,
dashboard, scene, Marketplace (HAMCP-290), Thread/Matter topology (HAMCP-231),
security metadata (HAMCP-134) and device configuration entity (HAMCP-297) source
reviews. Maintain this list whenever a contract,
source read, or binding judgment changes. The literal-path AST guard detects
constant `homeassistant/.../*.py` paths in contract scripts and the device-fixture
generator. It cannot establish computed paths, transitive semantic dependencies,
source locations embedded in generated code, or completeness of human judgments.
Review must close those gaps; the guard is not authority to narrow the list.

Measured on 2026-11-02, 14 immutable stable tags form 11 adjacent patch pairs
(including .0→.1): August **0/3**, September **0/4**, October **2/4**, total
**2/11 (18.2%)**. Passing pairs: 2026.10.2→10.3 and 10.3→10.4. That historical fixed
list contained nine files absent in August and eight absent in September; the
then-current rule refused missing files even when absent at both tags. Period-available scopes would yield
4/11, a sensitivity measurement only. Excluding .0→.1 yields 2/8. Full hashes and
results are retained in `docs/evidence/hamcp279/`. These are historical measurements
of the original 337-path inventory and the pre-amendment absence rule, not the
current union-manifest result. Re-evaluating that original inventory under the
2026-11-05 rule yields 4/11; this is not a measurement of the expanded 456 paths.
A proposed removal of the
uncalled `config/auth_provider_homeassistant.py` endpoint yields the same 2/11;
**the narrower 336-file proposal is not adopted** and requires architect review.

2026.10.1→10.3 is a real negative: `auth/__init__.py`,
`components/cloud/alexa_config.py`, `components/config/auth_provider_homeassistant.py`
and `helpers/config_validation.py` differ. The historical review accepted these
semantically; strict O3 refuses. In particular, configuration validation accepts
additional multi-state `for` conditions. The signed .1 set is 26 profiles; the
signed .3 set is 31. Inheritance never turns 26 into 31.

Disposable lanes accept either exact main bindings or a same-run, same-attempt,
same-revision O3 proof. A derived binding preserves device-registry semantics,
typed-operation judgment and paired ha-mcp version. Its release, image digests
and source provenance alone change. An inherited predecessor not on main must
supply the exact prior review-evidence bytes authenticated by its signed entry.
Unknown or missing evidence refuses; no provider or runtime admission is added.

Withholding is per group: the 19 general-contract profiles stand or fall together;
availability and exposure share one group; the other ten profiles each have one
group. The previously dormant seq-13 groups are enabled only in this workflow.
Historical disposable jobs took 8m29s/.1 and 7m47s/.3 within the 50-minute bound;
these timings do not prove a new run passed. All fixtures are synthetic, owned
and cleaned up. Delete acceptance retains the elevated-path-only limitation.
The candidate contains exactly the passing subset of the signed predecessor's
references, preserving fingerprints and order; passing lanes cannot add profiles.
A lane group that fails records `passed: false` and prints `WITHHELD`; the lane
step still exits 0 by design so that every other group runs (HAMCP-309 confirmed
this is intended, not a defect). The candidate builder selects only groups whose
`passed` is exactly `true`, treats a missing receipt as withheld, refuses an
empty subset, and in acceptance refuses anything short of all 31 references.

Off GitHub Actions (HAMCP-309), the same committed harness runs only with an
explicit `CORE_EVIDENCE_HOST_MODE=local`, which is logged; on Actions that
variable is unset and behaviour is unchanged. `scripts/core_evidence_host.py`
replaces the two `sudo` steps: on Actions it executes exactly the former
`sudo install -m 600` / `sudo rm -rf` argv, and locally it uses the pinned
fixture writer image with no network, mounting only the lane's owned
disposable directory. Local mode never fetches that image: it must already be
present by its pinned digest (otherwise the helper refuses, naming it) and
every `docker run` uses `--pull never`. It verifies the staged file's SHA-256
and mode and refuses on any removal residue. Locally, `github_retry.py
download` may fetch the public `codeload.github.com` archive without a token,
but only with a pinned `--sha256` (the workflow now also passes the Alarmo pin on Actions).
The disposable image binding accepts the configuration digest, or the pinned
index digest only with a local chain proof: hash-exact local amd64 manifest
and configuration blobs, the index link proven from the verifier's
pinned-hash remote index bytes, linux/amd64, and unchanged container identity.

PR acceptance is artifact-only: real .1→.3 refusal, identical-source **fixture**
inheritance plus disposable lanes, and a separate real .3 run using main's binding.
The fixture substitutes only its predecessor source observation; it makes no
claim that a real .2 tag has those bytes. Both positive runs must reproduce all
31 actual signed .3 references through the unchanged `add` utility (a renewal
for this already-listed release). Synthetic CI keys inside existing lanes test
signature verification; no production key, signing environment or publication is
used by acceptance.

After cutover, a separate job with only `contents: write` and `actions: write`
creates `registry-candidates/<version>` by a normal non-force push, containing
only `core-candidate.json` and `core-review-evidence.json`. An existing branch
with different content refuses. It dispatches only
`core-release-registry-publish.yml` on `main`, with this run's computed candidate
and evidence hashes and `dry_run=false`. That existing workflow still requires
Josh's protected-environment approval before signing. HAMCP-279 does not change
it. Before `registry` exists, or for manual dry-run/PR acceptance, artifacts are
the only output: no candidate branch and no dispatch. The signed entry's evidence
hash binds the O3 proof and withholding summary Josh reviews.

Disable **Automated Core release evidence** in Actions to stop detection. Manual
binding PRs and the existing signing runbook remain available. Disabling this
workflow does not cancel an already-dispatched signing request; inspect and
cancel that separate run before any owner approval if necessary. Never delete
`registry-candidates/*` branches, including in cleanup sweeps; compare retained
bytes and the original run for recovery. No deployment or Core upgrade is authorized by candidate preparation.

Release discovery (HAMCP-309). A dispatch with `core_version` reads only that
exact release tag (`releases/tags/<version>`) and never lists releases; a missing
tag, a different returned tag, a prerelease or a draft refuses. Scheduled
discovery lists Core releases newest first, 100 per page, and stops after the page
that holds the signed predecessor's own release record: the official stable release
whose tag equals the newest signed version. A version comparison never ends the
scan, because a lower backport can be listed before a higher release. Every record
read must have a `created_at` timestamp. Across every page read, the timestamps of
official stable releases (not a draft, not a prerelease, an exact stable tag), taken
in listing order, must not increase. Drafts, prereleases and other tags are
excluded from that comparison only: selection never chooses them and none of them
can be the predecessor's record that ends the scan, so their positions change
neither the pages read nor the version selected. GitHub's listing does not keep
prereleases in `created_at` order (2026.4.0b10 is listed after 2026.4.0b9 though
created later), and comparing them refused every scheduled run after HAMCP-309
Part 1 (HAMCP-309 Part 3). `published_at` is used only when `created_at` is absent
(missing or null); a present `created_at` that is not a valid timestamp refuses and
never falls back. One parser reads both fields and accepts only an ISO 8601 string
with a UTC offset, so a missing, unparseable or timezone-less timestamp on any record
read, or an increasing official stable timestamp, refuses. The bound is 10 pages, so it limits
releases created after the signed predecessor rather than Core's whole history;
reaching it without the predecessor's record refuses. A listing that ends before
the bound without that record is complete and is used as before. Selection is
unchanged.

Residual assumption: a higher stable release created before the signed
predecessor's record is listed after it and is not read. That happens only if the
signed predecessor was itself a later backport. Josh reviews the selected version
at the dry run, and a `core_version` dispatch reads any exact release directly.

Selected-release job names retain idempotency evidence after attempt artifacts expire;
a pending or successful retained run suppresses another attempt. An O3 refusal
emits a separate durable `core-release-refused-<version>` failed job. That run
fails once with "binding PR required", listing every differing or missing file
and both source commits. Subsequent schedules skip that terminal refusal without
another failure. A newer release remains eligible; an explicit workflow_dispatch
with that exact `core_version` may retry the refusal. API, tag SHA and provenance
errors are ordinary failures and remain retryable. PR negative acceptance checks
the refusal without scheduling this production notification job.

`registry-candidates/*` branches are permanent provenance records. Never delete
them, including after publication or in cleanup sweeps, and never force-push them.
`bound_entry` recovers an inherited predecessor's binding from that branch's
`core-review-evidence.json`, authenticated by the signed entry's `evidence_sha256`.
Deleting a branch removes this load-bearing evidence and forces the next release
to a binding PR. Signed registry publication does not copy or replace these bytes.
Retain the GitHub attempt/refusal run records too: deleted history cannot be
reconstructed. Josh must configure the owner-setup ruleset in the runbook to block
candidate-branch deletion and force-push while allowing workflow branch creation.
