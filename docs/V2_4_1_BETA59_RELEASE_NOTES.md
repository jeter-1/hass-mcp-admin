# Engineering 2.4.1-beta.59 release notes

Engineering 2.4.1-beta.59 (HAMCP-270) shows scene, group and dashboard
references in four governed plan disclosures before approval: entity
availability/exposure, automation and script delete, entity metadata, and
helper options. The references are information only. They are not bound by
the approval, and they never change risk, policy or approval. It builds on
2.4.1-beta.58 (#342, HAMCP-278). No tool, input schema, route, provider,
signed profile, fingerprint, plan or approval binding, persisted format,
add-on option or Home Assistant write changes.

Acceptance: [`V2_4_1_BETA59_ACCEPTANCE.md`](V2_4_1_BETA59_ACCEPTANCE.md).

## The change

- Each of those disclosures gets a `global_source_consumers` sibling (model
  `global_source_consumers_v1`) next to its bound consumer evidence. It is
  built from `global_sources.visible().findings` in the dependency snapshot
  the index already holds. Building it never triggers a build or refresh, so
  it sends no new Home Assistant request.
  - **Scenes and groups** list loaded membership (`$.attributes.entity_id[i]`).
  - **Dashboards** list the view or card location and whether the card shows
    only under a condition.
  - **Completeness** is reported per source (`complete`, `partial`,
    `unavailable`), with the first reason for each incomplete source.
    Templates are listed as unavailable, so that gap stays visible.
    Dashboards are partial or unavailable live until HAMCP-277. When the
    dashboard provider authority has changed, cached dashboard evidence is
    withheld and reported as unavailable.
- **Statements.** Each sibling says:
  - the references are information only;
  - scene and group references reflect loaded membership;
  - which sources were scanned, with their completeness;
  - "Results may be partial: …", naming each incomplete source;
  - with no reference found while sources are incomplete, that this is not
    proof of none.
  The sibling's statements replace the fixed
  `dashboards_scripts_and_templates_not_scanned` wording for these sources.
- **Unbound by construction.** The sibling lives only in the projected dry-run
  results. It is excluded from the consumer evidence fingerprint, plan hash,
  policy subject and decision hashes, semantic projection hash and approval
  binding. A plan approved before a scene, group or dashboard change still
  applies.
- **Never a refusal.** If no snapshot is held, or the store is absent or
  unreadable, the sibling reports `status: unavailable` with a reason, and
  planning continues.
- **Bounded.** Each sibling carries per-source counts plus a top-N item list,
  with `truncated` and "Showing X of N references." when cut. The list is at
  most 8 items, and one plan shares an 8-item budget across its children
  (at least 1 each). Every string is length-bounded.

## Unchanged by design

The bound coverage notes are left as they were: the entity metadata
`coverage_gaps` entry `dashboards_scripts_and_templates_not_scanned`, and the
availability/delete coverage warnings. They are part of the consumer evidence
fingerprint or the policy subject hash, so rewording them would change plan
bindings. Each sibling says those notes describe the bound evidence only.

## Known limitation

The sibling is rendered twice per child: in the plan's dry-run results and in
the operation's. A multi-entity availability/exposure plan of up to five
entities with large reference sets stays under the 60,000-character response
bound. Six or seven entities with references can now exceed it; eight
entities already exceeded it before this release. Over the bound, the
existing structurally flagged renderer applies
(`response_completeness.approval_disclosures_complete: false`, with retrieval
hints). Use `get_change_plan` for the full plan.

## Rollback

Reinstall 2.4.1-beta.58. Plans keep their hashes and approvals, because the
sibling was never bound. No persisted format changed.
