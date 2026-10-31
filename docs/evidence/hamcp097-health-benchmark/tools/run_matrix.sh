#!/usr/bin/env bash
# Interleaved before/after benchmark for one scale: run_matrix.sh SCALE ROUNDS
set -euo pipefail
cd "$(dirname "$0")/../.."
export TMPDIR=$PWD/.artifacts/tmp
S=$1; R=${2:-2}; OUT=.artifacts/h097/results; mkdir -p $OUT
PY=.artifacts/venv/bin/python; B=.artifacts/h097/bench_health.py; ST=.artifacts/h097/store_x$S
for r in $(seq 1 $R); do
  $PY $B --store $ST --calls 5 --source-root .artifacts/h097/base --cold-mode plain --label x$S --json-out $OUT/x${S}_r${r}_before_plain.json > $OUT/x${S}_r${r}_before_plain.log 2>&1
  $PY $B --store $ST --calls 5 --source-root . --cold-mode startup_validation --label after_x$S --json-out $OUT/x${S}_r${r}_after_sv.json > $OUT/x${S}_r${r}_after_sv.log 2>&1
  $PY $B --store $ST --calls 5 --source-root . --cold-mode plain --label after_x$S --json-out $OUT/x${S}_r${r}_after_plain.json > $OUT/x${S}_r${r}_after_plain.log 2>&1
done
echo done x$S
