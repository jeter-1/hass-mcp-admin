# Engineering 2.4.1-beta.39 acceptance

This contract covers automation trace snapshots and watched automations
(HAMCP-225, audit finding HAAC-E001), including the trace-read part of
HAMCP-194 folded into it (owner decision O2). It follows 2.4.1-beta.38 and
includes it. The release adds four Engineering-native tools and one
Engineering-local store. It adds no Home Assistant write, no Core capability
profile, no signed registry, no delegated provider and no fallback.

See also:
- Release notes:
  [`V2_4_1_BETA39_RELEASE_NOTES.md`](V2_4_1_BETA39_RELEASE_NOTES.md).
- Design: [`HAMCP_225_226_228_AUDIT_TOOLING_DESIGN.md`](HAMCP_225_226_228_AUDIT_TOOLING_DESIGN.md)
  (owner decisions at the top and in section 8; HAMCP-225 in section 2;
  implementation note in section 10).
- The beta.38 contract this release keeps:
  [`V2_4_1_BETA38_ACCEPTANCE.md`](V2_4_1_BETA38_ACCEPTANCE.md).

**Release target.** `2.4.1-beta.39` is staged by one owner-authorized run of
`scripts/promote_next_release.py --apply` on a base that carries
2.4.1-beta.38. The run consumes the declaration.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's decisions of 2026-10-26: O1, O2, O3 and the Part 2 decisions W1,
  E1 + E2, R1, B and P, plus the watch-list management addition (design note,
  top and section 8);
- `2.4.1-beta.39` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

The review needs a security section covering:
- **No Home Assistant write.** The only new Core commands are `trace/list`
  and `trace/get` with `domain: "automation"`, the same command shapes
  `list_automation_traces` and `get_automation_trace` already send. No
  `domain: "script"`, service call, reload, restart or deletion becomes
  reachable. The new writes are Engineering-local only (risk
  `engineering_evidence_write`).
- **One sanitized exit, one shared screen (review H1).**
  `get_automation_trace` output passes `sanitize_trace_detail` once: first the
  shared `credentials.screen` (the HAMCP-132 and HAMCP-224 pipeline, labelled
  forms) on every string and every dictionary key at every depth, then the
  HAMCP-191 strict sanitizer, with truncation last. A flagged key withholds its
  whole entry; nesting beyond 64 levels is withheld as unverifiable. There is
  no trace-specific rule. Capture screens before anything is stored, then runs
  one strict pass over the complete record. Read-back re-screens each page
  envelope (R1 strict). Any withholding makes the result `partial` with its
  categories.
- **Identifiers (review H2).** An automation or run ID the screen flags is
  replaced by `withheld_` and a per-process HMAC digest in the index, records,
  watch list, audit rows, results and refusals; the gateway audit records
  such an ID as `null` with `identifiers_withheld: true`. The reserved form is
  refused as input. After a restart, a watch on a withheld ID ends with an
  `identifier_unavailable` audit row.
- **Store integrity.** `<governance_path>/trace-snapshots-v1/`: `flock`,
  `O_NOFOLLOW`, mode `0600`, temporary file plus `fsync` plus `rename`,
  digest-checked loads, a fail-closed index, and fixed diagnostics with no
  payload. Bounds: 200 snapshots, 256 KiB each, 32 MiB, 90 days, 5 runs a
  capture. A damaged record is deleted and leaves a content-free quarantine
  stub; at most 20 stubs, aged on the 90-day rule, oldest dropped first and
  counted (review H3).
- **Capacity (E1 + E2).** A manual capture at capacity is refused
  (`store_full`). A watch evicts only its own oldest snapshot beyond its
  20-snapshot quota; manual snapshots are never evicted. Every eviction is
  audited.
- **Watch (W1).** At most 10 watches, each expiring within 168 hours. The
  poller is the only periodic Core read: one `trace/list` per watch every 30
  seconds, `trace/get` only for new stopped runs matching the mode, under
  its own Core authority lease. It pauses while authority is unavailable or
  the version is not admitted; missed runs are counted, not backfilled.
  The first poll captures runs that finished after the watch was created
  (review M1). Each watch gets an equal share of the remaining read time and
  the start order rotates every tick (M2). Authority is re-checked before a
  watch's reads are committed; after retirement nothing is stored and the
  poller pauses (M3).
  Managing the list needs no plan or approval; every add and remove writes
  a required audit row with the caller, and every response returns the
  current list.
- **Caller binding.** List and read cursors are HMAC-bound to the caller and
  end at a server restart.

Preserve the following:
- stable v1.1.2;
- every existing persisted format (the trace snapshot store is new);
- every existing descriptor and input schema except the one reviewed
  `get_automation_trace` description disclosure (its input schema is
  unchanged); the catalog is **100 tools, 101 with the optional YAML reader
  admitted** (static 72);
- probe fingerprints, the signed Core capability profiles, the owner-signed
  Core registry, the signed ha-mcp registry and the delete profile's
  contract fingerprint, recomputed at base and head;
- server health output;
- the HOLD and the support window;
- add-on permissions, dependencies and every workflow.

### Offline proof

No test reaches a live system. Every transport is a synthetic fake.

- `tests/test_hamcp225_trace_snapshots.py`: capture by run id and by
  selection; every outcome (`captured`, `already_captured`,
  `trace_not_retained`, `run_in_progress`, `over_size_bound`, `store_full`,
  `read_failed`); age expiry; quarantine; symlink refusal; corrupt-index
  refusal; caller binding and cursor tampering; Core unavailable and
  authority drift refusals; synthetic needles planted in the trace fields
  and the note, checked against every stored byte, the capture result, the
  list and every read page; the sanitized and partial
  `get_automation_trace` response and a clean complete one; the poller's
  first-poll baseline, new-run capture, mode filters, pause without
  authority, gap count, its own Core lease, watch expiry, quota eviction
  (E2) and a manual snapshot surviving a full watch quota; watch
  add, replace, remove and list with audit rows, and refusal when the audit
  is unavailable; negative command-shape checks (automation domain only).
- `tests/test_hamcp225_cross_path_credentials.py`: every credential case
  from the HAMCP-132 reviewer suites (`test_pr294_delta_review`,
  `test_pr294_corr2_extra`, `test_pr294_final32_review`) and the HAMCP-224
  reviewer suites (`test_pr313_independent_review`,
  `test_pr313_delta_residuals`, `test_review2_ansi_residual`), 123 cases, as a
  value and as a key in 12 trace positions, through the capture path (never
  on disk, never returned, partial) and through `get_automation_trace`.
- `tests/test_hamcp225_review1_{reviewer,store,wire}_probes.py`: the
  independent reviewer's reproductions, unchanged, including the cross-watch
  eviction test.
- `tests/test_hamcp191_shared_sanitizer.py`: the new `SANITIZER_SITES`
  entries.
- Catalog, routing and Core-gate pins updated to 72 static and 100 total
  (`tests/test_hamcp164_core_gate_fail_closed.py` classifies the four new
  tools).
- `docs/evidence/hamcp225-mutations/`: the review-1 mutation runner and its
  results. All 18 mutants were killed: the shared screen skipped, a
  trace-specific screen, keys not screened, screening after storage, an
  unscreened `get_automation_trace`, a raw identifier kept, a raw gateway
  audit identifier, the digest form accepted as input, unbounded quarantine,
  first-poll discard, no rotation, no per-watch budget, no authority recheck
  before commit, cross-watch eviction, and four design §7 mutants. Raw
  per-mutant logs are not committed because a surviving leak would print
  planted values.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge handoff.
Verify the merged source, the release tag, the immutable version and commit
images, provenance and SBOM with the shipped verifiers. Never invent future
image identities. Retain the beta.38 artifacts and the known-good earlier
artifacts for recovery.

Rollback is reinstalling the beta.38 artifact. Beta.38 does not read the new
`trace-snapshots-v1/` namespace; it stays on disk, unused, until a
re-upgrade.

## 3. Installed Engineering

Separately authorize installation. Checks 1 and 2 are read-only; checks 3 and
4 are owner-approved live canaries.

1. **Installed-image binding.** Bind the running add-on to the published
   image index digest, the release tag and the protected `main` merge commit.
2. **Catalog.** `tools/list` shows 100 tools (101 with the YAML reader
   admitted), including the four trace snapshot tools.
3. **Capture canary.** With Josh's approval, capture one test automation's
   retained traces with `capture_automation_trace`. The result lists each
   run's outcome; `list_trace_snapshots` shows the snapshots; every page of
   `get_trace_snapshot` reads back; a second capture reports
   `already_captured`.
4. **Watch canary.** With Josh's approval, `set_automation_trace_watch`
   `add` on a test automation, `list`, then `remove`. Each response shows
   the current list, and the audit log shows one `trace_watch_change` row
   for the add and one for the remove, each with the caller.

## 4. Out of scope

HAMCP-226 and HAMCP-228 (a later pull request), script traces (O1), the rest
of HAMCP-194, any registry signing and any downgrade are not authorized by
this document.
