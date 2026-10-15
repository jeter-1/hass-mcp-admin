# HAMCP-182: entity-metadata extension of Core 2026.10.1 / 2026.10.0 and revocation of 2026.9.4

**Status: Part 1 prepared, STOPPED for the Codex pre-signing review.** This
pull request changes no journal: `upstream-trust/ha-core-release-registry.json`
is byte-identical to `main` (sequence 9). Nothing here was signed with the
production key, and no private key was read, requested or handled by the agent.

The proposed operations are, in this order:

| Step | Operation | Result sequence |
|---|---|---|
| 1 | `extend-capabilities` 2026.10.1: append `core.entity_registry_metadata_operation` (21 → 22 references) | 10 |
| 2 | `extend-capabilities` 2026.10.0: the same reference (21 → 22) | 11 |
| 3 | `revoke` 2026.9.4 (outside the CI support window) | 12 |

The owner signs with the reviewed runbook only:
[`OWNER-SIGNING-RUNBOOK.md`](OWNER-SIGNING-RUNBOOK.md).

## 1. Source and deployment identity

| Identity | Value |
|---|---|
| Branch base and current `origin/main` | `690a00b2fcbffc4e90c97a1cad36ccf6168e8ec1` (fetched 2026-10-15; main has not moved) |
| Deployed Engineering | 2.4.1-beta.9, build `690a00b2…8ec1` (owner report: accepted live) |
| beta.9 runtime tree (`ha_mcp_engineering`) | `c363397b2cabc567394b9cdde9daffe9a0bfb5bd` |
| Rollback Engineering | 2.4.1-beta.8, `7370ad25eb98e03347d73e152f38881895477d74`, runtime tree `b4736c81…` |
| Shared by beta.8 and beta.9 | `ha_core_readmission` tree `3f6f66e9…`; `scripts/prepare_core_release_registry.py` blob `1ace9bcc66892acc22462a94bd42631bacc92ef9` (SHA256 `6e09d3f9…b368`); journal blob `d8b535a9…` |
| beta.8 → beta.9 runtime delta | `f3_configuration/adapter.py`, `governance/entity_metadata.py`, `version.py` (the HAMCP-183 fix and the version) |
| Production journal | sequence 9, SHA256 `ecaccd79ff79582f633e1406546a844ee4b38920703e4192e6463162dd4a4402`, content digest `sha256:9a5431ccc32992df336a6119c6d90f2c91be5d93328c1d9cda7377ae75d8d89d`, expires 2027-01-10T21:12:05Z |
| Production public key | `core-public-key.txt` (byte-identical copy of `../hamcp161/core-public-key.txt`), raw-key SHA256 `7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac` |
| Support window (`ci/support-window.yaml` at 690a00b) | candidate `null`, current 2026.10.1, rollback 2026.10.0 |

The live Home Assistant Core version was not observed (no live access). The
operations are safe for whichever of 2026.10.0 / 2026.10.1 is running; see §6
for 2026.9.4.

## 2. Receipts: main full-tier CI, Actions run 38681524688

The owner brief calls this run "CI #1099" with "13/13 jobs". The GitHub API
reports **run_number 1100**, attempt 1, `workflow_dispatch` on `main`, head
`690a00b2…8ec1`, conclusion `success`, with **24 jobs, all successful**. The run
ID, head and artifacts match the brief; only the label and job count differ.
The HAMCP-161 "CI #1086" receipts are not used.

| Slot | Core | Artifact (ID) | Zip SHA256 | Entity receipt SHA256 | Cleanup receipt SHA256 |
|---|---|---|---|---|---|
| current | 2026.10.1 | `entity-metadata-core-2026.10.1-current-amd64` (11948098495) | `124950f1…2114` | `4ff155a18c34f668bae723ee16da69a1eb8448b29f43f279ce07b6b59c824c51` | `f1fdc9dfcee20794feba9bf712f748cf64b09b397c738ca629af4d7f8676b09e` |
| rollback | 2026.10.0 | `entity-metadata-core-2026.10.0-rollback-amd64` (11948661527) | `8dc5a32b…c9e4` | `52b8ecff4fbc50c984e8d194ab46d45633b00184ef438bfb5a71e51bee288d0d` | `3d0dd3de40d09cea7929aec63096fdc399ed275d75b3fcf3084e2ead47870c75` |

The receipts are byte-identical copies in `receipts/<slot>/`;
`receipts/manifest.json` records the run, artifacts, digests and verifier output.

**Offline verification.** The shipped verifier was run at 690a00b. Current main
is the same commit, so this one run covers both. The verifier is
`entity_metadata_contract_acceptance.verify_receipt_files`, which applies the
strict duplicate-key loader, the window and current source.

| Slot | Result |
|---|---|
| 2026.10.1 (current) | **PASS** |
| 2026.10.0 (rollback) | **PASS** |

Both slots show 9 scenarios, `fixture_cleanup: true`, `authority_transition`
`[21, 22]` and fingerprint `sha256:cc09630e…f767` (`part0/receipt-verify-690a00b.json`).

`harness/make_inputs.py` additionally asserts, for each receipt:
- `engineering_source` equals `{commit: 690a00b…, runtime_tree: c363397b…}`, so
  **the runtime-tree binding holds for deployed beta.9**;
- `added_capability` is exactly the entity reference;
- `image_index_digest` equals the production entry;
- `predecessor_capabilities_sha256` is the hash of the production entry's exact
  21 references.

Each cleanup receipt binds its entity receipt's SHA256.

## 3. Inputs (`inputs/`)

| File | SHA256 |
|---|---|
| `entry-extend-2026.10.1.json` | `3bba61e410205023128a816b1979e41b6d8b31226c932daa6c43b54a0cbfa366` |
| `evidence-extend-2026.10.1.json` | `5271e4580ca7bd693a0cea63a53c58a892bab635c54cefac06cb216f38c59f40` |
| `entry-extend-2026.10.0.json` | `c31779cbce35dd68dc0fb245d0369e9d6bd589ac7d36cfe08f068b585e67b462` |
| `evidence-extend-2026.10.0.json` | `3adb28b6e69b959cee6b03b6827b1b3f0a80bbb68268b0d7da65d385bd22639f` |
| `entry-revoke-2026.9.4.json` | `fc039f0ecddcc772c906ffad64864b0440c9dd78e34781a4aa1f9241b88c0e1e` |
| `evidence-revoke-2026.9.4.json` | `539556f2f02fb7ed3d08e23096cb2574a0f0bdf0bded8024d37f24cdbb1b68db` |

- **Extension evidence** is canonical `core-capability-extension-evidence-v1`,
  with only the closed fields the prep tool accepts:
  - `previous_entry_sha256` is the hash of the exact signed sequence-9 entry;
  - `previous_capabilities` holds that entry's 21 references;
  - `added_capabilities` holds the one entity reference;
  - `review_artifacts_sha256` = `[entity receipt, cleanup receipt, artifact zip]`.

  `prepare.require_extension_evidence` accepts each one.
- **Extension entries** are the signed entries with the reference appended
  after the unchanged ordered prefix, and `evidence_sha256` rebound. No other
  field changes.
- **Revocation evidence** is `core-release-withdrawal-evidence-v1`. It records:
  - the owner direction (2026-10-14: revoke 2026.9.4, outside the support
    window);
  - the retained releases;
  - the window file blob and hash, and the journal it is withdrawn from;
  - the exact withdrawn entry and its hash;
  - the §6 dependency review.

  The revocation entry is the exact signed 2026.9.4 entry with
  `evidence_sha256` rebound.
- `REASON` = `outside the CI support window; retired by owner direction (HAMCP-182)`.

## 4. Candidate 1 (prepared, not signed)

`candidates/candidate-1-extend-2026.10.1.json`:
- **SHA256 `d9607779d6f7945083dc1ef8126857c880901653e33b32cae808eaece16488d4`**;
- prepared with the shipped `prepare_core_release_registry.py extend-capabilities`
  from the production sequence-9 journal and the production public key;
- `generated_at` 2026-10-15T10:31:21Z, `expires_at` 2027-01-13T10:31:21Z;
- sequence 10, predecessor `sha256:9a5431cc…d89d`.

`check_step.py candidate 1` passes on it against the production journal.
Candidates 2 and 3 depend on the bytes Josh signs, so their hashes cannot be
precomputed. The runbook prepares them from the previous signed step.
`check_step.py` then content-checks each one before it is signed.

`check_step.py` (public key only) pins the six input hashes and requires:
- the reviewed key fingerprint, the step's predecessor sequence and the exact
  predecessor bytes;
- a canonical candidate with the exact field set, model and operation;
- the direct-successor sequence and digest, and the evidence binding;
- a 90-day expiry.

For an extension it also requires:
- `extension_review` equal to the reviewed evidence bytes;
- the entry equal to the signed entry plus exactly the entity reference;
- all siblings and revocations unchanged.

For the revocation it requires the exact entry, the removal of only that entry,
and exactly one appended tombstone with the reviewed reason.

In `signed` mode it also verifies the signed journal with the public key, the
tip against the candidate, the chain, and that the earlier envelopes are
unchanged.

## 5. Part 0: rehearsal on deployed beta.9 and rollback beta.8 (ephemeral key)

The production key is never needed. `harness/build_journals.py` re-signs the
unchanged sequence-9 envelopes with a fresh in-memory key. The content digests,
sequence numbers and chain are identical, and the per-version observations
equal production's. It then applies the three planned operations with the
shipped `prepare_candidate`/`sign_candidate` and the real inputs.

**Rehearsal chain** (`part0/rehearsal-manifest.json`):

| Sequence | Change |
|---|---|
| 10 | 2026.10.1 has 22 references |
| 11 | 2026.10.0 has 22 references |
| 12 | 2026.9.4 revoked; 2026.10.x have 22 each; revoked {9.2, 9.3, 9.4} |

**Signing-utility negative controls (8/8 refused):**
- stale predecessor, two forms: `current journal changed after review`;
- extension relabeled `add`: `candidate fields invalid`;
- journal read with another public key: `registry_invalid_signature`;
- reviewed candidate signed with another private key: `reviewed Core key mismatch`;
- wrong expected SHA: `reviewed candidate hash mismatch`;
- replayed extension on sequence 12: `extension requires exactly one existing changed entry`;
- re-adding 2026.9.4: `revoked release cannot be re-added`.

**Runtime check.** `harness/runtime_check.py` imports each `git archive` source
extract and observes the `CoreRuntime` authority boundary with the rehearsal
key. Reports: `part0/runtime-beta9-690a00b.json` and
`part0/runtime-beta8-7370ad25.json`. Both **PASS**, and the `result` objects
are identical.

| Version | Baseline (seq 9) | Final (seq 12) |
|---|---|---|
| 2026.10.1 | 21 compatible, entity unavailable | 22 compatible, entity available, exact-target acquire succeeds |
| 2026.10.0 | 21 compatible, entity unavailable | 22 compatible, entity available, exact-target acquire succeeds |
| 2026.9.4 | 21 compatible | revoked: 0 compatible, 22 deny-only, no new lease |
| 2026.9.2 / 2026.9.3 | revoked | revoked (unchanged) |

- **Stepwise:**
  - seq 10: the entity capability is on 10.1 only;
  - seq 11: on both 10.x;
  - seq 12: 9.4 is revoked.

  The stepwise final state equals the direct jump.
- **Cache and refresh:** a restart from the cache stays at sequence 12, and a
  rollback refresh to sequence 9 is refused (the tip stays at 12).
- **Other checks:** no fallback, no compiled authority, and no fan/power legacy
  pin at any of these versions.
- **Selection tokens change** on 10.0 and 10.1, as the extension intends.

**Lease and plan disclosure.** An in-flight configuration lease on 10.0 or 10.1
at the refresh is **not revalidated** (fail closed): that one operation is
refused, and new leases succeed. A lease on 9.4 is not renewed. Part 0 did not
exercise approved-but-unapplied plans. A plan bound to the old selection token
may need to be re-created after publication.

**Owner-path rehearsal** (`harness/runbook_rehearsal.py`, `part0/runbook-rehearsal.json`).
This runs the runbook's exact commands as subprocesses:

- the shipped prepare CLI;
- `check_step.py candidate`;
- `sign_step.py`, with an unencrypted PEM for steps 1 and 3 and a
  passphrase-encrypted PEM for step 2;
- `check_step.py signed`.

Results:
- **All 3 steps pass.**
- **7/7 signer refusals:** another Ed25519 PEM, a P-256 PEM, a wrong
  passphrase, a wrong expected SHA, a stale predecessor, the default
  (production) fingerprint with the rehearsal key, and an existing output.
- No output contains the passphrase, the raw private key or PEM text.

**Checker controls** (`harness/check_step_controls.py`,
`part0/check-step-controls.json`): all 6 planned step/mode checks print `OK`,
and all **16 mutations are refused**. The mutations are:
- wrong step, wrong predecessor, or a step-1 output offered for step 2;
- wrong key, or the production fingerprint with the rehearsal key;
- non-canonical bytes;
- relabeled operation or swapped model;
- removed or altered `extension_review`;
- an extra reference, an altered sibling, or altered revocations;
- expiry beyond 90 days;
- an altered or dropped tombstone.

### 5.1 beta.8 rollback: the journal is safe, the entity-metadata tool is NOT

The journal is **safe on beta.8**. beta.8 parses, accepts and caches
sequences 10–12 exactly as beta.9 does: the prep tool, the
`ha_core_readmission` tree and the result object are identical. A rollback to
beta.8 keeps every other capability's authority unchanged, revokes 9.4, and
refuses a registry rollback.

The entity-metadata capability is **also available on beta.8** once the journal
is at sequence 10 or later. beta.8 lacks the HAMCP-183 fix (OD-15): after
`config/entity_registry/update` is sent, beta.8 treats any error result as
proof of non-mutation. Core 2026.10 can return `unknown_error` after the
registry has already applied the change, so beta.8 can record an applied
update as not applied, without the registry readback beta.9 performs.

> **`create_entity_metadata_plan` and the entity-metadata apply path must NOT
> be used while Engineering 2.4.1-beta.8 is running.** A rollback to beta.8
> restores the error-result defect. If Engineering is rolled back to beta.8
> after publication, do not create, approve or apply entity-metadata plans
> until a release containing the HAMCP-183 fix is running again. This is an
> operating rule, not an enforced runtime refusal: beta.8 will offer the tool.

## 6. Revocation of 2026.9.4: dependency review

- `compiled_exact_authority("2026.9.4")` is empty at beta.9 and beta.8.
- The only fan/power legacy receipt pin is 2026.9.2, which is already revoked.
  No authority depends on 9.4.
- `dashboard_analysis/rules.py` `CORE_VERSION = "2026.9.4"` selects the default
  dashboard-analysis rules when the observed Core is 2026.9.4. It grants no
  authority.
- `f3_dashboard/atomicity.py` cites 2026.9.4 as static ha-mcp 8.6.0 source
  evidence. It grants no authority.
- **Consequence:** if a Home Assistant running Core 2026.9.4 observes sequence 12,
  all Core authority on it is denied, the same as for 9.2 and 9.3. 2026.9.4 is
  outside the support window.

## 7. Publication, refresh, rollback

**Publication is the merge of a later Part 2 change** that copies the signed
sequence-12 journal into `upstream-trust/ha-core-release-registry.json`. This PR
does not publish anything. The runtime fetches raw `main` with the behavior
documented in
[HAMCP-161 §6.5](../hamcp161/core-registry-hamcp161.md#65-publication-refresh-and-cache),
which is unchanged in beta.8/beta.9:

| Behavior | Detail |
|---|---|
| Refresh | First Core reconcile, then every 6 h. An unknown release triggers an early refresh, at most once per 60 s per version. |
| Cache | `/data/ha-core-release-registry-cache.json`, survives restarts |
| Older sequences | Never accepted; revocations are retained |

**Rollback.** A signed journal cannot be un-published by reverting the file,
because the runtime refuses older sequences. Withdrawing the entity capability
again requires a new owner-signed sequence: revoke or re-issue the 10.x
entries. An Engineering rollback to beta.8 is covered in §5.1.

## 8. Signing boundary

- The agent used only the production **public** key. The ephemeral rehearsal
  keys were generated in memory, written as PEMs only into a private
  `TemporaryDirectory` (mode 0700, files 0600), and deleted at exit.
- Josh signs with `sign_step.py`, the **reviewed** PEM signer, under the reviewed
  runbook only. It replaces the unreviewed per-step PEM helpers of
  [HAMCP-161 §6.4](../hamcp161/core-registry-hamcp161.md#64-signing-path-deviation).
  - It changes only how the key is loaded.
  - Signing is the shipped `sign_candidate`, and output is the shipped
    `write_new`.
  - It refuses a key whose public fingerprint is not `7793e80f…3dac`.
  - It never prints or stores key material.
  - Every failure exits with a fixed message.
- Any deviation from the runbook voids this review: a different signer, an
  edited command, or an unreviewed helper. Stop and report instead.

## 9. Limitations

- The receipts come from synthetic disposable Core containers (see
  [HAMCP-179](../hamcp179/entity-metadata-container-evidence.md#disclosed-limitations)).
  They prove the registry contract, not installed Supervisor lineage.
- Part 0 is an offline import-level check of the authority boundary. It is not
  a running add-on.
- The beta.8 rule in §5.1 is procedural.
- Approved-but-unapplied plans across the selection-token change were not
  exercised.
- The live Core version was not observed.
