#!/usr/bin/env bash
# HAMCP-162 D1 orchestrator. Usage: run.sh <phase> [args]
# Every container is attached only to the --internal network hamcp162-int
# (the deps install uses --network none). Nothing is published to the host.
set -euo pipefail
W=${W:-/home/josh/tmp/hamcp162-d1}
H=$W/harness
NET=hamcp162-int
CORE=ghcr.io/home-assistant/home-assistant@sha256:130241f28d01fa80dfa3b44f7d89781c188d8642ecab9024f079f19f9f82baa0
declare -A MCPIMG=(
  [8.5.0]=ghcr.io/homeassistant-ai/ha-mcp@sha256:e1538bcdadb13a5467bbb8258fba3262a05518585e18115742da638adec7057c
  [8.4.3]=ghcr.io/homeassistant-ai/ha-mcp@sha256:d5cea47a0115e5d161c2b319ee637b1b0a5bcfafe1597cb490299bbbc6329456
)
COMPONENT_REPO=/tmp/ha-mcp-8.6.0
COMPONENT_REF=v8.6.0
SECRET_PATH=/synthetic-review/mcp

drv() {  # run the driver in the Core image on the internal network
  docker run --rm --network $NET --name hamcp162-driver \
    -v "$H":/harness:ro -v "$W/fixtures":/fixtures:ro -v "$W/state":/state \
    -v "$W/config":/config -v "$W/out":/out -v "$W/deps":/deps:ro -e PYTHONPATH=/deps \
    -e MCP_URL="${MCP_URL:-}" -e CASESET="${CASESET:-}" --entrypoint python3 $CORE /harness/driver.py "$@"
}

case "$1" in
  prepare)
    mkdir -p "$W/config" "$W/state" "$W/out" "$W/deps"
    chmod 700 "$W/state"
    docker network create --internal $NET
    cat > "$W/config/configuration.yaml" <<'YAML'
# HAMCP-162 D1 bootstrap configuration (replaced by fixtures after start-up)
homeassistant:
  name: hamcp162-d1
  time_zone: UTC
  country: US
http:
logger:
  default: info
YAML
    git -C $COMPONENT_REPO archive $COMPONENT_REF custom_components/ha_mcp_tools | tar -x -C "$W/config"
    {
      echo "component_ref=$COMPONENT_REF"
      echo "component_commit=$(git -C $COMPONENT_REPO rev-parse $COMPONENT_REF^{commit})"
      echo "component_tree=$(git -C $COMPONENT_REPO rev-parse $COMPONENT_REF:custom_components/ha_mcp_tools)"
      echo "component_manifest_version=$(python3 -c "import json;print(json.load(open('$W/config/custom_components/ha_mcp_tools/manifest.json'))['version'])")"
      (cd "$W/config" && find custom_components -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1 | sed 's/^/component_files_sha256=/')
    } > "$W/out/component.txt"
    docker run --rm --network none -v "$W/wheels":/wheels:ro -v "$W/deps":/deps --entrypoint sh $CORE -c \
      'pip install --no-index --no-deps --find-links /wheels --target /deps ruamel.yaml==0.19.1 voluptuous-openapi==0.4.1 >/dev/null && ls /deps'
    ;;
  core)
    docker run -d --name hamcp162-core --network $NET -e TZ=UTC -e PYTHONPATH=/deps \
      -v "$W/config":/config -v "$W/deps":/deps:ro $CORE >/dev/null
    docker run -d --name hamcp162-proxy --network $NET -v "$H":/harness:ro -v "$W/state":/state \
      -e PROXY_LOG=/state/proxy.jsonl --entrypoint python3 $CORE /harness/proxy.py >/dev/null
    echo started
    ;;
  setup)
    drv setup
    ;;
  mcp)  # mcp <version> <run> <beta on|off>
    V=$2; RUN=$3; BETA=$4
    NAME=hamcp162-mcp-${V//./}-$RUN
    FLAGS=(-e ENABLE_YAML_CONFIG_EDITING=false -e HA_MCP_DISABLE_SETTINGS_UI=true -e HA_MCP_DISABLE_UPDATE_CHECK=true)
    if [ "$BETA" = on ]; then
      FLAGS+=(-e ENABLE_BETA_FEATURES=true -e HAMCP_ENABLE_FILESYSTEM_TOOLS=true -e DISABLED_TOOLS=ha_write_file,ha_delete_file)
    fi
    docker run -d --name $NAME --network $NET --read-only --tmpfs /tmp:rw,size=64m -e HOME=/tmp \
      --env-file "$W/state/token.env" -e HOMEASSISTANT_URL=http://hamcp162-proxy:8124 \
      -e MCP_HOST=0.0.0.0 -e MCP_PORT=8086 -e MCP_SECRET_PATH=$SECRET_PATH "${FLAGS[@]}" \
      ${MCPIMG[$V]} ha-mcp-web >/dev/null
    echo $NAME
    ;;
  catalog)  # catalog <container> <label>
    MCP_URL=http://$2:8086$SECRET_PATH drv catalog "$3"
    ;;
  cases)  # cases <container> <label>
    MCP_URL=http://$2:8086$SECRET_PATH drv cases "$3"
    ;;
  stopmcp)
    docker logs "$2" > "$W/state/$2.log" 2>&1 || true
    docker rm -f "$2" >/dev/null
    ;;
  *) echo "unknown phase"; exit 2 ;;
esac
