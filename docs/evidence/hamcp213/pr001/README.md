# HAMCP-213 PR001 worked example (anonymized)

Design evidence for `docs/HAMCP_213_SAFETY_CRITICAL_APPROVAL_DESIGN.md` D6.
Nothing here is runtime code, a runtime fixture, or a test the gate runs.

## Provenance

The eight `op{1..4}_{before,after}.yaml` files are **anonymized structural
transforms** of the owner's real PR001 example: the current and candidate
configurations of the four operations in change plan `7b651ef4…`, read from the
owner's Home Assistant (Core 2026.10.1) through the Engineering gateway
(2.4.1-beta.19) on 2026-10-20. They were supplied by the owner outside the repository.
The source file, the anonymizer, and the real-to-anonymized mapping are **not**
committed, because the repository is public.

The transform:

- renames every entity, notify service, trigger id, notification tag, action id,
  and category to a neutral name (for example `cover.garage_main`,
  `person.resident_a`, `notify.mobile_app_owner_phone`);
- replaces free text (`alias`, `description`, `message`, `title`, `name`, and
  stop reasons) with neutral text numbered per distinct source string, so text
  that was equal before and after stays equal and changed text stays changed;
- keeps `{{ … }}` template segments with the renamed identifiers, so templated
  text stays templated.

These properties were checked by machine before the fixtures were written:

- identical structure: the same keys, list lengths, non-string values
  (including `mode`, `continue_on_error`, timeouts and delays) and step order,
  so actions, conditions, waits, choose branches, repeat and modes are
  unchanged;
- the same set of changed paths before and after: op1 61, op2 102, op3 16,
  op4 22;
- the same service domains, the `garage` substring kept on every entity that
  had it, and no water-related terms, so the shipped substring classifiers see
  the same inputs;
- a privacy scan of the fixtures and of this directory, against the source's
  entity ids, names, notify services and device names plus 4-grams of its
  free text, found 0 violations. A positive control (scanning the source
  itself) found 218.

Fingerprints of the **source** configurations (the first 16 hex characters of
sha256 over canonical JSON; for the script, `category` is excluded) matched the
values the owner recorded. Those fingerprints apply to the originals, not to
these anonymized files:

| Op | Type | Before | After |
| --- | --- | --- | --- |
| op1 | automation | `fb0594f3b75dcc5d` | `50fc3a92c5612f20` |
| op2 | automation | `10e5784cdc3dd124` | `84f02282d479cdc9` |
| op3 | script | `01adc9e892480f8c` | `0b5d3211c36c2f87` |
| op4 | automation | `7f0500f465738950` | `b2b311c95660654e` |

## Files

- `probe_pr001.py` / `.out`: the **shipped** classifier and policy at
  origin/main `f69bf8c` (2.4.1-beta.24), run on the fixtures as one 4-operation
  plan. It shows today's behavior.
- `d2_reference_check.py` / `.out`: a **design-validation prototype** of the D2
  equivalence rules (not runtime and not a proposed implementation). It reports
  per-operation verdicts under the proposed rule and the conservative OD-10
  alternative, plus 7 mutations that must each turn the verdict to `changed`.

## Usage

Run from the repository root. This needs CPython 3.12 with
`hass_mcp_engineering_beta/requirements.lock` installed via `--require-hashes`.
The reference check needs only PyYAML.

```text
python -I docs/evidence/hamcp213/pr001/probe_pr001.py hass_mcp_engineering_beta docs/evidence/hamcp213/pr001
python -I docs/evidence/hamcp213/pr001/d2_reference_check.py docs/evidence/hamcp213/pr001
```

No live Home Assistant or MCP endpoint is contacted.
