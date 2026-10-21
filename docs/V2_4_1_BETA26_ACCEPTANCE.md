# Engineering 2.4.1-beta.26 acceptance

This contract covers HAMCP-217: `certifi` 2026.7.22 → 2026.10.19 for
GHSA-6q48-6c7w-9gw2 (PYSEC-2026-4320). It builds on 2.4.1-beta.25
(HAMCP-212).

The change was implemented and independently reviewed inside HAMCP-215
(PR #289, commit `c6a49a0f0a7a72237e78a11ec72c2d9bdc8bb930`). Per Josh's
2026-10-20 decision it ships on its own, ahead of HAMCP-215, carrying only
that commit's lock, inventory and test changes and no HAMCP-215 runtime code.

See also:
[`V2_4_1_BETA26_RELEASE_NOTES.md`](V2_4_1_BETA26_RELEASE_NOTES.md).

**Release target.**
- This PR is based on `main` at 2.4.1-beta.25 (base
  `e42fa15de1cd033af73258d52a058d67baa1b9d8`).
- It is staged as `2.4.1-beta.26` by one owner-authorized run of
  `scripts/promote_next_release.py --apply`, which consumes the declaration.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- Josh's 2026-10-20 decision: ship the certifi fix separately as the next
  beta so that other pull requests can merge;
- `2.4.1-beta.26` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head, including
  `validate_prerequisites` and its `pip-audit --strict` dependency audit;
- the Evidence tier log named in the PR.

The diff against the base must be limited to:
- the certifi entry in both hashed locks and in `build-inputs.json`;
- `tests/test_hamcp217_certifi_20261019_contract.py`;
- the three version authorities;
- release documentation.

Preserve the following:
- stable v1.1.2;
- historical persisted fixtures and durable formats;
- the beta.25 public catalog and every descriptor, probe fingerprints, the
  Core capability profiles, every owner-signed Core registry entry and the
  signed ha-mcp registry;
- the HOLD and the support window;
- every dependency except `certifi`, pinned runners and workflow permissions.

### Dependency proof

- `certifi==2026.10.19` with the single `py3-none-any` hash
  `42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b` appears
  in both `hass_mcp_engineering_beta/requirements.lock` and
  `tests/requirements.lock`.
- `build-inputs.json` records `certifi: 2026.10.19` and the new
  `runtime_lock_sha256`.
- `build_inputs.py source` passes with 39 distributions.
- No other pin or hash changes. `requirements.txt` is unchanged.
- `pip-audit --strict` on the runtime lock reports no known vulnerability. On
  beta.25's lock it reports exactly `certifi` 2026.7.22 PYSEC-2026-4320.
- The test lock's `urllib3` 2.7.0 findings (PYSEC-2026-4175/4176/4177) are
  pre-existing. They are outside this release under the 2026-10-19 owner
  decision and are not in the image.

### Offline proof

No test reaches a live system.

`tests/test_hamcp217_certifi_20261019_contract.py` (11 tests) passes and pins:
- the installed version and lock/inventory agreement;
- the removal of the `emSign ECC Root CA - G3` root;
- the retained unaffected emSign roots;
- the exact bundle trust, `CERT_REQUIRED` and hostname checking of each
  Engineering httpx path;
- refusal of a root outside the bundle, with zero requests and a positive
  control;
- the aiohttp boundary (aiohttp does not read certifi).

The module fails on 2026.7.22 and on 2026.10.18. The complete unittest suite
passes in the Evidence tier.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence. The dependency audit must pass on the exact head.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.26;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.25 artifacts and the
known-good earlier artifacts for recovery.

No stored format changes. Rollback is reinstalling the beta.25 artifact, which
restores certifi 2026.7.22 and its advisory.

## 3. Installed Engineering

All installed checks below are read-only unless a step names Josh's explicit
authorization.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.26 tag;
   - the protected `main` merge commit that contains this PR.

   A source version alone does not prove the installed image.
2. **Dependency inventory.** From the published SBOM or the image inventory,
   confirm `certifi` 2026.10.19.
3. **Catalog unchanged.** Compare the public descriptors with beta.25: the
   same tool count, input schemas and descriptions. Also compare:
   - Core signed selection;
   - storage and audit health;
   - F3 readiness;
   - zero fallback.
4. **Ordinary reads (read-only).** Run `get_server_health` and one delegated
   ha-mcp read. Each succeeds with no new error class.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
