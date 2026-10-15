"""HAMCP-181 PR1 architecture guard (design section 5).

The dispatch-facts projection is display only.  Eligibility, claim, rollback
admission and cancellation keep deciding from the stored dispatch intent
(``_task_is_dispatched`` and the F3 records), never from the projection.
Public projections never fall back to a bare ``bool(task.dispatched_at)`` or
``bool(task.provider_attempts)``.  The guard is source-level (AST) so a new
consumer, or a decision that starts consulting the projection, fails here
and gets reviewed.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
PROJECTION = "f3_runtime/dispatch_facts.py"

# Modules allowed to import the projection.  Each is a display host.
PROJECTION_HOSTS = {
    "f3_runtime/runtime.py",
    "governance/service.py",
    "fan/audit.py",
}

# Modules that hold eligibility, claim, rollback admission, cancellation or
# dispatch decisions on their own.  None may import the projection.
DECISION_MODULES = (
    "f3_runtime/repository.py",
    "f3_runtime/registry.py",
    "f3_runtime/health_scan.py",
    "governance/configuration_eligibility.py",
    "governance/configuration_rollback_eligibility.py",
    "governance/configuration_reverification.py",
    "governance/historical_policy.py",
    "governance/operational_lifecycle.py",
    "governance/policy.py",
    "governance/storage.py",
    "governance/task_models.py",
    "governance/task_storage.py",
    "fan/service.py",
    "fan/adapter.py",
    "fan/locks.py",
    "power/service.py",
    "power/adapter.py",
    "routing.py",
    "tools/governance.py",
)

# Calls that return projection values (or an error built from them).
FACT_PRODUCERS = {
    "_dispatch_facts",
    "_f3_plan_dispatch_facts",
    "dispatch_facts_for_task",
    "public_task_facts",
    "dispatch_fact_fields",
    "setter_not_sent_fields",
    "duplicate_apply_message",
    "cancel_refusal_message",
    "_cancel_refusal",
    "_child_audit_facts",
}

# Every function that consumes the projection, per host.  A new entry is a
# review point: it must be a display path, never an admission decision.
EXPECTED_CONSUMERS = {
    "f3_runtime/runtime.py": {
        "_child_audit_facts",
        "F3RuntimeIntegration._audit_record_events",
        "F3RuntimeIntegration._reconciliation_steps",
        "F3RuntimeIntegration.handle_legacy_apply",
        "F3RuntimeIntegration.dispatch_facts_for_task",
        "F3RuntimeIntegration.public_task_facts",
        "F3RuntimeIntegration._apply",
        "F3RuntimeIntegration._cancel_refusal",
        "F3RuntimeIntegration._cancel_locked",
        "F3RuntimeIntegration._decorate_task",
        "F3RuntimeIntegration.decorate_task",
        "F3RuntimeIntegration._dispatch_outcome_counts",
        "_setter_call_not_sent_recorded",
    },
    "governance/service.py": {
        "ChangeGovernanceService._build_health_summary",
        "ChangeGovernanceService._dispatch_facts",
        "ChangeGovernanceService._f3_plan_dispatch_facts",
        "ChangeGovernanceService._project_f3_operational_block",
        "ChangeGovernanceService._public",
        "ChangeGovernanceService._public_task_with_facts",
        "ChangeGovernanceService._task_plan_projection",
        "ChangeGovernanceService._record",
        "ChangeGovernanceService._task_audit",
        "ChangeGovernanceService.cancel_refusal_message",
        "ChangeGovernanceService.cancel_execution_task",
        "ChangeGovernanceService.duplicate_apply_message",
        "ChangeGovernanceService.list_execution_tasks",
        "ChangeGovernanceService._terminal_task_apply_result",
        "ChangeGovernanceService._decorate_task_result",
    },
    "fan/audit.py": {"dispatch_fact_fields", "FanExecutionRepository.project"},
}

LEGACY_FIELDS = {"dispatched_at", "provider_attempts"}
PROJECTED_KEYS = {
    "provider_dispatch_occurred",
    "dispatch_performed",
    "changed_by_engineering",
    "dispatch_phase",
}
STORED_INTENT_PREDICATE = "_task_is_dispatched"


def _tree(relative: str) -> ast.Module:
    return ast.parse((RUNTIME / relative).read_text(encoding="utf-8"))


def _imports_projection(tree: ast.Module) -> set[str]:
    """Local names bound to the projection module or its members."""

    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if module.endswith("dispatch_facts"):
            names |= {alias.asname or alias.name for alias in node.names}
        for alias in node.names:
            if alias.name == "dispatch_facts" and not module.endswith(
                "dispatch_facts"
            ):
                names.add(alias.asname or alias.name)
        if isinstance(node, ast.Import):  # pragma: no cover - not used
            continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith("dispatch_facts"):
                    names.add(alias.asname or alias.name)
    return names


def _functions(tree: ast.Module):
    """Yield (qualified name, node) for every function, nested included."""

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                yield from walk(child, prefix + [child.name])
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = ".".join(prefix + [child.name])
                yield qualified, child
                yield from walk(child, prefix + [child.name])

    yield from walk(tree, [])


def _own_nodes(function: ast.AST):
    """Nodes of a function body, excluding nested function bodies."""

    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _is_fact_reference(node: ast.AST, imported: set[str]) -> bool:
    if isinstance(node, ast.Name) and node.id in imported:
        return True
    if isinstance(node, ast.Call) and _call_name(node) in FACT_PRODUCERS:
        return True
    return False


def _consumers(relative: str) -> set[str]:
    tree = _tree(relative)
    imported = _imports_projection(tree)
    found = set()
    for qualified, function in _functions(tree):
        if any(_is_fact_reference(node, imported) for node in _own_nodes(function)):
            found.add(qualified)
    return found


def _tainted_names(function: ast.AST, imported: set[str]) -> set[str]:
    """Local names assigned (transitively) from a projection value."""

    tainted: set[str] = set()
    for arg in function.args.args + function.args.kwonlyargs:
        annotation = arg.annotation
        if annotation is not None and any(
            isinstance(item, ast.Name) and item.id in {"DispatchFacts", "ChildFacts"}
            for item in ast.walk(annotation)
        ):
            tainted.add(arg.arg)
    changed = True
    while changed:
        changed = False
        for node in _own_nodes(function):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            source = any(
                _is_fact_reference(item, imported)
                or (isinstance(item, ast.Name) and item.id in tainted)
                for item in ast.walk(node.value)
            )
            if not source:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for item in ast.walk(target):
                    if isinstance(item, ast.Name) and item.id not in tainted:
                        tainted.add(item.id)
                        changed = True
    return tainted


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _on_refusal_path(node: ast.AST, function: ast.AST, parents) -> bool:
    """True when ``node`` sits in a raise, or in a block that ends by raising."""

    current = node
    while current is not function:
        parent = parents[current]
        # A branch condition is the decision itself, never a refusal detail.
        if (
            isinstance(parent, (ast.If, ast.While, ast.IfExp, ast.Assert))
            and current is parent.test
        ):
            return False
        if isinstance(parent, ast.comprehension) and current in parent.ifs:
            return False
        if isinstance(current, ast.Raise):
            return True
        for field in ("body", "orelse", "handlers", "finalbody"):
            block = getattr(parent, field, None)
            if isinstance(block, list) and current in block:
                if block and isinstance(block[-1], ast.Raise):
                    return True
        current = parent
    return False


def _decision_functions(tree: ast.Module) -> dict[str, ast.AST]:
    """Functions that decide eligibility, claim, rollback or cancellation."""

    decisions = {}
    for qualified, function in _functions(tree):
        short = qualified.rsplit(".", 1)[-1]
        calls_stored_predicate = any(
            isinstance(node, ast.Call) and _call_name(node) == STORED_INTENT_PREDICATE
            for node in _own_nodes(function)
        )
        lowered = short.lower()
        named_decision = short == STORED_INTENT_PREDICATE or any(
            token in lowered
            for token in ("rollback", "claim", "eligib", "admission", "_cancel_locked")
        )
        if calls_stored_predicate or named_decision:
            decisions[qualified] = function
    return decisions


class ProjectionImportBoundaryTests(unittest.TestCase):
    def test_only_display_hosts_import_the_projection(self):
        actual = set()
        for path in sorted(RUNTIME.rglob("*.py")):
            relative = path.relative_to(RUNTIME).as_posix()
            if relative == PROJECTION:
                continue
            if _imports_projection(ast.parse(path.read_text(encoding="utf-8"))):
                actual.add(relative)
        self.assertEqual(actual, PROJECTION_HOSTS)

    def test_decision_modules_do_not_import_the_projection(self):
        for relative in DECISION_MODULES:
            path = RUNTIME / relative
            with self.subTest(module=relative):
                self.assertTrue(path.exists(), relative)
                source = path.read_text(encoding="utf-8")
                self.assertEqual(_imports_projection(ast.parse(source)), set())
                self.assertNotIn("dispatch_facts", source)

    def test_projection_imports_no_f3_and_no_runtime_host(self):
        tree = _tree(PROJECTION)
        relative_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertFalse(alias.name.startswith("ha_mcp_engineering"))
            if isinstance(node, ast.ImportFrom) and node.level:
                relative_modules.add(("." * node.level) + (node.module or ""))
            elif isinstance(node, ast.ImportFrom):
                self.assertIn(
                    node.module,
                    {"__future__", "dataclasses", "datetime", "typing"},
                )
        # The only package import is the shared no-op text; no ..f3, so the
        # packaging approved-import set is unchanged (design section 5).
        self.assertEqual(relative_modules, {"..governance.entity_metadata"})

    def test_service_and_routing_still_do_not_import_f3(self):
        for relative in ("governance/service.py", "routing.py"):
            tree = _tree(relative)
            with self.subTest(module=relative):
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.level:
                        module = node.module or ""
                        self.assertFalse(
                            module == "f3" or module.startswith("f3."),
                            f"{relative} imports ..{module}",
                        )


class ProjectionConsumerTests(unittest.TestCase):
    def test_projection_consumers_are_the_reviewed_display_paths(self):
        for relative, expected in EXPECTED_CONSUMERS.items():
            with self.subTest(module=relative):
                self.assertEqual(_consumers(relative), expected)

    def test_decision_functions_consult_the_projection_only_to_word_a_refusal(self):
        checked = 0
        for relative in sorted(PROJECTION_HOSTS):
            tree = _tree(relative)
            imported = _imports_projection(tree)
            parents = _parents(tree)
            for qualified, function in _decision_functions(tree).items():
                tainted = _tainted_names(function, imported)
                for node in _own_nodes(function):
                    reference = _is_fact_reference(node, imported) or (
                        isinstance(node, ast.Name)
                        and isinstance(node.ctx, ast.Load)
                        and node.id in tainted
                    )
                    if not reference:
                        continue
                    checked += 1
                    with self.subTest(function=qualified, line=node.lineno):
                        self.assertTrue(
                            _on_refusal_path(node, function, parents),
                            f"{relative}:{node.lineno} {qualified} consults the "
                            "dispatch-facts projection outside a refusal",
                        )
        # The cancel refusals really are covered (not vacuous).
        self.assertGreater(checked, 0)

    def test_stored_intent_predicate_is_unchanged(self):
        tree = _tree("governance/service.py")
        functions = dict(_functions(tree))
        predicate = functions[f"ChangeGovernanceService.{STORED_INTENT_PREDICATE}"]
        returns = [node for node in ast.walk(predicate) if isinstance(node, ast.Return)]
        self.assertEqual(len(returns), 1)
        self.assertEqual(
            ast.unparse(returns[0].value),
            "bool(task.dispatched_at or task.provider_attempts)",
        )
        imported = _imports_projection(tree)
        self.assertFalse(
            any(_is_fact_reference(node, imported) for node in _own_nodes(predicate))
        )

    def test_eligibility_call_sites_keep_the_stored_intent_predicate(self):
        tree = _tree("governance/service.py")
        callers = {
            qualified.rsplit(".", 1)[-1]
            for qualified, function in _functions(tree)
            if any(
                isinstance(node, ast.Call)
                and _call_name(node) == STORED_INTENT_PREDICATE
                for node in _own_nodes(function)
            )
        }
        # Cancellation admission still reads the stored intent.
        self.assertIn("cancel_execution_task", callers)
        # S4/S5: the plan projection moved to the facts object.
        self.assertNotIn("_task_plan_projection", callers)
        self.assertNotIn("_public", callers)


class LegacyPredicateTests(unittest.TestCase):
    @staticmethod
    def _bare_legacy(node: ast.AST) -> bool:
        """``bool(x.dispatched_at)``, ``bool(x.provider_attempts)`` or an or/and of them."""

        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "bool"
            and len(node.args) == 1
        ):
            return False
        argument = node.args[0]
        operands = argument.values if isinstance(argument, ast.BoolOp) else [argument]
        return all(
            isinstance(item, ast.Attribute) and item.attr in LEGACY_FIELDS
            for item in operands
        )

    @staticmethod
    def _reads_legacy(node: ast.AST) -> bool:
        return any(
            isinstance(item, ast.Attribute) and item.attr in LEGACY_FIELDS
            for item in ast.walk(node)
        )

    def test_no_bare_legacy_predicate_outside_the_stored_intent_decision(self):
        found = []
        for path in sorted(RUNTIME.rglob("*.py")):
            relative = path.relative_to(RUNTIME).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for qualified, function in _functions(tree):
                for node in _own_nodes(function):
                    if self._bare_legacy(node):
                        found.append(f"{relative}:{qualified}")
        self.assertEqual(
            found, [f"governance/service.py:ChangeGovernanceService.{STORED_INTENT_PREDICATE}"]
        )

    def test_public_dispatch_fields_are_not_derived_from_legacy_stored_fields(self):
        for path in sorted(RUNTIME.rglob("*.py")):
            relative = path.relative_to(RUNTIME).as_posix()
            if relative == PROJECTION:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                pairs = []
                if isinstance(node, ast.Dict):
                    pairs = [
                        (key.value, value)
                        for key, value in zip(node.keys, node.values)
                        if isinstance(key, ast.Constant)
                    ]
                elif isinstance(node, ast.Call):
                    pairs = [(item.arg, item.value) for item in node.keywords]
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Subscript)
                            and isinstance(target.slice, ast.Constant)
                        ):
                            pairs.append((target.slice.value, node.value))
                for key, value in pairs:
                    if key in PROJECTED_KEYS:
                        with self.subTest(path=relative, line=value.lineno, key=key):
                            self.assertFalse(
                                self._reads_legacy(value),
                                f"{relative}:{value.lineno} derives {key} from "
                                "dispatched_at/provider_attempts",
                            )


if __name__ == "__main__":
    unittest.main()
