"""Built-image import closure and repository-wide F3 import boundaries."""

from __future__ import annotations

import ast
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

ALLOWED_EXPLICIT_F3_IMPORTS = {
    "f3_contracts/operation_adapter.py",
    "tests/f3_synthetic_adapter.py",
    "tests/test_f3_adapter_core.py",
    "tests/test_f3_contract.py",
    "tests/test_f3_dashboard_executor_conformance.py",
    "tests/test_f3_execution_persistence.py",
    "tests/test_f3_fault_injection.py",
    "tests/test_f3_lock_manager.py",
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
            self.assertFalse((image_app / "f3_contracts").exists())

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


class F3ImportBoundaryTests(unittest.TestCase):
    def test_shipped_modules_use_no_repository_only_packages(self):
        forbidden = {"f3_contracts", "f3_dashboard", "tests"}
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

    def test_current_runtime_routes_remain_f3_inert(self):
        sensitive = {
            "application.py",
            "capabilities.py",
            "governance/service.py",
            "governance/task_recovery.py",
            "providers/routing.py",
            "tools/registry.py",
        }
        for relative in sorted(sensitive):
            path = RUNTIME / relative
            if not path.exists():
                continue
            source = path.read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertNotIn("ha_mcp_engineering.f3", source)
                self.assertNotIn("f3_dashboard", source)
                self.assertNotIn("f3_contracts", source)


if __name__ == "__main__":
    unittest.main()
