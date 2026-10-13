# Engineering 2.4.1-beta.6 acceptance

This contract covers HAMCP-129: three delegated platform reads
(`ha_get_system_health`, `ha_get_app`, `ha_get_integration`) on top of beta.5.
It separates four stages: source and disposable acceptance, release CI,
publication, and installed Engineering acceptance. This document authorizes
none of the later stages.

## 1. Source, review and CI

Bind the following:
- Josh's design approval of 2026-10-12, decisions O1-O9 in
  [the design](HAMCP_129_PLATFORM_READS_DESIGN.md) §10;
- the protected-path authorization (O6), which covers the Engineering
  runtime, the three upstream policy files, the unsigned compiled release
  registry and the beta.6 release metadata;
- the base `main`, and every later main merge into the branch;
- the final reviewed commit and tree.

Earlier review does not cover changed code.

Materialize 2.4.1-beta.6 in all three version authorities through the existing
staging tool. Require exact acceptance and release-note resolution, with no
staged declaration left over. Complete Full/Evidence on clean source and the
bounded independent review. The review needs a security section covering:
- provider admission and adapter binding;
- argument refusal before dispatch;
- output allowlists and masking;
- the stats fan-out bound;
- Core route leases;
- zero write, fallback or forwarding reachability.

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Preserve the following:
- stable v1.1.2;
- historical persisted fixtures and durable formats;
- locks, holds and uncertainty;
- production journals;
- probe fingerprints;
- the Core capability profiles;
- every owner-signed Core registry entry;
- the 8.6.0 HOLD and the support window;
- dependency locks, build inputs and workflow permissions.

The public catalog grows from 84 to **87 tools (59 native, 28 delegated)**.
Every beta.5 descriptor is unchanged. The compiled ha-mcp release registry
evidence identities (`artifact_evidence_sha256`, dashboard
`attestation_fingerprint`) are regenerated only because the reviewed
classification counts change (automatic reads 25 to 28, wrapper-required 13
to 10).

Offline source evidence must prove each of the following through the shipped
gateway, policy loader and Core readmission at 8.4.3, 8.5.0 and 8.6.0.

**Useful success.**
- Each tool returns its projected result from synthetic upstream payloads.
- Each tool also does so from the exact upstream outputs captured offline in
  `tests/fixtures/hamcp129/`, where the pinned ha-mcp releases ran against
  synthetic Core and Supervisor fakes.

**Refusal before dispatch.** Each of the following is refused with zero
upstream calls:
- `slug`, `domain`, `entry_id`, `include_options`, `include_schema`;
- the `diagnostics`, `config_check` and `zha_network_full` sections;
- `repository` and the `"available"` source;
- unknown keys, and invalid types or values.

This also holds when the published schema is replaced by an open one.

**Positive output allowlists.**
- Every result field is listed.
- Synthetic secrets and canaries in unlisted keys never appear.
- Upstream error text and warnings are withheld.
- A shape violation is `invalid_response`, with no partial data.

**Masking and bounds.**
- E-mail-like substrings in integration and Matter titles are masked before
  output and before filtering.
- Lists and strings are capped, and the result is reported `partial`.
- A result beyond the size limit fails closed as `response_too_large`.
- More than 32 started add-ons refuses `include_stats` before the stats call.

**Admission.**
- Upstream schema, description or annotation drift quarantines only the
  affected tool at each version.
- A policy-data change that moves, enables or drops an adapter binding refuses
  policy load.
- An unknown Core withholds all three tools without dispatch.
- A missing WebSocket profile withholds exactly its consumers.
- Admitted dispatches lease and release their Core commits.

**Mutation checks.** Removing a key refusal, widening an allowlist or
disabling masking each fails a test.

## 2. Publication and recovery preparation

Publication needs Josh's applicable Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.6;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the known-good beta.5 and beta.4
artifacts and options for recovery. Nothing new is persisted, so downgrade to
beta.5 needs no migration.

## 3. Installed Engineering

1. Separately authorize installation of the published Engineering artifact and
   bind it to the published index and protected source.
2. Compare all 87 public descriptors, the Core signed selection, storage and
   audit health, upstream admission (28 delegated reads with no quarantine
   for the three new tools at the deployed ha-mcp version) and zero fallback.
3. Call each new tool once with default arguments. Each call must:
   - return `success` with a `completeness` block;
   - contain no add-on `options`, network data, repair placeholders,
     Supervisor or Core error text, or unmasked e-mail addresses.

   A `partial` result with fixed reason codes is acceptable.
4. Call `ha_get_app` with `include_stats=true`. It either returns stats or, if
   more than 32 add-ons are started, refuses as `response_too_large` without
   the stats call.

No push, CI trigger, signing, publication or installation is authorized by
source completion.
