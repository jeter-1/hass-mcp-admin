# Test Instructions

These are more-specific instructions for `tests/`. Nonconflicting repository-root
instructions still apply; this file takes precedence if guidance conflicts.

- Run focused regression tests during implementation and the complete unittest
  suite before draft-PR completion.
- Cover success and failure paths, negative reachability, and preservation of the
  original failure behavior.
- For governed or action-capable work, separate execution-integrity controls
  from consequence classification. Include positive coverage showing that an
  exact owner-authorized operation remains actionable when its disclosed
  consequence is high, critical, safety-critical, unknown, or incompletely
  analyzed while its execution contract remains exact, and negative coverage
  showing refusal for ambiguous targets or arguments, stale state, invalid
  authority, provider drift, unsafe concurrency, duplicate dispatch, and
  unverifiable execution.
- Test the supported single-endpoint topology: client-visible Engineering
  capabilities may use reviewed internal `ha-mcp` providers, but catalog,
  routing, attribution, admission, and no-fallback behavior must remain aligned.
- Include stable-v1 compatibility checks when a shared development surface could
  affect it.
- Use deterministic, offline fixtures and disposable repositories. Tests must not
  require network access, Docker, GitHub credentials, Home Assistant, or
  production endpoints/data.
- Never place real credentials in fixtures. Synthetic values must be unmistakable
  and must not be printed by context or evidence tooling.
