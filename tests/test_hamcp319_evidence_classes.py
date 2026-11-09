"""HAMCP-319 ruling 1 enforcement: 8.6 recordings are never presented as 8.5 evidence.

Architect ruling 2026-11-07 item 1, closed test-only by ruling 1 (2026-11-08).
Evidence classes: C = captured from the exact version, S = synthetic or
replayed. On 8.5.0 only row 25 (``ha_mcp_8_5_0/read-results.json``), rows 26-28
(``hamcp129/results-8.5.0.json``) and the row-29 catalog (hamcp162
``catalog-8.5.0-A.json``) are C; every other 8.5.0 row is S.

The machine-checkable labels live in
``tests/fixtures/ha_mcp_9_1_0/config/provenance.json``
(``eight_five_evidence_classes``) and in the loading modules' docstrings and
comments. This module binds them:

* every committed recording an ``tests/test_hamcp319_*.py`` module loads by a
  literal path from an 8.5.0 or 8.6.0 capture has exactly one provenance entry,
  and every declared loader really loads it (no stale or missing entries);
* a recording whose recorded version is not 8.5.0 (the 8.6 recordings) is
  classed S for every version it is evidence for, never C;
* a C entry is C only for 8.5.0 and points at an existing capture whose
  recorded version, resolved from its own provenance, is 8.5.0;
* each loader carries the declared label text, and no 8.5-side fixture in any
  ``ha_mcp_9_1_0/*/provenance.json`` is classed C.

Read-only over committed files; no live Home Assistant, no network.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVENANCE_DIR = ROOT / "tests/fixtures/ha_mcp_9_1_0"
CONFIG_PROVENANCE = PROVENANCE_DIR / "config/provenance.json"
POLICY_DIR = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering"
EIGHT_FIVE = "8.5.0"
CLASSES = {"C", "S"}
# A literal path names an 8.5.0 or 8.6.0 recording when it is a JSON file
# outside the Engineering source tree and carries one of those versions.
_RECORDING_VERSION = re.compile(r"8[._]5[._]0|8[._]6[._]0")
# An 8.5-side fixture key in a 9.1.0 provenance file.
_EIGHT_FIVE_SIDE = re.compile(r"(^|/)(v85|ha_mcp_8_5_0|refusals_8_5_0)(/|$)")


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _pointer(document, pointer: str):
    value = document
    for token in pointer.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            value = value[int(token)]
        else:
            value = value[token]
    return value


def _canonical_sha256(value) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _versions_by_reviewed_commit() -> dict[str, str]:
    versions: dict[str, str] = {}
    for policy_path in sorted(POLICY_DIR.glob("upstream_tool_policy_*.json")):
        policy = _json(policy_path)
        commit = policy.get("reviewed_source_commit")
        version = policy.get("reviewed_upstream_version")
        if commit and version:
            versions[commit] = version
    return versions


def recorded_version(entry) -> str:
    """Resolve a recording's version from its own provenance, never its label."""
    spec = entry["recorded_version"]
    value = _pointer(_json(ROOT / spec["source"]), spec["pointer"])
    if spec["kind"] == "version":
        return value
    if spec["kind"] == "commit":
        versions = _versions_by_reviewed_commit()
        if value not in versions:
            raise AssertionError(f"{entry['path']}: commit {value!r} is no reviewed upstream release")
        return versions[value]
    raise AssertionError(f"{entry['path']}: unknown recorded_version kind {spec['kind']!r}")


def class_violations(recordings) -> list[str]:
    """Return every ruling-1 class violation in the declared recordings."""
    problems: list[str] = []
    for entry in recordings:
        path = entry["path"]
        classes = entry.get("classes") or {}
        if not classes:
            problems.append(f"{path}: no evidence class")
        for version, label in classes.items():
            if label not in CLASSES:
                problems.append(f"{path}: {version} class {label!r} is not C or S")
        if not (ROOT / path).is_file():
            problems.append(f"{path}: recording file is missing")
            continue
        version = recorded_version(entry)
        captured = sorted(v for v, label in classes.items() if label == "C")
        if version != EIGHT_FIVE and captured:
            problems.append(f"{path}: a {version} recording is classed C for {captured}; it must be S")
        if version == EIGHT_FIVE and set(captured) - {EIGHT_FIVE}:
            problems.append(f"{path}: an 8.5.0 capture is classed C for {sorted(set(captured) - {EIGHT_FIVE})}")
        document = _json(ROOT / path)
        for pointer in entry.get("pointers", ()):
            try:
                _pointer(document, pointer)
            except (KeyError, IndexError, ValueError, TypeError):
                problems.append(f"{path}: pointer {pointer} does not resolve")
        if entry.get("canonical_sha256") != _canonical_sha256(document):
            problems.append(f"{path}: canonical_sha256 does not match the committed recording")
    return problems


def scanned_loaders() -> dict[tuple[str, str], int]:
    """Map (module, literal) to line for each 8.5/8.6 recording path literal."""
    found: dict[tuple[str, str], int] = {}
    for module in sorted((ROOT / "tests").glob("test_hamcp319_*.py")):
        relative = module.relative_to(ROOT).as_posix()
        if module.resolve() == Path(__file__).resolve():
            continue  # this enforcement module names the recordings itself
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            literal = node.value
            if (
                literal.endswith(".json")
                and "\n" not in literal
                and not literal.startswith("hass_mcp_engineering_beta/")
                and _RECORDING_VERSION.search(literal)
            ):
                found.setdefault((relative, literal), node.lineno)
    return found


def coverage_violations(recordings, scanned) -> list[str]:
    """Every scanned literal maps to one entry and every loader is real."""
    problems: list[str] = []
    declared: set[tuple[str, str]] = set()
    for entry in recordings:
        for module in entry.get("loaded_by", {}):
            declared.add((module, entry["path"]))
    used: set[tuple[str, str]] = set()
    for (module, literal), line in sorted(scanned.items()):
        matches = [e["path"] for e in recordings if e["path"] == literal or e["path"].endswith("/" + literal)]
        if len(matches) != 1:
            problems.append(f"{module}:{line} loads {literal!r} with {len(matches)} provenance entries")
            continue
        if (module, matches[0]) not in declared:
            problems.append(f"{module}:{line} loads {matches[0]} but is not a declared loader")
        used.add((module, matches[0]))
    for module, path in sorted(declared - used):
        problems.append(f"{module} is declared as loading {path} but does not")
    return problems


def label_violations(recordings) -> list[str]:
    problems: list[str] = []
    for entry in recordings:
        for module, labels in entry.get("loaded_by", {}).items():
            source = (ROOT / module).read_text(encoding="utf-8")
            if "C (8.6" in source:
                problems.append(f"{module} labels an 8.6 recording as C")
            for label in labels:
                if label not in source:
                    problems.append(f"{module} lacks the label {label!r} for {entry['path']}")
    return problems


def eight_five_side_violations(documents) -> list[str]:
    """No 8.5-side fixture or 8.6 recording reference in a 9.1.0 provenance is C."""
    problems: list[str] = []

    def walk(name, value, path):
        if isinstance(value, dict):
            label = value.get("class")
            references_86 = any(
                isinstance(item, str) and "ha_mcp_8_6_0/" in item for item in value.values()
            )
            if _EIGHT_FIVE_SIDE.search(path) and label is not None and label != "S":
                problems.append(f"{name}:{path} 8.5-side fixture is classed {label!r}")
            if references_86 and label != "S":
                problems.append(f"{name}:{path} references an 8.6 recording with class {label!r}")
            for key, item in value.items():
                if not path and key == "eight_five_evidence_classes":
                    continue  # checked by class_violations and coverage_violations
                walk(name, item, f"{path}/{key}" if path else key)

    for name, document in documents.items():
        walk(name, document, "")
    return problems


def provenance_documents() -> dict[str, dict]:
    return {
        path.relative_to(ROOT).as_posix(): _json(path)
        for path in sorted(PROVENANCE_DIR.glob("*/provenance.json"))
    }


class EightFiveEvidenceClassTests(unittest.TestCase):
    def setUp(self):
        self.recordings = _json(CONFIG_PROVENANCE)["eight_five_evidence_classes"]["recordings"]

    def test_eight_six_recordings_are_never_eight_five_captures(self):
        self.assertEqual(class_violations(self.recordings), [])
        eight_six = [e for e in self.recordings if recorded_version(e) == "8.6.0"]
        self.assertEqual(
            {e["path"] for e in eight_six},
            {"tests/fixtures/ha_mcp_8_6_0/read-results.json", "tests/fixtures/hamcp129/results-8.6.0.json"},
        )
        for entry in eight_six:
            self.assertEqual(set(entry["classes"].values()), {"S"}, entry["path"])

    def test_captured_eight_five_rows_are_exactly_25_to_28_and_the_29_catalog(self):
        captured = {
            e["path"]: e["rows"][EIGHT_FIVE]
            for e in self.recordings
            if e["classes"].get(EIGHT_FIVE) == "C"
        }
        self.assertEqual(
            captured,
            {
                "tests/fixtures/ha_mcp_8_5_0/read-results.json": "25",
                "tests/fixtures/hamcp129/results-8.5.0.json": "26-28",
                "docs/evidence/hamcp162/captures/catalog-8.5.0-A.json": "29 (catalog)",
            },
        )
        for entry in self.recordings:
            if entry["path"] in captured:
                self.assertEqual(recorded_version(entry), EIGHT_FIVE, entry["path"])
        rows_1_24 = next(e for e in self.recordings if e["path"].endswith("ha_mcp_8_6_0/read-results.json"))
        self.assertEqual((rows_1_24["rows"][EIGHT_FIVE], rows_1_24["classes"][EIGHT_FIVE]), ("1-24", "S"))

    def test_every_loaded_recording_has_one_provenance_entry(self):
        scanned = scanned_loaders()
        self.assertTrue(scanned, "the loader scan found no recordings")
        self.assertEqual(coverage_violations(self.recordings, scanned), [])

    def test_loaders_carry_the_declared_labels(self):
        self.assertEqual(label_violations(self.recordings), [])

    def test_no_eight_five_side_fixture_in_any_91_provenance_is_captured(self):
        documents = provenance_documents()
        self.assertEqual(eight_five_side_violations(documents), [])
        config = documents["tests/fixtures/ha_mcp_9_1_0/config/provenance.json"]
        self.assertEqual(config["eight_five_shaped_responses"]["class"], "S")


class EightFiveEvidenceClassMutantTests(unittest.TestCase):
    """In-memory mutants: each checker must reject the corrupted ruling-1 state."""

    def setUp(self):
        self.recordings = _json(CONFIG_PROVENANCE)["eight_five_evidence_classes"]["recordings"]

    def _copy(self):
        return json.loads(json.dumps(self.recordings))

    def test_eight_six_relabelled_c_is_rejected(self):
        for index, entry in enumerate(self.recordings):
            if recorded_version(entry) != "8.6.0":
                continue
            for version in entry["classes"]:
                with self.subTest(path=entry["path"], version=version):
                    mutant = self._copy()
                    mutant[index]["classes"][version] = "C"
                    self.assertTrue(class_violations(mutant))

    def test_c_label_on_a_non_eight_five_capture_is_rejected(self):
        mutant = self._copy()
        entry = next(e for e in mutant if e["path"].endswith("ha_mcp_8_5_0/read-results.json"))
        entry["classes"]["9.1.0"] = "C"
        self.assertTrue(class_violations(mutant))

    def test_c_entry_pointing_at_an_eight_six_record_is_rejected(self):
        mutant = self._copy()
        entry = next(e for e in mutant if e["path"].endswith("hamcp129/results-8.5.0.json"))
        entry["recorded_version"]["pointer"] = "/upstream/8.6.0/commit"
        self.assertTrue(class_violations(mutant))

    def test_missing_capture_file_is_rejected(self):
        mutant = self._copy()
        mutant[0]["path"] = "tests/fixtures/ha_mcp_8_5_0/absent.json"
        self.assertTrue(class_violations(mutant))

    def test_removed_entry_or_loader_is_rejected(self):
        scanned = scanned_loaders()
        for index in range(len(self.recordings)):
            with self.subTest(removed=self.recordings[index]["path"]):
                mutant = self._copy()
                del mutant[index]
                self.assertTrue(coverage_violations(mutant, scanned))
        mutant = self._copy()
        del mutant[0]["loaded_by"]["tests/test_hamcp319_side_by_side.py"]
        self.assertTrue(coverage_violations(mutant, scanned))

    def test_unloaded_declared_loader_is_rejected(self):
        mutant = self._copy()
        mutant[0]["loaded_by"]["tests/test_hamcp319_history.py"] = []
        self.assertTrue(coverage_violations(mutant, scanned_loaders()))

    def test_new_unregistered_eight_six_literal_is_rejected(self):
        scanned = dict(scanned_loaders())
        scanned[("tests/test_hamcp319_history.py", "tests/fixtures/ha_mcp_8_6_0/read-results.json")] = 1
        self.assertTrue(coverage_violations(self.recordings, scanned))
        scanned = dict(scanned_loaders())
        scanned[("tests/test_hamcp319_history.py", "tests/fixtures/ha_mcp_8_6_0/other.json")] = 1
        self.assertTrue(coverage_violations(self.recordings, scanned))

    def test_missing_label_is_rejected(self):
        mutant = self._copy()
        mutant[0]["loaded_by"]["tests/test_hamcp319_side_by_side.py"] = ["S (8.6 replay), absent label"]
        self.assertTrue(label_violations(mutant))

    def test_eight_five_side_fixture_labelled_c_is_rejected(self):
        documents = json.loads(json.dumps(provenance_documents()))
        history = documents["tests/fixtures/ha_mcp_9_1_0/history/provenance.json"]
        history["fixtures"]["scenarios/history_minimal/ha_mcp_8_5_0"]["class"] = "C"
        self.assertTrue(eight_five_side_violations(documents))
        documents = json.loads(json.dumps(provenance_documents()))
        config = documents["tests/fixtures/ha_mcp_9_1_0/config/provenance.json"]
        config["eight_five_shaped_responses"]["class"] = "C"
        self.assertTrue(eight_five_side_violations(documents))
        documents = json.loads(json.dumps(provenance_documents()))
        entities = documents["tests/fixtures/ha_mcp_9_1_0/entities/provenance.json"]
        entities["fixtures"]["v85/ha_get_state/single"]["class"] = "C"
        self.assertTrue(eight_five_side_violations(documents))

    def test_content_drift_is_rejected(self):
        mutant = self._copy()
        mutant[0]["canonical_sha256"] = "0" * 64
        self.assertTrue(class_violations(mutant))


if __name__ == "__main__":
    unittest.main()
