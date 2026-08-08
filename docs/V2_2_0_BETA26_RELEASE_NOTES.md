# HA MCP Engineering 2.2.0-beta.26 plan-store scaling release notes

Beta 26 corrects the retained-plan scaling defect that caused approximately
six-second plan and approval reads and recurring idle recovery CPU in the
deployed Beta 25 runtime. It adds process-local, rebuildable navigation for
active plans, approval challenges, recovery work, and execution tasks while
keeping persisted records as the sole authorization and execution authority.

`list_change_plans` now pages before deserialization. Approval inventory loads
only active challenges, Ingress detail reloads one requested record, periodic
recovery loads only nonterminal/recoverable work, and repeated health polling
uses generation-bound aggregates. A deliberate startup or deep audit still
validates retained history. Corrupt authority continues to quarantine or fail
closed, and every approval, apply, rollback, and recovery action still reloads
and validates the exact persisted authority.

Deterministic fixtures hold one active challenge constant across 130, 1,000,
and 10,000 terminal plans. The former full-scan regression touches every
record. The corrected list touches one terminal record; approval inventory and
detail each touch one active record; idle recovery touches no terminal record;
and cached health polling touches none. Local wall time is evidence, not a
flaky CI threshold; record-touch counts are the deterministic acceptance gate.

The change adds bounded health evidence for enumeration, deserialization,
terminal touches, recovery candidates, index rebuild/update/invalidation,
cache behavior, and hot-path latency. It exposes no payloads or secrets.

Beta 26 does not change Home Assistant or `ha-mcp` compatibility, the exact
8.1.1 admission entry, `ha_search`, held `ha_get_operation_status`, provider
routing, Dashboard behavior, public tool schemas, approval or F3 authority,
fallback, or stable v1.1.2. The currently published Engineering version stays
`2.2.0-beta.25` until a separately authorized protected promotion consumes
the staged declaration.
