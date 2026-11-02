# HAMCP-279 evidence

Base: `d09fc8a6196593880832de2d77cc5ce143562891`. Read-only measurement on 2026-11-02.

`tag-commits.json` lists 14 exact stable Core tag commits in 2026.8/9/10.
Each version JSON records the immutable tar.gz archive SHA-256 and selected full-file
SHA-256 hashes; the archive PAX commit was checked before reading. The October .1
and .3 archive hashes also match the previously committed lane provenance.
`scope-inputs.json` maps the historical HAMCP-222 scopes and five seq15 additions.
The exact 337-file inventory is `ci/core-contract-read-files.json`.
`results.json` records all 11 adjacent comparisons, missing files and the real
.1→.3 negative. No cross-minor pair is counted; .0→.1 is included.

The strict fixed-list inheritance rate is 2/11. Nine current files are absent in
August and eight in September; missing files refuse, even at both tags. A
period-available-scope sensitivity would pass 4/11, not the adopted rule.
`narrower-proposal.json` proposes removing only the uncalled credential-management
endpoint and gives profile-based retention reasons for each remaining file.
It also yields 2/11. It is **not adopted**.

Exact .1→.3 differences and effects:

| Core path | Difference and judgment relevance |
|---|---|
| auth/__init__.py | Deleted-user token revocation closes WebSocket sessions. Auth lifecycle changes, fail-closed; no device/schema/ha-mcp pairing judgment change. |
| components/cloud/alexa_config.py | Suppresses RequireRelink in background sync. Prior cloud.alexa side effect and allowed exposure readback are unchanged; full-file O3 still refuses. |
| components/config/auth_provider_homeassistant.py | Owner guard, credential-create lock and duplicate credential refusal. Not an F3 readback endpoint; only matched the broad config scope. Narrowing proposal removes it. |
| helpers/config_validation.py | Multi-state `for` conditions become valid. Actual validation semantics change; generated grammar is narrower, but owner-supplied bodies can differ. Keep the file. |

The detailed historical semantic review is
`docs/evidence/hamcp222/artifacts-2026.10.3/tools/build_assessment_json.py:200-324`.
It accepted compatibility; it did not prove strict byte identity.

## Local mutation evidence

Run from repository root using the task's Python test environment:
`python docs/evidence/hamcp279/run_mutations.py`.
The harness copies only Git-listed and nonignored new repository files into
`.artifacts/mutation/copy`, changes that copy only, clears copied bytecode and
runs focused assertions. `mutation-results.json`: 14 assertion kills, zero invalid
kills, zero survivors. A nonzero exit without an assertion failure is classified
invalid, never a successful assertion kill. Logs are retained in the ignored
`.artifacts/mutation/` directory. This is implementer testing, not independent review.

Real disposable CI receipts and head/run identity belong in the PR and task
handoff; local mocks and historical timings do not substitute for new CI acceptance.
