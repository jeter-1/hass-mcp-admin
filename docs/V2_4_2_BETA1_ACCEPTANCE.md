# Engineering 2.4.2-beta.1 acceptance

This contract covers HAMCP-306: `get_dashboard_config` checks a dashboard read
against the response limit using the limit's own measure, and reports that
same measure. It follows 2.4.1-rc.1 (HAMCP-302, #359).

See also the release notes:
[`V2_4_2_BETA1_RELEASE_NOTES.md`](V2_4_2_BETA1_RELEASE_NOTES.md).

**Release target.** `2.4.2-beta.1`, following 2.4.1-rc.1. A release
candidate may advance to beta.1 of the next patch. HAMCP-301 is being
developed at the same time and also targets 2.4.2-beta.1. Merge order is
fixed when a PR reaches Ready. A PR that merges second merges `main` with a
merge commit, restages once to the next free beta, and then runs final
Evidence once. The release is materialized in all three version
authorities, with no staged declaration left. Nothing is published or
deployed.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed read-only acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- the HAMCP-306 contract (architect, 2026-11-04) and the decision to run
  HAMCP-306 and HAMCP-301 in parallel as the first 2.4.2 work (Josh,
  2026-11-04);
- the cause, with file and line at the 2.4.1-rc.1 base `38b29a3f`:
  `providers/upstream_dashboard.py:608-610` checked
  `len(json.dumps(safe_config, indent=2)) + RESPONSE_ENVELOPE_RESERVE`
  (16,000, `:167`). `:595` and `:617`/`:645` reported the raw configuration's
  compact size instead;
- the non-goals:
  - no change to the dashboard write tools, plan or readback hashes, the
    dependency index or global response truncation;
  - no schema change;
  - no paging or partial reads (RCG-045, HAMCP-273);
- `2.4.2-beta.1` materialized before Ready, with exact document resolution;
- the PR head SHA, its `validate` run and the Evidence tier log named in the
  PR.

The review needs a security section covering:
- **No new reachability.** No new tool, route, provider dispatch, write or
  fallback. A read that was refused before now returns the sanitized
  configuration through the same provider, admission and redaction path,
  with one upstream dispatch.
- **Inherited threshold.** The F3 plan preread, the post-apply readback and
  the governance metadata fingerprint reader use the same provider read. A
  dashboard that fits the response can now be planned against. The
  unchanged F3 configuration cap (40,000 bytes), the patch cap
  (16,384 bytes), the hashes and the approval bindings still apply.
- **Bounded output.** The estimate is never below the real response's
  compact JSON, and exceeds it by at most 84 characters. So a returned
  configuration is never truncated and the returned text never exceeds the
  limit; that text is the indented JSON only when the indented form also
  fits. The refusal carries no configuration.

Required offline evidence is in `tests/test_hamcp306_dashboard_read_size.py`:
- a ~30 KB nested dashboard, which the old check refused, is returned with
  both hashes and is complete;
- an oversized dashboard is refused truthfully: the reported estimate is
  above the limit and equals the success-path measure of the same response;
- the reported size is the exact admission threshold: a limit equal to the
  estimate returns, and one less refuses with the same estimate;
- the boundary at the default 60,000 limit: estimates of 59,999 and 60,000
  return, and 60,001 refuses;
- the estimate against the real compact response, for small, ~30 KB,
  non-ASCII-heavy and near-limit fixtures, without and with a request
  context, a 128-character request ID, and the growing timing members at
  their narrowest and widest: never below it, at most 84 characters above
  it (0 at the widest, exactly 84 at the narrowest);
- a complete response of 59,612 compact characters is returned (Codex R1);
- real compact responses of 59,999 and 60,000 return and 60,001 refuses
  when the growing members are at full width; a real response of 59,916
  returns when they are at their narrowest; a real response of 60,001 is
  refused with an estimate above the limit;
- the sanitized configuration is what is measured;
- regression: the dashboard read, write, readback and dependency-scan suites
  pass. One fixture changed: the refusal-shape test in
  `tests/test_rc3a_dashboard_provider.py` used one 10,000-character card
  against a 6,000 limit. The sanitizer bounds that string, and its
  sanitized response (5,637 compact characters) now fits and is returned
  correctly. The test now uses three such cards and also asserts that the
  reported size is above the limit;
- mutation testing in a copied tree, counting only assertion failures as
  kills, for a wrong measure, an off-by-one at the boundary, a reported
  number that differs from the checked one, a growing timing member left
  unwidened, a narrower float width, a substituted request ID, a changed
  summary, and an added reserve;
- the complete repository suite in the Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes, including the
release transition.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow.

## 4. Installed read-only acceptance (separately authorized, after deploy)

This stage runs only after Josh separately authorizes deploying this
release.

1. **Identity.** `server_info` and `get_server_health` report
   `2.4.2-beta.1` (or the beta this change restaged to), the merged release
   SHA as build SHA and `dirty: false`.
2. **The defect.** The architect reads `home-admin-dashboard` read-only with
   `get_dashboard_config`. Confirm:
   - `success: true` and `configuration_returned: true`;
   - a `configuration` body with `config_hash` and
     `engineering_config_hash`;
   - `estimated_serialized_size` is at most `response_limit` (60,000).
     Expected near 32,000–34,000: the rc.1 refusal's raw compact size of
     29,907 plus the wrapper. This is an estimate, not a pass threshold;
   - `config_hash` matches the hash the rc.1 refusal reported
     (`f692af48683ac6f1`) if the dashboard has not changed since.
3. **Tool catalog.** A fresh `tools/list` shows the same tool names and
   descriptors as 2.4.1-rc.1.

PASS needs every check. If a step fails, record the response in HAMCP-306.
Whether to roll back is Josh's decision.
