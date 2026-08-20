# Promotion regression manifest

Consolidated Deficiency Register (HAMCP-089) item #22.

Live regression testing has been performed twice against the deployed
`2.2.0-beta.39` server, both times by hand, in conversation, with the results
reported and then lost to scrollback. Nothing stopped a future beta from
silently regressing a capability that a previous beta had already proven. This
directory turns that ad-hoc process into a versioned artifact so every future
promotion runs the same fixed checks.

## What is here

| File | Purpose |
| --- | --- |
| `promotion_regression_manifest.yaml` | The sentinels. Data, not code: each entry's expected status is reviewable in a diff. |
| `manifest_schema.json` | JSON Schema the manifest must satisfy. |
| `../scripts/promotion_regression_check.py` | The checker. Classifies a recorded capture against the manifest. |
| `../tests/test_promotion_regression_manifest.py` | Offline proof that the manifest is well formed and the classification logic is correct. |
| `../tests/fixtures/promotion_regression/` | A synthetic capture built from the values the register recorded. Not a production record. |

## The checker does not talk to anything

The register requires the promotion checks to be read-only against the live
target. Rather than promise that, the checker is built so it cannot be
otherwise: it has no transport, no subprocess, and no file-writing code path at
all. Its only inputs are the manifest and a capture file, and its only output is
stdout. `tests/test_promotion_regression_manifest.py` asserts this structurally
by scanning the checker's own syntax tree.

The consequence is that a person, or an interactive agent session that already
holds live MCP access, performs the calls — the same way the two prior
regression passes were performed — and the checker does the arithmetic.

## Running it

Everything below is offline except step 2.

**1. See what to call.**

```bash
python scripts/promotion_regression_check.py plan
```

This prints each observation in order with its tool, arguments, effect class,
procedure, any operator-supplied input it needs, and the sentinels that depend
on it. Ten observations back twenty-five sentinels; `get_server_health` alone
backs thirteen, so do not re-call it per sentinel.

**2. Perform the calls against the deployed server and record the responses.**

Start from a skeleton:

```bash
python scripts/promotion_regression_check.py template > /path/outside/repo/capture.json
```

For each observation, put the tool's complete response under
`observations.<id>.response`. Either a decoded object or the raw JSON string the
tool returned is accepted. If an observation could not be performed, leave
`response` null and replace `not_recorded_reason` with why — that produces an
honest `NOT_CAPTURED`, not a silent pass.

Three observations need an operator-supplied identifier the repository
deliberately does not carry (a long `wait_template` automation id, the
stale-state canary task id, and the active compatibility entry id). The
manifest's `target_note` says where each comes from.

**Keep the capture outside the repository.** It contains instance data —
automation content, dashboard configuration, entity state. `promotion/captures/`
is gitignored as a safety net if you would rather keep it nearby, but nothing in
a capture belongs in a commit.

**3. Classify.**

```bash
python scripts/promotion_regression_check.py evaluate --capture /path/outside/repo/capture.json
```

Add `--format json` for machine-readable output.

## Classifications

| Outcome | Meaning | Blocks promotion? |
| --- | --- | --- |
| `CONFIRMED` | Expected to pass, and it passed. | No |
| `REGRESSION` | Expected to pass, but it failed. Previously accepted behavior broke. | **Yes** |
| `KNOWN_FAILING` | Expected to fail against a linked open deficiency, and it still fails. | No, but visible |
| `UNEXPECTED_PASS` | Expected to fail, but it passed. A deficiency may be fixed. | No — needs a human |
| `NOT_CAPTURED` | The check was not performed. The run is incomplete. | No, but the run is not a gate |

`REGRESSION` and `KNOWN_FAILING` are counted and printed separately and are
never merged. A run with three `KNOWN_FAILING` entries and no `REGRESSION` is a
clean run against today's known state.

`NOT_CAPTURED` is not one of the register's four outcomes. It exists because the
alternative is worse: an uncaptured check silently classified as either a pass or
a regression would make an incomplete run look like a verdict.

Exit codes:

| Code | Condition |
| --- | --- |
| 0 | Complete run, no `REGRESSION` |
| 1 | At least one `REGRESSION` — do not promote |
| 2 | No `REGRESSION`, but the run is incomplete |
| 3 | Usage error, unreadable input, or an invalid manifest |

## Flipping an `expected_fail` to `expected_pass`

The checker never changes a status. A beta is accepted when the full acceptance
chain passes, not because one checker run looked clean, so this is a human
decision by design.

When an `UNEXPECTED_PASS` appears:

1. Read the sentinel's `deficiency` block for the register item and the evidence
   originally observed.
2. Confirm the underlying fix is actually deployed and verified live — one
   passing observation is not a fix.
3. Edit that sentinel in `promotion_regression_manifest.yaml`: change
   `expected_status: expected_fail` to `expected_status: expected_pass` and
   delete its `deficiency` block. The schema requires a deficiency reference on
   every `expected_fail` entry and forbids one on `expected_pass`, so a
   half-finished flip fails validation.
4. Run `python scripts/promotion_regression_check.py validate` and the offline
   test module.
5. Commit the one-line flip with the evidence in the commit message.

Going the other way — `expected_pass` to `expected_fail` — is how a newly
accepted-as-broken behavior gets recorded, and it needs a register item and
observed evidence for the same reason.

## Updating for a new release

`target.build_sha` and the `runtime-build-provenance` sentinel pin the exact
promoted commit, and `runtime-server-version` pins the version. Update both in
the same commit that promotes a new release. A stale value here produces a
`REGRESSION`, which is the intended behavior for an image that cannot be tied to
reviewed source.

## Field paths

Paths are rooted at the recorded response. Engineering tools using the
structured envelope expose their payload under `data`; `get_automation_config`
returns a bare JSON object and its paths are rooted at the object itself. A path
may address a list element by index (`source_coverage.0`) or by selector
(`source_coverage[source_type=blueprint]`); an ambiguous selector is refused
rather than guessed.

Every path in the manifest was derived from `2.2.0-beta.39` runtime source. Two
of them differ from how the register phrased them, and the manifest notes the
difference where it matters:

- non-terminal execution accounting lives at
  `governance.execution_tasks.storage.navigation.nonterminal_record_count`, not
  `execution_tasks.navigation.nonterminal_record_count`;
- the dashboard provider has no `fallback_count`. It declares zero fallback per
  route, so `upstream-dashboard-zero-fallback` asserts
  `ordinary_dashboard_read_route.fallback`,
  `governed_dashboard_write_route.fallback`, and
  `governed_dashboard_write_route.direct_home_assistant_fallback` instead.

## The one observation that is not a pure read

`helper_no_change_probe` calls `create_helper_state_plan`. When the requested
state already matches the current state, the tool returns a verified no-change
result and creates no plan and dispatches nothing — which is exactly the
behavior being asserted. It is marked `effect_class: no_change_probe` rather than
`read_only` and carries a `precondition`: read the helper's current state first
and confirm it is already `off`. If it is not `off`, skip the observation and
record the reason. Requesting a state the helper does not hold would create a
change plan, which is a different operation with its own approval path.

Write-capable canaries stay out of this manifest entirely; the register requires
those to remain explicitly approved and separate.

## Not wired into CI

This deliberately stops at "runnable by a human or an agent session with live
access". Reaching a home Home Assistant instance from GitHub Actions would need
credential storage this project does not currently use, and that is a
security-relevant decision deserving its own scoping rather than a silent
addition here. **Recommended follow-up:** scope CI integration separately, as a
decision about secret management first and a workflow second.

A live run is the natural first real use of this manifest. It has not been
performed as part of the commit that introduced it.
