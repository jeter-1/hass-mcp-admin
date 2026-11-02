#!/usr/bin/env bash
# Core registry publication (HAMCP-278); bash, git and sha256sum only.
#
#   core_registry_publish.sh assemble   build and check the one commit, push nothing
#   core_registry_publish.sh publish    the same, then push it non-force to `registry`
#
# The publish job holds a contents:write token and never the signing secret,
# so this script deliberately runs no Python or package installer. Signature
# verification already happened in the read-only reverify job; here the signed
# bytes are bound to that job and to the signer by the manifest digest.
#
# Inputs (environment): SIGNED_DIR, MANIFEST_SHA256 (signer's manifest),
# REVERIFIED_MANIFEST_SHA256 (reverify job's manifest), PREDECESSOR_SHA256
# (journal the candidate was verified against), CORE_VERSION, EVIDENCE_SHA256,
# REMOTE_URL, WORK_DIR (must not exist). The target ref is fixed below.
set -euo pipefail
set +x

readonly TARGET_REF="refs/heads/registry"
readonly JOURNAL_PATH="upstream-trust/ha-core-release-registry.json"
readonly SHA256_PATTERN='^[0-9a-f]{64}$'
readonly VERSION_PATTERN='^20[0-9]{2}\.(1[0-2]|[1-9])\.(0|[1-9][0-9]*)$'

refuse() {
  # Fixed reasons only; never echo supplied file contents.
  echo "::error::Core registry publication refused: $1; ${TARGET_REF#refs/heads/} is unchanged." >&2
  exit 1
}

mode="${1:-}"
[[ "$mode" == "assemble" || "$mode" == "publish" ]] || refuse "mode must be assemble or publish"
for name in SIGNED_DIR MANIFEST_SHA256 REVERIFIED_MANIFEST_SHA256 PREDECESSOR_SHA256 \
    CORE_VERSION EVIDENCE_SHA256 REMOTE_URL WORK_DIR; do
  [[ -n "${!name:-}" ]] || refuse "$name is required"
done
for name in MANIFEST_SHA256 REVERIFIED_MANIFEST_SHA256 PREDECESSOR_SHA256 EVIDENCE_SHA256; do
  [[ "${!name}" =~ $SHA256_PATTERN ]] || refuse "$name is malformed"
done
[[ "$CORE_VERSION" =~ $VERSION_PATTERN ]] || refuse "Core version is not an exact stable release"
[[ ! -e "$WORK_DIR" ]] || refuse "work directory already exists"
readonly EVIDENCE_PATH="upstream-trust/evidence/ha-core-${CORE_VERSION}-${EVIDENCE_SHA256}.json"
expected="$(printf '%s\n' "$EVIDENCE_PATH" "$JOURNAL_PATH" | LC_ALL=C sort)"

# 1. The signed artifact is exactly what the signer staged and reverify checked.
manifest="$SIGNED_DIR/SHA256SUMS"
[[ -f "$manifest" && ! -L "$manifest" ]] || refuse "signed manifest is missing"
actual_manifest="$(sha256sum -- "$manifest" | cut -d ' ' -f 1)"
[[ "$actual_manifest" == "$MANIFEST_SHA256" ]] || refuse "signed manifest digest does not match the signing job"
[[ "$actual_manifest" == "$REVERIFIED_MANIFEST_SHA256" ]] || refuse "signed manifest digest does not match the reverify job"
[[ "$(cut -c 67- "$manifest" | LC_ALL=C sort)" == "$expected" ]] || refuse "signed manifest path set is not exactly the journal and evidence"
[[ "$(cd "$SIGNED_DIR/files" && find . -mindepth 1 ! -type d -printf '%P\n' | LC_ALL=C sort)" == "$expected" ]] \
  || refuse "signed artifact path set is not exactly the journal and evidence"
[[ -z "$(cd "$SIGNED_DIR/files" && find . -mindepth 1 ! -type d ! -type f)" ]] || refuse "signed artifact contains a link or special file"
(cd "$SIGNED_DIR/files" && sha256sum --strict --check --quiet ../SHA256SUMS) >/dev/null 2>&1 \
  || refuse "signed artifact files do not match their manifest"
[[ "$(sha256sum -- "$SIGNED_DIR/files/$EVIDENCE_PATH" | cut -d ' ' -f 1)" == "$EVIDENCE_SHA256" ]] \
  || refuse "evidence digest does not match the reviewed value"

# 2. The registry branch still holds the journal the candidate was verified against.
git init --quiet -- "$WORK_DIR"
git -C "$WORK_DIR" fetch --quiet --no-tags --depth=1 -- "$REMOTE_URL" "+${TARGET_REF}:refs/remotes/origin/registry" \
  2>/dev/null || refuse "pre-cutover: the registry branch is absent or unreachable"
base="$(git -C "$WORK_DIR" rev-parse --verify --quiet 'refs/remotes/origin/registry^{commit}')" \
  || refuse "registry branch tip is not a commit"
current="$(git -C "$WORK_DIR" cat-file blob "${base}:${JOURNAL_PATH}" 2>/dev/null | sha256sum | cut -d ' ' -f 1)"
[[ "$current" == "$PREDECESSOR_SHA256" ]] \
  || refuse "registry journal changed since verification (expected the verified predecessor)"
if git -C "$WORK_DIR" cat-file -e "${base}:${EVIDENCE_PATH}" 2>/dev/null; then
  refuse "evidence file already exists on the registry branch"
fi

# 3. One commit on the fetched tip touching exactly the journal and evidence.
export GIT_INDEX_FILE="$WORK_DIR/.git/publish-index"
git -C "$WORK_DIR" read-tree "$base"
for relative in $expected; do
  blob="$(git -C "$WORK_DIR" hash-object -w -- "$SIGNED_DIR/files/$relative")"
  git -C "$WORK_DIR" update-index --add --cacheinfo "100644,${blob},${relative}"
done
tree="$(git -C "$WORK_DIR" write-tree)"
unset GIT_INDEX_FILE
commit="$(
  GIT_AUTHOR_NAME="github-actions[bot]" GIT_COMMITTER_NAME="github-actions[bot]" \
  GIT_AUTHOR_EMAIL="41898282+github-actions[bot]@users.noreply.github.com" \
  GIT_COMMITTER_EMAIL="41898282+github-actions[bot]@users.noreply.github.com" \
  git -C "$WORK_DIR" commit-tree "$tree" -p "$base" \
    -m "Publish owner-approved Core ${CORE_VERSION} registry journal"
)"
changes="$(git -C "$WORK_DIR" diff-tree --no-commit-id --name-status -r "$base" "$commit")"
[[ "$changes" == "$(printf 'A\t%s\nM\t%s' "$EVIDENCE_PATH" "$JOURNAL_PATH")" ]] \
  || refuse "commit path set is not exactly the journal (modified) and evidence (added)"
[[ "$(git -C "$WORK_DIR" rev-list --parents -n 1 "$commit")" == "$commit $base" ]] \
  || refuse "commit is not a single-parent child of the verified registry tip"
for relative in $expected; do
  [[ "$(git -C "$WORK_DIR" cat-file blob "${commit}:${relative}" | sha256sum | cut -d ' ' -f 1)" \
    == "$(sha256sum -- "$SIGNED_DIR/files/$relative" | cut -d ' ' -f 1)" ]] \
    || refuse "committed bytes differ from the signed artifact"
done

summary="${GITHUB_STEP_SUMMARY:-/dev/null}"
{
  echo "### Core registry publication (${mode})"
  echo "- Registry tip verified: \`${base}\`"
  echo "- Commit: \`${commit}\`"
  echo "- Paths: \`${JOURNAL_PATH}\` (modified), \`${EVIDENCE_PATH}\` (added)"
} >> "$summary"

if [[ "$mode" == "assemble" ]]; then
  echo "Assembled ${commit} on ${base}; nothing was pushed."
  exit 0
fi

# 4. Non-force push of that exact commit to the fixed registry ref only. A
# remote that moved since the fetch rejects the non-fast-forward update.
refspec="${commit}:${TARGET_REF}"
[[ "$refspec" =~ ^[0-9a-f]{40}:refs/heads/registry$ ]] || refuse "push target is not the registry branch"
git -C "$WORK_DIR" -c credential.helper= -c 'credential.helper=!gh auth git-credential' \
  push --quiet --no-verify -- "$REMOTE_URL" "$refspec" || refuse "non-force push to the registry branch failed"
published="$(git -C "$WORK_DIR" ls-remote -- "$REMOTE_URL" "$TARGET_REF" | cut -f 1)"
if [[ "$published" != "$commit" ]]; then
  echo "::error::The push was accepted but the registry branch does not show ${commit}; inspect it before any rerun." >&2
  exit 1
fi
echo "Published ${commit} to ${TARGET_REF#refs/heads/}."
