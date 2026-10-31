# Engineering 2.4.1-beta.52 release notes

Engineering 2.4.1-beta.52 builds a governed edit of one existing UI-managed
scene, **inactive** (HAMCP-071). It builds on 2.4.1-beta.51 (#331). It
activates nothing new. It changes no signed registry, signed profile,
existing fingerprint, existing input schema or existing plan family.

**Version sequencing.** 2.4.1-beta.51 (HAMCP-252, #331) has merged, and
this branch merged `main` after it. The release transition is therefore
beta.51 → beta.52, the next beta that `scripts/release_transition.py`
accepts.

Acceptance: [`V2_4_1_BETA52_ACCEPTANCE.md`](V2_4_1_BETA52_ACCEPTANCE.md).
Design and decisions:
[`HAMCP_071_SCENE_CONFIGURATION_DESIGN.md`](HAMCP_071_SCENE_CONFIGURATION_DESIGN.md).

## Governed scene configuration edit (HAMCP-071)

New tool: `create_scene_configuration_plan`. It proposes a change to the
`name`, `icon` or member states (`entities`) of one existing UI-managed
scene, by `config_id`. The plan is approved externally and applied with
`apply_change_plan`.

- **Saving never activates the scene.** No device changes state when the
  plan is applied. The tool never sends `scene.turn_on`, `scene.apply`,
  `scene.create`, `scene.reload`, `scene.delete` or the scene-config
  DELETE.
- **Route.** Engineering-native, direct to Core. It is a new resource type
  in the governed F3 configuration-plan family. The one write is
  `POST /api/config/scene/config/{config_id}`, carrying the full proposed
  configuration without `id`. Unrequested keys, `metadata` included, are
  sent exactly as stored. The readback is the stored configuration, the
  registry entry and the loaded scene. It does not go through ha-mcp and
  has no fallback. The generic `create_configuration_plan` route and the
  delegated `ha_config_set_scene` were rejected, because each would change
  a signed or upstream contract (design section 0).
- **Edit only.** Creating a scene is Part 3, a follow-up. Undoing a create
  needs a governed scene delete, and that is materially more than the
  automation create pattern (design section 0).
- **Inactive.** It needs the new runtime-only Core profile
  `core.scene_configuration_operation`, which signed sequence 14 does not
  name and the sequence-15 batch does not include. It is planned for
  sequence 16.
  - The tool and `create_configuration_plan` refuse
    `pending_signed_registry_admission` before any Home Assistant request
    or persistence.
  - The send boundary refuses a complete, valid command before it is sent:
    `pending_signed_registry_admission` while the profile is not admitted,
    and `active_f3_execution_required` outside the F3 dispatch window.
  - A legacy apply without the F3 runtime refuses
    `core_authority_unavailable`.
  - Health under sequence 14 lists 26 compatible and 5 withheld profiles,
    including this one.
- **Editable scenes.** An editable scene has a registry entry with domain
  `scene`, platform `homeassistant` and `unique_id` equal to the config id,
  and a stored configuration that GET returns. Integration scenes, YAML
  scenes outside `scenes.yaml` and scenes without an id are refused.
- **Planning.** Planning refuses these cases before any dispatch, with no
  plan:
  - unknown, mismatched, ambiguous, disabled or unreviewable targets;
  - a loaded scene that does not match its stored configuration;
  - invalid values and withheld text;
  - removing a member that is not in the scene, or removing the last
    member;
  - unknown added members;
  - members in the `scene` domain;
  - stored values that the closed POST could not send or restore.

  A request that changes nothing returns a no-change result and creates no
  plan.
- **Disclosure.** The plan shows the name and icon before and after, each
  changed member's snapshot before and after, and the consumers: the
  automations and scripts that reference the scene, the switch members and
  the `input_boolean`/`input_select` members.
  Consumers come only from the cached dependency index, which planning never
  refreshes. When the index is unavailable or not current, the gap is
  disclosed instead.
  Fixed texts state these facts:
  - saving does not activate the scene;
  - Home Assistant reloads every scene after the save, which removes
    `scene.create` runtime scenes;
  - stored entries without an id are given ids;
  - members are saved in `entity_id` order, which is not an activation
    order;
  - metadata is never changed;
  - Core has no compare-and-swap and its save is an upsert. Between the
    final check and the save, a concurrent edit can be overwritten without
    being detected, and a concurrent deletion can make the save recreate the
    scene. Verification proves only this plan's stored result. Josh accepted
    this race with this disclosure;
  - verification needs post-save reload evidence, and recovery is
    read-only.
- **Policy.** If any member before or after the edit is a protected object
  under the OD-4 `object_protection` rules, the plan is `elevated_admin`,
  risk HIGH,
  physical consequence `indirect`. The rules cover:
  - a sensitive domain;
  - a garage cover;
  - a water control;
  - a `garage`, `gate` or `door` cover;
  - a cover without a device class;
  - a siren;
  - a safety binary sensor.

  Any `switch` member before or after the edit also makes the plan
  `elevated_admin`, because a switch's purpose cannot be resolved from its
  entity id. The switch entity ids are disclosed.
  Any `input_boolean` or `input_select` member before or after the edit
  also makes the plan `elevated_admin` (Josh's decision D4): mode/helper
  members can change how automations behave, for example a guest mode that
  disables camera alerts. Their entity ids are disclosed. `input_number`
  and `input_text` members do not elevate.
  A member whose device class cannot be read also counts as protected.
  Otherwise the plan is `standard_admin`, risk LOW, physical consequence
  none. A multi-operation or incomplete scene plan is `PROHIBITED`.
- **Apply.** The scene is re-read immediately before the write, and drift
  is refused as `stale_target_state`. A scene missing at that check is
  refused; Engineering never asks Core to create one, although Core's upsert
  can recreate a scene deleted after the check. The lowest write layer sends
  only under an F3 write permit bound to the plan, the target, the command
  and the dispatch intent; a planning lease is not a permit.
  The write is dispatched at most once, with durable intent, no redispatch
  and truthful attribution. A 5xx or lost response is decided by the
  readback.
- **Verification.** The stored configuration must be exact, the registry
  identity unchanged and the loaded scene consistent with the stored
  configuration, with genuine evidence that Home Assistant reloaded the
  scene after the save, observed within the bounded window (10 × 0.5 s).
  Without that evidence the result is changed but unverified: the task stays
  observing and then requires manual review, never verified and never
  redispatched. Comparisons are type-exact.
- **Rollback.** `rollback_change` creates a separate governed plan that
  restores the exact prior configuration. It needs its own approval, and it
  does not activate the scene either.
- **Two defects fixed before release.** Both were found by the new tests.
  Elevated plans no longer crash on invalid policy reason codes. The
  no-change, preflight and rollback-drift comparisons are now type-exact
  (design section 8).
- **Report-only.** A not-found read surfaces as a retryable generic
  `upstream_error`. This is not fixed here (design section 1.2).

No scene evidence lane is defined yet. The seq-16 lane, its fingerprint pin
and its `ci.yml` wiring are November work (design section 7).

## Counts

- Static tools: 78 → 79.
- Advertised catalog: 107, or 108 with the optional YAML reader admitted.
- Runtime Core profiles: 30 → 31.

## Rollback

Reinstall 2.4.1-beta.51. Under sequence 14, beta.52 writes no new record
shape, because the new family refuses before persistence.
