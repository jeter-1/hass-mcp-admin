# Engineering 2.4.0-beta.16 acceptance

This defines acceptance for the garage-only Engineering increment; it is not an
executed receipt or authority to deploy or change household configuration.
Preparation base: `740a4392d1aafa61e5d482a8ddc329d69c0b1839` (beta.15).
Implementation input: `bcc75d26f2e5b388c0c2599baffe8c6336fc0309`.
Bind final validation, review and publication to the actual materialized candidate
and protected merge tree; neither a prospective merge SHA nor image digest may
be invented. Govee and HAMCP-151 are outside this release.

## 1. Source, review and compatibility

Require all three authoritative version declarations to equal 2.4.0-beta.16,
no unconsumed `.release/next-version`, and exact active acceptance/release-notes
resolution from `scripts/codex-context.py`. Require clean-head Full/Evidence
15/15 and complete exact-candidate CI `validate`, including every required job
family. Record actual counts and individual skip reasons; a cancelled, missing or
failed required job is not acceptance. Retained implementation results are not
substitutes for the final materialized candidate checks.

Bind the independent implementation approval to its exact garage head, original
review receipt and evidence manifest. The author reports 4,725 tests run,
24 skipped, zero failures/errors; the user-supplied independent approval reports
15 candidate tests and three extra probes. These numbers are reconciliation
inputs, not assertions of a new campaign. Verify the underlying receipts before
closing the source gate. Obtain a focused independent review of the
implementation-to-release delta; do not reopen an unchanged implementation
review merely because version metadata changed.

Compare the 12 implementation files byte-for-byte with the reviewed input or
explicitly review any actual delta. Preserve the preceding **83 complete tool
descriptors (58 native + 25 delegated)**, all **21 compiled Core profiles** and
fingerprints, signed journals/trust roots, provider routes/admission, durable
formats, dependencies, workflows/permissions and frozen stable v1.1.2. There is
no new capability to sign. Inspect the exact preparation/publication workflow;
a draft PR or local candidate does not itself authorize publication or deployment.

Required source coverage includes useful success for both exact transformations;
unchanged historical policy; complete guard, mode, target, response, dependency
and before/after identity binding; structural limits; refusal of tampering,
unavailable F3, stale state, absent/invalid approval and changed provider authority;
zero legacy fallback; every bundle prefix and final-bundle drift; lost-response
and duplicate/recovery behavior; local refusal followed by unrelated useful
execution; and truthful unavailable/partial inverse handling. Preserve the
existing F3 readiness, durable-write, lock, approval and recovery tests.

## 2. Exact Core evidence

Retain the assembled disposable Core 2026.9.4 evidence for the exact unchanged
garage implementation and sanitized fixtures. The existing image is
`ghcr.io/home-assistant/home-assistant@sha256:e47c978e1b801466e7f62f612fd552bc3a228e077b31a3f1c22c05cf63d754da`,
with Python 3.14.6. Verify source, fixture/harness hashes, raw results, command
boundaries and cleanup; 296 executed checks include 289 distinct cases and
seven repeated entry controls. Reuse valid exact-input evidence; repeat affected
cases only if a semantic input changes or a material evidence gap is found.

The evidence must distinguish real Core schema/condition/template/stop-response,
wait/reload and concurrency execution from fake cover/notification/log services
and any deliberately aged state durations. Preserve both Cleaner call paths,
the complete 30-second script wait and 300-second Cleaner wait cases, conditions
and response errors, unavailable prerequisites, close-before/during-wait,
close-then-reopen, policy rechecks, cancellation/busy behavior and reload-prefix
observations. Selected source-function probes or prepared recipes do not replace
assembled Core execution. This proves neither installed household behavior nor
a global physical retry limit. It adds no ad hoc Core-version admission pin.

## 3. Publication and installed identity

After separately authorized publication, bind the version tag and protected
source/tree to the successful publication run, clean build time, OCI index,
both declared architecture manifests/configs, provenance, SBOM and locked build
inputs. Verify actual immutable bytes and subjects. Require the running container
configuration digest to bind through its architecture manifest to that index;
runtime version/source labels alone are insufficient. Use the existing bounded
operator receipt route, with owner-private inputs and no unrestricted container
configuration export. Do not rerun a completed collection without reconciling
the retained receipt first.

Before a separately authorized deployment, record the prior known-good artifact,
current storage health and the approved recovery procedure. Preserve all durable
governance records. The absence of a durable-format change does not prove that
beta.15 can execute/recover a new marked plan; do not downgrade across unresolved
work or treat an image downgrade as household configuration restoration.

## 4. Installed read-only smoke and continuity

Only after deployment and authorization for this read-only acceptance:

1. Record beta.16's exact source/build/clean flag and installed image proof.
   Establish current Core identity, accepted/current signed applicability and
   all profiles required by the installed capabilities; record the journal
   sequence and authority generation rather than copying historical values.
   Require exact admitted ha-mcp 8.5.0, healthy F3 execution readiness, storage
   and audit, and zero fallback. A different installed pairing requires its own
   reviewed compatibility evidence; do not refresh/sign solely for this release.
2. Capture a fresh separate public MCP session containing initialize,
   notifications/initialized and all tools/list pages, with zero tools/call.
   Compare all 83 descriptors to exact beta.16 source and their preservation
   against beta.15, not just the count. Keep before/after server_info and
   get_server_health(check_ha=false) brackets in separate sessions. Endpoint and
   optional token remain private terminal inputs. Preserve failures/drift and
   finite capture limits; no retry loop or fallback.
3. Use one useful native read and one admitted delegated read of an already
   selected non-action target. Read one retained successful plan/task without
   replay or reverification. Preserve provider attribution, request IDs and
   original dispatch history. Do not repeat the completed dashboard, Alarmo,
   logbook or automation-baseline campaigns unless a concrete regression arises.
4. Retain a dated render-only owner observation of the existing approval panel;
   create and approve nothing for this gate.
5. Compare final no-probe settlement with the baseline: authority/admission,
   readiness, pending approvals, plans/tasks/events, nonterminal work, locks,
   holds, recovery, audit/storage failures and fallback. Attribute concurrent
   authorized household activity rather than inventing a no-write claim from
   unchanged counters alone. Stop on unexplained dispatch, authority drift,
   provider loss, persistent fault or unresolved counter changes. Preserve
   timings as observations, not a newly imposed latency SLA.

No household plan, challenge, apply, script invocation, cover movement, forced
failure, restart/reload, lock clearing or historical-operation replay belongs to
this read-only smoke. A manufactured garage canary is not required to accept the
installed Engineering baseline. Its useful action semantics are proved by the
source and disposable gates above.

## 5. Separate household application and D2

Installing beta.16 does not close HAFA-F010/F011 or satisfy D2. Before any live
proposal/application, the remediation owner must independently close the
operational recovery gate, including the non-atomic five-write sequence, old
in-flight runs/reload evidence and the prohibited original-script inverse.
Caller-only restoration is partial, requires fresh approval and cannot be
presented as full recovery. There is no automatic rollback.

Only a separately scoped and approved household task may freshly read complete
configurations and caller mappings, choose the exact intended transformation,
create a fresh plan, obtain its external approval and apply it under current
authority. Do not reuse the historical prohibited plans/approvals or confuse
the minimal unconditional-close/no-response transformation with the full
caller-owned retry design. An eligibility marker alone is not proof or authority.

That task must verify every saved member, prefix/final evidence, partial outcomes
and normal runtime behavior; saved bytes do not prove physical success. Any
physical failure/retry test needs its own consequence/recovery scope. Prefer
authorized natural observations and do not manufacture a failed close or unsafe
sensor condition as release acceptance. The full design retains one possible
retry per caller run, Cleaner restart mode and no global attempt budget.

## Verdicts and stop conditions

Report **Engineering beta.16 source/release acceptance**, **installed read-only
acceptance**, and **household garage application/behavior** separately. Installed
acceptance may PASS only after gates 1–4 have complete matching evidence; a gate
not yet run remains NOT RUN/INCOMPLETE and an observed failure remains FAIL.
Keep household application NOT RUN/BLOCKED BY D2 until separately cleared;
never convert Engineering acceptance into a household remediation claim.
Review, CI and publication checks do not create live authority. This document
authorizes no push, Ready action, merge, publication, deployment or HA operation.
