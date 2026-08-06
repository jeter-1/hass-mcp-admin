# HA MCP Engineering Server 2.2.0-beta.22 approval-review acceptance

## Source and release boundary

- repository: `jeter-1/hass-mcp-admin`;
- merged Beta 21 base and exact PR #95 restack target:
  `bc150bd2ed1ee2744bc25e2f2f1930e5155efbe9`;
- accepted PR #94 head and second parent of that merge:
  `35a83f2d2065b488ccff3594c9e8a7a629f9dcb9`;
- merged Beta 20 main and first parent of that merge:
  `e2152911c0f3581c38b6ef42e52a2dd221cd8d96`;
- pre-restack independently reviewed Beta 22 head:
  `1f249de2f40ed698eb536ac1d1221bf0b429d2bc`;
- post-restack clean-snapshot Evidence head, including O-1/O-2 tests:
  `e03b7af4340e0cceca748815c7eb71eb394a3f14`;
- independently reviewed and accepted pre-correction PR #95 head:
  `ddc5e4fbd2ee922a4e1fe99cb8c67919ca71f622`;
- Engineering/stable versions: `2.2.0-beta.22` / `1.1.2`;
- protocol: `2025-03-26`;
- secure pins: `aiohttp==3.14.3` and `cryptography==50.0.0`; and
- public tool, upstream admission, and fallback deltas: zero.

This is an approval-review correctness release stacked on Beta 21. It does not
change the accepted F3 adapter, lock, intent, dispatch, recovery, rollback, or
exact `ha-mcp` 8.1.0 compatibility semantics.

## Post-merge restack and review hardening

PR #94 was merged with exact reviewed head `35a83f2d...`, producing merge
commit `bc150bd2...` with the two parents recorded above. Promotion run
`31061625405` was canceled before its detection or promotion jobs executed.
After cancellation, the Beta 21 tag, GitHub release, GHCR image tag, and any
promotion commit were all absent.

The ten previously reviewed Beta 22 commits were replayed without conflict
onto the exact merge commit. The two accepted nonblocking observations were
then added as regression-only hardening:

- O-1 records the arguments passed to semantic-projection validation and
  proves that `policy_class` and `physical_impact` match the authoritative plan
  policy decision even when projection-controlled risk fields contain different
  values; and
- O-2 parses the approval renderer and proves that semantic `before` and
  `after` snapshots flow through the complete snapshot renderer without any
  `_summary_scalar` clipping call.

These guards change no production runtime, schema, policy, or dispatch path.
They increase the Beta 22 focused module from 18 to 20 tests. The restacked PR
must remain draft and unmerged until a new independent exact-head review is
complete.

## Post-review release-readiness correction

The protected-merge preflight for accepted head `ddc5e4fb...` found two
publication blockers before PR #95 was marked ready or merged:

- the Beta 22 acceptance and release-note records used descriptive filenames
  that the exact document-authority resolver did not recognize; and
- the normal promotion workflow created and verified the immutable image and
  annotated tag, but did not create the required GitHub Release.

The records now use the canonical authority paths
`docs/V2_2_0_BETA22_ACCEPTANCE.md` and
`docs/V2_2_0_BETA22_RELEASE_NOTES.md`. The promotion authority validates that
exact pair and passes the resolved release-note path to publication rather
than reconstructing or guessing it.

After anonymous multi-architecture image, provenance, SBOM, release-commit,
and annotated-tag verification, the workflow now creates a non-draft Beta 22
GitHub prerelease from the canonical notes. Its appended immutable identity
records the exact source and release commits, both immutable image references,
OCI manifest digest, build timestamp, declared architectures, and verified
attestations. The workflow reads the Release back and requires exact tag,
title, URL, body, draft state, prerelease state, publication ID, and publication
timestamp before reporting the release complete.

Both the initial and final Release-absence probes fail closed unless GitHub
returns an explicit HTTP 404. Main or annotated-tag drift also fails closed.
If the immutable image and tag succeed but Release creation or readback fails,
the summary identifies the partial phase and directs recovery to the Release
record only; it forbids rebuilding or overwriting the immutable image or tag.
PR validation retains read-only permissions and cannot create a Release.

Deterministic offline execution tests cover successful exact publication and
refusal when a Release already exists, main moves, or the tag moves. The
canonical authority resolver and workflow structure have permanent regression
coverage. Fast protected-path validation passed with 57 workflow tests, one
documented Windows-only skip, exact base `bc150bd2...`, and explicit authority
for the inherited Engineering runtime/version paths plus the publication
workflow.

The complete protected-path Full gate then passed at release-correction
implementation head `5fe56c68134cc229af504b1ce91aaf131a018b93`:
1,939 tests discovered, two documented skips, zero failures, and all metadata,
dependency-consistency, YAML, PowerShell, secret-pattern, and whitespace checks
passed. The first sandboxed attempt reported six errors because the desktop
sandbox denied creation of temporary loopback HTTP test servers. An otherwise
identical permission-corrected run at the same commit executed those cases and
passed; this was an execution-environment restriction, not a product or test
failure.

This correction does not change Engineering runtime behavior, stable 1.1.2,
Beta 22's advertised version, public tools, schemas, policy, dispatch, or
Home Assistant compatibility. It deliberately changes the accepted PR head,
so PR #95 remains draft and unmerged pending complete Evidence, exact-head CI,
and a new independent exact-head review and merge authorization. No tag,
Release, image, deployment, restart, canary, or Home Assistant mutation is
performed by this correction.

## Defect and independently traced root cause

At the PR #94 exact head, configuration plan creation persisted normalized
operations without an authoritative review projection. `approve()` could then
create and persist an external challenge before final semantic reviewability
was checked. `_review_summary()` rebuilt a projection from the persisted
proposed configuration every time the approval page was loaded, and
`decide_external_approval()` enforced completeness only at the final decision.
The Ingress page could consequently refuse a plan that plan creation and the
approval-request path had already accepted.

The lossy implementation was in
`governance/service.py`:

- `_approval_primitive()` omitted strings longer than 200 characters;
- `_configuration_approval_projection()` called the sanitizer with
  `max_string=200` and treated truncation or redaction as incompleteness;
- `MAX_APPROVAL_PROJECTION_STEPS=16`, metadata `=10`, targets/data `=8`,
  depth `=4`, controls `=16`, per-plan actions `=32`, and details `=128`
  silently bounded the semantic view; and
- `_configuration_approval_review_complete()` and the final decision path
  discovered the loss after a challenge could exist.

`approval_web.py` added another presentation-only 200-character boundary in
`_semantic_value()`. The old expected behavior was fixed in
`tests/test_dev14_configuration_plans.py`, including the long-same-prefix,
unsupported-nested-construct, and 17-action refusal cases. Those expectations
proved the defect before they were replaced with complete-review assertions.

The traced end-to-end path was plan request -> operation normalization ->
lossy review reconstruction -> plan persistence -> F2 plan/policy authority ->
approval challenge -> private Ingress reconstruction -> external decision ->
F3 proposal/prepared hash -> F3 execution.

## Correctness invariant and new behavior

> A governed configuration plan enters the approval workflow only when every
> ordered operation has a deterministic, complete, safe, user-reviewable
> semantic projection bound to the exact prepared configuration that F3 can
> later execute.

`governance/semantic_projection.py` now creates the projection once from the
normalized before/after material during plan preparation. Plan creation
validates it before plan, audit, approval, or F3 persistence. Projection
failure returns the stable
`configuration_projection_unreviewable` domain outcome with a bounded
`projection_error` reason. It creates no approval task or challenge, F3 intent,
provider dispatch, mutation, fallback, or audit artifact.

The only semantic size boundaries are explicit product boundaries:
131,072 canonical UTF-8 JSON bytes per operation and 1,048,585 bytes for the
eight-operation array, including its JSON framing. The aggregate remains below
the existing 1,100,000-byte MCP outcome capture boundary, leaving response
envelope margin. These are not UI clipping limits. No changed entry or
reviewable string is silently dropped.

## Projection schema 1

Each persisted operation projection contains:

| Field | Authority |
|---|---|
| `projection_schema_version`, `projection_complete` | exact schema and completeness |
| `operation_index`, `operation_id`, `operation_type` | ordered operation identity |
| `resource` | resource type/subtype and exact target identifier |
| `risk` | risk, policy, and physical-impact classifications |
| `changes[]` | ordered JSON Pointer, add/modify/remove type, and complete before/after snapshots |
| `before` / `after` snapshot | exact JSON value, absence, or field-aware redaction categories |
| `redacted_change_count` | exact protected-change accounting |
| `binding` | current-state fingerprint and raw/normalized prepared hashes |
| `projection_hash` | SHA-256 of canonical projection JSON excluding only the self digest |

Canonical serialization uses UTF-8, sorted object keys, compact separators,
`ensure_ascii=false`, and rejects non-finite/non-JSON values. Change order is
operation order followed by UTF-8-sorted mapping keys and list index. Long
automation, script, helper, nested action/condition/trigger, template, and
ordinary scalar values remain complete.

Field-aware sanitization replaces an entire protected snapshot with its sorted
redaction categories. The raw protected value is absent from the projection,
review HTML, audit data, and hashes other than one-way hashes of the prepared
configuration. Redaction therefore protects a value without declaring the
otherwise complete semantic projection incomplete.

## Authority and tamper binding

The projection digest is covered by both the F2 policy subject and the exact
configuration plan hash. The unchanged F3 migration receives that plan hash;
the unchanged F3 adapter includes it in the prepared payload and therefore in
`prepared_operation_hash`. This transitively binds the displayed projection to
the executable prepared operation without altering core F3 dispatch.

Creation, approval request, CSRF issuance, external decision, apply admission,
and F3 task creation validate the persisted projection. Target, operation
order, changed path, before/after value, completeness, schema version, prepared
configuration, or projection-digest tampering fails closed before dispatch.

## Historical records and approval page

The model reader accepts pre-Beta-22 records without fabricating new fields and
preserves their exact serialized shape. A historical pending plan with no
projection, a truncated projection, an unbound projection, or malformed
projection is audit-readable but non-approvable and non-dispatchable. It is not
reconstructed from mutable Home Assistant state; the operator must create a
new plan. Completed historical records remain readable and cannot be
redispatched.

The private Ingress page consumes only the persisted projection. It preserves
operation order, shows every change, distinguishes create/update, identifies
the exact typed target, shows risk and physical impact, and labels protected
changes without values. Values longer than 200 characters use escaped,
expandable `<details>` content; this presentation does not alter authority.
HTML, JSON, YAML, and template metacharacters are escaped. Existing Supervisor
Ingress-peer, authenticated administrator, CSRF, challenge, plan hash,
generation, prepared-hash, and hold-generation boundaries remain unchanged.

## Performance evidence

Projection and semantic diff work occurs once during plan preparation. The
approval page performs structural/hash validation and HTML rendering from the
persisted record, with no Home Assistant query, provider dispatch, or mutable
state reconstruction. The permanent regression is
`Beta22ReviewabilityTests.test_render_is_stable_and_requires_no_mutable_state_query`.

On 2026-08-05, CPython 3.12.13 on Debian 13/Linux 6.12.96 x86_64 rendered a
maximum eight-operation fixture containing 24 semantic changes 100 times in
0.189469 seconds (1.894686 ms mean). The 19,391-byte pages were byte-identical
and the fake gateway recorded zero calls. This is a measurement, not a newly
invented pass/fail latency threshold. The response remains bounded by the
projection product boundary plus worst-case HTML escaping and fixed form
overhead.

## Focused and regression evidence

The Beta 22 module covers single and eight-operation plans, more than eight
aggregate changes, long automation/script/template/scalar values, deep nested
structures, deterministic serialization, safe redaction, complete ordered
rendering, XSS/metacharacter escaping, creation failures, every authority
tamper class, post-approval tampering, historical variants, audit readability,
and transitive F3 prepared-hash binding. Existing suites continue to cover
Ingress admin/CSRF/challenge binding, stale plan/prepared/hold generations,
secret-free artifacts, F3 execution/recovery, public tool counts, fallback,
and exact 8.1.0 admission.

Recorded commands and final totals:

```text
PYTHONDONTWRITEBYTECODE=1 /tmp/hass-mcp-beta15-final-venv/bin/python -m unittest -v tests.test_beta22_approval_review_correctness tests.test_dev14_configuration_plans
# 76 tests, 0 failures, 0 skips

PYTHONDONTWRITEBYTECODE=1 /tmp/hass-mcp-beta15-final-venv/bin/python -m unittest -v tests.test_beta25_external_approval tests.test_f2_policy_approval tests.test_f3_configuration_identity tests.test_f3_configuration_migration tests.test_f3_runtime_integration
# 109 tests, 0 failures, 0 skips

PYTHONDONTWRITEBYTECODE=1 /tmp/hass-mcp-beta15-final-venv/bin/python -m unittest -v tests.test_ha_mcp_8_1_0_compatibility tests.test_exact_8_1_runtime_acceptance tests.test_hacs_8_1_response_compatibility tests.test_upstream_release_registry
# 56 tests, 0 failures, 0 skips

DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 /tmp/hass-mcp-pr70-pwsh/pwsh -NoLogo -NoProfile -Command "& ./scripts/check.ps1 -Tier Full -PythonExecutable /tmp/hass-mcp-beta15-final-venv/bin/python -BaseRef bc150bd2ed1ee2744bc25e2f2f1930e5155efbe9 -AuthorizedProtectedPath @('hass_mcp_engineering_beta/ha_mcp_engineering/','hass_mcp_engineering_beta/config.yaml')"
# Full passed at e03b7af4340e0cceca748815c7eb71eb394a3f14:
# 1,936 discovered, 2 skipped, 0 failures; suite step 179.387 seconds

DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 /tmp/hass-mcp-pr70-pwsh/pwsh -NoLogo -NoProfile -Command "& ./scripts/check.ps1 -Tier Evidence -PythonExecutable /tmp/hass-mcp-beta15-final-venv/bin/python -BaseRef bc150bd2ed1ee2744bc25e2f2f1930e5155efbe9 -AuthorizedProtectedPath @('hass_mcp_engineering_beta/ha_mcp_engineering/','hass_mcp_engineering_beta/config.yaml')"
# Evidence passed at clean head e03b7af4340e0cceca748815c7eb71eb394a3f14:
# 1,936 discovered, 2 skipped, 0 failures; suite step 176.226 seconds
# Snapshot identity remained stable and clean.
```

During the original pre-restack implementation, the first Full attempt
discovered the then-current 1,934 tests and two skips but found two preservation
defects: the fixed error-taxonomy expectation lacked the new domain code, and
historical prohibited apply returned projection-unreviewable instead of the
stronger established prohibited outcome. The focused fixes passed seven
historical/tamper tests plus the taxonomy test. The post-restack Full and
Evidence commands above include those fixes and the two new review guards.

## Skip-count reconciliation

Both inherited reports discovered the same 1,916 PR #94 tests. Neither used
pytest, test deselection, parametrization, or a reporting plugin. Python's
standard `unittest` reported environment-dependent skips:

- accepted Evidence command:
  `/tmp/ha-mcp-8.1-validation.GAy2fC/venv/bin/python -m unittest discover -s tests -v`,
  invoked as a child of `/tmp/hass-mcp-pr70-pwsh/pwsh` by
  `scripts/check.ps1`; `Ran 1916`, `OK (skipped=2)`, meaning 1,914 non-skipped
  successes, not 1,916 passed plus two additional tests;
- independent shell command:
  `/tmp/hass-mcp-beta15-final-venv/bin/python -m unittest discover -s tests -v`;
  `Ran 1916`, `OK (skipped=16)`, meaning 1,900 non-skipped successes; and
- both used CPython 3.12.13 on Debian 13/Linux 6.12.96 x86_64 with the three
  pinned requirement files. The accepted fresh venv used pip 26.2.1; the
  independent pinned venv used pip 26.2. Requirement SHA-256 values were
  `b584915d...`, `5d17b64f...`, and `42b5a0df...` respectively.

The 14-test delta is exactly executable discovery. A Python child of the
PowerShell runner resolves `/tmp/hass-mcp-pr70-pwsh/pwsh`; the direct shell
environment resolves neither `pwsh` nor `powershell`. The two skips common to
both environments are:

- `PrEvidenceTests.test_windows_path_case_for_same_repository_is_accepted` —
  Windows path-casing behavior; and
- `AddonMetadataValidationTests.test_staged_release_allows_feature_pr_to_keep_advertised_version` —
  the staged declaration is consumed only on the promoted release commit.

The direct-shell-only skips, all with reason `PowerShell is unavailable`, are:

- `CheckScriptExecutionTests.test_evidence_accepts_and_records_an_exact_authorized_protected_path`;
- `CheckScriptExecutionTests.test_evidence_accepts_and_records_multiple_authorized_paths`;
- `CheckScriptExecutionTests.test_evidence_fails_if_the_worktree_changes_during_validation`;
- `CheckScriptExecutionTests.test_evidence_rejects_a_dirty_worktree_before_repository_code`;
- `CheckScriptExecutionTests.test_fast_executes_the_tiny_fixture_suite`;
- `CheckScriptExecutionTests.test_full_accepts_an_authorized_protected_directory_scope`;
- `CheckScriptExecutionTests.test_full_secret_scan_reads_a_safe_tracked_dotfile`;
- `CheckScriptExecutionTests.test_full_secret_scan_rejects_a_tracked_dotfile_with_a_secret`;
- `CheckScriptExecutionTests.test_invalid_authorization_syntax_is_rejected_before_repository_code`;
- `CheckScriptExecutionTests.test_invalid_base_fails_safely_on_every_tier`;
- `CheckScriptExecutionTests.test_native_failure_exits_nonzero_and_overwrites_passing_evidence`;
- `CheckScriptExecutionTests.test_nonmatching_authorization_is_rejected_as_unused`;
- `CheckScriptExecutionTests.test_protected_path_is_rejected_before_repository_code_on_every_tier`; and
- `PowerShellValidationTests.test_all_repository_powershell_parses`.

Thus the reports are reconcilable but not equivalent. The accepted document's
phrase “1,916 passed with two skips” was colloquial; the underlying artifact
correctly recorded 1,916 discovered and two skipped.

## Carried observations and retained limitations

- O-1: the first later production canary action must be plan-only and verify
  `target_class=upstream_ha_mcp_addon`, `binding=bound`, and
  `installed_version == initialized_server_version == 8.1.0`. Beta 22 does
  not run or authorize that canary.
- O-2: the permanent canonical evidence at
  `docs/evidence/upstream-read-compatibility/ha-set-integration-8.0.0-to-8.1.0-input-schema.json`
  proves one genuine description-only change at
  `/properties/config/description`. The 8.1.0 registry hash is already exact,
  and `ha_set_integration` remains `persistent_write`; no admission change is
  warranted.
- O-3: upstream's DNS-rebinding guard remains default-disabled. Engineering
  retains the reviewed loopback/Supervisor endpoint and exact identity checks,
  but does not claim that upstream setting as an active defense. This is a
  known retained threat acceptance; changing it requires separate compatibility
  and deployment review.

Other retained limitations are the explicit 128 KiB-per-operation product
boundary, the requirement to recreate unbound historical plans, no dashboard
execution, no arbitrary Home Assistant service execution, no held-tool
promotion, and no wildcard upstream trust.

## Artifact hashes and remaining gates

```text
d921f25290f4d6b8be893aeebbff5a8e506243c9cac2916148cda2de07bee37b  hass_mcp_engineering_beta/ha_mcp_engineering/governance/semantic_projection.py
44c56cdebf28c7f0b72570b81873944ddb7b9c1aacdc42f94b0a405ddac6c3c0  hass_mcp_engineering_beta/ha_mcp_engineering/governance/service.py
a5b0ef5b169853a6c730b2461ea6fff7bc35678dd4100676b34f274f2237099e  hass_mcp_engineering_beta/ha_mcp_engineering/approval_web.py
2a8db27e08c8847cdd95e9e30e8ace46d0e6fa4b5c64f228e220a85f751344c1  docs/evidence/upstream-read-compatibility/ha-set-integration-8.0.0-to-8.1.0-input-schema.json
163582e160398892ef8541e9f0c7e97de2b4e8c25301dcc867e0067a8a617035  hass_mcp_engineering_beta/ha_mcp_engineering/upstream_release_registry.json
```

Local Docker access is unavailable, so no local image or architecture result
is claimed. Existing CI remains authoritative for package builds, declared
amd64/arm64/arm-v7 no-push builds, exact-image matrices, immutable add-on
acceptance, lifecycle acceptance, disposable Home Assistant contracts, and
Ingress/image acceptance. These are intentionally remaining gates until the
stacked draft PR's exact-head workflows complete.

Additional deterministic and dependency commands completed before the clean
Evidence run:

```text
/tmp/hass-mcp-beta15-final-venv/bin/python scripts/review_upstream_read_release.py validate
/tmp/hass-mcp-beta15-final-venv/bin/python scripts/review_upstream_read_release.py generate --output /tmp/beta22-restack-registry-one.json
/tmp/hass-mcp-beta15-final-venv/bin/python scripts/review_upstream_read_release.py generate --output /tmp/beta22-restack-registry-two.json
cmp /tmp/beta22-restack-registry-one.json /tmp/beta22-restack-registry-two.json
cmp /tmp/beta22-restack-registry-one.json hass_mcp_engineering_beta/ha_mcp_engineering/upstream_release_registry.json
# byte-identical; SHA-256 163582e160398892ef8541e9f0c7e97de2b4e8c25301dcc867e0067a8a617035

/tmp/hass-mcp-beta15-final-venv/bin/python -m pip check
# No broken requirements found.

/tmp/ha-mcp-8.1-validation.GAy2fC/venv/bin/python -m pip_audit --strict --progress-spinner off --local
# No known vulnerabilities found; complete installed environment including transitives.

/tmp/ha-mcp-8.1-validation.GAy2fC/venv/bin/python -m pip_audit --strict --progress-spinner off --no-deps --disable-pip --requirement hass_mcp_engineering_beta/requirements.txt
# No known vulnerabilities found; exact seven direct pins.
```

No tag, release, image publication, deployment, add-on update, restart,
production canary, Home Assistant restart, or production mutation is
authorized by this acceptance record.
