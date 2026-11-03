# Engineering 2.4.1-beta.60 release notes

Engineering 2.4.1-beta.60 (HAMCP-270) shows scene, group and dashboard
references in four governed plan disclosures before approval: entity
availability/exposure, automation and script delete, entity metadata, and
helper options. The references are information only. They are not bound by
the approval, and they never change risk, policy or approval. It builds on
2.4.1-beta.59 (#346, HAMCP-274). No tool, input schema, route, provider,
signed profile, fingerprint, plan or approval binding, persisted format,
add-on option or Home Assistant write changes.

Acceptance: [`V2_4_1_BETA60_ACCEPTANCE.md`](V2_4_1_BETA60_ACCEPTANCE.md).

## The change

- Each of those plans gets one `global_source_consumers` block (model
  `global_source_consumers_v1`) in its plan-level dry-run results, next to the
  bound consumer evidence of each operation. It is built from
  `global_sources.visible().findings` in the dependency snapshot the index
  already holds, read once per plan. Building it never triggers a build or
  refresh, so it sends no new Home Assistant request.
  - The block states the shared context once: status, per-source
    `completeness`, `reasons`, `statements` and `provenance` (fingerprint
    prefix and index generation).
  - `children` then lists each child in operation order: its `target` (or
    `targets` for a multi-entity child), `count`, nonzero `by_source` totals,
    `truncated`, and its top `items`.
  - **Scenes and groups** are listed by id and name. They are loaded
    membership, so the relation and list position are implied.
  - **Dashboards** show the url path, title, card location, rule and whether
    the card shows only under a condition (`conditional: true`).
  - **Completeness** is reported per source (`complete`, `partial`,
    `unavailable`), with the first reason for each incomplete source.
    Templates are listed as unavailable, so that gap stays visible.
    Dashboards are partial or unavailable live until HAMCP-277. When the
    dashboard provider authority has changed, cached dashboard evidence is
    withheld and reported as unavailable.
- **Statements.** The block says, once per plan:
  - the references are information only;
  - scene and group references are loaded membership;
  - while any source is incomplete, that a count of 0 is not proof of none;
  - when a list is cut, how many each child lists and that counts are totals;
  - that the bound note `dashboards_scripts_and_templates_not_scanned`
    describes the risk evidence, not these references.
- **Unbound by construction.** The block lives only in the projected dry-run
  results. It is excluded from the consumer evidence fingerprint, plan hash,
  policy subject and decision hashes, semantic projection hash and approval
  binding. A plan approved before a scene, group or dashboard change still
  applies.
- **Never a refusal.** If no snapshot is held, or the store is absent or
  unreadable, the block reports `status: unavailable` with a reason, each
  child names only its target (no count is claimed), and planning continues.
- **Distinct references stay distinct.** References are deduplicated and
  ordered on their full original source, target, location and relation
  before any display truncation. Two distinct references whose shortened
  displays collide stay separate; the later one gets a short `~N` id suffix.
- **Bounded.** One plan shares an 8-item budget across its children (at least
  1 each). Every string is length-bounded (ids 32, names 24, locations 32
  characters).

## Unchanged by design

The bound coverage notes are left as they were: the entity metadata
`coverage_gaps` entry `dashboards_scripts_and_templates_not_scanned`, and the
availability/delete coverage warnings. They are part of the consumer evidence
fingerprint or the policy subject hash, so rewording them would change plan
bindings. The block says those notes describe the risk evidence only.

## Where the block appears

Only in the create-plan response (`create_entity_availability_exposure_plan`,
`create_configuration_delete_plan`, `create_entity_metadata_plan`,
`create_helper_options_plan`, and the rollback plans built through them).
`get_change_plan` and `list_change_plans` strip dry-run results, so they do
not show it, and nothing else exposes it.

## Known limitation

A tool response is bounded at 60,000 characters. When a plan response is
larger, the existing bounded renderer drops the largest optional parts and
flags the response (`response_completeness.truncated: true`,
`approval_disclosures_complete: false`). Its retrieval hint names
`get_change_plan`, which returns the plan summary but not these references.

This happens to availability/exposure plans of eight entities, as it did
before this release, whatever the reference store holds. Plans of up to seven
entities fit with the block, with or without references (measured with the
store absent, empty, and about 120 references per entity).

## Rollback

Reinstall 2.4.1-beta.59. Plans keep their hashes and approvals, because the
block was never bound. No persisted format changed.
