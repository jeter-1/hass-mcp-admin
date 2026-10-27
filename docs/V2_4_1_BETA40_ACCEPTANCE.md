# Engineering 2.4.1-beta.40 acceptance

This contract covers HAMCP-226 and HAMCP-228. It follows 2.4.1-beta.39
(HAMCP-225, #315) and includes it. It adds one Engineering-native tool and
one Engineering-local store, and no Home Assistant write.

See also:
- Release notes:
  [`V2_4_1_BETA40_RELEASE_NOTES.md`](V2_4_1_BETA40_RELEASE_NOTES.md).
- Design and owner decisions:
  [`HAMCP_225_226_228_AUDIT_TOOLING_DESIGN.md`](HAMCP_225_226_228_AUDIT_TOOLING_DESIGN.md).
- The beta.39 contract this release keeps:
  [`V2_4_1_BETA39_ACCEPTANCE.md`](V2_4_1_BETA39_ACCEPTANCE.md).
- Feature contracts:
  [`AUTOMATION_BASELINE_CAPTURE.md`](AUTOMATION_BASELINE_CAPTURE.md),
  [`AUTOMATION_AUDIT_BASELINE.md`](AUTOMATION_AUDIT_BASELINE.md),
  [`AUTOMATION_BASELINE_CAPTURE_ACCEPTANCE.md`](AUTOMATION_BASELINE_CAPTURE_ACCEPTANCE.md),
  [`CONFIGURATION_INTEGRITY_ANALYSIS.md`](CONFIGURATION_INTEGRITY_ANALYSIS.md),
  [`ENTITY_DEPENDENCY_ANALYSIS.md`](ENTITY_DEPENDENCY_ANALYSIS.md).

**Release target.** `2.4.1-beta.40` is materialized by one owner-authorized
run of `scripts/promote_next_release.py --apply` on a base that carries
2.4.1-beta.39 (#315, merged in, never rebased). The run consumes the
declaration. Static tools 73; advertised catalog 101 (102 with the optional
YAML reader).

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- the owner decisions S (durable store: 12 baselines, 180 days), T (resume
  retries timeouts only), B (limits as written) and P (separate from #315);
- the architect direction Josh agreed on 2026-10-26: `PREFIX_MATCH` is its
  own bucket, an id on one side only against recorded fingerprints is
  `UNKNOWN`, and the store lives under `governance_path`;
- Josh's 2026-10-26 direction that Home Assistant ids and names pass the
  default-mode credential screen;
- `2.4.1-beta.40` materialized in all three version authorities, with exact
  document resolution and no staged declaration left over;
- the PR head SHA, its passing `validate` run, and the Evidence tier log
  named in the PR;
- the mutation evidence under `docs/evidence/` named in the PR.

The review needs a security section covering:
- **One new Engineering-local write.** The capture writes one bounded
  record per finished capture under `<governance_path>/audit-baselines-v1/`
  (0600 files, 0700 directories, `O_NOFOLLOW`, temporary file plus `fsync`
  plus rename, nonblocking `flock`). It stores no configuration body. No
  Home Assistant write, service call, reload, provider, fallback or
  upstream dispatch is added.
- **Caller binding.** Pages, resume and compare read only the caller's own
  baselines; another caller's is `baseline_not_found`; `anonymous` is
  refused.
- **Integrity.** Every read checks size, SHA-256, `artifact_sha256` and the
  validator; damage deletes the record and leaves a content-free stub.
  Hashes detect damage, not a writer able to rewrite record and index.
- **Capacity.** At 12 a capture is refused before any Home Assistant read;
  nothing is evicted.
- **Credential screen.** Retained baseline identifiers and the whole compare
  output pass the default-mode screen; the PEM header check is a stricter
  addition to default mode. The HAMCP-226 margin property: a value is
  screened over its shown part plus 1,024 characters before any cut.
- **Compare semantics.** `PREFIX_MATCH` never counts as `UNCHANGED`;
  recorded fingerprints carry no inventory authority.
- **Classification.** `capture_automation_baseline` changes to
  `readOnlyHint=false` (Engineering local evidence capture);
  `compare_automation_baselines` is read-only.

Required offline evidence:
- `tests/test_hamcp228_baseline_store.py` (store bounds, sweep, quarantine,
  symlinks, modes, caller binding, busy and commit-unknown refusals;
  resume success and every refusal before reads; v2 validator negatives;
  compare buckets, prefix lengths 16/17/63/64, model mismatch, bounded
  output; credential matrix; beta.37 v1 fixture; public tool contract);
- `tests/test_hamcp226_named_sources.py`;
- the existing capture, transport, comparator and catalog suites;
- the complete repository suite in the Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes.

## 3. Publication

Only through Josh's Ready action and the repository's head-bound workflow.

## 4. Installed Engineering acceptance (separately authorized)

Follow [`AUTOMATION_BASELINE_CAPTURE_ACCEPTANCE.md`](AUTOMATION_BASELINE_CAPTURE_ACCEPTANCE.md),
including its HAMCP-228 section: one ordinary capture stored under the
governance path; one compare against recorded fingerprints from that
capture's digests showing `UNCHANGED` and `PREFIX_MATCH`, with zero Home
Assistant reads; `resume_not_needed` for a capture with no timeouts; one
`configuration_integrity_analysis` and one `entity_dependency_analysis`
call showing `bounded_sources` and `target_availability`. Do not
manufacture timeouts, a full store, a changed inventory or a credential on a
live installation.
