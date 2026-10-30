# Engineering 2.4.1-beta.49 acceptance

Beta 49 stages HAMCP-198 and HAMCP-120. Engineering continues to advertise
2.4.1-beta.47 on this branch; `.release/next-version` declares
`2.4.1-beta.49` and is not materialized.

See also:
- Release notes:
  [`V2_4_1_BETA49_RELEASE_NOTES.md`](V2_4_1_BETA49_RELEASE_NOTES.md).

**Release target.** `2.4.1-beta.49` is materialized by one owner-authorized
run of `scripts/promote_next_release.py --apply` on a base that carries
2.4.1-beta.48 (HAMCP-248, #328). Merge `origin/main` in first (never
rebase) and rerun Evidence. If another release merges first, restage at the
next free version and rename these documents.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- Josh's HAMCP-198/HAMCP-120 direction: health accuracy over quietness; each
  cleared warning is explained by a fixed, documented classification; no
  change to approval, execution, dispatch, locks, policy decisions, stored
  formats, tool count or input schemas;
- `2.4.1-beta.49` materialized in all three version authorities, with exact
  document resolution and no staged declaration left over;
- the PR head SHA, its passing `validate` run, and the Evidence tier log
  named in the PR;
- the mutation evidence under `docs/evidence/hamcp198-mutations/`.

The review needs a security section covering:
- **No new reachability.** No Home Assistant write, service call, reload,
  provider, route, fallback or upstream dispatch. Projection outcomes,
  approval, apply, rollback and recovery are unchanged.
- **Bounded output.** At most eight `{plan_id, category}` rows; categories
  come from a fixed tuple; no record content, hash, reason code or exception
  text.
- **No masked tool.** `reviewed_tool_accounting_valid` is true only when
  every advertised tool is a stock policy entry or a bound optional reviewed
  tool and none is unreviewed.
- **Persistence.** No stored record or format changes.

Required offline evidence:
- `tests/test_hamcp198_projection_diagnostics.py` (every category, ordering,
  bound and truncation, no record contents, only listed plans examined,
  positive controls, unconfigured default shape);
- `tests/test_hamcp162_yaml_admission.py` (true with the optional model; false
  with an unaccounted advertised tool, with and without the reader);
- the existing historical-projection and governance-health suites;
- the complete repository suite in the Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes.

## 3. Publication

Only through Josh's Ready action and the repository's head-bound workflow.

## 4. Installed Engineering acceptance (separately authorized)

1. `get_server_health`: `governance.projection_failure_count` is unchanged
   (2 on the beta.46 store) and the warning is still populated;
   `projection_failures` lists those two plan IDs with categories from the
   fixed vocabulary; `projection_failures_truncated` is false.
2. `upstream_read_gateway.reviewed_tool_accounting_valid` is true with
   `upstream_advertised_tool_count` 80 and
   `optional_reviewed_accounting_valid` true; `reviewed_policy_entry_count`
   is still 77.
3. `execution_tasks.tasks_manual_review` and
   `manual_review_terminalization_count` are unchanged (1 each).
4. The architect retrieves the two listed plans read-only. Each is then
   dispositioned in HAMCP-198 as a current defect (fix with a regression
   test) or an immutable historical record (bounded historical profile,
   `authorization_effect: none_projection_only`, keyed to the exact stored
   shape) in a later release.
5. Josh decides whether a manual-review acknowledgment workflow is wanted
   (HAMCP-120).
