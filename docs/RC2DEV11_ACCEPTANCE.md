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
- Protected environment `upstream-attestation-signing` and one concurrency group.
- Inputs: operation, exact version selector, expected sequence, 1-365 day expiry,
  and bounded non-secret reason.
- Private seed exists only in the mutation signing step; verify has public key
  only; normal PR CI has neither environment nor package permissions.
- Mutation PR is draft and exactly four allowlisted data files.
- No image/package/tag/release/deployment command is present.

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
