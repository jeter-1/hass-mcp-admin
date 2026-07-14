# Beta 21 release notes

Version: `2.0.0-beta.21`

Beta 21 adds one read-only Engineering-native tool: `handoff_generation`.
Registered tools increase from 37 to 38; canonical tools remain 25, and all
existing public schemas remain unchanged. No planned feature capabilities remain.

The tool produces structured and optional deterministic Markdown handoffs from
bounded runtime, dependency, integrity, reliability, incident, governance,
apply, verification, and rollback evidence. Facts, inferences, recommendations,
and limitations are distinct. Proposed or approved work is not completed;
completed work requires apply and verification evidence. Authorization
boundaries are explicit, correlation is not promoted to causation, and
contradicting evidence remains visible.

Signed sanitized pagination snapshots preserve the whole handoff and require no
upstream work on continuation. Beta 20 coverage semantics remain in force:
partial usable evidence is not a provider failure, and actual source failures are
counted separately from coverage limitations.

No write capability, executable service payload, unapproved YAML generation,
automatic remediation, background monitoring, or general result cache was
added. Production v1.1.2 remains unchanged.

Because Beta 21 adds a public tool, clients that cache MCP `tools/list` may need
the beta connector reconnected or recreated after deployment.
