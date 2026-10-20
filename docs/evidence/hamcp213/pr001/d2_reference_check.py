"""Design-validation prototype for HAMCP-213 D2 (physical-effect equivalence).

NOT runtime code and not a proposed implementation: a small reference model of the
rules in docs/HAMCP_213_SAFETY_CRITICAL_APPROVAL_DESIGN.md D2, used to show which
verdicts the rules give for the anonymized PR001 fixtures and that targeted
mutations flip them. Standard library plus PyYAML only; no repository imports.

Usage: python -I d2_reference_check.py <fixture dir>
"""
import copy, json, pathlib, sys
import yaml

FIXTURES = pathlib.Path(sys.argv[1])
PLAN = [("op1", "automation"), ("op2", "automation"), ("op3", "script"), ("op4", "automation")]

# Closed tables (design proposal, D2.1).
EFFECT_PREFIXES = ("cover.", "lock.", "alarm_control_panel.", "valve.", "siren.")
EFFECT_EXACT = {"homeassistant.turn_on", "homeassistant.turn_off", "homeassistant.toggle"}
TRANSITIVE = ("script.", "automation.trigger", "scene.turn_on")
REFERENCE_ONLY_EXACT = {"logbook.log", "system_log.write"}
REFERENCE_ONLY_PREFIXES = ("notify.", "persistent_notification.")
DISPLAY_KEYS = {"message", "title", "name"}
CONTEXT_KEYS = ("triggers", "trigger", "conditions", "condition", "mode", "max", "max_exceeded",
                "variables", "fields", "use_blueprint")
COMPOSITE_KEYS = ("choose", "default", "if", "then", "else", "repeat", "parallel", "sequence")


def protected(entity_id):
    # Floor rule as shipped (risk.py:719); D5 would add registry device_class.
    return isinstance(entity_id, str) and entity_id.startswith("cover.") and "garage" in entity_id


def service(step):
    s = step.get("action") or step.get("service")
    return s if isinstance(s, str) and "." in s else None


def targets(step):
    out = []
    for holder in (step.get("target") or {}, step):
        v = holder.get("entity_id") if isinstance(holder, dict) else None
        out += v if isinstance(v, list) else [v] if v else []
    return sorted(str(x) for x in out)


def is_reference(svc):
    return svc in REFERENCE_ONLY_EXACT or svc.startswith(REFERENCE_ONLY_PREFIXES)


def is_effect(step):
    svc = service(step)
    if not svc or is_reference(svc):
        return False
    if svc.startswith(TRANSITIVE):
        return True
    hits = [t for t in targets(step) if protected(t)]
    if svc.startswith(EFFECT_PREFIXES) or svc in EFFECT_EXACT:
        return bool(hits)
    return bool(hits)  # unknown service on a protected target is an effect (conservative)


def children(step):
    """Yield (label, step list, in_loop) for every nested step list."""
    if not isinstance(step, dict):
        return
    for j, opt in enumerate(step.get("choose") or []):
        yield f"choose[{j}]", opt.get("sequence") or [], False
    if "choose" in step and step.get("default"):
        yield "default", step["default"], False
    if "if" in step:
        yield "then", step.get("then") or [], False
        yield "else", step.get("else") or [], False
    if isinstance(step.get("repeat"), dict):
        yield "repeat", step["repeat"].get("sequence") or [], True
    for k, branch in enumerate(step.get("parallel") or []):
        yield f"parallel[{k}]", branch if isinstance(branch, list) else [branch], False
    if isinstance(step.get("sequence"), list) and "choose" not in step and "repeat" not in step:
        yield "sequence", step["sequence"], False


def contains_effect(steps):
    for s in steps if isinstance(steps, list) else [steps]:
        if is_effect(s) or any(contains_effect(lst) for _, lst, _ in children(s)):
            return True
    return False


def shell(step):
    """The step minus nested step lists and alias (the lists are normalized separately)."""
    s = {k: v for k, v in step.items() if k not in ("alias",)}
    if "choose" in s:
        s["choose"] = [{k: v for k, v in o.items() if k != "sequence"} for o in s["choose"]]
        s.pop("default", None)
    for k in ("then", "else", "parallel"):
        s.pop(k, None)
    if isinstance(s.get("repeat"), dict):
        s["repeat"] = {k: v for k, v in s["repeat"].items() if k != "sequence"}
    if isinstance(s.get("sequence"), list):
        s.pop("sequence")
    return s


def normalize(steps, effect_follows, mode):
    """Erase non-aborting reference steps per `mode`; keep everything else (D2.4).

    mode "proposed": a reference-only step with continue_on_error: true is erased
    wherever it is (it can neither abort nor gate an effect).
    mode "conservative" (OD-10 alternative): such a step is erased only when no
    effect follows it, so a push added before a close (added latency) is a change.
    Non-reference steps (waits, delays, conditions, other services) are always kept
    anywhere in the operation: that is the run-occupancy signature (OD-10).
    """
    out = []
    for i, step in enumerate(steps):
        later = effect_follows or contains_effect(steps[i + 1:])
        svc = service(step)
        if svc and is_reference(svc):
            coe = step.get("continue_on_error") is True
            if coe and (mode == "proposed" or not later):
                continue  # non-aborting display step: erased
            data = {k: v for k, v in (step.get("data") or {}).items() if k not in DISPLAY_KEYS | {"data"}}
            text = {k: v for k, v in (step.get("data") or {}).items()
                    if k in DISPLAY_KEYS and isinstance(v, str) and "{{" in v}
            out.append({"ref_call": svc, "targets": targets(step), "data": data,
                        "templated_text": text, "continue_on_error": coe})
            continue
        node = {"step": shell(step)}
        for label, lst, loop in children(step):
            follows = later or (loop and contains_effect(lst))
            node[label] = normalize(lst, follows, mode)
        out.append(node)
    return out


def effects(steps, path=""):
    found = []
    for i, step in enumerate(steps):
        here = f"{path}[{i}]"
        if is_effect(step):
            found.append((service(step), [t for t in targets(step) if protected(t)] or targets(step), here))
        for label, lst, _ in children(step):
            found += effects(lst, f"{here}.{label}")
    return found


def affordances(config):
    ids = set()
    def walk(x):
        if isinstance(x, dict):
            svc = service(x)
            if svc and svc.startswith("notify."):
                for a in ((x.get("data") or {}).get("data") or {}).get("actions") or []:
                    if isinstance(a, dict) and a.get("action"):
                        ids.add(a["action"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(config)
    return ids


def body(config, rtype):
    return config.get("sequence" if rtype == "script" else "actions") or []


def verdict(cur, prop, rtype, mode):
    eb, ea = effects(body(cur, rtype)), effects(body(prop, rtype))
    if not eb and not ea:
        return "none", []
    reasons = []
    for key in CONTEXT_KEYS:
        if json.dumps(cur.get(key), sort_keys=True) != json.dumps(prop.get(key), sort_keys=True):
            reasons.append(f"context_changed:{key}")
    if [e[:2] for e in eb] != [e[:2] for e in ea]:
        reasons.append("effect_set_changed")
    nb = normalize(body(cur, rtype), False, mode)
    na = normalize(body(prop, rtype), False, mode)
    if json.dumps(nb, sort_keys=True) != json.dumps(na, sort_keys=True):
        reasons.append("normalized_run_structure_changed")
    return ("changed" if reasons else "equivalent"), reasons


def load(op):
    return (yaml.safe_load((FIXTURES / f"{op}_before.yaml").read_text()),
            yaml.safe_load((FIXTURES / f"{op}_after.yaml").read_text()))


def report(label, cur, prop, rtype):
    eb, ea = effects(body(cur, rtype)), effects(body(prop, rtype))
    print(f"== {label} ({rtype}) protected effects before={len(eb)} after={len(ea)}")
    for svc, tgt, path in ea:
        print(f"   effect: {svc} {tgt} at {path}")
    for mode in ("proposed", "conservative"):
        v, reasons = verdict(cur, prop, rtype, mode)
        print(f"   verdict[{mode}]: {v} {reasons}")
    ab, aa = affordances(cur), affordances(prop)
    print(f"   notification action ids: before={sorted(ab)} after={sorted(aa)} added={sorted(aa - ab)}")


for op, rtype in PLAN:
    report(op, *load(op), rtype)

# Mutations: each must make the verdict `changed` under both modes.
print("== mutations (expected: changed under both modes)")
def mutate(name, op, rtype, fn):
    cur, prop = load(op)
    prop = copy.deepcopy(prop)
    fn(prop)
    results = {m: verdict(cur, prop, rtype, m)[0] for m in ("proposed", "conservative")}
    ok = all(v == "changed" for v in results.values())
    print(f"   {'PASS' if ok else 'FAIL'} {name}: {results}")
    return ok

def first_override_branch(p):
    # op2: first departure branch -> qualifying choose -> sequence holding the close.
    seq = p["actions"][0]["choose"][0]["sequence"][6]["choose"][0]["sequence"]
    assert service(seq[2]) == "cover.close_cover" and protected(seq[2]["target"]["entity_id"])
    return seq

def m_remove_coe(p):
    note = first_override_branch(p)[0]["choose"][0]["sequence"][1]
    assert service(note).startswith("notify.") and note.pop("continue_on_error") is True

def m_delay_before_close(p):
    first_override_branch(p).insert(2, {"delay": "00:05:00"})

def m_wait_after_close(p):
    first_override_branch(p).append({"wait_template": "{{ false }}", "timeout": "00:10:00"})

def m_script_mode(p):
    p["mode"] = "restart"

def m_script_target(p):
    p["sequence"][0]["choose"][1]["sequence"][0]["target"]["entity_id"] = "cover.garage_side"

def m_add_close_to_warning(p):
    p["actions"][0]["choose"][1]["sequence"].append(
        {"action": "cover.close_cover", "target": {"entity_id": "cover.garage_main"}})

def m_condition_before_close(p):
    first_override_branch(p).insert(2, {"condition": "state", "entity_id": "input_boolean.visitor_mode", "state": "on"})

ok = all([
    mutate("op2 added push loses continue_on_error", "op2", "automation", m_remove_coe),
    mutate("op2 delay inserted before a close", "op2", "automation", m_delay_before_close),
    mutate("op2 condition inserted before a close", "op2", "automation", m_condition_before_close),
    mutate("op2 long wait appended after a close (run occupancy)", "op2", "automation", m_wait_after_close),
    mutate("op3 mode single -> restart", "op3", "script", m_script_mode),
    mutate("op3 close target changed", "op3", "script", m_script_target),
    mutate("op1 gains a cover.close_cover", "op1", "automation", m_add_close_to_warning),
])
print("== all mutations detected:", ok)
