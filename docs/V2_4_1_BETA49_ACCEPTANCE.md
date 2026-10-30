# Engineering 2.4.1-beta.49 acceptance

This contract covers HAMCP-198 and HAMCP-120, the health warnings that
stayed lit. It follows 2.4.1-beta.48 and includes it.

See also:
- Release notes:
  [`V2_4_1_BETA49_RELEASE_NOTES.md`](V2_4_1_BETA49_RELEASE_NOTES.md).

**Release target.** `2.4.1-beta.49` is the next free beta after
2.4.1-beta.48 (#328, HAMCP-248). This branch merged `main` at beta.48 and
then materialized beta.49 with `scripts/promote_next_release.py --apply`;
`--validate-transition 2.4.1-beta.48 2.4.1-beta.49` passes. If another
release reaches `main` first, this change is restaged at the next free beta
and these documents are renamed.

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
- the mutation evidence under `docs/evidence/hamcp198-mutations/`,
  including one check-removal mutant per accounting check.

The review needs a security section covering:
- **No new reachability.** No Home Assistant write, service call, reload,
  provider, route, fallback or upstream dispatch. Projection outcomes,
  approval, apply, rollback and recovery are unchanged.
- **Bounded output.** At most eight `{plan_id, category}` rows; categories
  come from a fixed tuple; no record content, hash, reason code or exception
  text.
- **No masked tool.** `reviewed_tool_accounting_valid` is true only when
  every reviewed name (stock policy entry or bound optional reviewed tool)
  is advertised exactly once, nothing is missing or unreviewed, no optional
  sibling has drifted and every optional tool is in a recognized state. It is
  diagnostic only: admission, routing and exposure never read it.
- **Persistence.** No stored record or format changes.

Required offline evidence:
- `tests/test_hamcp198_projection_diagnostics.py` (every category, ordering,
  bound and truncation, no record contents, only listed plans examined,
  positive controls, unconfigured default shape);
- `tests/test_hamcp162_yaml_admission.py` (true for the exact 80/77/3 and
  stock shapes; false, in both admission modes, for a missing tool balanced
  by a duplicate, each sibling's drift, an unknown optional state, a missing
  read or non-read tool, a duplicate and an unreviewed extra, with health,
  exposure and dispatch unchanged when the flag is forced true; one
  single-check negative per helper check);
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
5. Manual review: Josh decided the warning stays lit (HAMCP-120); an
   acknowledgment workflow is tracked separately as HAMCP-253.
