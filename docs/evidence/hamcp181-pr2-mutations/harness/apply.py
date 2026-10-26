"""apply.py <root> <id>|--check : exact single-occurrence replacement."""
import sys, os, importlib.util
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mutants", os.path.join(here, "mutants.py"))
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
root = sys.argv[1]
ids = list(mod.M) if sys.argv[2] == "--check" else [sys.argv[2]]
write = sys.argv[2] != "--check"
bad = 0
for mid in ids:
    concept, edits, fast = mod.M[mid]
    texts = {}
    for rel, old, new in edits:
        path = os.path.join(root, rel)
        text = texts.get(rel)
        if text is None:
            text = open(path, encoding="utf-8").read()
        n = text.count(old)
        if n != 1:
            print(f"{mid}: {rel}: old occurs {n} times", file=sys.stderr); bad += 1; continue
        line = text[: text.index(old)].count("\n") + 1 + (1 if old.startswith("\n") else 0)
        print(f"{mid}\t{rel.split('ha_mcp_engineering/')[-1]}:{line}\t{concept}")
        texts[rel] = text.replace(old, new, 1)
    if write:
        for rel, text in texts.items():
            open(os.path.join(root, rel), "w", encoding="utf-8").write(text)
sys.exit(1 if bad else 0)
