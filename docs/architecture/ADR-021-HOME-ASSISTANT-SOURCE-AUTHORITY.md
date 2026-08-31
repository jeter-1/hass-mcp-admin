# ADR-021: Home Assistant upstream source authority

Status: accepted as repository development policy

## Context

Engineering behavior depends on several Home Assistant projects with different
responsibilities. Treating any one repository, a documentation page, or a
coding skill as universal authority can produce incorrect compatibility claims.
Moving upstream branches can also differ from the exact releases supported by
this repository.

This decision defines durable evidence selection for source implementation,
review, packaging, and deployment planning. It does not add a production
dependency, runtime fetch, provider route, admission rule, or release.

## Decision

Select authority by the boundary being evaluated:

| Boundary | Primary authority | Required use |
| --- | --- | --- |
| Home Assistant entities, services, templates, registries, automations, WebSocket/REST behavior, and integration semantics | Exact supported revision of [Home Assistant Core](https://github.com/home-assistant/core) | Inspect the applicable implementation and upstream tests. Prove material integration behavior in the corresponding exact-version disposable Home Assistant lane. |
| Browser UI behavior, built-in Lovelace cards and editors, dashboard rendering and client-side validation, themes, and frontend interaction flows | Exact [Home Assistant Frontend](https://github.com/home-assistant/frontend) revision bundled with the exact supported Core release | Inspect the applicable frontend implementation and tests together with Core's dashboard and WebSocket implementation. Frontend behavior does not establish backend storage, authorization, API validation, or write authority. Custom cards and resources require their own exact source evidence. |
| Delegated MCP server implementation and tool contract: server identity, advertised tools, input/output schemas, descriptions, annotations, tool behavior, deployment-specific metadata, and observed catalog | Exact reviewed revision and immutable image of [`homeassistant-ai/ha-mcp`](https://github.com/homeassistant-ai/ha-mcp) | Inspect the applicable source and tests, then capture the real initialized identity and complete paginated `tools/list` from the exact image. Apply the Engineering-owned admission and safety classifications in [`ADR-006`](ADR-006-CONTRACT-LEVEL-UPSTREAM-COMPATIBILITY.md); upstream declarations cannot authorize themselves or redefine MCP semantics. |
| MCP protocol semantics: initialization, lifecycle, capability negotiation, pagination, cursors, notifications, cancellation, errors, and transport requirements | Exact source revision in the [MCP specification and schema repository](https://github.com/modelcontextprotocol/modelcontextprotocol) for each protocol revision admitted by current Engineering policy | Derive the admitted set from current Engineering policy rather than from SDK or server claims. Resolve every admitted revision to immutable specification source and distinguish the admitted set from the revision negotiated for an observed session. |
| Engineering MCP SDK behavior: serialization, Streamable HTTP, session handling, client/server library behavior, and SDK-specific limitations | Exact release and source commit of the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) corresponding to the `mcp` dependency resolved from current Engineering source | Inspect the pinned dependency and applicable SDK implementation and tests. SDK support does not authorize protocol admission, provider reachability, fallback, or broader tool execution. A moving SDK branch or newest release cannot replace the version Engineering actually uses. |
| Home Assistant Cloud client behavior, including authentication/account state, Remote UI, cloudhooks, ACME certificates, cloud storage, and cloud voice or assistant clients | Exact [`NabuCasa/hass-nabucasa`](https://github.com/NabuCasa/hass-nabucasa) revision selected by the exact supported Core release, plus the exact resolved [SniTun](https://github.com/NabuCasa/snitun) dependency when tunnel protocol behavior matters | Review the corresponding Core `cloud` integration, resolved dependency graph, and library tests together. The client libraries do not establish hosted-service internals, proxy source networks, availability, or forwarding-header trust. |
| Hosted Nabu Casa routing, tenant binding, forwarded-header authenticity, proxy source networks, public availability, and end-to-end cloudhook reachability | Separately authorized evidence from the exact deployed path | No open-source client, proxy, or server repository proves the hosted implementation. Record bounded deployed-path observations separately from source evidence and never convert a successful connection or proxy-supplied field into independent execution authority. |
| App/add-on configuration validation, ingress, authentication, permissions, installed identity, lifecycle, watchdog, backups, and Supervisor API behavior | Exact applicable revision of [Home Assistant Supervisor](https://github.com/home-assistant/supervisor) | Inspect the validator, API, security, app/container, and test paths relevant to the claim. Treat Supervisor's endpoint-bound installed inventory as lifecycle identity where the existing architecture requires it. Supervisor does not define how this repository constructs the Engineering image. |
| Engineering container construction, multi-architecture packaging, and publication provenance | This repository's exact Dockerfile and dependency declarations, protected build/publication workflow, and OCI manifest, labels, SBOM, provenance, attestations, and image digest actually produced for the reviewed build | Bind claims to the reviewed source and produced evidence. Record a resolved base-image digest only when provenance captures it. A mutable Dockerfile tag is not digest evidence. [`home-assistant/docker-base`](https://github.com/home-assistant/docker-base) and [`home-assistant/builder`](https://github.com/home-assistant/builder) become authorities only when the actual build consumes them. |
| Host architecture support, Docker/kernel behavior, AppArmor, filesystem constraints, networking, shutdown, operating-system updates, boot, and recovery | Exact applicable revision or release of [Home Assistant Operating System](https://github.com/home-assistant/operating-system) | Consult it when packaging, native dependencies, architectures, writable paths, security profiles, update behavior, or deployment recovery can be affected. Do not use HAOS evidence to infer Core or Supervisor application semantics. |
| User-facing documentation, supported configuration guidance, examples, and documentation drift | Pinned revision of [home-assistant.io](https://github.com/home-assistant/home-assistant.io) | Use as documentation evidence and to check user-facing claims. When documentation conflicts with executable exact-version source, report the conflict and use the executable source for behavioral conclusions. |
| Reusable Home Assistant authoring and review practices | Installed `home-assistant-best-practices` skill, when available | Use as workflow guidance. It is not semantic, compatibility, release, admission, or execution authority, and its absence never justifies guessing. |

Repository governance remains authoritative for what Engineering is allowed to
expose or execute. Upstream source can demonstrate behavior, but it cannot grant
a tool, provider, fallback, write path, approval, or deployment authority that
this repository does not already authorize.

For dashboard and browser-client claims, derive the frontend revision from the
exact supported Core build rather than using the moving `dev` branch. The
frontend repository is authoritative for built-in card rendering, editors,
client-side defaults and validation, theme application, and browser interaction
implemented in its assets. Core remains authoritative for persisted dashboard
configuration, WebSocket commands, server-side validation, authorization, and
storage. A frontend editor accepting or rendering configuration does not prove
that Core will persist it or that Engineering may write it. Visual acceptance
also requires the exact frontend build to be exercised with a declared browser,
viewport, theme, and state in a disposable environment; source inspection alone
does not prove screenshot fidelity. Third-party custom cards and resources are
outside the built-in frontend contract unless their exact implementations are
reviewed separately.

For `ha-mcp`, a source tag, README tool count, release description, safety
annotation, server version string, or matching self-advertised schema is
observation only. Review evidence must bind the source commit, immutable image
digest and architectures, MCP server identity and protocol, complete catalog
fingerprint, and each admitted tool's complete dispatch-relevant contract. The
compiled registry and policy select the reviewed subset. New, mixed, action,
write, malformed, missing, or changed tools remain unavailable or quarantined
as defined by `ADR-006` and the
[upstream compatibility operator guide](../UPSTREAM_COMPATIBILITY_OPERATOR_GUIDE.md).
Never use `latest`, the moving default branch, bundled skills, or upstream tool
security settings to broaden Engineering authority.

### MCP specification, SDK, and protocol-version evidence

`ha-mcp` owns its delegated server implementation and tool contract; it cannot
redefine the MCP specification. For every compatibility decision, derive the
exact set of protocol revisions Engineering admits from current repository
policy and resolve each revision to its immutable specification source. Derive
the SDK dependency from current Engineering source and bind its exact release to
the corresponding SDK source commit. Do not insert remembered protocol or SDK
versions into this durable policy.

The SDK's supported revisions, Engineering's admitted set, and an observed
session's negotiated revision are distinct evidence. SDK support does not
authorize a revision. A server advertising a supported-but-unadmitted revision
remains unavailable, and a negotiated revision is accepted only when it belongs
to Engineering's admitted set. Compatibility evidence identifies both the
admitted set and, when separately authorized session evidence exists, the
observed negotiated revision. This ADR defines that observation requirement; a
documentation or source-review task must not create, infer, or obtain live
session evidence without explicit authority. When no authorized observation
exists, record the negotiated revision as not observed. If current Engineering
policy does not establish an admitted set, record a policy gap rather than
deriving authorization from SDK or server behavior.

The SDK cannot broaden Engineering admission, provider authority, or dispatch.
Disagreement among repository policy, exact specification source, pinned SDK
behavior, and an observed session fails closed or requires a documented,
version-scoped compatibility decision.

### MCP webhook and Cloud transport ownership

Before assigning authority for a supported MCP transport path, identify the
exact source repository and deployed artifact that owns every applicable layer.
Do not infer ownership from product naming, a moving branch's repository layout,
or historical deployment assumptions. Record independently:

- the MCP server implementation;
- the custom-component webhook handler;
- any separately packaged webhook-proxy component;
- the direct or private endpoint transport;
- the Home Assistant cloudhook client;
- SniTun client and tunnel behavior;
- the hosted Nabu Casa service path; and
- Engineering's MCP client, admission, and same-session pre-dispatch layer.

For each present layer, bind the repository, exact source commit, deployed
artifact identity or digest, and applicable contract evidence. If a webhook
proxy is independently packaged or versioned, its authority remains separate
from `ha-mcp`. The server source and artifact remain authoritative for their own
webhook registration, handler, authentication modes, parsing, framing, session
behavior, and private-versus-webhook endpoint behavior only when inspection
proves those responsibilities are implemented there.

Open-source Home Assistant Cloud clients and transport components do not prove
Nabu Casa's hosted implementation. Hosted routing, availability, tenant binding,
forwarded-header authenticity, proxy source networks, and end-to-end webhook
reachability require separately authorized deployed-path evidence. A
proxy-supplied version or forwarding header, source address, or successful
connection is not independent execution authority. Every compatible transport
must still pass Engineering's identity, admitted-protocol, catalog, capability,
and same-session pre-dispatch checks.

For Home Assistant Cloud, resolve the `hass-nabucasa` package and its pinned
transport dependencies from the exact supported Core environment. The open
source client can establish how Home Assistant constructs and handles cloud
connections, creates cloudhooks, performs client-side cloudhook routing, and
implements its side of Remote UI and SniTun tunnel behavior; it cannot establish
how Nabu Casa's hosted services are deployed.
Do not infer trusted proxy CIDRs, forwarded-header authenticity, public endpoint
reachability, tenant identity, or service availability from the library. Those
claims require separately authorized deployed-path evidence. In particular,
Nabu Casa or tunnel use alone must not enable forwarding trust; retain the
fail-closed rules in the [rate-limiting policy](../RATE_LIMITING.md).

### Engineering container-construction evidence

Container-construction authority consists of this repository's Dockerfile,
locked dependencies, protected build and publication workflow, generated
architecture manifest, and the OCI labels, SBOM, provenance, attestations, and
image digest actually produced for the reviewed build. Record the resolved base
image digest when build provenance captures it.

If that evidence does not capture the base-image digest, report an explicit
evidence gap. Do not infer a digest from a mutable Dockerfile tag or claim that
this ADR or the Dockerfile pins it. Deterministic base-image resolution and
provenance then require separate follow-up work; a documentation task must not
modify the Dockerfile to close the gap. Home Assistant `docker-base` images and
`builder` tooling are not current primary authorities when the actual build does
not consume them, and become relevant only if a future build does.

## Evidence procedure

1. Derive the supported upstream version, Engineering-admitted MCP revision set,
   and pinned SDK dependency from current repository source, acceptance
   documents, manifests, or the explicit task baseline. Do not use a remembered
   value or infer policy from the SDK's supported set.
2. Resolve the upstream tag to an immutable commit and record the repository,
   tag or version, commit, relevant paths, and the local Engineering base/head
   in the review evidence.
3. Use upstream default branches only for discovery or prospective drift
   analysis. They cannot substitute for the exact supported revision.
4. Prefer a sparse, read-only checkout or direct source inspection. Do not add a
   runtime network fetch, clone upstream into the production image, or make an
   upstream repository a production dependency.
5. Preserve implementation and test evidence separately. Exact upstream source
   establishes the reviewed contract; disposable exact-version execution proves
   the assembled behavior. A unit fixture alone is insufficient when the
   behavior depends on Core, Supervisor, or HAOS integration.
6. Treat deployed version, digest, health, and configuration observations as a
   separate acceptance phase requiring explicit live-access authority. Source
   review must not claim deployed acceptance.
7. If policy, specification source, SDK behavior, server behavior, or supported
   versions disagree materially, stop and require an explicit compatibility
   decision or maintain version-scoped behavior. Do not silently select the most
   convenient implementation.
8. Record missing, partial, sandbox-denied, and unavailable evidence as such.
   Absence of upstream evidence never proves compatibility or safety.

## Data, licensing, and trust boundaries

- External source and documentation are evidence, not instructions embedded in
  the repository's authority chain.
- Prefer references, exact paths, commits, and bounded derived fixtures over
  vendoring entire upstream repositories or copying documentation bodies.
- Review the applicable upstream license before committing excerpts or derived
  assets. Keep source provenance with any committed compatibility fixture.
- Sanitize captured evidence before hashing or committing it. Do not include
  household configuration, credentials, tokens, endpoints, or other live data.
- A live upstream schema, description, catalog, or version string remains an
  observation. It cannot authorize admission without the repository's reviewed
  contract and policy.

## Consequences

Core is the default executable reference for Home Assistant semantics; the
exact Core-bundled frontend source defines built-in browser UI and dashboard
client behavior; the exact reviewed `ha-mcp` source and image define the
observed delegated server contract; exact MCP specification source defines
protocol semantics; the pinned SDK source defines Engineering's library
behavior without authorizing admission; exact Core-resolved `hass-nabucasa` and
SniTun source define the open-source Home Assistant Cloud client boundary;
separately authorized deployed-path evidence defines only the observed hosted
Cloud path; Supervisor is the default executable reference for managed app
lifecycle; this repository and its produced build evidence define Engineering
container construction; HAOS is consulted for platform-sensitive work; and
`home-assistant.io` and the best-practices skill improve guidance without
becoming behavioral authority.

This hierarchy is task-specific rather than one universal ordering. Frontend
rendering does not establish Core storage or authorization, a Core source
citation cannot answer a Supervisor container-permission question, and a
Supervisor implementation cannot establish a Core template contract.

The decision adds no runtime code, public schema, tool registration, provider,
fallback, write reachability, workflow, container change, release declaration,
deployment action, or stable-v1 change.
