# Engineering 2.4.1-beta.11 acceptance

This contract covers HAMCP-188: the pinned MCP Python SDK upgrade from 1.28.1
to 1.30.0 for GHSA-84m7-p3x7-pcfv / CVE-2026-59951, on top of beta.10
(HAMCP-181). It separates four stages:
- source and disposable acceptance;
- release CI;
- publication;
- installed Engineering acceptance.

This document authorizes none of the later stages.

## 1. Source, review and CI

Bind the following:
- Josh's owner decision for HAMCP-188: the latest 1.30.x patch, same major
  line, no move to 2.x, in a PR separate from HAMCP-184;
- the base `main` (2.4.1-beta.10 at `38ce701`) and every later main merge into
  the branch;
- the final reviewed commit and tree.

Earlier review does not cover changed code.

Materialize 2.4.1-beta.11 in all three version authorities through the existing
staging tool. Require exact acceptance and release-note resolution, with no
staged declaration left over. Complete Full/Evidence on clean source and the
bounded independent review. The review needs a security section covering:
- the advisory analysis per SDK component (inbound server transport and session
  manager, server auth, outbound client, websocket, experimental tasks);
- zero new write, dispatch, service-call, fallback or forwarding reachability;
- each SDK behaviour change listed in the
  [release notes](V2_4_1_BETA11_RELEASE_NOTES.md), accepted by Josh or
  corrected before Ready;
- no Engineering adaptation beyond the pinned-version guard.

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence. The `validate_prerequisites` dependency audit
(`pip-audit --strict`) must pass on the exact head.

Preserve the following:
- stable v1.1.2, including its `mcp==1.9.0` pin;
- historical persisted fixtures and durable formats;
- the 87-tool public catalog (59 native, 28 delegated), every beta.10
  descriptor, probe fingerprints, the Core capability profiles, every
  owner-signed Core registry entry and the signed ha-mcp registry;
- the 8.6.0 HOLD and the support window;
- every dependency except `mcp`, pinned runners and workflow permissions.

Offline source evidence must prove each of the following with the shipped
gateway, server factory, SDK adapter and synthetic peers.

**Dependency.** `pip-audit --strict` on the runtime lock reports no known
vulnerability. The locks differ from beta.10 only in the `mcp` entry, and
`build_inputs.py source` passes.

**Inbound transport** (`tests/test_hamcp188_mcp_sdk_session_contract.py`):
- In both stateless and stateful mode, a JSON-RPC batch is refused with 400
  and `INVALID_PARAMS` and dispatches nothing, while the equivalent single
  call dispatches once.
- A batch on a wrong credential path is refused with 404.
- Inbound negotiation of 2025-03-26, 2024-11-05 and an unsupported version is
  unchanged in both modes, and `tools.listChanged` is never advertised.
- The SDK protocol constants are unchanged.

**Stateful sessions.**
- The idle timeout is 1,800 seconds.
- Engineering's 1,024 cap and 2,000,000-byte gateway POST limit stay below the
  SDK's 10,000 sessions and 4 MiB.
- Refused opening requests consume no capacity, and DELETE and idle expiry
  reclaim the session and refuse its ID.
- An oversized POST is refused by the gateway with 413 before the SDK.
- The existing generation-gate, capacity and redaction tests still pass.

**Outbound client.**
- A cross-origin redirect from the upstream is not followed and is classified
  `upstream_error`.
- A same-origin, method-preserving redirect is followed. The SDK also permits
  a same-host, default-port HTTP→HTTPS upgrade; any other scheme, host or port
  change, a method change or added userinfo is refused.
- The existing tools/list, tools/call, exception-group (HAMCP-175 truthful
  refusal), `tool_call_attempted` and cancellation suites pass unchanged.

**Catalog.**
- All 59 native descriptors are byte-identical to 1.28.1.
- The 28 delegated reads and the power, fan, dashboard and entity-metadata
  admission suites pass unchanged.

## 2. Publication and recovery preparation

Publication needs Josh's applicable Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.11;
- the immutable version and commit images;
- provenance and SBOM.

The SBOM must list `mcp` 1.30.0. Never invent future image identities. Retain
the known-good beta.10 and beta.9 artifacts and options for recovery. Nothing
new is persisted, so downgrade to beta.10 needs no migration.

## 3. Installed Engineering

1. **Installed-image binding.**
   - Separately authorize installation of the published Engineering artifact.
   - Bind the running add-on to the published image index digest, the
     v2.4.1-beta.11 tag and the protected `main` merge commit.
   - Confirm that startup admitted the reviewed SDK. A refused SDK version
     fails startup, so a ready server is the evidence.
   - A source version alone does not prove the installed image.
2. **Catalog and health.** Compare all 87 public descriptors, Core signed
   selection, storage and audit health, F3 readiness, upstream admission and
   zero fallback.
3. **Live read.** Through the client-facing connector, run one native read
   (`get_entity` on an entity Josh chooses) and one admitted delegated read
   (`ha_get_state` on the same entity). Each must succeed with the expected
   state and provider attribution, and no fallback.
4. **Power canary.** On an entity Josh chooses, with Josh's explicit
   authorization of each physical action, run `turn_off`, then `turn_on`, or
   restore the recorded pre-canary state. Each step must report exactly one
   provider attempt, `succeeded_verified` by readback, and the beta.10
   dispatch facts (`dispatch_phase: sent_response_received`,
   `dispatched_at: null` with `send_time_not_recorded`). Leave the entity in
   its recorded pre-canary state.
5. **Dashboard read.** On a dashboard Josh chooses, run `get_dashboard_config`
   and `dashboard_integrity_analysis`. Both must complete through the
   reviewed ha-mcp path with bounded output and no write.
6. **Session reclamation (if the registry is enabled).** Repeat a connector
   reconnect after a session ends. A new session must open normally, and
   capacity health must not report exhaustion.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
