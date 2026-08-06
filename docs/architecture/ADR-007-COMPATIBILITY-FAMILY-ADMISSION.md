# ADR-007: Evidence-bound compatibility-family admission

Status: accepted for staged Engineering 2.2.0-beta.23

## Context

Engineering previously required a complete human review for every exact
`ha-mcp` release, even when a patch changed only immutable identity,
documentation, or dependency packaging and its runtime contract remained
identical. Removing that friction by accepting a version range would be unsafe:
a semver family, matching catalog hash, or upstream declaration is not release
authority and cannot prove response, provider, packaging, or security behavior.

The desired boundary is exact evidence for every release, with human review
reserved for meaningful contract or behavior drift.

## Decision

A compatibility family is a source-controlled review policy and comparison
model. It is not a runtime version matcher. The family compiler may emit one
new exact release entry only after all of these are bound:

- exact source tag target, commit, source-archive digest, standalone OCI index,
  per-architecture manifests, add-on indexes/manifests, and image revision;
- exact package, MCP initialize, and Supervisor installed versions;
- two byte-identical exact runtime captures and a complete 78-tool accounting;
- the exact per-tool policy, response-envelope and provider dispositions;
- dashboard, lifecycle, security/transport, and dependency-isolation evidence;
  and
- one canonical family decision whose digest is stored in the exact entry.

Runtime selection remains a lookup in `upstream_release_registry.json` by the
observed complete version. The family’s major/minor/patch fields only determine
whether the review-time compiler may compare a candidate. They never authorize
8.1.x, latest, an unlisted patch, or another server. Candidate evidence cannot
authorize itself.

Repository validation verifies the family policy and decision resources before
the compiled registry can be accepted. The packaged runtime intentionally does
not read those review-only documents; it consumes the resulting exact registry
entry, including its bound decision digest and provider dispositions. This keeps
the review-time compiler outside the production image without weakening exact
runtime selection.

## Drift decisions

Automatic admission is limited to reviewed nonsemantic categories: immutable
identity, documentation, dependency packaging, deployment-dynamic metadata,
and a descriptor wording change covered by an explicit tool-and-field
normalization rule. No normalization rule is present for the 8.1.x family, so
any descriptor wording change currently fails as unknown drift.

Input or output schema, safety annotation, classification, consumed response
envelope, lifecycle provider, dashboard provider, security/transport, tool
addition/removal/rename, or unknown drift cannot take the automatic path.
Changed automatic reads are held individually when the rest of the release can
remain exact. Changed writes and mixed tools remain nondelegated. A provider
whose required contract changed is held without disabling unrelated providers.
Unknown drift or a changed tool set rejects the candidate globally.

## Revocation and migration

Each exact entry may be revoked independently. Revoked entries remain
historically readable but disappear from active runtime authority and supported
version reporting. A revocation cannot affect another patch release unless that
entry is separately revoked. The default entry may never be revoked.

Registry format 3 adds optional family binding, provider disposition, source
archive, and release-specific revocation fields. Historical format-2 release
semantics are migrated in place: existing exact entries have no inferred family
authority, default to admitted providers, and remain active. Exact 8.1.0 policy,
tool classifications, digests, and runtime behavior are unchanged.

## First acceptance: ha-mcp 8.1.1

The 8.1.1 tag resolves to source commit
`ae84694b50bfbd8d507042381fdee5e529bf73c5`. Its two exact runtime captures are
byte-identical and all 78 descriptors match 8.1.0. Observed source drift is
documentation plus private vendoring of `websockets==17.0.1`; production source
does not import or declare the shared package, and the embedded listener selects
no WebSocket protocol. All four provider surfaces remain admitted. The generated
exact runtime entry is `ha-mcp-v8.1.1-e1d76a6e`.

The named 409 sentence is `policies.pending.already_decided`, a settings-UI
locale host sentence exercised by browser-side translation tests. It is not an
MCP tool descriptor, tool response, or provider-consumed envelope, so its exact
affected Engineering surface is empty and it is classified as documentation
drift rather than normalized descriptor drift.

## Consequences

Ordinary patch releases with exact matching evidence can be admitted with less
repetitive human review. Every runtime release still has immutable identities,
its own catalog and policy, exact provider authority, deterministic validation,
and release-specific revocation. Meaningful or unknown drift still requires a
human decision and cannot be hidden by semver, normalization, or fallback.
