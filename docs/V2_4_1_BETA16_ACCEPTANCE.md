# Engineering 2.4.1-beta.16 acceptance

This contract covers HAMCP-200: path-segment safety for every Home Assistant
REST call. It addresses independent static-scan findings 7 and 15 and builds
on 2.4.1-beta.15 (HAMCP-189). Release notes:
[`V2_4_1_BETA16_RELEASE_NOTES.md`](V2_4_1_BETA16_RELEASE_NOTES.md).

**Release target.** This change goes into the next Engineering beta.
- `2.4.1-beta.16` was staged in `.release/next-version`.
- It was then materialized in this PR by one owner-authorized run of
  `scripts/promote_next_release.py --apply`, which consumed the
  declaration.
- HAMCP-162 (PR #267) stages the same version. Whichever PR merges second
  must merge `main`, restage and rematerialize the next beta, and rename its
  acceptance document and release notes to match.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- `2.4.1-beta.16` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

### Offline proof: the central guard (`tests/test_hamcp200_path_safety.py`)

- `SegmentGuardTests`:
  - 22 traversal variants are refused before encoding:
    - `.` and `..`;
    - `/`, `\`, NUL, newline, `?` and `#`;
    - single-, double- and triple-encoded forms, including mixed case;
    - a dotted ID.
  - Non-string, empty, oversized and undecodable values are refused.
  - Valid IDs encode as exactly one segment.
  - Typed entity and timestamp segments allow only their grammar.
  - Query values cannot add parameters.
  - Templates must be fixed source routes.
  - A raw string is accepted only as a fixed literal route.
- `IdentifierGrammarTests`: the tool-boundary grammars accept valid IDs
  (numeric, UUID, underscore, mixed case, `legacy:import-01`) and refuse
  every traversal variant.
- `NormalizedRouteTests`:
  - `test_crafted_route_that_normalizes_elsewhere_is_refused`: a crafted
    route whose rendered path normalizes into `/hassio/addons` is refused
    as `route_mismatch`.
  - `test_central_guard_rechecks_values_however_the_path_was_built`: a
    route carrying a separator, built without the helper, is refused as
    `invalid_identifier`.
  - `test_forged_route_never_opens_a_session_or_attaches_the_token`.
  - `test_redirects_are_disabled_and_a_3xx_is_a_failure`: 301, 302, 307
    and 308 each raise `ha_api_error`, and `allow_redirects` is `False`.
  - `test_valid_request_sends_the_exact_template_url`.
- `ProviderBoundaryTests`:
  - The governed automation write and the configuration-resource write
    backstop are not dispatched for unsafe IDs.
  - Handoff skips unsafe Home Assistant-derived IDs and requests only the
    valid URLs.
  - Impact exact-state refuses an unsafe entity, all with zero requests.
- `ToolRefusalTests` run through the full application (gateway, audit,
  metrics). `get_automation_config`, `get_entity`, `get_history` and
  `automation_reliability_analysis` each refuse traversal variants as
  `invalid_request` with "The identifier is invalid.". Each refusal:
  - has a failure audit record;
  - adds one `invalid_request` count;
  - opens zero sessions and sends zero requests.

  Valid automation IDs read the exact `/api/config/automation/config/<id>`
  URL, and a valid `get_history` entity is one encoded query value.
- `SourceScanTests`:
  - The Engineering package has no raw interpolated request path.
  - Every named exemption is still present.
  - The scanner flags each of 12 raw forms and accepts guarded forms.
- `HarnessScriptTests`: every Home Assistant REST call in the disposable-Core
  harness scripts uses a fixed static route or a `rest_path` binding. The
  scan flags all 21 sites in the pre-fix harness scripts.
- `CompatibilityEntryPointTests`: `tools/compatibility.py` has no `main`,
  gateway, token bucket, audit writer or `__main__` guard, and the canonical
  `ha_mcp_engineering/__main__.py` remains.

### Mutation proof

Each mutation was applied to a copy of the source and the suite above was run
against it. Each one must fail:
1. Removing the central re-check loop in `request_url`.
2. Skipping the normalized path and query comparison.
3. Restoring the raw f-string
   `rest("GET", f"/config/automation/config/{automation_id}")` in
   `get_automation_config`. This fails both the source scan and the
   valid-ID read, because the transport refuses an unbound path.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.16;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.15 artifacts and the
known-good earlier artifacts for recovery.

No new persisted format is introduced, and beta.15 reads every record this
change writes.

## 3. Installed Engineering

All installed checks below are read-only unless a step names Josh's explicit
authorization.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.16 tag;
   - the protected `main` merge commit that contains this PR.

   A source version alone does not prove the installed image.
2. **Catalog unchanged.** Compare the public descriptors with beta.15:
   87 tools, the same input schemas and descriptions. Also compare:
   - Core signed selection;
   - storage and audit health;
   - F3 readiness;
   - zero fallback.
3. **Live read-only path check.** Through the public connector:
   1. List automations and choose one existing automation's configuration
      ID.
   2. Call `get_automation_config` with that ID. It returns that
      automation's configuration.
   3. Call `get_automation_config` with `../../hassio/addons`. It is refused
      as `invalid_request` with "The identifier is invalid.".
   4. Confirm that no Home Assistant request was sent: in `get_audit_log`,
      the record for that call has result `failure`, error code
      `invalid_request` and an empty `ha_endpoint_categories` list. The
      record for the valid read in step 2 lists `config/automation`.

   No write, reload or other live action is part of this check.
4. **Redirect refusal, provider-derived ID handling and the source scan**
   are proven offline only (§1).

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
