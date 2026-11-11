#!/usr/bin/env bash
# HAMCP-297 lane runner: disposable Core 2026.11.0 + test-only Matter Server stub on an internal
# bridge (no egress, no published ports). Explicit --memory on every container. No live HA.
# Test-only lane asset; never a product path. Launch through the heavy-job gate (kind lane).
# Output goes to $LANE_OUT (default: <repo>/.artifacts/lanes/hamcp297_matter_server_stub).
set -euo pipefail
ASSET=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$ASSET/../../.." && pwd)
LANE_OUT=${LANE_OUT:-$REPO/.artifacts/lanes/hamcp297_matter_server_stub}
IMG=ghcr.io/home-assistant/home-assistant@sha256:c6c1ca9931036bf3a2c4cc088d8db25dfea3b33935a5ecc83c40349462e2f69e
RUN=hamcp297-lane-$(date +%Y%m%d%H%M%S)
NET=$RUN-net
VOL=$RUN-config
OUT=$LANE_OUT/$RUN
LABELS=(--label "com.docker.compose.project=$RUN" --label "org.hamcp.lane=$RUN")
mkdir -p "$OUT"
chmod 0777 "$OUT"
cleanup() {
  set +e
  for c in core stub; do docker logs "$RUN-$c" > "$OUT/$c.log" 2>&1; done
  docker rm -f "$RUN-core" "$RUN-stub" "$RUN-driver" >/dev/null 2>&1
  docker volume rm "$VOL" >/dev/null 2>&1
  docker network rm "$NET" >/dev/null 2>&1
  echo "cleanup done: $(docker ps -a --filter label=org.hamcp.lane=$RUN -q | wc -l) containers left"
}
trap cleanup EXIT
echo "run=$RUN image=$IMG"
docker image inspect "$IMG" --format 'image id {{.Id}}'
sha256sum "$ASSET/stub.py" "$ASSET/driver.py" "$ASSET"/nodes/*.json | tee "$OUT/hashes.txt"
docker network create --internal --driver bridge "${LABELS[@]}" "$NET" >/dev/null
docker volume create "${LABELS[@]}" "$VOL" >/dev/null
docker run --detach --pull never --name "$RUN-stub" "${LABELS[@]}" --network "$NET" --network-alias stub \
  --memory 256m --security-opt no-new-privileges --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$ASSET,target=/lane,readonly" \
  --mount "type=bind,source=$ASSET/nodes,target=/nodes,readonly" \
  --mount "type=bind,source=$OUT,target=/out" \
  --entrypoint python3 "$IMG" -I /lane/stub.py --nodes /nodes --out /out/stub.jsonl >/dev/null
docker run --detach --pull never --name "$RUN-core" "${LABELS[@]}" --network "$NET" --network-alias core \
  --memory 2g --security-opt no-new-privileges \
  --mount "type=volume,source=$VOL,target=/config" "$IMG" >/dev/null
set +e
timeout 900 docker run --rm --pull never --name "$RUN-driver" "${LABELS[@]}" --network "$NET" \
  --memory 256m --security-opt no-new-privileges --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$ASSET,target=/lane,readonly" \
  --mount "type=bind,source=$OUT,target=/out" \
  --entrypoint python3 "$IMG" -I /lane/driver.py /out/driver-result.json 2>&1 | tee "$OUT/driver.log"
rc=${PIPESTATUS[0]}
echo "driver_rc=$rc"
exit $rc
