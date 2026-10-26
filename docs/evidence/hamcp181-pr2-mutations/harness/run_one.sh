#!/bin/bash
# run_one.sh <id> <logname> <module>...   (id "base" = unmutated)
set -u
id=$1; logname=$2; shift 2
M=/var/tmp/hamcp181pr2-tmp/mut
PY=/var/tmp/hamcp181pr2-env/bin/python
LOG=/var/tmp/hamcp181pr2-tmp/logs/mut-$logname.log
work=$M/work-$logname
while [ "$(systemctl --user is-active hamcp181pr2-sweep1 2>/dev/null)" = "active" ]; do sleep 30; done
rm -rf "$work"; cp -a $M/base "$work"
if [ "$id" != "base" ]; then
  $PY -I $M/scripts/apply.py "$work" "$id" > "$LOG.apply" 2>&1 || { echo "APPLY_FAILED" > "$LOG"; rm -rf "$work"; exit 3; }
fi
systemd-run --user --unit=hamcp181pr2-mut-$logname -p MemoryMax=2500M -p OOMPolicy=continue \
  --wait --collect --quiet --working-directory="$work" \
  --setenv TMPDIR=/var/tmp/hamcp181pr2-tmp --setenv DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \
  --setenv PYTHONPATH=tests:. \
  /bin/bash -c "timeout ${TMO:-1200} $PY -m unittest $* > $LOG 2>&1; echo EXIT=\$? >> $LOG"
rm -rf "$work"
