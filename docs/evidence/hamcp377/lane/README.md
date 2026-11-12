# HAMCP-377 rollback proof lane: source-subject receipts

These receipts record what Engineering 2.4.2-beta.7 does with stores written by
Engineering 2.4.2-beta.8. They were produced by `scripts/rollback_proof_lane.py`
and are re-checked by `tests/test_hamcp377_rollback_proof_classifier.py`.

| Receipt | Scenario | Result | sha256 of the committed bytes |
| --- | --- | --- | --- |
| `12a-source.json` | Registry entry carrying capability references beta.7 does not compile | PASS | `8c087b86111b2f223e80f93702e889fb5c8eebd4f4acec808764b884cbebe255` |
| `12b-source.json` | Device-configuration lock records, with recovery 27a | PASS | `29cdd49e53564f298f6fedfbac7dab93545b23d47b5c5e91a67fcd91b87b8890` |
| `12c-source.json` | Scene-create and created-object rollback plans | PASS | `3350d81e566fd6644de66645690018fc7d954d728fd81436af34fe2fbb8399d6` |

Each receipt holds the observations, the words derived from them, and a
`summary` computed from the observations. The receipt is the record; the
sentences below repeat its `summary`.

## What ran

- Run: 2026-11-11 22:41:23 to 22:42:17 Central (2026-11-12T04:41:23Z to 04:42:17Z).
- Writer: `git archive` of `aeb3b314dd180592874243c9ae370a63f0622c2b`
  (`origin/release/2.6.0`, declares and reports 2.4.2-beta.8).
- Reader: `git archive` of `b614be43f8c56ae06fdec0ac0bcb479b9da39715`, the commit
  of tag `v2.4.2-beta.7` (tag object `998e9021e5f187b0f375bff9b4b867243fd270c0`;
  declares and reports 2.4.2-beta.7).
- Lane and drivers: checkout `747436e766bbb97e8576cd765c7d8a122a52d38e`, drivers
  unmodified. `tip_writer.py` sha256
  `7485113e156ea79489aae0ebd72233cccfde5427b4df45a5c2804a82221fb5fc`,
  `previous_reader.py` sha256
  `f0427230aaccf439a26677ecf8b3214dd847db6a122d87367507587594b811ab`.
- Interpreters: Python 3.12.14 for each side, in separate environments. Each
  holds its tree's `hass_mcp_engineering_beta/requirements.lock` (39 pins, 0
  mismatches; sha256
  `f0ed2856e5c30ade513a3088e173a5b103782754bb450f3955746106109e229c`, the same
  file at both commits). Every step ran with `-B -I`.
- Published registry journal used as the base of the 12a entry: git blob
  `59c4100473fd7722b4b8e44ac5061bef9d599c87`, sha256
  `3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d`.

Command, from the checkout:

```
<writer-python> -B scripts/rollback_proof_lane.py run \
    --out docs/evidence/hamcp377/lane --work <new scratch directory> \
    --reader-python <reader-python> --writer-python <writer-python> \
    --writer-commit aeb3b314dd180592874243c9ae370a63f0622c2b
```

The exact command of every writer and reader step is in each receipt under
`lane.commands`.

No store is committed. Every store was written during the run by the writer and
read by the reader without being edited in between. The lane hashed each store
after the writer exited, immediately before the read and immediately after it;
the three digests are equal for every store and are in each receipt under `data`.

## Results

12a. The previous release read a signed journal whose entry carries 38
capability references: 33 admitted including `core.marketplace_read`, 5 not
admitted (`core.created_helper_delete_operation`,
`core.device_configuration_entity_operation`,
`core.scene_create_delete_operation`, `core.security_metadata_read`,
`core.thread_matter_topology_read`), 0 quarantined. The entry is sequence 2 of a
test journal. It carries the 32 references of the published sequence-17 entry
followed by the six added ones, was built by the writer's
`scripts/prepare_core_release_registry.py` (`prepare_candidate`,
`sign_candidate`) and was signed with a key generated in the run and not
retained. It is not a published registry entry.

12b. With a conflict hold left by a settled device-configuration task, and with
a crashed device-configuration task whose lease is still live, beta.7 refuses
`control_power` on the held switch with `lock_conflict` before any provider
attempt, leaves the lock records unchanged, runs `control_power` on an unrelated
switch, and reports governed execution ready. With the same crash after the
lease expired, beta.7 refuses `control_power` on both switches with
`lock_conflict`, reports governed execution not ready (`storage_read`) after
three recovery passes, and refuses with `execution_task_storage_error`.

Recovery 27a, on a second crash store: beta.7 was read first and showed the
same block. beta.8 then settled the crashed task to `manual_review_required`
with no further provider write, and its hold-release path returned
`hold_released_state_differs` and left no lock record. beta.7 then ran
`control_power` on both switches, verified, with governed execution ready.

12c. beta.7 refuses the scene-create plans (pending, approved, applied), the
scene rollback plan and the helper rollback plan on get, approve, apply and
rollback with `policy_snapshot_mismatch`, and writes nothing to the provider.
beta.7 applies the approved automation and script rollback plans as ordinary
deletes, one provider `DELETE` each, and approves and then applies a pending
automation rollback plan the same way. The beta.7 runtime source has 0
references to `created_identity`; the beta.8 runtime source has 6.

## Limits of these receipts

- Synthetic provider: both releases ran their own offline test harness. No Home
  Assistant Core, add-on container, Supervisor, MCP transport or Ingress page
  took part, so these receipts show what the previous release's code does with
  these records, not what a deployed add-on does end to end.
- 12a: no real marketplace read took place; the source subject has no Core.
- 12b, recovery 27a: the hold was released by calling beta.8's service methods
  with a synthetic Ingress principal. The Ingress HTTP layer and its
  administrator check were not exercised.
- 12c: the objects beta.7 deleted were placed in its synthetic Core by the
  reader harness under the ids the writer's plans name. They are not the
  objects beta.8 created.
- The lane starts no container and opens no socket by construction (plain
  interpreter processes, scrubbed environment). The receipts record that as a
  statement about the lane, not as a measurement.
- Restoring the Engineering add-on backup taken before deploy is not exercised
  here.

## Re-checking and regenerating

```
python scripts/rollback_proof_lane.py check docs/evidence/hamcp377/lane
```

`check` re-derives every word and every summary sentence from the recorded
observations and compares them with the pinned expectation. It accepts only
receipts whose writer was a `git archive` of one commit, whose interpreters held
the locked dependencies, and whose drivers were unmodified.

To regenerate, run the command under "What ran" into this directory, then update
the table and the identities above.
