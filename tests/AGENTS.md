# Test Instructions

These are more-specific instructions for `tests/`. Nonconflicting repository-root
instructions still apply; this file takes precedence if guidance conflicts.

- Run focused regression tests during implementation and the complete unittest
  suite before draft-PR completion.
- Cover success and failure paths, negative reachability, and preservation of the
  original failure behavior.
- Include stable-v1 compatibility checks when a shared development surface could
  affect it.
- Use deterministic, offline fixtures and disposable repositories. Tests must not
  require network access, Docker, GitHub credentials, Home Assistant, or
  production endpoints/data.
- Never place real credentials in fixtures. Synthetic values must be unmistakable
  and must not be printed by context or evidence tooling.
