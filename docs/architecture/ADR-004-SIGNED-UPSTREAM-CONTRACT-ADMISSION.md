# ADR-004: Signed upstream contract-family admission

Status: accepted for RC2dev9

RC2dev10 clarification: the exact selected attestation also supplies optional
raw-schema, reviewed-security-descriptor, fixture-runtime-descriptor, and
published-runtime-descriptor fingerprints for retained health fields. These
values are informational only and are not added to steps 3–5 of the admission
decision. This removes stale single-release diagnostics without expanding the
registry's authority.

RC2dev11 operational clarification: registry lifecycle changes are prepared by
a fixed-path repository CLI and a serialized three-job workflow. Inspection has
no seed or write authority and emits separate raw-evidence and wheelhouse
artifacts. Signing is protected, read-only, imports only the standard library and
`cryptography`, reconstructs the requested mutation from trusted dispatch inputs,
the verified current registry, and raw evidence before seed exposure, and installs
only a hash-locked offline dependency closure. The seed-bearing phase signs only
prevalidated canonical bytes. Publication is the only repository/PR
writer and receives no seed. Lifecycle evidence is individually signed and
forms a contiguous digest chain. The captured `main` SHA and expected sequence
are checked before signing and again before publication. Direct CLI mutation
uses complete-set staging and verification, per-file atomic replacement, and
automatic byte-for-byte restoration on failure; it does not claim filesystem
transactionality. Workflow publication uses one coherent verified Git commit.
These operator mechanics do not participate in runtime admission and do not
expand the signed data authority described below.

## Decision

Engineering admits an exact upstream release only when a binary-owned semantic
contract family and a release attestation both match. The family contains
executable safety policy; the attestation contains reviewed release data. Signed
data never becomes code.

The internal framework uses a compiled family table so another family can be
designed later without changing the registry format. RC2dev9 compiles exactly one
entry, `ha_mcp_dashboard_read_v2`, and enables exactly two typed Engineering
operations over `ha_config_get_dashboard`. Unknown family identifiers fail while
the registry is parsed.

The admission sequence is:

1. initialize and discover the exact required tool without calling it;
2. require exact server identity, protocol and a compiled family structure;
3. normalize dispatch-relevant input, safety, output and runtime contracts;
4. resolve an exact built-in or verified signed release attestation;
5. require all normalized fingerprints to match and the entry not to be revoked;
6. construct one fixed non-screenshot argument shape;
7. dispatch only the required dashboard read tool;
8. validate the structured return and dual hash contract.

There is no selection by arbitrary tool name and no fallback. An unknown release
may cause one bounded signed-registry refresh, never speculative dispatch.

## Rationale

Pinning a whole serialized descriptor makes harmless prose and presentation
changes outages. Pinning a whole catalog makes unrelated tool additions outages.
Trusting annotations alone is insufficient because the upstream dashboard tool is
mixed-operation. The combined family/attestation design tolerates only reviewed
irrelevant drift while retaining fail-closed argument, annotation, return and hash
contracts.

Built-in entries make reviewed releases independent of remote-registry
availability. The optional Ed25519 registry permits a later exact compatible
release to be reviewed and admitted through a data PR without granting the signer
authority to alter executable capability policy.

## Consequences

Health distinguishes admission status/source, observed and attested versions,
family, four normalized admission results, separate selected-release legacy
fingerprints/results, revocation, registry signature/sequence/age,
cache and refresh state. Endpoint and registry contents remain secret/bounded.

The public catalog stays `40 / 25 / 0`; schema version 1 and every public input
schema are unchanged. Generic Standard HA MCP delegation remains unavailable.
Dashboard writes, screenshots, preference persistence, service/batch execution,
`ha_set_entity`, and `ha_set_device` remain unreachable.

Future registry administration is not an extension of this family. It requires a
new separately reviewed governed plan/apply/rollback design and a new compiled
family or native provider decision.

The monotonic runtime anchor is the verified cache in persistent `/data`. A
lower sequence and equal-sequence conflicting replay fail closed. Recovery from
revocation, bad data or expiry uses a separately reviewed higher sequence; a Git
revert is not a registry rollback procedure. Erasing `/data` removes the local
rollback anchor and therefore belongs to a separately governed backup/recovery
policy. Production registry URLs remain fixed, and failure-injection stays in
the disposable acceptance harness.
