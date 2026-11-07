# Engineering 2.4.2-beta.7 release notes

**Status: in progress.** Engineering 2.4.2-beta.7 is the 2.5.0 build. It
builds on 2.4.2-beta.6 (HAMCP-304, #373). Its content lands through item pull
requests into the `release/2.5.0` integration branch, and the final release
pull request (HAMCP-331) completes these notes.

Josh decided on 2026-11-07 to build all 2.5.0 items into one release. The
version string is 2.4.2-beta.7 because the release transition refuses
`2.4.2-beta.6 -> 2.5.0-beta.1`.

Acceptance: [`V2_4_2_BETA7_ACCEPTANCE.md`](V2_4_2_BETA7_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

## Current state

At the base commit of `release/2.5.0` the version is materialized and no
item has landed. The runtime is identical to 2.4.2-beta.6 apart from the
version strings. Nothing is published or deployed.

## Planned items

Each item is described here only after its pull request lands in
`release/2.5.0`.

- **HAMCP-327 (folds in HAMCP-301).** `control_helper` fails to ask: when
  helper safety evidence cannot be established, it returns
  `confirmation_required` with the reason instead of dispatching.
  **Pending: not landed.**
- **HAMCP-288.** ha-mcp secret-path containment in Engineering's log reads.
  **Pending: not landed.**
- **HAMCP-319 Phase 2.** ha-mcp 9.1.0 admission side by side with the 8.5.0
  surface, including the `/catalog/full` option.
  **Pending: not landed.**
- **HAMCP-290.** Bounded Core Marketplace inventory read.
  **Pending: not landed.**
- **HAMCP-278 / HAMCP-279.** Core registry signing and publication, and
  automated Core release evidence. **Pending: status to be recorded.**

## ha-mcp 9.1 `/catalog/full` option

**Pending** (HAMCP-319 Phase 2). The final release pull request describes
the option, its default and how to enable or disable it, from the
implementation that landed.

## Tool catalog

**Pending.** The final release pull request states the expected connector
tool counts from the landed source.

## Rollback

**Pending.** The final release pull request states whether rolling back to
2.4.2-beta.6 is safe, from the storage, provider and option changes that
actually landed.
