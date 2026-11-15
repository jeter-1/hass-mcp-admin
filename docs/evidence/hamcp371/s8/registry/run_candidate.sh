#!/usr/bin/env bash
# HAMCP-371 S8: the exact `candidate` command for the 9.2.0 registry entry.
# Run from the repository root: run_candidate.sh PYTHON OUTDIR
# This is pass 1 only. Pass 2 is build_candidate_inputs.py finalize (README).
# No --artifact-evidence: candidate's check requires the 8.1.0 misversioned
# release-asset set (review_upstream_read_release.py _artifact_evidence_binding),
# which a 9.x review does not carry; finalize binds the 9.2.0 review instead.
set -euo pipefail
python=$1 outdir=$2
here=docs/evidence/hamcp371/s8/registry
"$python" -I scripts/review_upstream_read_release.py candidate \
  --capture docs/evidence/upstream-read-compatibility/ha-mcp-9.2.0.json \
  --base-policy "$here/base-policy-9.2.0.json" \
  --version 9.2.0 \
  --source-commit 51e3aac788482a081807591cd5d58d07dc4e540b \
  --source-tag-object 95924750d3a1cdbcf6eb9ffe8dbd5a3ed6616fe5 \
  --source-tree 377909bd56f80aea959c0eca00014c489febd4c8 \
  --provider-disposition read_gateway=admitted \
  --provider-disposition dashboard=admitted \
  --provider-disposition lifecycle=held \
  --provider-disposition backup=held \
  --image-index-digest sha256:9de5bf6c4216142fdffe862900abe72dac8dfe9621dd0b2b050d132372b676ba \
  --amd64-digest sha256:349a3ec6b2f4fa0797651b2c5d8985ca28d7a1028156eefba8d94fe51683fdb8 \
  --arm64-digest sha256:5799ff6f0b85f01710b725792e32caf79255934a6e80b8af28373768d42e9a80 \
  --addon-amd64-index-digest sha256:e0fd8903915be64f65744f7fa78d8d46432388159bc000ff0807079f494fe3ba \
  --addon-amd64-manifest-digest sha256:928c55d6ba38d0219058be2e0ae4ae36375eb3db7aad0cb21c5b573848ed9244 \
  --addon-arm64-index-digest sha256:54459403f8bc0c77d108d8df5f3f64cc64f98eaf0d79dafdb7df5832fc9c326a \
  --addon-arm64-manifest-digest sha256:de36201ee267e111cb7f675ea978aef8861bd5ddc812f86976425c572e9010dc \
  --image-revision 4d7148309af7e4d3da95e16ced8bb67d6a43b9c1 \
  --review-date 2026-11-15 \
  --review-decisions "$here/review-decisions-9.2.0.json" \
  --runtime-contract-fingerprint-model ha-mcp-operational-tool-descriptor-v2 \
  --strict-tools-response "$here/strict-tools-response-9.2.0.json" \
  --strict-full-contract-fingerprint-model ha-mcp-strict-full-contract-v1 \
  --held-tool ha_get_operation_status \
  --dashboard-status quarantined \
  --output-policy "$outdir/upstream_tool_policy_9_2_0.json" \
  --output-entry "$outdir/registry-entry-9.2.0.json"
