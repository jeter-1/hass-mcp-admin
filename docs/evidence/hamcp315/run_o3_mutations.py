"""HAMCP-315: copy repository inputs; count only assertion-killed O3 mutants."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
TARGET = "scripts/core_release_evidence.py"
SUITE = "tests.test_core_o3_union_manifest"
MUTANTS = (
    ("one_sided_absence_equal", "if left is None and right is None:", "if left is None or right is None:",
     SUITE + ".UnionComparison.test_cross_layout_additions_and_removal_require_judgments"),
    ("guard_dropped", "    validate_manifest_coverage(paths)\n", "    # mutation: existence guard omitted\n",
     SUITE + ".ExistenceGuard.test_typo_absent_from_every_bound_release_fails_naming_path"),
    ("both_absent_refused", "if left is None and right is None:\n        return True",
     "if left is None and right is None:\n        return False",
     SUITE + ".UnionComparison.test_october_self_accepts_21_shared_absences"),
)
RUNNER = '''import json,sys,unittest
suite=unittest.defaultTestLoader.loadTestsFromName(sys.argv[1])
result=unittest.TextTestRunner(verbosity=2).run(suite)
print(json.dumps({"tests":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),
                  "failed_tests":[test.id() for test,trace in result.failures]}))
sys.exit(0 if result.wasSuccessful() else 1)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".artifacts/o3-mutations")
    args = parser.parse_args()
    OUT = args.output.resolve()
    if ROOT / ".artifacts" not in OUT.parents:
        raise ValueError("mutation output must be task-local scratch")
    COPY = OUT / "copy"
    OUT.mkdir()  # Never replace a prior mutation record.
    COPY.mkdir()
    names = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT)
    for name in sorted(set(names.decode().split("\0")) - {""}):
        source = ROOT / name
        if source.is_file():
            destination = COPY / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    (COPY / ".artifacts").mkdir(exist_ok=True)
    original = (ROOT / TARGET).read_bytes()
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(COPY / ".artifacts")}
    rows = []
    for name, old, new, test in (("control", "", "", SUITE), *MUTANTS):
        text = original.decode()
        if name != "control":
            assert text.count(old) == 1, "mutation anchor is ambiguous"
            text = text.replace(old, new, 1)
        (COPY / TARGET).write_text(text)
        run = subprocess.run([sys.executable, "-c", RUNNER, test], cwd=COPY, env=env,
                             capture_output=True, text=True, timeout=120)
        (OUT / (name + ".log")).write_text(run.stdout + run.stderr)
        receipt = json.loads(run.stdout.strip().splitlines()[-1])
        passed = (run.returncode == 0 and receipt["failures"] == receipt["errors"] == 0
                  if name == "control" else run.returncode == 1 and receipt["failures"] > 0 and receipt["errors"] == 0)
        rows.append({"name": name, "test_target": test, **receipt, "accepted": passed})
        assert passed, "control failed or mutant was not killed by assertions"
    (COPY / TARGET).write_bytes(original)
    assert (ROOT / TARGET).read_bytes() == original, "original tree changed"
    value = {"model": "hamcp315-o3-mutations-v1", "copied_tree": True,
             "source_sha256": hashlib.sha256(original).hexdigest(), "results": rows}
    (OUT / "results.json").write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps(value, indent=2))


if __name__ == "__main__":
    main()
