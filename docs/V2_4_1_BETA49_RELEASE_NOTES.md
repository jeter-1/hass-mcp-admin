# Engineering 2.4.1-beta.49 release notes

Engineering 2.4.1-beta.49 explains the health warnings that stayed lit
(HAMCP-198, HAMCP-120). It follows 2.4.1-beta.48 and includes it. It adds no
Home Assistant write, tool, input schema, route, provider, Core command or
signed registry change. It does not change approval, execution, dispatch,
locks, policy decisions or stored formats.

Acceptance: [`V2_4_1_BETA49_ACCEPTANCE.md`](V2_4_1_BETA49_ACCEPTANCE.md).

## Why

Health on 2.4.1-beta.46 showed three warnings that never cleared:

- `governance.projection_failure_count: 2` with
  `one_or_more_governance_plans_could_not_be_projected`. The two plans fit
  none of the five reviewed historical profiles. Health gave a count but no
  plan ID, so the records could not be retrieved or classified.
- `upstream_read_gateway.reviewed_tool_accounting_valid: false` (77 policy
  entries, 80 advertised tools) while
  `optional_reviewed_accounting_valid` was true. The three extra tools are
  the HAMCP-162 optional reviewed tools; nothing was unreviewed.
- `execution_tasks.tasks_manual_review: 1` and
  `manual_review_terminalization_count: 1`, with one
  `restart_home_assistant` post-dispatch and verification failure.

## The change

**Projection failures are identified.** `governance` adds
`projection_failures` (at most eight `{plan_id, category}` rows, ordered by
plan ID), `projection_failures_truncated` and
`projection_failure_category_model`
(`hamcp198-projection-failure-category-v1`). The category names the first
failing check from a fixed vocabulary; see
[OBSERVABILITY](../hass_mcp_engineering_beta/OBSERVABILITY.md). Rows contain
no record contents. The count and warning are unchanged, so the warning stays
lit while the plans fail.

**Accounting uses the optional reviewed-tool model.**
`reviewed_tool_accounting_valid` is now true when every advertised tool is a
stock policy entry or a bound optional reviewed tool and none is unreviewed.
This supersedes HAMCP-162 design decision D3's "meaning unchanged" clause by
Josh's direction. An unaccounted or unreviewed advertised tool keeps it
false.

## Not changed

- **The two projection failures stay failures.** Which class they are
  (current defect or immutable historical record) cannot be determined from
  source. No historical profile is added in this release. After deploy, the
  categories and plan IDs let the architect retrieve the two records
  read-only for that decision.
- **Manual review stays counted.** `manual_review_required` is terminal and
  no stored field or supported action separates open from reviewed cases.
  Clearing it needs an operator acknowledgment workflow, which is an open
  design decision. Task history is not rewritten or dismissed.
- Projection outcomes, reads, approval, apply, rollback, recovery and every
  refusal behave as before. Every field is additive. Static tools, the
  advertised catalog and input schemas are unchanged.
- `governance_health` timing on the 200-plan test fixture with two failures:
  medians 156–170 ms before, 142–152 ms after (no slowdown; diagnosis
  examines at most eight plans).

## Rollback

Reinstall 2.4.1-beta.48. The added fields are ignored by the older reader,
the accounting flag returns to its stock meaning, and no stored record
changes shape.
