#!/usr/bin/env bash
# Read-only Core registry branch checks (HAMCP-278); owner-run, git and sha256sum only.
#
#   check_core_registry_branch.sh cutover [REMOTE_URL]
#       main and registry hold byte-identical Core journals, and registry holds
#       nothing but the journal and Core evidence files.
#   check_core_registry_branch.sh last-publish [REMOTE_URL]
#       the registry tip commit has one parent and changes exactly the journal
#       (modified) and one Core evidence file (added).
#
# It fetches into a temporary bare repository and deletes it; it never pushes,
# changes a branch, or touches the caller's checkout.
set -euo pipefail

readonly JOURNAL_PATH="upstream-trust/ha-core-release-registry.json"
readonly EVIDENCE_GLOB='^upstream-trust/evidence/ha-core-20[0-9]{2}\.[0-9]+\.[0-9]+-[0-9a-f]{64}\.json$'
mode="${1:-}"
remote="${2:-https://github.com/jeter-1/hass-mcp-admin.git}"
[[ "$mode" == "cutover" || "$mode" == "last-publish" ]] || {
  echo "usage: $0 cutover|last-publish [REMOTE_URL]" >&2
  exit 2
}
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
git init --quiet --bare -- "$scratch/check.git"
fetch() {
  git -C "$scratch/check.git" fetch --quiet --no-tags --depth="$2" -- "$remote" "+refs/heads/$1:refs/check/$1" 2>/dev/null
}
fail() { echo "FAIL: $1" >&2; exit 1; }

fetch registry 2 || fail "the registry branch is absent or unreachable"
registry_files="$(git -C "$scratch/check.git" ls-tree -r --name-only refs/check/registry)"
while IFS= read -r path; do
  [[ "$path" == "$JOURNAL_PATH" || "$path" =~ $EVIDENCE_GLOB ]] \
    || fail "registry holds an unexpected path: $path"
done <<< "$registry_files"
registry_sha="$(git -C "$scratch/check.git" cat-file blob "refs/check/registry:$JOURNAL_PATH" | sha256sum | cut -d ' ' -f 1)"

if [[ "$mode" == "cutover" ]]; then
  fetch main 1 || fail "main is unreachable"
  main_sha="$(git -C "$scratch/check.git" cat-file blob "refs/check/main:$JOURNAL_PATH" | sha256sum | cut -d ' ' -f 1)"
  echo "main     journal sha256: $main_sha"
  echo "registry journal sha256: $registry_sha"
  [[ "$main_sha" == "$registry_sha" ]] || fail "journals differ"
  echo "OK: byte-identical journals; registry holds only Core registry data."
  exit 0
fi

tip="$(git -C "$scratch/check.git" rev-parse refs/check/registry)"
parents="$(git -C "$scratch/check.git" rev-list --parents -n 1 "$tip" | wc -w)"
[[ "$parents" -eq 2 ]] || fail "the registry tip is not a single-parent commit (or has no parent)"
changes="$(git -C "$scratch/check.git" diff-tree --no-commit-id --name-status -r "$tip^" "$tip")"
[[ "$(printf '%s\n' "$changes" | wc -l)" -eq 2 ]] || fail "the tip changes other than two paths"
[[ "$(printf '%s\n' "$changes" | sed -n 2p)" == "$(printf 'M\t%s' "$JOURNAL_PATH")" ]] \
  || fail "the tip does not modify exactly the journal"
added="$(printf '%s\n' "$changes" | sed -n 1p)"
[[ "${added:0:2}" == "$(printf 'A\t')" && "${added:2}" =~ $EVIDENCE_GLOB ]] \
  || fail "the tip does not add exactly one Core evidence file"
echo "registry tip:            $tip"
echo "registry journal sha256: $registry_sha"
echo "OK: the tip changes only the journal and adds ${added:2}."
