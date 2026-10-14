# Engineering 2.4.1-beta.7 acceptance

This contract covers HAMCP-129: three delegated platform reads
(`ha_get_system_health`, `ha_get_app`, `ha_get_integration`) on top of
beta.6 (the HAMCP-175 typed power and fan admission repair).
It separates four stages: source and disposable acceptance, release CI,
publication, and installed Engineering acceptance. This document authorizes
none of the later stages.

## 1. Source, review and CI

Bind the following:
- Josh's design approval of 2026-10-12, decisions O1-O9 in
  [the design](HAMCP_129_PLATFORM_READS_DESIGN.md) §10;
- the protected-path authorization (O6), which covers the Engineering
  runtime, the three upstream policy files, the unsigned compiled release
  registry and the beta.7 release metadata;
- Josh's release-order decision of 2026-10-13, which assigns beta.6 to the
  HAMCP-175 repair (PR #250) and beta.7 to this change (PR #247);
- the base `main`, and every later main merge into the branch, including the
  merge of beta.6;
- the final reviewed commit and tree.

Earlier review does not cover changed code.

Materialize 2.4.1-beta.7 in all three version authorities through the existing
staging tool. Require exact acceptance and release-note resolution, with no
staged declaration left over. Complete Full/Evidence on clean source and the
bounded independent review. The review needs a security section covering:
- provider admission and adapter binding;
- argument refusal before dispatch;
- output allowlists and masking;
- the sampled stats bound and its disclosed race (design D11);
- signed-only reuse withholding only the three platform wrappers, and the
  enumerated legacy 8.4.3 policy binding (D8);
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
Every beta.6 descriptor is unchanged. The compiled ha-mcp release registry
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
- `include_options` and `include_schema`;
- the `diagnostics`, `config_check` and `zha_network_full` sections;
- `repository` and the `"available"` source;
- unknown keys, and invalid types or values.

This also holds when the published schema is replaced by an open one.

`slug`, `domain` and `entry_id` are accepted public filters that Engineering
applies itself. Their upstream modes are never sent: `ha_get_app` never sends
`slug`, and `ha_get_integration` sends one fixed list request with
`include_options=false`, using `domain` only as an upstream `query` prefilter.

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
- When Engineering's stats-free snapshot shows more than 32 started add-ons,
  `include_stats` is refused as `response_too_large` and Engineering does not
  dispatch the stats call. This is a sampled bound: an add-on started after
  the snapshot is still fanned out upstream before the over-cap response is
  refused (design D11).
- Requested stats that upstream cannot supply for a started add-on make the
  result `partial` with reason `stats_unavailable` and a
  `stats_unavailable:<slug>` warning for each affected add-on. When every
  requested stat is present, or stats are not requested, the result is
  `complete`.

**Admission.**
- Upstream schema, description or annotation drift quarantines only the
  affected tool at each version.
- A policy-data change that moves, enables or drops an adapter binding refuses
  policy load.
- An unknown Core withholds all three tools without dispatch.
- A missing WebSocket profile withholds exactly its consumers.
- Admitted dispatches lease and release their Core commits.
- A future ha-mcp release admitted only through an owner-signed entry exposes
  the same 25 delegated reads as beta.6. The three platform wrappers are
  withheld with reason `platform_read_binding_absent`, reported in
  `readmission_withheld_*` health and audit fields. None of the 25 is
  quarantined or removed. A compiled exact release exposes all 28.
- A signed entry generated by the exact base code, and binding the earlier
  8.4.3 policy digest (`sha256:dc61eeb2…0cbe`), is used unchanged. It still
  exposes the same 25 reads, dispatches through the signed authority and
  withholds the three wrappers. It is matched only through the one
  enumerated, digest-verified legacy binding, which loads the packaged copy
  of the earlier policy.
- With that entry, the following still refuse: drift, a changed contract,
  revocation and expiry.
- An arbitrary digest, the legacy digest under another resource name, the
  earlier 8.5.0 digest and a tampered packaged copy are refused.
- A compiled exact 8.4.3 release still exposes 28 when a legacy entry is
  present.

**Mutation checks.** Each of the following fails a test:
- removing a key refusal, widening an allowlist or disabling masking;
- re-narrowing signed-only reuse;
- removing the legacy binding, skipping its digest check, aliasing any digest
  for its resource, or dropping its withheld wrappers;
- moving the stats cap check after the stats dispatch;
- reporting unavailable stats as complete.

## 2. Publication and recovery preparation

Publication needs Josh's applicable Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.7;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the known-good beta.6 and beta.5
artifacts and options for recovery. Nothing new is persisted, so downgrade to
beta.6 needs no migration.

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
4. Call `ha_get_app` with `include_stats=true`. Any one of the following
   passes:
   - it returns stats (`partial` with `stats_unavailable` if any started
     add-on has none);
   - Engineering's stats-free snapshot shows more than 32 started add-ons, and
     the call is refused as `response_too_large`. Engineering does not
     dispatch the stats call. "No dispatch" applies only to this case, which
     is decided from Engineering's snapshot;
   - the disclosed post-snapshot refusal (design D11). The snapshot shows at
     most 32 started add-ons, so Engineering dispatches the stats call. An
     add-on started in between means upstream sees more than 32 and fans out
     its stats requests. Engineering then refuses the over-cap result as
     `response_too_large`. For example, a snapshot of 32 dispatches, upstream
     sees 33 and fans out, and Engineering refuses the result. This accepted
     sampled-bound outcome is not an acceptance failure, and the upstream
     dispatch it involves is expected.

No push, CI trigger, signing, publication or installation is authorized by
source completion.
