"""Static cross-module trace of Core transports reachable from a tool function.

Reads source text from a git object database with `git show <rev>:<path>`.
Nothing from the repository is imported, checked out or executed.

Usage: trace_reads.py <git-dir-or-worktree> <rev> <path>::<function> [...]
"""
import ast
import subprocess
import sys

REPO, REV = sys.argv[1], sys.argv[2]
ROOT = "src/ha_mcp/"
REST = ROOT + "client/rest_client.py"
CLIENTISH = {"client", "_client", "ws", "ws_client", "_ws", "websocket_client", "ha_client"}
_cache = {}


def show(path):
    r = subprocess.run(["git", "-C", REPO, "show", f"{REV}:{path}"], capture_output=True)
    return r.stdout.decode("utf-8") if r.returncode == 0 else None


def load(path):
    if path in _cache:
        return _cache[path]
    text = show(path)
    if text is None:
        _cache[path] = None
        return None
    tree = ast.parse(text)
    defs, imports = {}, {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.setdefault(node.name, node)
        elif isinstance(node, ast.ImportFrom):
            base = path.rsplit("/", 1)[0]
            if node.level:
                for _ in range(node.level - 1):
                    base = base.rsplit("/", 1)[0]
            elif node.module and node.module.startswith("ha_mcp"):
                base = "src"
            else:
                continue
            mod = (node.module or "").replace(".", "/")
            target = base + ("/" + mod if mod else "")
            for alias in node.names:
                imports[alias.asname or alias.name] = (target, alias.name)
    _cache[path] = (defs, imports)
    return _cache[path]


def resolve_module(target, name):
    for cand in (target + ".py", target + "/__init__.py", target + "/" + name + ".py"):
        mod = load(cand)
        if mod is None:
            continue
        defs, imports = mod
        if name in defs:
            return cand, name
        if name in imports:  # re-export
            t2, n2 = imports[name]
            got = resolve_module(t2, n2)
            if got:
                return got
    return None


def chain(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    else:
        parts.append("?")
    return list(reversed(parts))


def literal(call):
    out = []
    for a in list(call.args[:2]) + [k.value for k in call.keywords if k.arg in ("type",)]:
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            out.append(a.value)
        elif isinstance(a, ast.Dict):
            for k, v in zip(a.keys, a.values):
                if isinstance(k, ast.Constant) and k.value == "type":
                    out.append(v.value if isinstance(v, ast.Constant) else "<dynamic type>")
        elif isinstance(a, ast.JoinedStr):
            out.append("".join(p.value if isinstance(p, ast.Constant) else "{}" for p in a.values))
        elif isinstance(a, (ast.Name, ast.Attribute, ast.Call, ast.BinOp)):
            out.append("<dynamic>")
    return " ".join(str(x) for x in out)


def trace(start_path, start_fn):
    seen, todo = set(), [(start_path, start_fn, start_fn)]
    found, unresolved = set(), set()
    rest_methods = load(REST)[0]
    while todo:
        path, fn, via = todo.pop()
        if (path, fn) in seen:
            continue
        seen.add((path, fn))
        mod = load(path)
        if mod is None or fn not in mod[0]:
            continue
        defs, imports = mod
        short = path[len(ROOT):]
        for node in ast.walk(defs[fn]):
            if not isinstance(node, ast.Call):
                continue
            c = chain(node.func)
            last = c[-1]
            where = f"{short}:{node.lineno}"
            if path == REST and c[:1] == ["self"] and last in ("_request", "_raw_request", "guarded_request"):
                found.add(("REST", literal(node), where))
                continue
            if path == REST and "httpx_client" in c:
                found.add(("REST", "httpx " + last + " " + literal(node), where))
                continue
            if last == "send_websocket_message":
                found.add(("WS", literal(node), where))
                continue
            if last == "send_command":
                found.add(("WS", literal(node), where))
                continue
            if last in ("_request", "_raw_request", "guarded_request") and any(p in CLIENTISH for p in c):
                found.add(("REST", literal(node), where))
                continue
            if len(c) >= 2 and any(p in CLIENTISH for p in c[:-1]) and last in rest_methods:
                todo.append((REST, last, where))
                continue
            if len(c) == 2 and c[0] in ("self", "cls") and last in defs:
                todo.append((path, last, where))
                continue
            if len(c) == 1:
                if last in defs:
                    todo.append((path, last, where))
                elif last in imports:
                    got = resolve_module(*imports[last])
                    if got:
                        todo.append((got[0], got[1], where))
                continue
            if len(c) >= 2 and c[0] in ("self", "cls") and last not in defs and not last.startswith("__"):
                if c[1] not in ("_client", "client") and len(c) >= 3:
                    unresolved.add(".".join(c) + "@" + where)
                elif len(c) == 2:
                    unresolved.add(".".join(c) + "@" + where)
    return found, unresolved


for spec in sys.argv[3:]:
    path, fn = spec.split("::")
    found, unresolved = trace(ROOT + path, fn)
    print(f"== {fn} ({path})")
    for kind, lit, where in sorted(found):
        print(f"  {kind:4} {lit:60.60} {where}")
    if unresolved:
        print("  unresolved: " + "; ".join(sorted(unresolved))[:700])
