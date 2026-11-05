# Engineering 2.4.2-beta.1 acceptance

This contract covers HAMCP-301 Option A: per-record attribution of rejected
expand-snapshot state records, so that a rejected record outside `group`,
`zone` and the target's domain no longer holds helper plans for group
membership or turns an absent `expand()` member into a coverage failure. It
follows 2.4.1-rc.1 (HAMCP-302, #359).

See also the release notes:
[`V2_4_2_BETA1_RELEASE_NOTES.md`](V2_4_2_BETA1_RELEASE_NOTES.md).

**Release target.** `2.4.2-beta.1`, following 2.4.1-rc.1. The release
transition accepts only `2.4.1-rc.1 -> 2.4.2-beta.1` from the current base.
#361 (HAMCP-306) also claims 2.4.2-beta.1. Merge order is fixed when Codex
review marks a PR ready; only the next-in-line PR runs final Evidence. If
#361 merges first, this change merges `main` (merge commit), restages once to
2.4.2-beta.2 and runs final Evidence once (Josh's decision, 2026-11-04). The
release is materialized in all three version authorities, with no staged
declaration left. Nothing is published or deployed.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed acceptance, plan-only.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- the HAMCP-301 diagnosis and Josh's Option A decision (2026-11-04) on PR
  #360;
- the pinned Core 2026.10.3 source: `homeassistant/core.py:180-183` (entity
  id grammar), `helpers/template/extensions/state.py:145-185` (`expand`) and
  `const.py:461` (`ATTR_PERSONS`);
- the non-goals: no change to `valid_entity_id`, the obligation ledger, the
  semantics registry, policy classes, exact-obligation handling, action
  profiles, coverage or bounded-source logic, tools or schemas;
- the three protected runtime files, each declared with
  `-AuthorizedProtectedPath`: `dependency/provider.py`
  (`_build_expand_snapshot_evidence`), `governance/helper_dependency.py`
  (`_group_membership_hold`) and `dependency/extraction.py` (expand
  resolution);
- the PR head SHA, its `validate` run and the Evidence tier log named in the
  PR.

The review needs a security section covering:
- **No new reachability.** No tool, route, provider dispatch, service call or
  Home Assistant write is added. The change can only remove a group hold or
  turn a coverage failure into a bounded opaque obligation; it never makes a
  target exact or dependency-neutral.
- **Fail closed.** Every rejected record that cannot be attributed, and every
  over-bound state list, keeps the earlier hold and coverage failure.
- **Disclosure.** The new evidence holds attributed ids, a count and a
  fingerprint, and no attribute value.

Required offline evidence is in `tests/test_hamcp301_group_attribution.py`:
- released: the live digit-only ids `binary_sensor.1_1_1_1` and
  `binary_sensor.8_8_8_8` (attributes without `entity_id`) for input_boolean
  and input_select targets; `expand(group.x)` with resolvable members is
  decided;
- held: a rejected `group.*`, `zone.*` (with persons) or target-domain id;
  any rejected record with an `entity_id` attribute; non-dict attributes or
  records; a non-string or Core-invalid id; a duplicate `group.*` id; a
  duplicate of a group-sourced record; an over-bound snapshot;
- unchanged: a real group member stays held; `guest_mode` stays
  `safety_critical` with seven exact obligations and an unrelated rejected
  record present (the beta48 and beta54 tests are unchanged);
- live-shaped: an `engineering_acceptance`-shaped helper loses
  `group_membership_unresolved` and is still `elevated_admin`;
- mutation testing in a copied tree, counting only assertion failures as
  kills;
- the complete repository suite in the Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes, including the
release transition.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow.

## 4. Installed acceptance, plan-only (separately authorized, after deploy)

This stage runs only after Josh separately authorizes deploying this release.
It is plan-only: nothing is approved or applied.

The architect creates two unapproved plans:

| Helper | Expected |
|---|---|
| `input_boolean.engineering_acceptance` | `elevated_admin`; no `group_membership_unresolved` or `group_membership_consumers_unattributed` in the uncertainty codes; no "Group membership keeps this plan elevated" warning; `opaque_downstream_obligations` still present |
| `input_boolean.guest_mode` | `safety_critical` physical consequence, unchanged from 2.4.1-rc.1 |

Both plans are left unapproved and expire. PASS needs both rows. If a row
fails, record the response in HAMCP-301. Whether to roll back is Josh's
decision.
