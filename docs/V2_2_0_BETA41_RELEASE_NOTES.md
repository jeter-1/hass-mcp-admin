# Engineering 2.2.0-beta.41 release notes

Beta 41 is the staged large-blueprint dependency-evidence candidate. Beta 40
remains the advertised Engineering add-on version until a separate protected
promotion consumes `.release/next-version`. Staging is not publication,
promotion, deployment, or authorization to access live Home Assistant.

## Complete bounded large-blueprint analysis

The dependency provider now admits legitimate large blueprint sources within
explicit source, YAML, resolution, semantic-analysis, terminal-evidence, and
deadline limits. The source limit remains 1 MiB. YAML composition is bounded to
32,768 nodes and depth 64; blueprint input resolution and configuration
analysis are each bounded to 16,384 values and depth 64; context evidence is
bounded to 256 members; terminal document evidence is bounded to 8,192
obligations; and analysis retains a 60-second monotonic deadline.

No limit silently truncates authoritative evidence. A source that exceeds a
declared bound produces an explicit coverage-failure obligation and remains
non-executable. Unreadable, partial, expired, drifted, or semantically opaque
evidence never means that no dependency exists.

One scan reads and parses a declared blueprint path once. Parsed source may be
reused within that scan, but resolved configuration analysis and evidence
binding remain consumer-specific. The cache never crosses scans, source
SHA-256 values, index generations, consumers, or incompatible analysis
contexts.

## Per-obligation attribution and aggregation

Every required terminal has a deterministic identity bound to the consumer
automation, blueprint path, source SHA-256, configuration path, obligation
type, relation, expression fingerprint, and configuration fingerprint.
Automations that share one parsed source retain independent obligation and
diagnostic accounting.

Every unresolved authoritative obligation has exactly one attributable
internal diagnostic. Stable diagnostic codes distinguish source-size,
YAML-node, YAML-depth, analysis-node, analysis-depth, context-member,
terminal-count, deadline, unsupported dynamic entity lookup, and source-drift
failures. Coverage cannot become complete while any required obligation is
unresolved.

The public compatibility projection remains independently bounded. Its
retained count and fingerprint cannot truncate the authoritative ledger or
drive the coverage decision, and the delegated response-size limit is
unchanged.

## Sanitized fixture provenance

The small control is pinned to Home Assistant Core commit
`53998d7710b4ac280658511c24a2a3e2651f9873`, path
`homeassistant/components/automation/blueprints/motion_light.yaml`, blob
`11900708b19a23e627ad46e20a58621be56bfc7e`, and SHA-256
`e07ac35fae7270131f118da767b036e7f7776672077691d9fbcd026e5a7e3f9c`.

The large structural witness is Sensor Light 8.7 at immutable gist commit
`c710556d02f6d37052efaf98c1baf5b4380e7d48`, blob
`1563956f6ee7231b0037a98aa093d706424a0579`, and SHA-256
`eb378157e063d531523a3658690b50dbeed61c75e8f9bad4a4222288496fe9eb`.
It is 701,076 bytes with 18,568 composed YAML nodes, 11,442 resolved value
nodes, maximum depth 31, 620 blueprint-input occurrences, and 140 root
variables.

No upstream blueprint text, live observation, or real entity ID is committed.
The repository fixture is a deterministic structurally equivalent generator
with 10,648 resolved values, 140 root variables, 3,000 neutral template
obligations, and 4,500 padding values.

For two consumers sharing that fixture, the exact regression output is one
source read, 3,000 independently identified blueprint obligations per
consumer, 6,000 total obligations, zero unresolved diagnostics, complete
blueprint coverage, and zero failed obligations.

## Regression and refusal coverage

The repository package covers the small motion-light control, the large Sensor
Light analogue, two consumers sharing one source, a genuinely over-bound
source, unreadable source evidence, unsupported dynamic entity lookup,
non-entity template text, source-hash invalidation, public projection overflow,
multiple simultaneous failures, target-specific Jinja behavior, Beta 39
selector semantics, and dictionary method/key precedence.

The provider tripwires allow only reviewed GET and inventory-list commands.
Tests prove that no fallback, Home Assistant mutation, provider write, service
call, or helper-write authorization is created by dependency analysis.

## Compatibility and security

Stable v1.1.2, all public schemas, MCP registration, the 51-tool catalog,
provider routing and admission, fallback policy, helper authorization,
workflows, Dockerfile, repository metadata, and advertised Engineering Beta 40
remain unchanged. The change adds no direct-Home-Assistant fallback, arbitrary
forwarding, provider write, or new storage format.

## Validation state and limitations

The final Task 1 focused suite passed 125 tests, and the broader dependency,
governance, helper-risk, and routing selection passed 282 tests on Windows.
Protected Fast validation passed 15 tests together with affected compilation,
PowerShell parsing, and whitespace validation.

The pre-staging Full run is not a pass: it ran 2,618 tests with 156 failures,
203 errors, and 28 skips. The observed failures include unchanged upstream
release-registry policy-digest cascades in the Windows dependency environment,
and metadata validation correctly rejected the runtime diff because no next
version was staged. Beta 41 staging addresses only the version-policy failure.
Full and Evidence validation must be rerun and every remaining limitation must
be reported accurately. Exact-head PR CI must be completely green before
independent review is requested.

No live canary, deployment, promotion, helper mutation, or HAMCP-089 action was
performed. Task 1 is not operationally complete until independent review,
separately authorized deployment, and read-only live acceptance prove complete
fresh coverage for both Sensor Light automations.

## Rollback

Treat the complete Task 1 pull request as one coherent unit. Before promotion,
rollback is removal or reversion of the staged declaration, these documents,
and all Task 1 extraction, attribution, and regression commits together. After
merge, revert the pull-request merge as a unit. No persistence migration,
schema downgrade, deployment change, or live-system repair is required.
