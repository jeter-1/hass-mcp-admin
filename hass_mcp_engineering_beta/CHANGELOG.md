# Changelog

## 2.2.0-beta.11 - Bounded restart recovery and exact ha-mcp 8.0.0 compatibility

- Bound restart reconciliation by the original persisted post-dispatch
  deadline. Expired or structurally ineligible restart records terminalize
  without Home Assistant, Supervisor, or lifecycle-provider probes and can
  never redispatch.
- Persist restart reconciliation attempt timing and capped backoff
  (`1m`, `2m`, `5m`, then `15m`) across Engineering restarts, enforce
  task-level single flight and bounded batches, and expose bounded active,
  pending, avoided-probe, expiry, and failure evidence in health.
- Preserve exact reviewed `ha-mcp` 7.14.2 admission and add a separate exact
  reviewed 8.0.0 release entry, contract snapshot, immutable artifact
  evidence, and complete 78-tool policy.
- Under 8.0.0, admit exactly 24 delegated reads and hold `ha_search` and
  `ha_get_operation_status` for a later production canary. Unknown later 8.x
  releases remain untrusted and expose no delegated reads.
- Preserve the constrained dashboard list/get wrapper, bounded backup and
  lifecycle providers, the current MCP protocol policy, 48 Engineering-local
  tools, task schema 1, approval authority 3, stable source version 1.1.2, and
  zero fallback.
- Retain the existing operational admission fingerprint model separately from
  the stricter full-contract evidence model. Static and exact-image evidence
  do not claim production architecture, runtime add-on digest, or live
  dashboard, backup, lifecycle, outage, reconnection, or held-tool behavior.
- Defer delta-aware safety-reducing policy beyond Beta 11; this release adds no
  policy classification, provider, Engineering-local tool, write, protocol,
  or fallback authority.

## 2.2.0-beta.10 - Legacy expired-automation compatibility

- Recognize the exact contract-v1 prohibited/expired `update_automation`
  records written by Beta 6's legacy `create_plan` path, separately from the
  existing contract-v2 superseded configuration-plan profile.
- Generate both legacy fixtures through the exact shipped Beta 6 writer and
  expiration lifecycle, with neutral pre-hash inputs and committed source,
  generator, fixture, and event-sequence provenance.
- Admit only the two source-established complete event sequences. The expiry
  event is not added to the generic no-execution allowlist, and every
  additional, missing, reordered, successful, or differently coded event
  remains fail-closed.
- Require exact plan, target, lifecycle, authority-v3, prohibited-policy, hash,
  empty-operation, and no-authority/no-execution evidence. Task-store failures
  continue to propagate.
- Preserve byte-identical records across detail, listing, health, startup,
  Ingress, and handoff reads; no record migration, challenge, task, provider
  call, fallback, or execution path is added.
- Regenerate both Beta 6 compatibility fixture families in CI from the exact
  historical source and compare every fixture and provenance file byte for
  byte.
- Retain Beta 7 provider-response truthfulness and Beta 9 partial-list and
  reconciled-health behavior, authority version 3, task schema 1, the
  25/23/48 plus configured 26/74 tool contract, stable v1.1.2, and zero
  fallback.
- Defer delta-aware safety-reducing policy to Beta 11; Beta 10 adds no policy,
  provider, resource, tool, execution, recovery, or fallback authority.

## 2.2.0-beta.9 - Real Beta 6 prohibited-plan compatibility

- Correct the historical compatibility gate to recognize exactly the
  contract-v2 prohibited/superseded representation written by shipped Beta 6,
  rather than the contract-v1 default produced by an incomplete manual test
  fixture.
- Generate neutral compatibility fixtures through the exact Beta 6 writer and
  supersession lifecycle, with committed source, generator, and fixture
  provenance; contract-v2 prepared operations remain non-execution evidence.
- Retain strict immutable plan and policy validation and reject any authority,
  task, dispatch, response, apply, verification, rollback, operation-state, or
  event contradiction.
- Return bounded partial plan listings when an individual loaded plan cannot be
  projected, while preserving top-level failure for systemic storage errors.
- Reconcile every stored plan into a health policy-class bucket, including the
  fail-closed `projection_failed` bucket and an explicit bounded warning.
- Preserve byte-identical records on detail, listing, health, startup, Ingress,
  and handoff reads; add no migration, challenge, task, provider call, or
  fallback.
- Retain Beta 7 provider-response truthfulness, current prohibited semantics,
  authority version 3, task schema 1, the 25/23/48 plus configured 26/74 tool
  contract, stable v1.1.2, and zero fallback.
- Defer delta-aware safety-reducing policy to Beta 10; Beta 9 adds no policy,
  provider, resource, tool, execution, recovery, or fallback authority.

## 2.2.0-beta.8 - Persisted prohibited-plan compatibility

- Historical correction: Beta 8's manually reconstructed fixture omitted
  `contract_version` and defaulted to contract v1, so its intended historical
  branch did not recognize the real contract-v2 records written by Beta 6.

- Recognize the exact validated Beta 6 authority-v3 prohibited-plan shape in
  which same-target supersession changed only legacy lifecycle fields to
  `superseded` and `invalidated`.
- Project those records as terminal and non-actionable across detail, listing,
  health, Ingress, startup rehydration, and handoff paths without rewriting
  their persisted fields, events, hashes, or timestamps.
- Count validated historical records as prohibited while excluding them from
  awaiting, required, pending-challenge, and authorization-required work.
- Fail closed when a purported historical record has nonempty
  acknowledgements, authority, a task, provider or apply evidence, successful
  work, or an invalid policy snapshot.
- Retain Beta 7 transport-response truthfulness, current prohibited semantics,
  authority version 3, task schema 1, the 25/23/48 plus configured 26/74 tool
  contract, stable v1.1.2, and zero fallback.
- Defer delta-aware safety-reducing policy to Beta 9; Beta 8 adds no policy,
  provider, resource, tool, execution, recovery, or fallback authority.

## 2.2.0-beta.7 - Acceptance evidence and projection corrections

- Record configuration-provider HTTP responses and WebSocket frames truthfully
  before readback, including empty successes and received error responses,
  while retaining false for a timeout or connection failure with no known
  response.
- Keep provider response receipt, write outcome, readback, and semantic
  verification as separate durable facts; a later mismatch cannot erase a
  received response and readback cannot manufacture one.
- Project validated F2 prohibited plans as terminal and non-actionable in plan
  detail, inventory, handoff evidence, Ingress, and health instead of claiming
  that they await or require an approval that cannot exist.
- Preserve authority-v3 persisted enum fields and historical schema-v1 task
  evidence without migration or startup backfill.
- Preserve F2 policy mapping, same-administrator approval, one-task ownership,
  task schema 1, 48 local tools, 26 configured delegated reads, stable v1.1.2,
  and zero fallback.

## 2.2.0-beta.6 - Policy, risk, and elevated approval semantics

- Add deterministic, server-derived `risk_delta`, independent
  `physical_consequence`, and `standard_admin`, `elevated_admin`, or
  `prohibited` policy decisions to every new immutable governed plan.
- Bind the complete F2 policy snapshot and policy-decision hash into the plan
  hash; validation, approval, apply, and startup recovery fail closed on
  missing or mismatched authority.
- Advance new approvals to authority version 3. Standard plans require one
  Home Assistant administrator approval. Elevated plans require a separate
  plan approval followed by an elevated-risk acknowledgement from the same
  authenticated administrator; this is not two-person control.
- Recheck policy and the complete approval bundle immediately before dispatch,
  create or reserve the durable F1 task before consumption, and retain bounded
  approval evidence without changing task schema version 1.
- Keep historical authority-v2 records readable but non-actionable, prohibit
  unknown, unsupported, destructive, critical, safety-critical, or evasive
  plans, and preserve one-task ownership, no blind redispatch, provider-response
  truthfulness, exact provider routing, and zero fallback.
- Prohibit the reviewed `lock.unlock` and
  `alarm_control_panel.alarm_disarm` service names independently of entity,
  device, area, data-based, broad, templated, unresolved, mixed, or omitted
  target representation while retaining existing mappings for other high-risk
  services.
- Add disposable pinned-Core contracts for standard, elevated, and prohibited
  configuration policies. Planning never triggers the configured future
  physical action, and duplicate apply performs no second write.
- Verify accepted automation readback through a narrow schema-aware
  `service`/`action` alias normalization while retaining raw fingerprints,
  meaningful-field mismatches, unchanged plan/policy hashes, and truthful
  provider-response evidence.
- Require every verification action step to match exactly one reviewed family;
  identical unknown mappings, ambiguous families, malformed control flow, and
  unreviewed extra fields fail closed with bounded evidence.
- Preserve 48 local Engineering tools, the configured 26 exact-admitted
  delegated reads, stable v1.1.2, and the existing upstream fingerprints.

## 2.2.0-beta.5 - Update and recovery preflight foundation

- Add immutable, deterministic models for explicit update targets, installed
  and candidate versions, explicit caller-supplied version direction,
  compatibility, current repairs and errors, backup, storage, power, recovery,
  disruption, and post-update verification evidence.
- Preserve separate ordered blockers, warnings, and unknowns with exact
  `ready_for_governed_planning`, `blocked`, `manual_review_required`, and
  `unsupported` advisory verdicts.
- Fail closed when authoritative candidate or compatibility evidence, required
  backup evidence, storage, power, recovery, disruption, or verification facts
  are missing or conflict with the selected target policy.
- Permit readiness only for confirmed upgrades; require manual review for
  downgrades, unknown direction, direction contradictions, and HIGH unresolved
  repairs or errors; block same-version candidates and CRITICAL issues.
- Keep E1 pure and runtime-inert: no evidence collection, startup loading,
  network or Home Assistant access, tool, health, provider, plan, approval,
  task, update, backup, restart, restore, downgrade, safe-mode action, write, or
  fallback is added.
- Preserve task schema v1, 48 local Engineering tools, the configured 26
  exact-admitted delegated reads, stable v1.1.2, and zero fallback.

## 2.2.0-beta.4 - Knowledge provenance foundation

- Add strict, deterministic local knowledge-manifest parsing with closed fields,
  bounded values, exact classifications, duplicate rejection, and explicit
  malformed-manifest failures.
- Add provenance-only trust classes, independent Engineering, Home Assistant,
  and integration version scopes, explicit expiry handling, and
  `unknown` relevance that never implies compatibility or recommendation.
- Restrict content to canonical child paths and bounded UTF-8 text formats;
  reject traversal, absolute paths, escaping symlinks, oversized content, and
  SHA-256 mismatches.
- Preserve exact source, document, version, path, digest, and bounded citation
  provenance while treating all embedded text as instruction-inert data.
- Keep K1 runtime-inert: no startup loading, remote retrieval, MCP tools,
  recommendation or plan authority, Home Assistant access, provider or signed
  registry integration, write path, or fallback is added.
- Preserve task schema v1, 48 local Engineering tools, the configured 26
  exact-admitted delegated reads, stable v1.1.2, and zero fallback.

## 2.2.0-beta.3 - Signed compatibility registry foundation

- Add strict, closed signed-registry envelope, reviewed-release, and
  revocation models that preserve the compiled registry's exact contract
  evidence.
- Add deterministic canonical serialization and content digests, Ed25519
  verification through configured public trust anchors, and typed fail-closed
  validation results.
- Reject unknown keys, invalid signatures, payload mutation, rollback, replay
  conflicts, broken digest chains, expiration, future generation timestamps,
  duplicate identities, and entry/revocation contradictions.
- Keep the compiled reviewed-release registry authoritative. This release adds
  no runtime loading, retrieval, admission, tool, health, provider, execution,
  write, fallback, or production signing-key behavior.
- Preserve task schema v1, 48 local Engineering tools, the configured 26
  exact-admitted delegated reads, stable v1.1.2, and zero fallback.

## 2.2.0-beta.2 - F1 recovery evidence and counters

- Preserve a lost original provider response as
  `response_received=false` when startup/readback verification later proves
  successful completion.
- Emit `provider_response_recorded` only for an actual provider return; recovery
  and process-identity verification remain separate durable evidence.
- Include terminal-task duplicate applies exactly once in operation-specific
  apply and no-blind-redispatch counters without increasing provider dispatch.
- Preserve task schema v1, immutable plans, external approval, 48 Engineering
  tools, 26 reviewed delegated reads, and zero fallback.

## 2.2.0-beta.1 - Durable execution tasks

- Separate the immutable hash-bound change plan and existing external approval
  from one mutable, versioned execution task for each newly executed plan.
- Persist a materialized task together with ordered append-only lifecycle
  events in the isolated `execution-tasks-v1` namespace using atomic
  flush/fsync/replace writes and fail-closed consistency validation.
- Make `apply_change_plan` create or reuse one authoritative task before
  preflight, project existing dispatch and operation-specific verification
  evidence, and additively return the task identifier and state.
- Preserve the exact-once boundary across duplicate callers, client timeout,
  provider response loss, and Engineering process restart. Startup task
  rehydration performs no provider action; existing operational reconciliation
  remains readback-only.
- Add bounded `get_execution_task`, `list_execution_tasks`, and
  pre-dispatch-only `cancel_execution_task`. Cancellation after dispatch is
  refused and is never represented as rollback or compensation.
- Set the immutable maximum recovery deadline to 24 hours after first dispatch.
  Unresolved tasks then enter `manual_review_required` without redispatch.
- Keep historical taskless plans readable as legacy records without fabricating
  execution evidence or changing any plan hash.
- Increase the complete catalog to 48 Engineering tools plus 26 reviewed
  delegated reads, 74 total. Upstream admission, provider routes, public
  schemas outside the three additive tools, stable v1, and zero fallback remain
  unchanged.

## 2.1.1-beta.3 - Home Assistant restart evidence reconciliation

- Accumulate authoritative post-dispatch Home Assistant Core outage and
  reconnection evidence across bounded verification attempts, repeated apply
  calls, process restarts, and startup or periodic reconciliation.
- Retain the approximately 15-second initial probe budget while binding new
  outage observations to an independent immutable 180-second interval derived
  from the original persisted dispatch timestamp. Recovery may finish later
  after a qualified outage, but a future unrelated outage cannot verify an old
  plan.
- Validate persisted outage evidence as one complete record; incomplete or
  malformed raw outage flags fail closed and cannot skip the Core probe or
  satisfy terminal verification.
- Require confirmed dispatch, a qualified Core-unavailable observation, and a
  later successful identity read with explicit `reconnected_at` evidence
  before a Home Assistant restart can reach
  `applied_verified`; current availability or provider acknowledgement alone
  remains insufficient.
- Preserve the exact-once dispatch boundary: reconciliation is readback-only,
  old plans without authoritative outage evidence remain pending, and no
  optional uptime entity is required.
- Retain 45 Engineering tools plus 26 delegated reads, 71 total, with no
  public schema, reviewed fingerprint, provider policy, stable-v1, or fallback
  change.

## 2.1.1-beta.2 - Beta 2 self-restart identity correction

- Resolve the running Engineering add-on through Supervisor's exact
  caller-relative self metadata rather than comparing an installed,
  repository-prefixed slug with the source add-on slug.
- Bind new restart plans to the requested and resolved add-on identity.
  Exact self-targets retain `process_identity`; reviewed upstream restarts
  retain `upstream_readmission`; ordinary add-ons retain the weaker
  `provider_acknowledgement` proof grade.
- Normalize a missing installed add-on to non-retryable `addon_not_found`.
  This expected domain outcome does not degrade an otherwise exact provider
  contract or increment operational provider failures.
- Retain the complete Beta 2 surface of 45 Engineering tools plus 26
  delegated reads, 71 total, with no schema, reviewed fingerprint, provider
  policy, write reachability, or fallback change.

## 2.1.0-beta.2 - 2.1A governed operational lifecycle

- Add proposal-only `create_reload_plan`, `create_addon_restart_plan`, and
  `create_home_assistant_restart_plan` tools while retaining the shared
  contract-v3 external approval, immutable hash, exact-once apply, persistence,
  audit, and readback lifecycle.
- Constrain exact reviewed `ha_reload_core`, `ha_manage_addon`, and `ha_restart`
  contracts to four reload domains, one exact installed add-on restart, and
  one confirmed Home Assistant restart. Generic mixed-tool exposure, arbitrary
  service data, provider arguments, and fallback remain prohibited.
- Reuse strict full configuration validation at reload and Home Assistant
  restart planning and immediately before dispatch; record planning and
  apply-time evidence without claiming a whole-configuration fingerprint.
- Persist dispatch intent before every action and add bounded background and
  startup reconciliation. Lost responses, server restarts, and expected
  connection loss resume verification only and never blindly redispatch.
- Verify reload state readability, exact add-on identity and restart evidence,
  Engineering self-restart process identity, upstream `ha-mcp` readmission,
  Home Assistant identity, tool restoration, governance and audit persistence,
  dependency recovery, and zero fallback.
- Normalize the reviewed valid single-entity-ID `ha_get_entity`
  missing-registry outcome to bounded, non-retryable `entity_not_found`;
  resolver, bulk, malformed, and unknown combinations remain fail closed.
- Classify proposal audit records as `access=proposal` with
  `operation_class=proposal`; approval and apply remain writes.
- Increase the complete catalog to 45 Engineering tools plus 26 delegated
  reads, for 71 total. The upstream catalog and all reviewed fingerprints,
  classifications, automatic-read decisions, and dashboard attestations remain
  unchanged.

## 2.1.0-beta.1 - 2.1A operational administration Dev1

- Add the proposal-only `create_backup_plan` tool and contract-v3 operational
  plans while reusing exact external approval, apply, storage, and audit
  authority.
- Add an Engineering-owned constrained provider for only
  `ha_manage_backup(scope="snapshot", action="create", name=...)` under the
  exact reviewed 7.14.1 and 7.14.2 contracts. Generic mixed-tool exposure,
  arbitrary arguments, restore, deletion, and fallback remain prohibited.
- Persist dispatch intent and consume approval before the one permitted
  provider invocation. Ambiguous transport outcomes enter verification-only
  recovery and never trigger blind creation retries.
- Verify completion independently through bounded `backup/info` evidence,
  distinguishing operation completion and inventory readback from unsupported
  archive-content integrity validation.
- Formalize strict reusable configuration-check evidence for later reload and
  restart milestones without changing the read-only `check_config` surface.
- Add operational audit and health evidence with explicit persistent,
  process-cumulative, and active-state counter sources.
- Increase the complete catalog truthfully to 42 Engineering tools plus 26
  delegated reads, for 68 total. Existing upstream fingerprints and policies
  remain unchanged.

## 2.0.1 - GA promotion

- Promote the accepted `2.0.1-rc1-dev2` runtime without changing public
  schemas, reviewed fingerprints, compatibility policy, admission, routing,
  tool registration, governance, audit, dashboard constraints, or fallback
  enforcement.
- Retain the MCP SDK upgrade from 1.9.0 to 1.28.1, strict dependency auditing,
  fail-closed private SDK registry integration, transactional dynamic-registry
  replacement and restoration, server capability behavior, and the prohibition
  on generic upstream write delegation.
- Retain source-controlled compatibility entries for exact `ha-mcp` 7.14.1 and
  7.14.2. Each entry has 78 release-specific tool-contract records, for 156
  total, and admits only its 26 explicitly reviewed automatic reads. Unknown
  versions fail closed; new or changed tools are not automatically admitted;
  per-tool quarantine remains available; and direct Home Assistant fallback
  remains prohibited.
- Retain SHA-256-bound canonical captures, complete per-tool fingerprint
  regeneration, deterministic registry drift validation, separately
  human-owned classifications and automatic-read decisions, dashboard
  decisions bound to exact built-in attestations, and a registry-derived
  exact-image CI matrix that verifies image-index, platform, OCI version, and
  OCI revision evidence before startup.
- Keep runtime-observed MCP identity separate from reviewed image and source
  evidence. The runtime does not claim to observe the live upstream container
  digest; deployment artifact verification remains an operator responsibility.
- Record operator-provided acceptance with reviewed 7.14.1 and 7.14.2,
  including `7.14.1 -> 7.14.2 -> 7.14.1 -> 7.14.2` transition testing without
  rebuilding Engineering.
- Preserve 41 Engineering tools plus 26 delegated reads for 67 total under
  complete admission, the catalog fingerprint
  `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`,
  zero fallback, and stable v1.1.2 historical-source isolation.

## 2.0.1-rc1-dev2 - reviewed upstream release updateability

- Replace the single reviewed generic-read release assumption with a compiled,
  source-controlled registry containing exact `ha-mcp` 7.14.1 and 7.14.2
  identity, provenance, image, protocol, catalog, per-tool contract, policy,
  dashboard, and review evidence.
- Admit only an exact reviewed version and exact per-tool contract; quarantine
  changed reads independently, keep new tools unreviewed and unexposed, report
  removed tools, and fail closed for unknown versions.
- Reconcile version changes atomically so one Engineering image can move
  between the two reviewed upstream releases without stale routes or a rebuild.
- Add deterministic capture, normalization, fingerprint, diff, validation,
  report, and candidate-generation tooling. Generated candidates remain
  unapproved until a reviewed source change authorizes them.
- Extend pinned-image CI across exact 7.14.1 and 7.14.2 image digests while
  retaining the original gate.
- Record that both reviewed upstream versions return the ambiguous
  `SERVICE_CALL_FAILED` envelope for a missing `ha_get_entity` registry entry.
  Engineering preserves the bounded generic operational failure rather than
  classifying untrusted English prose as a not-found outcome.
- Preserve 41 Engineering tools, 26 delegated automatic reads when either
  exact catalog is fully admitted, 67 total tools, generic-write prohibition,
  public schemas, governance, audit, redaction, and zero fallback.

## 2.0.1-rc1-dev1 - MCP SDK and dependency hardening

- Centralize the pinned MCP SDK's private FastMCP tool-registry access behind
  one fail-closed compatibility adapter with startup shape validation,
  read-only mapping snapshots, exact removal, cached version validation, and
  transactional replacement with restoration on post-check failure.
- Upgrade `mcp` from 1.9.0 to 1.28.1, the newest compatible stable 1.x release,
  while preserving Streamable HTTP, the exact reviewed `ha-mcp` 7.14.1
  admission contract, and the 41 plus 26 tool composition.
- Upgrade the directly used Engineering web and security dependencies to
  `aiohttp` 3.14.2, `uvicorn` 0.51.0, `starlette` 1.3.1, and `cryptography`
  48.0.1; retain the already-current PyYAML and jsonschema pins.
- Add a pinned `pip-audit` pull-request gate to the reusable validation
  workflow required by release promotion.
- Retain the one-second Engineering transport floor as explicit policy while
  removing the obsolete MCP 1.9.0 fractional-timeout rationale.
- Preserve exact `ha-mcp` 7.14.1 admission by explicitly requesting reviewed
  upstream protocol `2025-03-26` instead of broadening to the SDK's newer
  default.
- Implement MCP 1.28's delegated result-conversion callback while preserving
  the established bounded JSON text response and reviewed output contract.
- Preserve the normal MCP 1.28 `ClientSession` initialization state, including
  the public server-capabilities accessor, while requesting the exact reviewed
  protocol.
- Record CVE-2025-66416 as a mitigated, deferred production configuration risk
  tracked by issue #62; do not infer loopback Host protection for the
  production `0.0.0.0` bind.
- Retire stable v1.1.2 as an operational rollback target and scope dependency
  audit and behavioral tests to the Engineering server. Historical v1 source
  and packaging remain unchanged.
- Preserve public schemas, fingerprints, provider policy, audit semantics,
  governance, dashboard behavior, and zero fallback.

## 2.0.0 - GA promotion

- Promote the accepted RC2dev16 runtime without changing functional behavior,
  public schemas, reviewed fingerprints, provider policy, admission, routing,
  tool registration, governance, dashboard reads, or fallback enforcement.
- Retain the existing `hass_mcp_engineering_beta` slug, port `8100`, Ingress,
  image repository, options, `/data` persistence, connector identity, and
  runtime/display name to avoid migration or contract drift.
- Preserve 41 Engineering tools plus 26 exact reviewed `ha-mcp` 7.14.1 reads
  for 67 total tools when fully admitted.
- Add exact GA release, in-place upgrade, rollback, provenance, and acceptance
  guidance. Stable v1.1.2 was retained unchanged at release time but is now
  operationally retired and is not a supported rollback installation.

## 2.0.0-rc2-dev16 - delegated error normalization

- Classify only explicitly allowlisted structured ha-mcp 7.14.1 error codes,
  preserving caller validation, optional-capability, authentication,
  connection, timeout, and internal-provider distinctions.
- Keep arbitrary, malformed, oversized, and unknown upstream error content on
  the bounded generic provider-error path without reflecting upstream prose,
  metadata, or retryability.
- Record caller validation and capability-unavailable answers as completed
  provider dispatches rather than operational provider failures, while
  retaining truthful failure audit outcomes.
- Document how clients should choose filtered entity discovery, exact
  dependency analysis, known-configuration reads, and broad configuration-text
  search with truthful partial coverage.
- Preserve the 41 Engineering plus 26 delegated catalog, exact upstream
  admission, public schemas, zero fallback, governed writes, dashboard
  behavior, and stable v1.1.2.
- Defer the separately planned generic signed-registry milestone; Dev16 adds no
  registry lifecycle, search implementation, deployment, or publication path.

## 2.0.0-rc2-dev15 - contract-level upstream compatibility

- Require the exact compiled generic release/profile, currently ha-mcp 7.14.1,
  before admitting each reviewed automatic read by its exact input, output,
  safety, full-runtime-description fingerprint, behavior, identity, and
  protocol contract.
- Separate exact upstream wire-annotation presence/value fingerprints from the
  stricter Engineering-owned annotations published after admission; never
  normalize omitted optional hints into invented defaults.
- Bind each reviewed read to the pinned generic object output-schema
  fingerprint at discovery and immediately before dispatch.
- Keep unchanged reads available, quarantine only changed reads, remove missing
  reads, and block new, mixed, write, action, and unsupported tools without
  collapsing the complete delegated catalog.
- Evaluate the argument-constrained dashboard family independently and require
  an exact built-in or verified signed release attestation before applying its
  compiled contract checks.
- Preserve last-known-good routes across transient discovery failures, bind
  calls to the admitted catalog generation, and separate fast startup recovery
  from slow compatibility reprobes.
- Keep process liveness available but return bounded readiness and HTTP 503 for
  authenticated MCP traffic until configured upstream reconciliation publishes
  its first stable or terminal catalog result.
- Let equivalent reviewed-catalog observations publish under busy exact calls,
  while coalescing repeated stale mismatches to one immediate retry and then
  the bounded slow cadence.
- Revalidate exact release/profile authority and the selected target's contract
  through same-session `tools/list` before `tools/call`; fail closed on
  unreviewed version movement while retiring a missing, duplicate, or changed
  target independently.
- Use immutable route snapshots and short leases so delegated network I/O is
  concurrent; a retired route cannot dispatch or be revived by a finishing
  in-flight call.
- Keep unrelated malformed or duplicate unreviewed descriptors unavailable as
  bounded reconciliation anomalies without blocking an exact selected target.
- Preserve stable v1.1.2, public schemas, governed configuration writes,
  `ha_search` completeness truth, and the no-fallback boundary.

## 2.0.0-rc2-dev14 - practical governed configuration plans

- Add one bounded `create_configuration_plan` entry point for ordered
  automation, script, `input_boolean`, and `input_number` create/update
  operations under one exact Home Assistant administrator approval.
- Apply approved operations in dependency order with all-target and immediate
  per-operation stale checks, typed write adapters, exact readback, one final
  configuration check, and truthful partial or uncertain outcomes.
- Keep raw writers, deletion, reload, restart, deployment, fallback, automatic
  rollback, and every unsupported resource or action unreachable.
- Preserve the existing six governance tool schemas, all 25 canonical
  capabilities, the 26 exact reviewed delegated reads, and stable v1.1.2.

## 2.0.0-rc2-dev13 - reboot reconciliation and completeness truth

- Start the native Engineering listeners without blocking on ha-mcp readiness,
  then supervise exact fail-closed read-gateway admission with capped retry
  delays until the reviewed catalog becomes available.
- Preserve all 40 statically registered tools (25 canonical plus 15
  Engineering-native) during retry and automatically make all 26 exact delegated
  reads available to subsequent `tools/list` calls without a manual Engineering
  restart.
- Preserve `ha_search`'s reviewed top-level `partial` signal in Engineering
  response metadata, provider metrics, and request telemetry;
  missing or malformed completeness fails closed as partial.
- Preserve all public schemas, provider policy/trust data, no-fallback and
  no-write boundaries, stable v1.1.2, governance, and deployment behavior.

## 2.0.0-rc2-dev10 - selected-attestation observability truth

- Derive retained raw-schema, reviewed-security, fixture-runtime, and
  published-runtime expectations from the exact built-in or signed attestation
  selected for the observed upstream release.
- Keep those informational fingerprints distinct from the normalized
  input/security/output/runtime fingerprints that remain the admission gate.
- Report `ha_mcp_dashboard_read_v2` as the active dashboard trust profile and
  describe exact-release attestation instead of a globally pinned version.
- Preserve routing, the one-tool allowlist, exact non-screenshot arguments,
  hashes, no-fallback enforcement, public schemas, and governance storage.

## 2.0.0-rc2-dev9 - signed upstream contract admission

- Replace the one-version dashboard profile with the compiled
  `ha_mcp_dashboard_read_v2` family and exact built-in attestations for
  ha-mcp 7.13.0, 7.14.0, and 7.14.1.
- Add an optional Ed25519-verified, fixed-location release-attestation registry
  with atomic last-known-good caching, expiry, revocation, and sequence rollback
  protection. Registry data cannot define tools, argument builders, or families.
- Preserve the one-tool dashboard allowlist and exact non-screenshot inventory
  and configuration call shapes; all upstream write and generic delegation
  paths remain unavailable.

## 2.0.0-rc2-dev8 - pre-validation enforcement and audit truth

- Intercept `call_service`, `reload_domain`, `upsert_automation`, and
  `delete_automation` at the authenticated Streamable HTTP boundary before
  FastMCP/Pydantic argument coercion so their fixed fail-closed policy result is
  independent of caller argument shape.
- Return the existing canonical `provider_unavailable` or
  `provider_prohibited` Engineering envelope without provider, Home Assistant,
  upstream, governance, or fallback dispatch.
- Inspect bounded MCP responses for structured Engineering failures,
  FastMCP validation failures, and JSON-RPC errors so HTTP 200 no longer causes
  failed tool calls to be audited as successful.
- Preserve all 40 public tool schemas, the 25 canonical classifications, zero
  planned tools, RC2dev7 authentication/audit filtering, governance storage,
  dependency-index behavior, and the pinned ha-mcp 7.13.0 dashboard profile.

## 2.0.0-rc2-dev7 - exact audit-event filtering

- Parse bounded audit JSONL records and compare only the exact, case-sensitive
  top-level `event` field instead of searching serialized lines.
- Keep routed `get_audit_log` calls self-audited as `tool_call` without allowing
  their nested filter argument to contaminate `auth_failure`,
  `auth_failure_throttled`, or `rate_limited` evidence.
- Skip malformed, non-object, blank, and oversized historical records while
  preserving the existing bounded JSONL response, ordering, redaction, public
  schemas, authentication controls, provider routing, governance storage, and
  stable v1.1.2 behavior.

## 2.0.0-rc2-dev6 - throttled-authentication audit correction

- Distinguish ordinary authentication rejection (`auth_failure`) from
  authentication-failure limiter exhaustion (`auth_failure_throttled`) while
  preserving the existing 404/429 responses, error codes, limiter behavior,
  secret redaction, and pre-dispatch enforcement.
- Keep authenticated general rate limiting separately audited as
  `rate_limited`; no provider, governance, dependency-index, dashboard, or
  stable-v1 behavior changes in this release.

## 2.0.0-rc2-dev5 - live-acceptance corrections

- Replace the five-minute hard dependency-index expiry with configurable
  10-minute soft and 60-minute hard bounds, single-flight background refresh,
  explicit stale-evidence metadata, and nonblocking delayed startup prewarm.
- Normalize the reviewed ha-mcp 7.13 missing-dashboard envelope as the
  non-retryable `dashboard_not_found` domain outcome without degrading provider
  reachability or contract health.
- Return deduplicated root-cause groups in reliability summary mode and keep
  intentional unavailable-state triggers as informational notes.
- Treat Home Assistant webhook IDs as sensitive identifiers in relayed logs.
- Repair deterministic annotated-tag identity and partial-promotion reporting;
  this change does not backfill the missing RC2dev4 tag.

## 2.0.0-rc2-dev4 - release hardening

- Removed every reachable legacy direct-write implementation and made the
  capability catalog report governed redirects, prohibitions, and unavailable
  providers truthfully.
- Distinguished plans that merely require approval from plans with a pending
  external Ingress challenge; pre-approval principal separation is now
  explicitly not evaluated.
- Added single-flight dependency-index builds, cold-build profiling, explicit
  build-state/expiry semantics, a bounded eight-read pool, optional one-shot
  connectivity-gated prewarming, and expected-outcome metrics.
- Hardened relayed System Log and structured-log redaction, dashboard not-found
  handling, upstream health freshness, summary response size, and reliability
  root-cause deduplication.
- Added a fixture-only transport bake harness and an RC2dev4 acceptance guide.

## 2.0.0-rc2-dev3

- Reproduce the exact public `ha-mcp` 7.13.0 add-on runtime descriptor and
  record its immutable artifact and dependency evidence.
- Separate the complete input-schema and reviewed security-contract blocking
  fingerprints from the complete runtime-descriptor observability fingerprint.
- Permit only the proven presentation-only `_meta.ha_mcp` exposure/pinning
  delta while preserving exact identity, version, protocol, input schema,
  safety annotations, output contract, argument builders, and tool allowlist.
- Report expected/observed component fingerprints and bounded descriptive
  versus semantic runtime drift without exposing schemas or endpoint data.
- Publish `readOnlyHint=true` on both Engineering dashboard tools and prove the
  raw server-side MCP `tools/list` contains all 40 tools even while the provider
  is unavailable; clients may need connector refresh after schema caching.
- Stage dev3 in `.release/next-version` while Home Assistant metadata remains
  at published dev2 until the image is built, anonymously verified, and
  atomically promoted.

## 2.0.0-rc2-dev2

- Add the reviewed `ha_mcp_7_13_dashboard_read_v1` trust profile for upstream
  `ha-mcp` 7.13.0 at commit
  `f4eb53621ccb814cb7123d2811e06eda3577129c`.
- Distinguish contract-level read-only trust from the explicit
  `reviewed_argument_constrained` exception for the mixed-operation dashboard
  tool.
- Pin initialize identity, version, protocol, exact annotations, input schema,
  and the complete canonical tool contract.
- Construct only non-screenshot inventory and exact-configuration argument
  shapes; reject rendering, preferences, unknown arguments, and other tools
  before transport dispatch.
- Preserve the verified upstream 16-character optimistic-lock hash and
  independent 64-character Engineering evidence hash.
- Stage dev2 in `.release/next-version` while leaving Home Assistant metadata at
  dev1 until the image is built, anonymously verified, and atomically promoted.
- Replace manual tag-first publication with one main-only, repository-locked
  promotion transaction that publishes and verifies before pushing the release
  commit and annotated tag.
- Preserve 40 registered/25 canonical/zero planned capabilities, public input
  schemas, governance, external approval, audit, direct-HA policies,
  unavailable Standard HA MCP delegation, and production v1.1.2.

## 2.0.0-rc2-dev1

- Add the RC3A read-only `upstream_dashboard` provider using the maintained
  `mcp==1.9.0` streamable-HTTP client already present in the image.
- Add bounded `list_dashboards` and `get_dashboard_config` tools backed only by
  upstream `ha_config_get_dashboard`.
- Verify the upstream 16-character dashboard optimistic-lock hash against an
  exact local recomputation and expose a distinct 64-character Engineering
  evidence hash calculated from complete raw JSON before sanitization.
- Classify rejected endpoint/secret paths, connection failures, and genuine
  timeouts deterministically without exposing endpoint details.
- Use AwesomeVersion 25.8.0 to select the installable RC3A development version
  `2.0.0-rc2-dev1`; the rejected `2.0.0-rc.2.rc3a.1` form is incomparable.
- Validate the upstream read annotation and minimum input schema, record
  sanitized identity and schema/catalog fingerprints, and fail closed on
  missing or incompatible capabilities.
- Treat the complete upstream URL as a password-style secret and exclude it
  from responses, health, logs, audit records, errors, and tracebacks.
- Preserve all 25 canonical tool contracts, Standard HA MCP unavailability,
  direct-HA policies, governance, external approval authority version 2, and
  production v1.1.2.
- Add no dashboard write, delete, backup, service, physical-action, generic
  upstream forwarding, or live deployment path.

## 2.0.0-rc.2

- Supersede immutable RC1 because Home Assistant could continue resolving the
  original `2.0.0-rc.1` image after the reviewed source correction retained the
  same add-on version and image tag.
- Publish the already reviewed `search_entities` correction under a distinct
  installable version: explicit `transitional_direct` routing to
  `direct_ha_api`, policy `bounded_entity_state_search`, one bounded read-only
  `/states` inventory, deterministic slim results, and no fallback.
- Preserve the Beta 26 public schemas and enums, 38 registered tools, 25
  canonical tools, zero planned capabilities, schema version 1, external
  approval authority version 2, and production v1.1.2 unchanged.
- Retain deterministic multi-architecture GHCR provenance and publication for
  amd64, arm64, and arm/v7 with immutable version and source-commit tags.

## 2.0.0-rc.1

- Freeze the accepted Beta 26 public catalog, schemas, governance semantics, and
  external authority version 2 for release candidate validation.
- Add deterministic image-build provenance for the existing `build_sha` and
  `build_time` fields, with safe `unknown` fallbacks for local development.
- Add clean-install and persisted Beta 26 upgrade compatibility coverage plus
  the RC1 deployment, acceptance, soak, and rollback procedures.
- Preserve 38 registered tools, 25 canonical tools, zero planned capabilities,
  schema version 1, the Beta/RC ports and slug, and production v1.1.2 unchanged.

## 2.0.0-beta.26

- Make plan expiration a single terminal transition; repeated plan, list,
  health, Ingress, and handoff reads no longer rewrite an expired record or
  duplicate lifecycle events, audit entries, logs, or `updated_at` changes.
- Resolve external-challenge expiry through one governance lifecycle path so
  public reads, health, Ingress, approval requests, apply, and rollback agree on
  the effective current state.
- Exclude expired challenges from actionable plan views, the Ingress inbox, and
  `pending_challenge_count`; eligible plans may request one fresh challenge
  bounded by the plan expiry while the old challenge remains unusable.
- Preserve fail-closed apply/rollback enforcement, external authority version 2,
  distinct apply/rollback approvals, and all Beta 25 principal-separation rules.
- Preserve 38 registered tools, 25 canonical tools, zero planned capabilities,
  schema version 1, and every Beta 25 public input schema. Production v1.1.2 is
  unchanged.

## 2.0.0-beta.25

- Make `approve_change_plan` request external review without granting approval;
  the public input schema is unchanged.
- Add an administrator-only Home Assistant Ingress approval panel on unmapped
  internal port 8110 with escaped HTML, POST-only decisions, one-time CSRF and
  strict bounds/security headers.
- Persist 15-minute authority-version-2 challenges bound to the exact plan hash,
  kind, target, operation and risk; preserve idempotency, expiry, invalidation,
  replay resistance and restart recovery.
- Require separate external apply and rollback approvals and make rejection a
  terminal historical state.
- Fail active Beta 24 caller approvals closed; never silently migrate authority.
- Update handoff, audit and health contracts for external-pending, approved,
  consumed, rejected, expired and invalidated states.
- Add disposable, digest-pinned Home Assistant Core 2026.7.2 REST/WebSocket,
  id-less automation, configuration-validation and trace contract tests.
- Preserve 38 registered tools, 25 canonical tools, zero planned capabilities,
  schema version 1 and every existing public input schema. Production v1.1.2 is
  unchanged.

## 2.0.0-beta.24

- Treat top-level automation `id` as explicitly verified identity metadata,
  outside behavioral normalization, fingerprints, and plan hashes.
- Fail closed for legacy `upsert_automation` before provider dispatch and require
  the governed plan/approval/apply workflow.
- Require an explicit matching policy for every direct Home Assistant exception.
- Make forwarded client identity untrusted by default and add bounded trusted
  proxy CIDR configuration for validated IPv4/IPv6 forwarding.
- Replace whole-store rate-bucket resets with atomic bounded LRU eviction.
- Exclude known-unavailable pre-dispatch providers from provider request/failure
  counters and clamp audit-log reads to 1–500 records.
- Preserve 38 registered tools, 25 canonical tools, zero planned capabilities,
  and all public schemas. Production v1.1.2 remains unchanged.

## 2.0.0-beta.23

- Require an explicit provider-dispatch assertion before global provider request,
  success, partial, or failure counters change.
- Keep request/cursor validation, authentication, rate limiting, policy rejection,
  and snapshot-only continuation out of provider failure accounting.
- Preserve one attributable failure for real Engineering/direct provider errors
  and timeouts; keep successful partial coverage non-failing.
- Retain all Beta 22 handoff corrections, 38 registered/25 canonical tools, an
  empty planned list, all public schemas, and production v1.1.2 unchanged.

## 2.0.0-beta.22

- Normalize shared handoff evidence to one effective coverage record per logical
  source, removing synthetic dependency failures while preserving real failures.
- Treat expired, superseded, rolled-back, and validation-only terminal plans as
  retained history rather than active pending work or authorization requirements.
- Resolve automation internal IDs through one bounded state inventory and freeze
  successful automation entity IDs into structured, Markdown, and cursor scope.
- Define `risk_count` as the number of handoff items in the `risks` section and
  keep open/authorization counts limited to current actionable work.
- Retain 38 registered/25 canonical tools, all public schemas, an empty planned
  list, the read-only boundary, and production v1.1.2 unchanged.

## 2.0.0-beta.21

- Add read-only Engineering-native `handoff_generation`, increasing the beta
  catalog to 38 registered/25 canonical tools with no existing schema changes.
- Add system-status, focused-review, incident, and change handoffs with explicit
  fact/inference/recommendation/limitation and authorization contracts.
- Require apply plus verification evidence before work is called completed;
  preserve pending, failed, and rolled-back lifecycle truth.
- Add deterministic structured/Markdown output, five-minute signed sanitized
  pagination, bounded audit, dedicated health counters, and Beta 20 coverage
  semantics without any write capability.
- Remove `handoff_generation` from planned capabilities; no planned feature
  capability remains. Production v1.1.2 is unchanged.

## 2.0.0-beta.20

- Correct incident-correlation coverage so a successfully built, usable but
  incomplete dependency index has `failure_category=null` rather than
  `provider_upstream_error`.
- Distinguish complete, partial usable, failed, item-failed, unsupported,
  truncated/retention-limited, and not-requested evidence through shared coverage
  normalization.
- Separate hypothesis `missing_evidence` from stable bounded
  `coverage_limitations` while retaining supporting dependency references.
- Count only actual failed sources or source operations in health and provider
  failure telemetry; preserve partial assessment and cursor snapshot behavior.
- Reject non-canonical signed cursor encodings fail-closed without changing valid
  cursor behavior.
- Retain 37 registered/25 canonical tools, all existing public schemas, the
  read-only boundary, planned `handoff_generation`, and production v1.1.2.

## 2.0.0-beta.19

- Add read-only Engineering-native `incident_correlation`, increasing the beta
  tool count to 37 while retaining 25 canonical tools and all existing schemas.
- Correlate bounded automation, entity, trace, history, logbook, structured log,
  registry, dependency, integrity, and reliability evidence into deterministic
  ranked hypotheses with supporting and contradicting evidence.
- Add explicit confidence, causal-status, severity, coverage, event-normalization,
  clustering, pagination, security, audit, and health-counter contracts.
- Preserve the Beta 16–18 signed snapshot lifecycle; continuation performs no
  upstream collection, index work, classification, or recorrelation.
- Add no write capability; production v1.1.2 remains unchanged and
  `handoff_generation` remains planned.

## 2.0.0-beta.18

- Replace broad dotted-token template scanning with context-aware extraction from
  explicit entity-bearing fields and recognized Home Assistant template helpers.
- Reject numeric fragments, versions, IP addresses, URLs, hostnames, service
  names, member access, and arbitrary dotted prose as exact entity references.
- Preserve exact literal references for `states`, `is_state`, `is_state_attr`,
  `state_attr`, and `expand`, while reporting dynamic arguments separately and
  without an invented target.
- Harden the shared canonical entity-ID validator and retain deterministic
  deduplication, evidence IDs, index fingerprints, pagination, health, and audit
  contracts.
- Preserve all 36 public tools, all existing input schemas, the read-only safety
  model, and production v1.1.2.

## 2.0.0-beta.17

- Add the Engineering-native, read-only `configuration_integrity_analysis`
  tool, increasing the beta manifest from 35 to 36 tools while retaining 25
  canonical tools.
- Detect exact missing entity references, references to disabled and
  registry-only entities, conservative orphan-registry candidates, and
  unresolved dynamic references as distinct evidence-backed findings.
- Reuse the shared dependency index plus one bounded state inventory and one
  entity-registry inventory; unsupported coverage remains explicit.
- Reuse Beta 16 signed, immutable pagination snapshots with upstream-free
  continuation, fixed provenance, whole-analysis totals, and fail-closed
  cursor validation.
- Add deterministic deduplication, bounded detail levels, field-level
  validation, safe auditing, and dedicated health counters.
- Add no write capability and no automatic cleanup, reference rewrite,
  incident correlation, handoff generation, or RC stabilization.
- Preserve production v1.1.2 and all existing public tool input schemas.

## 2.0.0-beta.16

- Bind refreshed-index pagination snapshots and signed cursors to the final
  committed dependency-index generation, allowing immediate upstream-free
  continuation while retaining fail-closed expiry, tamper, query-change, and
  index-replacement checks.
- Separate impact findings from unique affected objects and root-cause groups in
  both results and health telemetry; retain corrected Beta 15 compatibility
  aliases with explicit deprecation metadata.
- Report confirmed target-related, unresolved requested-scope, and out-of-scope
  dynamic references separately, requiring manual review when requested coverage
  remains unresolved.
- Return stable field/reason validation details without provider activity,
  dependency-index access, or pagination state, and correct generated article
  wording without changing rule IDs.
- Preserve the 35-tool manifest, every public input schema, the read-only impact
  policy, governance boundaries, and production v1.1.2.

## 2.0.0-beta.15

- Add the read-only `change_impact_analysis` Engineering-native tool for one
  entity and a proposed rename, remove, or disable operation, increasing the
  callable beta manifest from 34 to 35 tools.
- Reuse the existing dependency index for bounded direct and indirect impact
  evidence; no second dependency graph or write authority was introduced.
- Add 22 deterministic, evidence-backed rule IDs, stable findings, affected-object
  grouping, operation-specific consequences, advisory remediation, and four
  conservative assessment states without an opaque risk score.
- Add exact state and entity-registry evidence, honest static-source coverage,
  bounded retained trace headers, and sanitized exact System Log correlation.
- Add signed evidence-bound pagination snapshots, detail-dependent payload caps,
  truthful cache/timing provenance, safe audit summaries, and identity-free health
  metrics.
- Preserve every Beta 14 tool name and input schema, governance and provider
  boundaries, and production v1.1.2.

## 2.0.0-beta.14

- Fixed the Beta 13 null analysis timestamp by accepting injected timezone-aware clock instants and capturing one UTC request instant.
- Unified `list_automation_traces` and reliability analysis behind one sanitized trace-list transport and normalization contract.
- Added Home Assistant `{start, finish}` trace interval support alongside offset ISO strings and permitted epoch timestamps.
- Made the lookback cutoff inclusive, timezone-aware, fixed for the request, and bound to pagination fingerprints.
- Added truthful trace coverage states and bounded counts for upstream, parsed, eligible, selected, retrieved, failed, malformed, duplicate, and truncated runs.
- Restricted `no_recent_execution_evidence` to trustworthy empty trace results; source defects now return partial or failure truthfully.
- Added bounded sanitized pagination snapshots so cursor pages do not repeat HA trace collection or inflate aggregate counters.
- Preserved Beta 13 correlation, chronology, root-cause, timing, cache-truth, redaction, routing, and write-boundary protections.
- Preserved all 34 tool names and input schemas; production v1.1.2 remains unchanged.

## 2.0.0-beta.13

- Corrected reliability observation chronology using timezone-aware UTC ordering independent of Home Assistant source order.
- Replaced broad System Log substring correlation with exact identifier and trace-signature bases, with safe confidence metadata.
- Added deterministic root-cause groups so overlapping trace and action findings are not presented as independent incidents.
- Standardized reliability timestamps as RFC 3339 UTC strings and trace intervals as `started_at`/`finished_at` objects.
- Distinguished the bounded System Log snapshot from unverifiable lookback retention without discarding independent findings.
- Separated cumulative Home Assistant request effort from upstream wall-clock span and concurrency.
- Marked reliability-result caching honestly unavailable and prevented pagination from inflating finding/root-cause counters.
- Preserved all 34 tool names and input schemas; production v1.1.2 is unchanged.

## 2.0.0-beta.12

- Add the read-only `automation_reliability_analysis` tool for one internal
  automation ID, increasing the beta manifest to 34 callable tools.
- Compose bounded configuration, state, blueprint, trace, referenced-entity,
  registry, and sanitized System Log evidence behind an engineering facilitator
  provider; no tool handler calls Home Assistant directly.
- Add 13 deterministic rules with stable findings, confidence/status, evidence
  references, fingerprint-bound pagination, three detail levels, and honest partial
  coverage. No opaque reliability score is produced.
- Bound lookback, traces, entity reads, concurrency, findings, evidence, log
  correlation, response size, and total duration; independent source failures retain
  useful confirmed findings.
- Add safe analysis health counters without exposing configuration or evidence.
- Collapse duplicate adjacent Matter setup-payload redaction markers while preserving
  detection and sanitizer idempotence.
- Preserve the original 25 names and input schemas, all eight prior beta-native tools,
  governance and Phase 3C boundaries, and production v1.1.2.

## 2.0.0-beta.11

- Sanitize the complete recursive Home Assistant System Log result before any
  entry selection, field bounding, normalization, formatting, or serialization.
- Add key-aware and free-text redaction for authentication material, auth flows,
  webhook secrets, Matter commissioning values, credential-bearing URLs,
  serialized Python/JSON representations, cookies, and known runtime secrets.
- Use stable category markers without exposing secret fragments, lengths, hashes,
  encodings, prefixes, or suffixes.
- Fail closed per field when sanitation raises, preserve existing markers
  idempotently, and keep prompt-like log text inert untrusted evidence.
- Report only bounded redaction categories, field counts, and fail-closed state.
- Preserve 33 tools, all original 25 input schemas, Phase 3C routing, governance,
  dependency behavior, and production v1.1.2.

## 2.0.0-beta.10

- Replace the conditionally registered `/api/error_log` REST read with Home
  Assistant's supported admin-only `system_log/list` WebSocket command.
- Preserve the `tail_lines` input while returning bounded, newest-first,
  structured warning/error entries with explicit truncation and untrusted-data
  metadata.
- Redact access secrets, Supervisor tokens, authorization material,
  credential-bearing URLs, webhook secrets, and session identifiers from log
  content before it reaches responses, application logs, or audit output.
- Classify pre-upstream request validation as `request_validation` source
  coverage with zero Home Assistant time.
- Count `recent_error_counts` once per terminal public tool failure instead of
  once at each REST, structured-response, and facilitator propagation layer.
- Preserve all 33 tools, Phase 3C's four direct administrative-read policies,
  dependency behavior, governance boundaries, and production v1.1.2.

## 2.0.0-beta.9

- Align capability truth for `get_entity`, `list_areas`, `search_services`, and
  `list_services`: lifecycle `transitional`, route `transitional_direct`, and provider
  `direct_ha_api` under four specific read-only policies.
- Preserve facilitator dispatch, normalized envelopes, source coverage, timing, audit
  correlation, and provider counters for all four administrative reads.
- Document the verified stateless Home Assistant `/api/mcp` endpoint and reject
  approximate `GetLiveContext` mappings for exact entity, area, and service semantics.
- Honor dependency-analysis limits through 100 with explicit requested/effective limit
  metadata; separate current lookup/request timing from original build provenance.
- Clarify dependency health counters as cumulative truncation events versus current
  unresolved-reference index state.
- Keep production v1.1.2 and all write, physical-action, reload, delete, and governance
  boundaries unchanged.

## 2.0.0-beta.8

- Route canonical delegated, transitional, direct-required, and prohibited tools
  through the facilitator dispatcher while preserving all 33 tool input schemas.
- Fail delegated calls with a structured provider error when the Standard HA MCP
  gateway is unavailable; never silently invoke the legacy direct-HA implementation.
- Normalize routed responses and attribute provider request, success, failure,
  partial-result, and prohibited-fallback metrics.
- Enforce a reviewed tool-specific allowlist for direct Home Assistant exceptions and
  verify that `entity_dependency_analysis` is present and serializable in `tools/list`.

## 2.0.0-beta.7

- Add the read-only `entity_dependency_analysis` tool; the beta manifest now exposes
  33 tools.
- Build a bounded in-memory dependency index from automation configuration, blueprint
  input/source roles, entity state, and entity registry evidence.
- Add exact structured/template extraction, partial source coverage, cautious stale
  assessment, stable cursors, cache/refresh/invalidation, and bounded detail levels.
- Report unsupported source families and standard-MCP delegation honestly as
  unavailable while preserving all prior schemas and production v1.1.2.

## 2.0.0-beta.6

- Establish the Engineering MCP facilitator architecture, deterministic provider
  routing policy, and transport-independent evidence-provider contracts.
- Represent standard Home Assistant MCP delegation honestly as unavailable until a
  supported nested client transport is configured and verified.
- Add bounded, paginated, deduplicated response and evidence models for future
  analytical tools, plus safe provider-routing health counters.
- Replace free-text safety keyword matching with structured action, service, target,
  entity-domain, and blueprint-input risk evidence; harmless descriptive text no
  longer produces high-risk plans.
- Preserve all 32 beta tools, the original 25 schemas, the seven beta-native schemas,
  governance persistence compatibility, and production v1.1.2.

## 2.0.0-beta.5

- Map missing or invalid change-plan lookups to `change_plan_not_found` while
  reserving storage failures for real I/O, corruption, serialization,
  permission, and atomic-write failures.
- Treat the expected create-automation availability 404 as a successful probe
  branch so client responses, logs, plan events, and tool-call audits agree.
- Reject existing automation IDs as `configuration_conflict` and malformed or
  failed HA probe responses as real upstream failures.
- Replace transport-lifetime request latency with separate MCP operation, tool,
  and Home Assistant latency summaries; open stream lifetime is excluded.
- Preserve all 32 beta tools and all original 25 compatibility schemas.

## 2.0.0-beta.4

- Add approval-based change plans for creating and updating Home Assistant
  automations, with deterministic dry-run diffs and risk classification.
- Add hash-bound approval, stale-state protection, per-target concurrency,
  controlled apply, read-back verification, and separately approved rollback.
- Add atomic beta-only governance persistence, retention, corrupt-record
  quarantine, restart recovery, safe audit events, and bounded health metrics.
- Expose six governance tools for 32 total callable beta MCP tools while
  preserving all 25 production-compatible tool schemas.

## 2.0.0-beta.3

- Add fail-closed beta deployment and metadata validation for Windows development.
- Add a repeatable beta release checklist, optional health check, and cache-delay
  troubleshooting guidance.
- Keep the production v1.1.2 add-on and runtime unchanged.

## 2.0.0-beta.2

- Explicitly register `get_server_health` with the served FastMCP registry and
  verify its `tools/list`/`tools/call` exposure.
- Correlate upstream HA 4xx/5xx failures across structured tool responses,
  logs, and audit records; entity 404s now use `entity_not_found`.
- Add typed success and failure response contracts and a stable error taxonomy.
- Add request correlation, structured logging, bounded audit records, timing,
  and safe runtime metrics.
- Add beta-native `get_server_health` and migrate `server_info`,
  `list_capabilities`, and `get_error_log` to structured responses.

## 2.0.0-beta.1

- Add an isolated, parallel-installable v2 beta add-on.
- Introduce modular application, gateway, client, model, audit, capability, and
  version boundaries.
- Preserve the v1.1.2 25-tool catalog and public argument schemas.
