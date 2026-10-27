"""HAMCP-225 review-1 correction mutations: one source edit at a time in a disposable copy."""
import json, subprocess, sys, time
from pathlib import Path

ROOT = Path(sys.argv[1]); LOGS = Path(sys.argv[2]); PY = sys.argv[3]
only = set(sys.argv[4:])
E = "hass_mcp_engineering_beta/ha_mcp_engineering/"
SVC, NORM, CON = E + "trace_snapshots/service.py", E + "trace_normalization.py", E + "trace_snapshots/contracts.py"
CORE = ["tests.test_hamcp225_trace_snapshots", "tests.test_hamcp225_review1_reviewer_probes",
        "tests.test_hamcp225_review1_store_probes", "tests.test_hamcp225_review1_wire_probes"]
CROSS = CORE + ["tests.test_hamcp225_cross_path_credentials"]
STRICT = "__import__('ha_mcp_engineering.sanitization', fromlist=['x']).sanitize_untrusted_data"
MUTANTS = [
    # Review H1 (architect-named)
    ("H1-shared-screen-skipped", NORM,
     "screened, withheld, categories, failed = screen_trace_value(detail, known_secrets)",
     "screened, withheld, categories, failed = detail, 0, set(), False", CROSS),
    ("H1-trace-specific-screen", NORM,
     "decided[text] = credentials.screen(text, known_secrets, labelled=True)",
     "decided[text] = credentials.WITHHELD_MARKER if __import__('re').search("
     "r'(?i)password|bearer|token|secret', text) else None", CROSS),
    ("H1-keys-not-screened", NORM,
     "found = marker(key if type(key) is str else str(key))", "found = None", CROSS),
    ("H1-screen-after-storage", SVC,
     "first = sanitize_trace_detail(detail, known_secrets=self.known_secrets, registry=registry)",
     "first = " + STRICT + "(detail, known_secrets=self.known_secrets, max_string=2000, registry=registry)",
     CROSS),
    ("H1-get-automation-trace-unscreened", E + "tools/compatibility.py",
     '"data": sanitized.value,', '"data": result,', CROSS),
    # Review H2
    ("H2-identifier-kept-raw", SVC, "if identifier_cleared(value, self.known_secrets):", "if True:", CORE),
    ("H2-gateway-audit-copies-raw", E + "routing.py",
     "        if not trace_identifier_cleared(value, known_secrets):\n            withheld = True\n            return None\n",
     "", CORE),
    ("H2-digest-form-accepted", CON,
     "if (type(value) is not str or not AUTOMATION_CONFIG_ID_PATTERN.fullmatch(value)\n            or WITHHELD_ID.fullmatch(value)):",
     "if type(value) is not str or not AUTOMATION_CONFIG_ID_PATTERN.fullmatch(value):", CORE),
    # Review H3
    ("H3-quarantine-unbounded", SVC, "        if overflow > 0:\n            quarantine",
     "        if False:\n            quarantine", CORE),
    # Review M1-M3
    ("M1-first-poll-discards", SVC, 'and _ended_after(h, watch["created_at_ms"])]', "and False]", CORE),
    ("M2-no-rotation", SVC, "first = self.rotation % len(watches)", "first = 0", CORE),
    ("M2-no-per-watch-budget", SVC, "await self._poll_watch(watch, authority, time.monotonic() + share)",
     "await self._poll_watch(watch, authority, deadline)", CORE),
    ("M3-no-recheck-before-commit", SVC,
     "            del detail\n        if not self._still_authorized(authority):",
     "            del detail\n        if False:", CORE),
    # Reviewer's cross-watch eviction mutation
    ("R-cross-watch-eviction", SVC, 'if e["watch_id"] == watch_id),\n',
     'if e["watch_id"] is not None),\n', CORE),
    # Design note section 7 targets
    ("D-partial-flag", SVC, '"completeness": "partial" if trace_sanitization_partial(first) else "complete"',
     '"completeness": "complete"', CORE),
    ("D-domain-literal", SVC, '{"type": "trace/get", "domain": "automation",',
     '{"type": "trace/get", "domain": "script",', CORE),
    ("D-quota-boundary", SVC, "len(own) - c.WATCH_QUOTA + 1", "len(own) - c.WATCH_QUOTA", CORE),
    ("D-cursor-caller-unbound", SVC, '(value + ":" + caller).encode()', "value.encode()", CORE),
]

def run(name, modules):
    log = LOGS / f"{name}.log"
    start = time.time()
    with open(log, "w") as out:
        r = subprocess.run([PY, "-m", "unittest", *modules], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                           timeout=1800, env={"TMPDIR": "/var/tmp/hamcp225-mut/tmp", "PATH": "/usr/bin:/bin"})
    text = log.read_text(errors="replace")
    failing = [l for l in text.splitlines() if l.startswith(("FAIL:", "ERROR:"))]
    return r.returncode, failing, round(time.time() - start, 1), str(log)

results = []
rc, failing, secs, log = run("baseline", sorted(set(CROSS)))
results.append({"id": "baseline", "returncode": rc, "result": "PASS" if rc == 0 else "FAIL", "seconds": secs, "log": log})
print("baseline", rc, flush=True)
if rc != 0:
    sys.exit("baseline failed")
for name, path, old, new, modules in MUTANTS:
    if only and name not in only:
        continue
    target = ROOT / path
    source = target.read_text()
    assert source.count(old) == 1, (name, source.count(old))
    try:
        target.write_text(source.replace(old, new))
        rc, failing, secs, log = run(name, modules)
    finally:
        target.write_text(source)
    verdict = "KILLED" if rc else "SURVIVED"
    results.append({"id": name, "file": path, "result": verdict, "killing_tests": failing[:12],
                    "killing_count": len(failing), "seconds": secs, "log": log})
    print(name, verdict, len(failing), flush=True)
(LOGS / "results.json").write_text(json.dumps(results, indent=2))
print("SURVIVED:", [r["id"] for r in results if r["result"] == "SURVIVED"])
