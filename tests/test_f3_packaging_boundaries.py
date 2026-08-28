"""Built-image import closure and repository-wide F3 import boundaries."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
RUNTIME = BETA_DIR / "ha_mcp_engineering"
sys.path.insert(0, str(BETA_DIR))

ALLOWED_EXPLICIT_F3_IMPORTS = {
    "f3_contracts/operation_adapter.py",
    "tests/f3_synthetic_adapter.py",
    "tests/test_beta34_automation_verification.py",
    "tests/test_beta39_execution_authority.py",
    "tests/test_beta39_obligation_governance.py",
    "tests/test_beta49_helper_obligation_target_scope.py",
    "tests/test_beta50_helper_production_target_scope.py",
    "tests/test_f3_adapter_core.py",
    "tests/test_f3_contract.py",
    "tests/test_f3_dashboard_executor_conformance.py",
    "tests/test_f3_execution_persistence.py",
    "tests/test_f3_fault_injection.py",
    "tests/test_f3_lock_manager.py",
    "tests/f3_configuration_fixtures.py",
    "tests/test_f3_configuration_identity.py",
    "tests/test_f3_configuration_lifecycle.py",
    "tests/test_f3_configuration_resources.py",
    "tests/test_f3_configuration_sequence.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/f3_configuration/adapter.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/f3_configuration/locks.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/f3_configuration/models.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/f3_configuration/outcomes.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/f3_configuration/sequence.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/f3_configuration/strategies.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/f3/operational_adapter.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/f3/operational_locks.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/f3/operational_models.py",
    "hass_mcp_engineering_beta/ha_mcp_engineering/f3/operational_strategies.py",
    "tests/f3_operational_fixtures.py",
    "tests/test_f3_operational_adapter.py",
    "tests/test_f3_orphan_child_recovery.py",
    "tests/test_f3_operational_invariants.py",
    "tests/test_f3_operational_recovery.py",
    "tests/test_f3_packaging_boundaries.py",
    "tests/test_f3_runtime_integration.py",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _source_module_names() -> set[str]:
    names = {"ha_mcp_engineering"}
    for path in RUNTIME.rglob("*.py"):
        relative = path.relative_to(RUNTIME)
        if relative.name == "__init__.py":
            if relative.parent != Path("."):
                names.add(
                    "ha_mcp_engineering."
                    + ".".join(relative.parent.parts)
                )
        else:
            names.add(
                "ha_mcp_engineering."
                + ".".join(relative.with_suffix("").parts)
            )
    return names


class BuiltImageImportClosureTests(unittest.TestCase):
    def test_every_shipped_module_imports_without_repository_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            isolated = Path(temporary)
            image_app = isolated / "app"
            working = isolated / "working"
            shutil.copytree(RUNTIME, image_app / "ha_mcp_engineering")
            working.mkdir()

            script = textwrap.dedent(
                """
                import importlib
                import json
                from pathlib import Path
                import pkgutil
                import sys

                forbidden_root = Path(sys.argv[1]).resolve()
                for entry in sys.path:
                    resolved = Path(entry or Path.cwd()).resolve()
                    if resolved == forbidden_root or forbidden_root in resolved.parents:
                        raise AssertionError(f"repository path leaked into sys.path: {resolved}")

                import ha_mcp_engineering
                names = {"ha_mcp_engineering"}
                names.update(
                    item.name
                    for item in pkgutil.walk_packages(
                        ha_mcp_engineering.__path__,
                        ha_mcp_engineering.__name__ + ".",
                    )
                )
                for name in sorted(names):
                    importlib.import_module(name)
                importlib.import_module("ha_mcp_engineering.f3.contracts")

                forbidden_modules = sorted(
                    name
                    for name in sys.modules
                    if name == "f3_contracts"
                    or name.startswith("f3_contracts.")
                    or name == "tests"
                    or name.startswith("tests.")
                    or name == "f3_dashboard"
                    or name.startswith("f3_dashboard.")
                )
                if forbidden_modules:
                    raise AssertionError(
                        f"repository-only modules imported: {forbidden_modules}"
                    )
                print(json.dumps(sorted(names)))
                """
            )
            environment = {
                key: value
                for key, value in os.environ.items()
                if key not in {"PYTHONHOME", "PYTHONPATH"}
            }
            environment["PYTHONPATH"] = str(image_app)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            completed = subprocess.run(
                [sys.executable, "-P", "-c", script, str(ROOT)],
                cwd=working,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(
                completed.returncode,
                0,
                msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
            )
            imported = set(json.loads(completed.stdout))
            self.assertEqual(imported, _source_module_names())
            self.assertIn("ha_mcp_engineering.f3.contracts", imported)
            self.assertIn(
                "ha_mcp_engineering.f3_configuration.adapter", imported
            )
            self.assertIn(
                "ha_mcp_engineering.f3.operational_adapter", imported
            )
            self.assertFalse((image_app / "f3_contracts").exists())
            self.assertFalse((image_app / "f3_dashboard").exists())

    def test_every_declared_architecture_uses_the_same_package_copy(self):
        config = yaml.safe_load(
            (BETA_DIR / "config.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(config["arch"], ["amd64", "aarch64", "armv7"])
        dockerfile = (BETA_DIR / "Dockerfile").read_text(encoding="utf-8")
        self.assertEqual(
            dockerfile.count(
                "COPY ha_mcp_engineering ./ha_mcp_engineering"
            ),
            1,
        )
        self.assertNotIn("TARGETARCH", dockerfile)
        with tempfile.TemporaryDirectory() as temporary:
            copies = {}
            for architecture in config["arch"]:
                target = Path(temporary) / architecture / "ha_mcp_engineering"
                shutil.copytree(
                    RUNTIME,
                    target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
                digest = hashlib.sha256()
                for path in sorted(target.rglob("*.py")):
                    digest.update(path.relative_to(target).as_posix().encode())
                    digest.update(path.read_bytes())
                copies[architecture] = digest.hexdigest()
            self.assertEqual(len(set(copies.values())), 1, copies)

    def test_c1_shipped_sources_use_only_the_canonical_contract(self):
        c1 = RUNTIME / "f3_configuration"
        importers = set()
        for path in sorted(c1.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("f3_contracts", source)
            imported = _imports(path)
            if any(name.endswith("f3.contracts") for name in imported):
                importers.add(path.name)
        self.assertEqual(
            importers,
            {
                "adapter.py",
                "locks.py",
                "models.py",
                "outcomes.py",
                "sequence.py",
                "strategies.py",
            },
        )

    def test_c2_shipped_sources_use_canonical_objects_by_identity(self):
        from ha_mcp_engineering.f3 import contracts
        from ha_mcp_engineering.f3 import operational_models

        canonical_names = (
            "F3_ADAPTER_CONTRACT_MODEL",
            "AdapterCapabilityDescriptor",
            "DispatchResult",
            "LockMode",
            "LockRequest",
            "LockScope",
            "NormalizedOperationOutcome",
            "ObservationResult",
            "OperationTarget",
            "PreflightResult",
            "PreparedOperation",
            "RecoveryContext",
            "VerificationResult",
        )
        for name in canonical_names:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(operational_models, name),
                    getattr(contracts, name),
                )

        c2_files = {
            "operational_adapter.py",
            "operational_locks.py",
            "operational_models.py",
            "operational_strategies.py",
        }
        importers = set()
        duplicate_names = set(canonical_names) - {"F3_ADAPTER_CONTRACT_MODEL"}
        for name in c2_files:
            path = RUNTIME / "f3" / name
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("f3_contracts", source)
            self.assertNotIn("f3_dashboard", source)
            tree = ast.parse(source)
            declared = {
                node.name
                for node in tree.body
                if isinstance(node, (ast.ClassDef, ast.FunctionDef))
            }
            self.assertFalse(declared & duplicate_names)
            if any(
                value == "ha_mcp_engineering.f3.contracts"
                for value in _imports(path)
            ):
                importers.add(name)
        self.assertEqual(importers, c2_files)


class F3ImportBoundaryTests(unittest.TestCase):
    def test_shipped_modules_use_no_repository_only_packages(self):
        forbidden = {"f3_contracts", "tests"}
        for path in sorted(RUNTIME.rglob("*.py")):
            imported = _imports(path)
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertFalse(
                    any(
                        name.split(".", 1)[0] in forbidden
                        for name in imported
                    ),
                    msg=f"forbidden shipped imports: {sorted(imported)}",
                )

    def test_explicit_f3_imports_match_the_reviewed_allowlist(self):
        actual: set[str] = set()
        for path in sorted(ROOT.rglob("*.py")):
            if ".git" in path.parts:
                continue
            imported = _imports(path)
            if any(
                name == "ha_mcp_engineering.f3"
                or name.startswith("ha_mcp_engineering.f3.")
                for name in imported
            ):
                actual.add(path.relative_to(ROOT).as_posix())
        self.assertEqual(actual, ALLOWED_EXPLICIT_F3_IMPORTS)

    def test_runtime_routes_use_only_the_reviewed_f3_integration_boundary(self):
        sensitive = {
            "application.py",
            "capabilities.py",
            "governance/runtime.py",
            "governance/service.py",
            "governance/task_recovery.py",
            "providers/routing.py",
            "routing.py",
            "tools/registry.py",
        }
        for relative in sorted(sensitive):
            path = RUNTIME / relative
            if not path.exists():
                continue
            source = path.read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertNotIn("from ..f3 import", source)
                self.assertNotIn("from ..f3.", source)
                self.assertNotIn("ha_mcp_engineering.f3.", source)
                if relative not in {
                    "governance/runtime.py",
                    "governance/service.py",
                }:
                    self.assertNotIn("f3_dashboard", source)
                self.assertNotIn("f3_contracts", source)

    def test_only_the_closed_integration_package_imports_f3_runtime_internals(self):
        approved = {
            "f3_runtime/registry.py",
            "f3_runtime/repository.py",
            "f3_runtime/runtime.py",
        }
        actual = set()
        for path in sorted(RUNTIME.rglob("*.py")):
            relative = path.relative_to(RUNTIME).as_posix()
            if relative.startswith(("f3/", "f3_configuration/", "f3_dashboard/")):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                direct_relative = (
                    isinstance(node, ast.ImportFrom)
                    and node.level == 2
                    and node.module is not None
                    and (node.module == "f3" or node.module.startswith("f3."))
                )
                direct_absolute = (
                    isinstance(node, (ast.Import, ast.ImportFrom))
                    and any(
                        name == "ha_mcp_engineering.f3"
                        or name.startswith("ha_mcp_engineering.f3.")
                        for name in (
                            [alias.name for alias in node.names]
                            if isinstance(node, ast.Import)
                            else [node.module or ""]
                        )
                    )
                )
                if direct_relative or direct_absolute:
                    actual.add(relative)
        self.assertEqual(actual, approved)


if __name__ == "__main__":
    unittest.main()
