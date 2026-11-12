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

The 12a values that carry the sequence 18 proof, and the comparison `check`
holds each to. Paths without a top-level name are under
`cases.registry.observed`. Line numbers are those of the commit that added this
list.

- `writer.published_journal_sha256`, `writer.published_sequence` (17),
  `writer.published_expires_at`, `writer.previous_references` (the 32 published
  references, a prefix of `writer.references`) and `writer.sequence` (18) equal
  the constants the lane pins: `scripts/rollback_proof_lane.py:467-471` (G3).
- `writer.added` and the last six of `writer.references` are the ruled six in
  the ruled order: `scripts/rollback_proof_lane.py:475` (G3).
- `writer.tools.candidate`: path, operation `extend-capabilities`, exit 0 and the
  argument list naming the published public key and the published journal:
  `scripts/rollback_proof_lane.py:403-407` (G3).
- `writer.candidate`: model, operation, `sequence` 18, `previous_journal_sha256`
  equal to the published sequence 17 journal, `public_key_sha256` and
  `writer.published_public_key` equal to the committed public key, and one
  changed entry: `scripts/rollback_proof_lane.py:410-416` (G3).
- `writer.candidate.sha256`, `writer.candidate.reported_sha256`,
  `lane.candidate.sha256` and the top-level `data.registry.candidate_sha256` are
  one value, and `writer.candidate.file` is `core-candidate.json`:
  `scripts/rollback_proof_lane.py:419-420` (G3).
- `writer.reference_objects`: their `capability_id`s are `writer.references`,
  each has exactly the five reference fields, their digest equals
  `lane.candidate.reference_objects_sha256` and
  `lane.journal.reference_objects_sha256`, the two files read as a candidate and
  a signed journal, and `lane.candidate.sequence`, `lane.journal.sequence` and
  `writer.candidate.sequence` are equal: `scripts/rollback_proof_lane.py:422-430`
  (G3).
- `writer.tools.test_signing` (path, operation `dry-run-sign`, exit 0, argument
  list) and `writer.test_signed`: `file`, `sequence` equal to the candidate's,
  and `envelope_equals_candidate_envelope`, `entry_references_equal_candidate`
  and `predecessor_content_unchanged` each true:
  `scripts/rollback_proof_lane.py:434-442` (G3).
- `writer.test_signed.key_sha256` has 64 digits and is not the published key's:
  `scripts/rollback_proof_lane.py:445` (G3).
- The top-level `candidate_tool` and `test_signing_tool`: path, operation, and a
  commit and source equal to the writer's; `sha256` equal to the tool's as it
  ran; `blob_run` equal to `blob_at_commit`: `scripts/rollback_proof_lane.py:451-455`
  (G3). That the blobs and hashes are those of the tool files at that commit is
  compared with git by `GitBoundReceiptTests`, not by `check`:
  `tests/test_hamcp377_rollback_proof_classifier.py:1528-1539`, called at
  `tests/test_hamcp377_rollback_proof_classifier.py:1578`.
- The top-level `writer.commit` equals `WRITER_COMMIT`:
  `scripts/rollback_proof_lane.py:1596-1599`, in the `check` command only.
- `reader.entry_references` equals `writer.references`, and
  `reader.entry_reference_objects_sha256` is the digest of
  `writer.reference_objects` (`33264f99…`): `scripts/rollback_proof_lane.py:477-478`
  (G3).
- `writer.journal_sha256`, `reader.journal_sha256`, `lane.journal.sha256` and the
  top-level `data.registry.journal_sha256` are one value:
  `scripts/rollback_proof_lane.py:480-482` (G2).
- `reader.refreshed` is true and `reader.authority` is exactly `entry_present`
  true, `positive_authority_current` true, `sequence` 18 and `surface_denied`
  false: `scripts/rollback_proof_lane.py:501-503` (G5).
- The top-level `real_candidate_comparison`: its scope text,
  `lane_reference_count` (38) and `lane_reference_objects_sha256` (the digest of
  `writer.reference_objects`), and, while pending, no other key:
  `scripts/rollback_proof_lane.py:349-357` (G14).
- The second `summary` sentence (sequence 18 on the published sequence 17
  journal; the signed content equals the candidate and the published content) is
  computed from these values at `scripts/rollback_proof_lane.py:774-781` and
  compared at `scripts/rollback_proof_lane.py:984-985` (G8).

`check` calls `gate` at `scripts/rollback_proof_lane.py:1595`, which classifies
each receipt again at `scripts/rollback_proof_lane.py:1016`. The candidate and
journal digests are compared only with each other; `check` cannot re-measure
them.

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

## 12a against the lane this change ships, without regenerating

The committed `12a-source.json` was produced by the lane at checkout
`cd2080846b25a4f632f404bc15ef9aabe24051fc`. `scripts/rollback_proof_lane.py`
changed after that (50e6f3b8 and cba3557a); `tip_writer.py` and
`previous_reader.py` did not. The receipt therefore names older lane code than
the lane this change ships. It was kept, not regenerated, on the following
comparison.

The 12a driver was run again from the lane at 33cfe46a, in a clean clone outside
this worktree, with writer commit `aeb3b314dd180592874243c9ae370a63f0622c2b`
and `--scenario 12a`, into a scratch directory outside this worktree: PASS,
2026-11-12 11:23:10 to 11:23:17 Central. The scratch receipt is not committed.
Its sha256 begins `8fff6aa7`; the full digest is in the pull request. Values
that only the scratch receipt holds are given here in short form, since
`ReadmeTests` binds every full-length identity in this file to a committed
receipt.

Both receipts were flattened to key paths (object keys joined by dots, list items
by index, an empty list or object as a path of its own): 722 paths each, 677 of
them scalar values and 45 empty lists or objects. No path is in only one
receipt.

These 17 paths were declared ignorable before the scratch receipt was read. Each
is a timestamp, or a field of the writer's run whose value carries the run time
or the dry run's new key:

- `started_at` (timestamp): wall-clock start of the lane run.
- `ended_at` (timestamp): wall-clock end of the lane run.
- `cases.registry.observed.writer.candidate.generated_at` (timestamp):
  extend-capabilities stamps the time it runs (receipt `limitations[1]`).
- `cases.registry.observed.writer.candidate.expires_at` (timestamp):
  `generated_at` plus the tool's fixed validity window.
- `cases.registry.observed.writer.candidate.sha256` (writer field): sha256 of
  the writer's `core-candidate.json`, whose bytes carry `generated_at`.
- `cases.registry.observed.writer.candidate.reported_sha256` (writer field): the
  same digest as printed by the candidate tool.
- `cases.registry.observed.writer.tools.candidate.stdout` (writer field): the
  candidate tool's stdout line, `Prepared output SHA256: <candidate sha256>`.
- `cases.registry.observed.writer.journal_sha256` (writer field): sha256 of the
  dry-run-signed journal, which carries `generated_at` and an ed25519 signature
  by a key generated inside each dry run.
- `cases.registry.observed.writer.test_signed.key_sha256` (writer field): sha256
  of the public half of the dry run's ephemeral key, new each run.
- `cases.registry.observed.lane.candidate.sha256` (writer field): the lane's
  digest of the writer's `core-candidate.json` (as `candidate.sha256`).
- `cases.registry.observed.lane.journal.sha256` (writer field): the lane's digest
  of the writer's signed journal (as `writer.journal_sha256`).
- `cases.registry.observed.reader.journal_sha256` (writer field): the reader's
  digest of the writer's signed journal (as `writer.journal_sha256`).
- `data.registry.candidate_sha256` (writer field): the data-set digest of the
  writer's `core-candidate.json`.
- `data.registry.journal_sha256` (writer field): the data-set digest of the
  writer's signed journal.
- `data.registry.before_read` (writer field): tree digest of the writer's data
  directory, which holds the candidate and the journal.
- `data.registry.after_write` (writer field): the same tree digest, taken after
  the write.
- `data.registry.after_read` (writer field): the same tree digest, taken after
  the read.

All 17 differ, as expected. Inside the scratch receipt the ignored digests agree
with each other: the four candidate digests are one value, the four journal
digests are one value and the three data-tree digests are one value, so ignoring
them hides no disagreement between writer, lane and reader.

Two further paths differ. Both name the lane that ran, and both were accepted by
the architect's ruling of 2026-11-12 11:33 Central:

- `drivers.rollback_proof_lane.py`: committed
  `e8d064ba6f3fcc2ea5c83ca912c8962d0c25bc85cdefb472927a759d20824814`, the
  sha256 of `scripts/rollback_proof_lane.py` at cd208084; scratch `dbec9b15…`,
  its sha256 at 33cfe46a.
- `lane.checkout_commit`: committed `cd2080846b25a4f632f404bc15ef9aabe24051fc`;
  scratch 33cfe46a.

Every other path is identical: the remaining 658 scalar values and the 45 empty
lists and objects. They include the 38 entries of `writer.reference_objects`; the derived counts 33
admitted, 5 not admitted, 0 quarantined; `route_status`; `compiled`; sequence 18
in the writer, the candidate, the test-signed journal and the reader's authority;
the reference digest `33264f99859e011afc512dbcba0f6297446c4d4980afabdfee9ce364cfa303a6`;
the rest of the `reader` record, `reader.authority` included; `runs`; the
`summary`; and the
driver hashes `tip_writer.py`
`0e4dc04e115f0015c2e1b5ab3fe79f2b0c10dc69aa55ec85586b21eda1f436ca` and
`previous_reader.py`
`f507fa35086addb60cb5377a20e8b0c44899af1905422f3fb6107f4a3d3f7349`.

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
- 12a: the candidate tool extends only a journal that has not expired. The 12a
  receipt cannot be regenerated after 2027-02-05 00:16 CST
  (2027-02-05T06:16:34Z), when the pinned sequence 17 journal expires. After
  that the lane reports that it cannot run until the journal then published is
  pinned in its place.
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
- Recovery 27a (roll forward, settle, roll back on the same data) is not a
  supported recovery path. Its tier, classifier and the 12b record of it stay
  as committed; they show what happened in this run, not a path to follow.

Known limits that are not corrected, by decision; each is a statement about
what `check` cannot see, and none changes a recorded result:

- L5: the 12c reader's synthetic Core counts POST and DELETE provider writes but
  not PUT. The 12c plans write with POST and DELETE only. Correcting it changes
  the reader driver's bytes, which would require regenerating the receipts.
- R4: the recorded `flags` are the command line the lane built, and the reader's
  `isolated` is written as a constant; neither is what the interpreter reports.
  `check` does not read `flags`.
- R5: `check` reads only the `*.json` files at the top of the receipt
  directory. Another file or a subdirectory beside them is not read.
- R7: the recorded start and end times are bound only by their order, not to
  the commits or to the clock.
- R1 (architect's ruling of 2026-11-12 11:19 Central): `closed_records`
  (`scripts/rollback_proof_lane.py:845-897`) does not close every record a
  receipt holds, so "a key the receipt schema does not define" fails only in the
  records it walks. Three gaps remain:
  - the names of the data sets are not closed: each record under `data` is
    closed by its name (`:877-879`), but an added name is not refused;
  - the `writer` record of a 12b or 12c case (`cases.<name>.writer`, allowed by
    `CASE_KEYS` at `:145-147`) is not walked;
  - in 12a, only the first level below `cases.registry.observed` is closed
    (`:874-876`). A key added deeper, for example in `writer.candidate`,
    `writer.test_signed`, `writer.tools`, `lane.candidate` or `lane.journal`,
    fails only where a classifier compares the whole record, as G5 does for
    `reader.authority`.
- `check` cannot re-measure what a receipt records as constants or digests: the
  commands and flags (B11, B12), the store digests (B17), the dependency pins
  (B20) and the lock file hashes (B21). For B21, `WriterCommitGitTests`
  compares each side's recorded lock hash with the lock file at that side's
  commit where git is at hand; `check` does not.

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
dependencies and unmodified drivers. It accepts only receipts whose writer commit
is the one the lane pins (`WRITER_COMMIT`,
`aeb3b314dd180592874243c9ae370a63f0622c2b`); `gate` does not compare it. It fails
on a key the receipt schema does not define, at the top level or in any record
`closed_records` lists (the `lane`, writer and reader records, their
dependencies, the runs, the cases and their observed and derived records, the
data sets, the tool and comparison records and an exercised 27a tier), on more
than one receipt for a subject and
scenario, on a file whose name is not that of the receipt it holds, on an
unknown subject or scenario, and on a `.json` file it cannot read as a receipt.

`check` does not consult git, the files that ran, the clock or this README. In
the classifier test module, `GitBoundReceiptTests` compares the committed
receipts with the repository's objects: that the recorded checkout commit
exists, that each recorded driver hash is the sha256 of that file at that
commit, and that the recorded tool blobs and hashes are those of the tool files
at the commit recorded with them. It is skipped where git or the recorded
commits are unavailable. `RecordedTimeTests` compares that a run did not end
before it started. `WriterCommitGitTests` compares each side's commit with git:
that it is a commit of the repository and equals its `archive_commit`, that the
version `hass_mcp_engineering_beta/config.yaml` declares at it is the recorded
version, that the recorded lock hash is the sha256 of
`hass_mcp_engineering_beta/requirements.lock` at it, and, unless the clone is
shallow, that the writer commit is an ancestor of the recorded checkout commit.

In a shallow clone these tests do not compare everything. `WriterCommitGitTests`
is skipped as a class where the clone does not hold the commits the receipts
name (`tests/test_hamcp377_rollback_proof_classifier.py:1741-1742`), and its
ancestry test is skipped in any shallow clone (`:1753-1754`; the forged-fact
test likewise leaves ancestry out, `:1786-1787`). `GitBoundReceiptTests` is
skipped in a shallow clone that lacks the recorded commits (`:1571-1573`), and
both are skipped where git is unavailable (`:1566-1567`, `:1736-1737`). A run
in which any of them was skipped must be reported with those tests as skipped,
not as passed.

To regenerate all three receipts, run the one command under "What ran" into this
directory, then update the table, the identities above and the three recorded
`summary` blocks. A run from another writer commit also needs `WRITER_COMMIT`
changed to that commit in the same change.
