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
- Use explicit runner images (for example `ubuntu-24.04`), never floating
  `-latest` labels or runner groups, including matrix-supplied runners; change
  images deliberately. An explicit label pins the OS release only: GitHub still
  updates the image's preinstalled software on its own schedule, so do not treat
  tool or software versions as frozen.
- Review the workflow permission diff and every external action/image reference
  before completion.
