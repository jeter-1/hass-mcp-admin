# RC2dev9 release notes

Version: `2.0.0-rc2-dev9`
Baseline: RC2dev8 source `4ae05328bf70d9b603554cb7333c3126aea10b4c`
Release type: dashboard-provider compatibility and trust hardening

## Why this release exists

RC2dev8 safely pins one reviewed `ha-mcp` dashboard contract, but each upstream
version change otherwise requires another Engineering code release even when the
dispatch-relevant contract is unchanged. RC2dev9 replaces that one-version gate
with a binary-owned contract family and exact release attestations. It does not
broaden the provider or the public Engineering tool surface.

Built-in attestations cover exact upstream releases `7.13.0`, `7.14.0`, and
`7.14.1`. Each binds server identity, version, source tag and commit, immutable
official image digest and platform digests, image revision, semantic contract
fingerprints, and reviewed evidence. The compiled family is
`ha_mcp_dashboard_read_v2`.

## Admission contract

Admission requires all of the following:

- initialized server name exactly `ha-mcp` and MCP protocol `2025-03-26`;
- exact release entry from built-in data or a valid signed registry;
- exact tool name `ha_config_get_dashboard`;
- compatible input and output structures;
- `destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`, and
  absent `readOnlyHint` for this reviewed mixed-operation contract;
- exact normalized input, security, output, and runtime fingerprints;
- a compiled family and compiled Engineering argument builder.

Descriptions, display titles, property ordering, unrelated upstream tools, and
the whole-catalog fingerprint are observability evidence, not security gates.
Argument names, types, defaults, required fields, `additionalProperties`, safety
annotations, output/hash requirements, and unknown semantic metadata remain
fail-closed.

The only allowed upstream calls remain:

```json
{"list_only":true,"include_screenshot":false}
```

and:

```json
{"url_path":"<exact-path>","list_only":false,"force_reload":true,"include_screenshot":false}
```

where `force_reload=false` is the sole allowed variation. Engineering never
forwards `mode`, `query`, screenshot/rendering fields, caller-supplied raw
arguments, or newly discovered optional arguments.

## Signed compatibility registry

An optional fixed-location HTTPS registry supports future exact stable upstream
releases without changing the Engineering binary. The add-on options are:

- `upstream_trust_registry_enabled` (default `false`);
- `upstream_trust_registry_public_key` (base64 Ed25519 public key; non-secret).

The private signing key is not an add-on option and exists only in the protected
GitHub environment used by the manual attestation workflow. Runtime validation
uses strict JSON, detached Ed25519 signatures, sequence rollback/replay checks,
expiry, revocation, fixed size/time limits, and an atomic last-known-good cache
under `/data`. A failed refresh never replaces valid cached data. Built-in
entries remain available when the remote registry is disabled or unreachable.

The registry is data-only. It cannot define a contract family, tool, provider,
argument builder, route, fallback, or public Engineering capability. The manual
workflow accepts only an exact stable version, reviews fixed
`homeassistant-ai/ha-mcp` source and `ghcr.io/homeassistant-ai/ha-mcp` image
locations, runs an exact-image disposable read test, signs bounded evidence, and
opens a draft data PR. It cannot publish packages, tags, releases, deployments,
or Engineering images.

## Upstream evidence

| Version | Source commit | Official image index digest | Image revision |
|---|---|---|---|
| 7.13.0 | `f4eb53621ccb814cb7123d2811e06eda3577129c` | `sha256:2bc9a40fcc162424b123b1d30102cfa567090a41cb54003d0883d8d59c2d0167` | `108d84869ee866784e13c4b7e4c1316d26ecc79a` |
| 7.14.0 | `488085ed32192f159fd2c9ffcc4376098df667eb` | `sha256:198fa1567c6661d3b4b20cff10dfaf0c6ddecd2c4dbf13828df2a1ce23eac2fd` | `fb455a20357a31e7653e63a96b3a663fab514a6f` |
| 7.14.1 | `255acec1affa6528004a122eb83e30aee9c77713` | `sha256:68f386d9becfcc58476f1881a0025f4c6a3ae5874c15cdd61097b14156886292` | `4911d09d1c19923230f624d2f3158f1cda5ccc46` |

The image revision commits descend from the reviewed tags and differ only in
upstream add-on changelog/config metadata. The dashboard implementation and hash
contract are unchanged. Official images provide amd64 and arm64 manifests with
per-platform provenance; SBOM presence is recorded rather than assumed.
Detailed evidence is in `docs/evidence/RC2DEV9_UPSTREAM_RELEASE_REVIEW.json`.

## Explicit non-goals and deferred work

RC2dev9 adds no public tools and preserves `40 registered / 25 canonical / 0
planned`, schema version 1, and every public input schema. All upstream writes,
generic delegation, service/batch execution, dashboard mutation, screenshots,
and preference persistence remain disabled. `StandardHaMcpGateway.available`
remains false and fallback remains none.

`ha_set_entity` and `ha_set_device` are destructive upstream registry tools and
are not implemented, exposed, allowlisted, routed, or added to governance.
Their reviewed 7.14.1 schema/source evidence is retained only in
`docs/evidence/RC2DEV9_DEFERRED_REGISTRY_WRITE_CONTRACTS.json`. Governed registry
administration is a separately reviewed future milestone.

Production v1.1.2, its slug, port, image, storage, source, and packaging are
unchanged. RC2 governance records require no migration.

## Rolling upgrade and rollback

After review, merge and successful normal Engineering promotion, deploy dev9
while upstream remains 7.13.0. Verify runtime provenance, `40 / 25 / 0`, HA,
governance/audit/provider health, built-in admission, dashboard list/get, zero
fallback and all write flags false. Configure the public registry key only in a
separate approved operator step. Verify the exact 7.14.1 entry before updating
the standard upstream add-on directly from 7.13.0 to 7.14.1; 7.14.0 need not be
installed. Re-run list/get/not-found and negative reachability acceptance.

Rollback Engineering to
`ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc2-dev8` and/or upstream to
7.13.0 if admission, signature/cache, schemas, provenance, hashes or write
containment fail. Do not change stable v1.1.2.

An MCP initialize/tools-list observation cannot by itself prove the immutable
digest of the running upstream container. The attestation binds official source
and image evidence to the exact observed runtime identity/semantic contract;
direct Supervisor/container-runtime digest evidence remains a separate live
acceptance item. Engineering still constructs and validates every upstream
argument itself even after an attestation is accepted.
