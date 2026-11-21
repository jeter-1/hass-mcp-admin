import json, os, subprocess
from pathlib import Path
src = Path("run.py").read_text().split("results = {}")[0]
ns = {"__file__": str(Path("run.py").resolve())}; exec(src, ns)
rel = ns["IP"]
old = 'source_type in GLOBAL_SOURCE_TYPES\n                        else "direct_ha_api" if selected else "none"'
new = 'source_type in GLOBAL_SOURCE_TYPES\n                        and item.provider != "none"\n                        else "direct_ha_api" if selected else "none"'
p = ns["TREE"] / rel; s = p.read_text(); assert s.count(old) == 1
p.write_text(s.replace(old, new))
try:
    r = subprocess.run([ns["PY"], "-m", "unittest", ns["TEST"]], cwd=ns["TREE"], capture_output=True, text=True,
                       env=dict(os.environ, TMPDIR=str(Path("../tmp").resolve()), PYTHONDONTWRITEBYTECODE="1"))
    lines = r.stderr.splitlines()
    out = {"M56": {"file": "provider.py", "mutant": "an unread partition row (provider none) is attributed to direct_ha_api",
                   "status": "killed" if r.returncode else "SURVIVED",
                   "failed_tests": [l for l in lines if l.startswith(("FAIL:", "ERROR:"))], "tail": lines[-1:]}}
finally:
    p.write_text(s)
Path("results_m56.json").write_text(json.dumps(out, indent=1)); print(out)
