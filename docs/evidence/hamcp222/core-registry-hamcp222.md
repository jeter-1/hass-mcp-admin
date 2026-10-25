# HAMCP-222: four capabilities on Core 2026.10.1 (sequence 13) and Core 2026.10.3 (sequence 14)

**Status: prepared for signing, STOPPED for the independent pre-signing review.**
Nothing is signed. The agent never read, requested or handled a private key.
This pull request publishes nothing: `upstream-trust/ha-core-release-registry.json`
is unchanged (sequence 12). Publication is a separate, later pull request (§7).

The owner signs with the reviewed runbook only:
[`OWNER-SIGNING-RUNBOOK.md`](OWNER-SIGNING-RUNBOOK.md).

| Step | Operation | Result sequence |
|---|---|---|
| 1 | `extend-capabilities` 2026.10.1: append the four contracts below (22 → 26 references) | 13 |
| 2 | `add` 2026.10.3 with the same 26 references as the signed 2026.10.1 entry | 14 |

## What you are signing, in plain English

### Sequence 13: extend Core 2026.10.1

Home Assistant Core 2026.10.1 (the support-window *current* release) keeps its
22 signed capabilities and gains four. After this, Engineering may use these
four operations against 2026.10.1 when its own runtime checks also pass:

| Capability | What it allows | Contract fingerprint |
|---|---|---|
| `core.entity_registry_availability_operation` | governed enable/disable of an entity in the entity registry | `sha256:ae292fbc16ad82c8d8347b7e2eb1c3a7e8c7644d95783f5bf2ae9f9cc63a76b2` |
| `core.entity_assistant_exposure_operation` | governed change of an entity's voice-assistant exposure | `sha256:474c8ccaaae83c12f56a2848995ff089097936568a2ad5dc8d25ce855d51dbbd` |
| `core.label_registry_operation` | governed label create and update (no label delete) | `sha256:b2f0dd11ff243aa2559c27738d962785e37455d37b836cb5a6997aeabb26813c` |
| `core.configuration_delete_operation` | governed automation/script delete with a captured pre-image | `sha256:5688af5d141b97ca33eadf251ecfbb8f3b1f833024b0ee1acea4973f558f887d` |

Each is still an immutable plan with exact-hash external approval, governed
apply and read-back verification; the registry only says the contract is
reviewed for this Core release. Nothing else changes: 2026.10.0 keeps its 22,
the release identity, image digests and probe of 2026.10.1 are unchanged, and
the revocations (2026.9.2, 2026.9.3, 2026.9.4) are unchanged.

- Candidate: `candidates/candidate-1-extend-2026.10.1.json`,
  SHA256 **`923bd135b02618046552786261036d53dced703be05da93f27c1f80510072458`**,
  generated 2026-10-25T03:00:00Z, expires 2027-01-23T03:00:00Z.
- Fenced to the production sequence-12 journal SHA256 `089247ca…85e6`
  (content digest `sha256:9d14f06a…e5fc`).
- Review evidence: `inputs/evidence-extend-2026.10.1.json`, SHA256
  `367a9303e10463029ceaaa1620769412e526a5a8cbf1cc803c074f87f1009cf4`; entry
  `inputs/entry-extend-2026.10.1.json`, `db8e9c77…4de9`.
- Executed evidence: real-Core CI, Actions run **40148395528 attempt 2**, job
  **121050685046** `Real HA ha-2026-10-1 (current)` (§2).

### Sequence 14: add Core 2026.10.3

Core 2026.10.3 (the support-window *candidate*; PR #305) gets a new signed
entry carrying exactly the same 26 contracts and the same probe profile as the
sequence-13 2026.10.1 entry. Until sequence 14 is published, Engineering on
2026.10.3 has no Core authority at all (`incompatible`).

| Field | Value |
|---|---|
| `entry_id` | `ha-core-2026.10.3-reviewed-v1` |
| Source commit / tree | `1f214b8461dd1fbbd66e10765c6905f49d3c1d86` / `7c592b097111f15031bbfbc37edbbb42f8235320` |
| Source archive SHA256 | `57a15d2dcabb8703c9b9d08e8c243e8a7130db8cb8618e1c799ae46e1c75e4e9` |
| Image index digest | `sha256:407b89cd11a8cb67d388d21739a226c74acbbebe1ac59e3b83b11b43ce76b98f` |
| amd64 / arm64 manifests | `sha256:c32ab33f…2127` / `sha256:c2bb99de…c44c` |
| Probe profile | `core-probes-child-device-name-parts-v1`, `sha256:8dad86e4…eb6b` (unchanged) |
| Capabilities | the 26 of the sequence-13 2026.10.1 entry, byte-identical |

- Inputs: `inputs/entry-add-2026.10.3.json` `98cdad1001cc9312a5bf82ac1d6ced3e22da4ae8369c2b15772fd6a2172ae31d`,
  `inputs/evidence-add-2026.10.3.json` `e245e6347b5327f746aa15085aa690d7a14add3f1fa35d0980ba86d8837a9f01`.
- The candidate is **prepared by the owner from the signed sequence-13
  journal** (runbook step 2), so its SHA256 is not known in advance.
  `check_step.py candidate 2` proves it is the reviewed operation before signing.
- Executed evidence: the same run, job **121050685050**
  `Real HA ha-2026-10-3 (candidate)` (§2).
- Upstream review: §3.

## 1. Source and deployment identity

| Identity | Value |
|---|---|
| Branch base and current `origin/main` | `587266d26f66d3d257751ef11125d0e8aaf7ae5d` (fetched 2026-10-25; main has not moved) |
| Contains | PR #305 head `c1c0b023` (2026.10.3 support-window candidate, still draft) and main merge `b839b471` |
| Deployed Engineering | 2.4.1-beta.36, build `16b724ec` (architect-verified; not observed by the agent) |
| beta.36 runtime tree (`ha_mcp_engineering`) | `70479fa2958aafd36d0e74e3082f98c50b11053c` |
| beta.35 | `904950279ea7656c84d7c7971f9a0378fcaccf4d`; `ha_core_readmission` identical to beta.36 |
| Production journal | sequence 12, SHA256 `089247cafa29b3ab0c1687a63419d999ae9c894fa0a94248f73008eabf6785e6`, content digest `sha256:9d14f06ab0a0870404540ea8cfa70cefe05cc243bebb9fab0b090121081be5fc`, expires 2027-01-13T13:40:21Z |
| Public key | `core-public-key.txt` (byte-identical to `../hamcp182/core-public-key.txt`), raw-key SHA256 `7793e80f480b573fbef4e7ca762ff9d1ac783fee1e253db7c35ba8fe21f63dac` |
| `scripts/prepare_core_release_registry.py` | SHA256 `6e09d3f9…b368` (unchanged since HAMCP-182) |

The live Home Assistant Core version was not observed (no live access).

## 2. Receipts: run 40148395528 attempt 2

`receipts/manifest.json` (`14886982…5e3c`) records the run: `pull_request`
event of PR #306, head `902690a5…6408`, merge `8c9b44e8…923a`, conclusion
`success`. PR #306 differs from this package outside `docs/` only by enabling
the dormant seq-13 lanes; the binding to beta.36 is by the identical runtime
tree `70479fa2…`. Release tag `v2.4.1-beta.36`; predecessor capabilities hash
`1fdf85c4…6d35`; verified at commit `4604ad1c`. All receipts are byte-identical
copies under `receipts/<version>/` and pass the shipped verifiers.

| Core (slot, job) | Lane | Transition | Scenarios | Receipt SHA256 |
|---|---|---|---|---|
| 2026.10.1 (current, 121050685046) | availability + exposure | 22 → 24 | 8 | `4fea89b68cbf88149dc2733f0a6f88dcacad95a548cf0330d6fb00b11077fc12` |
| | label registry | 22 → 23 | 7 | `537d4bf672b9d7842db321a7762ddfb70822048042bfa6070c3cb11ab1cd3dbb` |
| | configuration delete | 22 → 23 | 7 | `1baa7b28e9f1ba16b69576963e89c1414c7ea0e3263285d052fbc7b88aee4202` |
| | entity metadata (signed sibling) | 21 → 22 | 9 | `49893efb932272ede74582eb8502bc102bc99be359f779ae8aae6ef5cee6ef91` |
| | alarmo cleanup | | | `56fd7e1ffe6d5ac3e35d4c7601e97a7f25156d334bf7c5272c45289bc9d64d2c` |
| 2026.10.3 (candidate, 121050685050) | availability + exposure | 22 → 24 | 8 | `0eb0737a200527cdbb993718880138f0574320f863e960b41b60806ec22cb017` |
| | label registry | 22 → 23 | 7 | `d2276c1bbd0a1270e4ffa9dad76e8eab97ca5d8883b0754b2c93349362b84eb6` |
| | configuration delete | 22 → 23 | 7 | `d552760824ae69e5d91963ee148a4acb8d0ee82b946be404d3ba45b998fe5250` |
| | entity metadata | 21 → 22 | 9 | `e0f65ad65e7c549071aba66670f3d061968eb23ff987778f8095fa1c1cbbd24b` |
| | alarmo cleanup | 19 → 20 | | `a6022faf0467cbd233cd8a234483cf91d657e8f288f10fa0ecccb14ad58df918` |

Artifact zips (SHA256): 2026.10.1 contracts `3963cd5d…7277`, delete
`7ccdc1f2…f483`, entity metadata `8c754583…c0dd`; 2026.10.3 contracts
`739ce27d…447b`, delete `3477ecdd…4792`, entity metadata `f2d790ab…51a2`. Every
lane reports `fixture_cleanup: true` and the fingerprints in the table above.
The job logs are kept outside the repository at
`/var/tmp/hamcp222-seq13/logs/run40148395528/attempt2` (10.3 job log SHA256
`4418ab10…3e42`).

## 3. Core 2026.10.3 review (sequence 14)

`artifacts-2026.10.3/` holds the hash-bound public artifacts (release, ref,
commit and OCI JSON, `raw/SHA256SUMS`) and the review: `assessment.json`
(`a4315011f1730aaea5af50118e1c28115a6d0c25bf3c1b9c5944802a81f398ec`), the
changed-file list and the flagged diffs from 2026.10.1 (`abe1fdd7`, archive
`a4305046…81c4`). The tag commit `1f214b84` is the merge of the 2026.10.2 tag
`8d7fd8b6`; release published 2026-10-23T18:50:09Z; GitHub's commit
verification is `valid`. ha-mcp is 8.5.0 (unchanged).

Of the 26 capabilities, 20 rely on no changed Core path. Six rely on a changed
path and are assessed as satisfied, with cited lines asserted by
`tools/make_assessment.py`:

- `core.basic_websocket_read`: a session whose user is deleted is now closed;
  Engineering never deletes users (negative grep in `tools/assert-result.json`).
- `core.configuration_validation`, `core.governed_configuration_operation`:
  2026.10.3 accepts multi-state `for` state conditions that 2026.10.1 rejected.
  The newly valid shape is not executed against a real 2026.10.3.
- `core.entity_registry_availability_operation`,
  `core.entity_assistant_exposure_operation`, `core.f3_mutation_verification`:
  changed relied-on files, assessed as not changing the paths Engineering uses
  (`assessment.json`), and executed by the 2026.10.3 lanes in §2.

Owner decisions applied here:

- The 2026.10.1 evidence wording that `core.entity_registry_metadata_operation`
  is "NOT included" is dropped for 2026.10.3: that reference is one of the 26.
- 2026-10-25 (Josh): the real-Core delete lane proves only the elevated
  (protected, high-risk) delete path. The lane's dependency runtime has no
  script reader, so every plan carries
  `dependency_evidence_incomplete:script_evidence_unavailable` and the OD-4
  incomplete-evidence rule marks it protected. The standard (medium) path is
  covered offline only. A lane script reader is a later follow-up.

## 4. Step checker and rehearsal (ephemeral key)

- `check_step.py` (SHA256 `54f38c6afab7eca663870c0fb991f66c99363a8356ff30caafa4fa10d2dc007a`)
  pins the four inputs and the production key fingerprint. It reads only the
  public key and uses the shipped loader and transition validator. Candidate 1
  against the production journal prints sequence 13, references
  `{'2026.10.0': 22, '2026.10.1': 26}`, `Candidate SHA256: 923bd135…2458`, `OK`.
- 42/42 checker mutations are refused (`rehearsal/check-step-controls.json`; corrected
  from 44 after the pre-signing review, L2).
- The rehearsal (`rehearsal/manifest.json`) signs the full sequence with an
  in-memory ephemeral key (`b8d6a566…`) over a seq-12 twin
  (`journal-twin-seq12.json`, `3eed9e94…`). The rehearsed candidate 1 equals
  the committed one apart from key and predecessor file. Planned journals:
  step 1 `46650bbc…`, step 2 `111fdec6…`. 12/12 signing-utility controls are
  refused (stale predecessor, relabel, wrong key, wrong expected SHA, expired
  predecessor, replay, renewal, unknown contract, re-add of a revoked release).

## 5. Runtime check on beta.36 and beta.35

`harness/runtime_check.py` (`2bf35d7f…`) imports the **released runtime's own**
`ha_core_readmission` from a `git archive` of the release commit and feeds it
the rehearsed signed journals through its own verifier and cache. Synthetic
Core snapshots only; no network, no Home Assistant.

| Report | SHA256 | Result |
|---|---|---|
| `rehearsal/runtime/runtime-beta36-16b724ec.json` | `9dd1c065…` | **PASS**, 0 failures |
| `rehearsal/runtime/runtime-beta35-90495027.json` | `5ec48bae…` | **PASS**, 0 failures |
| `rehearsal/runtime/runtime-check-controls.json` | `bb96083a…` | 5/5 negative controls refused |
| `rehearsal/runtime/older-runtime-beta34-fff324bb.json` | `adbc4fc6…` | observation (§5.2) |

Asserted on both runtimes:

- Sequences advance 12 → 13 → 14. Applying both steps equals jumping from 12
  to 14. A restart from the cache restores sequence 14. Offering sequence 12 or
  13 again is refused and 14 stays.
- **2026.10.1:** at sequence 13 exactly the baseline 22 plus the four are
  admitted (compatible +4); each new capability is routable and a lease is
  acquirable, including the planner and F3 verification pairs. Unchanged at 14.
- **2026.10.3:** no authority at 12 and 13; at 14 the identical 26 as
  2026.10.1 with the same probe. A 2026.10.3 lease cannot be acquired before
  sequence 14 and can be after.
- **2026.10.0:** identical admission at 12, 13 and 14, never interrupted.
- Revoked (2026.9.2–9.4) and never-listed (2026.10.2) versions never change.
- Negative controls: no extension, steps out of order, production key reading
  rehearsal journals, final journal without the add, and an expired check
  time each fail the check.

### 5.1 What a running add-on sees when the registry advances (disclosure)

Observed with one runtime kept running (no restart) across 12 → 13 → 14:

- The first verification after sequence 13 moves 2026.10.1 to `confirming`
  with **0 admitted**: the entry changed, so the HAMCP-215 restoration gate
  re-confirms it. The next verification restores all 26. In production the
  confirmation spacing is 15 s with up to 20 % jitter (longer after repeated
  flaps), so **2026.10.1's Core authority is withheld for about 15–18 s once**.
  2026.10.3 behaves the same at sequence 14 (from no authority to 26).
- 2026.10.0 is never interrupted.
- A 2026.10.1 configuration lease held while the registry advances fails its
  in-flight revalidation and is released cleanly (`in_flight_revalidated:
  false`, `finished: true`); a new lease succeeds after the confirmation. This
  is the fail-closed behavior: an operation in flight at that moment is
  refused and must be retried. Leases on unchanged entries revalidate.
- A restart of the add-on admits immediately through the startup path. That is
  a live action Josh would choose; it is not required.

### 5.2 Engineering rollback below beta.35

beta.34 compiles 25 of the 26 profiles (no `core.configuration_delete_operation`).
With the rehearsed journals it admits 25 of the 26 references on 2026.10.1
(and on 2026.10.3 at sequence 14) and 22 on 2026.10.0. An uncompiled
reference drops only itself, not the entry. An Engineering rollback to beta.34
is therefore safe for this journal; delete stays unavailable there.

## 6. Signing timeline

- Candidate 1 is valid to sign from 2026-10-25T03:00:00Z. The extension also
  requires a current predecessor, so **sign step 1 before the sequence-12 tip
  expires at 2027-01-13T13:40:21Z**.
- Step 2 is prepared after step 1 is signed. Its candidate is generated at
  preparation time and expires 90 days later; the published sequence-14 tip
  expires 90 days after step 2 is prepared.

## 7. Publication, refresh, rollback

**Publication is the merge of a later draft pull request** that copies the
signed sequence-14 journal byte-identically into
`upstream-trust/ha-core-release-registry.json`. This pull request publishes
nothing. That later pull request must also:

- update the tests that pin the production tip, which change only at
  publication: `test_hamcp155_configuration_delete_admission`,
  `test_hamcp069b_entity_availability_exposure_admission`,
  `test_hamcp165_label_registry` ("no signed envelope has ever referenced it",
  sequence 12), `test_hamcp129_platform_reads` (tip 12, entries 10.0/10.1) and
  `test_hamcp069a_entity_metadata_readmission` (tip 12, "exactly 2026.10.0
  and 2026.10.1");
- re-run the reviewed checker and the runtime check on the real signed files;
- follow PR #305 (2026.10.3 as support-window candidate), which merges first.

How the signed registry reaches the running add-on (beta.36 source,
unchanged from HAMCP-182 §7):

| Behavior | Detail |
|---|---|
| Source | `https://raw.githubusercontent.com/jeter-1/hass-mcp-admin/main/upstream-trust/ha-core-release-registry.json` |
| Refresh | Inside Core reconciliation (every 300 s): first reconcile, then at most every 6 h. A running Core version with no entry refreshes early, at most once per 60 s. |
| Cache | `/data/ha-core-release-registry-cache.json`, survives restarts |
| Older sequences | Never accepted; revocations are retained |
| Key | the add-on option `ha_core_release_registry_public_key` must be the key above and `ha_core_release_registry_enabled` true (both owner-set; not observed) |

**No Engineering release, image or add-on update is needed.** beta.35 and
beta.36 already compile all 26 contracts (§5); the registry is data fetched
from `main`. After the publication merge a running add-on on 2026.10.1 picks
up sequence 14 within about 6 h; on 2026.10.3 within about a minute. A
restart is optional.

**Rollback.** Reverting the file cannot un-publish a signed journal, because
the runtime refuses older sequences. Withdrawing a capability or a release
requires a new owner-signed sequence: `revoke` the release, or re-issue the
entry with fewer references through the reviewed preparation path. Rollback of
Engineering to beta.34 is covered in §5.2.

## 8. Signing boundary

- The agent used only the production **public** key. Rehearsal keys were
  generated in memory and never written outside a private temporary directory.
- Josh signs with `../hamcp182/sign_step.py` (`43491477…1381`), the reviewed
  PEM signer, under the runbook only. It changes only how the key is loaded;
  signing is the shipped `sign_candidate` and output the shipped `write_new`.
  It refuses a key whose public fingerprint is not `7793e80f…3dac`, reads an
  encrypted PEM's passphrase only at a verified hidden prompt, and never
  prints key material.
- Any deviation from the runbook voids this review: stop and report instead.

## 9. Limitations

- Execution is one `pull_request` CI run of PR #306; the 2026.10.3 lane ran in
  the candidate slot. amd64 only: arm64 is bound but not executed.
- OCI attestation and SLSA provenance signatures were not cryptographically
  verified; digests and declared revisions are recorded as observed.
- The 2026.10.3 image base moves from homeassistant-base 2026.07.0 to 2026.10.1
  and dependency pins change (aiohttp, frontend, uv, pycares, bitstring,
  pypasser). Base-image and package contents, including the exact Python patch
  version, were not reviewed (unknown).
- The newly valid multi-state `for` condition is not executed on 2026.10.3.
- The real-Core delete lane proves only the elevated path (owner decision, §3).
- The stale `PENDING_ADMISSION_TEXT` in the runtime's
  `entity_availability_exposure.py` (it still says the capability awaits
  admission) is disclosed and left unchanged by Josh's direction; the runtime
  is a protected path outside this task.
- The runtime check is an offline import-level check of the released
  authority code with synthetic Core snapshots, not a running add-on.
- The live Core version and the deployed add-on options were not observed.
- No household physical effects, Supervisor lifecycle, add-on continuity,
  all-custom-integration coverage or ha-mcp claim.
