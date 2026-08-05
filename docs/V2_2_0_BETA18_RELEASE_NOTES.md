# HA MCP Engineering Server 2.2.0-beta.18 release notes

## Release boundary

Beta 18 adds internal, runtime-inert F3-C1 configuration adapters for create
and update of automation, script, `input_boolean`, and `input_number`. It is
based directly on merged Beta 17 main
`1815f7aabeb09eefeb86bbca1108c5cea537da5d` and consumes the canonical shipped
F3 contracts, executor, and durable lock model.

The current configuration routes remain legacy-authoritative. Beta 18 adds no
executor, lock store, task repository, public tool, startup coordinator, or
runtime route activation.

## Configuration conformance

One closed adapter and four resource strategies expose eight exact capability
identities. They bind existing direct Home Assistant configuration gateway
operations, immutable plan evidence, exact target identity, normalized hashes,
F2 policy and approval evidence, fixed provider arguments, exact readback, and
rollback unavailable.

Every operation requests an exclusive exact resource lock, its matching shared
`reload:<domain>` lock, and shared `home_assistant:core`, all resource scope.
There is no `ha-mcp` add-on dependency lock. The merged executor proves atomic
lock acquisition for a single operation.

The lifecycle is locks, final preflight, idempotent approval consumption,
durable intent, and one fixed gateway write. Preflight never claims consumed
approval. The adapter calls the executor's irreversible callback immediately
before the write, with no mutable decision or unrelated await between them.
Approval or intent failure invokes the gateway zero times.

Provider response is not verification. Exact normalized resource identity,
configuration readback, resulting hash, and full configuration validity are
required. Response loss and every post-intent reconstruction use readback only;
the same attempt cannot redispatch.

## Ordered-plan boundary

The pure 1–8 operation model validates order, dependencies, unique IDs and
targets, complete lock union, and deterministic future child descriptors. It
persists nothing, calls no provider, retains one public task ID, and never
authorizes redispatch from restart-position evidence.

Merged F3-A has one execution record per exact prepared operation. Durable
multi-operation execution therefore remains an F3-D prerequisite: one public
task must own one ordered list and one child F3 identity per operation. Beta 18
does not hide several writes behind one intent or manufacture public tasks.

Task cancellation is accepted only before the first child intent. After any
possible dispatch it is rejected; verified work remains represented and later
work may remain undispatched without a partial-cancellation outcome.

All eight forward capabilities declare rollback unsupported and unavailable.
Historical contract-v1 automation rollback remains unchanged on the legacy
route. Contract-v2 F3 rollback requires a separately governed future decision.

## Known concurrency limit

The reviewed Home Assistant operations have no compare-and-save or expected
generation. Final preflight rejects changes already visible before approval
and intent, and post-write readback detects a different final result. It cannot
prove that an external edit inside the remaining preflight-to-save window was
not overwritten.

## Runtime and compatibility preservation

Beta 18 preserves:

- no current F3 configuration route and no dashboard execution;
- 25 canonical plus 23 Engineering-native tools, for 48 local tools;
- task schema 1, configuration plan contract 2, operational plan contract 3;
- exact 7.14.2 accounting: 78 advertised, 26 delegated, zero held, 48 local,
  and 74 total;
- exact 8.0.0 accounting: 78 advertised, 24 delegated, two held, 48 local, and
  72 total;
- held tools `ha_search` and `ha_get_operation_status`;
- fallback zero, protocol `2025-03-26`, `aiohttp==3.14.3`,
  `cryptography==50.0.0`, and stable v1.1.2.

Nothing here authorizes deployment, publication, tagging, merge, production
access, or runtime adapter activation.
