# RC2dev11 release notes

Version: `2.0.0-rc2-dev11`

RC2dev11 is a signed-registry production-operations correction. It adds no MCP
tool, provider route, contract family or Home Assistant capability. The public
catalog remains 40 registered / 25 canonical / zero planned.

## Operator lifecycle

`scripts/manage_upstream_trust_registry.py` provides fixed-path `bootstrap`,
`add`, `revoke`, `restore`, `renew` and public-key-only `verify` operations.
Every mutation supports `--dry-run`, requires an expected current sequence,
derives exactly the next sequence, creates deterministic canonical JSON, checks
private/public Ed25519 correspondence, verifies before replacement and writes
the four allowed data classes with per-file atomic replacement plus complete-set
preverification, post-write verification, and automatic restoration on failure.
This is not a transactionally atomic multi-file filesystem operation. It never accepts a registry URL,
output path, family, tool or capability override.

The protected workflow now has three authority boundaries. Online inspection is
repository-read-only, has no private seed, and emits raw immutable inspection
evidence and the verified signing wheelhouse as two separately rooted artifacts.
It does not choose the registry mutation that receives the signature. Protected
signing is repository-read-only, installs a complete reviewed hash-locked wheel closure
offline with `--no-index --require-hashes`, and exposes the seed only to the
minimum signing step. Publication is the only repository/PR writer and never
receives or references the seed. It re-verifies with the public key and publishes
the complete four-class set through one coherent Git commit and a draft PR.

The protected signer has a deliberately minimal import graph: standard library
plus `cryptography`, with no Engineering application, MCP, Home Assistant,
dashboard, transport, or test dependency. Its no-seed phase verifies the exact
artifact layouts and trusted workflow inputs, fetches the accepted current
registry, reconstructs the requested transition from that registry and raw
evidence, and emits canonical prepared bytes and their digests. The seed-bearing
step only signs those already validated bytes and performs no network, Git,
installation, or reconstruction work. Public-key-only verification produces the
separate signed-data artifact. CI exercises these exact phases as subprocesses in
a fresh lock-only virtual environment.

Every lifecycle record is independently signed over canonical JSON and chains
the previous registry digest and previous complete signed-evidence digest.
Verification traverses the complete contiguous history from sequence 1. Editing,
removing, replacing, reordering, duplicating, or adding a historical record fails.
The exact captured `main` SHA and expected sequence are checked immediately before
signing and again immediately before publication; stale runs abort and require a
new dispatch.

## Runtime correction and acceptance

`ReleaseAttestation.reviewed_at` now requires a valid timezone-aware ISO-8601
UTC value. `Z` and `+00:00` are accepted and represented canonically as `Z`;
naive values, non-UTC offsets and invalid dates fail before admission. Contract
normalization and admission fingerprints are unchanged.

The disposable harness uses the production verifier, LKG cache, admission and
health paths with synthetic keys, temporary `/data`, injected fetch results and
an injectable clock. Coverage includes fresh/cache admission, restart,
unavailability, bad signatures before/after the seven-day hard age,
rollback/replay, revocation/restoration, renewal and higher-sequence recovery.

## Security and compatibility

- The compiled family remains only `ha_mcp_dashboard_read_v2`.
- The only allowlisted upstream tool remains `ha_config_get_dashboard`.
- Screenshots, preference writes, dashboard writes, services,
  `ha_set_entity`, `ha_set_device` and generic delegation remain unreachable.
- Public MCP input schemas and governance storage are unchanged.
- Registry schema version 1 and pre-RC2dev11 registry entries remain readable.
- Stable v1.1.2 is unchanged.

## Accepted limitations

Production URLs remain fixed and there is no live outage-injection switch.
Failure/expiry tests run only in the disposable harness. The rollback anchor is
the persistent `/data` LKG; erasing `/data` removes that anchor, so backup and
restore require separate governance. Production use must wait for creation of
the protected environment, reviewer policy, three environment secrets, key
custody, an offline recovery copy, and independent rereview of these controls.
This release does not create any of them.

Rollback target: `2.0.0-rc2-dev10`. A rollback does not justify lowering a
registry sequence or deleting the LKG cache.
