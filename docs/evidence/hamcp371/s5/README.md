# HAMCP-371 step S5: image inventory for the disposable-Core capture lane

Local evidence, scriptbox, 2026-11-11 (Central). Produced by
`scripts/core_read_replay_capture_local.py inventory`, launched through the
host heavy gate. The inventory creates one container per bound image, reads
`docker export` as a stream and removes the container. **No container was
started, nothing was pulled (`--pull never`), nothing was extracted to disk and
no Home Assistant was contacted.** Everything read from an image is data.

Images (all cached, re-checked against their bound digests by the harness):
Core 2026.11.0, ha-mcp 9.1.2, ha-mcp 9.2.0. Digests are in each `receipt.json`
under `bindings` and `images`.

## Launches

| Launch | Harness commit | Started - ended (Central) | Result | Gate before launch |
| --- | --- | --- | --- | --- |
| inventory-1 | 0ccde47b | 23:34:32 - 23:36:31 | PASS | see `gate/inventory-1.txt` |
| inventory-2 | fa8b7e40 (scan change c14c5369) | 23:42:19 - 23:43:38 | PASS | heavy_total=2 |
| inventory-3 | 68d87e10 | 23:47:53 - 23:48:54 | PASS | heavy_total=1 |

`launches.jsonl` is the harness launch log; `gate/` holds the gate receipts.

Why three launches, none of them a re-run for a pass:

- inventory-1 passed. Its ha-mcp distribution list (72 names) had no `fastmcp`,
  `mcp` or `websockets`, and the scan counted only regular-file `METADATA`
  members, so an undercount through hard-link members was suspected.
- inventory-2 ran after a tracked harness change with tests (c14c5369) that
  counts hard-link members and lists any `dist-info` directory seen without a
  counted `METADATA` file. Result: 0 hard-link members and no uncounted
  `dist-info` directory in any of the three images. The suspicion was wrong;
  the inventory-1 counts stand (72, 72 and 1659 distributions). `fastmcp`,
  `mcp` and `websockets` are not installed distributions in either ha-mcp
  image. Where the image carries them was not inspected (file paths under
  `/app/src/ha_mcp/` are counted, not listed).
- inventory-3 ran after a second tracked change with tests (68d87e10) that
  reads the `Requires-Dist` rows of the `ha-mcp` distribution's `METADATA`
  member and compares the names with the Core image.

## Findings

1. Core 2026.11.0 runs Python 3.14. The ha-mcp 9.1.2 image runs 3.13 and the
   9.2.0 image runs 3.14.
2. The `ha_mcp_tools` component tree is in neither ha-mcp image
   (`component_tree_in_image` is empty for both). The lane takes it from the
   local upstream clone at the bound source commit (`git archive`, tree only).
3. The component manifest requires `mcp>=1.24.0`, `ruamel.yaml>=0.18.0`,
   `voluptuous-openapi>=0.4.1` and `ha-mcp==<version>` (step S2). The Core
   image carries `mcp 2.3.0`. It carries **neither `ruamel.yaml` nor
   `voluptuous-openapi`**. Neither ha-mcp image carries them either.
4. The `ha-mcp` distribution declares 39 requirement rows on both versions.
   Ten of the unconditional ones are not in the Core image by name:
   `cyclopts`, `griffelib`, `jsonref`, `jsonschema-path`, `openapi-pydantic`,
   `py-key-value-aio`, `pydantic-monty` (pinned `==0.0.18`), `pyperclip`,
   `uncalled-for`, `watchfiles`. (`pywin32` is also listed as absent by the
   report; its row applies to Windows only. The report skips only rows that
   apply to an extra.) Specifiers are recorded and were not evaluated. All ten
   are installed distributions in both ha-mcp images, built for those images'
   interpreters; whether they would load in the Core image was not tested.
   Which of them the component's code path imports was not determined.

## Consequence (pending Josh)

The 22:59 record, item 4, reads: "N28: supply ha-mcp==9.2.0 for the second
capture profile from the pinned image; no network route. Report if that proves
infeasible."

As things stand the second capture profile cannot be set up under that ruling:
two manifest requirements are in neither the Core image nor the pinned image,
and ten declared requirements of `ha-mcp` itself are absent from the Core
image. The harness refuses the profile with `profile_b_core_requirement_missing`
and adds no route. Nothing was fetched. Whether, and from where, those
distributions may be supplied to the disposable Core is an open question for
the owner; the replay conditions on the YAML read (which goes through the
component's services) wait on the same answer.

The S4 resource inventory used the same regular-file test for its distribution
list; inventory-2 shows that test did not undercount.
