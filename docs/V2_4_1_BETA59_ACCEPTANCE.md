# Engineering 2.4.1-beta.59 acceptance

This contract covers HAMCP-270: availability/exposure, delete, entity
metadata and helper options plans disclose scene, group and dashboard
references before approval, as information only. It follows 2.4.1-beta.58
and includes it.

See also the release notes:
[`V2_4_1_BETA59_RELEASE_NOTES.md`](V2_4_1_BETA59_RELEASE_NOTES.md).

**Release target.** `2.4.1-beta.59`, following 2.4.1-beta.58 (#342,
HAMCP-278). The release is materialized in all three version authorities,
with no staged declaration left. Nothing is published or deployed. If
another pull request takes beta.59 first, this one restages to the next
beta.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed read-only acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- the HAMCP-270 row in [`FEATURE_READINESS_MAP.md`](FEATURE_READINESS_MAP.md):
  disclosure only, no fingerprint change, no risk downgrade, and no promised
  dashboard coverage;
- the non-goals:
  - HAMCP-277 (dashboard live completeness);
  - HAMCP-273;
  - renames;
  - new capabilities;
  - signing;
  - `ci.yml`;
- `2.4.1-beta.59` materialized before Ready, with exact document resolution;
- the PR head SHA, its `validate` run and the Evidence tier log named in the
  PR.

The review needs a security section covering:
- **No new reach.** The sibling is built from the dependency snapshot already
  held, through `read_cached_dependency_snapshot`. It triggers no build or
  refresh, so it sends no new Home Assistant request. It adds no tool,
  schema, route, provider or write.
- **Signed profiles.** No Core profile contract or fingerprint changes. Profile
  contract material does not include plan output.
- **Bindings.** The sibling is projected only into dry-run results. It is not
  read by:
  - the consumer evidence fingerprint;
  - the plan hash;
  - the policy subject or decision hashes;
  - the semantic projection hash;
  - the approval binding.
- **Risk.** The sibling never raises or lowers risk or policy.

Required offline evidence, `tests/test_hamcp270_global_reference_disclosure.py`:
- each of the four disclosures shows the sibling with per-source
  completeness, the informational and membership statements, partial-result
  naming, and truthful partial or unavailable dashboards;
- binding invariance: adding, changing, withholding or removing global
  findings leaves bindings and the bound disclosure byte-identical, and an
  approved plan applies after a scene, group or dashboard change;
- risk is never downgraded, including for elevated plans;
- the size bound with large reference sets;
- an absent snapshot, a raising reader, an absent store and an unreadable
  store each give `unavailable` with no failure and no refusal;
- mutation testing in a copied tree, including a key-in-fingerprint mutant
  and a dropped-informational-statement mutant;
- the unchanged HAMCP-069a, 069b, 155, 165, 227 and 232 suites;
- the complete repository suite in the Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes, including the
beta.58 → beta.59 release transition.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow.

## 4. Installed read-only acceptance (separately authorized, after deploy)

The architect performs these steps against the deployed beta.59. Create
plans only; **never approve or apply them.** Change nothing in Home
Assistant.

1. Pick an entity that is a member of a scene or a group (for example from
   `ha_config_list_groups` or a scene's configuration).
2. Create one plan for it with `create_entity_availability_exposure_plan`
   (`operation: disable`), or a removal plan for a referencing automation or
   script with `create_configuration_delete_plan`. Do not approve it.
3. In the plan response, confirm that the operation's disclosure has a
   `global_source_consumers` sibling with:
   - `status: read`, `informational: true`, `binding: none` and
     `affects_risk: false`;
   - an item for that scene or group (`relation` ending `_member`);
   - per-source `completeness`, with `dashboard` shown as `partial` or
     `unavailable` and `template` as `unavailable`;
   - the statements: information only, loaded membership, and
     "Results may be partial: …" naming the incomplete sources.
4. Note the `plan_hash`. Read the plan again with `get_change_plan` and
   confirm that `plan_hash` and the policy decision are unchanged.
5. Leave the plan unapproved. It expires or is superseded with no effect.

PASS needs all of steps 3 and 4. If the sibling reports `unavailable`
because no dependency snapshot is held yet, that is truthful, not a failure.
Record it, and repeat once after the dependency index has built. If a step
fails, record the response in HAMCP-270. Whether to roll back is Josh's
decision.
