# HAMCP-378: created-object rollback downgrade receipt

This is preparer evidence for the release 2.11.0 Lane C governance PR (C3). It
records which runtime runs which created-object rollback plan after the
option B policy form. It is not a signed admission, CI attestation, or approval
to downgrade a live system. An in-place downgrade stays unsupported; recovery
is a full Home Assistant backup restore (HAMCP-383).

## How it was produced

[`receipt.json`](receipt.json) is the unedited receipt of:

    python scripts/hamcp378_downgrade_harness.py run --tip 29176686 \
        --work .artifacts/h378-29176686 --python <venv python> \
        --receipt .artifacts/h378-29176686.receipt.json

- Tip: `29176686f0eaf95f6415807d6df8b99b4cfa7e7f`, the C3 branch after merging
  `release/2.11.0` at `c3f48124`. Its declared version is `2.4.2-beta.15`.
  The commit that adds this directory changes only this directory, so the
  runtime and the harness are the same as at the tip.
- Harness SHA-256:
  `f32ea588c7a470e4c3ee32b69501ef7668009f8860a333e280aa736dd25bc0ce`
  (`harness_sha256` in the receipt).
- 2.4.2-beta.7: `b614be43f8c56ae06fdec0ac0bcb479b9da39715`.
- 2.4.2-beta.14: `54627e35dcfd8e972d468aec8745fd918db53ad3`.

Every tree is a `git archive` extraction of one commit. Each tree's own
unmodified code writes and reads the records through its own synthetic test
providers. No Home Assistant, socket, container or network is used, and the
receipt holds no local paths.

## Results

| Case | Pending and approved rollback plans | Applied rollback history | Provider writes |
| --- | --- | --- | --- |
| `tip_to_beta7` | all 7 refused, `policy_snapshot_mismatch` | unreadable, `policy_snapshot_mismatch` | 0 |
| `beta14_to_beta7` (control) | 4 automation and script plans executed; 3 helper and scene plans refused | readable | 4 |
| `beta14_to_tip` | all 7 refused, `policy_snapshot_mismatch` | unreadable, `policy_snapshot_mismatch` | 0 |

- `tip_to_beta7` is the HAMCP-378 requirement: beta.7 refuses every rollback
  plan the tip writes, so it cannot run an automation or script rollback as
  an ordinary delete.
- `beta14_to_beta7` reproduces the HAMCP-377 finding, so the refusal above is
  not an artefact of the reader.
- `beta14_to_tip` is the upgrade consequence disclosed in the PR and in design
  §8: old-form pending and approved rollback plans must be requested again
  after they expire, and old-form applied rollback plans count as a
  `projection_failure` in `list_plans`. Forward create plans stay readable,
  and readiness and recovery report `ok` in every case.
