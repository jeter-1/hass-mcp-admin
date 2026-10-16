# Engineering 2.4.1-beta.13 acceptance

This contract covers HAMCP-164: the Core gateway fails closed for any tool
with no Core requirement mapping. It builds on beta.12 (HAMCP-184).

It separates four stages:
1. source and disposable acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

This document authorizes none of the later stages.

## 1. Source, review and CI

Bind the following:
- the design note `docs/HAMCP_164_CORE_GATE_FAIL_CLOSED_DESIGN.md`, with its
  final protected-file list, inventory table and residual findings R1–R8;
- Josh's owner direction on HAMCP-164:
  - an unmapped static tool or F3 identity is refused;
  - Core independence is an explicit, closed, justified allowlist, never
    implied by a missing entry;
  - no currently working tool changes behavior.
- the original base `main` (2.4.1-beta.12 at `b126970`), and every later
  `main` merge into the branch, including `cef5b80` (HAMCP-162, documentation
  only);
- the final reviewed commit and tree.

Earlier review does not cover changed code.

Materialize 2.4.1-beta.13 in all three version authorities through the
existing staging tool. Require exact acceptance and release-note resolution,
with no staged declaration left over. Complete Full/Evidence on clean source
and the bounded independent review.

The review needs a security section covering:
- zero new write, dispatch, service-call, fallback or forwarding reachability;
- the refusal occurring before Core acquisition and before the MCP
  application;
- exact-name allowlist membership, with no prefix or wildcard path;
- delegated reads recognized only while currently published as `delegated`
  read-gateway capabilities;
- truthful zero-attempt telemetry and a value-free audit record.

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Preserve the following:
- stable v1.1.2;
- historical persisted fixtures and durable formats;
- probe fingerprints, the Core capability profiles, every owner-signed Core
  registry entry and the signed ha-mcp registry;
- the 8.6.0 HOLD and the support window;
- dependency locks, build inputs, pinned runners and workflow permissions.

The public catalog stays at **87 tools (59 native, 28 delegated)**, and every
beta.12 descriptor is unchanged.

### Offline source evidence

Run `tests/test_hamcp164_core_gate_fail_closed.py` against the exact released
tree. It must prove each of the following.

**Inventory.**
- All 59 native tools match the pinned table: 37 mapped, 2 conditional
  (`check_ha`), 16 allowlisted and 4 prevalidation-refused.
- The allowlist is exactly the 16 reviewed names, immutable, and justified.
- All 30 delegated routes map non-empty requirements.
- The 15 F3 identities map profiled requirements.

**Unmapped refusal (offline proof).**
- A synthetic unmapped tool is refused as `CoreRequirementUnmapped` /
  `unsupported_operation` / `core_requirement_unmapped`, not retryable, in an
  MCP tool result with `isError: true`, with:
  - zero MCP application calls;
  - zero Core acquisitions;
  - zero Home Assistant and upstream request counts.
- The audit record has field names only.
- Prefix-extended, truncated, case-variant, whitespace and wildcard names are
  refused the same way, as are unpublished or withheld delegated names.
- Unknown, empty or conflicting F3 identities cannot acquire Core authority.

**Unchanged success.**
- Every allowlisted tool, and both conditional tools with `check_ha=false`,
  runs while Core refuses every lease.
- `get_core_log_history` runs on a Core release that refuses mapped reads.
- Mapped tools acquire exactly their requirements.
- A published delegated read reaches the read gateway.
- The production application installs the Core runtime.

**Mutation checks.** Each of the following fails a test:
- restoring skip-on-empty (no unmapped refusal);
- prefix-matching the allowlist;
- wildcard-matching the allowlist;
- emitting the unmapped refusal with `isError: false`.

## 2. Publication and recovery preparation

Publication needs Josh's applicable Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.13;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.12 artifacts and the
known-good beta.11 and beta.10 artifacts and options for recovery.

No new persisted format is introduced, and beta.12 reads every beta.13
record.

## 3. Installed Engineering

All installed checks below are read-only unless a step names Josh's explicit
authorization.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to the published
   image index digest, the v2.4.1-beta.13 tag and the protected `main` merge
   commit. A source version alone does not prove the installed image.
2. **Catalog unchanged.** Compare all 87 public descriptors (59 native, 28
   delegated) with beta.12. Also compare Core signed selection, storage and
   audit health, F3 readiness and zero fallback.
3. **All 87 tools behave as before (read-only spot checks).**
   - **Delegated read:** one delegated read (for example `ha_get_state` on a
     Josh-chosen entity) succeeds exactly as on beta.12.
   - **Native read:** one native mapped read (for example `get_entity` on the
     same entity) succeeds exactly as on beta.12.
   - **Allowlisted:** `get_core_log_history` succeeds exactly as on beta.12.

   No published tool may return `CoreRequirementUnmapped`. Any occurrence is
   a stop condition.
4. **Unmapped refusal.** This is proven offline only (§1). Do not probe the
   live endpoint with synthetic tool names.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
