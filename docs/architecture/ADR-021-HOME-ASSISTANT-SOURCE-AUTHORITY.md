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
| Delegated MCP server identity, protocol, advertised tools, input/output schemas, descriptions, annotations, and tool behavior | Exact reviewed revision and immutable image of [`homeassistant-ai/ha-mcp`](https://github.com/homeassistant-ai/ha-mcp) | Inspect the applicable source and tests, then capture the real initialized identity and complete paginated `tools/list` from the exact image. Apply the Engineering-owned admission and safety classifications in [`ADR-006`](ADR-006-CONTRACT-LEVEL-UPSTREAM-COMPATIBILITY.md); upstream declarations cannot authorize themselves. |
| Home Assistant Cloud client behavior, including authentication/account state, Remote UI, cloudhooks, ACME certificates, cloud storage, and cloud voice or assistant clients | Exact [`NabuCasa/hass-nabucasa`](https://github.com/NabuCasa/hass-nabucasa) revision selected by the exact supported Core release, plus the exact [SniTun](https://github.com/NabuCasa/snitun) dependency when tunnel protocol behavior matters | Review the corresponding Core `cloud` integration and library tests together. The client library does not establish hosted-service internals, proxy source networks, availability, or forwarding-header trust. |
| App/add-on configuration validation, ingress, authentication, permissions, installed identity, lifecycle, watchdog, backups, Supervisor API behavior, and container construction | Exact applicable revision of [Home Assistant Supervisor](https://github.com/home-assistant/supervisor) | Inspect the validator, API, security, app/container, and test paths relevant to the claim. Treat Supervisor's endpoint-bound installed inventory as lifecycle identity where the existing architecture requires it. |
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

For Home Assistant Cloud, resolve the `hass-nabucasa` package and its pinned
transport dependencies from the exact supported Core environment. The open
source client can establish how Home Assistant constructs and handles cloud
connections; it cannot establish how Nabu Casa's hosted services are deployed.
Do not infer trusted proxy CIDRs, forwarded-header authenticity, public endpoint
reachability, tenant identity, or service availability from the library. Those
claims require separately authorized deployed-path evidence. In particular,
Nabu Casa or tunnel use alone must not enable forwarding trust; retain the
fail-closed rules in the [rate-limiting policy](../RATE_LIMITING.md).

## Evidence procedure

1. Derive the supported upstream version from current repository source,
   acceptance documents, manifests, or the explicit task baseline. Do not use a
   remembered version.
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
7. If supported versions disagree materially, stop and require an explicit
   compatibility decision or maintain version-scoped behavior. Do not silently
   select the most convenient implementation.
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
observed delegated MCP contract; exact Core-selected `hass-nabucasa` and SniTun
source define the open-source Home Assistant Cloud client boundary; Supervisor
is the default executable reference for the managed app boundary; HAOS is
consulted for platform-sensitive work; and `home-assistant.io` and the
best-practices skill improve guidance without becoming behavioral authority.

This hierarchy is task-specific rather than one universal ordering. Frontend
rendering does not establish Core storage or authorization, a Core source
citation cannot answer a Supervisor container-permission question, and a
Supervisor implementation cannot establish a Core template contract.

The decision adds no runtime code, public schema, tool registration, provider,
fallback, write reachability, workflow, container change, release declaration,
deployment action, or stable-v1 change.
