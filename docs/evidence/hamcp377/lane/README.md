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
`summary` computed from the observations. The receipt is the record; the
sentences below repeat its `summary`.

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

12a. The previous release read a signed journal whose entry carries 38
capability references: 33 admitted including `core.marketplace_read`, 5 not
admitted (`core.created_helper_delete_operation`,
`core.device_configuration_entity_operation`,
`core.scene_create_delete_operation`, `core.security_metadata_read`,
`core.thread_matter_topology_read`), 0 quarantined.

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

Comparison with the real sequence 18 candidate: not yet available: the real
candidate does not exist. The receipt reserves `real_candidate_comparison` for
it and records the digest of the 38 reference objects beta.7 read
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

`check` re-derives every word and every summary sentence from the recorded
observations and compares them with the pinned expectation. It accepts only
receipts whose writer was a `git archive` of one commit, whose interpreters held
the locked dependencies, and whose drivers were unmodified.

To regenerate all three receipts, run the one command under "What ran" into this
directory, then update the table and the identities above.
