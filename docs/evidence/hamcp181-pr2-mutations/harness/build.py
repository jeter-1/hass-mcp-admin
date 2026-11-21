"""Build docs/evidence/hamcp181-pr2-mutations/results.json from the campaign logs."""
import importlib.util, json, os, re, subprocess, sys
WT, OUT = sys.argv[1], sys.argv[2]
MUT = "/var/tmp/hamcp181pr2-tmp/mut"; LOGS = "/var/tmp/hamcp181pr2-tmp/logs"
spec = importlib.util.spec_from_file_location("mutants", f"{MUT}/scripts/mutants.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
check = subprocess.run([sys.executable, "-I", f"{MUT}/scripts/apply.py", WT, "--check"],
                       capture_output=True, text=True, check=True).stdout
sites = {}
for line in check.splitlines():
    mid, site, _ = line.split("\t", 2)
    sites.setdefault(mid, []).append(site)
rows = [l.rstrip("\n").split("\t") for l in open(f"{MUT}/results.tsv") if "\t" in l]
def fails(log):
    p = f"{LOGS}/{log}"
    if not os.path.exists(p): return []
    names = re.findall(r"^(?:FAIL|ERROR): (test\w+) \(([\w.]+)\)", open(p, errors="replace").read(), re.M)
    return sorted({qual for _name, qual in names})
CLOSED = {
 "M2e": ["test_hamcp181_terminal_outcome_t2.RejectedDeleteTests.test_compact_receipt_never_sheds_a_distinct_outcome_code"],
 "M6b": ["test_hamcp181_proven_no_call_producers.HelperStateGatewaySiteTests.test_unclassified_failure_after_the_command_carries_no_proof"],
 "M6d": ["test_hamcp181_proven_no_call_producers.FanProofTests.test_authority_retired_after_the_call_is_never_proven_not_sent",
         "test_hamcp181_proven_no_call_producers.PowerProofTests.test_authority_retired_after_the_call_is_never_proven_not_sent"],
 "M8b": ["test_hamcp181_proven_no_call_producers.BackupExclusionTests.test_real_provider_not_dispatched_error_after_intent_is_never_proven_not_sent"],
 "M8c": ["test_hamcp181_proven_no_call_producers.BackupExclusionTests.test_backup_pre_send_refusal_is_labelled_refused"],
 "M11c": ["test_hamcp181_proven_no_call_producers.WebSocketClientProofTests.test_connection_failure_after_the_command_carries_no_proof"],
 "SEND1": ["test_hamcp181_proven_no_call_producers.ConfigurationChainTests.test_helper_appearing_after_intent_stores_no_send_time",
           "test_hamcp181_proven_no_call_producers.EntityMetadataChainTests.test_authentication_refused_after_intent_stores_no_send_time"],
}
out = []
for mid, (concept, edits, fast) in mod.M.items():
    mine = [r for r in rows if r[0] == mid]
    fast_row = next((r for r in mine if r[1] == "fast"), None)
    entry = {"id": mid, "concept": concept, "sites": sites.get(mid, []),
             "edits": [{"file": rel, "old": old, "new": new} for rel, old, new in edits],
             "fast_modules": fast}
    if fast_row and fast_row[-1] == "KILLED":
        entry["campaign_verdict"] = "KILLED"; entry["killed_by_stage"] = "fast"
        entry["killing_tests"] = fails(f"mut-{mid}.log"); entry["logs"] = [f"mut-{mid}.log"]
    else:
        full = [r for r in mine if r[1].startswith("full:")]
        killer = next((r for r in full if len(r) > 3 and r[3].startswith("FAILED")), None)
        if mid == "M1a" and not mine:  # first attempt, single-process run
            entry["campaign_verdict"] = "KILLED"; entry["killed_by_stage"] = "fast (first attempt)"
            entry["killing_tests"] = fails("mut-M1a.log"); entry["logs"] = ["mut-M1a.log"]
        elif killer:
            m = killer[1].split(":", 1)[1]
            entry["campaign_verdict"] = "KILLED"; entry["killed_by_stage"] = f"full:{m}"
            entry["killing_tests"] = fails(f"mut-{mid}-full-{m}.log")
            entry["logs"] = [f"mut-{mid}.log", f"mut-{mid}-full-{m}.log"]
        else:
            entry["campaign_verdict"] = "SURVIVED"
            entry["logs"] = [f"mut-{mid}.log"] + [f"mut-{mid}-full-{r[1].split(':',1)[1]}.log" for r in full]
    if mid in CLOSED:
        entry["gap_closed_by"] = CLOSED[mid]
        entry["gap_closure"] = {"head": f"gap-{mid}-head.log", "mutant": f"gap-{mid}-mutant.log",
                                "mutant_result": open(f"{LOGS}/gap-{mid}-mutant.log").read().strip().splitlines()[-1]}
        entry["final_verdict"] = "KILLED"
    else:
        entry["final_verdict"] = entry["campaign_verdict"]
    out.append(entry)
summary = {
  "task": "HAMCP-181 PR2 (+HAMCP-214, +HAMCP-193)",
  "campaign_mutant_base": "4624c8947d4c378b246d139859645f01704c75f3",
  "note": ("Mutants applied to disposable git-archive copies; runtime files are unchanged between the campaign base "
           "and the PR head. Survivors were closed by new tests verified against the same mutant in a fresh copy."),
  "baseline": "16 modules, 289 tests, 0 failures (test_hamcp181_beta8_downgrade skips in archive copies: no git history)",
  "log_directory": LOGS,
  "total": len(out),
  "campaign_killed": sum(e["campaign_verdict"] == "KILLED" for e in out),
  "campaign_survived": sum(e["campaign_verdict"] == "SURVIVED" for e in out),
  "final_killed": sum(e["final_verdict"] == "KILLED" for e in out),
  "mutants": out,
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(summary, open(OUT, "w"), indent=2); open(OUT, "a").write("\n")
print(summary["total"], summary["campaign_killed"], summary["campaign_survived"], summary["final_killed"])
for e in out:
    if not e.get("killing_tests") and e["campaign_verdict"] == "KILLED": print("no names:", e["id"])
