# Engineering 2.4.1-beta.58 acceptance

This contract covers HAMCP-278 (278a): the Core registry URL moves to the
`registry` branch, with owner-gated one-tap signing and a separate publish job.
It follows 2.4.1-beta.57 and includes it.

See also the release notes:
[`V2_4_1_BETA58_RELEASE_NOTES.md`](V2_4_1_BETA58_RELEASE_NOTES.md).

**Release target.** `2.4.1-beta.58`, following 2.4.1-beta.57 (#343,
HAMCP-276). This pull request first staged beta.56, restaged to beta.57
after #340 merged, and restaged to beta.58 after #343 took beta.57. The
release is materialized in all three version authorities with no staged
declaration left. Nothing is published or deployed.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. owner cutover and installed acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- Josh's 2026-11-02 decisions as recorded in
  [`CORE_RELEASE_REGISTRY.md`](CORE_RELEASE_REGISTRY.md#joshs-decisions-2026-11-02);
- the non-goals: no HAMCP-279 evidence automation, no new profile, no
  change to sequence 16 or its manual flow, no change to
  `scripts/prepare_core_release_registry.py`, no journal schema change;
- `2.4.1-beta.58` materialized in all three version authorities before
  Ready, with exact document resolution;
- the PR head SHA, its `validate` run and the Evidence tier log named in
  the PR.

The review needs a security section covering:
- **Runtime.** Only the branch segment of `CORE_REGISTRY_URL` changes. No
  option, environment variable or argument can override it, and fetching
  any other location is refused.
- **Signing gate.** Only `sign` names the protected environment. It has
  `contents: read`, verifies both reviewed digests before signing, signs
  with `--expected-candidate-sha256`, and checks the result against the
  committed public key with the runtime verifier. No job signs without
  Josh's environment approval.
- **Publication.** `publish` has `contents: write` and no environment or
  secret, runs no Python, re-checks the signer's and `reverify`'s manifest
  digests, requires `registry` to still hold `verify`'s predecessor, builds
  one single-parent commit adding the evidence file and modifying the journal
  only, and pushes it without force to `refs/heads/registry` only.
- **Dry run.** No environment, no reference to the real secret, nothing
  pushed.
- **Key handling.** No agent handled the real key. Tests and the dry run use
  ephemeral keys only. The seed converter writes only to a pipe.

Required offline evidence, `tests/test_hamcp278_core_registry_publication.py`:
- the exact URL constant with no override, and identical verification of the
  same bytes from either URL;
- the approval summary, including the withheld-profiles block and the exact
  approval line;
- an end-to-end publication with an ephemeral key against a local bare
  remote;
- one refusal test each for: digest mismatch, registry absent, wrong key,
  broken chain, replay, re-adding a revoked release, registry changed between
  `verify` and `publish`, an extra path in the signed set, and a push target
  other than `registry`;
- static workflow checks that fail on injected violations;
- mutation testing of the guards in a copied tree, every mutant killed;
- the unchanged HAMCP-202 supply-chain guard and Core registry suites;
- the complete repository suite in the Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes, including the
beta.57 → beta.58 release transition.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow.

## 4. Owner cutover and installed acceptance (separately authorized)

Josh performs every step of
[`CORE_REGISTRY_ONE_TAP_RUNBOOK.md`](CORE_REGISTRY_ONE_TAP_RUNBOOK.md); agents
perform none. In order:

1. Create `registry` from `main` and run
   `scripts/check_core_registry_branch.sh cutover` (OK).
2. Configure the ruleset, the environment and the secret.
3. Deploy beta.58, refresh the connector and read Core health:
   `release_registry` reports `registry_id_status: accepted`,
   `freshness_status: current` and the same `sequence` as `registry`.
4. One dry run: nothing pushed, `cutover` check still OK.
5. First real publication: `last-publish` check OK, and after the six-hour
   refresh or an authorized restart, health reports the new sequence. This is
   the first observation that only `registry` could supply.
6. Core-upgrade gate (runbook step 0; applies from item 1 onward). PASS only
   if Home Assistant Core was not upgraded before item 3 showed the same
   sequence fetched from `registry`,
   and, for every later Core upgrade, the exact target release, its sequence
   and its reviewed profiles were published on `registry` (`last-publish` OK
   for that release, health reporting that sequence) before the upgrade.
   **FAIL if the target release exists only on `main`**, or only as a
   candidate, a dry run or an UNCERTAIN publication.

If a step fails, keep the run, its summary and the branch state, and record
them in HAMCP-278. Whether to roll back is Josh's decision.
