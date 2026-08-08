# HA MCP Engineering 2.2.0-beta.26 plan-store scaling acceptance

## Release boundary

- baseline: resolve and record the exact current `origin/main` SHA in the PR;
- published Engineering version: `2.2.0-beta.25`;
- staged version: `2.2.0-beta.26`;
- upstream: exact reviewed `ha-mcp` 8.1.1;
- Home Assistant lanes: current 2026.8 compatibility fixtures and exact image;
- stable version: `1.1.2`; and
- merge, publication, deployment, and live Home Assistant access are excluded.

## Source and deterministic acceptance

Require all of the following before merge:

- the former full-history projection path is reproduced and shown to
  deserialize all retained plans before a one-record limit;
- fixtures with 130, 1,000, and 10,000 terminal plans hold active approval and
  recovery work constant;
- `list_change_plans(limit=1)` enumerates and validates one requested record;
- approval inventory and detail each load only the active/requested review;
- task and operational recovery inspect only nonterminal/recoverable work;
- repeated health polling deserializes no terminal history;
- work counters remain constant across all three terminal-history sizes;
- startup rebuild equals uninterrupted derived state;
- active-set corruption rebuilds, expired approval disappears atomically, and
  explicit deep audit detects/quarantines historical tampering;
- direct persisted authority, cross-process task uniqueness, idempotency,
  append-only history, locking, and crash/fault behavior remain fail closed;
- Beta 11 restart gates, Beta 22 complete review projection, Beta 20 F3,
  Beta 25 provider/catalog behavior, Dashboard, and zero fallback remain green;
- stable v1.1.2 and every public tool schema remain unchanged; and
- Full, Evidence, exact-image, and both Home Assistant compatibility lanes pass.

Wall-clock results must be reported for diagnosis but are not CI pass/fail
thresholds. Deterministic enumeration/deserialization/terminal-touch counters
are the release gate.

## Later deployment acceptance

After separate merge, promotion, publication, and deployment authorization,
verify exact Beta 26 build provenance. With no active recovery work, call
`list_change_plans(limit=1)`, approval inbox/detail, and health repeatedly.
Compare `plan_store_scaling.hot_paths` with the Beta 25 evidence: list must
enumerate one record, approval paths only their active/requested records,
recovery zero terminal records, and cached health zero terminal records.

Observe CPU across several recovery intervals. The prior approximately
30-second recurring terminal-history sweep must be absent. Confirm Beta 11
provider-probe counters, Beta 22 approval completeness, F3 state, exact 8.1.1
admission, 25 delegated reads, one held read, 74 total tools, Dashboard, and
fallback `none` are unchanged. Do not infer deployment provenance from the
advertised version alone.

Hold or roll forward if authority validation weakens, an active item disappears
after restart, a corrupt record becomes actionable, catalog/provider accounting
changes, or retained terminal history still drives routine latency or idle CPU.
