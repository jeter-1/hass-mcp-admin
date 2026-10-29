# Engineering 2.4.1-beta.46 acceptance

This contract covers HAMCP-247: `list_capabilities` returns usable data for
the unfiltered call and every documented filter within the response cap. It
follows 2.4.1-beta.45 and includes it.

See also:
- Release notes:
  [`V2_4_1_BETA46_RELEASE_NOTES.md`](V2_4_1_BETA46_RELEASE_NOTES.md).
- The tool entry: [`TOOL_AUDIT.md`](../TOOL_AUDIT.md#list_capabilities).

**Live findings (beta.44, 2026-10-29).** With no filter, `list_capabilities`
succeeded with `/data` omitted (96,902 characters against the 60,000 limit)
and an empty `response_completeness.retrieval`. `status=delegated` did the
same (91,274 characters). `category=foundation` worked.

**Diagnosis (no STOP).** A filter narrowed only the canonical, planned and
dynamic upstream lists; `beta_native`, `provider_matrix` and the gateway
snapshot (~66k together) were always returned in full. Over the limit, the
shared envelope (`models/responses.py`) drops the largest optional subtree,
`/data`, and its retrieval only knows plan and task identifiers. The fix is a
tool-local bounded projection; the global cap and the envelope's truncation
mechanism are unchanged. Filter semantics in the description are not
ambiguous: "category is unchanged" is read as the category matching
semantics (exact, trimmed, case-insensitive) being unchanged, and a filter
now narrows every section it applies to.

**Release target.** `2.4.1-beta.46` is the next free beta after `main` at
2.4.1-beta.45 (#322, HAMCP-235). If another release reaches `main` first,
this change is restaged at the next free beta and these documents are
renamed.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- the HAMCP-247 scope (the `list_capabilities` summary view, filter
  narrowing, the optional `detail_section`, the `sections` map and the size
  statement; no other change);
- `2.4.1-beta.46` materialized in all three version authorities, with exact
  acceptance and release-note resolution from `scripts/codex-context.py` and
  no staged declaration left over;
- the PR head SHA;
- the passing PR `validate` run for that exact head, with both Real HA jobs;
- the Evidence tier log named in the PR;
- the mutation evidence `docs/evidence/hamcp247-mutations/results.json`.

The review needs a security section covering:
- **No new reads, writes or dispatch.** `list_capabilities` stays a
  Core-independent, in-process read of the Engineering catalog. No Home
  Assistant call, provider, route, upstream dispatch or write is added.
- **Bounded output.** The tool's data is sized on the compact rendering
  against the response limit less a 4,000-character envelope reserve. The
  tool never relies on the envelope's truncation. The global cap and
  `dump_json_with_truncation` are unchanged.
- **Nothing silently dropped.** Every omission is named in `size_limit`
  and `sections` with a request that returns it, or, for a class that both
  filters already pin, `retrieval_unavailable_reason: "no_narrower_filter"`.
- **Input.** `detail_section` is a closed set; an unknown value is refused as
  `invalid_request` (`unknown_section`, allowed values listed). No input
  reaches anything but the in-process projection.
- **Disclosure unchanged.** The same catalog fields are published; the
  gateway rows are the published health snapshot, narrowed. Nothing new is
  read from the gateway or the governance service.

Preserve the following:
- stable v1.1.2;
- every other input schema, tool and descriptor, and the catalog count;
- routing, providers, policy, governance, the Core gate and telemetry;
- probe fingerprints, the signed Core capability profiles and both signed
  registries;
- every workflow and dependency. `config.yaml` changes only its version.

### Offline proof

No test reaches a live system. Every value is synthetic.

- `tests/test_hamcp247_list_capabilities_bounded.py`, on a live-shaped
  catalog: the real upstream read gateway driven with the ha-mcp 8.6.0
  capture (105 registered tools; the fixture's unfiltered and
  `status=delegated` catalogs exceed the cap the way beta.44 did):
  - **Size.** The unfiltered call, every status, every documented and
    every extra category, every section, and every section with
    `status=delegated` and with `category=foundation`, through the real tool
    and envelope: under the cap, success, no `response_completeness`, at
    live size and at +50% growth (every row and gateway list). At live size
    every documented view and every section is complete.
  - **Summary.** Identity, version and count fields, canonical tools, a
    `tool_index` of every registered tool, and the gateway's counts.
  - **Filter semantics.** A status filter narrows `beta_native`,
    `dynamic_upstream`, `provider_matrix` and the gateway detail;
    `delegated` keeps exactly the delegated tools' matrix and gateway rows,
    with the unfiltered totals stated; category matching is unchanged
    (trimmed, case-insensitive, unknown value empty); filters combine as a
    conjunction; filtered responses keep the established keys; the no-
    argument catalog builder is unchanged.
  - **Section selector.** Each section in full; filters still apply; the
    name is normalized; an unknown section (including `planned`,
    `tool_index` and a misspelling) is refused clearly; every advertised
    request returns its section.
  - **Size limit.** Following `size_limit.retrieval` from the summary
    under a forced small limit recovers every tool, beta-native, dynamic and
    matrix item, at live size and at +50%. An oversize section names its
    partitions; a category-filtered one partitions by status; an oversize
    gateway drops its largest per-tool fields and says so; a gateway whose
    identity cannot fit is omitted with retrieval; a class with both
    filters set says `no_narrower_filter`; no single status-and-category
    class can outgrow the real cap; the tool never reaches the envelope's
    truncation.
  - **Size guard.** At limits derived from the measured sizes: the
    provider matrix goes first and alone when that suffices; dropping the
    tool index names all three tool-list requests; an oversize gateway
    drops its largest field first and then the nested readmission list;
    its retrieval is never the same call; and stepping through every limit
    above the floor, a section response always fits (the `sections` entry
    is marked before sizing).
  - **Gateway projection** on synthetic rows: string, dict, keyed and
    nested readmission rows are narrowed by any naming key (`tool`,
    `upstream_name`, `exposed_name`); the summary drops every detail list;
    the published snapshot is never changed.
- Updated: `test_beta_v2` (stable-v1 parity allows exactly the additive
  `detail_section`; the catalog test reads its sections with
  `detail_section`), `test_hamcp181_catalog_core_admission`, and the
  historical schema pins in `test_rc1_compatibility` and
  `test_change_impact_analysis`, which remove exactly the reviewed addition
  (`tests/hamcp247_descriptor_changes.py`) so every other byte stays pinned.
- Mutations: `docs/evidence/hamcp247-mutations/` (a filter not narrowing a
  section, the gateway projection weakened, the selector or summary
  weakened, the budget or size guard weakened, an oversize section
  reported untruthfully or without retrieval, the entry point not passing
  the cap or the section).

## 2. Release CI and publication

Require exact-head CI, both Real HA jobs included, before release
acceptance. Pending or unavailable jobs are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the merged source, the release tag, the immutable version
and commit images, provenance and the SBOM with the shipped verifiers. Never
invent future image identities. Keep the beta.45 artifacts and the
known-good earlier artifacts for recovery.

Nothing is stored. **Rollback is reinstalling the 2.4.1-beta.45
artifact.**

## 3. Installed Engineering

Installation is separately authorized. Every check after step 1 is
read-only. Record each result in HAMCP-247.

1. **The add-on starts after the update (first).** Confirm the Engineering
   add-on reaches `started` and its MCP endpoint answers. If it does not,
   stop and reinstall 2.4.1-beta.45.
2. **Installed-image binding.** Bind the running add-on to the published
   image index digest, the release tag and the protected `main` merge
   commit.
3. **Refresh the connector.** Start a fresh client session so the new
   `list_capabilities` schema is loaded. Re-read `tools/list`: the tool
   count equals beta.45's, and `list_capabilities` lists the optional
   `detail_section`.
4. **Unfiltered call (required).** `list_capabilities` with no argument:
   `success: true`, `/data` present, no `response_completeness`, length
   under 60,000. `view` is `summary`; `server.version` is `2.4.1-beta.46`;
   `sections` lists all five sections with a `request` each.
5. **`status=delegated` (required).** `/data` present, under the cap,
   `view: "filtered"`; `dynamic_upstream` lists the delegated tools and
   `provider_matrix` and the gateway rows name only them.
6. **`category=foundation` (required).** `/data` present, under the cap;
   every listed tool has category `foundation`.
7. **One section call (required).** Call one `request` from step 4's
   `sections` map, for example `detail_section=provider_matrix`: `/data`
   present, under the cap, and the section is `full` or, if
   `omitted_size_limit`, its `retrieval` calls each return data.
8. **Record** each call's response length and `size_limit.omitted_sections`.
   If any call returns `/data` omitted, stop and record it.

## 4. Not covered

No Home Assistant read, write, Core command, live canary, registry signing or
Supervisor role change is part of this release. The global response cap and
the envelope's truncation are unchanged; other tools are not affected. A
status-and-category class that alone exceeds the cap has no narrower call
(`no_narrower_filter`); none does on the live-shaped catalog, and at the
real cap even the whole 77-tool ha-mcp 8.6.0 upstream fits in one class.
The smallest record an oversize section can return (its item names and
narrower calls, about 2–5k characters on this catalog) is the tool's floor:
under a limit too small for it, the tool cannot fit and the envelope's own
bound applies. The floor is reachable only in tests with a forced small
limit, never at the real cap.
