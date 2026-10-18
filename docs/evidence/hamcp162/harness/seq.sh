set -u
R="bash harness/run.sh"
cat_retry(){ for i in $(seq 1 30); do $R catalog "$1" "$2" >>state/seq.log 2>&1 && return 0; sleep 2; done; echo "catalog failed $1" >>state/seq.log; return 1; }
$R stopmcp hamcp162-mcp-850-A
for spec in "8.5.0 B on" "8.5.0 OFF off" "8.4.3 A on" "8.4.3 B on" "8.4.3 OFF off"; do
  set -- $spec; N=$($R mcp $1 $2 $3); echo "started $N" >>state/seq.log
  cat_retry $N $1-$2
  if [ "$2" = A ]; then $R cases $N $1 > state/cases-${1//./}.stdout 2> state/cases-${1//./}.stderr; echo "cases $1 rc=$?" >>state/seq.log; fi
  $R stopmcp $N
done
echo DONE >>state/seq.log
