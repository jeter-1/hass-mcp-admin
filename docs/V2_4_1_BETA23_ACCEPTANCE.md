# Engineering 2.4.1-beta.23 acceptance

This contract covers HAMCP-209. The dependency index is built from Home
Assistant's loaded automation configuration with a bounded number of requests,
and falls back to stored reads only where it reports doing so. Coverage and
partial reporting stay truthful. Release notes:
[`V2_4_1_BETA23_RELEASE_NOTES.md`](V2_4_1_BETA23_RELEASE_NOTES.md).
Design and Part 1 verification:
[`HAMCP_209_FAST_DEPENDENCY_INDEX_DESIGN.md`](HAMCP_209_FAST_DEPENDENCY_INDEX_DESIGN.md).

**Release target.** This PR follows 2.4.1-beta.22 (HAMCP-211, PR #284).
- It was implemented on `2.4.1-beta.19` (`a1236c4`).
- It merged `main` with merge commits only: `538a7c7` (HAMCP-191, beta.20) at
  `78a2595`, then `c618884` (HAMCP-205 beta.21 and HAMCP-211 beta.22) at
  `969086e`. Neither merge had a textual conflict.
- beta.21 was superseded before publication (see the beta.22 notes), so
  beta.22 is the predecessor for catalog comparison and rollback.
- It was staged as `2.4.1-beta.23` by one owner-authorized run of
  `scripts/promote_next_release.py --apply`, which consumed the declaration.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- `2.4.1-beta.23` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR.

### Offline proof (`tests/test_hamcp209_fast_dependency_index.py`, 37 tests)

The tests use the synthetic Home Assistant model in `tests/hamcp209_fixtures.py`.
It models Core's stored route as one lock plus a whole-file parse, an
in-memory loaded form, blueprint substitution and the post-write reload. The
model is driven through the real provider, index, coverage and governed write
boundaries. Client tests use a loopback WebSocket peer.

- **Extraction parity** (4): the loaded source and the stored-only path give an
  identical index view and snapshot fingerprint. This holds for a
  representative set (plain, templated, dynamic, `label_entities()`,
  blueprint-based, unavailable and id-less automations) and for 113
  automations. The loaded form of a blueprint instance is never used.
- **Bounded requests** (3): 113 automations give one connection, 226 commands,
  no `automation_config` REST read and at most 5 provider requests.
- **Fallback reporting** (4): every reason is counted and named in the
  coverage policy. Malformed results are reported. A whole-source failure or
  an incomplete batch is reported with a warning, never silently.
- **Coverage** (3): partial and failure counts match the stored path. An
  automation unreadable from both routes is partial.
- **Pending-write journal** (11): retention until reload; written-but-not-loaded
  is partial (including with an empty inventory); a 404 clears an entry;
  unreadable entries are retained; a rewrite during the build is retained;
  overflow is handled.
- **Governed write hooks** (3), **batch allowlist** (1) and **batch client**
  (6).
- **Resolved-secret disclosure** (2): secret-shaped values planted in a
  loaded-only package automation and a stored automation never appear in
  the following outputs:
  - dependency, impact, integrity and reliability;
  - incident;
  - handoff (structured and markdown);
  - helper-state and entity-metadata disclosures.

  Effect-projection values that the sanitizer recognizes are shown as
  `redacted_sha256:` digests.

Cross-tree parity (`docs/evidence/hamcp209/parity.py`, `parity.json`): the
beta.19, beta.20 and beta.22 providers and this tree give identical views and
fingerprints on both fixture sets.

### Mutation proof

Each mutation is applied to a disposable copy of the source
(`docs/evidence/hamcp209-mutations/run_mutations.py`, `results.json`), and
each one must fail:
1. The per-automation REST loop restored (the request-count test fails).
2. A missing loaded configuration read as an empty, complete configuration.
3. A written automation absent from the inventory never checked.
4. Written-but-not-loaded reported unavailable instead of partial.
5. A fallback reason not recorded.
6. The pending journal ignored.
7. The `search/related` blueprint report ignored.
8. The loaded identity check removed.
9. The batch allowlist opened.
10. The `AutomationGateway` write not journaled.
11. The resource automation write not journaled.

### Measurement

`docs/evidence/hamcp209/measure.py` runs on the synthetic 113-automation model.

| | Base `a1236c4` | Head |
| --- | --- | --- |
| Build wall time | 34.6 s | 0.43 s |
| Provider requests | 116 | 4 |
| Per-automation REST reads | 113 | 0 |
| Write during the build | 2.7 s | 0.33 s |

The idle write takes 0.30 s in both trees.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- v2.4.1-beta.23;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.22 artifacts and the
known-good earlier artifacts for recovery.

No stored format changes. Rollback is reinstalling the beta.22 artifact.

## 3. Installed Engineering

All installed checks below are read-only unless a step names Josh's explicit
authorization.

1. **Installed-image binding.** Separately authorize installation of the
   published Engineering artifact. Bind the running add-on to:
   - the published image index digest;
   - the v2.4.1-beta.23 tag;
   - the protected `main` merge commit that contains this PR.

   A source version alone does not prove the installed image.
2. **Catalog unchanged.** Compare the public descriptors with beta.22: the
   same tool count, input schemas and descriptions. Also compare:
   - Core signed selection;
   - storage and audit health;
   - F3 readiness;
   - zero fallback.
3. **Index build profile (read-only).** After the startup prewarm finishes,
   call `get_server_health` and read
   `dependency_analysis.index.last_build_profile`. Require:
   - `automation_config_source` is `loaded_websocket`, or `mixed` when blueprint
     instances exist;
   - `request_count_by_operation` has no `automation_config` key, or only the
     number of stored fallbacks;
   - `loaded_automation_config_batch: 1` and `loaded_config_connection_count: 1`;
   - `loaded_config_command_count` is twice the number of id-bearing,
     available automations;
   - the sum of `stored_config_fallback_reasons` equals
     `automation_config_source_counts.stored_rest`, with each reason expected
     for the installation (for example `blueprint_instance` once per blueprint
     automation);
   - `pending_write_journal.overflow_at_build: false`.

   Record `build_wall_clock_ms` (expected a few seconds at most, against
   33.7–41.5 s on beta.16–19), `request_count`,
   `maximum_single_request_queue_wait_ms` and the index coverage. Coverage must
   not be worse than the last beta.22 build: the same or fewer automation
   failures. Package-defined automations that were unreadable before may now
   be covered.
4. **Write right after a restart (needs Josh's explicit authorization for the
   add-on restart and for one governed automation write).**
   1. With authorization, restart the Engineering add-on. Do not wait for the
      prewarm to finish.
   2. Within about 5 s of the MCP endpoint becoming available, apply one
      authorized governed automation write. Use a benign change Josh selects,
      through the normal plan, external approval and apply.
   3. **Pass:** the apply completes at normal speed. Normal means about the
      same as an idle write on the same installation, and not the 35–44 s seen
      on beta.16–19. Read-back verification succeeds. A following
      `get_server_health` shows `pending_write_journal.pending_at_build` at
      `0` once Home Assistant has reloaded the automation, and the index is
      `complete` (or as complete as before).
   4. **Fail:** the apply takes more than about twice the idle write time, or
      the index reports `automation_pending_reload_not_loaded` after Home
      Assistant has reloaded. Stop and report the profile and the receipts.
5. **Fallback visibility (read-only).** If `automation_config_source` is
   `stored_rest` with `loaded_source_unavailable`, the loaded source failed as
   a whole. Health still reports the coverage warning and the index stays
   truthful. Record the warning and report it. Do not retry by restarting.

The following are proven offline only (§1):
- per-reason fallback;
- journal overflow;
- withdrawn dispatch authority;
- protocol violations.

No push, CI trigger, signing, publication, installation or live action is
authorized by source completion.
