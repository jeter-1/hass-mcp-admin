# ha-mcp capability-scoped automatic-readmission acceptance

This is the operational acceptance plan for ADR-023. It is not a release
declaration, deployment authorization, registry-signing procedure, or approval
to access a household Home Assistant installation.

## Source and CI acceptance

At the exact release-candidate head:

1. Replay every ADR-020 vector through both the reference and production
   adapters and require identical reports.
2. Run gateway, signed-registry, routing, health, MCP transport, configuration,
   and exact-release regressions.
3. Prove a synthetic signed compatible update restores matching reads without
   restarting Engineering.
4. Prove one changed read is quarantined while compatible siblings return.
5. Prove missing, duplicate, malformed, incomplete, identity-mismatched,
   protocol-mismatched, unsigned, expired, rollback, replay-conflict, and
   revoked evidence fails closed.
6. Prove unknown action, mixed, write, and future tools remain unreachable.
7. Prove call-time initialize, catalog, target, authority, generation, session,
   and lease validation precedes the single logical `tools/call`.
8. Prove sequential and concurrent duplicate lease commit fails, an unused
   retired lease cannot commit, and a committed call cannot republish authority.
9. Prove restart accepts only a signature-valid cache, expired positive evidence
   becomes unavailable, and cached revocations remain denial-only.
10. Require bounded health/audit evidence and fallback count zero.
11. Run complete unit, Fast, Full, Evidence, exact-image, exact ha-mcp, disposable
    update, dependency, secret, YAML, PowerShell, whitespace, metadata, public
    contract, and stable-v1 gates required by the staged release.
12. For exact 8.4.1 and 8.4.3, require capability-scoped error compatibility: a changed
    `invalid_search` envelope may affect `ha_search` but cannot suppress the 21
    unchanged reads or other independently accepted changed reads.
13. Require `ha_get_operation_status` and all App, mixed, action, write,
    destructive, unknown, and generic-forwarding capabilities to remain
    unreachable. Dashboard authority is separate from generic reads and must be
    exactly attested or explicitly quarantined; Beta 58 retains exact 8.4.3
    getter/setter authority.
14. Prove a synthetic future catalog can remove a reviewed blueprint getter and
    add a mixed read/write blueprint manager while retaining every unrelated
    exact read and exposing neither blueprint capability.

## Disposable update scenarios

Use only synthetic registries with ephemeral test keys and disposable ha-mcp
catalogs. No production private key or household endpoint is permitted.

- **Compatible:** update from a compiled exact release to a synthetic signed
  release bound to the same binary profile. Matching delegated reads return;
  no Engineering process restart is used.
- **Partial:** change one selected read contract. That read is quarantined and
  every compatible reviewed sibling remains delegated.
- **Incompatible:** use no authority, invalid identity/protocol, incomplete
  pagination, malformed or duplicate selected descriptors, expired evidence,
  rollback, replay conflict, or revocation. The affected route or surface is
  unavailable before `tools/call`.
- **Negative reachability:** add synthetic action, mixed, persistent-write, and
  unknown tools. None is registered or forwarded. Provider call count remains
  zero for rejected cases and fallback remains zero.
- **Races:** reconcile two observations concurrently and require only the newest
  ticket to publish. Retire authority with unused leases and require every
  later commit to fail. Concurrent duplicate commit has one winner.
- **Capacity:** exhaust bounded leases, commits, diagnostics, reasons, or
  revocation-source history and require a local fail-closed result without
  changing unrelated surfaces.

## Post-deployment read-only acceptance

This section requires separate deployment and live-access authorization.

1. Confirm the exact advertised Engineering release and build SHA.
2. Confirm the configured ha-mcp endpoint still negotiates the reviewed MCP
   protocol and the complete catalog is bounded.
3. Confirm the distinct release-registry option is enabled with the approved
   public trust anchor. Do not reveal the key or endpoint in evidence.
4. Manually update ha-mcp to the separately reviewed signed compatible release.
5. Wait for bounded reconciliation, then reconnect or explicitly re-list the
   client catalog. Do not claim list-change notification support.
6. Confirm only signed, binary-known compatible automatic reads returned.
7. Confirm every changed or missing read is held independently and every action,
   mixed, write, or unclassified tool remains unreachable.
8. Execute one harmless read through the normal connector. Confirm one logical
   dispatch, bounded response, correct generation/authority projection, and
   fallback count zero.
9. Revoke that exact synthetic acceptance release through the separately
   governed production signing/publication process. Confirm the route generation
   retires before any later call and survives restart as denial-only evidence.
10. Confirm Core, proxy, dashboard, helper, F3, write, and provider-routing
    authority did not change.

No step authorizes a Home Assistant mutation, add-on restart, deployment,
credential change, private-key handling, or registry publication by this
development session.
