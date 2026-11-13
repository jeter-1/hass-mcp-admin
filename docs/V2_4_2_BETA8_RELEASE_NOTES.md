# Engineering 2.4.2-beta.8 release notes

**Status: release candidate.** Engineering 2.4.2-beta.8 is the 2.6.0 build.
It builds on 2.4.2-beta.7 (HAMCP-331, #383). Every item landed through its
own reviewed pull request into the `release/2.6.0` integration branch, and
the release pull request (HAMCP-332) carries the build to `main`.

Josh chose the version 2.4.2-beta.8 on 2026-11-09 (HAMCP-354). The release
transition refuses `2.4.2-beta.7 -> 2.6.0-beta.1`.

Acceptance: [`V2_4_2_BETA8_ACCEPTANCE.md`](V2_4_2_BETA8_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

## Current state

The version is materialized on `release/2.6.0` in the catch-up that merges
`main` through #413 (HAMCP-354). Nothing is published or deployed. Registry
sequence 18 is not signed.

## Items

Landed in `release/2.6.0` since 2.4.2-beta.7, each through its own reviewed
pull request.

**Engineering runtime and tools**
- **HAMCP-104 Slice A** (#398). Precise stale-state pre-dispatch refusal
  reasons.
- **HAMCP-294** (#397). Bounded logbook window and entity/name filter for
  `get_logbook`.
- **HAMCP-267** (#400). Integrity, incident and handoff analysis consume the
  scene/group/dashboard reference store.
- **HAMCP-229** (#402). New tool `automation_reliability_batch_analysis`:
  bounded reliability analysis across several automations.
- **HAMCP-296** (#401). Trace watch lifecycle: renew, lapsed records,
  priority displacement, up to 20 watches.
- **HAMCP-143** (#412). Bounded refusal diagnostics for dashboard compiler
  limits.
- **HAMCP-337 / HAMCP-338** (#411). Entities-card rows classified by domain;
  E031 lock-tile risk fix.
- **HAMCP-345** (#429). Valve, group, header-toggle and strategy dashboard
  controls classified.
- **HAMCP-350** (#418). Bounded startup readiness, per-phase timing and UTC
  log timestamps.
- **HAMCP-351** (#426). Exact ha-mcp 9.1.2 admitted at 9.1.0 parity. This
  house stays on 8.5.0; see [Known limitations](#known-limitations).
- **HAMCP-264** (#430). `dependency_index` YAML reader purpose for template
  and script references.
- **HAMCP-134** (#423). New tool `get_security_metadata`: a read-only
  security metadata read, dormant until registry sequence 18.
- **HAMCP-297** (#428). New tool `control_device_configuration`: governed
  device-configuration entities, Matter only (child-lock `switch`,
  power-on-behavior `select` and sensitivity `number`). Dormant until
  registry sequence 18.
- **HAMCP-290** (#421). `marketplace_read` local exact-image lane, evidence
  group and source manifest. `get_marketplace_inventory` shipped in
  2.4.2-beta.7 and stays dormant until sequence 18.
- **HAMCP-329 / HAMCP-298** (#424). Scene create through
  `create_scene_configuration_plan(action="create")` and created-helper
  delete, both dormant until sequence 18. Governed rollback of automation and
  script creates through `rollback_change`, live at deploy on the
  already-signed delete profile.
- **HAMCP-231** (#427). New tool `get_thread_matter_topology`: read-only
  Thread dataset and Matter topology read, dormant until sequence 18.
- **HAMCP-366** (#434). Device-configuration signing lane with loopback-only
  and closed-command guards.
- **HAMCP-358** (#433). Batched Low-severity cleanup. Runtime change is
  comments and docstrings only.
- **HAMCP-379** (#436). Recovery containment for governed power, fan,
  helper-value and device-configuration tasks: `get_execution_task` becomes a
  pure read and counted recovery claims are bounded. See
  [Known limitations](#known-limitations).

**Tests, tooling and documents only**
- **HAMCP-149** (#396). Offline approval-completion fixture.
- **HAMCP-361** (#431). Reset leaked governance state between tests.
- **HAMCP-362** (#432). Restore `create_application()` state in every
  remaining test builder.
- **HAMCP-356** (#422). `check.ps1` detects the PR base and defaults to the
  worktree venv.
- **HAMCP-348** (#420). Publication handoff reads the complete PR timeline.
- **HAMCP-347** (#419). Three beta.7 document errors corrected.
- **HAMCP-364** (#435). Lane clients talk only to the disposable Core their
  lane started; the seven sequence 18 lane receipts are regenerated from
  `5fc40f93`.
- **HAMCP-377** (#437). Rollback proof lane: 2.4.2-beta.7 reads stores
  written by 2.4.2-beta.8 (receipts 12a, 12b and 12c in
  `docs/evidence/hamcp377/lane/`), and CI's side-by-side rollback stage runs
  2.4.2-beta.7 instead of 2.4.2-beta.6.
- **HAMCP-352** (#415, on `main`). Instructions only: the
  Evidence-before-review rule is scoped by change type. It reaches the
  release head through `1d430d6d`.
- Design and investigation documents, no runtime change: HAMCP-297 (#389),
  HAMCP-329 (#393), HAMCP-295 (#390), HAMCP-134 feasibility (#392),
  HAMCP-231 investigation (#394), HAMCP-332 wave-1 notes (#391).
- The 2.6.0 signing plan for registry sequence 18
  ([`HAMCP_332_2_6_0_SIGNING_PLAN.md`](HAMCP_332_2_6_0_SIGNING_PLAN.md),
  from #395), folded into the release head through `3bd5fe3f`; its
  expected-values sheet A is refreshed at `41786cc0`.

**Version and catch-up**
- **HAMCP-354** (#416). Catch-up with `main` through #413; declares
  2.4.2-beta.8.
- The 2.5.0 build (2.4.2-beta.7) came in through #410 (`release/2.5.0`).
  #410 and the HAMCP-332 merges `7edddf38`, `e642b7b3` and `72e8a130` add
  no commits beyond the beta.7 tag.

**Not in this release**
- #425 (HAMCP-295, entity rename): stays out of 2.6.
- #388 (HAMCP-232, helper-options recheck design, docs only): stays an open
  draft and is retargeted after this release pull request merges.

## Tool catalog

| Catalog | Engineering | Delegated | Total |
| --- | --- | --- | --- |
| Stock upstream shape (source baseline) | 86 | 27 | 113 |
| This house: ha-mcp 8.5.0 add-on, optional YAML reader on | 86 | 28 | 114 |

Engineering-owned tools rise from 82 to 86. The delegated catalog does not
change: the ha-mcp add-on is not updated with this release.

**Added (4):**
- `automation_reliability_batch_analysis` (HAMCP-229): read-only analysis;
  carries no MCP annotations, like `automation_reliability_analysis`.
- `control_device_configuration` (HAMCP-297): a governed write.
- `get_security_metadata` (HAMCP-134): read-only.
- `get_thread_matter_topology` (HAMCP-231): read-only.

**Changed description or input schema (6):**
- `capture_automation_trace`: description.
- `control_fan` (HAMCP-379): description. It now says that
  `get_execution_task` reads the stored task and that recovery runs in the
  background. The control_fan description changed in beta.8; clients should
  refresh their cached tool schemas.
- `create_scene_configuration_plan`: description, plus the new `action`
  input (`create`).
- `get_logbook`: description, plus window and entity/name filter inputs.
- `rollback_change`: description; it now also rolls back governed creates.
- `set_automation_trace_watch`: description, plus lifecycle inputs.

**Removed:** none.

Clients must refresh their tool list after deploying: start a new chat, and
reconnect the connector only if the four new tools are absent. An existing
chat keeps the old schemas; for example, it offers
`create_scene_configuration_plan` without `action`.

**Dormant until registry sequence 18.** Six Core profiles compile in this
release. They are not yet in the signed 2026.11.0 registry entry, which is
sequence 17 with 32 references:
- `core.marketplace_read`
- `core.security_metadata_read`
- `core.device_configuration_entity_operation`
- `core.scene_create_delete_operation`
- `core.created_helper_delete_operation`
- `core.thread_matter_topology_read`

Each profile joins sequence 18 only if its lane passes on the signing
commit (Josh's ruling R-a of 2026-11-13; see the
[signing plan](HAMCP_332_2_6_0_SIGNING_PLAN.md)). Until Josh signs sequence
18 and the runtime loads it:
- `get_marketplace_inventory`, `get_security_metadata` and
  `get_thread_matter_topology` refuse with
  `pending_signed_registry_admission` and send no command.
- `create_scene_configuration_plan(action="create")` refuses with the same
  reason and creates no plan.
- `control_device_configuration` refuses with
  `device_configuration_core_authority_unavailable`.

Signing sequence 18 does not change `tools/list`.

## Rollback

Recovery from a bad 2.4.2-beta.8 is a restore from the full Home Assistant
backup that Josh takes in the Home Assistant UI immediately before
deploying 2.4.2-beta.8. In-place rollback to 2.4.2-beta.7 is not
supported.

- **Restore the full backup (tier 4).** This is the primary recovery. It
  returns the Engineering add-on to 2.4.2-beta.7 with its pre-deploy data,
  and it also reverts the Home Assistant objects created during acceptance.
  It returns all of Home Assistant to the time of the backup, so any other
  change made since then is lost too. **The full restore is untested
  here.**
- **Restore only the Engineering add-on (secondary).** Use this only when
  Home Assistant changes made after deploy must be kept. The Supervisor
  reinstalls the backed-up add-on version and pulls its image by tag. **It
  does not revert Home Assistant.** Before this restore, always:
  1. list every object created or changed during acceptance (scene,
     helper, automation, device-configuration entity), with its id and
     pre-state;
  2. clean them up through 2.4.2-beta.8 if it is functional; otherwise
     record them for manual cleanup.

**Before either restore**, export the 2.4.2-beta.8 audit
(`get_audit_log`). Every Engineering record written since deploy is lost
on restore: plans, receipts, audit and trace watches. See
[`runbooks/DOWNGRADE-VERSUS-BACKUP-RESTORE.md`](runbooks/DOWNGRADE-VERSUS-BACKUP-RESTORE.md).
After a restore, refresh every client; the connector catalog returns to 110
tools.

**Why in-place downgrade is not supported.** Two findings on
2.4.2-beta.7, both shown by the rollback proof receipts below:

- It executes created-object rollback plans written by 2.4.2-beta.8 as
  ordinary deletes. It runs automation and script rollback plans without
  the created-identity check, and can approve pending ones.
- An expired device-configuration lock left by 2.4.2-beta.8 faults
  governed execution. 2.4.2-beta.7 neither releases nor redispatches a lock
  it does not own, and the shared `home_assistant:core` record blocks
  governed writes on other entities too.

**Why removing lock records is not offered.** 2.4.2-beta.8 settles and
releases a governed power, fan, helper-value or device-configuration task
through that task's own lock records (HAMCP-379). Removing them, by any
method, is not a supported recovery step.

**Registry sequence 18 is one-way, and a restore does not undo it.**
Signing seq 18 is one-way. There is no per-capability removal; the only
undo is revoking the whole 2026.11.0 entry, which withdraws all its
capabilities and cannot be re-added; recovery is then a new Core-version
binding (HAMCP-367). Signing is outside the Home Assistant backup, so after
a restore 2.4.2-beta.7 runs against sequence 18. Never roll back the
registry journal or delete a cache.

**Rollback proof.** Each item cites a committed source-subject receipt of
the HAMCP-377 lane in
[`evidence/hamcp377/lane/`](evidence/hamcp377/lane/README.md), with
2.4.2-beta.8 at `aeb3b314` as the writer and 2.4.2-beta.7 at `b614be43` as
the reader. All three receipts pass. Their limits are listed under
[Known limitations](#known-limitations).

- **12a**, before signing (`12a-source.json`, sha256
  `b7b66591d2620e09063627c4dec6d2aa42ef94846b9f2f1526ce354d78646437`):
  2.4.2-beta.7 reads a test-signed journal whose 2026.11.0 entry carries 38
  references, and admits 33, including `core.marketplace_read`. The other 5
  are not admitted, and 0 are quarantined.
- **12b**, before the first device-configuration write (`12b-source.json`,
  sha256 `fee3d5250fee91f0210d2ed2e778d132e83d2a468392d2768010ff9a0136fb46`):
  on 2.4.2-beta.7, `control_power` on a switch held by a
  device-configuration task is refused with `lock_conflict` and the lock
  records are unchanged. Once the crashed task's lease has expired, a write
  to an unrelated switch is refused too, and governed execution reports not
  ready.
- **12c**, before the first scene-create write (`12c-source.json`, sha256
  `74b26509179b4edfe412e017f90503ecde3fc1aba56d006b68a569a46258d474`):
  2.4.2-beta.7 loads the store, refuses scene-create and scene/helper
  rollback plans with `policy_snapshot_mismatch` and no provider write, and
  applies automation/script rollback plans as ordinary deletes.

**State 2.4.2-beta.8 adds** (all of it is lost on a restore):
- created-identity records;
- device-configuration verified observations and undo claims;
- trace-watch sidecar slots.

There is no add-on option change, and the server schema version stays
`"1"`. CI's side-by-side rollback image is 2.4.2-beta.7
(`sha256:5801d42f…3c47`).

Whether to roll back is Josh's decision.

## Known limitations

### Execution-task recovery (HAMCP-379), fixed in this release

Previously, a governed power, fan, helper-value or device-configuration
task whose outcome was uncertain could keep appending recovery events.
After 128 events the task was neither terminal nor readable, and
`get_execution_task` returned `invalid_request`. Three conditions could
cause this:
- Core reads that kept failing past the sweep budget;
- the process stopping between a lock transfer and the record update;
- frequent `get_execution_task` polling while the readback did not match.

In this release (#436):
- get_execution_task is a pure read. It makes no change to execution
  records, locks, governance state or Home Assistant. Like every
  Engineering tool, it may append to Engineering's own audit log: the
  tool-call entry, plus the idempotent backfill of missing projection
  entries from existing events, via receipt() -> project() ->
  AuditLogger.write_batch (fan/service.py:339, fan/audit.py:153).
  It returns the record for every existing task. Recovery runs in the
  30-second background sweep.
- Counted recovery claims are bounded. At the bound the task settles to
  `manual_review_required` with reason `recovery_bound_reached`, and is
  never dispatched again.
- A record written by earlier code that is already close to the 128-event
  cap is trimmed of its newest recovery bookkeeping events, so that the
  settling events fit.
- Repeated recovery failures, an unreadable lock store included, are
  counted in `recovery_failures` instead of appending events.
  `get_server_health` shows the counter under `ordinary_fan`,
  `ordinary_power`, `ordinary_helper_value` and
  `ordinary_device_configuration`. It is held in memory and resets on
  restart.
- A task that settles at the bound without its lock handle is contained by
  the next sweep: the task's own lock rows are promoted, the entity row
  becomes the conflict hold, and the shared rows, `home_assistant:core`
  included, are released.

`recovery_bound_reached` is client-visible, through the receipt's
`verification.manual_review_reason_code` and the hold-release item's
`manual_review_reason`. It is the only new client-visible value.
`diagnostic_codes` reaches no tool output; the trace covered the four typed
write families.

2.4.2-beta.7 does not have this fix. After a restore, its power, fan and
helper families are exposed again.

Limits that remain:
- If the lock store holds rows of one typed write task under two different
  owners (possible only after lock-store corruption), the task settles to
  manual review but not all of its locks are released: home_assistant:core
  stays held, so governed power, fan, helper value and
  device-configuration writes are refused until the pre-deploy backup is
  restored. The condition is counted as a recovery failure on every sweep,
  and nothing is dispatched.
- If a crash leaves a typed write task's lock under a generation its record
  does not name, and the lock store is unreadable when that task settles,
  the task's shared locks are still released but its entity conflict hold
  cannot be released; governed writes to that one entity are refused until
  the pre-deploy backup is restored. The condition is counted as a recovery
  failure on every sweep, and nothing is dispatched.
- The recovery-claim spacing rule for a clock that steps backwards is
  tested on synthetic events only (HAMCP-379). It has not been exercised
  with a real clock change.
- Audit sequences after a trim: a trimmed old-writer record reuses its two
  newest audit sequences. This is pinned by
  `test_a_trimmed_old_writer_record_reuses_its_two_newest_audit_sequences`.

### Other limitations

- **Plan-governed tasks after an Engineering crash (HAMCP-381).** If the
  Engineering add-on stops at one specific point while it is recovering a
  plan-governed task (after it takes over the task's expired claim and
  before it transfers the task's locks), the task can stay non-terminal and
  keep the `home_assistant:core` lock. Nothing in this release settles such
  a task or releases that lock. The only recovery is a backup restore (see
  [Rollback](#rollback)). This applies to 2.4.2-beta.7 as well; it is not
  new in this release. The HAMCP-379 change covers the power, fan,
  helper-value and device-configuration tools, not plan-governed tasks. A
  fix for plan-governed tasks is planned early in 2.7. Before a deploy, and
  before an Engineering-only restore, check that no plan or task is in
  flight. Make backups in the Home Assistant UI, not through
  `create_backup_plan`.
- Governed backup plans (HAMCP-381): review analysis found that a governed backup created with create_backup_plan that stays pending reaches the 128-event recovery bound in about 2.7 hours, without a crash, and leaves the home_assistant:core lock held, blocking other governed operations on Home Assistant Core. This was not observed in production. Until HAMCP-381 is fixed, do not use create_backup_plan; make backups in the Home Assistant UI or rely on the nightly Home Assistant backup.
- **Polling execution tasks.** AI clients poll `get_execution_task` no more
  often than every 10 seconds, and stop after a few minutes. In
  2.4.2-beta.8 a read no longer drives recovery, but 2.4.2-beta.7, which a
  restore brings back, still reconciles its power, fan and helper tasks on
  each read.
- **ha-mcp add-on version.** Keep the ha-mcp add-on on 8.5.0 with
  auto-update off. This release admits exact 9.1.2 at 9.1.0 parity
  (HAMCP-351). It does not admit 9.2.0, which is absent from the reviewed
  upstream release registry. On an unadmitted upstream version, Engineering
  withholds every delegated read. Update the add-on only after an
  Engineering release that admits the store's version is deployed with its
  replay green (HAMCP-371, after 2.6).
- **Core version.** Keep Home Assistant Core on 2026.11.0. The signed
  registry has no 2026.11.1 entry, and 2026.11.1 is not a compiled release.
  On 2026.11.1 the Core-native profiles are not admitted, and Engineering's
  Core-native tools refuse. The 2026.11.1 binding is HAMCP-367, planned for
  2.7.
- CI Core window: CI boots Home Assistant Core 2026.10.0, 2026.10.1 and 2026.10.3 only. The deployed Core, 2026.11.0, is not booted in CI; this release is covered on it by lane receipts and by live acceptance after deploy. Moving the CI window to the 2026.11 line is planned for 2.7 with HAMCP-367.
- **Device configuration: number entities.** `control_device_configuration`
  supports `set_value` on admitted Matter `number` configuration entities.
  2.6 live acceptance does not exercise that path. The only candidate in
  this house is a security sensor on a live alarm system. Offline tests
  cover it in `tests/test_hamcp297_device_configuration.py`:
  `test_select_switch_number_each_one_closed_call_and_verified`,
  `test_number_receipt_carries_identity_conversion_proof` and
  `test_number_unit_conversion_and_bounds_refuse`.
- **Rollback proof receipts (HAMCP-377).** The receipts are source-subject
  results: each release ran in its own offline test harness, and no Home
  Assistant Core, add-on container, Supervisor, MCP transport or Ingress
  page took part. In 12a no real marketplace read took place; 2.4.2-beta.7
  read the candidate under the dry run's key, not the published key; the
  candidate has not been compared with the real sequence 18 candidate,
  which does not exist yet; and the receipt cannot be regenerated after
  2027-02-05 00:16 CST, when the pinned sequence 17 journal expires. In
  12b, recovery 27a is not a supported recovery path, and its hold release
  used a synthetic Ingress principal. In 12c, the objects 2.4.2-beta.7
  deleted were placed by the reader harness. Restoring a backup is not
  exercised.
- **Rollback proof lane check (HAMCP-377).** The R1 closed-schema check is
  narrower than first documented. `check` refuses a key the receipt schema
  does not define only in the records `closed_records` walks; besides the
  three gaps the lane README first named, it also leaves the `drivers`
  record, the records nested in the 12b and 12c `observed` records and the
  records nested in the exercised 27a steps open to an extra key
  (HAMCP377-R1-WIDER).
