# Owner runbook: Core registry one-tap cutover (HAMCP-278)

**Josh performs every step. Agents perform none of them.** No agent creates
the branch, the ruleset, the environment or the secret, dispatches the
workflow, deploys, or handles the private key. Background and decisions are in
[`CORE_RELEASE_REGISTRY.md`](CORE_RELEASE_REGISTRY.md#one-tap-signing-and-registry-branch-publication-hamcp-278).

Rules:

- If any check prints `FAIL`, a refusal or anything other than what is
  expected, STOP and keep everything as it is.
- The private key, the seed and the secret value never go into chat, a log, a
  file, a ticket or an agent session. Step 5 pipes the value straight into
  `gh secret set`. Run it in your own local terminal, never through an agent.
- Steps 2 and 5 run from a checkout of `main` at the merged HAMCP-278 commit.

## 0. STOP: no Home Assistant Core upgrade before the gate

> [!CAUTION]
> **Do not upgrade Home Assistant Core** until all four of these are done, in
> this order:
>
> 1. sequence 16 is live on `main` (step 1);
> 2. `registry` is created byte-identical from `main`, and the `cutover` hash
>    check passes (step 2);
> 3. this beta is deployed (step 6);
> 4. Core health shows the **same** `sequence`, now fetched from `registry`
>    by the deployed build (step 6).
>
> **Every later Core upgrade:** before upgrading, the exact target release,
> its sequence and its reviewed profiles must already be published on
> `registry`: its publication passed the `last-publish` check for
> `ha-core-<target version>-<sha256>.json` (step 10) and Core health reports
> that publication's `sequence`. A release present only on `main` does **not**
> count; neither does a candidate, a dry run or an UNCERTAIN publication.
>
> Why: when Engineering observes a new Core version it retires the prior
> authority, and the verified cache does not authorize a new version. Upgrading
> first leaves Core-backed capabilities unavailable until `registry` carries
> the release.

## 1. Prerequisites

- The HAMCP-278 pull request is merged to `main`.
- Sequence 16 is live on `main`: it is published at
  `upstream-trust/ha-core-release-registry.json` through its existing manual
  flow, and the deployed Engineering health reports it.
- Do not start before both are true. `registry` must start from the final
  journal on `main`.

## 2. Create `registry` byte-identical from `main`

`registry` holds only the `upstream-trust/` tree from `main` (the Core journal
and its evidence files) in one parentless commit.

```bash
git fetch origin main
git rev-parse origin/main                     # expect the merge commit that holds sequence 16
UT="$(git rev-parse origin/main:upstream-trust)"
ROOT="$(printf '040000 tree %s\tupstream-trust\n' "$UT" | git mktree)"
C="$(git commit-tree "$ROOT" -m "Create Core registry branch from main $(git rev-parse --short origin/main)")"
git push origin "$C:refs/heads/registry"      # creates the branch; refuses if it already exists
bash scripts/check_core_registry_branch.sh cutover
#   expect equal "main" and "registry" journal sha256 lines, then
#   "OK: byte-identical journals; registry holds only Core registry data."
```

The check script is read-only: it fetches into a temporary bare repository and
deletes it. From here on, the copy on `main` is frozen. Do not update or delete
it.

## 3. Ruleset for `registry`

Settings → Rules → Rulesets → New branch ruleset:

- Name: `registry`; Enforcement: Active; Target: branch `registry`.
- Enable **Restrict deletions** and **Block force pushes** only.
- Do not add "Require a pull request", "Restrict updates" or status checks:
  they would block the publish job's ordinary non-force push.

## 4. Signing environment

Settings → Environments → New environment `core-release-registry-signing`:

- **Required reviewers:** Josh (`jeter-1`) only.
- **Deployment branches and tags:** Selected branches, `main` only.
- Leave "Prevent self-review" off, because you dispatch and approve.
- Turn off "Allow administrators to bypass configured protection rules".
- Add no other secret or variable.

## 5. Store the key as the environment secret

`scripts/core_registry_seed_secret.py` reads your Core signing PEM and writes
base64 of the raw 32-byte seed to standard output, which is the form the signer
reads. It refuses to write to a terminal, refuses any key other than the
committed owner Core key (`7793e80f…3dac`), and writes no file. An encrypted PEM
asks for its passphrase only at the hidden prompt `PEM passphrase (hidden): `.

```bash
set +x
PY=<locked-env>/bin/python     # the repository's locked Python 3.12 environment (tests/requirements.lock)
KEY_PEM=<path to your Core signing PEM>
"$PY" scripts/core_registry_seed_secret.py --pem "$KEY_PEM" \
  | gh secret set HA_CORE_RELEASE_REGISTRY_SIGNING_KEY \
      --env core-release-registry-signing --repo jeter-1/hass-mcp-admin
#   expect only gh's "✓ Set Actions secret ..." line. A "Seed conversion refused: ..."
#   line means nothing was written and gh received no value. Check the environment's
#   secret list: if an empty secret was created, delete it before retrying.
unset KEY_PEM
```

Never run the script without the pipe, redirect it to a file, or copy its
output.

## 6. Deploy and confirm the runtime reads `registry`

1. Publish and deploy the Engineering beta that contains HAMCP-278. This is a
   separately authorized release and deployment.
2. Refresh the Claude connector.
3. Read Core health. `release_registry` must show `registry_id_status:
   accepted`, `freshness_status: current`, a `refresh_status` of `accepted` or
   `idempotent`, and the same `sequence` as `registry` (sequence 16).

At cutover, `main` and `registry` hold the same bytes, so this check shows the
new build accepts `registry`. The first publication in step 10 is the definitive
check: only `registry` holds that sequence.

## 7. Dry run once

Use the first real candidate (see the manual flow in
[`CORE_RELEASE_REGISTRY.md`](CORE_RELEASE_REGISTRY.md#manual-flow-for-each-publication)):

```bash
gh workflow run core-release-registry-publish.yml --ref main --repo jeter-1/hass-mcp-admin \
  -f core_version=<version> -f candidate_ref=registry-candidates/<version> \
  -f candidate_sha256=<reviewed candidate SHA-256> -f evidence_sha256=<reviewed evidence SHA-256> \
  -f dry_run=true
```

Expect:

- the `verify`, `dry-run-sign` and `dry-run-publish` jobs succeed;
- no approval request;
- the `dry-run-publish` log ends with "Assembled <commit> on <tip>; nothing was pushed.";
- `sign`, `reverify` and `publish` are skipped.

Then confirm nothing was pushed:

```bash
bash scripts/check_core_registry_branch.sh cutover   # still OK, same journal sha256 as step 2
```

## 8. Approve a real run from the mobile app

1. Dispatch the same command with `-f dry_run=false`.
2. When GitHub Mobile shows the pending review for
   `core-release-registry-signing`, open the run and read the `verify` job's
   **Core registry approval summary**.
3. Compare its **Candidate SHA-256** and **Evidence SHA-256** with the values
   you recorded at review. Check the new sequence. Read the **WITHHELD** block
   if one is shown.
4. Approve only if everything matches. Otherwise reject; nothing is signed.

## 9. Disable

Do either of these at any time. They take effect on the next run.

- Stop runs: `gh workflow disable core-release-registry-publish.yml --repo jeter-1/hass-mcp-admin`
  (`gh workflow enable` reverses it).
- Remove the key: `gh secret delete HA_CORE_RELEASE_REGISTRY_SIGNING_KEY --env core-release-registry-signing --repo jeter-1/hass-mcp-admin`.
  Signing then refuses. Restoring it means repeating step 5.

Neither changes the published `registry` branch or the runtime's verified cache.

## 10. Verify each publication

```bash
bash scripts/check_core_registry_branch.sh last-publish
#   expect "OK: the tip changes only the journal and adds upstream-trust/evidence/ha-core-<version>-<sha256>.json."
```

Then read Core health. After the next six-hour refresh, or after an add-on
restart you authorize, `release_registry` must report the new `sequence` with
`refresh_status: accepted`.

The publish job's log and summary end with one outcome:

- **PUBLISHED**: done. If it carries a warning that the push client reported a
  failure, the remote read still showed the new commit; do not publish again.
- **UNCHANGED**: `registry` was not changed. Fix the cause and dispatch again.
- **UNCERTAIN**: the push was sent and its result is not confirmed. Run the
  `last-publish` check above and compare the tip with the attempted commit in
  the summary before any further publication. Never dispatch again before you
  know which commit `registry` holds.

Only after PUBLISHED and the new `sequence` in health may Core be upgraded to
that release (step 0).
