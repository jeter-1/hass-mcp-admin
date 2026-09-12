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

## Runtime lifecycle

Core reuses the existing bounded signed-journal verification, durable cache,
checkpoint, replay/rollback refusal, expiry and retained-revocation lifecycle.
It has a distinct identity (`ha-core-reviewed-releases`), key ID
(`ha-core-release-registry-v1`), entry schema and cache. The code-owned URL is:

`https://raw.githubusercontent.com/jeter-1/hass-mcp-admin/main/upstream-trust/ha-core-release-registry.json`

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

A separate authorized data-only PR can place the signed output at
`upstream-trust/ha-core-release-registry.json` with safe review evidence. Review
the exact signature/chain/content against the installed public key before
merging that data. No Engineering version bump, new image or runtime code change
is needed for a compatible profile selection. No new signing workflow or GitHub
permission is introduced here. The signing owner remains a required independent
trust boundary; automation must not turn probe success into self-approval.

For withdrawal use `revoke` with the exact entry, hashed review evidence and
`--reason`, then the same review/sign/publish process. This also permits a
reviewed denial of a compiled pairing. Preserve tombstones across renewal and
compaction. Do not restore an old journal or delete a cache as rollback.

Withdrawal does not require the writer to understand a newer positive profile.
The denial-only path may remove positives and retain exact unchanged siblings;
it cannot add or alter a positive entry. This allows an older writer to withdraw
newer-profile authority without an Engineering code change.

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
