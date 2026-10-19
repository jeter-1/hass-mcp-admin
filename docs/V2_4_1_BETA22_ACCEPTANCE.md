# Engineering 2.4.1-beta.22 acceptance

This contract covers HAMCP-211: `aiohttp` 3.14.3 → 3.14.4 for
GHSA-2g87-pp6c-29x5, CVE-2026-111063 and CVE-2026-111064, and the `yarl`
1.24.5 → 1.25.1 upgrade that 3.14.4 requires. It builds on 2.4.1-beta.21
(HAMCP-205).

beta.21 was merged but superseded before publication. Its publish run failed
`pip-audit --strict` on these advisories. Per Josh's decision, beta.21 is not
re-tagged or re-published. beta.22 is the first published beta to carry
HAMCP-205's administrator verification, so this contract includes
[the beta.21 acceptance contract](V2_4_1_BETA21_ACCEPTANCE.md) for the
installed checks.

See also:
- Release notes:
  [`V2_4_1_BETA22_RELEASE_NOTES.md`](V2_4_1_BETA22_RELEASE_NOTES.md).
- beta.21 release notes:
  [`V2_4_1_BETA21_RELEASE_NOTES.md`](V2_4_1_BETA21_RELEASE_NOTES.md).

**Release target.**
- This PR is based on `main` at 2.4.1-beta.21 (base
  `881636aeaa96708259ddea1440cd131e34befceb`).
- It was staged as `2.4.1-beta.22` by one owner-authorized run of
  `scripts/promote_next_release.py --apply`, which consumed the declaration.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's owner decisions for HAMCP-211: aiohttp 3.14.4, shipped as the next
  beta, with beta.21 recorded as superseded before publication and not
  re-tagged or re-published;
- `2.4.1-beta.22` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head, including the
  `pip-audit --strict` dependency audit;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- the advisory analysis per aiohttp component (client REST, client
  WebSocket, cookie jar, server);
- zero new write, dispatch, service-call, fallback or forwarding
  reachability;
- each library behaviour change listed in the
  [release notes](V2_4_1_BETA22_RELEASE_NOTES.md), accepted by Josh or
  corrected before Ready;
- no Engineering runtime adaptation.

Preserve the following:
- stable v1.1.2, including its `aiohttp==3.9.5` pin;
- historical persisted fixtures and durable formats;
- the beta.21 public catalog and every descriptor, probe fingerprints, the
  Core capability profiles, every owner-signed Core registry entry and the
  signed ha-mcp registry;
- the 8.6.0 HOLD and the support window;
- every dependency except `aiohttp` and `yarl`, pinned runners and workflow
  permissions.

### Dependency proof

- `pip-audit --strict --no-deps --disable-pip` on
  `hass_mcp_engineering_beta/requirements.lock` and on
  `hass_mcp_engineering_beta/requirements.txt` reports no known
  vulnerability. On beta.21's lock it reports exactly the three aiohttp
  advisories. CI's audit (`pip_audit --strict` on the runtime lock) passes.
- **Open owner decision:** the test-only lock `tests/requirements.lock`
  carries `urllib3` 2.7.0 (PYSEC-2026-4175, PYSEC-2026-4176 and
  PYSEC-2026-4177; fixed in 2.8.0), unchanged from beta.21. `urllib3` is not
  in the runtime image and is not changed here, because HAMCP-211 bumps only
  what aiohttp 3.14.4 requires. Josh decides whether a separate task bumps it.
- The runtime and test locks differ from beta.21 only in the `aiohttp` and
  `yarl` entries. Both were rendered by `scripts/prepare_build_inputs.py` from
  pip 25.0.1 reports for linux/amd64 and linux/arm64 on CPython 3.12.14.
  Re-rendering the test lock from the new runtime lock is byte-identical.
- `build_inputs.py source` passes with 39 distributions, and
  `build-inputs.json` records the new requirement and lock digests.

### Offline proof

No test reaches a live system. The peers are raw loopback sockets with
synthetic data, so they can send exact header bytes and frame sequences.

**`tests/test_hamcp211_aiohttp_3144_contract.py` (15 tests):**
- `InstalledVersionTests`: the test environment has aiohttp 3.14.4 and yarl
  1.25.1. This is a version expectation, not behaviour proof.
- `ContentEncodingWhitespaceTests`, through the REST client's logbook read:
  - the parser strips trailing whitespace from header values;
  - a missing, `identity`, `Identity` or whitespace-padded `identity` coding
    is read;
  - `gzip`, padded `gzip`, `br` and coding lists are refused with
    `HomeAssistantApiError`, each after exactly one request.
- `SingleAttemptTests`: a peer that drops every request sees exactly one
  request from the logbook read and from a single-read REST call, and both
  fail with `HomeAssistantUnavailableError`.
- `WebSocketFrameBoundTests`, through the bounded Alarmo inspection read:
  - a result exactly at the byte limit is read, whole or fragmented;
  - one byte over the limit, whole or fragmented, is `response_bytes`;
  - a new data frame or a control frame between fragments is
    `malformed_response`;
  - each sends exactly one command on one connection.
- `RouteQueryTests`:
  - golden URLs for entity, timestamp, query, empty-value, valueless and
    non-ASCII routes;
  - yarl 1.25.1's empty-field and invalid-UTF-8 query parsing;
  - a route whose raw text carries those forms is refused as
    `route_mismatch` even though its parsed query matches;
  - invalid UTF-8 path values are refused before any URL exists.

**Differential proof.** The same module was run against an environment with
beta.21's aiohttp 3.14.3 and yarl 1.24.5:
- the header-whitespace and yarl-query tests fail there, which shows they pin
  the changed behaviour;
- the WebSocket, single-attempt and golden-route tests pass on both versions.

A separate 9,440-case differential run of Engineering's route construction
gave identical results and URLs on yarl 1.24.5 and 1.25.1.

**Existing transport suites.** These 31 modules (772 tests) pass on both
versions, each in its own process. With the new module, 32 modules (787
tests) pass on the final tree. They cover:
- REST, WebSocket and logbook clients;
- HAMCP-200 path safety;
- the dependency and build-input index;
- approval and HAMCP-205 administrator verification;
- the real-HA contract.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence. The dependency audit must pass on the exact head.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.22;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. No v2.4.1-beta.21 tag, image or
release exists or is created. Retain the beta.20 artifacts and the
known-good earlier artifacts for recovery.

No stored format changes. Rollback is reinstalling the beta.20 artifact. It
restores aiohttp 3.14.3 with its advisories and removes HAMCP-205's
administrator verification, with the consequences the beta.21 contract
describes.

## 3. Installed Engineering

All installed checks below are read-only unless a step names Josh's explicit
authorization.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.22 tag;
   - the protected `main` merge commit that contains this PR.

   A source version alone does not prove the installed image.
2. **Dependency inventory.** From the published SBOM or the image inventory,
   confirm `aiohttp` 3.14.4 and `yarl` 1.25.1.
3. **Catalog unchanged.** Compare the public descriptors with beta.20, the
   last installed beta: the same tool count, input schemas and descriptions.
   Also compare:
   - Core signed selection;
   - storage and audit health;
   - F3 readiness;
   - zero fallback.
4. **Ordinary reads (read-only).** Run `get_server_health`, a bounded
   `get_logbook` read and a WebSocket-backed read such as `list_areas`. Each
   succeeds with no new error class.
5. **HAMCP-205 administrator verification.** Run steps 3 to 7 of
   [the beta.21 installed checks](V2_4_1_BETA21_ACCEPTANCE.md#3-installed-engineering)
   against beta.22, with the same authorization limits. Steps 6 and 7 still
   need Josh's explicit authorization.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
