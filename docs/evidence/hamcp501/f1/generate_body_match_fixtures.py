"""HAMCP-501 W6 F1: body-match goldens from the pinned ha-mcp 9.2.0 component.

Codex r1 F1 asked for the pinned component to be exercised with numeric and
string secrets, full and substring queries, degraded scrub loading and
legitimate body matches, with the results carried through native search. This
generator runs the component's own code on synthetic inputs and records what
it returns. ``tests/test_hamcp501_w6_f1_body_matching.py`` carries each
recorded result through native search and runs no upstream code.

Executed, after a SHA-256 check (component files are under
``custom_components/ha_mcp_tools/websocket_api/``):

- ``search_score.py`` (standard library only), loaded by path:
  ``_config_score``, ``_name_tier`` and ``_tokenize``;
- ``secrets.py``, loaded by path: ``_load_secret_scrub``,
  ``_load_secret_values`` and its two scrub-degraded warning texts, which the
  golden records by SHA-256 only;
- three functions of ``search.py``, taken by AST and nothing else of that
  module: ``_search_prep``, ``_degradation_warnings`` and
  ``_add_optional_keys``.

Read as data by AST and never executed as a module: ``constants.py`` (the
top-level assignments named in ``CONSTANT_NAMES``), ``flow_domains.py``
(``HELPER_FLOWS_DEGRADED_WARNING``), and the server's
``src/ha_mcp/tools/search/response.py`` and ``entities.py`` (the page warning
texts a sub-call page can carry).

Stubbed: ``homeassistant.core.HomeAssistant`` (an empty class, for the
imports), the package parents (empty placeholders, so no other upstream file
can be imported) and ``_flow_helper_domains`` (no flow domains; the prep step
awaits it for a helper search). Logging is disabled, so the degraded loader's
log line is not emitted. A fake ``hass`` serves ``config.path`` from a
temporary directory that holds each variant's synthetic ``secrets.yaml`` and
runs ``async_add_executor_job`` inline. Nothing contacts a network or Home
Assistant, and every input is synthetic and defined below: none of the values
is a credential.

Usage (from the task venv, whose PyYAML serves ``secrets.py``'s
``yaml.safe_load``, outside the upstream tree, in a network namespace with no
network; a source tree of ha-mcp at commit 51e3aac7 holds the files)::

    unshare -rn <venv>/bin/python -I docs/evidence/hamcp501/f1/generate_body_match_fixtures.py \\
        --upstream <ha-mcp-51e3aac7> --output-dir docs/evidence/hamcp501/f1

``--check`` regenerates in memory and fails when a committed file differs.
"""

from __future__ import annotations

import __future__
import argparse
import ast
import asyncio
import hashlib
import importlib.util
import itertools
import json
import logging
from pathlib import Path
import re
import sys
import tempfile
from types import ModuleType, SimpleNamespace

SOURCE_REPOSITORY = "https://github.com/homeassistant-ai/ha-mcp"
SOURCE_VERSION = "9.2.0"
SOURCE_COMMIT = "51e3aac788482a081807591cd5d58d07dc4e540b"
COMPONENT = "custom_components/ha_mcp_tools/websocket_api"
SCORE_PATH = f"{COMPONENT}/search_score.py"
SECRETS_PATH = f"{COMPONENT}/secrets.py"
SEARCH_PATH = f"{COMPONENT}/search.py"
CONSTANTS_PATH = f"{COMPONENT}/constants.py"
FLOW_DOMAINS_PATH = f"{COMPONENT}/flow_domains.py"
RESPONSE_PATH = "src/ha_mcp/tools/search/response.py"
ENTITIES_PATH = "src/ha_mcp/tools/search/entities.py"
SOURCE_SHA256 = {
    SCORE_PATH: "ba034030b149091fba1e2777ac6c0c4d9c442fa33148b7d92441cba3feca43de",
    SECRETS_PATH: "9f4b00dc2dfc2d1d3167e70dc6793b58e5d58065d1203c283813e34b616a7958",
    SEARCH_PATH: "5f1a1e312884494245f71c4720c5ddd3ac3d4211f9c8257e9be9460be36781a8",
    CONSTANTS_PATH: "672c13bb636c77c549cb27a72cf27c304d8ac1a61cc833348e4531ad40cbfa71",
    FLOW_DOMAINS_PATH: "f371892fbbbb7f77dd7938eb8239133d6538e43922c0e7fc1c154fbda319bccd",
    RESPONSE_PATH: "fe776978341246e12a2ddcfd8a7ec3f66f8bbee5712d9697d4b8edc6d43af07c",
    ENTITIES_PATH: "650dcd0ea85c63541f6220696767e7db568d097214627496c09d2129762b7500",
}
CONSTANT_NAMES = (
    "FUZZY_THRESHOLD", "HIDDEN_SCORE_PENALTY", "SEARCH_TYPE_ENTITY", "SEARCH_TYPE_AUTOMATION",
    "SEARCH_TYPE_SCRIPT", "SEARCH_TYPE_SCENE", "SEARCH_TYPE_HELPER", "ALL_SEARCH_TYPES",
    "CONFIG_SEARCH_TYPES", "_SPLIT_RE",
)
SEARCH_FUNCTIONS = ("_degradation_warnings", "_add_optional_keys", "_search_prep")
# The server-side and component texts a sub-call page's ``warnings`` can hold,
# found by these fragments among each file's string constants.
WARNING_FILES = (RESPONSE_PATH, ENTITIES_PATH, FLOW_DOMAINS_PATH)
WARNING_FRAGMENTS = (
    "explicit search_types", "ha_mcp_tools component", "could not list its helper flows",
    "incomplete results",
)
GOLDEN_NAME = "body-match-golden.json"
PROVENANCE_NAME = "provenance.json"
COMMAND = (
    "unshare -rn <venv>/bin/python -I docs/evidence/hamcp501/f1/generate_body_match_fixtures.py"
    " --upstream <ha-mcp-51e3aac7> --output-dir docs/evidence/hamcp501/f1"
)

# Synthetic ``secrets.yaml`` variants. ``clean`` loads; ``degraded`` is present
# but unparseable; ``absent`` has no file; ``not_a_mapping`` parses to a list.
SECRETS_FILES = {
    "clean": (
        "# Synthetic: none of these is a credential.\n"
        "gate_code: 24681357\n"
        "alarm_message: quasarvelvet\n"
        "rate_limit: 3.75\n"
        "feature_flag: true\n"
        "backup_codes:\n"
        "  - nebulaorchid\n"
    ),
    "degraded": "gate_code: [24681357\n",
    "absent": None,
    "not_a_mapping": "- 24681357\n",
}
# Values whose membership in each loaded scrub set the golden records.
PROBED_VALUES = ("24681357", "quasarvelvet", "3.75", "True", "true", "nebulaorchid")
SCORED_VARIANTS = ("clean", "degraded")

# One automation body holding each resolved secret beside ordinary values.
CONFIG = {
    "id": "1700000000501",
    "alias": "Front gate",
    "description": "Unlock the gate once the alarm is disarmed",
    "triggers": [{"trigger": "state", "entity_id": "alarm_control_panel.home", "to": "disarmed"}],
    "conditions": [],
    "actions": [
        # gate_code, an integer secret, resolved into the body.
        {"action": "lock.unlock", "target": {"entity_id": "lock.front_gate"},
         "data": {"code": 24681357}},
        # alarm_message, a string secret.
        {"action": "notify.notify", "data": {"message": "quasarvelvet"}},
        # rate_limit, a float secret.
        {"action": "input_number.set_value", "target": {"entity_id": "input_number.gate_rate"},
         "data": {"value": 3.75}},
        # A member of backup_codes, a list secret: never collected.
        {"action": "notify.notify", "data": {"title": "nebulaorchid"}},
        # Ordinary values.
        {"action": "light.turn_on", "target": {"entity_id": "light.porch"},
         "data": {"brightness_pct": 75}},
        {"delay": "10:00:00"},
    ],
    "mode": "single",
}
# (bucket, entity_id, name) the body is scored under: an automation, a
# collection helper and a flow helper (whose record has no entity id).
SUBJECTS = (
    ("automation", "automation.front_gate", "Front gate"),
    ("helper", "timer.front_gate", "Front gate"),
    ("flow", None, "Front gate flow"),
)
# (kind, query). Each runs in exact and fuzzy mode against each scored variant.
QUERIES = (
    ("numeric_secret_full", "24681357"),
    ("numeric_secret_substring", "2468"),
    ("numeric_secret_substring", "8135"),
    ("numeric_secret_near", "24681358"),
    ("numeric_secret_mixed", "gate 24681358"),
    ("float_secret_full", "3.75"),
    ("string_secret_full", "quasarvelvet"),
    ("string_secret_substring", "quasar"),
    ("string_secret_near", "quasarvelvat"),
    ("list_secret_member", "nebulaorchid"),
    ("legit_body", "unlock"),
    ("legit_body", "disarmed"),
    ("legit_body_near", "dissarmed"),
    ("legit_numeric", "75"),
    ("legit_numeric", "10:00"),
    ("name", "front gate"),
    ("name", "gate"),
    ("name_near", "frnt gate"),
    ("no_match", "xyzzy"),
)
# Extra queries whose upstream tokens the golden records for the trigger.
TOKEN_QUERIES = (
    "", "  ", "e", "t", "E", "inf", "nan", "info", "nano", "1.5e-07", "+07", "--", "10:00",
    "2026-01-01", "2026-01-01T10:00:00", "kitchen 2", "x1", "kitchen", "Kitchen-Light_2",
    "living room", "１２", "a.b_c-d e",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_sources(root: Path) -> None:
    for relative, expected in SOURCE_SHA256.items():
        actual = sha256_file(root / relative)
        if actual != expected:
            raise SystemExit(f"sha256 mismatch: {relative}")


def _tree(root: Path, relative: str) -> ast.Module:
    return ast.parse((root / relative).read_text(encoding="utf-8"), filename=relative)


def _constants(root: Path) -> ModuleType:
    """``constants.py``'s named top-level assignments, run alone in order."""

    nodes = [node for node in _tree(root, CONSTANTS_PATH).body
             if isinstance(node, ast.Assign) and len(node.targets) == 1
             and isinstance(node.targets[0], ast.Name) and node.targets[0].id in CONSTANT_NAMES]
    found = [node.targets[0].id for node in nodes]
    if sorted(found) != sorted(CONSTANT_NAMES):
        raise SystemExit("constants not found exactly once")
    module = ModuleType("ha_mcp_tools.websocket_api.constants")
    module.__dict__["re"] = re
    exec(compile(ast.Module(body=nodes, type_ignores=[]), CONSTANTS_PATH, "exec"),
         module.__dict__)
    del module.__dict__["re"]
    return module


def _string_constant(root: Path, relative: str, name: str) -> str:
    values = [node.value.value for node in _tree(root, relative).body
              if isinstance(node, ast.Assign) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name
              and isinstance(node.value, ast.Constant) and type(node.value.value) is str]
    if len(values) != 1:
        raise SystemExit(f"{name} not found exactly once")
    return values[0]


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_upstream(root: Path) -> SimpleNamespace:
    _check_sources(root)
    for name in ("homeassistant", "ha_mcp_tools", "ha_mcp_tools.websocket_api"):
        placeholder = ModuleType(name)
        placeholder.__path__ = []
        sys.modules[name] = placeholder
    core = ModuleType("homeassistant.core")

    class HomeAssistant:
        pass

    core.HomeAssistant = HomeAssistant
    sys.modules[core.__name__] = core
    constants = _constants(root)
    sys.modules[constants.__name__] = constants
    score = _load(root / SCORE_PATH, "ha_mcp_tools.websocket_api.search_score")
    secrets = _load(root / SECRETS_PATH, "ha_mcp_tools.websocket_api.secrets")
    flows_degraded = _string_constant(root, FLOW_DOMAINS_PATH, "HELPER_FLOWS_DEGRADED_WARNING")

    async def _flow_helper_domains(hass):
        return {}

    namespace = {
        "Any": object, "HomeAssistant": HomeAssistant,
        "ALL_SEARCH_TYPES": constants.ALL_SEARCH_TYPES,
        "CONFIG_SEARCH_TYPES": constants.CONFIG_SEARCH_TYPES,
        "SEARCH_TYPE_HELPER": constants.SEARCH_TYPE_HELPER,
        "SCRUB_DEGRADED_WARNING": secrets.SCRUB_DEGRADED_WARNING,
        "SCRUB_DEGRADED_MATCH_WARNING": secrets.SCRUB_DEGRADED_MATCH_WARNING,
        "HELPER_FLOWS_DEGRADED_WARNING": flows_degraded,
        "_load_secret_scrub": secrets._load_secret_scrub,
        "_load_secret_values": secrets._load_secret_values,
        "_flow_helper_domains": _flow_helper_domains,
    }
    nodes = [node for node in _tree(root, SEARCH_PATH).body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name in SEARCH_FUNCTIONS]
    if sorted(node.name for node in nodes) != sorted(SEARCH_FUNCTIONS):
        raise SystemExit("search.py functions not found exactly once")
    exec(compile(ast.Module(body=nodes, type_ignores=[]), SEARCH_PATH, "exec",
                 flags=__future__.annotations.compiler_flag, dont_inherit=True), namespace)
    return SimpleNamespace(constants=constants, score=score, secrets=secrets,
                           flows_degraded=flows_degraded,
                           **{name: namespace[name] for name in SEARCH_FUNCTIONS})


def _hass(directory: Path) -> SimpleNamespace:
    async def run_inline(function, *args):
        return function(*args)

    return SimpleNamespace(config=SimpleNamespace(path=lambda name: str(directory / name)),
                           async_add_executor_job=run_inline)


def _with_secrets(variant: str, action):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        text = SECRETS_FILES[variant]
        if text is not None:
            (path / "secrets.yaml").write_text(text, encoding="utf-8")
        return action(_hass(path))


def _docstrings(tree: ast.Module) -> set:
    scopes = [tree, *(node for node in ast.walk(tree) if isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))]
    return {id(scope.body[0].value) for scope in scopes
            if scope.body and isinstance(scope.body[0], ast.Expr)
            and isinstance(scope.body[0].value, ast.Constant)}


def _warning_texts(root: Path) -> list:
    texts = []
    for relative in WARNING_FILES:
        tree = _tree(root, relative)
        docstrings = _docstrings(tree)
        found = sorted({node.value for node in ast.walk(tree)
                        if isinstance(node, ast.Constant) and type(node.value) is str
                        and id(node) not in docstrings
                        and any(fragment in node.value for fragment in WARNING_FRAGMENTS)})
        texts.extend({"file": relative, "text": text} for text in found)
    if any("secret" in entry["text"].lower() for entry in texts):
        raise SystemExit("a collected page warning text names the scrub")
    return texts


def build(root: Path) -> dict:
    upstream = load_upstream(root)
    logging.disable(logging.CRITICAL)
    score, secrets = upstream.score, upstream.secrets
    names = {
        secrets.SCRUB_DEGRADED_WARNING: "SCRUB_DEGRADED_WARNING",
        secrets.SCRUB_DEGRADED_MATCH_WARNING: "SCRUB_DEGRADED_MATCH_WARNING",
        upstream.flows_degraded: "HELPER_FLOWS_DEGRADED_WARNING",
    }
    loads, scrub_sets = [], {}
    for variant in SECRETS_FILES:
        values, degraded = _with_secrets(variant, secrets._load_secret_scrub)
        plain = _with_secrets(variant, secrets._load_secret_values)
        scrub_sets[variant] = values
        loads.append({
            "variant": variant, "degraded": degraded, "size": len(values),
            "holds": {value: value in values for value in PROBED_VALUES},
            "values_size": len(plain), "values_equal": plain == values,
        })
    prep = []
    for variant in ("clean", "degraded", "absent"):
        for search_types in (["automation"], ["script"], ["scene"], ["helper"], None):
            message = {} if search_types is None else {"search_types": search_types}
            out = _with_secrets(variant,
                                lambda hass: asyncio.run(upstream._search_prep(hass, message)))
            sets = [value for value in out.values() if type(value) is frozenset]
            flags = [value for value in out.values() if type(value) is bool]
            if len(sets) != 1 or len(flags) > 1:
                raise SystemExit("unexpected prep shape")
            prep.append({"variant": variant, "search_types": search_types,
                         "size": len(sets[0]), "degraded": flags[0] if flags else None})
    warnings = []
    for degraded, include_config, flows in itertools.product((False, True), repeat=3):
        result: dict = {}
        upstream._add_optional_keys(result, {}, [], upstream._degradation_warnings(
            scrub_degraded=degraded, include_config=include_config, flows_degraded=flows))
        listed = result.get("warnings")
        warnings.append({"scrub_degraded": degraded, "include_config": include_config,
                         "flows_degraded": flows,
                         "warnings": None if listed is None else [names[text] for text in listed]})
    scores, tiers = [], []
    for bucket, entity_id, name in SUBJECTS:
        for (kind, query), exact in itertools.product(QUERIES, (True, False)):
            query_lower = query.strip().lower()
            tier = score._name_tier(query_lower, [entity_id, name], exact=exact)
            tiers.append({"bucket": bucket, "entity_id": entity_id, "name": name,
                          "query": query, "exact": exact, "tier": tier})
            for variant in SCORED_VARIANTS:
                found = score._config_score(query_lower, entity_id, name, CONFIG, exact=exact,
                                            secret_values=scrub_sets[variant])
                scores.append({"bucket": bucket, "variant": variant, "kind": kind,
                               "query": query, "exact": exact,
                               "result": None if found is None else list(found)})
    queries = sorted({query for _, query in QUERIES} | set(TOKEN_QUERIES))
    constants = upstream.constants
    return {
        "schema": "hamcp501-f1-body-match-golden-v1",
        "source": {"repository": SOURCE_REPOSITORY, "version": SOURCE_VERSION,
                   "commit": SOURCE_COMMIT, "sha256": SOURCE_SHA256},
        "constants": {"FUZZY_THRESHOLD": constants.FUZZY_THRESHOLD,
                      "HIDDEN_SCORE_PENALTY": constants.HIDDEN_SCORE_PENALTY,
                      "_SPLIT_RE": constants._SPLIT_RE.pattern},
        "degraded_text_sha256": {
            "SCRUB_DEGRADED_WARNING": sha256_text(secrets.SCRUB_DEGRADED_WARNING),
            "SCRUB_DEGRADED_MATCH_WARNING": sha256_text(secrets.SCRUB_DEGRADED_MATCH_WARNING),
        },
        "helper_flows_degraded_warning": upstream.flows_degraded,
        "page_warning_texts": _warning_texts(root),
        "subjects": [list(subject) for subject in SUBJECTS],
        "config": CONFIG,
        "secrets_files": SECRETS_FILES,
        "scrub_loads": loads,
        "prep": prep,
        "degradation_warnings": warnings,
        "name_tiers": tiers,
        "scores": scores,
        "tokens": [{"query": query, "tokens": score._tokenize(query)} for query in queries],
    }


def render(document: dict) -> bytes:
    """One JSON value per list item per line: small, stable diffs."""
    lines = ["{"]
    keys = sorted(document)
    for index, key in enumerate(keys):
        value = document[key]
        comma = "," if index < len(keys) - 1 else ""
        if isinstance(value, list):
            lines.append(f"{json.dumps(key)}: [")
            for item_index, item in enumerate(value):
                item_comma = "," if item_index < len(value) - 1 else ""
                lines.append(json.dumps(item, sort_keys=True, ensure_ascii=True) + item_comma)
            lines.append("]" + comma)
        else:
            lines.append(f"{json.dumps(key)}: "
                         f"{json.dumps(value, sort_keys=True, ensure_ascii=True)}{comma}")
    lines.append("}")
    return ("\n".join(lines) + "\n").encode("ascii")


def provenance(golden: bytes) -> bytes:
    import yaml

    record = {
        "schema": "hamcp501-f1-body-match-provenance-v1",
        "source": {
            "repository": SOURCE_REPOSITORY, "version": SOURCE_VERSION,
            "commit": SOURCE_COMMIT, "files": SOURCE_SHA256,
            "executed": [SCORE_PATH, SECRETS_PATH,
                         *(f"{SEARCH_PATH}::{name}" for name in SEARCH_FUNCTIONS)],
            "read_as_data": [CONSTANTS_PATH, FLOW_DOMAINS_PATH, RESPONSE_PATH, ENTITIES_PATH],
            "stubbed": {
                "homeassistant.core": "HomeAssistant, an empty class",
                "_flow_helper_domains": "returns no flow domains",
                "hass": "config.path from a temporary directory; executor jobs run inline",
            },
        },
        "generator": {
            "path": "docs/evidence/hamcp501/f1/generate_body_match_fixtures.py",
            "sha256": sha256_file(Path(__file__)),
        },
        "golden": {"path": f"docs/evidence/hamcp501/f1/{GOLDEN_NAME}",
                   "sha256": hashlib.sha256(golden).hexdigest()},
        "python": "{}.{}.{}".format(*sys.version_info[:3]),
        "pyyaml": yaml.__version__,
        "isolation": "python -I in the task venv (PyYAML for secrets.py), under unshare -rn "
                     "(no network)",
        "command": COMMAND,
        "inputs": "synthetic, defined in the generator; no value is a credential",
    }
    return (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    golden = render(build(args.upstream))
    outputs = {GOLDEN_NAME: golden, PROVENANCE_NAME: provenance(golden)}
    if args.check:
        stale = [name for name, data in outputs.items()
                 if not (args.output_dir / name).exists()
                 or (args.output_dir / name).read_bytes() != data]
        if stale:
            print("stale: " + ", ".join(stale), file=sys.stderr)
            return 1
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, data in outputs.items():
        (args.output_dir / name).write_bytes(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
