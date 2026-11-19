#!/usr/bin/env bash
# CODEX-022 refusal tests for the policy-locks workflow. Josh runs this by hand
# against the throwaway repository jeter-1/rules-day-scratch ONLY; it refuses
# every other target. It seeds the scratch main from an exact hass-mcp-admin
# commit, opens four pull requests and compares each check with the expected
# result:
#   (a) contents: write plus a release step  -> policy-workflow-lint fails
#   (b) a synthetic secret-pattern fixture    -> policy-secret-scan fails
#   (c) policy-locks.yml edited to always pass, plus a violation
#                                            -> main's lint still runs and fails
#   (d) a clean change                        -> both checks pass
# Usage: policy-locks-scratch-refusal.sh <hass-mcp-admin commit sha>
#        policy-locks-scratch-refusal.sh --check-push-target <clone>  (guard only)
# Needs: gh authenticated as jeter-1, git, Actions enabled on the scratch repo,
# and pull_request_target allowed for policy-locks.yml there. Pushes never go
# through a remote name: git runs without global or system configuration and
# pushes to the exact scratch URL, after checking that the clone cannot
# redirect it. The case-* branches are never force-pushed, so a rerun
# needs a fresh scratch repository or the old case-* branches deleted by hand.
set -euo pipefail

readonly SCRATCH="jeter-1/rules-day-scratch"
readonly SCRATCH_URL="https://github.com/$SCRATCH.git"
readonly SOURCE="jeter-1/hass-mcp-admin"
readonly CHECKS=(policy-workflow-lint policy-secret-scan)

# Isolated git: no global, system or environment-supplied configuration.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0
unset GIT_CONFIG GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT GIT_DIR GIT_WORK_TREE \
  GIT_PUSH_OPTION_COUNT
# Without global configuration, commits need an explicit identity.
export GIT_AUTHOR_NAME="CODEX-022 refusal test" GIT_COMMITTER_NAME="CODEX-022 refusal test"
export GIT_AUTHOR_EMAIL="jeter-1@users.noreply.github.com"
export GIT_COMMITTER_EMAIL="jeter-1@users.noreply.github.com"
# Credentials come only from gh, named explicitly for each network command.
readonly GH_CREDENTIAL=(-c credential.helper= -c 'credential.helper=!gh auth git-credential')

refuse_push() {
  echo "Refusing to push: $1 Use a fresh clone of $SCRATCH_URL with no pushurl," \
    "insteadOf, pushInsteadOf or include settings, then rerun." >&2
  exit 3
}

assert_push_target() {  # clone directory; local checks only, no network
  local urls rewrites includes status=0
  urls="$(git -C "$1" remote get-url --push --all origin 2>/dev/null)" ||
    refuse_push "the clone has no origin remote."
  [[ "$urls" == "$SCRATCH_URL" ]] ||
    refuse_push "origin's push URL is not exactly $SCRATCH_URL."
  # Exit status 1 means no match; anything else is an error, never a pass.
  rewrites="$(git -C "$1" config --get-regexp '^url\..*\.(push)?insteadof$')" || status=$?
  includes="$(git -C "$1" config --get-regexp '^include(if)?\.')" || status=$((status | $?))
  (( status <= 1 )) || refuse_push "git could not read the clone's configuration."
  [[ -z "$rewrites" ]] || refuse_push "the clone rewrites URLs (url.*.insteadOf)."
  [[ -z "$includes" ]] || refuse_push "the clone includes other configuration files."
}

push_scratch() {  # refspec...
  assert_push_target .
  git "${GH_CREDENTIAL[@]}" push --quiet "$SCRATCH_URL" "$@"
}

if [[ "${1:-}" == "--check-push-target" ]]; then
  [[ -n "${2:-}" ]] || { echo "Pass the clone directory to check." >&2; exit 2; }
  assert_push_target "$2"
  echo "Push target is exactly $SCRATCH_URL."
  exit 0
fi

source_sha="${1:-}"
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "Pass the exact 40-character hass-mcp-admin commit to test." >&2; exit 2; }
[[ "${REPOSITORY:-$SCRATCH}" == "$SCRATCH" ]] || {
  echo "This script only targets $SCRATCH; unset REPOSITORY." >&2; exit 2; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
gh repo view "$SCRATCH" --json nameWithOwner --jq .nameWithOwner | grep -qx "$SCRATCH"
read -r -p "Seed main of $SCRATCH from $SOURCE@$source_sha and open 4 PRs? [y/N] " answer
[[ "$answer" == "y" || "$answer" == "Y" ]] || { echo "Cancelled."; exit 1; }

git "${GH_CREDENTIAL[@]}" clone --quiet "$SCRATCH_URL" "$work/repo"
cd "$work/repo"
assert_push_target .
# Work on main even when the scratch repository is still empty.
if git rev-parse --verify --quiet HEAD >/dev/null; then
  git checkout --quiet -B main
else
  git symbolic-ref HEAD refs/heads/main
fi
mkdir -p .github/workflows scripts
for path in .github/workflows/policy-locks.yml scripts/policy_locks.py \
            scripts/policy_locks.requirements.txt; do
  gh api "repos/$SOURCE/contents/$path?ref=$source_sha" --jq .content | base64 -d > "$path"
done
cat > .github/workflows/check.yml <<'YAML'
name: check
on: pull_request
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-24.04
    steps:
      - run: echo read-only
YAML
echo "rules-day scratch" > README.md
git add -A
git commit --quiet -m "Seed policy locks from $SOURCE@$source_sha" || true
push_scratch HEAD:main

open_case() {  # name, title; the working tree holds the change
  git checkout --quiet -b "case-$1"
  git add -A
  git commit --quiet -m "$2"
  push_scratch "refs/heads/case-$1:refs/heads/case-$1"
  gh pr create --repo "$SCRATCH" --base main --head "case-$1" --title "$2" \
    --body "CODEX-022 refusal test $1." >/dev/null
  git rev-parse HEAD > "$work/$1.sha"
  git checkout --quiet main
}

git checkout --quiet main
cat > .github/workflows/release.yml <<'YAML'
name: release
on: pull_request
permissions:
  contents: write
jobs:
  ship:
    runs-on: ubuntu-24.04
    steps:
      - run: gh release create v0.0.1
YAML
open_case a "(a) contents: write plus a release step"

# Assembled at run time; synthetic and not a credential.
printf 'fixture = "%s"\n' "s""k-SYNTHETICrulesDay0fixture00" > fixture.txt
open_case b "(b) synthetic secret-pattern fixture"

sed -i 's#python -I base/scripts/policy_locks.py lint --base base --head pr-head#true#' \
  .github/workflows/policy-locks.yml
grep -q '^          true$' .github/workflows/policy-locks.yml
cat > .github/workflows/release.yml <<'YAML'
name: release
on: pull_request
permissions:
  contents: write
jobs:
  ship:
    runs-on: ubuntu-24.04
    steps:
      - run: gh release create v0.0.1
YAML
open_case c "(c) policy-locks.yml edited to always pass"

echo "clean change" >> README.md
open_case d "(d) clean change"

expected() {  # case, check -> success|failure
  case "$1:$2" in
    a:policy-workflow-lint|b:policy-secret-scan|c:policy-workflow-lint) echo failure ;;
    *) echo success ;;
  esac
}

failures=0
for name in a b c d; do
  sha="$(cat "$work/$name.sha")"
  for check in "${CHECKS[@]}"; do
    conclusion=""
    for _ in $(seq 1 60); do
      conclusion="$(gh api "repos/$SCRATCH/commits/$sha/check-runs?check_name=$check&filter=latest" \
        --jq '[.check_runs[] | select(.status == "completed") | .conclusion] | first // ""')"
      [[ -n "$conclusion" ]] && break
      sleep 10
    done
    want="$(expected "$name" "$check")"
    if [[ "$conclusion" == "$want" ]]; then
      echo "PASS ($name) $check: $conclusion"
    else
      echo "FAIL ($name) $check: got '${conclusion:-none}', expected $want"
      failures=$((failures + 1))
    fi
  done
done
echo "Scratch PRs stay open on $SCRATCH for inspection; close or delete them by hand."
exit $((failures > 0))
