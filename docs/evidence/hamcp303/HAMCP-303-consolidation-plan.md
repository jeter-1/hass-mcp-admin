# HAMCP-303: docs and README consolidation plan (Part 1)

Status: **plan approved; implemented in Part 2 on this branch** (see §5.6). The
text below is the Part 1 plan as reviewed.

## 1. Baseline

| Item | Value |
| --- | --- |
| Base (`origin/main`, fetched 2026-11-03) | `34b8538ba704ded6032f13600ec5b185590100eb`, the merge of PR #354 (HAMCP-253) |
| Expected base in the contract | `1008ef9` (beta.62). Main has since moved: HAMCP-253 merged, so the Part 2 precondition is already met. |
| Branch | `agent/HAMCP-303-docs-consolidation`, created from that base |
| Engineering version | `2.4.1-beta.63` in `config.yaml`, `version.py` and `validate_addon_metadata.py` (`codex-context.py` reports no inconsistencies) |
| Document resolution for beta.63 | `exact`: `docs/V2_4_1_BETA63_RELEASE_NOTES.md`, `docs/V2_4_1_BETA63_ACCEPTANCE.md` |
| Staged release | none (`.release/next-version` absent) |
| Tool counts (`codex-context.py`) | 80 static registered + 28 expected delegated reads = 108 expected connector total (reviewed ha-mcp 8.6.0) |
| Instructions loaded | root `AGENTS.md`, `tests/AGENTS.md`, `.github/workflows/AGENTS.md`, `hass_mcp_engineering_beta/AGENTS.md`, all at the base revision |

## 2. Inventory

The scope is 552 tracked Markdown files under `docs/` plus the root `README.md`.
The full per-file table is in Appendix A. "Inbound" counts the tracked text
files (`.md`, `.py`, `.ps1`, `.yml`, `.json`, `.sh` and others) that mention the
file's basename, excluding the file itself.

| Class | Files | KiB |
| --- | ---: | ---: |
| active-release-authority (beta.63 notes and acceptance) | 2 | 11 |
| current-reference | 45 | 889 |
| current-reference with stale items (`CORE_REGISTRY_ONE_TAP_RUNBOOK.md`, `CORE_RELEASE_REGISTRY.md`) | 2 | 52 |
| current-reference, runbooks (`docs/runbooks/`) | 7 | 17 |
| design or ADR (`docs/architecture/`, `HAMCP_*`, `F2_*`, `F3_*`) | 78 | 1935 |
| evidence (`docs/evidence/`, Core compatibility evidence) | 35 | 648 |
| feature acceptance contracts (Alarmo, baseline capture, auto-readmission) | 3 | 32 |
| historical release companion (canary failure, image bake, beta35 navigation and others) | 8 | 33 |
| historical release record (per-version acceptance and release notes: `V2_*`, `RC*`, `BETA_*`) | 369 | 2427 |
| historical release record with stale items (`V2_4_1_BETA58_ACCEPTANCE.md`) | 1 | 5 |
| stale guidance (`BETA_DEPLOYMENT.md`, `2_1_ROADMAP.md`) | 2 | 63 |
| README (narrates betas) | 1 | 62 |
| **Total** | **553** | **6180** |

### README sections that narrate betas

`README.md` is 1,050 lines. Of those, about 860 narrate releases.

- **Lines 1–859, before `## Install or update Engineering`.** 53 per-release
  paragraphs ("Not yet released: Engineering **2.4.1-beta.63**…", "Historically,
  beta.14…", "Beta.11's…") covering 47 distinct beta versions, newest first.
  Each duplicates that version's release notes.
- **Version-pinned text inside the remaining sections:**
  - `## Install or update Engineering` names "2.4.1-beta.29" (the inbound policy),
    the "2.4.0-beta.13" acceptance checks and image platforms, and the "beta.6
    recovery contract".
  - `## Capabilities and compatibility` states "67 static tools plus 28 delegated
    reads, 95 total" and "25 capability profiles". Current source reports 80 + 28 = 108.
  - `## Governed changes and evidence` carries the 2.3.0-beta.4 downgrade note.
  - `## Development and release` links `docs/2_1_ROADMAP.md` as the "current
    roadmap"; its "Current direction" is dated 2026-10-02 (beta.14).
  - `## Historical references` refers to "the current 2.4.0-beta.13 contract".

## 3. Everything that resolves or links docs by exact path

### 3.1 Release-document resolver and validators

| Location | Behavior |
| --- | --- |
| `scripts/codex-context.py:517-596` `resolve_documents` | Builds exactly `docs/<STEM>_RELEASE_NOTES.md` and `docs/<STEM>_ACCEPTANCE.md`, flat in `docs/`. Stems: stable `V<core>`, beta `V<core>_BETA<n>`, dev RC `RC<n>DEV<m>`, final RC `V<core>_RC<n>` **if any `docs/V<core>_RC<n>_*.md` exists**, otherwise legacy `RC<n>` (`:545-555`). |
| `scripts/codex-context.py:418-454` `document_matches_version` | Requires the exact version string in the body and in the first `# ` heading, and refuses if the first 12 lines contain `historical only`, `immutable historical release`, `not (active) authority`, `cannot authorize` or `does not authorize`. |
| `scripts/codex-context.py:457-514` `historical_documents` | Globs `docs/RC*_*.md` (flat) for informational historical references. |
| `scripts/codex-context.py:711-720` | If `README.md` contains "development stage is now hardened as `X`", X must equal the Engineering version. The phrase is absent today. |
| `scripts/codex-context.py:722-731` | Reports the `documents.*` unknowns ("an exact version-matched file under docs/"). |
| `scripts/promote_next_release.py:103-131` | `validate_document_authority` and `staged_document_resolution` load `codex-context.resolve_documents`; the resolver is the single authority. `--resolve-release-notes` prints the path. |
| `scripts/release_transition.py:48-120` | Version-transition rules; no document paths. |
| `scripts/validate_promotion_candidate.py:16-29` | Runs the release-coupled tests (beta25, F3 invariants, rc1 publication ordering); no doc paths. |
| `.github/workflows/publish-rc-image.yml:481-506` | `--validate-transition`, `--validate-authority`, then `--resolve-release-notes`; the path must match `^docs/[A-Za-z0-9._/-]+_RELEASE_NOTES\.md$` and exist. It feeds the GitHub Release body (`:1265`, `:1541-1564`). |

### 3.2 Tests that assert doc paths or content

91 `ROOT / "docs" / ...` references across tests. Real-repository paths to
historical records:

- `tests/test_codex_workflow.py:1894-2130`: `CODEX_WORKFLOW.md`, `RC2DEV12`–`RC2DEV16` notes and acceptance.
- `tests/test_codex_workflow.py:1919`: README must contain `docs/CODEX_WORKFLOW.md`.
- `tests/test_beta_v2.py:559-566`: `RC2`, `RC3A`, `RC1DEV1`, `RC1DEV2` notes and acceptance.
- `tests/test_rc2dev8_prevalidation_enforcement.py:604-605`.
- `tests/test_deployment_metadata.py:867-896`: `V2_0_1` notes and acceptance content.
- `tests/test_rc2_release.py:19-20`.
- `tests/test_beta25_ha_search_promotion.py:96-99`.
- `tests/test_beta27_*:102`, `beta28:129`, `beta29:114`, `beta30:603`, `beta31:56`, `beta32:62`, `beta33:577`, `beta35:51,250,264`, `beta36:78-81`, `beta50…:1536`, `beta52:12`, `beta53:26-47`, `beta54:77-78,2088-2092`, `beta58_release_materialization_truth:14-15`.
- `tests/test_hamcp071_scene_configuration.py:449-451`: `V2_4_1_BETA53` docs and the CHANGELOG.
- `tests/test_hamcp191_shared_sanitizer.py:1211-1218`: the README and `V2_4_1_BETA20` docs, in the secret scan.
- `tests/test_hamcp278_core_registry_publication.py:59-61,1055-1097`: runbook, registry doc and beta58 acceptance **content**:
  - the runbook step-0 list must contain, in order, "1. sequence 16 is live on `main`" … item 4;
  - the beta58 `## 4. Owner cutover` item 6 text;
  - the registry doc's "Canonical source…" section strings;
  - PUBLISHED, UNCHANGED and UNCERTAIN in both documents.
- `tests/test_core_release_evidence.py:187-195`: the registry doc and the runbook must keep the permanent-provenance sentences and the OWNER SETUP strings.
- `tests/test_rc1_publication.py`, `tests/test_rc1_release_transition.py` and `tests/test_codex_workflow.py:675-690` write **synthetic** `docs/V2_2_0_RC1_*` and `docs/V2_4_1_BETA1_*` files into disposable fixture repositories. They are unaffected by the real layout but encode the flat convention.
- `scripts/check.ps1:649`: a change to `README.md`, an `AGENTS.md` or `docs/CODEX_WORKFLOW.md` maps to `tests.test_codex_workflow.InstructionFileTests`.

### 3.3 CI workflows

- `.github/workflows/publish-rc-image.yml:503`: the release-notes path regex above.
- Other workflow doc references are evidence and registry artifacts only: `core-release-registry-publish.yml:146,254,335,420` (`docs/evidence/hamcp222/core-public-key.txt`), `prepare-ha-mcp-release-registry-update.yml:441-563` and `prepare-upstream-compatibility-attestation.yml:328-425` (`docs/evidence/...json`, `docs/generated/*_INDEX.md`), and `ci.yml:909-915`.
- None of them touches historical release records or the README.

### 3.4 Hash-bound and evidence path records

- `upstream-trust/evidence/ha-core-2026.9.2-a497d25b….json:350-351` and
  `ha-core-2026.9.3-c175df88….json:445-446` record `source_hashes` for
  `docs/CORE_RELEASE_REGISTRY.md`, `docs/V2_2_0_RC9_ACCEPTANCE.md` and
  `docs/V2_3_0_BETA4_ACCEPTANCE.md`. These files are content-addressed (the
  filename carries the sha256) and are also on the frozen `registry` branch.
  The two acceptance docs still match their recorded hashes. Nothing
  re-verifies `source_hashes` today.
- `docs/evidence/core-2026.9.3-source-review.json:28` and
  `docs/evidence/dashboard-analysis-source-review.json:497` record doc paths.

### 3.5 Cross-links and instruction guidance

- 127 relative links from current docs and the README point into historical records.
- **184 historical records contain 371 relative links of their own**, for
  example `V2_4_1_BETA58_ACCEPTANCE.md` → `V2_4_1_BETA58_RELEASE_NOTES.md` and
  `CORE_RELEASE_REGISTRY.md`.
- 0 broken relative links today. 7 false positives are code spans parsed as
  links; Part 2 uses a stricter checker.
- `AGENTS.md:39,48,58,189,263` link `docs/CODEX_WORKFLOW.md` and the ADRs.
- `docs/CODEX_WORKFLOW.md:1167-1200` describes the "exact acceptance document"
  states without stating a path layout.
- `.release/README.md` links `../docs/CODEX_WORKFLOW.md`.
- `hass_mcp_engineering_beta/README.md` (the add-on store page) links
  `../docs/*` and is itself stale: it is headed by 2.4.0-beta.11 and states 81 tools.

## 4. Validator transition answer: beta.63 → rc.1

**Permitted.** Checked from source and by execution:

```
$ python scripts/promote_next_release.py --validate-transition 2.4.1-beta.63 2.4.1-rc.1
Validated exact Engineering release sequence 2.4.1-beta.63 -> 2.4.1-rc.1.
```

`release_transition.py:55-71` lets a beta advance to the next beta or to `rc.1`
of the same core. From beta.63, `2.4.1-rc.2` and `2.4.1` are refused (both
executed). From `rc.1`, the next permitted transitions are `rc.2`, stable
`2.4.1`, or `2.4.2-beta.1`.

Today `resolve_documents(…, "2.4.1-rc.1")` returns `missing`. It falls back to
the legacy `RC1` stem, and `docs/RC1_*.md` do not name 2.4.1-rc.1.

What an rc.1 promotion PR must contain, from source:

1. While authoring, `.release/next-version` containing exactly `2.4.1-rc.1` and
   a newline. It is consumed by `promote_next_release.py --apply` in the same
   PR, and the merged diff must not contain it (`publish-rc-image.yml:496-499`).
2. All three version authorities updated to `2.4.1-rc.1`:
   - `hass_mcp_engineering_beta/config.yaml` `version:`;
   - `hass_mcp_engineering_beta/ha_mcp_engineering/version.py` `SERVER_VERSION`;
   - `scripts/validate_addon_metadata.py` `BETA_VERSION`.

   The publish workflow requires all three in the diff (`:486-494`). The first
   two are protected paths and need explicit authorization in HAMCP-302.
3. `docs/V2_4_1_RC1_ACCEPTANCE.md` and `docs/V2_4_1_RC1_RELEASE_NOTES.md`,
   **flat in `docs/`**. Each must:
   - carry `2.4.1-rc.1` in the first `# ` heading and the body;
   - avoid the refusal phrases in its first 12 lines.

   Creating any `docs/V2_4_1_RC1_*.md` switches the stem from legacy `RC1` to
   `V2_4_1_RC1`.
4. `promote_next_release.py --validate-authority 2.4.1-rc.1` and
   `validate_promotion_candidate.py --require-materialized` (the release-coupled
   tests) pass. Exact-head Evidence and the `validate` CI check are also required.
5. By convention, not enforcement: a `hass_mcp_engineering_beta/CHANGELOG.md`
   entry and the README current-release paragraph.

## 5. Proposed plan

### 5.1 Layout choice: keep every file in place (no moves)

Recommended: **zero moves.** Add one release index and one docs map, and
rewrite the README. A `docs/releases/<version>/` layout was evaluated and
rejected:

- **Resolver.** Active and staged documents must stay flat in `docs/`
  (`codex-context.py:556-560`, `publish-rc-image.yml:503`). A split layout
  (current flat, history nested) would need every release PR to move its
  predecessor's documents, a new recurring step the validators do not know about.
- **Byte-identity versus links.** The 184 historical records with 371 internal
  relative links would break if moved byte-identically. Fixing them means
  editing evidence. Both outcomes violate the objective.
- **Tests.** About 60 real-repository test path references would need updating.
- **Hash-bound evidence.** The signed-registry evidence files on `main` and the
  frozen `registry` branch, plus two `docs/evidence` JSON files, record flat
  paths. They cannot be edited.
- **External links.** PR bodies, register notes, GitHub Releases and handoffs
  link `blob/main/docs/V2_…` paths.

New files:

| New file | Content |
| --- | --- |
| `docs/RELEASES.md` | **The release index.** One table, newest first: version, release-notes link, acceptance link, companion docs (canary failure, image bake, beta35 navigation and others), plus a pointer to GitHub Releases. Each version is derived from the document's own first heading, not inferred from the filename; unresolvable legacy names (for example `BETA_17_RELEASE_NOTES.md`) are listed under "legacy naming" with the heading text verbatim. It links the pre-consolidation README by permalink (`blob/34b8538b/README.md`) so the old narration stays one click away. A short "Adding a release" note: add one row; do not prepend narration to the README. |
| `docs/README.md` | **Docs map.** It renders when browsing `docs/` on GitHub. Sections: current reference, runbooks, architecture and ADRs, design records, evidence, release history (→ `RELEASES.md`). No per-file history. |

Both names are safe for the resolver: neither matches `RC*_*.md` nor
`V<core>_RC<n>_*.md`, and neither carries a version heading. Historical files:
all 369 + 1 records and 8 companions are **kept in place, byte-identical**.
Move count: **0**.

### 5.2 README outline (about 180–220 lines, from 1,050)

1. **HA MCP Engineering Server**: what it is. One client-facing MCP endpoint
   through Nabu Casa. Reviewed ha-mcp tools are internal delegated providers,
   and native capabilities supply the deeper semantics.
2. **Current release**: one paragraph naming the current version, linking its
   exact notes and acceptance documents and `docs/RELEASES.md`. This is the only
   README text a release PR changes. The resolver phrase "development stage is
   now hardened as" is avoided.
3. **Install and update**:
   - add-on repository URL, **HA MCP Engineering Server Beta** slug, ports
     8100 / 8110 ingress;
   - inbound Host/Origin options (→ `INBOUND_SECURITY.md`);
   - 64-bit platforms; controlled build (→ `BUILD_INPUTS.md`).

   Version pins are removed.
4. **Using it**:
   - connecting a client;
   - typical reads and analyses (→ `READ_COVERAGE_MATRIX.md`, `FEATURE_READINESS_MAP.md`);
   - ordinary fan, light and switch requests (current `control_fan` /
     `control_power` text, without version pins).
5. **Capabilities and compatibility**:
   - tool counts taken from `codex-context.py` at implementation time
     (today 80 + 28 = 108), stated as source expectations, not installed proof;
   - admitted ha-mcp versions from source;
   - signed Core registry (→ `CORE_RELEASE_REGISTRY.md`).
6. **Safety model**:
   - fail-closed routing and provider admission;
   - no fallback or forwarding;
   - bounded output, redaction and audit;
   - signed Core authority;
   - technical refusal versus disclosed consequence (ADR-022).
7. **Approving changes**:
   - the plan → approve in the **HA MCP Approval** ingress panel → apply once
     → inspect → read back and verify cycle;
   - reconciliation after timeouts;
   - stuck-records acknowledgement;
   - links to `CHANGE_GOVERNANCE.md`, `EXTERNAL_APPROVAL.md`,
     `GOVERNED_EXECUTION_RECOVERY.md`, `AUDIT_LOG.md`.
8. **Recovery and rollback**: `docs/runbooks/` and downgrade-versus-restore.
9. **Development and release**:
   - `docs/CODEX_WORKFLOW.md` (required by `test_codex_workflow.py:1919`);
   - `codex-context.py`;
   - one-PR release materialization.
10. **History**:
    - `docs/RELEASES.md`, `docs/README.md` and GitHub Releases;
    - stable v1.1.2 retirement (→ `hass_mcp_admin/README.md`);
    - `V2_BETA_ARCHITECTURE.md`.

### 5.3 Stale-guidance corrections

Evidence for the cutover, read-only:

- `origin/registry` = `f95a8c1a315a098690042e5b86c3ac2d4a88055d`, committed
  2026-11-03T17:29:48-06:00, "Create Core registry branch from main c731ad8b".
- The journal sha256 is `a4dfbb143ce6cd932b91bf49c4b3d0fe9b889f690a27188c547c4700e8b62dc4`
  on both `registry` and `main`.
- The HAMCP-278 register row records "CUTOVER DONE 2026-11-03 on Core 2026.10.3
  / seq 15 ([DECISION] Josh)", with seq 16 (Core 2026.11.0) next under HAMCP-280.

| File | Proposed edit |
| --- | --- |
| `docs/CORE_REGISTRY_ONE_TAP_RUNBOOK.md` (current reference) | **Option A, recommended:** a status block under the title: "Cutover executed 2026-11-03 at **sequence 15** (Core 2026.10.3), not 16 as written below: `registry` = `f95a8c1a` from main `c731ad8b`, journal `a4dfbb14…`. Steps 0–6 are the executed record; do not re-run step 2. The next publication is **sequence 16 (Core 2026.11.0) on `registry`** via steps 7–10 and the HAMCP-279 automated input (HAMCP-280)." Short inline "(executed at sequence 15; see status)" notes after step-0 item 1, the step-1 bullet and the step-6 "(sequence 16)". The asserted strings are kept, so **no test change**. **Option B:** rewrite steps 0, 1, 2 and 6 to sequence 15. That breaks `test_hamcp278_core_registry_publication.py:1063-1068` and needs a named test edit. |
| `docs/CORE_RELEASE_REGISTRY.md:480-483` | Add a dated note after the paragraph: "Superseded 2026-11-03: the cutover used sequence 15; sequence 16 (Core 2026.11.0) will be the first publication on `registry` (HAMCP-280). The text above is preserved as written for HAMCP-278." The original lines are unchanged. |
| `docs/V2_4_1_BETA58_ACCEPTANCE.md:29-31` (historical record) | Add one note line directly after the bullet, in section 1, outside the `## 4. Owner cutover` region that the tests assert: "> Note (HAMCP-303, 2026-11-03): the 'sequence 16 / manual flow' non-goal is superseded; the cutover ran at sequence 15 and sequence 16 publishes on `registry`. The contract text is unchanged." The beta58 acceptance is not the active authority, so this cannot affect resolution. Its blob changes; Part 2 records the before and after hashes. |
| `docs/BETA_DEPLOYMENT.md` | **Recommended: retire behind the index.** Prepend a banner ("Historical: superseded by README §Install and update; not current guidance"). Remove the README link, list the file only in `RELEASES.md` history and `docs/README.md`, and leave the body unchanged. Its inbound links from `HAMCP_129…`, `HAMCP_196…`, `RC1_*` and `V2_3_0_ACCEPTANCE` stay valid. |
| `docs/2_1_ROADMAP.md` (proposed addition, not in the contract) | Its "Current direction" is dated 2026-10-02 and the README calls it the "current roadmap". Prepend a historical banner (pointing to the register as the live tracker) and drop the "current roadmap" README wording. Needs approval. |

### 5.4 Part 2 file list (if approved as recommended)

- **New:** `docs/RELEASES.md`, `docs/README.md`.
- **Rewritten:** `README.md`.
- **Notes added, original text unchanged:**
  - `docs/CORE_REGISTRY_ONE_TAP_RUNBOOK.md`;
  - `docs/CORE_RELEASE_REGISTRY.md`;
  - `docs/V2_4_1_BETA58_ACCEPTANCE.md`;
  - `docs/BETA_DEPLOYMENT.md`;
  - optionally `docs/2_1_ROADMAP.md`.
- **Not touched:**
  - runtime, schemas, tools, workflows, versions;
  - protected paths;
  - `.release/`;
  - `hass_mcp_admin/`;
  - every other historical record and evidence file.
- **Tests:** none under Option A. Optional (see 5.5): one new
  `tests/test_docs_index.py`.
- **Validation:** an ad-hoc relative-link checker (kept in `.artifacts/`, or in
  the optional test), `codex-context.py` exact for beta.63, the focused tests
  listed in §3.2, then Evidence once as the contract specifies.

### 5.5 Decisions needed (pending Josh; none is recorded as decided)

1. Layout: keep in place with an index (recommended), or move.
2. Runbook: Option A annotations, no test change (recommended), or Option B rewrite plus a named test edit.
3. Also refresh `hass_mcp_engineering_beta/README.md`, the add-on store page
   headed by 2.4.0-beta.11 and stating 81 tools, to a short pointer at the root
   README? Recommended, but it widens scope.
4. Include the `2_1_ROADMAP.md` banner? Recommended.
5. Add `tests/test_docs_index.py`? Recommended, so the index cannot silently
   rot. It would check:
   - every `docs/*_{ACCEPTANCE,RELEASE_NOTES}.md` appears in `RELEASES.md`;
   - no relative link in `docs/**` or the README is broken;
   - `RELEASES.md` and `docs/README.md` never match a resolver stem.
6. Record the "update one README paragraph plus one index row" release
   convention in `docs/CODEX_WORKFLOW.md` (about 3 lines)? Optional. It is an
   instruction-file change and triggers `InstructionFileTests`.

### 5.6 Decisions (Josh, 2026-11-03, in session)

Josh approved the plan for Part 2:

1. Layout: keep every file in place and add the index (`docs/RELEASES.md`, `docs/README.md`).
2. Runbook: Option A, annotations only, with no test change.
3. Refresh `hass_mcp_engineering_beta/README.md` to a short pointer at the root README.
4. Add the `2_1_ROADMAP.md` historical banner.
5. Add `tests/test_docs_index.py`.
6. Add the release-convention note to `docs/CODEX_WORKFLOW.md`.

He also asked for a dated note in `docs/V2_4_1_BETA62_ACCEPTANCE.md`, below
line 12 and with the original text unchanged, stating that its HAMCP-278
deploy freeze ended with the 2026-11-03 sequence-15 cutover. HAMCP-253 had
merged (`main` `34b8538b`), so Part 2 proceeded on the same base.

Item 3 is deferred (Josh, 2026-11-03, in session). `scripts/validate_addon_metadata.py`
treats any change under `hass_mcp_engineering_beta/` except `AGENTS.md` as an
add-on change that requires a version bump, so CI refused the refreshed add-on
README. That file is not copied into the image. It is left byte-identical
here, and the refresh moves to the HAMCP-302 2.4.1-rc.1 PR, which bumps the
version anyway. The prepared text is in commit `72c670d0` on this branch.

## 6. Risk list

| Risk | Mitigation |
| --- | --- |
| A new file named `docs/V2_4_1_RC1_*.md`, or anything matching `docs/RC*_*.md`, changes rc.1 stem selection or historical references | The index and map are named `RELEASES.md` and `README.md`; the optional test asserts this. |
| Banner text in an active or staged document's first 12 lines ("not authority", "historical only") flips resolution to `missing` | No banner is added to the beta.63 docs. Banners go only on non-release documents (`BETA_DEPLOYMENT`, roadmap). The beta58 note sits on line 32 or later, and beta58 is not active. |
| README contains "development stage is now hardened as" with a stale version | The phrase is avoided in the rewrite. |
| README loses the `docs/CODEX_WORKFLOW.md` link | Kept in the outline (§9). |
| Runbook, registry doc or beta58 asserted strings change (`test_hamcp278…:1055-1097`, `test_core_release_evidence.py:187-195`) | Option A appends notes only; the focused tests run before Evidence. |
| Old README anchors linked externally (for example `README.md#ordinary-fan-requests`) disappear | Keep the headings `Install or update Engineering`, `Ordinary fan requests`, `Ordinary light and switch requests` and `Governed changes and evidence` where they still fit. Link the pre-consolidation README permalink. |
| Secret scan of the README (`test_hamcp191_shared_sanitizer.py`) | Synthetic and real secrets stay absent; it runs in the focused tests. |
| `codex-context.py` loses exact beta.63 resolution | Neither beta.63 file is touched; re-run in Part 2 and record the result. |
| A concurrent release PR (HAMCP-302 rc.1) prepends to the old README | Land HAMCP-303 first. If rc.1 work starts in parallel, its README edit becomes "replace the Current release paragraph". |
| Release index version mapping guessed for legacy names | Versions are taken from each document's first heading; unresolved names are listed verbatim. |

## Appendix A: per-file inventory

<details>
<summary>553 files (path, class, bytes, inbound files, inbound references)</summary>

| Path | Class | Bytes | Inbound files | Inbound refs |
| --- | --- | ---: | ---: | ---: |
| `docs/V2_4_1_BETA63_ACCEPTANCE.md` | active-release-authority | 7974 | 2 | 3 |
| `docs/V2_4_1_BETA63_RELEASE_NOTES.md` | active-release-authority | 4041 | 2 | 3 |
| `docs/AUDIT_LOG.md` | current-reference | 3985 | 3 | 4 |
| `docs/AUTOMATION_AUDIT_BASELINE.md` | current-reference | 17631 | 10 | 14 |
| `docs/AUTOMATION_BASELINE_CAPTURE.md` | current-reference | 27843 | 9 | 18 |
| `docs/AUTOMATION_LIFECYCLE_ANALYSIS.md` | current-reference | 13661 | 4 | 4 |
| `docs/AUTOMATION_RELIABILITY_ANALYSIS.md` | current-reference | 12568 | 1 | 1 |
| `docs/BUILD_INPUTS.md` | current-reference | 8860 | 8 | 9 |
| `docs/CHANGE_GOVERNANCE.md` | current-reference | 38166 | 9 | 10 |
| `docs/CHANGE_IMPACT_ANALYSIS.md` | current-reference | 16132 | 4 | 9 |
| `docs/CODEX_WORKFLOW.md` | current-reference | 75064 | 11 | 20 |
| `docs/CONFIGURATION_INTEGRITY_ANALYSIS.md` | current-reference | 18720 | 12 | 18 |
| `docs/CONFIGURATION_REVERIFICATION.md` | current-reference | 11826 | 5 | 5 |
| `docs/CORE_AUTHORITY_DIAGNOSTICS.md` | current-reference | 9756 | 3 | 7 |
| `docs/CORE_LOG_HISTORY.md` | current-reference | 9855 | 2 | 2 |
| `docs/DASHBOARD_INTEGRITY_ANALYSIS.md` | current-reference | 10883 | 1 | 1 |
| `docs/ENTITY_DEPENDENCY_ANALYSIS.md` | current-reference | 31153 | 9 | 14 |
| `docs/ERROR_TAXONOMY.md` | current-reference | 10982 | 0 | 0 |
| `docs/EXTERNAL_APPROVAL.md` | current-reference | 34698 | 15 | 25 |
| `docs/FEATURE_READINESS_MAP.md` | current-reference | 29603 | 4 | 7 |
| `docs/GOVERNED_EXECUTION_RECOVERY.md` | current-reference | 8448 | 3 | 3 |
| `docs/HANDOFF_GENERATION.md` | current-reference | 12713 | 2 | 4 |
| `docs/HA_CORE_CAPABILITY_AUTO_READMISSION.md` | current-reference | 6530 | 0 | 0 |
| `docs/HA_MCP_8_5_0_COMPATIBILITY.md` | current-reference | 6736 | 5 | 5 |
| `docs/HA_MCP_8_6_0_COMPATIBILITY.md` | current-reference | 14779 | 3 | 4 |
| `docs/HEALTH_PERFORMANCE.md` | current-reference | 22811 | 8 | 12 |
| `docs/HELD_READ_CANARY_ERROR_TAXONOMY.md` | current-reference | 3167 | 0 | 0 |
| `docs/INBOUND_SECURITY.md` | current-reference | 8125 | 8 | 10 |
| `docs/INBOUND_TOPOLOGY_CAPTURE.md` | current-reference | 9027 | 5 | 7 |
| `docs/INCIDENT_CORRELATION.md` | current-reference | 18092 | 3 | 8 |
| `docs/INSTRUCTION_GUIDANCE_RECONCILIATION.md` | current-reference | 7513 | 2 | 2 |
| `docs/INTEGRATION_INSPECTION.md` | current-reference | 10625 | 8 | 8 |
| `docs/LOGBOOK_READS.md` | current-reference | 4102 | 3 | 3 |
| `docs/OPERATIONAL_ADMINISTRATION.md` | current-reference | 29040 | 3 | 4 |
| `docs/PUBLICATION_RECOVERY_RUNBOOK.md` | current-reference | 7297 | 3 | 4 |
| `docs/RATE_LIMITING.md` | current-reference | 3787 | 4 | 6 |
| `docs/READ_COVERAGE_MATRIX.md` | current-reference | 169238 | 3 | 6 |
| `docs/SCRIPT_CALL_DEPENDENCIES.md` | current-reference | 7716 | 6 | 6 |
| `docs/SECURITY.md` | current-reference | 89281 | 14 | 15 |
| `docs/SUN_CONDITION_VERIFICATION.md` | current-reference | 10419 | 2 | 4 |
| `docs/TERMINAL_HISTORY_PROJECTION_CORRECTION.md` | current-reference | 6901 | 3 | 3 |
| `docs/TOKEN_EFFICIENCY.md` | current-reference | 6002 | 5 | 7 |
| `docs/TRIGGER_CONDITION_VERIFICATION.md` | current-reference | 5491 | 4 | 4 |
| `docs/TYPED_FAN_CONTROL.md` | current-reference | 13878 | 7 | 7 |
| `docs/TYPED_POWER_CONTROL.md` | current-reference | 11239 | 5 | 5 |
| `docs/UPSTREAM_COMPATIBILITY_OPERATOR_GUIDE.md` | current-reference | 25887 | 3 | 3 |
| `docs/UPSTREAM_TRUST_REGISTRY.md` | current-reference | 10645 | 1 | 2 |
| `docs/CORE_REGISTRY_ONE_TAP_RUNBOOK.md` | current-reference (stale items) | 12742 | 8 | 13 |
| `docs/CORE_RELEASE_REGISTRY.md` | current-reference (stale items) | 41514 | 28 | 36 |
| `docs/runbooks/DOWNGRADE-VERSUS-BACKUP-RESTORE.md` | current-reference(runbook) | 2371 | 5 | 5 |
| `docs/runbooks/LAST-KNOWN-GOOD-CONFIGURATION-RECOVERY.md` | current-reference(runbook) | 2085 | 0 | 0 |
| `docs/runbooks/MANUAL-RESTORE-DRILL.md` | current-reference(runbook) | 2217 | 0 | 0 |
| `docs/runbooks/POST-UPDATE-RECOVERY-VERIFICATION.md` | current-reference(runbook) | 2870 | 0 | 0 |
| `docs/runbooks/RECOVER-GOVERNANCE-AND-TASK-PERSISTENCE.md` | current-reference(runbook) | 2925 | 0 | 0 |
| `docs/runbooks/SAFE-MODE-RECOVERY.md` | current-reference(runbook) | 2368 | 0 | 0 |
| `docs/runbooks/UPDATE-BACKUP-PREREQUISITE-POLICY.md` | current-reference(runbook) | 2605 | 0 | 0 |
| `docs/F2_HISTORICAL_POLICY_PROJECTION_COMPATIBILITY.md` | design-adr | 11330 | 0 | 0 |
| `docs/F3_ADAPTER_LOCK_CORE.md` | design-adr | 16907 | 1 | 1 |
| `docs/F3_B_DASHBOARD_WRITE_FOUNDATION.md` | design-adr | 20200 | 0 | 0 |
| `docs/F3_C1_CONFIGURATION_CONFORMANCE.md` | design-adr | 12963 | 0 | 0 |
| `docs/F3_COMPLETION_ACCEPTANCE.md` | design-adr | 11979 | 2 | 2 |
| `docs/F3_CURRENT_STATE_INVENTORY.md` | design-adr | 17769 | 2 | 2 |
| `docs/F3_DASHBOARD_WRITE_CONTRACT.md` | design-adr | 14896 | 5 | 8 |
| `docs/F3_OPERATIONAL_CONFORMANCE.md` | design-adr | 15320 | 1 | 1 |
| `docs/F3_PARALLEL_DEVELOPMENT_PLAN.md` | design-adr | 12864 | 3 | 4 |
| `docs/F3_RUNTIME_INTEGRATION.md` | design-adr | 35076 | 3 | 3 |
| `docs/HAMCP135_CONFIGURATION_ELIGIBILITY.md` | design-adr | 6360 | 4 | 4 |
| `docs/HAMCP135_GOVERNED_ROLLBACK.md` | design-adr | 9015 | 3 | 3 |
| `docs/HAMCP_069A_ENTITY_METADATA_DESIGN.md` | design-adr | 71445 | 3 | 3 |
| `docs/HAMCP_069B_ENABLE_EXPOSE_DESIGN.md` | design-adr | 46944 | 3 | 6 |
| `docs/HAMCP_071_SCENE_CONFIGURATION_DESIGN.md` | design-adr | 42502 | 4 | 7 |
| `docs/HAMCP_089_EXACT_HELPER_STATE.md` | design-adr | 12153 | 2 | 4 |
| `docs/HAMCP_106_DASHBOARD_METADATA_DESIGN.md` | design-adr | 13415 | 3 | 6 |
| `docs/HAMCP_129_PLATFORM_READS_DESIGN.md` | design-adr | 59488 | 5 | 5 |
| `docs/HAMCP_130_BACKUP_INVENTORY_DESIGN.md` | design-adr | 29992 | 4 | 6 |
| `docs/HAMCP_131_INCIDENT_LOGS.md` | design-adr | 22381 | 11 | 16 |
| `docs/HAMCP_132_DEFINITION_VISIBILITY_DESIGN.md` | design-adr | 28478 | 8 | 14 |
| `docs/HAMCP_132_SLICE1_IMPLEMENTATION_NOTES.md` | design-adr | 13815 | 4 | 6 |
| `docs/HAMCP_132_SLICE2_COMPONENT_REVIEW.md` | design-adr | 14978 | 3 | 5 |
| `docs/HAMCP_155_GOVERNED_DELETION_DESIGN.md` | design-adr | 12360 | 4 | 8 |
| `docs/HAMCP_162_YAML_READER_DESIGN.md` | design-adr | 145189 | 6 | 8 |
| `docs/HAMCP_164_CORE_GATE_FAIL_CLOSED_DESIGN.md` | design-adr | 26783 | 4 | 5 |
| `docs/HAMCP_165_LABELS_DESIGN.md` | design-adr | 15124 | 3 | 6 |
| `docs/HAMCP_171_HEALTH_PERFORMANCE_INVESTIGATION.md` | design-adr | 39771 | 3 | 5 |
| `docs/HAMCP_181_TRUTHFUL_OUTCOMES_DESIGN.md` | design-adr | 143442 | 6 | 8 |
| `docs/HAMCP_184_AUDIT_HARDENING_DESIGN.md` | design-adr | 15442 | 3 | 4 |
| `docs/HAMCP_187_HA_MCP_870_REVIEW.md` | design-adr | 85682 | 2 | 17 |
| `docs/HAMCP_189_PR_A_DESIGN.md` | design-adr | 10987 | 2 | 3 |
| `docs/HAMCP_189_PR_B_DESIGN.md` | design-adr | 25428 | 2 | 3 |
| `docs/HAMCP_189_TRIAGE.md` | design-adr | 41694 | 6 | 8 |
| `docs/HAMCP_191_IMPLEMENTATION_NOTES.md` | design-adr | 44408 | 2 | 4 |
| `docs/HAMCP_191_SHARED_SANITIZER_DESIGN.md` | design-adr | 32426 | 3 | 3 |
| `docs/HAMCP_192_OBSERVING_FIX_DESIGN.md` | design-adr | 33000 | 1 | 2 |
| `docs/HAMCP_192_OBSERVING_INVESTIGATION.md` | design-adr | 30668 | 2 | 3 |
| `docs/HAMCP_195_GATED_HELPER_TOGGLE_DESIGN.md` | design-adr | 26857 | 2 | 3 |
| `docs/HAMCP_196_ADDON_PERMISSIONS_REVIEW.md` | design-adr | 22546 | 1 | 1 |
| `docs/HAMCP_205_ADMIN_VERIFICATION_DESIGN.md` | design-adr | 13572 | 2 | 4 |
| `docs/HAMCP_209_FAST_DEPENDENCY_INDEX_DESIGN.md` | design-adr | 41148 | 3 | 6 |
| `docs/HAMCP_210_PULL_REQUEST_TARGET.md` | design-adr | 23537 | 0 | 0 |
| `docs/HAMCP_212_LOADED_SCRIPT_CONFIG_DESIGN.md` | design-adr | 17794 | 4 | 7 |
| `docs/HAMCP_213_SAFETY_CRITICAL_APPROVAL_DESIGN.md` | design-adr | 61189 | 8 | 10 |
| `docs/HAMCP_215_CORE_AUTHORITY_RECOVERY_DESIGN.md` | design-adr | 38927 | 3 | 5 |
| `docs/HAMCP_218_SUPERVISOR_MANAGER_ROLE.md` | design-adr | 38899 | 1 | 2 |
| `docs/HAMCP_220_INCIDENT_LOG_TIMESTAMPS.md` | design-adr | 20857 | 5 | 10 |
| `docs/HAMCP_222_SIGNED_COVERAGE_AUDIT.md` | design-adr | 45391 | 2 | 4 |
| `docs/HAMCP_224_HOST_JOURNAL_DESIGN.md` | design-adr | 55173 | 5 | 10 |
| `docs/HAMCP_225_226_228_AUDIT_TOOLING_DESIGN.md` | design-adr | 39857 | 6 | 12 |
| `docs/HAMCP_232_145_HELPER_OPTIONS_AND_SELECT_DESIGN.md` | design-adr | 44721 | 4 | 8 |
| `docs/HAMCP_253_STUCK_RECORD_ACKNOWLEDGEMENT_DESIGN.md` | design-adr | 22994 | 4 | 7 |
| `docs/HAMCP_269_SEQ15_CORE_EVIDENCE.md` | design-adr | 8173 | 3 | 3 |
| `docs/architecture/ADR-002-ENGINEERING-MCP-FACILITATOR.md` | design-adr | 21402 | 4 | 6 |
| `docs/architecture/ADR-003-REVIEWED-ARGUMENT-CONSTRAINED-DASHBOARD-READS.md` | design-adr | 6732 | 3 | 3 |
| `docs/architecture/ADR-004-SIGNED-UPSTREAM-CONTRACT-ADMISSION.md` | design-adr | 3348 | 2 | 2 |
| `docs/architecture/ADR-005-READONLY-UPSTREAM-GATEWAY.md` | design-adr | 11588 | 3 | 3 |
| `docs/architecture/ADR-006-CONTRACT-LEVEL-UPSTREAM-COMPATIBILITY.md` | design-adr | 20935 | 8 | 8 |
| `docs/architecture/ADR-007-COMPATIBILITY-FAMILY-ADMISSION.md` | design-adr | 5879 | 3 | 3 |
| `docs/architecture/ADR-007-GOVERNED-OPERATIONAL-ADMINISTRATION.md` | design-adr | 5974 | 3 | 3 |
| `docs/architecture/ADR-008-DURABLE-EXECUTION-TASKS.md` | design-adr | 7454 | 4 | 4 |
| `docs/architecture/ADR-009-SIGNED-COMPATIBILITY-REGISTRY-FOUNDATION.md` | design-adr | 5894 | 3 | 3 |
| `docs/architecture/ADR-010-KNOWLEDGE-SOURCE-PROVENANCE.md` | design-adr | 3419 | 1 | 1 |
| `docs/architecture/ADR-011-GOVERNED-UPDATE-RECOVERY-PREFLIGHT.md` | design-adr | 6812 | 2 | 2 |
| `docs/architecture/ADR-012-*.md` (policy risk and elevated approval; full name abbreviated because it matches the case-insensitive `sk-` credential pattern in `scripts/check.ps1`) | design-adr | 10476 | 0 | 0 |
| `docs/architecture/ADR-013-F3-OPERATION-ADAPTER-AND-LOCK-CONTRACT.md` | design-adr | 17299 | 3 | 3 |
| `docs/architecture/ADR-014-F3-CONFIGURATION-ADAPTER-CONFORMANCE.md` | design-adr | 6936 | 0 | 0 |
| `docs/architecture/ADR-015-F3-OPERATIONAL-EVIDENCE-AND-HOLDS.md` | design-adr | 3382 | 0 | 0 |
| `docs/architecture/ADR-016-F3-RUNTIME-INTEGRATION.md` | design-adr | 3179 | 1 | 1 |
| `docs/architecture/ADR-017-F3-CHILD-EXECUTION-AND-HOLDS.md` | design-adr | 3191 | 1 | 1 |
| `docs/architecture/ADR-018-F3-GOVERNED-ROLLBACK.md` | design-adr | 2763 | 1 | 1 |
| `docs/architecture/ADR-019-BOUNDED-PLAN-STORE-NAVIGATION.md` | design-adr | 5065 | 0 | 0 |
| `docs/architecture/ADR-020-CAPABILITY-SCOPED-AUTOMATIC-READMISSION.md` | design-adr | 15962 | 3 | 3 |
| `docs/architecture/ADR-021-HOME-ASSISTANT-SOURCE-AUTHORITY.md` | design-adr | 18602 | 6 | 11 |
| `docs/architecture/ADR-022-OWNER-AUTHORITATIVE-PRODUCT-DIRECTION.md` | design-adr | 18145 | 10 | 11 |
| `docs/architecture/ADR-023-HA-MCP-AUTOMATIC-READMISSION.md` | design-adr | 11404 | 0 | 0 |
| `docs/architecture/ADR-024-SANITIZED-INTEGRATION-INSPECTION.md` | design-adr | 2170 | 1 | 1 |
| `docs/CORE_2026_10_COMPATIBILITY_EVIDENCE.md` | evidence | 6138 | 4 | 4 |
| `docs/evidence/core-automation-baseline-registry-sequence5.md` | evidence | 5925 | 1 | 2 |
| `docs/evidence/hamcp071-mutations/README.md` | evidence | 17643 | 44 | 73 |
| `docs/evidence/hamcp097-health-benchmark/README.md` | evidence | 11110 | 44 | 73 |
| `docs/evidence/hamcp097-health-benchmark/summary.md` | evidence | 13193 | 5 | 11 |
| `docs/evidence/hamcp106-mutations/README.md` | evidence | 5262 | 44 | 73 |
| `docs/evidence/hamcp132/README.md` | evidence | 1479 | 44 | 73 |
| `docs/evidence/hamcp161/core-registry-hamcp161.md` | evidence | 24703 | 6 | 7 |
| `docs/evidence/hamcp162/README.md` | evidence | 32106 | 43 | 71 |
| `docs/evidence/hamcp171-measurements/README.md` | evidence | 1030 | 44 | 73 |
| `docs/evidence/hamcp179/entity-metadata-container-evidence.md` | evidence | 11458 | 1 | 1 |
| `docs/evidence/hamcp181-pr2-mutations/README.md` | evidence | 3557 | 44 | 73 |
| `docs/evidence/hamcp182/OWNER-SIGNING-RUNBOOK.md` | evidence | 9799 | 4 | 8 |
| `docs/evidence/hamcp182/core-registry-hamcp182.md` | evidence | 32806 | 4 | 7 |
| `docs/evidence/hamcp195/README.md` | evidence | 2792 | 44 | 73 |
| `docs/evidence/hamcp209/README.md` | evidence | 1334 | 44 | 73 |
| `docs/evidence/hamcp212/README.md` | evidence | 3499 | 44 | 73 |
| `docs/evidence/hamcp213/pr001/README.md` | evidence | 3636 | 44 | 73 |
| `docs/evidence/hamcp222/OWNER-SIGNING-RUNBOOK.md` | evidence | 10691 | 4 | 8 |
| `docs/evidence/hamcp222/core-registry-hamcp222.md` | evidence | 26547 | 2 | 4 |
| `docs/evidence/hamcp227-mutations/README.md` | evidence | 9690 | 44 | 73 |
| `docs/evidence/hamcp251/HAMCP-251-ha-mcp-9x-review.md` | evidence | 77797 | 0 | 0 |
| `docs/evidence/hamcp251/HAMCP-251-part-b1-core-2026.11-beta.md` | evidence | 148159 | 0 | 0 |
| `docs/evidence/hamcp251/source-contract-table.md` | evidence | 69525 | 1 | 1 |
| `docs/evidence/hamcp253-mutations/README.md` | evidence | 10990 | 44 | 73 |
| `docs/evidence/hamcp269/README.md` | evidence | 9895 | 44 | 73 |
| `docs/evidence/hamcp277-mutations/README.md` | evidence | 7704 | 44 | 73 |
| `docs/evidence/hamcp279/README.md` | evidence | 3504 | 44 | 73 |
| `docs/evidence/hamcp290/HAMCP-290-hacs-marketplace-migration.md` | evidence | 61431 | 0 | 0 |
| `docs/evidence/upstream-read-compatibility/ha-mcp-7.14.1-to-7.14.2.md` | evidence | 6414 | 0 | 0 |
| `docs/evidence/upstream-read-compatibility/ha-mcp-8.0.0-live-addon-field-diff.md` | evidence | 3991 | 1 | 2 |
| `docs/evidence/upstream-read-compatibility/ha-mcp-8.0.0-review.md` | evidence | 2711 | 0 | 0 |
| `docs/evidence/upstream-read-compatibility/ha-mcp-8.1.0-review.md` | evidence | 17730 | 2 | 3 |
| `docs/evidence/upstream-read-compatibility/ha-mcp-8.2.0-review.md` | evidence | 5574 | 0 | 0 |
| `docs/evidence/upstream-read-compatibility/ha-mcp-8.4.1-provenance.md` | evidence | 4130 | 0 | 0 |
| `docs/ALARMO_INSPECTION_ACCEPTANCE.md` | feature-acceptance-contract | 18674 | 4 | 4 |
| `docs/AUTOMATION_BASELINE_CAPTURE_ACCEPTANCE.md` | feature-acceptance-contract | 8810 | 3 | 6 |
| `docs/HA_MCP_CAPABILITY_AUTO_READMISSION_ACCEPTANCE.md` | feature-acceptance-contract | 5497 | 1 | 1 |
| `docs/BETA35_MOBILE_APPROVAL_NAVIGATION.md` | historical-release-companion | 3033 | 2 | 2 |
| `docs/HA_CORE_2026_9_COMPATIBILITY.md` | historical-release-companion | 5749 | 0 | 0 |
| `docs/RC2DEV8_EXACT_IMAGE_BAKE.md` | historical-release-companion | 3005 | 0 | 0 |
| `docs/RESTART_RECONCILIATION_BETA11.md` | historical-release-companion | 1944 | 0 | 0 |
| `docs/V2_2_0_BETA12_CANARY_FAILURE.md` | historical-release-companion | 12060 | 2 | 4 |
| `docs/V2_2_0_BETA21_HA_MCP_8_1_0_ACCEPTANCE.md` | historical-release-companion | 3106 | 0 | 0 |
| `docs/V2_2_0_BETA21_HA_MCP_8_1_0_RELEASE_NOTES.md` | historical-release-companion | 1666 | 0 | 0 |
| `docs/V2_2_0_BETA31_WORKSTREAM2_HA_MCP_8_2_0_ACCEPTANCE.md` | historical-release-companion | 4082 | 0 | 0 |
| `docs/BETA_17_RELEASE_NOTES.md` | historical-release-record | 989 | 0 | 0 |
| `docs/BETA_18_RELEASE_NOTES.md` | historical-release-record | 2511 | 0 | 0 |
| `docs/BETA_19_RELEASE_NOTES.md` | historical-release-record | 1337 | 0 | 0 |
| `docs/BETA_20_RELEASE_NOTES.md` | historical-release-record | 1457 | 0 | 0 |
| `docs/BETA_21_RELEASE_NOTES.md` | historical-release-record | 1398 | 0 | 0 |
| `docs/BETA_22_RELEASE_NOTES.md` | historical-release-record | 1364 | 0 | 0 |
| `docs/BETA_23_RELEASE_NOTES.md` | historical-release-record | 4658 | 1 | 1 |
| `docs/BETA_24_RELEASE_NOTES.md` | historical-release-record | 6188 | 1 | 2 |
| `docs/BETA_25_RELEASE_NOTES.md` | historical-release-record | 2091 | 0 | 0 |
| `docs/BETA_26_RELEASE_NOTES.md` | historical-release-record | 4486 | 1 | 2 |
| `docs/RC1DEV1_ACCEPTANCE.md` | historical-release-record | 5383 | 1 | 1 |
| `docs/RC1DEV1_RELEASE_NOTES.md` | historical-release-record | 5003 | 1 | 1 |
| `docs/RC1DEV2_ACCEPTANCE.md` | historical-release-record | 6504 | 1 | 1 |
| `docs/RC1DEV2_RELEASE_NOTES.md` | historical-release-record | 5672 | 1 | 1 |
| `docs/RC1_ACCEPTANCE.md` | historical-release-record | 18235 | 7 | 14 |
| `docs/RC1_RELEASE_NOTES.md` | historical-release-record | 13191 | 4 | 8 |
| `docs/RC2DEV10_ACCEPTANCE.md` | historical-release-record | 3394 | 0 | 0 |
| `docs/RC2DEV10_RELEASE_NOTES.md` | historical-release-record | 3061 | 0 | 0 |
| `docs/RC2DEV12_ACCEPTANCE.md` | historical-release-record | 1434 | 1 | 8 |
| `docs/RC2DEV12_RELEASE_NOTES.md` | historical-release-record | 1356 | 1 | 11 |
| `docs/RC2DEV13_ACCEPTANCE.md` | historical-release-record | 6731 | 1 | 5 |
| `docs/RC2DEV13_RELEASE_NOTES.md` | historical-release-record | 3849 | 1 | 3 |
| `docs/RC2DEV14_ACCEPTANCE.md` | historical-release-record | 8806 | 1 | 3 |
| `docs/RC2DEV14_RELEASE_NOTES.md` | historical-release-record | 5989 | 1 | 2 |
| `docs/RC2DEV15_ACCEPTANCE.md` | historical-release-record | 10280 | 1 | 3 |
| `docs/RC2DEV15_RELEASE_NOTES.md` | historical-release-record | 8085 | 1 | 2 |
| `docs/RC2DEV16_ACCEPTANCE.md` | historical-release-record | 7144 | 1 | 2 |
| `docs/RC2DEV16_RELEASE_NOTES.md` | historical-release-record | 4956 | 1 | 2 |
| `docs/RC2DEV4_ACCEPTANCE.md` | historical-release-record | 5790 | 0 | 0 |
| `docs/RC2DEV4_RELEASE_NOTES.md` | historical-release-record | 3490 | 0 | 0 |
| `docs/RC2DEV5_ACCEPTANCE.md` | historical-release-record | 6531 | 0 | 0 |
| `docs/RC2DEV5_RELEASE_NOTES.md` | historical-release-record | 6004 | 0 | 0 |
| `docs/RC2DEV6_ACCEPTANCE.md` | historical-release-record | 3420 | 0 | 0 |
| `docs/RC2DEV6_RELEASE_NOTES.md` | historical-release-record | 3186 | 0 | 0 |
| `docs/RC2DEV7_ACCEPTANCE.md` | historical-release-record | 2994 | 0 | 0 |
| `docs/RC2DEV7_RELEASE_NOTES.md` | historical-release-record | 3332 | 1 | 2 |
| `docs/RC2DEV8_ACCEPTANCE.md` | historical-release-record | 3609 | 1 | 1 |
| `docs/RC2DEV8_RELEASE_NOTES.md` | historical-release-record | 3439 | 1 | 1 |
| `docs/RC2DEV9_ACCEPTANCE.md` | historical-release-record | 5005 | 0 | 0 |
| `docs/RC2DEV9_RELEASE_NOTES.md` | historical-release-record | 6691 | 0 | 0 |
| `docs/RC2_ACCEPTANCE.md` | historical-release-record | 3889 | 13 | 20 |
| `docs/RC2_RELEASE_NOTES.md` | historical-release-record | 5201 | 5 | 8 |
| `docs/RC3A_ACCEPTANCE.md` | historical-release-record | 5108 | 2 | 2 |
| `docs/RC3A_RELEASE_NOTES.md` | historical-release-record | 7671 | 4 | 6 |
| `docs/V2_0_0_ACCEPTANCE.md` | historical-release-record | 11216 | 2 | 4 |
| `docs/V2_0_0_RELEASE_NOTES.md` | historical-release-record | 7971 | 1 | 2 |
| `docs/V2_0_1_ACCEPTANCE.md` | historical-release-record | 9409 | 3 | 5 |
| `docs/V2_0_1_RELEASE_NOTES.md` | historical-release-record | 8878 | 2 | 3 |
| `docs/V2_1_0_BETA1_ACCEPTANCE.md` | historical-release-record | 3702 | 1 | 2 |
| `docs/V2_1_0_BETA1_RELEASE_NOTES.md` | historical-release-record | 3140 | 1 | 2 |
| `docs/V2_1_0_BETA2_ACCEPTANCE.md` | historical-release-record | 5764 | 1 | 2 |
| `docs/V2_1_0_BETA2_RELEASE_NOTES.md` | historical-release-record | 4969 | 0 | 0 |
| `docs/V2_1_1_BETA2_ACCEPTANCE.md` | historical-release-record | 6797 | 0 | 0 |
| `docs/V2_1_1_BETA2_RELEASE_NOTES.md` | historical-release-record | 4562 | 1 | 1 |
| `docs/V2_1_1_BETA3_ACCEPTANCE.md` | historical-release-record | 5862 | 1 | 2 |
| `docs/V2_1_1_BETA3_RELEASE_NOTES.md` | historical-release-record | 4025 | 0 | 0 |
| `docs/V2_2_0_ACCEPTANCE.md` | historical-release-record | 13096 | 3 | 3 |
| `docs/V2_2_0_BETA10_ACCEPTANCE.md` | historical-release-record | 5726 | 3 | 5 |
| `docs/V2_2_0_BETA10_RELEASE_NOTES.md` | historical-release-record | 3988 | 0 | 0 |
| `docs/V2_2_0_BETA11_ACCEPTANCE.md` | historical-release-record | 5951 | 1 | 2 |
| `docs/V2_2_0_BETA11_RELEASE_NOTES.md` | historical-release-record | 3838 | 0 | 0 |
| `docs/V2_2_0_BETA12_ACCEPTANCE.md` | historical-release-record | 4368 | 1 | 2 |
| `docs/V2_2_0_BETA12_RELEASE_NOTES.md` | historical-release-record | 3292 | 0 | 0 |
| `docs/V2_2_0_BETA13_ACCEPTANCE.md` | historical-release-record | 3714 | 1 | 2 |
| `docs/V2_2_0_BETA13_RELEASE_NOTES.md` | historical-release-record | 2492 | 0 | 0 |
| `docs/V2_2_0_BETA14_ACCEPTANCE.md` | historical-release-record | 6256 | 1 | 2 |
| `docs/V2_2_0_BETA14_RELEASE_NOTES.md` | historical-release-record | 5951 | 0 | 0 |
| `docs/V2_2_0_BETA15_ACCEPTANCE.md` | historical-release-record | 6376 | 3 | 6 |
| `docs/V2_2_0_BETA15_RELEASE_NOTES.md` | historical-release-record | 5762 | 0 | 0 |
| `docs/V2_2_0_BETA16_ACCEPTANCE.md` | historical-release-record | 3416 | 0 | 0 |
| `docs/V2_2_0_BETA16_RELEASE_NOTES.md` | historical-release-record | 3700 | 0 | 0 |
| `docs/V2_2_0_BETA17_ACCEPTANCE.md` | historical-release-record | 6808 | 0 | 0 |
| `docs/V2_2_0_BETA17_RELEASE_NOTES.md` | historical-release-record | 4356 | 0 | 0 |
| `docs/V2_2_0_BETA18_ACCEPTANCE.md` | historical-release-record | 4772 | 0 | 0 |
| `docs/V2_2_0_BETA18_RELEASE_NOTES.md` | historical-release-record | 4016 | 0 | 0 |
| `docs/V2_2_0_BETA19_ACCEPTANCE.md` | historical-release-record | 5503 | 0 | 0 |
| `docs/V2_2_0_BETA19_RELEASE_NOTES.md` | historical-release-record | 4700 | 0 | 0 |
| `docs/V2_2_0_BETA1_ACCEPTANCE.md` | historical-release-record | 6978 | 1 | 2 |
| `docs/V2_2_0_BETA1_RELEASE_NOTES.md` | historical-release-record | 4633 | 0 | 0 |
| `docs/V2_2_0_BETA20_ACCEPTANCE.md` | historical-release-record | 3898 | 0 | 0 |
| `docs/V2_2_0_BETA20_RELEASE_NOTES.md` | historical-release-record | 3672 | 0 | 0 |
| `docs/V2_2_0_BETA21_ACCEPTANCE.md` | historical-release-record | 7165 | 1 | 2 |
| `docs/V2_2_0_BETA21_RELEASE_NOTES.md` | historical-release-record | 3486 | 0 | 0 |
| `docs/V2_2_0_BETA22_ACCEPTANCE.md` | historical-release-record | 23103 | 0 | 0 |
| `docs/V2_2_0_BETA22_RELEASE_NOTES.md` | historical-release-record | 1802 | 2 | 2 |
| `docs/V2_2_0_BETA23_ACCEPTANCE.md` | historical-release-record | 10832 | 0 | 0 |
| `docs/V2_2_0_BETA23_RELEASE_NOTES.md` | historical-release-record | 3243 | 0 | 0 |
| `docs/V2_2_0_BETA24_ACCEPTANCE.md` | historical-release-record | 2860 | 0 | 0 |
| `docs/V2_2_0_BETA24_RELEASE_NOTES.md` | historical-release-record | 2043 | 0 | 0 |
| `docs/V2_2_0_BETA25_ACCEPTANCE.md` | historical-release-record | 2831 | 1 | 1 |
| `docs/V2_2_0_BETA25_RELEASE_NOTES.md` | historical-release-record | 1929 | 1 | 1 |
| `docs/V2_2_0_BETA26_ACCEPTANCE.md` | historical-release-record | 3113 | 0 | 0 |
| `docs/V2_2_0_BETA26_RELEASE_NOTES.md` | historical-release-record | 2053 | 0 | 0 |
| `docs/V2_2_0_BETA27_ACCEPTANCE.md` | historical-release-record | 3385 | 1 | 1 |
| `docs/V2_2_0_BETA27_RELEASE_NOTES.md` | historical-release-record | 2388 | 1 | 1 |
| `docs/V2_2_0_BETA28_ACCEPTANCE.md` | historical-release-record | 4096 | 1 | 1 |
| `docs/V2_2_0_BETA28_RELEASE_NOTES.md` | historical-release-record | 2744 | 1 | 1 |
| `docs/V2_2_0_BETA29_ACCEPTANCE.md` | historical-release-record | 3021 | 1 | 1 |
| `docs/V2_2_0_BETA29_RELEASE_NOTES.md` | historical-release-record | 2770 | 1 | 1 |
| `docs/V2_2_0_BETA2_ACCEPTANCE.md` | historical-release-record | 3276 | 0 | 0 |
| `docs/V2_2_0_BETA2_RELEASE_NOTES.md` | historical-release-record | 1881 | 0 | 0 |
| `docs/V2_2_0_BETA30_ACCEPTANCE.md` | historical-release-record | 4039 | 1 | 1 |
| `docs/V2_2_0_BETA30_RELEASE_NOTES.md` | historical-release-record | 3741 | 1 | 1 |
| `docs/V2_2_0_BETA31_ACCEPTANCE.md` | historical-release-record | 4042 | 1 | 1 |
| `docs/V2_2_0_BETA31_RELEASE_NOTES.md` | historical-release-record | 3005 | 1 | 1 |
| `docs/V2_2_0_BETA32_ACCEPTANCE.md` | historical-release-record | 4432 | 1 | 1 |
| `docs/V2_2_0_BETA32_RELEASE_NOTES.md` | historical-release-record | 2910 | 1 | 1 |
| `docs/V2_2_0_BETA33_ACCEPTANCE.md` | historical-release-record | 4800 | 1 | 1 |
| `docs/V2_2_0_BETA33_RELEASE_NOTES.md` | historical-release-record | 3648 | 1 | 1 |
| `docs/V2_2_0_BETA34_ACCEPTANCE.md` | historical-release-record | 6797 | 0 | 0 |
| `docs/V2_2_0_BETA34_RELEASE_NOTES.md` | historical-release-record | 4095 | 0 | 0 |
| `docs/V2_2_0_BETA35_ACCEPTANCE.md` | historical-release-record | 6660 | 1 | 2 |
| `docs/V2_2_0_BETA35_RELEASE_NOTES.md` | historical-release-record | 2910 | 0 | 0 |
| `docs/V2_2_0_BETA36_ACCEPTANCE.md` | historical-release-record | 5802 | 1 | 1 |
| `docs/V2_2_0_BETA36_RELEASE_NOTES.md` | historical-release-record | 3533 | 1 | 1 |
| `docs/V2_2_0_BETA37_ACCEPTANCE.md` | historical-release-record | 10115 | 0 | 0 |
| `docs/V2_2_0_BETA37_RELEASE_NOTES.md` | historical-release-record | 7130 | 0 | 0 |
| `docs/V2_2_0_BETA38_ACCEPTANCE.md` | historical-release-record | 8220 | 0 | 0 |
| `docs/V2_2_0_BETA38_RELEASE_NOTES.md` | historical-release-record | 6001 | 0 | 0 |
| `docs/V2_2_0_BETA39_ACCEPTANCE.md` | historical-release-record | 21066 | 0 | 0 |
| `docs/V2_2_0_BETA39_RELEASE_NOTES.md` | historical-release-record | 10042 | 0 | 0 |
| `docs/V2_2_0_BETA3_ACCEPTANCE.md` | historical-release-record | 3520 | 0 | 0 |
| `docs/V2_2_0_BETA3_RELEASE_NOTES.md` | historical-release-record | 2908 | 0 | 0 |
| `docs/V2_2_0_BETA40_ACCEPTANCE.md` | historical-release-record | 6823 | 0 | 0 |
| `docs/V2_2_0_BETA40_RELEASE_NOTES.md` | historical-release-record | 3746 | 0 | 0 |
| `docs/V2_2_0_BETA41_ACCEPTANCE.md` | historical-release-record | 7137 | 0 | 0 |
| `docs/V2_2_0_BETA41_RELEASE_NOTES.md` | historical-release-record | 4109 | 0 | 0 |
| `docs/V2_2_0_BETA42_ACCEPTANCE.md` | historical-release-record | 9142 | 0 | 0 |
| `docs/V2_2_0_BETA42_RELEASE_NOTES.md` | historical-release-record | 5959 | 0 | 0 |
| `docs/V2_2_0_BETA43_ACCEPTANCE.md` | historical-release-record | 7141 | 0 | 0 |
| `docs/V2_2_0_BETA43_RELEASE_NOTES.md` | historical-release-record | 4122 | 0 | 0 |
| `docs/V2_2_0_BETA44_ACCEPTANCE.md` | historical-release-record | 5648 | 0 | 0 |
| `docs/V2_2_0_BETA44_RELEASE_NOTES.md` | historical-release-record | 3367 | 0 | 0 |
| `docs/V2_2_0_BETA45_ACCEPTANCE.md` | historical-release-record | 6494 | 0 | 0 |
| `docs/V2_2_0_BETA45_RELEASE_NOTES.md` | historical-release-record | 3505 | 0 | 0 |
| `docs/V2_2_0_BETA46_ACCEPTANCE.md` | historical-release-record | 7994 | 0 | 0 |
| `docs/V2_2_0_BETA46_RELEASE_NOTES.md` | historical-release-record | 3275 | 0 | 0 |
| `docs/V2_2_0_BETA47_ACCEPTANCE.md` | historical-release-record | 9110 | 0 | 0 |
| `docs/V2_2_0_BETA47_RELEASE_NOTES.md` | historical-release-record | 3695 | 0 | 0 |
| `docs/V2_2_0_BETA48_ACCEPTANCE.md` | historical-release-record | 6444 | 0 | 0 |
| `docs/V2_2_0_BETA48_RELEASE_NOTES.md` | historical-release-record | 2365 | 0 | 0 |
| `docs/V2_2_0_BETA49_ACCEPTANCE.md` | historical-release-record | 8054 | 0 | 0 |
| `docs/V2_2_0_BETA49_RELEASE_NOTES.md` | historical-release-record | 2470 | 0 | 0 |
| `docs/V2_2_0_BETA4_ACCEPTANCE.md` | historical-release-record | 3685 | 0 | 0 |
| `docs/V2_2_0_BETA4_RELEASE_NOTES.md` | historical-release-record | 2677 | 0 | 0 |
| `docs/V2_2_0_BETA50_ACCEPTANCE.md` | historical-release-record | 9245 | 0 | 0 |
| `docs/V2_2_0_BETA50_RELEASE_NOTES.md` | historical-release-record | 2919 | 0 | 0 |
| `docs/V2_2_0_BETA51_ACCEPTANCE.md` | historical-release-record | 9663 | 1 | 1 |
| `docs/V2_2_0_BETA51_RELEASE_NOTES.md` | historical-release-record | 3296 | 0 | 0 |
| `docs/V2_2_0_BETA52_ACCEPTANCE.md` | historical-release-record | 8966 | 1 | 1 |
| `docs/V2_2_0_BETA52_RELEASE_NOTES.md` | historical-release-record | 3407 | 0 | 0 |
| `docs/V2_2_0_BETA53_ACCEPTANCE.md` | historical-release-record | 16621 | 1 | 2 |
| `docs/V2_2_0_BETA53_RELEASE_NOTES.md` | historical-release-record | 3967 | 1 | 2 |
| `docs/V2_2_0_BETA54_ACCEPTANCE.md` | historical-release-record | 9835 | 1 | 2 |
| `docs/V2_2_0_BETA54_RELEASE_NOTES.md` | historical-release-record | 4194 | 1 | 2 |
| `docs/V2_2_0_BETA55_ACCEPTANCE.md` | historical-release-record | 9162 | 0 | 0 |
| `docs/V2_2_0_BETA55_RELEASE_NOTES.md` | historical-release-record | 4162 | 0 | 0 |
| `docs/V2_2_0_BETA56_ACCEPTANCE.md` | historical-release-record | 5880 | 0 | 0 |
| `docs/V2_2_0_BETA56_RELEASE_NOTES.md` | historical-release-record | 2679 | 0 | 0 |
| `docs/V2_2_0_BETA57_ACCEPTANCE.md` | historical-release-record | 5873 | 0 | 0 |
| `docs/V2_2_0_BETA57_RELEASE_NOTES.md` | historical-release-record | 2938 | 0 | 0 |
| `docs/V2_2_0_BETA58_ACCEPTANCE.md` | historical-release-record | 7944 | 1 | 1 |
| `docs/V2_2_0_BETA58_RELEASE_NOTES.md` | historical-release-record | 4000 | 1 | 1 |
| `docs/V2_2_0_BETA5_ACCEPTANCE.md` | historical-release-record | 4486 | 0 | 0 |
| `docs/V2_2_0_BETA5_RELEASE_NOTES.md` | historical-release-record | 3603 | 0 | 0 |
| `docs/V2_2_0_BETA6_ACCEPTANCE.md` | historical-release-record | 9038 | 0 | 0 |
| `docs/V2_2_0_BETA6_RELEASE_NOTES.md` | historical-release-record | 6868 | 0 | 0 |
| `docs/V2_2_0_BETA7_ACCEPTANCE.md` | historical-release-record | 6347 | 0 | 0 |
| `docs/V2_2_0_BETA7_RELEASE_NOTES.md` | historical-release-record | 3263 | 0 | 0 |
| `docs/V2_2_0_BETA8_ACCEPTANCE.md` | historical-release-record | 6333 | 0 | 0 |
| `docs/V2_2_0_BETA8_RELEASE_NOTES.md` | historical-release-record | 4047 | 0 | 0 |
| `docs/V2_2_0_BETA9_ACCEPTANCE.md` | historical-release-record | 8533 | 2 | 3 |
| `docs/V2_2_0_BETA9_RELEASE_NOTES.md` | historical-release-record | 5684 | 0 | 0 |
| `docs/V2_2_0_RC1_ACCEPTANCE.md` | historical-release-record | 5230 | 3 | 8 |
| `docs/V2_2_0_RC1_RELEASE_NOTES.md` | historical-release-record | 2434 | 3 | 6 |
| `docs/V2_2_0_RC2_ACCEPTANCE.md` | historical-release-record | 8556 | 4 | 4 |
| `docs/V2_2_0_RC2_RELEASE_NOTES.md` | historical-release-record | 4682 | 1 | 1 |
| `docs/V2_2_0_RC3_ACCEPTANCE.md` | historical-release-record | 16391 | 3 | 3 |
| `docs/V2_2_0_RC3_RELEASE_NOTES.md` | historical-release-record | 5125 | 0 | 0 |
| `docs/V2_2_0_RC4_ACCEPTANCE.md` | historical-release-record | 17032 | 2 | 2 |
| `docs/V2_2_0_RC4_RELEASE_NOTES.md` | historical-release-record | 6165 | 0 | 0 |
| `docs/V2_2_0_RC5_ACCEPTANCE.md` | historical-release-record | 16854 | 1 | 1 |
| `docs/V2_2_0_RC5_RELEASE_NOTES.md` | historical-release-record | 6017 | 0 | 0 |
| `docs/V2_2_0_RC6_ACCEPTANCE.md` | historical-release-record | 13909 | 1 | 1 |
| `docs/V2_2_0_RC6_RELEASE_NOTES.md` | historical-release-record | 5988 | 0 | 0 |
| `docs/V2_2_0_RC7_ACCEPTANCE.md` | historical-release-record | 9986 | 1 | 1 |
| `docs/V2_2_0_RC7_RELEASE_NOTES.md` | historical-release-record | 3955 | 0 | 0 |
| `docs/V2_2_0_RC8_ACCEPTANCE.md` | historical-release-record | 11832 | 1 | 1 |
| `docs/V2_2_0_RC8_RELEASE_NOTES.md` | historical-release-record | 4601 | 0 | 0 |
| `docs/V2_2_0_RC9_ACCEPTANCE.md` | historical-release-record | 9561 | 4 | 4 |
| `docs/V2_2_0_RC9_RELEASE_NOTES.md` | historical-release-record | 4904 | 2 | 2 |
| `docs/V2_2_0_RELEASE_NOTES.md` | historical-release-record | 5499 | 3 | 3 |
| `docs/V2_2_1_ACCEPTANCE.md` | historical-release-record | 9684 | 1 | 1 |
| `docs/V2_2_1_RELEASE_NOTES.md` | historical-release-record | 5671 | 1 | 1 |
| `docs/V2_2_2_ACCEPTANCE.md` | historical-release-record | 9776 | 1 | 1 |
| `docs/V2_2_2_RELEASE_NOTES.md` | historical-release-record | 4304 | 1 | 1 |
| `docs/V2_2_3_ACCEPTANCE.md` | historical-release-record | 11261 | 2 | 2 |
| `docs/V2_2_3_RELEASE_NOTES.md` | historical-release-record | 5658 | 0 | 0 |
| `docs/V2_3_0_ACCEPTANCE.md` | historical-release-record | 9688 | 4 | 4 |
| `docs/V2_3_0_BETA1_ACCEPTANCE.md` | historical-release-record | 9334 | 1 | 1 |
| `docs/V2_3_0_BETA1_RELEASE_NOTES.md` | historical-release-record | 5870 | 0 | 0 |
| `docs/V2_3_0_BETA2_ACCEPTANCE.md` | historical-release-record | 8906 | 1 | 1 |
| `docs/V2_3_0_BETA2_RELEASE_NOTES.md` | historical-release-record | 4881 | 0 | 0 |
| `docs/V2_3_0_BETA3_ACCEPTANCE.md` | historical-release-record | 8759 | 2 | 2 |
| `docs/V2_3_0_BETA3_RELEASE_NOTES.md` | historical-release-record | 4516 | 0 | 0 |
| `docs/V2_3_0_BETA4_ACCEPTANCE.md` | historical-release-record | 8071 | 3 | 3 |
| `docs/V2_3_0_BETA4_RELEASE_NOTES.md` | historical-release-record | 4166 | 0 | 0 |
| `docs/V2_3_0_RC1_ACCEPTANCE.md` | historical-release-record | 9329 | 1 | 1 |
| `docs/V2_3_0_RC1_RELEASE_NOTES.md` | historical-release-record | 2976 | 0 | 0 |
| `docs/V2_3_0_RC2_ACCEPTANCE.md` | historical-release-record | 10970 | 3 | 3 |
| `docs/V2_3_0_RC2_RELEASE_NOTES.md` | historical-release-record | 3672 | 0 | 0 |
| `docs/V2_3_0_RELEASE_NOTES.md` | historical-release-record | 3720 | 2 | 2 |
| `docs/V2_4_0_BETA10_ACCEPTANCE.md` | historical-release-record | 9771 | 1 | 1 |
| `docs/V2_4_0_BETA10_RELEASE_NOTES.md` | historical-release-record | 4517 | 0 | 0 |
| `docs/V2_4_0_BETA11_ACCEPTANCE.md` | historical-release-record | 9490 | 3 | 4 |
| `docs/V2_4_0_BETA11_RELEASE_NOTES.md` | historical-release-record | 3325 | 2 | 3 |
| `docs/V2_4_0_BETA12_ACCEPTANCE.md` | historical-release-record | 9253 | 1 | 1 |
| `docs/V2_4_0_BETA12_RELEASE_NOTES.md` | historical-release-record | 3760 | 0 | 0 |
| `docs/V2_4_0_BETA13_ACCEPTANCE.md` | historical-release-record | 11659 | 2 | 2 |
| `docs/V2_4_0_BETA13_RELEASE_NOTES.md` | historical-release-record | 4413 | 0 | 0 |
| `docs/V2_4_0_BETA14_ACCEPTANCE.md` | historical-release-record | 7569 | 2 | 2 |
| `docs/V2_4_0_BETA14_RELEASE_NOTES.md` | historical-release-record | 2895 | 1 | 1 |
| `docs/V2_4_0_BETA15_ACCEPTANCE.md` | historical-release-record | 7988 | 3 | 3 |
| `docs/V2_4_0_BETA15_RELEASE_NOTES.md` | historical-release-record | 4008 | 0 | 0 |
| `docs/V2_4_0_BETA16_ACCEPTANCE.md` | historical-release-record | 9952 | 1 | 1 |
| `docs/V2_4_0_BETA16_RELEASE_NOTES.md` | historical-release-record | 4102 | 0 | 0 |
| `docs/V2_4_0_BETA1_ACCEPTANCE.md` | historical-release-record | 4010 | 3 | 3 |
| `docs/V2_4_0_BETA1_RELEASE_NOTES.md` | historical-release-record | 1524 | 0 | 0 |
| `docs/V2_4_0_BETA2_ACCEPTANCE.md` | historical-release-record | 4300 | 2 | 3 |
| `docs/V2_4_0_BETA2_RELEASE_NOTES.md` | historical-release-record | 1366 | 1 | 1 |
| `docs/V2_4_0_BETA3_ACCEPTANCE.md` | historical-release-record | 4554 | 2 | 2 |
| `docs/V2_4_0_BETA3_RELEASE_NOTES.md` | historical-release-record | 1391 | 1 | 1 |
| `docs/V2_4_0_BETA4_ACCEPTANCE.md` | historical-release-record | 4281 | 1 | 1 |
| `docs/V2_4_0_BETA4_RELEASE_NOTES.md` | historical-release-record | 1220 | 0 | 0 |
| `docs/V2_4_0_BETA5_ACCEPTANCE.md` | historical-release-record | 4622 | 1 | 1 |
| `docs/V2_4_0_BETA5_RELEASE_NOTES.md` | historical-release-record | 1805 | 0 | 0 |
| `docs/V2_4_0_BETA6_ACCEPTANCE.md` | historical-release-record | 11991 | 1 | 1 |
| `docs/V2_4_0_BETA6_RELEASE_NOTES.md` | historical-release-record | 3331 | 2 | 2 |
| `docs/V2_4_0_BETA7_ACCEPTANCE.md` | historical-release-record | 12936 | 2 | 2 |
| `docs/V2_4_0_BETA7_RELEASE_NOTES.md` | historical-release-record | 3244 | 2 | 2 |
| `docs/V2_4_0_BETA8_ACCEPTANCE.md` | historical-release-record | 12435 | 1 | 1 |
| `docs/V2_4_0_BETA8_RELEASE_NOTES.md` | historical-release-record | 3125 | 0 | 0 |
| `docs/V2_4_0_BETA9_ACCEPTANCE.md` | historical-release-record | 12973 | 1 | 1 |
| `docs/V2_4_0_BETA9_RELEASE_NOTES.md` | historical-release-record | 4129 | 0 | 0 |
| `docs/V2_4_0_RC1_ACCEPTANCE.md` | historical-release-record | 11765 | 1 | 1 |
| `docs/V2_4_0_RC1_RELEASE_NOTES.md` | historical-release-record | 5583 | 0 | 0 |
| `docs/V2_4_1_BETA10_ACCEPTANCE.md` | historical-release-record | 9377 | 2 | 2 |
| `docs/V2_4_1_BETA10_RELEASE_NOTES.md` | historical-release-record | 6655 | 1 | 1 |
| `docs/V2_4_1_BETA11_ACCEPTANCE.md` | historical-release-record | 6446 | 1 | 1 |
| `docs/V2_4_1_BETA11_RELEASE_NOTES.md` | historical-release-record | 5483 | 2 | 2 |
| `docs/V2_4_1_BETA12_ACCEPTANCE.md` | historical-release-record | 8001 | 2 | 2 |
| `docs/V2_4_1_BETA12_RELEASE_NOTES.md` | historical-release-record | 4724 | 1 | 1 |
| `docs/V2_4_1_BETA13_ACCEPTANCE.md` | historical-release-record | 5876 | 2 | 2 |
| `docs/V2_4_1_BETA13_RELEASE_NOTES.md` | historical-release-record | 4073 | 3 | 3 |
| `docs/V2_4_1_BETA14_ACCEPTANCE.md` | historical-release-record | 7366 | 2 | 2 |
| `docs/V2_4_1_BETA14_RELEASE_NOTES.md` | historical-release-record | 3922 | 2 | 3 |
| `docs/V2_4_1_BETA15_ACCEPTANCE.md` | historical-release-record | 13014 | 2 | 2 |
| `docs/V2_4_1_BETA15_RELEASE_NOTES.md` | historical-release-record | 6658 | 2 | 3 |
| `docs/V2_4_1_BETA16_ACCEPTANCE.md` | historical-release-record | 9948 | 1 | 1 |
| `docs/V2_4_1_BETA16_RELEASE_NOTES.md` | historical-release-record | 11172 | 1 | 1 |
| `docs/V2_4_1_BETA17_ACCEPTANCE.md` | historical-release-record | 10655 | 1 | 2 |
| `docs/V2_4_1_BETA17_RELEASE_NOTES.md` | historical-release-record | 9646 | 1 | 2 |
| `docs/V2_4_1_BETA18_ACCEPTANCE.md` | historical-release-record | 12601 | 3 | 5 |
| `docs/V2_4_1_BETA18_RELEASE_NOTES.md` | historical-release-record | 5545 | 2 | 3 |
| `docs/V2_4_1_BETA19_ACCEPTANCE.md` | historical-release-record | 12919 | 1 | 2 |
| `docs/V2_4_1_BETA19_RELEASE_NOTES.md` | historical-release-record | 9438 | 1 | 2 |
| `docs/V2_4_1_BETA1_ACCEPTANCE.md` | historical-release-record | 9580 | 2 | 2 |
| `docs/V2_4_1_BETA1_RELEASE_NOTES.md` | historical-release-record | 5296 | 1 | 1 |
| `docs/V2_4_1_BETA20_ACCEPTANCE.md` | historical-release-record | 17075 | 3 | 4 |
| `docs/V2_4_1_BETA20_RELEASE_NOTES.md` | historical-release-record | 11347 | 3 | 4 |
| `docs/V2_4_1_BETA21_ACCEPTANCE.md` | historical-release-record | 9181 | 3 | 5 |
| `docs/V2_4_1_BETA21_RELEASE_NOTES.md` | historical-release-record | 5506 | 4 | 6 |
| `docs/V2_4_1_BETA22_ACCEPTANCE.md` | historical-release-record | 8530 | 2 | 3 |
| `docs/V2_4_1_BETA22_RELEASE_NOTES.md` | historical-release-record | 8307 | 3 | 5 |
| `docs/V2_4_1_BETA23_ACCEPTANCE.md` | historical-release-record | 12294 | 2 | 3 |
| `docs/V2_4_1_BETA23_RELEASE_NOTES.md` | historical-release-record | 8860 | 2 | 3 |
| `docs/V2_4_1_BETA24_ACCEPTANCE.md` | historical-release-record | 7595 | 2 | 3 |
| `docs/V2_4_1_BETA24_RELEASE_NOTES.md` | historical-release-record | 7141 | 2 | 3 |
| `docs/V2_4_1_BETA25_ACCEPTANCE.md` | historical-release-record | 6463 | 2 | 3 |
| `docs/V2_4_1_BETA25_RELEASE_NOTES.md` | historical-release-record | 6576 | 2 | 3 |
| `docs/V2_4_1_BETA26_ACCEPTANCE.md` | historical-release-record | 5310 | 2 | 3 |
| `docs/V2_4_1_BETA26_RELEASE_NOTES.md` | historical-release-record | 4407 | 2 | 3 |
| `docs/V2_4_1_BETA27_ACCEPTANCE.md` | historical-release-record | 11082 | 2 | 3 |
| `docs/V2_4_1_BETA27_RELEASE_NOTES.md` | historical-release-record | 9036 | 2 | 3 |
| `docs/V2_4_1_BETA28_ACCEPTANCE.md` | historical-release-record | 15209 | 2 | 3 |
| `docs/V2_4_1_BETA28_RELEASE_NOTES.md` | historical-release-record | 9489 | 2 | 3 |
| `docs/V2_4_1_BETA29_ACCEPTANCE.md` | historical-release-record | 12602 | 3 | 4 |
| `docs/V2_4_1_BETA29_RELEASE_NOTES.md` | historical-release-record | 13371 | 2 | 3 |
| `docs/V2_4_1_BETA2_ACCEPTANCE.md` | historical-release-record | 8720 | 1 | 1 |
| `docs/V2_4_1_BETA2_RELEASE_NOTES.md` | historical-release-record | 4293 | 0 | 0 |
| `docs/V2_4_1_BETA30_ACCEPTANCE.md` | historical-release-record | 11954 | 3 | 5 |
| `docs/V2_4_1_BETA30_RELEASE_NOTES.md` | historical-release-record | 4737 | 2 | 3 |
| `docs/V2_4_1_BETA31_ACCEPTANCE.md` | historical-release-record | 8747 | 3 | 4 |
| `docs/V2_4_1_BETA31_RELEASE_NOTES.md` | historical-release-record | 5099 | 2 | 3 |
| `docs/V2_4_1_BETA32_ACCEPTANCE.md` | historical-release-record | 11106 | 2 | 3 |
| `docs/V2_4_1_BETA32_RELEASE_NOTES.md` | historical-release-record | 5192 | 2 | 3 |
| `docs/V2_4_1_BETA33_ACCEPTANCE.md` | historical-release-record | 30857 | 2 | 3 |
| `docs/V2_4_1_BETA33_RELEASE_NOTES.md` | historical-release-record | 29761 | 2 | 3 |
| `docs/V2_4_1_BETA34_ACCEPTANCE.md` | historical-release-record | 11584 | 2 | 3 |
| `docs/V2_4_1_BETA34_RELEASE_NOTES.md` | historical-release-record | 7585 | 2 | 3 |
| `docs/V2_4_1_BETA35_ACCEPTANCE.md` | historical-release-record | 12628 | 3 | 5 |
| `docs/V2_4_1_BETA35_RELEASE_NOTES.md` | historical-release-record | 7950 | 2 | 3 |
| `docs/V2_4_1_BETA36_ACCEPTANCE.md` | historical-release-record | 6903 | 3 | 5 |
| `docs/V2_4_1_BETA36_RELEASE_NOTES.md` | historical-release-record | 4357 | 2 | 3 |
| `docs/V2_4_1_BETA37_ACCEPTANCE.md` | historical-release-record | 12499 | 2 | 3 |
| `docs/V2_4_1_BETA37_RELEASE_NOTES.md` | historical-release-record | 9369 | 2 | 3 |
| `docs/V2_4_1_BETA38_ACCEPTANCE.md` | historical-release-record | 15244 | 4 | 7 |
| `docs/V2_4_1_BETA38_RELEASE_NOTES.md` | historical-release-record | 6526 | 2 | 3 |
| `docs/V2_4_1_BETA39_ACCEPTANCE.md` | historical-release-record | 11120 | 3 | 5 |
| `docs/V2_4_1_BETA39_RELEASE_NOTES.md` | historical-release-record | 7428 | 2 | 3 |
| `docs/V2_4_1_BETA3_ACCEPTANCE.md` | historical-release-record | 11065 | 2 | 2 |
| `docs/V2_4_1_BETA3_RELEASE_NOTES.md` | historical-release-record | 5038 | 0 | 0 |
| `docs/V2_4_1_BETA40_ACCEPTANCE.md` | historical-release-record | 7548 | 4 | 6 |
| `docs/V2_4_1_BETA40_RELEASE_NOTES.md` | historical-release-record | 5432 | 2 | 3 |
| `docs/V2_4_1_BETA41_ACCEPTANCE.md` | historical-release-record | 12808 | 2 | 3 |
| `docs/V2_4_1_BETA41_RELEASE_NOTES.md` | historical-release-record | 7699 | 2 | 3 |
| `docs/V2_4_1_BETA42_ACCEPTANCE.md` | historical-release-record | 11935 | 3 | 5 |
| `docs/V2_4_1_BETA42_RELEASE_NOTES.md` | historical-release-record | 4410 | 2 | 3 |
| `docs/V2_4_1_BETA43_ACCEPTANCE.md` | historical-release-record | 23619 | 3 | 5 |
| `docs/V2_4_1_BETA43_RELEASE_NOTES.md` | historical-release-record | 8008 | 2 | 3 |
| `docs/V2_4_1_BETA44_ACCEPTANCE.md` | historical-release-record | 11778 | 2 | 3 |
| `docs/V2_4_1_BETA44_RELEASE_NOTES.md` | historical-release-record | 5574 | 2 | 3 |
| `docs/V2_4_1_BETA45_ACCEPTANCE.md` | historical-release-record | 13666 | 2 | 3 |
| `docs/V2_4_1_BETA45_RELEASE_NOTES.md` | historical-release-record | 7559 | 2 | 3 |
| `docs/V2_4_1_BETA46_ACCEPTANCE.md` | historical-release-record | 13512 | 2 | 3 |
| `docs/V2_4_1_BETA46_RELEASE_NOTES.md` | historical-release-record | 8032 | 2 | 3 |
| `docs/V2_4_1_BETA47_ACCEPTANCE.md` | historical-release-record | 4347 | 2 | 3 |
| `docs/V2_4_1_BETA47_RELEASE_NOTES.md` | historical-release-record | 5818 | 2 | 3 |
| `docs/V2_4_1_BETA48_ACCEPTANCE.md` | historical-release-record | 10733 | 2 | 3 |
| `docs/V2_4_1_BETA48_RELEASE_NOTES.md` | historical-release-record | 6833 | 2 | 3 |
| `docs/V2_4_1_BETA49_ACCEPTANCE.md` | historical-release-record | 4371 | 2 | 3 |
| `docs/V2_4_1_BETA49_RELEASE_NOTES.md` | historical-release-record | 3826 | 2 | 3 |
| `docs/V2_4_1_BETA4_ACCEPTANCE.md` | historical-release-record | 5141 | 1 | 1 |
| `docs/V2_4_1_BETA4_RELEASE_NOTES.md` | historical-release-record | 3866 | 0 | 0 |
| `docs/V2_4_1_BETA50_ACCEPTANCE.md` | historical-release-record | 10080 | 2 | 3 |
| `docs/V2_4_1_BETA50_RELEASE_NOTES.md` | historical-release-record | 5744 | 2 | 3 |
| `docs/V2_4_1_BETA51_ACCEPTANCE.md` | historical-release-record | 3541 | 2 | 3 |
| `docs/V2_4_1_BETA51_RELEASE_NOTES.md` | historical-release-record | 2904 | 2 | 3 |
| `docs/V2_4_1_BETA52_ACCEPTANCE.md` | historical-release-record | 6853 | 2 | 3 |
| `docs/V2_4_1_BETA52_RELEASE_NOTES.md` | historical-release-record | 7198 | 2 | 3 |
| `docs/V2_4_1_BETA53_ACCEPTANCE.md` | historical-release-record | 14571 | 3 | 4 |
| `docs/V2_4_1_BETA53_RELEASE_NOTES.md` | historical-release-record | 9054 | 3 | 4 |
| `docs/V2_4_1_BETA54_ACCEPTANCE.md` | historical-release-record | 7119 | 2 | 3 |
| `docs/V2_4_1_BETA54_RELEASE_NOTES.md` | historical-release-record | 6236 | 2 | 3 |
| `docs/V2_4_1_BETA55_ACCEPTANCE.md` | historical-release-record | 13183 | 2 | 3 |
| `docs/V2_4_1_BETA55_RELEASE_NOTES.md` | historical-release-record | 6004 | 2 | 3 |
| `docs/V2_4_1_BETA56_ACCEPTANCE.md` | historical-release-record | 11274 | 2 | 3 |
| `docs/V2_4_1_BETA56_RELEASE_NOTES.md` | historical-release-record | 8718 | 2 | 3 |
| `docs/V2_4_1_BETA57_ACCEPTANCE.md` | historical-release-record | 10088 | 2 | 3 |
| `docs/V2_4_1_BETA57_RELEASE_NOTES.md` | historical-release-record | 6287 | 2 | 3 |
| `docs/V2_4_1_BETA58_RELEASE_NOTES.md` | historical-release-record | 2730 | 2 | 3 |
| `docs/V2_4_1_BETA59_ACCEPTANCE.md` | historical-release-record | 6105 | 2 | 3 |
| `docs/V2_4_1_BETA59_RELEASE_NOTES.md` | historical-release-record | 5725 | 2 | 3 |
| `docs/V2_4_1_BETA5_ACCEPTANCE.md` | historical-release-record | 4668 | 1 | 1 |
| `docs/V2_4_1_BETA5_RELEASE_NOTES.md` | historical-release-record | 2457 | 0 | 0 |
| `docs/V2_4_1_BETA60_ACCEPTANCE.md` | historical-release-record | 5736 | 2 | 3 |
| `docs/V2_4_1_BETA60_RELEASE_NOTES.md` | historical-release-record | 5055 | 2 | 3 |
| `docs/V2_4_1_BETA61_ACCEPTANCE.md` | historical-release-record | 10369 | 2 | 3 |
| `docs/V2_4_1_BETA61_RELEASE_NOTES.md` | historical-release-record | 10470 | 2 | 3 |
| `docs/V2_4_1_BETA62_ACCEPTANCE.md` | historical-release-record | 6515 | 2 | 3 |
| `docs/V2_4_1_BETA62_RELEASE_NOTES.md` | historical-release-record | 6618 | 2 | 3 |
| `docs/V2_4_1_BETA6_ACCEPTANCE.md` | historical-release-record | 7655 | 2 | 2 |
| `docs/V2_4_1_BETA6_RELEASE_NOTES.md` | historical-release-record | 3774 | 0 | 0 |
| `docs/V2_4_1_BETA7_ACCEPTANCE.md` | historical-release-record | 8970 | 1 | 1 |
| `docs/V2_4_1_BETA7_RELEASE_NOTES.md` | historical-release-record | 5111 | 0 | 0 |
| `docs/V2_4_1_BETA8_ACCEPTANCE.md` | historical-release-record | 8796 | 2 | 2 |
| `docs/V2_4_1_BETA8_RELEASE_NOTES.md` | historical-release-record | 5577 | 0 | 0 |
| `docs/V2_4_1_BETA9_ACCEPTANCE.md` | historical-release-record | 7790 | 3 | 3 |
| `docs/V2_4_1_BETA9_RELEASE_NOTES.md` | historical-release-record | 3807 | 1 | 1 |
| `docs/V2_4_1_BETA58_ACCEPTANCE.md` | historical-release-record (stale items) | 5170 | 3 | 4 |
| `README.md` | readme (narrates betas) | 64147 | 43 | 70 |
| `docs/2_1_ROADMAP.md` | stale-guidance | 27901 | 4 | 5 |
| `docs/BETA_DEPLOYMENT.md` | stale-guidance | 36782 | 5 | 7 |

</details>
