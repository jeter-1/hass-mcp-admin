#!/bin/bash
# driver.sh <id>... : fast modules first; full set (one run per module) only when fast survives.
M=/var/tmp/hamcp181pr2-tmp/mut
L=/var/tmp/hamcp181pr2-tmp/logs
PY=/var/tmp/hamcp181pr2-env/bin/python
FULL="test_hamcp181_architecture_guard test_hamcp181_audit_labels test_hamcp181_beta8_downgrade test_hamcp181_dispatch_facts test_hamcp181_entity_registry_error_unproven test_hamcp181_fan_power_dispatched_at test_hamcp181_health_core_admission test_hamcp181_proven_no_call_producers test_hamcp181_rollback_unchanged_forward test_hamcp181_surface_consistency test_hamcp181_terminal_outcome_t2 test_hamcp183_entity_registry_error_truth test_hamcp214_plan_verification_rollback_projection test_f3_runtime_integration test_hamcp135_governed_rollback test_hamcp069a_entity_metadata_execution test_hamcp181_send_time_boundary test_hamcp181_catalog_core_admission"
export TMO=2400
summ() { grep -E "^(OK|FAILED)" "$1" | tail -1; }
full() { # $1 id ; prints aggregate verdict
  local id=$1 any=0 bad=0 mod r
  for mod in $FULL; do
    bash $M/scripts/run_one.sh "$id" "$id-full-$mod" $mod
    r=$(summ $L/mut-$id-full-$mod.log)
    echo -e "$id\tfull:$mod\t$(grep -E '^Ran ' $L/mut-$id-full-$mod.log)\t${r:-NO_RESULT $(grep EXIT= $L/mut-$id-full-$mod.log)}" >> $M/results.tsv
    [[ "$r" == FAILED* ]] && any=1
    [[ -z "$r" ]] && bad=1
    [ "$id" != base ] && [ $any = 1 ] && break
  done
  if [ $any = 1 ]; then echo KILLED; elif [ $bad = 1 ]; then echo NO_RESULT; else echo SURVIVED; fi
}
for id in "$@"; do
  if [ "$id" = base ]; then v=$(full base); echo -e "base\tTOTAL\t\t\t$v" >> $M/results.tsv; continue; fi
  fast=$($PY -I -c "import importlib.util;s=importlib.util.spec_from_file_location('m','$M/scripts/mutants.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);print(' '.join(m.M['$id'][2]))")
  bash $M/scripts/run_one.sh "$id" "$id" $fast
  r=$(summ $L/mut-$id.log)
  if [[ "$r" == FAILED* ]]; then
    echo -e "$id\tfast\t$(grep -E '^Ran ' $L/mut-$id.log)\t$r\tKILLED" >> $M/results.tsv
  else
    echo -e "$id\tfast\t$(grep -E '^Ran ' $L/mut-$id.log)\t${r:-NO_RESULT}\t-" >> $M/results.tsv
    v=$(full "$id"); echo -e "$id\tTOTAL\t\t\t$v" >> $M/results.tsv
  fi
done
echo DRIVER_DONE >> $M/results.tsv
