# HAMCP-377 rollback proof lane: source-subject receipts

These receipts record what Engineering 2.4.2-beta.7 does with stores written by
Engineering 2.4.2-beta.8. They were produced by `scripts/rollback_proof_lane.py`
and are re-checked by `tests/test_hamcp377_rollback_proof_classifier.py`.

| Receipt | Scenario | Result | sha256 of the committed bytes |
| --- | --- | --- | --- |
| `12a-source.json` | Registry entry carrying capability references beta.7 does not compile | PASS | `b7b66591d2620e09063627c4dec6d2aa42ef94846b9f2f1526ce354d78646437` |
| `12b-source.json` | Device-configuration lock records, with recovery 27a | PASS | `fee3d5250fee91f0210d2ed2e778d132e83d2a468392d2768010ff9a0136fb46` |
| `12c-source.json` | Scene-create and created-object rollback plans | PASS | `74b26509179b4edfe412e017f90503ecde3fc1aba56d006b68a569a46258d474` |

Each receipt holds the observations, the words derived from them, and a
`summary` computed from the observations. The receipt is the record. Under
"Results" each receipt's `summary` is repeated line for line; the table above,
those blocks and every full-length identity in this file are compared with the
committed receipts by `ReadmeTests` in the classifier test module. The remaining
prose is explanation and is not compared.

## What ran

- Run: 2026-11-11 23:38:17 to 23:39:25 Central (2026-11-12T05:38:17Z to 05:39:25Z).
- Writer: `git archive` of `aeb3b314dd180592874243c9ae370a63f0622c2b`
  (`origin/release/2.6.0`, declares and reports 2.4.2-beta.8).
- Reader: `git archive` of `b614be43f8c56ae06fdec0ac0bcb479b9da39715`, the commit
  of tag `v2.4.2-beta.7` (tag object `998e9021e5f187b0f375bff9b4b867243fd270c0`;
  declares and reports 2.4.2-beta.7).
- Lane and drivers: checkout `cd2080846b25a4f632f404bc15ef9aabe24051fc`, drivers
  unmodified. `tip_writer.py` sha256
  `0e4dc04e115f0015c2e1b5ab3fe79f2b0c10dc69aa55ec85586b21eda1f436ca`,
  `previous_reader.py` sha256
  `f507fa35086addb60cb5377a20e8b0c44899af1905422f3fb6107f4a3d3f7349`.
- Interpreters: Python 3.12.14 for each side, in separate environments. Each
  holds its tree's `hass_mcp_engineering_beta/requirements.lock` (39 pins, 0
  mismatches; sha256
  `f0ed2856e5c30ade513a3088e173a5b103782754bb450f3955746106109e229c`, the same
  file at both commits). Every step ran with `-B -I`.
- Published registry journal the 12a candidate extends: git blob
  `59c4100473fd7722b4b8e44ac5061bef9d599c87`, sha256
  `3f79fce76484ff9f9a9c070defe4d1d13865ccbfd24fda0ab8724515c8cc6a5d`, sequence 17,
  expiring 2027-02-05T06:16:34Z.
- Tools that built and test-signed the 12a candidate, both run from the writer's
  `git archive` of `aeb3b314dd180592874243c9ae370a63f0622c2b`:
  `scripts/prepare_core_release_registry.py extend-capabilities` (file sha256
  `6e09d3f96e79253f730604ffb10caffe69d77c183fc734f5517a3733cdf4b368`, git blob
  `1ace9bcc66892acc22462a94bd42631bacc92ef9`, equal to the blob at that commit) and
  `scripts/core_registry_one_tap.py dry-run-sign` (file sha256
  `0e8e9b13994a54dd08ec786da04b573b888c2d7b978bdd5e7af61b173601e68c`, git blob
  `7524f83d52b53287e0fc1bf0b9d7f0f3621f8918`, equal to the blob at that commit).
  The 12a receipt carries both under `candidate_tool` and `test_signing_tool`.

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

### 12a

Recorded `summary` of `12a-source.json`:

```text
The previous release read a signed journal whose entry carries 38 capability references: 33 admitted including core.marketplace_read, 5 not admitted (core.created_helper_delete_operation, core.device_configuration_entity_operation, core.scene_create_delete_operation, core.security_metadata_read, core.thread_matter_topology_read), 0 quarantined.
The entry was built by scripts/prepare_core_release_registry.py extend-capabilities at commit aeb3b314dd180592874243c9ae370a63f0622c2b as sequence 18 on the published sequence 17 journal under the published public key, and test-signed by scripts/core_registry_one_tap.py dry-run-sign with a key generated in that dry run; the signed content equals the candidate and the published content.
Comparison with the real sequence 18 candidate: not yet available: the real candidate does not exist.
```

The entry was produced by the route the real sequence 18 candidate takes. The
writer derived the entry (the published 2026.11.0 entry with the six references
added after its 32, in the order of the writer's compiled profiles) and the
review-evidence file, then ran the release's own command,
`scripts/prepare_core_release_registry.py extend-capabilities`, on the published
sequence 17 journal under the committed public key
`docs/evidence/hamcp222/core-public-key.txt`. The tool wrote a sequence 18
candidate (`core-registry-extension-candidate-v1`, three entries unchanged) and
reported the sha256 the lane then measured on the file. The release's own
`scripts/core_registry_one_tap.py dry-run-sign` signed that candidate with a key
it generated in the dry run; only the public half was written. The writer driver
then recorded, and the gate requires, that the signed sequence 18 envelope
equals the candidate's envelope and that the content of the 17 published
envelopes is unchanged. The lane read the reference objects out of the candidate
file and out of the signed journal and requires the same bytes in both, and the
same list from beta.7's decoding. beta.7 read that journal under the dry run's
public key.

What differs from the real candidate by construction: the signing key; the
review evidence, which here names one placeholder artifact digest, and with it
the entry's `evidence_sha256` and the candidate's `review_evidence_sha256`; and
`generated_at` and `expires_at`, which the tool takes from the clock. The dry
run also re-signs the 17 published envelopes with its own key. The candidate is
not a published registry entry and neither tool was changed or copied.

The real sequence 18 candidate does not exist yet. The receipt reserves
`real_candidate_comparison` for it and records the digest of the 38 reference
objects beta.7 read
(`33264f99859e011afc512dbcba0f6297446c4d4980afabdfee9ce364cfa303a6`, sha256 over
the canonical JSON of each object joined by newlines). When the real candidate
exists:

```
python scripts/rollback_proof_lane.py compare-candidate \
    docs/evidence/hamcp377/lane <core-candidate.json> --record
```

compares the canonical bytes of each reference object, in order, with those of
the supplied file's 2026.11.0 entry, and records the result, the file's sha256
and every differing position in the 12a receipt. Signatures, timestamps and
evidence digests are not part of a reference object and are not compared. The
gate fails if any object differs, if the file is not a sequence 18 candidate,
or if the file is the lane's own test candidate. Without `--record` the receipt
is left as it is. `run --real-candidate <file>` does the same during a run.

### 12b

Recorded `summary` of `12b-source.json`:

```text
Conflict hold left by a settled device-configuration task: on the previous release control_power on the switch the device-configuration task holds is refused with lock_conflict before any provider attempt, and the lock records are unchanged; control_power on an unrelated switch succeeds; the governed runtime reports execution ready.
Crash during a device-configuration operation, lease still live: on the previous release control_power on the switch the device-configuration task holds is refused with lock_conflict before any provider attempt, and the lock records are unchanged; control_power on an unrelated switch succeeds; the governed runtime reports execution ready.
Crash during a device-configuration operation, lease expired: on the previous release control_power on the switch the device-configuration task holds is refused with lock_conflict before any provider attempt, and the lock records are unchanged; control_power on an unrelated switch is refused with lock_conflict as well; the governed runtime reports execution not ready (storage_read) after three recovery passes and refuses with execution_task_storage_error.
Recovery 27a, before (the same crash, read before rolling forward): on the previous release control_power on the switch the device-configuration task holds is refused with lock_conflict before any provider attempt, and the lock records are unchanged; control_power on an unrelated switch is refused with lock_conflict as well; the governed runtime reports execution not ready (storage_read) after three recovery passes and refuses with execution_task_storage_error.
Recovery 27a, roll forward: the current release settled the crashed task to manual_review_required with 0 further provider writes, and its hold-release path returned hold_released_state_differs leaving 0 lock records.
Recovery 27a, after (rolled back again): on the previous release control_power on the previously held switch succeeds and is verified; control_power on an unrelated switch succeeds; the governed runtime reports execution ready.
Recovery 27a, observed: on this store the previous release ran no governed control before the roll forward (execution not ready, control_power refused) and ran it after (execution ready, control_power verified).
```

"The previous release" is 2.4.2-beta.7 and "the current release" is
2.4.2-beta.8. Recovery 27a ran on a second crash store, written for it.

### 12c

Recorded `summary` of `12c-source.json`:

```text
automation_rollback_pending: approved on the previous release and then applied as an ordinary delete: one provider DELETE.
automation_rollback_approved, script_rollback_approved: applied by the previous release as an ordinary delete: one provider DELETE each.
automation_created: no rollback plan in the store: nothing to execute, no provider write.
helper_rollback_approved, scene_applied, scene_approved, scene_pending, scene_rollback_approved: refused on get, approve, apply and rollback with policy_snapshot_mismatch, no provider write.
References to created_identity in the previous release's runtime source: 0; in the writer's: 6.
```

## Limits of these receipts

- Synthetic provider: both releases ran their own offline test harness. No Home
  Assistant Core, add-on container, Supervisor, MCP transport or Ingress page
  took part, so these receipts show what the previous release's code does with
  these records, not what a deployed add-on does end to end.
- 12a: no real marketplace read took place; the source subject has no Core.
- 12a: beta.7 read the candidate under the dry run's key, not under the
  published key, and the review evidence is a placeholder.
- 12a: the reference objects come from the compiled profiles at the writer
  commit. The real candidate is prepared on its own signing commit; whether its
  reference objects are the same bytes is what the pending comparison answers.
- 12a: the comparison identifies a supplied candidate by its sha256 only. The
  lane does not establish that the file is the candidate that was or will be
  signed.
- 12a: the candidate tool extends only a journal that has not expired. After
  2027-02-05T06:16:34Z the lane reports that it cannot run until the journal
  then published is pinned in its place.
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

`check` reads only the receipt files. It re-derives every word and every
summary sentence from the recorded observations and compares them with the
pinned expectation, including the exact limitation list of each scenario and,
for recovery 27a, the crash point, the `manual_review_required` outcome and the
presence of the review record. It compares recorded fields with each other and
with the constants the lane pins, and it accepts only receipts that record a
writer taken by `git archive` from one commit, interpreters holding the locked
dependencies and unmodified drivers. It fails on a key the receipt schema or its
`lane` record does not define, on more than one receipt for a subject and
scenario, on a file whose name is not that of the receipt it holds, on an
unknown subject or scenario, and on a `.json` file it cannot read as a receipt.

`check` does not consult git, the files that ran, the clock or this README. In
the classifier test module, `GitBoundReceiptTests` compares the committed
receipts with the repository's objects: that the recorded checkout commit
exists, that each recorded driver hash is the sha256 of that file at that
commit, and that the recorded tool blobs and hashes are those of the tool files
at the commit recorded with them. It is skipped where git or the recorded
commits are unavailable. `RecordedTimeTests` compares that a run did not end
before it started. The writer commit a receipt records is not compared with a
pinned value.

To regenerate all three receipts, run the one command under "What ran" into this
directory, then update the table, the identities above and the three recorded
`summary` blocks.
