"""Add the review-1 rerun to results.json, keeping the campaign record unchanged.

usage: build_review1.py <worktree> <results.json> <rerun commit>
"""
import importlib.util, json, os, re, subprocess, sys
WT, OUT, COMMIT = sys.argv[1], sys.argv[2], sys.argv[3]
MUT = "/var/tmp/hamcp181pr2-tmp/mut3"; LOGS = "/var/tmp/hamcp181pr2-tmp/logs/mut3"
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
data = json.load(open(OUT))
campaign = {e["id"]: e for e in data["mutants"]}
# Mutants whose edit changed because the code they mutate changed in review 1.
RESPECIFIED = {"HEALTH1", "HEALTH2", "HEALTH3"}
out = []
for mid, (concept, edits, fast) in mod.M.items():
    mine = [r for r in rows if r[0] == mid]
    fast_row = next((r for r in mine if r[1] == "fast"), None)
    entry = {"id": mid}
    if mid not in campaign:
        entry["status"] = "new_in_review_1"
    elif mid in RESPECIFIED:
        entry["status"] = "respecified_in_review_1"
    else:
        entry["status"] = "campaign_spec"
    if entry["status"] != "campaign_spec":
        entry.update({"concept": concept, "sites": sites.get(mid, []),
                      "edits": [{"file": rel, "old": old, "new": new} for rel, old, new in edits],
                      "fast_modules": fast})
    if fast_row and fast_row[-1] == "KILLED":
        entry["verdict"] = "KILLED"; entry["killed_by_stage"] = "fast"
        entry["killing_tests"] = fails(f"mut-{mid}.log")
    else:
        full = [r for r in mine if r[1].startswith("full:")]
        killer = next((r for r in full if len(r) > 3 and r[3].startswith("FAILED")), None)
        if killer:
            m = killer[1].split(":", 1)[1]
            entry["verdict"] = "KILLED"; entry["killed_by_stage"] = f"full:{m}"
            entry["killing_tests"] = fails(f"mut-{mid}-full-{m}.log")
        else:
            entry["verdict"] = "SURVIVED" if mine else "NOT_RUN"
    out.append(entry)
data["review_1_rerun"] = {
    "mutant_base": COMMIT,
    "note": ("PR2 review 1 (M1, M2, L1): every current spec rerun with the committed harness on a "
             "git-archive copy of the mutant base, fast modules first, then the full set one module "
             "per process. The campaign entries above are unchanged; HEALTH1-3 were respecified "
             "because review 1 replaced the code they mutate."),
    "log_directory": LOGS,
    "total": len(out),
    "killed": sum(e["verdict"] == "KILLED" for e in out),
    "survived": sum(e["verdict"] == "SURVIVED" for e in out),
    "not_run": sum(e["verdict"] == "NOT_RUN" for e in out),
    "mutants": out,
}
json.dump(data, open(OUT, "w"), indent=2); open(OUT, "a").write("\n")
r = data["review_1_rerun"]
print(r["total"], r["killed"], r["survived"], r["not_run"])
for e in out:
    if e["verdict"] == "KILLED" and not e.get("killing_tests"): print("no names:", e["id"])
