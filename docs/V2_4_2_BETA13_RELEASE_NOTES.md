# Engineering 2.4.2-beta.13 release notes

**Status: released.** Engineering 2.4.2-beta.13 is the 2.9.1 build. It
builds on 2.4.2-beta.12 (the 2.9.0.1 hotfix, HAMCP-470, #488). Its content
landed through item pull requests into the `release/2.9.1` integration
branch, and release pull request #500 carried it to `main`.

The architect's block of 2026-11-23 names 2.4.2-beta.13 for the 2.9.1 build.
The same day, the 2.9.1 scope was re-scoped to audit-driven read fixes taken
from tool usage and a house audit. No other 2.9.0 hotfix is planned.

Acceptance: [`V2_4_2_BETA13_ACCEPTANCE.md`](V2_4_2_BETA13_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

## Current state

The version is materialized on `release/2.9.1` at its catch-up with `main`
(b4dd3da3, 2.4.2-beta.12). Every 2.9.1 item pull request below merged into
`release/2.9.1`. Release pull request #500 carried its tip 34bab9ad to
`main`, merged as f7e03478 on 2026-11-24. It was published the same day: tag
`v2.4.2-beta.13`, image digest
`sha256:bf7f5c1d7ecb71c4824447f38e77c200cb0160e020a07400ea3ed35b262d5af7`
(acceptance, section 3). Josh deployed it, and HA Admin's installed
read-only acceptance passed at 16:52-16:58 CST (acceptance, section 4; as
relayed by the architect). #496 (HAMCP-474 D1) is left open as a draft and
is not part of this build.

## Items

The pull request, merge commit and state of each row were read from GitHub
on 2026-11-24. An item or part that has not landed is not part of this
release, and these notes make no claim about its behavior. The final release
pull request binds each landed item's evidence.

| Item | Pull request | Merge | State | What landed | What did not |
| --- | --- | --- | --- | --- | --- |
| HAMCP-456 | #487 | 584b31da | merged | Version-gate lint: `tests/test_version_gate_lint.py` fails on an exact Core or ha-mcp version literal in the Engineering runtime that is outside `ci/support-window.yaml` and not listed in `ci/version-gate-allowlist.json` | No runtime change (tests only) |
| HAMCP-458 | #486 | c5f160c0 | merged | Release tooling: the release scripts refuse a guessed base and check the test venv, and a readiness sweep is added | No runtime change (tooling only) |
| HAMCP-472 | none | none | moved to native search | Diagnosis only. It did not reproduce: the HA Admin replay's domain-only and query-plus-domain `ha_search` calls returned entities. The source cause was found: a call that passes `search_types` still skips entities on ha-mcp 9.2.0 | No 2.9.1 code either way. The `search_types` path stays until the native search provider (HAMCP-481) ships |
| HAMCP-481 | #491 | e51968a2 | merged, design only | [`HAMCP_481_NATIVE_SEARCH_DESIGN.md`](HAMCP_481_NATIVE_SEARCH_DESIGN.md), revision 4, whose own status is draft for review, not approved: native search with parity to ha-mcp 9.2.0 for entities, automations, scripts, scenes and helpers, including configuration and reference matching with identity-only results; dashboard search and flow-helper options matching stay on delegated sub-calls; partial only with a reason | The build, planned for the next release. No runtime change |
| HAMCP-473 | #492 | 8ed1b99b | merged | `configuration_integrity_analysis` gains `detail_level` `compact` (up to 100 findings a page; 12 allowlisted keys; no explanation, remediation or evidence text; at most three configuration paths, with `configuration_path_count`) and an optional `severities` filter (default all). A narrowed query carries `severity_filter` (`excluded_finding_count`, `excluded_by_severity`, `truncated_selected_count`) on every page, and hidden findings still count toward `final_assessment`. Both inputs are bound to the cursor (`stale_cursor` if either changes mid-walk). A cut page is the largest that fits, found by binary search | The tool's audit record does not record `severities`, and the health counters add a filtered query's filtered counts, not the classified totals (both disclosed in [`CONFIGURATION_INTEGRITY_ANALYSIS.md`](CONFIGURATION_INTEGRITY_ANALYSIS.md)). The 2,000-finding bound is unchanged, and standard output is unchanged with `severities` omitted |
| HAMCP-474 D5, D6 | #497 | 0f52b0e0 | merged | `automation_reliability_batch_analysis` adds `source_limitations` (`source_type`, a closed-set `reason`, `affects_result_status`, `item_count`; the system log is named `retention_unknown` and does not change the result status) and root-cause detail (`confidence`, `current_state`, `representative_configuration_paths`, `recommended_investigation`, `governed_change_required`), with `root_cause_detail` `full`, or `reduced` under the 60,000-character bound | Near the bound a response can hold one automation fewer than before: the 20th becomes `skipped_budget` and is listed in `coverage.remaining_automation_ids`. Finding IDs and evidence references stay with the single-automation tool. D2, D3 and D4 were split to HAMCP-482, HAMCP-483 and HAMCP-484 |
| HAMCP-474 D1 | #496 | none | deferred to HAMCP-487; #496 left open as a draft | Nothing | A deep automation whose configuration path is long is still reported as possibly touching any target. The fix is HAMCP-487 (exact coverage without changing stored prepared-task hashes) |
| HAMCP-475 | #493 | 6adccba0 | merged | `get_audit_log` fits each page to `response_size_limit` without cutting a record, and every response ends with one `{"_page": {"returned", "next_cursor", "complete"}}` line. An optional opaque `cursor` (HMAC-signed with a per-process key) walks older records. A bad cursor is `invalid_cursor`; a changed `event` or a rotated, truncated or rewritten log is `stale_cursor`. Non-UTF-8 rows are counted as `skipped_undecodable`. The tool's audit record stores `cursor_present` only | The rotated `audit.jsonl.1` is not read. Cursors do not survive an add-on restart or upgrade. Redaction is unchanged (at write time) |
| HAMCP-476 | #498 | c6d6b538 | merged | One read-only native tool, `get_automation_run_counts`: 1-10 named automations, or every automation (at most 200, ranked by runs), over a window of 24 hours by default and at most 168, within fixed GET and time budgets. `partial` is set exactly when `partial_reasons` is non-empty. A set whose worst-case reply would not fit the response limit is refused with `response_size_exceeds_bound` before any logbook read | Failed-condition triggers and runs the automation's mode dropped are not counted. A named automation is not checked to exist (it counts 0 with a note). No paging |
| HAMCP-293 | #494 | 99b41cc5 | merged, middle path | An exact automation update plan keeps the legacy level, apply decision, evidence and warnings unchanged, and adds reasons that name what changed: the changed fields, the changed action steps, and each high-risk call instance by service and path as added or changed, moved, removed, or present, unchanged | No risk level is lowered; a provable lowering rule is follow-up HAMCP-486 (after 2.9.1). A create, an update without the current configuration, and other operations keep the legacy reasons only. Stored plans are not re-classified |
| HAMCP-292 | #495 | 010de1ff | merged | The dashboard update plan's review projection keeps strings whole up to 2,000 characters (the largest reviewed input bound) instead of cutting at 512, so a description of 513 to 2,000 characters, or a long patch path, is no longer refused as `plan_contains_prohibited_sensitive_data` | Secret detection is unchanged and runs per field; a secret split across fields is still not detected (pre-existing) |

Not in this build: native lifecycle and identity (Phase 1), native search
(HAMCP-481 build), the restart verification fixes (HAMCP-479, HAMCP-480), the
dashboard rule profile (HAMCP-444), the protocol ADR (HAMCP-384), the Core
mirror refresh (HAMCP-462), HAMCP-474 D1 (HAMCP-487) and the HAMCP-293
lowering rule (HAMCP-486). There is no lifecycle work in this build.

## Evidence

**Passed.** Release pull request #500 binds each landed item's merged head,
merge commit and head CI run (acceptance, section 1). The Evidence tier
passed on the final release head 34bab9ad (base `main` 3d7c1c67),
2026-11-24 14:24-15:22 CST: 18 of 18 steps, 14,100 tests run and 19 skipped.
`validation.json` sha256
`2be12ac51c82b7fcfcd7d6e533211ffa5c8ececc009be0e2fa0144e2da8bbc76`. Release
CI passed on the same head (CI run 45490799988, Policy locks run
45490799860; acceptance, section 2).

## Tool catalog

From the release source: the Engineering tools registered by
`ha_mcp_engineering.tools.registry`, counted at b4dd3da3 and at this docs
update (its runtime is that of 99b41cc5), and the count pins in
`tests/test_rc3_postcore_regressions.py` and
`tests/test_readonly_upstream_gateway.py`.

| | 2.4.2-beta.12 (b4dd3da3) | 2.4.2-beta.13 (99b41cc5) |
| --- | --- | --- |
| Engineering native tools | 86 | 87 |
| Added | none | `get_automation_run_counts` (HAMCP-476, #498) |
| Removed | none | none |

- Delegated tools are unchanged. No landed item changes ha-mcp admission,
  the 9.2.0 registry row or the read-gateway policy.
- Public input schemas change additively, with optional inputs only:
  `configuration_integrity_analysis` gains `severities` and the
  `detail_level` value `compact` (#492), and `get_audit_log` gains `cursor`
  (#493). No input is removed or made required.
- CI-observed lane values for 34bab9ad (CI run 45490799988, the 9.2.0
  exact-image lanes, as recorded in #500): 27 delegated, catalog
  fingerprint
  `0f1a2c75a766a111a83053733c6803ca14d60357137f4ecc7bc7978061982147` (the
  beta.12 value). No lane reports the Engineering native count.
- Live, after the deploy (acceptance, section 4, step 3, as relayed by the
  architect): 115 tools, 87 Engineering and 28 delegated, with the delegated
  catalog fingerprint unchanged. The lane's 27 against 28 live is not
  explained, as in beta.12.

## Rollback

See [the acceptance contract](V2_4_2_BETA13_ACCEPTANCE.md), "Rollback". In
short: restore the pre-deploy backup or the VM 400 snapshot, which returns
2.4.2-beta.12 (tag `v2.4.2-beta.12`, commit b4dd3da3, image digest
`sha256:916f941c052cdbe9516cce8f22ff68e7682754dcccfb3518960d245983e4d634`)
with ha-mcp 9.2.0. Restoring removes `get_automation_run_counts`, the
integrity and audit-log paging, the batch detail and the new risk reasons.
The restore also reverts every Home Assistant change made since the backup.
There is no in-place rollback. Export `get_audit_log` before any restore. No
landed item states a persisted-format change.
