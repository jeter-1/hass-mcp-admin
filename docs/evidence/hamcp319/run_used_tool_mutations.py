"""HAMCP-319 evidence: mutants that remove the exact used-tool admission checks.

Josh decision relayed by the architect, 2026-11-07: every upstream tool
Engineering uses on ha-mcp 8.5.0 and 9.1.0 must match its reviewed descriptor
exactly. Each mutant removes one runtime check and must be killed by an
assertion failure in tests/test_hamcp319_used_tool_exact_admission.py; an
error, import failure or survival is not a kill.

Run from the repository root with the test interpreter, after committing:

    python docs/evidence/hamcp319/run_used_tool_mutations.py

Everything runs in a disposable ``git clone --shared`` of HEAD under
``.artifacts/mutation-hamcp319-used-tools/``; the tree under test is never
mutated. Offline: no network, container, Home Assistant or MCP endpoint.
Results are written to ``results.json`` there and copied by hand to
``docs/evidence/hamcp319/used-tool-mutation-results.json``.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / ".artifacts/mutation-hamcp319-used-tools"
MODULE = "tests.test_hamcp319_used_tool_exact_admission"
PKG = "hass_mcp_engineering_beta/ha_mcp_engineering/"
POLICY = PKG + "upstream_tool_policy.py"
GATEWAY = PKG + "providers/upstream_read_gateway.py"
DASHBOARD = PKG + "providers/upstream_dashboard.py"
YAML = PKG + "providers/upstream_yaml_reader.py"

shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True)
HEAD = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
COPY = OUT / "copy"
subprocess.check_call(["git", "clone", "-q", "--shared", "--no-checkout", str(ROOT), str(COPY)])
subprocess.check_call(["git", "-C", str(COPY), "checkout", "-q", "--force", HEAD])
ENV = {**os.environ, "PYTHONPATH": os.pathsep.join([str(COPY / "hass_mcp_engineering_beta"), str(COPY / "tests")]),
       "TMPDIR": str(OUT)}


def run(test: str, log: str) -> tuple[int, bytes]:
    for cache in COPY.rglob("__pycache__"):
        shutil.rmtree(cache)
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", f"{MODULE}.{test}" if test else MODULE],
                          cwd=COPY, env=ENV, capture_output=True, timeout=1800)
    raw = proc.stdout + proc.stderr
    (OUT / log).write_bytes(raw)
    return proc.returncode, raw


def assertion_failure(code: int, raw: bytes) -> bool:
    return bool(code) and b"FAIL:" in raw and b"AssertionError" in raw and b"ERROR:" not in raw


COMPONENTS = ("        if any(\n            value != getattr(contract, component)\n"
              "            for component, value in observed.items()\n        ):\n            return False\n")
GATEWAY_RUNTIME = ("    elif (\n        reviewed_runtime_contract_fingerprint is not None\n"
                   "        and observed_runtime_contract_fingerprint\n"
                   "        != reviewed_runtime_contract_fingerprint\n    ):\n")
mutants = [
    ("typed/dashboard-write: drop the per-component descriptor comparison", POLICY, COMPONENTS, "",
     "TypedControlExactAdmission.test_each_field_class_change_of_each_used_tool_fails_closed"),
    ("typed/dashboard-write: drop only the whole-descriptor runtime fingerprint component", POLICY,
     "            for component, value in observed.items()\n",
     '            for component, value in observed.items() if component != "runtime_contract_fingerprint"\n',
     "TypedControlExactAdmission.test_each_field_class_change_of_each_used_tool_fails_closed"),
    ("typed/dashboard-write: accept a duplicated used tool", POLICY,
     "            or len(descriptors[name]) != 1\n", "            or len(descriptors[name]) < 1\n",
     "TypedControlExactAdmission.test_missing_or_duplicate_used_tool_fails_closed"),
    ("dashboard-write: drop the dependency check entirely", DASHBOARD,
     "        admitted = validate_reviewed_release_dependencies(\n",
     "        admitted = True or validate_reviewed_release_dependencies(\n",
     "DashboardExactAdmission.test_each_field_class_change_of_guide_or_setter_refuses_writes"),
    ("reads: drop the whole-descriptor runtime fingerprint comparison", GATEWAY, GATEWAY_RUNTIME,
     "    elif False:\n",
     "ReadGatewayExactAdmission.test_title_only_change_fails_closed_for_every_used_read"),
    ("reads: accept a duplicated used read (non-strict-full catalogs)", GATEWAY,
     "                    continue\n                if len(observed) != 1:\n",
     "                    continue\n                if len(observed) < 1:\n",
     "ReadGatewayExactAdmission.test_duplicate_used_read_fails_closed"),
    ("dashboard-read: drop the new exact getter enforcement (HAMCP-319)", DASHBOARD,
     "            if handshake.server_version in EXACT_USED_TOOL_RELEASES and (\n",
     "            if False and (\n",
     "DashboardExactAdmission.test_each_field_class_change_of_the_getter_fails_closed"),
    ("dashboard-read: shrink the exact release set to 9.1.0 only", POLICY,
     'EXACT_USED_TOOL_RELEASES = frozenset({"8.5.0", "9.1.0"})\n',
     'EXACT_USED_TOOL_RELEASES = frozenset({"9.1.0"})\n',
     "DashboardExactAdmission.test_each_field_class_change_of_the_getter_fails_closed"),
    ("yaml: drop the v2 fingerprint comparison", YAML,
     "    if observed == binding.runtime_contract_fingerprint:\n", "    if True:\n",
     "YamlReaderExactAdmission.test_each_field_class_change_fails_closed"),
    ("yaml: accept a duplicated reader", YAML,
     "    if len(occurrences) > 1:\n        return OptionalMatch(\"drift\", \"optional_descriptor_duplicate\", None)\n",
     "", "YamlReaderExactAdmission.test_missing_or_duplicate_reader_fails_closed"),
]

results = {"head": HEAD, "python": sys.version.split()[0], "module": MODULE, "mutants": [], "control": None}
for index, (name, target, before, after, test) in enumerate(mutants, 1):
    path = COPY / target
    original = path.read_text()
    assert original.count(before) == 1, (name, original.count(before))
    path.write_text(original.replace(before, after))
    code, raw = run(test, f"mutant-{index:02d}.log")
    classification = "assertion" if assertion_failure(code, raw) else ("invalid" if code else "survived")
    results["mutants"].append({"id": index, "name": name, "file": target, "test": test,
                               "classification": classification, "exit_code": code})
    path.write_text(original)
    print("mutant", index, name, classification, flush=True)

code, raw = run("", "control.log")
ran = [line for line in raw.decode(errors="replace").splitlines() if line.startswith("Ran ")]
results["control"] = {"name": "control (unmutated copy, whole module)",
                      "classification": "pass" if code == 0 else "fail", "exit_code": code,
                      "ran": ran[-1] if ran else None}
(OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps(results["control"]), flush=True)
if code or any(item["classification"] != "assertion" for item in results["mutants"]):
    sys.exit(1)
