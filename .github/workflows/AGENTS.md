# Workflow Instructions

These are more-specific instructions for `.github/workflows/`. Nonconflicting
repository-root instructions still apply; this file takes precedence if guidance
conflicts. Workflow changes alter repository authority and require explicit scope.

- Grant minimum GitHub permissions and pin security-sensitive actions or images
  immutably where supported.
- Keep secret boundaries explicit; validation must not print or upload secrets.
- Validation workflows must not publish, sign, tag, merge, release, or deploy.
- Separate validation, signing, publication, promotion, and deployment authorities.
- Do not add hidden merge, tag, package, release, or deployment side effects.
- Disposable resources require bounded timeouts and `always()` cleanup.
- Review the workflow permission diff and every external action/image reference
  before completion.
