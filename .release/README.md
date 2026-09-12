# Staged Engineering releases

`next-version` is a single-use authoring declaration on the feature branch.
It contains one exact version and a final newline. Prepare the matching
version-specific acceptance and release-notes documents, require exact staged
resolution from `scripts/codex-context.py`, then inspect the promotion preview:

```sh
python scripts/promote_next_release.py
python scripts/promote_next_release.py --apply
```

With release preparation authorized, `--apply` updates the three authoritative
Engineering version declarations and consumes `next-version`. Review and commit
that materialized state in the same pull request before Ready. Run complete
Evidence against the actual published base and obtain exact-head CI. An
unconsumed declaration is not mergeable release state; no post-merge promotion
commit or second promotion PR is required.

Leave agent delivery draft. Josh's later Ready action can authorize the
repository's controlled, head-bound merge/publication workflow. Publication
validates and publishes the exact protected-main release commit; it does not
write another version commit. Deployment and live acceptance remain separate.

Preserve immutable releases and all failed/partial publication evidence.
Ambiguous or partial publication requires bounded reconciliation, not blind
retry or reuse. See [the current workflow](../docs/CODEX_WORKFLOW.md) and the
exact version-resolved acceptance document for authority and recovery rules.
