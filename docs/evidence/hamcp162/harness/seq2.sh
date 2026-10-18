set -u
R="bash harness/run.sh"
cat_retry(){ for i in $(seq 1 30); do $R catalog "$1" "$2" >>state/seq.log 2>&1 && return 0; sleep 2; done; echo "catalog failed $1" >>state/seq.log; return 1; }
for v in 8.5.0 8.4.3; do
  N=$($R mcp $v A2 on); echo "started $N" >>state/seq.log
  cat_retry $N $v-A2
  $R cases $N $v > state/cases-${v//./}.stdout 2> state/cases-${v//./}.stderr; echo "cases $v rc=$?" >>state/seq.log
  $R stopmcp $N
done
echo DONE2 >>state/seq.log
