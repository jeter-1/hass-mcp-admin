# Engineering 2.4.1-beta.53 acceptance

This contract covers HAMCP-071, a governed edit of one existing UI-managed
scene, built **inactive**.

It adds one tool, `create_scene_configuration_plan`, and one runtime-only
Core capability profile, `core.scene_configuration_operation`. Neither the
signed registry (sequence 14) nor the sequence-15 batch references that
profile. The release activates nothing new; the tool switches on with the
sequence-16 signing. It follows 2.4.1-beta.52 (HAMCP-097).

See also:
- Release notes:
  [`V2_4_1_BETA53_RELEASE_NOTES.md`](V2_4_1_BETA53_RELEASE_NOTES.md).
- Design and decisions:
  [`HAMCP_071_SCENE_CONFIGURATION_DESIGN.md`](HAMCP_071_SCENE_CONFIGURATION_DESIGN.md).

**Release target.** `2.4.1-beta.53` is materialized directly in the three
version authorities on the HAMCP-071 branch:
- `hass_mcp_engineering_beta/config.yaml`;
- `ha_mcp_engineering/version.py` `SERVER_VERSION`;
- `scripts/validate_addon_metadata.py` `BETA_VERSION`.

`.release/` holds no staged declaration. 2.4.1-beta.52 (HAMCP-097, #334)
has merged, and this branch merged `main` after it. The transition that
`scripts/release_transition.py` checks is therefore beta.52 → beta.53.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- the HAMCP-071 decisions in design section 0: edit only, route (c),
  built inactive, object-protection classification;
- `2.4.1-beta.53` in all three version authorities, with exact acceptance
  and release-note resolution from `scripts/codex-context.py`;
- the PR head SHA;
- the passing PR `validate` run for that exact head;
- the Evidence tier log named in the PR;
- the runtime-only profile fingerprint, recomputed at the review head:

  | Profile | sha256 |
  |---|---|
  | `core.scene_configuration_operation` | `73bac9067f62b62b039ce03f7754d1312f9c0595d0fb16af49851a49c90ba6bb` |

The review needs a security section covering the following items:
- **New write path, unreachable at release.** The path is
  `POST /api/config/scene/config/{config_id}`, carrying the full proposed
  configuration without `id`. It is reachable only through an approved plan
  dispatched by the F3 runtime, and only for a profile admitted by a signed
  registry. Every entry point refuses first, before any read, validation
  read or storage:
  - the tool;
  - the generic `create_configuration_plan`;
  - the send boundary, before sending: `pending_signed_registry_admission`
    while unadmitted, and `active_f3_execution_required` outside the F3
    dispatch window.

  F3 dispatch admission is checked again, and a legacy apply without F3
  refuses `core_authority_unavailable`.
- **Closed vocabulary.** The route allowlist admits only GET and POST for
  the scene configuration. DELETE is unreachable. The only websocket reads
  are `config/entity_registry/list` and `config/entity_registry/get`.
  Engineering never sends `scene.turn_on`, `scene.apply`, `scene.create`,
  `scene.reload` or `scene.delete`.
- **No activation.** Saving a scene changes no device state. Core's
  post-write hook reloads every scene. The plan discloses this, and the
  disclosure says that the reload removes `scene.create` runtime scenes.
- **Target.** Exactly one existing UI-managed scene: registry platform
  `homeassistant`, `unique_id` equal to the config id, and a GET hit. A
  scene missing at planning or at the final check immediately before the
  write is refused. Core's save is an upsert, so a deletion after that check
  can be recreated, and a concurrent edit can be overwritten undetected;
  this is disclosed and accepted (D3), and no readback claims to detect it.
- **Write permit.** The lowest write layer sends only under an F3 write
  permit bound to the plan, target, command and dispatch intent.
- **Approval.**
  - A protected member, or any `switch`, `input_boolean` or
    `input_select` member, before or after the edit is `elevated_admin` /
    HIGH / `indirect`. Otherwise the plan is
    `standard_admin` / LOW / none.
  - A multi-operation or incomplete scene plan is `PROHIBITED`.
  - The planning caller cannot approve.
- **Verification.**
  - The fingerprint is checked immediately before the write.
  - The readback must show the stored configuration exactly and the
    registry identity unchanged.
  - Option C (Josh, 2026-10-31): `succeeded_verified` means exactly that
    the stored configuration reads back type-exactly and the registry
    identity is unchanged. Engineering does not read or verify the scene
    Home Assistant has loaded. Every result, task and plan disclosure
    carries `verification_scope: "stored_configuration_and_registry_identity"`,
    `loaded_state: "not_verified"` and the statement "Engineering verifies
    the saved scene and its registry identity; it does not verify the scene
    Home Assistant has loaded."
  - A stored mismatch or registry drift is a verification failure; a
    removed or disabled entry or a failed read stays observing and then
    requires manual review. Recovery is read-only.
  - A configuration that is already stored is `succeeded_verified` without
    dispatch (`desired_state_already_reached`).
  - Comparisons are type-exact.
  - No surface claims loaded-state verification (a guard test enforces
    this).
  - An indeterminate send is decided by readback and never redispatched.
- **Rollback.** A separately approved restore plan of the exact prior
  configuration.
- **Fallback.** There is no delegated provider and no fallback.
- **Defects fixed in review scope.** Elevated-plan reason codes, and
  type-exact comparisons for already-reached, preflight and rollback drift
  (design section 8).
- **Report-only.** Finding 7, the generic `upstream_error` on a not-found
  read, is not fixed (design section 1.2).

Preserve the following:
- stable v1.1.2;
- the persisted formats of every existing family. The new resource type is
  written only after signed admission;
- every existing descriptor and input schema;
- static tools **79** (78 + 1). The catalog is **107 tools, 108 with the
  optional YAML reader admitted**;
- probe fingerprints;
- every signed Core capability profile and the owner-signed Core registry
  (sequence 14, unchanged);
- the signed ha-mcp registry and the ha-mcp contracts;
- the HOLD and the support window;
- add-on permissions and dependencies;
- `ci.yml` and every other workflow, all unchanged.

### 1.1 Evidence lane (not defined)

No scene evidence lane exists. `scripts/seq15_contract_acceptance.py`
records the profile as `SEQ16_DORMANT`, and the other acceptance scripts'
dormant sets exclude it. The seq-16 lane, its fingerprint pin and its
`ci.yml` wiring are November work (design section 7).

### 1.2 Offline proof

No test reaches a live system. Every fixture is synthetic:
- `scene.synthetic_lounge_dim` (`1700000000001`);
- `scene.synthetic_leaving` (`1700000000002`);
- the integration scene `scene.synthetic_presence_simulation`;
- the YAML scene `scene.synthetic_yaml_scene` (`1700000000009`).

The offline proof consists of:
- `tests/test_hamcp071_scene_configuration.py` (152 tests) with
  `tests/hamcp071_fixtures.py`. These use the real service, the F3 runtime
  and the scene gateway, and cover:
  - positive edits of the name, the icon (including removal) and members
    (add, replace and remove);
  - disclosure and redaction;
  - every planning refusal, and the no-op;
  - inactive refusal at the tool, at the generic builder and at the send
    boundary, with no Core call and no plan;
  - generic routes that cannot carry the type;
  - admitted resource writes;
  - stale state at apply;
  - 4xx, 5xx and lost responses, decided truthfully;
  - readback mismatches;
  - the option C verification scope: every edit kind verifies the stored
    scene only, with the scope fields; the Codex R3-H1 probe; stored
    mismatches and registry drift as verification failures; already-stored
    targets; and a guard that no surface claims loaded-state verification;
  - rollback and its refusals;
  - the contract boundaries.
- `tests/test_hamcp242_core_authority_itemization.py`. It pins sequence 14
  at 26 compatible and 5 withheld, including
  `core.scene_configuration_operation`. It also pins 30 compatible and 1
  withheld after sequence 15, and 31 compatible and 0 withheld after
  sequence 16.
- Pin updates across the suite: tool counts, F3 identities and registry
  size, capability metadata, and dormant lane sets.
- Mutations: `docs/evidence/hamcp071-mutations/` (`README.md`,
  `optc-results.json` and `optc-rerun-results.json`). The option C full
  run retires the 24 mutants whose code was removed, replays the other
  99, and adds 11 check removals (`O1`–`O11`: stored equality, registry
  identity, response identity and the scope fields): 107 of 110 are
  killed against a 257-test baseline. The two non-equivalent survivors
  (O4 and O11) each got an isolated test, and the rerun kills both
  against a 259-test baseline. So 109 of 110 are killed. The one
  survivor, A3 (the persistence-entry recheck), is equivalent, and its
  isolated control A3A4, which removes both persistence gates, is
  killed.

### Downgrade rule

Rollback is reinstalling **2.4.1-beta.52**. Under sequence 14, beta.53
writes no new record shape, because the new family refuses before
persistence. After a future signed sequence admits the profile, a downgrade
below beta.53 must be re-evaluated before it is supported; that is not
covered here. Never clear F3 journals, task files or locks.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable jobs
are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the following with the shipped verifiers:
- the merged source;
- the release tag;
- the immutable version and commit images;
- provenance and SBOM.

Never invent future image identities. Retain the beta.50 and beta.51
artifacts for recovery.

## 3. Installed Engineering

Installation needs separate authorization.

### 3.1 After deploy (read-only)

1. **Installed-image binding.** Bind the running add-on to the published
   image index digest, the release tag and the protected `main` merge
   commit.
2. **Catalog.** `tools/list` shows 107 tools, or 108 with the YAML reader
   admitted, including `create_scene_configuration_plan`.
3. **Inactive refusal.** Call `create_scene_configuration_plan` once with
   `{"config_id": "1700000000001", "name": "x"}`. It must refuse with
   `pending_signed_registry_admission`, `plan_created` false and
   `provider_dispatch_occurred` false, and `list_change_plans` must show no
   new plan. The refusal comes before any read, so no Home Assistant
   request is made and the config id need not exist.
4. **Health.** Core authority shows 26 compatible and 5 withheld. The
   withheld itemization includes `core.scene_configuration_operation`
   (`positive_authority_missing`, `affected_tools`
   `["create_scene_configuration_plan"]`). `list_capabilities` lists the
   tool with that profile and a `core_admission` other than `admitted`.
5. **Smoke and regression.** Run the standard read-only smoke and
   regression checks.

### 3.2 After the sequence-16 signing (defined here, not run)

These steps require all of the following:
- the signed sequence 16 that names the profile in section 1;
- an add-on restart;
- health showing 31 compatible and 0 withheld.

**Josh approves each live change at the time.** The target is one
pre-existing, harmless UI-managed scene that Josh selects at the time,
with no protected members and no `switch`, `input_boolean` or
`input_select` member (each makes the plan `elevated_admin`). Its config id and entity id are recorded in the run
evidence, not here. **The scene is never activated** at any step: no
`scene.turn_on`, no `scene.apply`, and no dashboard or voice activation.

1. Read the scene. Record its stored configuration and its state value
   (the last activation time). The save reloads the scene, so the full
   state object and `last_changed` are not required to stay unchanged.
2. Create a plan that changes only the name, or only the icon. Check the
   disclosure:
   - the before and after values;
   - the consumers;
   - the not-activated, reload, id-assignment, member-order,
     verification-scope and concurrency texts, and
     `loaded_state: "not_verified"` (the concurrency text discloses that a
     concurrent edit can be overwritten undetected and a concurrent
     deletion can be recreated);
   - `standard_admin`.

   Josh approves it. Apply it, then verify:
   - the stored configuration equals the proposed one exactly;
   - the members and `metadata` are unchanged;
   - the registry entry and `entity_id` are unchanged;
   - the task is `succeeded_verified` with
     `verification_scope: "stored_configuration_and_registry_identity"`
     and `loaded_state: "not_verified"`. Record separately, as Josh's own
     observation and not as Engineering verification, whether the loaded
     scene shows the new name or icon; if it does not, reload scenes in
     Home Assistant yourself. Do not reverify or resend;
   - bounded no-activation evidence: the audit and the task record no
     scene service (`scene.turn_on`, `scene.apply`, `scene.create`,
     `scene.reload`, `scene.delete`) and only the one config POST, and the
     scene's state value (its last activation time) is unchanged.
     `last_changed` may move, because the reload re-adds the scene.
3. `rollback_change` creates a separate restore plan. Josh approves it.
   Apply it, then verify that the stored configuration equals the step-1
   record exactly, the registry identity is unchanged and the same bounded
   no-activation evidence holds (no scene service; the state value is
   unchanged).
4. Repeat a request for the current values. It is refused as no-change,
   with no plan.
5. Run the smoke and regression checks.

Creating a scene is **not** accepted here. It is HAMCP-071 Part 3, a
follow-up with its own contract and acceptance.

## 4. Out of scope

This document does not authorize:
- registry signing (sequence 16);
- defining or wiring the seq-16 scene lane into `ci.yml`;
- the post-signing steps;
- creating a scene (Part 3) or deleting one;
- activating a scene;
- metadata edits;
- integration or YAML scenes;
- the delegated `ha_config_set_scene`;
- fixing finding 7;
- any downgrade.
