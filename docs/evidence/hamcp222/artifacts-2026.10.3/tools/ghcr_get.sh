#!/bin/bash
# usage: ghcr_get.sh <manifests|blobs> <digest-or-tag> <outfile>  (anonymous public pull token; -I for HEAD when outfile ends .headers-only)
set -euo pipefail
kind=$1 ref=$2 out=$3
TOK=$(curl -sSf --max-time 30 "https://ghcr.io/token?scope=repository:home-assistant/home-assistant:pull&service=ghcr.io" | python3 -I -c "import json,sys;print(json.load(sys.stdin)['token'])")
ACC="application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json,application/vnd.in-toto+json,*/*"
R=https://ghcr.io/v2/home-assistant/home-assistant
for i in 1 2 3; do
  if [[ $out == *.headers-only ]]; then
    curl -sSf --max-time 60 -I -H "Authorization: Bearer $TOK" -H "Accept: $ACC" "$R/$kind/$ref" > "${out%-only}" && exit 0
  else
    curl -sSfL --max-time 90 -H "Authorization: Bearer $TOK" -H "Accept: $ACC" -D "$out.headers" -o "$out" "$R/$kind/$ref" && exit 0
  fi
  sleep 5
done
exit 1
