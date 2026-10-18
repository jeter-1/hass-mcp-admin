# Engineering 2.4.1-beta.17 acceptance

This contract covers HAMCP-200: every Home Assistant REST path is a typed,
registered route. It addresses independent static-scan findings 7 and 15 and
Codex review findings M1 and M2, and builds on 2.4.1-beta.16 (HAMCP-162).
Release notes:
[`V2_4_1_BETA17_RELEASE_NOTES.md`](V2_4_1_BETA17_RELEASE_NOTES.md).

**Release target.** This change goes into the next Engineering beta.
- This PR first staged and materialized `2.4.1-beta.16`. HAMCP-162 (PR #267)
  merged that version to `main` first.
- This PR then merged `main` with a merge commit and was restaged as
  `2.4.1-beta.17` by one owner-authorized run of
  `scripts/promote_next_release.py --apply`, which consumed the declaration.
  HAMCP-162's beta.16 release notes and acceptance document are unchanged.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- `2.4.1-beta.17` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

### Past-exposure determination (M1)

Before this change a crafted identifier could make Engineering send
token-bearing GET requests to other HA/Supervisor routes, including any
GET-enabled webhook (which can trigger automations); no POST/PUT/DELETE/PATCH
was reachable. The POST builders with a variable segment took only
pre-validated or operator-configured values. Whether any deployed webhook
allowed GET was not observed. The release notes say where a webhook
trigger's allowed methods are set and how to check them without Engineering.

### Offline proof (`tests/test_hamcp200_path_safety.py`, 59 tests)

- `SegmentGuardTests` (8):
  - 22 traversal variants are refused before encoding: `.` and `..`; `/`,
    `\`, NUL, newline, `?` and `#`; single-, double- and triple-encoded
    forms, including mixed case; a dotted ID.
  - Non-string, empty, oversized and undecodable values are refused.
  - Valid IDs encode as exactly one segment; typed entity and timestamp
    segments allow only their grammar; query values cannot add parameters.
  - Templates must be fixed source routes, and a well-formed template
    outside `ROUTE_TEMPLATES` (`/webhook/{}`, `/states/sensor/extra`,
    `/services/{}/{}` and others) is `unregistered_route`.
  - Every plain string is refused as `unbound_request_path`, including a
    registered static route such as `"/states"`, `str(route)`,
    `route + ""` and non-string values.
- `RouteForgeryTests` (5): a `Route` cannot be constructed or subclassed; a
  `str.__new__(Route, ...)` forgery is not issued; an issued route cannot be
  mutated, and copying returns the same object while pickling raises for
  every protocol; every string operation yields a refused plain `str`;
  attribute tampering with `object.__setattr__` is refused centrally.
- `WebhookReachabilityTests` (4):
  - No registered template names a webhook, and every template root is in
    the reviewed set.
  - For every template and every crafted webhook or traversal ID, the value
    is refused or the URL stays exactly within its template.
  - Crafted webhook IDs open zero client sessions, through `rest_path` or
    as a raw path.
  - The pre-fix normalization target is refused by the URL guard even when
    the encoder is bypassed.
- `IdentifierGrammarTests` (1): the tool-boundary grammars accept valid IDs
  (numeric, UUID, underscore, mixed case, `legacy:import-01`) and refuse
  every traversal variant.
- `NormalizedRouteTests` (6): route text that disagrees with its binding is
  `route_mismatch`; a route whose path normalizes into `/hassio/addons` is
  refused; values are re-checked however the route was built; a forged
  route never opens a session or attaches the token; 301, 302, 307 and 308
  each raise `ha_api_error` with `allow_redirects=False`; a valid request
  sends the exact template URL.
- `RuntimeProbeTests` (2): eleven bypass forms (alias prefix, helper
  wrapper, `.format`/`.format_map` aliases, a dynamic template, receiver and
  `getattr` request aliases, `functools.partial`, a keyword path, an unsafe
  binder and a static plain string) are each refused with zero sessions;
  the same requests succeed only through `rest_path`.
- `ProviderBoundaryTests` (4): the governed automation write and the
  configuration-resource write backstop are not dispatched for unsafe IDs;
  handoff skips unsafe Home Assistant-derived IDs and requests only the
  valid URLs; impact exact-state refuses an unsafe entity, all with zero
  requests.
- `ToolRefusalTests` (7), through the full application (gateway, audit,
  metrics): `get_automation_config`, `get_entity`, `get_history` and
  `automation_reliability_analysis` each refuse traversal variants, and
  `get_automation_config` and `get_entity` refuse crafted webhook IDs, as
  `invalid_request` with "The identifier is invalid.". Each refusal has a
  failure audit record, adds one `invalid_request` count, and opens zero
  sessions. Valid automation IDs read the exact
  `/api/config/automation/config/<id>` URL, and a valid `get_history`
  entity is one encoded query value.
- `SourceScanTests` (8): the Engineering package has no raw interpolated
  request path; every named exemption is present; the runtime uses exactly
  `ROUTE_TEMPLATES` and names or imports no harness route; websockets connect only
  to the configured URL; the scanner flags each raw form and each Codex
  counterexample, and accepts guarded forms.
- `HarnessScriptTests` (3): every Home Assistant REST call in the
  disposable-Core harness scripts binds through `rest_path` with a literal
  runtime template or through `harness_request` with a literal harness
  template; the harness sends each harness route exactly as many times as
  before the move; the scanner flags the forms the transport refuses,
  including a fixed raw `"/config"` and a non-harness `harness_request`.
- `HarnessRouteIsolationTests` (3): the runtime and harness registries are
  disjoint and `clients/paths.py` exposes no harness registry; `rest_path`
  refuses every harness route; the runtime REST client refuses each harness
  route as a plain string, a URL object and a forged `Route`, with zero
  sessions and zero requests.
- `HarnessSenderTests` (4): each harness route sends exactly
  `http://127.0.0.1:8123/api<route>` with the same headers, JSON body and
  disabled redirects as before; a non-harness route, a non-loopback Core or
  a call inside an Engineering request is refused with zero sessions; a
  redirect is an error, not followed; a timeout or connection failure
  raises the runtime client's typed error.
- `AddOnPackagingTests` (2): no file the Dockerfile copies names a harness
  route, registry or sender; an isolated (`python -I`) copy of the image
  package cannot import the harness module, and its `rest_path` refuses
  every harness route.
- `CompatibilityEntryPointTests` (2): `tools/compatibility.py` has no
  `main`, gateway, token bucket, audit writer or `__main__` guard, and the
  canonical `ha_mcp_engineering/__main__.py` remains.

### Mutation proof

Each mutation was applied to a disposable copy of the source and the suite
above was run against it. Each one must fail:
1. `require_route` accepting a plain `str` again.
2. `is_route` dropping the issued-route registry check.
3. Removing the re-encode comparison in `request_url`.
4. Skipping the normalized URL comparison.
5. Restoring the raw f-string in `get_automation_config`.
6. Enabling redirects in the REST transport.
7. Dropping the template-registry check.
8. Allowing `Route` construction.
9. The runtime registry check accepting the four harness routes again.
10. Restoring a harness registry in `clients/paths.py`.
11. Changing the harness URL (a trailing slash).
12. Dropping the harness loopback check.
13. Dropping the harness request-context check.
14. Enabling redirects in the harness sender.
15. Dropping the harness sender's typed timeout and connection errors.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.17;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.16 artifacts and the
known-good earlier artifacts for recovery.

No new persisted format is introduced, and beta.16 reads every record this
change writes. Rollback is reinstalling the beta.16 artifact.

## 3. Installed Engineering

All installed checks below are read-only unless a step names Josh's explicit
authorization.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.17 tag;
   - the protected `main` merge commit that contains this PR.

   A source version alone does not prove the installed image.
2. **Catalog unchanged.** Compare the public descriptors with beta.16: the
   same tool count (88 with `ha_config_get_yaml` admitted, otherwise 87),
   input schemas and descriptions. Also compare:
   - Core signed selection;
   - storage and audit health;
   - F3 readiness;
   - zero fallback.
3. **Live read-only path check.** Through the public connector:
   1. List automations and choose one existing automation's configuration
      ID.
   2. Call `get_automation_config` with that ID. It returns that
      automation's configuration.
   3. Call `get_automation_config` with `../../hassio/addons`, then with
      `../../../webhook/probe`. Each is refused as `invalid_request` with
      "The identifier is invalid.".
   4. Confirm that no Home Assistant request was sent: in `get_audit_log`,
      the records for the step 3 calls have result `failure`, error code
      `invalid_request` and an empty `ha_endpoint_categories` list. The
      record for the valid read in step 2 lists `config/automation`.

   No write, reload or other live action is part of this check.
4. **Redirect refusal, route forgery, provider-derived ID handling and the
   source scan** are proven offline only (§1).

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
