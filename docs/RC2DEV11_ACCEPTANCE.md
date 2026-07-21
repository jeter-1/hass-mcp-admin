# RC2dev11 acceptance

This checklist prepares production registry operations without generating a
production key or changing GitHub/Home Assistant.

## Repository acceptance

1. Confirm the branch is based on accepted commit `657df74e7122b123faf6cfa51e709e31ffc254a5`.
2. Run Python compilation, YAML/workflow parsing, metadata/dependency validation,
   secret scan and `git diff --check`.
3. Run the focused lifecycle, workflow, admission, observability, dashboard,
   authentication/audit, governance and dependency-index suites.
4. Run `python scripts/rc2dev11_registry_acceptance.py`; confirm it uses only
   temporary directories, synthetic keys and injected transport/clock.
5. Run the complete repository suite with no unexpected skips.
6. Validate production/Beta containers and the declared amd64, arm64 and arm/v7
   build matrix without pushing.
7. Confirm public tool schemas and counts remain 40 / 25 / 0, governance records
   need no migration, and stable v1.1.2 has no diff.

## Lifecycle fixture acceptance

- Bootstrap produces sequence 1 and exactly registry, signature, release
  evidence and index; bootstrap dry-run creates nothing.
- Add refuses duplicate IDs and exact identities.
- Revoke N+1 rejects admission and blocks dashboard `tools/call`; restore N+2
  admits the same exact reviewed release again.
- Renew advances once while entry semantics remain equal.
- Wrong expected sequence changes no file.
- Mismatched, malformed or wrong-sized key material fails without disclosure;
  verify succeeds with the public key alone.
- The LKG is usable just below and exactly at 604800 seconds, unusable just over,
  with registry expiry independently enforced.
- Bad remote data never replaces a usable LKG; after hard age the registry-only
  release fails closed; a corrected higher sequence recovers.
- Lower sequence and equal conflicting replay remain rejected across process
  reconstruction.
- Signed data cannot activate an uncompiled family, route, tool, fallback,
  screenshot, preference write or service call.

## Workflow contract

- Manual `workflow_dispatch` on `main` only.
- One concurrency group and three distinct jobs:
  - inspection has network access and read-only repository permission, but no
    signing-seed or repository/PR write reference;
  - signing uses protected environment `upstream-attestation-signing`, has
    read-only repository permission, and receives the private seed only in the
    minimum signing step; and
  - publication is the only job with repository and pull-request write
    permission, and has no private-seed reference.
- Every checkout uses `persist-credentials: false`.
- Inputs: operation, exact version selector, expected sequence, 1-365 day expiry,
  and bounded non-secret reason.
- The signing dependency closure is committed with exact versions and hashes.
  Inspection obtains that reviewed wheel set; signing verifies it, then installs
  with `--no-index --require-hashes` and performs no online resolution while the
  seed is present.
- Before signing and immediately before publication, `origin/main` must still
  equal the exact captured workflow base SHA and the committed sequence must
  still equal the operator's expected sequence. A stale run aborts without
  rebase or regeneration.
- Every lifecycle record has an Ed25519 signature over its canonical payload and
  chains the prior registry and prior complete-evidence digests. Verification
  traverses every contiguous sequence from genesis through the current registry.
- Publication constructs and verifies the four data classes in a disposable
  worktree and publishes them through one coherent Git commit. The draft PR is
  data-only.
- The signing job has no tag, release, package, image, deployment, branch-push,
  or PR authority. The publication job has GitHub write authority, but contains
  no tag, release, image, package, or deployment command.

## Local output-set failure acceptance

- Direct CLI mutation stages and verifies the complete output set first.
- Each fixed target is replaced atomically per file. This is not a
  transactionally atomic multi-file filesystem operation.
- Post-write verification is mandatory. A replacement or verification failure
  restores every original byte and mode, removes originally absent outputs, and
  verifies the restored set where possible.
- Tests inject failure after each of the four replacements and distinguish
  `staging_failed`, `replacement_failed`, `post_write_verification_failed`,
  `rollback_failed`, and `restored_state_verification_failed`.

## Later production ceremony (not part of this PR)

1. Establish custodians, required environment reviewers, main restriction and
   offline recovery policy.
2. Generate one production Ed25519 key on the trusted Windows workstation using
   the non-echoing documented procedure.
3. Add the three protected environment secrets.
4. Dispatch bootstrap on `main` with expected sequence 0.
5. Review and merge the data-only PR separately.
6. In a separately authorized change window, install only the public key and
   enable the registry, then restart Engineering once.
7. Verify `remote_fresh`, signature valid, sequence 1, exact release admission,
   registry-only dashboard reads and zero fallback/write reachability.
8. Allow the natural LKG path or use the disposable harness; do not inject a
   production outage.
9. Exercise a separately reviewed higher-sequence revoke and restore only if the
   production acceptance plan explicitly authorizes the temporary admission
   outage.
10. Restart Engineering and confirm the same sequence/admission reloads from
    persistent `/data`, then refreshes fresh.

Stop on any secret disclosure, non-data diff, sequence skip, signature/key-ID
mismatch, LKG replacement by invalid data, write reachability, fallback, public
schema change, governance migration or stable-v1 diff.

The production key ceremony remains blocked until this remediation receives an
independent rereview; passing CI alone does not authorize production use.
