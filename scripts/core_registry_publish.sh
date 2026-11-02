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
#
# Outcome (stderr/stdout and the job summary) and exit status:
#   0  PUBLISHED  the registry tip is the new commit. A push whose client
#                 reported failure but whose remote read shows the new commit
#                 is also PUBLISHED, with a warning.
#   1  UNCHANGED  refused before the push, or the push failed and one remote
#                 read shows the verified predecessor tip. Fix the cause and
#                 dispatch again.
#   3  UNCERTAIN  the push was dispatched and its result is not confirmed.
#                 Inspect the registry branch before any further publication.
# There is at most one push and never a retry.
set -euo pipefail
set +x

readonly TARGET_REF="refs/heads/registry"
readonly JOURNAL_PATH="upstream-trust/ha-core-release-registry.json"
readonly SHA256_PATTERN='^[0-9a-f]{64}$'
readonly VERSION_PATTERN='^20[0-9]{2}\.(1[0-2]|[1-9])\.(0|[1-9][0-9]*)$'

readonly READBACK_SECONDS=60
summary="${GITHUB_STEP_SUMMARY:-/dev/null}"
dispatched=0

refuse() {
  # Fixed reasons only; never echo supplied file contents. A refusal is
  # possible only before the push; afterwards nothing may claim "unchanged".
  if (( dispatched )); then
    uncertain "$1" ""
  fi
  echo "::error::Core registry publication refused: $1; ${TARGET_REF#refs/heads/} is unchanged." >&2
  echo "- Outcome: **UNCHANGED** (refused before any push: $1)" >> "$summary"
  exit 1
}

uncertain() {
  echo "::error::Core registry publication UNCERTAIN: $1. Attempted commit ${commit:-unknown}; observed registry tip ${2:-unavailable}. Inspect the registry branch before any further publication." >&2
  echo "- Outcome: **UNCERTAIN** ($1). Attempted commit \`${commit:-unknown}\`; observed registry tip \`${2:-unavailable}\`. Inspect the registry branch before any further publication." >> "$summary"
  exit 3
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
credentials=(-c credential.helper= -c 'credential.helper=!gh auth git-credential')
dispatched=1
push_status=0
git -C "$WORK_DIR" "${credentials[@]}" push --quiet --no-verify -- "$REMOTE_URL" "$refspec" || push_status=$?

# 5. One bounded read of the remote tip decides what to report. No retry.
# The listing is kept byte-exact in a file (command substitution would drop
# NUL bytes and trailing newlines). It must be exactly one record: 40
# lowercase hex, a tab and the exact ref, with at most one terminating
# newline. Anything else leaves the tip unknown.
readback="$WORK_DIR/.git/registry-readback"
read_status=0
timeout "$READBACK_SECONDS" git -C "$WORK_DIR" "${credentials[@]}" \
  ls-remote -- "$REMOTE_URL" "$TARGET_REF" > "$readback" 2>/dev/null || read_status=$?
tip=""
records=()
if (( read_status == 0 )) && (( $(tr -cd '\000' < "$readback" | wc -c) == 0 )); then
  mapfile -t records < "$readback"
fi
if (( ${#records[@]} == 1 )) && [[ "${records[0]}" =~ ^([0-9a-f]{40})$'\t'refs/heads/registry$ ]]; then
  tip="${BASH_REMATCH[1]}"
fi
if [[ "$tip" == "$commit" ]]; then
  if (( push_status != 0 )); then
    echo "::warning::The push client reported failure (status ${push_status}), but a remote read shows ${commit} on the registry branch: PUBLISHED." >&2
    echo "- Outcome: **PUBLISHED** (confirmed by a remote read; the push client reported failure)" >> "$summary"
  else
    echo "- Outcome: **PUBLISHED**" >> "$summary"
  fi
  echo "Published ${commit} to ${TARGET_REF#refs/heads/}."
  exit 0
fi
if (( push_status != 0 )) && [[ -n "$tip" && "$tip" == "$base" ]]; then
  echo "::error::Core registry publication failed: the push was not accepted (status ${push_status}) and a remote read shows the verified predecessor ${base}: UNCHANGED (confirmed). Attempted commit ${commit}." >&2
  echo "- Outcome: **UNCHANGED** (confirmed by a remote read after the failed push). Attempted commit \`${commit}\`." >> "$summary"
  exit 1
fi
if (( read_status != 0 )) || [[ -z "$tip" ]]; then
  uncertain "push status ${push_status}; the remote read of the registry branch failed or was not exactly one well-formed record" ""
fi
if [[ "$tip" == "$base" ]]; then
  uncertain "push status ${push_status}; the push reported success but the registry branch still shows the verified predecessor" "$tip"
fi
uncertain "push status ${push_status}; the registry branch shows neither the new commit nor the verified predecessor" "$tip"
