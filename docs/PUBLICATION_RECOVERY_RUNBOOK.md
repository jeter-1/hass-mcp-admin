# Publication recovery runbook (HAMCP-223)

When a release merges, `main` advertises the new Engineering version
immediately. Home Assistant can offer it as soon as the store refreshes. The
image only exists once **Publish reviewed Engineering release** succeeds, which
takes about 21 minutes on a clean run. This runbook covers what to do when that
window does not close on its own.

It adds detection and advice only. Every recovery action below is an owner
decision. The publisher's own guards are unchanged. They remain authoritative:
see **Missed protected-main publication recovery** in
[`CODEX_WORKFLOW.md`](CODEX_WORKFLOW.md).

## Detection: the publication watchdog

`.github/workflows/publication-watchdog.yml` runs hourly, after every publish
run completes, and on owner dispatch (optional `ref`, default `main`). It runs
`scripts/advertised_image_watchdog.py`. That script reads every add-on
configuration that the Supervisor store would read at the ref. It then checks
anonymously that `ghcr.io/<image>:<version>` is a multi-platform index with a
verified manifest for each declared architecture (`amd64` maps to
`linux/amd64`, `aarch64` to `linux/arm64`).

| Outcome | Meaning | Alert |
|---|---|---|
| `ok` | Every declared architecture is present | On `main`, closes the open watchdog issue with a comment |
| `pending` | Incomplete, but a publish run for the release commit is queued or in progress | None. Normal inside the ~21-minute window |
| `mismatch` | The image or an architecture is absent and no publish run for the release commit is live, including when that run completed without success | Opens or updates one `publication-watchdog` issue; the job fails |
| `unknown` | Registry or API responses stayed ambiguous after bounded retry (HAMCP-221 pattern), or an architecture has no reviewed platform mapping | Same as `mismatch` |

A `mismatch` or `unknown` report names the version, the release SHA, the
missing architectures, and every publish run for that SHA with its attempt and
conclusion. The issue is updated in place, never duplicated.

To check locally (read-only and anonymous):

```sh
git fetch origin main
python3 -I scripts/advertised_image_watchdog.py check --ref main
```

## Diagnosis: the recovery helper

```sh
git fetch origin main
python3 -I scripts/publication_recovery.py --version <VERSION>
# or
python3 -I scripts/publication_recovery.py --run-id <FAILED_PUBLISH_RUN_ID>
```

The helper reads the release commit, Engineering-tree drift, any staged
declaration, the claim ref `engineering-publication-attempt/v<VERSION>`, the Git
tag, the GitHub Release, both image tags and live publish runs. It prints one
verdict. When a dispatch is valid, it prints the exact owner command, with every
input checked against the publisher's input patterns. It never dispatches,
reruns or writes. `GH_TOKEN` is optional, and registry reads are anonymous.

| Verdict | Action |
|---|---|
| `published` | Nothing to recover. Refresh the store (below). |
| `wait` | A publish run is live. Rerun the helper after it completes. |
| `dispatch_fresh` | No claim and no artifacts. Josh may run the printed command. |
| `needs_digest` | The claimed run built an image. Supply `--digest` and `--build-time` (the helper says where to read them), then rerun. |
| `dispatch_digest` | Josh may run the printed digest-recovery command. |
| `roll_forward` | This version cannot be published. Merge a new reviewed release N+1. |
| `reconcile` | Partial or inconsistent state. Needs an explicit owner reconciliation decision. |
| `stop` | Inspection was incomplete: local main is stale, the release is ambiguous, a declaration is staged, or a probe is unknown. Fix the cause and rerun. |

## Failure modes

| # | Failure | Watchdog | Recovery |
|---|---|---|---|
| 1 | Post-merge full CI fails or flakes inside the publish run | `mismatch` once the run completes non-success | If `validate_source` succeeded, **Re-run failed jobs** on that publish run is safe (HAMCP-223 receipt reuse). If a unit shard or `validate_source` itself failed, use **Re-run all jobs** (`shard_manifest_count_mismatch` otherwise; beta.42). If neither applies, use the helper verdict. |
| 2 | Rerun refused with `validation_receipt_identity_mismatch` | `mismatch` | Fixed for same-run failed-jobs reruns (beta.32 attempt 2 would now pass). A different run, commit or later attempt is still refused by design; use the helper. |
| 3 | Main moved during the run (`protected_main_moved`) | `mismatch` | Helper: with no drift and no claim, `dispatch_fresh`. With a claimed failed run whose build succeeded, `needs_digest` then `dispatch_digest`. With drift, `roll_forward`. |
| 4 | Merge workflow failed after the merge, so no publish run started | `mismatch` with no publish runs listed | Helper: `dispatch_fresh` (no claim, no artifacts), or `roll_forward` if Engineering drift has occurred. |
| 5 | Concurrency replaced a pending publish run | `mismatch` (the run is listed as cancelled, or no run is listed) | Same as 4. The claim, not queue order, suppresses duplicates. |
| 6 | Cancelled, timed-out or failed run after the claim | `mismatch` | The claim forbids a fresh build. If the claimed attempt completed `failure`, was triggered by Josh and its build job succeeded: `needs_digest` then `dispatch_digest`. Otherwise `roll_forward`. |
| 7 | Tag creation partial or unknown | `mismatch` (version tag absent) or `unknown` | Helper: `reconcile`. Never delete the marker or blindly re-dispatch. |
| 8 | Engineering drift: a later merge changed `hass_mcp_engineering_beta/` | `mismatch` until N+1 publishes | Helper: `roll_forward`. Merge release N+1. The publisher refuses drift. beta.32 needed this, as beta.33. |
| 9 | Handoff refused `source_merge_not_final`, `source_job_mismatch` or `source_merge_unsuccessful` after a successful merge run | `mismatch` | GitHub can report the merge run complete before its job records settle (beta.50, HAMCP-258). The detector now re-reads the exact source attempt's jobs six times over 80 s and logs each read (`Source merge job read N/6`). If it still refuses, read those lines, then use the helper (`dispatch_fresh` when no claim exists). |

Registry visibility (anonymous pull fails) surfaces as `unknown` and as a
failed promote. Treat it as mode 7. Partial multi-architecture publication is
prevented by the publisher.

## After recovery: refresh the Home Assistant store

The Supervisor caches the store for up to 3 hours. After a successful recovery,
or after N+1 publishes, open **Settings → System → Updates** in Home Assistant.
Choose **Check for updates** from the top-right menu so the store re-reads the
repository instead of waiting for the cache to expire. Confirm the watchdog's
next `main` run reports `ok` and closes the issue.

## Boundaries

- The watchdog's permissions are `contents: read`, `actions: read`, and
  `issues: write` for the alert job only. It has no package or contents write,
  and it never dispatches, reruns, tags or publishes.
- The recovery helper is read-only. A printed command is advice for Josh's
  explicit, incident-specific authorization. It is not authorization.
- No store-branch, ruleset, merge-workflow or publisher change is part of this
  runbook.
