#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 ]]; then
  echo "usage: assert_protected_release_ancestor.sh RELEASE_SHA PROTECTED_MAIN_SHA" >&2
  exit 2
fi

release_sha="$1"
protected_main_sha="$2"
sha_pattern='^[0-9a-f]{40}$'

if [[ ! "$release_sha" =~ $sha_pattern || ! "$protected_main_sha" =~ $sha_pattern ]]; then
  echo "release and protected-main revisions must be full lowercase commit SHAs" >&2
  exit 1
fi
if ! git cat-file -e "${release_sha}^{commit}" 2>/dev/null \
  || ! git cat-file -e "${protected_main_sha}^{commit}" 2>/dev/null; then
  echo "release and protected-main revisions must resolve to local commits" >&2
  exit 1
fi
if ! git merge-base --is-ancestor "$release_sha" "$protected_main_sha"; then
  echo "the reviewed release commit is not an ancestor of protected main" >&2
  exit 1
fi

echo "Verified reviewed release commit ${release_sha} in protected-main history."
