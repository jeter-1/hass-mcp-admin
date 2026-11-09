# Engineering 2.4.2-beta.7 acceptance

This contract covers the 2.5.0 build, released as 2.4.2-beta.7. It follows
2.4.2-beta.6 (HAMCP-304, #373). Every item landed through its own reviewed
pull request into the `release/2.5.0` integration branch, and the release
pull request #383 (HAMCP-331) carries the build to `main`.

See also the release notes:
[`V2_4_2_BETA7_RELEASE_NOTES.md`](V2_4_2_BETA7_RELEASE_NOTES.md).

**Release target.** `2.4.2-beta.7`, following 2.4.2-beta.6. The release
transition accepts only `2.4.2-beta.6 -> 2.4.2-beta.7` or
`2.4.2-beta.6 -> 2.4.2-rc.1` from the current base
(`scripts/release_transition.py`). It refuses `2.4.2-beta.6 -> 2.5.0-beta.1`.
"2.5.0" names the build, not the version string.

**Why the version is materialized at the branch base.** Every change under
`hass_mcp_engineering_beta/` needs a version transition
(`scripts/validate_addon_metadata.py`), and CI validates against `origin/main`
with a materialized, unstaged release whatever the pull request base
(`.github/workflows/ci.yml`). So 2.4.2-beta.7 is materialized once, in the
first commit on `release/2.5.0`, and item pull requests carry no version
change of their own.

The release is materialized in all three version authorities, with no staged
declaration left. Nothing is published or deployed.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed read-only acceptance.

It authorizes none of the later stages.

## Items

Each item was reviewed by Codex in its own pull request into
`release/2.5.0` and merged at exactly the head its Ready verdict names.

| Item | Pull request | Ready head | Merge commit | Codex Ready verdict (PR comment) |
| --- | --- | --- | --- | --- |
| HAMCP-309 Part 3: Core release order guard | #384 | `be370131` | `cfdd3b39` | 6707218456, retarget 6708754052 |
| HAMCP-327 (folds in HAMCP-301): `control_helper` fails to ask | #386 | `ed3cd6fa` | `9a20c929` | 6708762517 |
| HAMCP-319 Phase 2 design | #385 | `aa8e1582` | `88212703` | 6711391984 |
| HAMCP-288: secret-path containment | #387 | `b1f38fbe` | `abfbfb13` | 6711411766 |
| HAMCP-288: encoded-only route-word refusal | #403 | `49b5cc50` | `12d0fb2e` | 6713154629 |
| HAMCP-319: 9.1.0 runtime | #404 | `24f8394f` | `62da9b6c` | 6713132005; post-merge full review 6713553830, F1 closed by #405 |
| HAMCP-319: #404 F1 correction | #405 | `121129a7` | `62008070` | 6715368567 |
| HAMCP-290: Marketplace inventory read | #409 | `32da4997` | `19caf514` | 6724566361 |
| HAMCP-319: build and exact-image lanes | #406 | `412dafb9` | `657fef21` | 6733207026 |
| HAMCP-319: lanes (into #406's branch) | #407 | `46e554ef` | `72d4048a` | 6729895046 (covers #407 and #408) |
| HAMCP-319: 9.1 lanes (into #407's branch) | #408 | `4ca5450c` | `765a3d43` | 6725224530 (combined) |
| HAMCP-278 / HAMCP-279 | none | n/a | n/a | status only (release notes) |

The version transition `2.4.2-beta.6 -> 2.4.2-beta.7` is materialized once in
the first commit on `release/2.5.0` (`7246f2d2`); no item pull request
changes it.

## 1. Source and offline acceptance

Bind:
- each item's owner decision and scope, as recorded in its design document
  or pull request:
  - HAMCP-319: [`HAMCP_319_PHASE2_DESIGN.md`](HAMCP_319_PHASE2_DESIGN.md),
    including JD-DCL1 (redirects refused on every ha-mcp connection) and the
    section 3.4 correction (catalog order is informational on 9.1.0; the
    lanes compare the full catalog as an order-independent set);
  - HAMCP-290: [`HAMCP_290_MARKETPLACE_READ_DESIGN.md`](HAMCP_290_MARKETPLACE_READ_DESIGN.md);
  - HAMCP-288: #387 and #403 (architect ruling on separator-only paths;
    Josh's encoded-only route-word refinement), the
    [LAN listener finding](evidence/hamcp288/LAN_LISTENER_FINDING.md) and
    the [rotation runbook](evidence/hamcp288/ROTATION_RUNBOOK.md);
  - HAMCP-327 and HAMCP-309 Part 3: #386 and #384;
- each item's Ready verdict to its landed merge commit (Items table);
- `2.4.2-beta.7` materialized, with exact document resolution;
- the final head SHA, its `validate` run and the single Evidence tier log
  named in #383.

The review of each item carried a security section for its provider, write,
workflow or persistence change. No item adds a write, service call, approval
source or fallback path.

Required offline evidence, by item:
- **HAMCP-319:** `tests/test_hamcp319_*.py`, including the endpoint
  selector, no-redirect (transport, catalog, matrix, useful results), device
  projection, dashboard, typed writes, side by side, rollback expectation and
  lane runner suites; the exact-image lane scripts
  `scripts/exact_image_admission_lanes.py` and
  `scripts/exact_image_admission_91_*.py`; mutation evidence under
  `docs/evidence/hamcp319/`.
- **HAMCP-288:** `tests/test_hamcp288_secret_path_containment.py`; mutation
  evidence under `docs/evidence/hamcp288/`.
- **HAMCP-290:** `tests/test_hamcp290_marketplace_inventory.py`; mutation
  evidence under `docs/evidence/hamcp290-mutations/`.
- **HAMCP-327:** `tests/test_hamcp327_fail_to_ask.py`; mutation evidence
  under `docs/evidence/hamcp327/`.
- **HAMCP-309 Part 3:** `tests/test_core_release_evidence.py`.
- **The complete repository suite** in the Evidence tier, run once on the
  final release head.

## 2. Release CI

The exact materialized final head's `validate` check passes, including the
release transition and the exact-image admission lanes (every configuration
`ACCEPTED`).

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow.

## 4. Installed read-only acceptance (separately authorized, after deploy)

This stage runs only after Josh separately authorizes deploying this
release, in the [deploy order](V2_4_2_BETA7_RELEASE_NOTES.md#deploy-order).
Every step is a read. Agents have no live access; Josh or an operator runs
it.

**Stage A: Engineering 2.4.2-beta.7 with the ha-mcp app on 8.5.**

1. **Identity.** `server_info` and `get_server_health` report:
   - `2.4.2-beta.7`;
   - the merged release SHA as build SHA;
   - `dirty: false`.
2. **Production 8.5 URL needs no redirect (JD-DCL1).** `get_server_health`
   shows:
   - `upstream_dashboard.upstream_server_version` is `8.5.0`;
   - `upstream_dashboard.reachable` is true;
   - neither the `upstream_dashboard` nor the `upstream_read_gateway` block
     reports `upstream_redirect_refused` or `upstream_endpoint_rejected`.
3. **Useful results.** One delegated read (for example `ha_get_state` on an
   entity the operator chooses) and one governed dashboard list both return
   useful results.
4. **HAMCP-288.** A `get_incident_logs` read that covers the ha-mcp app's
   own log source reports that source withheld with
   `upstream_secret_path_source_withheld`, and no response in this stage
   contains the configured secret path.
5. **HAMCP-290.** `ha_get_hacs_info` is absent from `tools/list`, and
   `get_marketplace_inventory` refuses with
   `pending_signed_registry_admission` (unsigned until its own signing).
6. **Tool catalog.** A fresh `tools/list` has the 82 Engineering tools and
   no delegated read outside the reviewed 8.5.0 set; with the optional YAML
   reader on, the reviewed 8.5.0 capture is 82 + 28 = 110. Admission is per
   tool, so a smaller compatible subset is possible; record the count.

If step 2 or 3 shows `upstream_redirect_refused`, set
`upstream_dashboard_mcp_url` to the exact URL that answers without a
redirect and restart Engineering, or roll Engineering back to 2.4.2-beta.6.
Do not continue to Stage B.

**Stage B: after Josh updates the app to 9.1 and appends `/catalog/full`.**

7. `get_server_health` shows `upstream_dashboard.upstream_server_version`
   `9.1.0`, reachable, and no `upstream_catalog_mode_unadmitted`,
   `upstream_catalog_selector_invalid`, `upstream_redirect_refused` or
   `upstream_endpoint_rejected`.
8. Step 3 again on 9.1.0.
9. A fresh `tools/list` has 82 Engineering tools and a compatible subset of
   the 27 reviewed 9.1.0 delegated reads (109 with stock policy).

PASS needs every check in a stage. If a step fails, record the response in
HAMCP-331. Whether to roll back is Josh's decision.

## Rollback

Rolling back Engineering to 2.4.2-beta.6 is safe under the conditions in the
[release notes](V2_4_2_BETA7_RELEASE_NOTES.md#rollback):
- if the app is on 9.1.0, roll it back to 8.5.0 and remove `/catalog/full`
  first, because beta.6 refuses 9.1.0;
- beta.6 has no HAMCP-288 containment;
- beta.6 restores `ha_get_hacs_info`; after `core.marketplace_read` is
  signed, follow the HAMCP-290 rollback rules;
- storage formats are unchanged.
