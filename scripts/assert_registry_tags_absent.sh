#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -eq 0 ]]; then
  echo "::error::At least one registry image tag is required."
  exit 2
fi

docker_cli="${DOCKER_CLI:-docker}"

is_explicit_manifest_not_found() {
  local output="$1"
  grep -Eiq \
    'manifest unknown|no such manifest|(^|[[:space:]])ERROR:[[:space:]]+[^[:space:]]+:[[:space:]]+not found([[:space:]]|$)|unexpected status from HEAD request.+404 Not Found' \
    <<<"$output"
}

for image in "$@"; do
  set +e
  inspect_output="$("$docker_cli" buildx imagetools inspect "$image" 2>&1)"
  inspect_status=$?
  set -e

  if [[ "$inspect_status" -eq 0 ]]; then
    echo "::error::Refusing to overwrite immutable tag $image."
    exit 1
  fi

  if is_explicit_manifest_not_found "$inspect_output"; then
    echo "Confirmed registry tag is absent: $image"
    continue
  fi

  echo "::error::Unable to prove registry tag is absent; refusing publication for $image (inspect exit $inspect_status)."
  printf '%s\n' "$inspect_output" >&2
  exit 1
done
