#!/bin/bash
# mk.sh <id> : fresh archive copy of HEAD + edited tests + mutant applied
set -e
W=/home/josh/repos/hass-mcp-admin/.claude/worktrees/bridge-cse_01UBjbMUUnbLfW3b7w69Mnnp
D=/var/tmp/hamcp181pr2-tmp/gap/$1
rm -rf "$D"; mkdir -p "$D"
git -C $W archive HEAD | tar -x -C "$D"
cp $W/tests/test_hamcp181_proven_no_call_producers.py $W/tests/test_hamcp181_terminal_outcome_t2.py "$D/tests/"
/var/tmp/hamcp181pr2-env/bin/python /var/tmp/hamcp181pr2-tmp/mut/scripts/apply.py "$D" "$1"
