# Engineering 2.4.1-beta.1 release notes

Engineering 2.4.1-beta.1 adds the reviewed HAMCP-135 governed configuration
inverse to the RC1 implementation. It restores eligible saved configurations
through the existing elevated approval and F3 execution route; it does not
operate the garage or complete the household recovery gate.

## Owner-selected development track

On October 8, Josh selected continued development releases for his existing
Engineering installation instead of a GA promotion. This supersedes the RC1
document's proposed `2.4.0-rc.1 -> 2.4.0` progression for this installation.
There is no GA prerequisite and no separate production installation to create.
The same technical Beta add-on, slug, image repository, options, ports, ingress,
persistent paths and single public MCP connector remain in use. Stable-v1
1.1.2 remains frozen and is not an installation or recovery target.

Release validation now also accepts an RC followed by beta.1 of the immediately
next patch version. This permits `2.4.0-rc.1 -> 2.4.1-beta.1`. It does not admit
same-version regression to beta, skipped patch or beta numbers, a new minor/major
from RC, or a direct RC-to-next-patch stable jump. Existing lifecycle transitions
and owner Ready, protected merge, publication and deployment controls remain.

## Exact governed inverse

The existing rollback interface can issue one deterministic inverse attempt for
an eligible terminal HAMCP-135 full-composition source task. It requires durable
proof of a contiguous verified forward prefix, exact source plan/hash and child
receipts, one-dispatch witnesses, current whole-bundle state, and fresh elevated
owner approval. It restores the captured prior bodies in reverse order, with
the shared script last. The separate minimal retry-removal plan gains no new
script-restoration authority.

All five resources remain continuously owned across intermediate inverse children.
Atomic transfers fence the exact predecessor and successor and check live claim
and lease deadlines inside the lock transaction. Interrupted token publication
preserves an exact recovered cancellation without poisoning global readiness;
genuine durable-write failures retain their existing fail-closed behavior.
Unknown outcomes retain ownership, and neither retry nor recovery redispatches
an uncertain operation or refreshes an old approval.

Incomplete or drifted source/state evidence can yield an explicitly partial,
caller-only recovery with the script withheld. Success for a selected subset is
not complete five-object recovery. A historical prohibited inverse remains
prohibited; no stored record or proof is upgraded in place. See the exact
[inverse contract](HAMCP135_GOVERNED_ROLLBACK.md).

## Preserved interfaces and evidence

The healthy catalog remains **83 tools: 58 Engineering-native and 25 delegated
reads**. Public descriptors, the 21 Core profiles and fingerprints, signed
journals, exact ha-mcp 8.5.0 admission, provider routes, persisted formats and
workflow permissions remain unchanged. No new registry signature or capability
activation is required solely for this release. Current applicable authority
is still required for every governed action.

Preparation base is protected RC1 source
`aea19575f441753817edb38292dbb88e5845566f`. The integrated garage candidate is
`1594e2647b90301d514b6b0ab74539159eb7745e`, independently reviewed with R1 resolved.
Its retained uninterrupted campaign ran 4,761 tests: 4,737 passed, 24 skipped,
including all 29 rollback methods. Those are predecessor results; validation
and CI for this release must bind the actual integrated revision. The 30 reviewed
garage files are preserved byte-for-byte. Retained actual-Core inverse evidence
contains 30 cases; the earlier forward campaign contains 296 executions / 289
distinct cases. Reuse is attributed, not represented as a new execution.

RC1's multidict 6.9.1 security correction, all other locked dependencies, build
inputs and base image are preserved. Source integration changes release tooling,
metadata and documentation alongside the reviewed inverse. No Govee provider,
HAMCP-151 add-on restart or ha-mcp 8.6.0 work is included.

## Separate household acceptance

Restoring the original shared script restores its unconditional close and internal
retry weakness. It does not undo a physical command. Multiple saves remain
non-atomic, external edits and active household runs are not excluded by F3 locks,
and configuration readback does not prove reload or run settlement.

Garage D2 remains open until Josh accepts the recovery procedure and its limits,
active runs are settled and activation is verified through supported routes under
separate live authorization. Installing this release does not authorize forward
application, rollback execution, a forced failure, script invocation or cover
operation. Release and installed checks follow the
[2.4.1-beta.1 acceptance contract](V2_4_1_BETA1_ACCEPTANCE.md).
