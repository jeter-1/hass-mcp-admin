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
the four allowed data classes atomically. It never accepts a registry URL,
output path, family, tool or capability override.

The existing protected workflow now dispatches the full lifecycle. It remains
main-only, uses `upstream-attestation-signing`, serializes operations, gives the
private seed only to the signing step, and creates only a data-only draft PR.
It cannot build/publish Engineering images, packages, tags, releases or
deployments.

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
custody and an offline recovery copy. This release does not create any of them.

Rollback target: `2.0.0-rc2-dev10`. A rollback does not justify lowering a
registry sequence or deleting the LKG cache.
