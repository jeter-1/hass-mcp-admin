# Upstream compatibility operator guide

This is the production ceremony for the data-only signed registry. It applies
only to stable `homeassistant-ai/ha-mcp` releases that can be proven compatible
with the already compiled `ha_mcp_dashboard_read_v2` family. Registry data
cannot add a family, route, tool, argument or fallback.

## Production prerequisites and custody

Do not begin the ceremony until all of the following exist:

1. Obtain an independent rereview of the RC2dev11 three-job workflow,
   lifecycle-evidence chain, freshness gates, and restoration behavior. Passing
   CI does not by itself authorize a production key ceremony.
2. Create the GitHub environment `upstream-attestation-signing` manually.
3. Restrict deployment branches to `main` and configure required reviewers who
   are independent of the workflow initiator where repository policy permits.
4. Assign a primary and recovery custodian. Record where the offline encrypted
   recovery copy is held, who may retrieve it, and how access is audited.
5. On a trusted, fully patched Windows administration workstation, outside a
   repository directory, create a new empty directory on encrypted removable or
   otherwise offline protected storage. Run this command from a PowerShell
   session whose transcript/history capture is disabled. It writes the seed and
   public key to ACL-restricted files and does not print either value:

   ```powershell
   $Out = Read-Host "Absolute empty offline key directory"
   New-Item -ItemType Directory -Path $Out -ErrorAction Stop | Out-Null
   icacls.exe $Out /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F" | Out-Null
   @'
   import base64, pathlib, sys
   from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
   target = pathlib.Path(sys.argv[1]).resolve()
   if any(target.iterdir()): raise SystemExit("target must be empty")
   key = Ed25519PrivateKey.generate()
   (target / "registry-signing-seed.b64").write_text(base64.b64encode(key.private_bytes_raw()).decode())
   (target / "registry-public-key.b64").write_text(base64.b64encode(key.public_key().public_bytes_raw()).decode())
   '@ | py - $Out
   ```

   Do not run this instruction during ordinary release work. It creates real key
   material. Verify the files exist without displaying their contents, make the
   separately controlled encrypted offline recovery copy, and remove any
   unencrypted working copy after secret entry and recovery-copy verification.
6. Add exactly these environment secrets through the GitHub UI without pasting
   them into a shell command, issue or log:
   `UPSTREAM_TRUST_REGISTRY_SIGNING_KEY` (base64 raw 32-byte seed),
   `UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY` (base64 raw 32-byte public key), and
   `UPSTREAM_TRUST_REGISTRY_KEY_ID` (a bounded operational identifier).
7. Only after a bootstrap data PR is reviewed and merged, configure Engineering
   Beta with `upstream_trust_registry_public_key` and enable
   `upstream_trust_registry_enabled` in a separately approved deployment window.
   The private seed is never an add-on option.

No environment, secret, reviewer, branch protection or add-on setting is created
by the repository workflow.

## Workflow inputs and common review

From GitHub Actions on `main`, run **Prepare ha-mcp compatibility attestation**.
Inputs are:

- `operation`: `bootstrap`, `add`, `revoke`, `restore`, `renew`, or `verify`;
- `upstream_version`: exact stable `X.Y.Z` for bootstrap/add/revoke/restore,
  empty for renew/verify;
- `expected_current_sequence`: `0` for bootstrap and the exact committed
  sequence for every other mutation; empty for verify;
- `expiry_days`: 1 through 365, default 90; and
- `operator_reason`: optional, non-secret, no control characters, at most 256
  characters.

One concurrency group serializes all operations. The workflow has three jobs:

1. Inspection may use the network, but has read-only repository permission, no
   private-seed reference, no persisted checkout credential, and no push/PR
   authority. It produces an unsigned candidate and bounded inspection manifest.
2. Protected signing uses `upstream-attestation-signing`, has read-only
   repository permission, and exposes the private seed only in the minimum
   signing step. The exact cryptographic dependency closure is downloaded by
   inspection from the committed hash lock, verified, and installed by signing
   with `--no-index --require-hashes`; no public-index resolution occurs while
   the seed is present.
3. Publication is the only job with repository and PR write permission. It has
   no seed reference, re-verifies with the public key, constructs the complete
   set in a disposable clean worktree, creates one coherent commit, re-verifies
   it, pushes only that branch, and opens only a draft PR.

The signing job has no repository-write, tag, release, package, image, or
deployment authority. The publication job does have repository/PR write
authority, but contains no tag, release, image, package, or deployment command.
Normal PR CI cannot access the protected signing secrets.

Immediately before signing and again immediately before publication, the
workflow fetches `origin/main`. Its SHA must still equal the captured dispatch
base, and the verified committed registry sequence must equal
`expected_current_sequence`. The signed evidence and publication manifest bind
the exact base SHA. Any stale run aborts; do not rebase or regenerate it.

Review every PR for exactly four data paths: registry, detached signature, one
review-evidence document and the generated index. Confirm old/new sequence,
affected exact identity, revocation transition, validity window, key ID, family,
evidence digest and applicable source/image evidence. Reject application,
workflow, test, add-on, version, container, release or unrelated documentation
changes.

Review the complete lifecycle chain, not only the new record. Every sequence
record has an embedded Ed25519 signature over its canonical payload, binds the
prior registry and prior complete-evidence digest, and includes the old/current
registry snapshots needed to prove the exact transition. Sequence numbers must
be contiguous from genesis; missing, altered, reordered, duplicated, skipped,
or unrelated future evidence invalidates verification.

## Initial bootstrap

1. Confirm neither registry JSON nor detached signature is committed.
2. Select `bootstrap`, one fully reviewed stable version, expected sequence `0`,
   and the default 90-day validity unless the review records another bounded
   choice.
3. The workflow resolves the exact upstream tag and official immutable image,
   proves source/image ancestry and platform provenance, runs the image against
   disposable Home Assistant, extracts the actual tool contract and proves zero
   write reachability.
4. It creates sequence 1, signs canonical JSON, verifies it, and opens a draft
   four-file data PR.
5. Require CI and human review, then merge separately. Runtime health after a
   later approved configuration window should report `remote_fresh`, sequence 1,
   valid signature/cache and the exact data-only admission.

## Add an exact compatible release

1. Select `add`, the new exact stable version, and the current committed
   sequence.
2. The same source/image/runtime inspection runs. A duplicate entry ID or
   server/version/family tuple is rejected.
3. Confirm existing entries are unchanged, the sequence advanced once, the new
   entry is unrevoked, and evidence proves the compiled family.
4. Merge the reviewed data PR. No Engineering image is required. On the next
   bounded refresh, health should report the new sequence and `remote_fresh`;
   admission occurs only after all normalized fingerprints match.

## Cached operation during remote unavailability

1. Do not inject an outage in production. Observe only a naturally occurring
   failure if one happens.
2. Before the seven-day cache hard age and registry expiry, the verified LKG is
   `remote_cached`; last failure reports `upstream_registry_unavailable`, while a
   data-only release can remain admitted.
3. The invalid/unavailable remote response must not replace the cache. Preserve
   `/data` during investigation.
4. At exactly 604800 seconds of cache age the current documented rule remains
   usable; just over that boundary it is unusable. Registry expiry is enforced
   independently and may make it unusable earlier.

## Revoke and restore

1. For revocation select `revoke`, exact version and current sequence. Confirm
   the entry is currently unrevoked. Review the diff: only its `revoked` value,
   top-level sequence/times/signature, evidence and index may change.
2. After the higher-sequence PR is merged and refreshed, health reports the new
   sequence and `rejected_revoked_attestation`; dashboard tool dispatch for that
   release is blocked before upstream `tools/call`.
3. To recover the same reviewed release, select `restore` with the now-current
   sequence. The entry must be revoked. Review the inverse boolean-only entry
   change and merge the separately reviewed higher-sequence PR.
4. Health then reports the restored higher sequence and admission succeeds only
   when the observed contracts still match. Never revert the revocation commit
   or publish a lower sequence.

## Renew, verify, restart and recovery

- `renew` preserves the semantic entry list exactly, advances the sequence once,
  and updates only generated/expiry times, signature, evidence and index. Run it
  before expiry with the exact current sequence.
- `verify` is read-only, uses only the public key, and checks canonical JSON,
  schema, signature/key ID, sequence, entries/evidence and generated index. It
  creates no branch or PR.
- After enabling the registry, an Engineering restart reloads the cache from
  persistent `/data`. Restart only in an approved deployment window. Expected
  health is `cache_status=valid`, the same sequence, and `remote_cached` until a
  successful refresh becomes `remote_fresh`.
- A bad signature cannot replace a usable LKG. After LKG hard age, a registry-only
  release fails closed with `upstream_registry_invalid_signature` in health and
  a bounded unsupported-trust-profile dashboard error. Correct the document with
  a valid higher sequence; never reuse the bad/equal sequence.
- A lower sequence is rollback and an equal sequence with different content is
  conflicting replay. Recovery always uses current committed state plus one.

## Key compromise, rotation and abandonment

On suspected private-key compromise, stop registry workflow approvals, preserve
the LKG and evidence, revoke GitHub secret access, and move affected upstreams to
a built-in attested version. Do not attempt a same-key corrective signature.
Rotation requires a separately reviewed Engineering release that trusts the new
public key, followed by a higher-sequence signature with the new key and removal
of the old private seed.

To abandon the registry, first run on a built-in attested ha-mcp version, disable
the registry in an approved add-on configuration change, verify built-in
admission, and retain the registry/cache/evidence for investigation and rollback
history. Do not erase `/data` as an expedient recovery.

## Accepted limitations

- Production registry URLs are fixed and redirects are disabled.
- There is no production outage-injection switch. Bad signatures, seven-day
  cache expiry, rollback/replay and restart reconstruction are tested with the
  disposable harness.
- The monotonic anchor lives in `/data`; erasing it removes the local rollback
  anchor. Backup/restore policy for `/data` must be governed separately.
- Runtime trusts one configured public key, so rotation is a planned release
  operation, not registry data.
- `ha_set_entity`, `ha_set_device`, service execution, dashboard writes,
  screenshots, preference writes and generic delegation remain unreachable.

The 7.14.1 registry-write contracts remain non-runtime design evidence only. A
future governed registry-administration milestone must separately define plan,
external approval, stale-state, apply, verification, rollback and audit behavior.
