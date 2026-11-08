"""HAMCP-319 section 6.4: the optional YAML read on ha-mcp 9.1.0.

Design ``docs/HAMCP_319_PHASE2_DESIGN.md`` section 6.4, ledger row 29
(``ha_config_get_yaml``, FW optional) and the section 8.3 "YAML" tests:

- 9.1.0 requests ``include_content=true`` for a bounded, constructor-free tag
  scan only; the raw text is deleted from both channels before the agreement
  checks and never reaches a response, error, audit or log record;
- the 2.2.1 preserved tags are not loss; ``!input`` and any other tag are
  disclosed through the closed ``lossy_tags`` allowlist; missing, malformed,
  oversize or untokenizable text is ``unavailable``, never lossless;
- the 8.4.3 and 8.5.0 contract (``include_content=false``, match keys
  ``{file, yaml_path, parsed}``, no tag fields) is unchanged;
- all four 9.1 file siblings stay ``prohibited_withheld`` and are never
  routed; with filesystem tools off every optional name is
  ``optional_absent``.

Fixtures: ``tests/fixtures/ha_mcp_9_1_0/yaml`` (class C receipts re-verified
here; class S shapes cite the pinned 9.1.0 source). Synthetic peers and
canaries only; no live Home Assistant and no real credential.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import logging
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from ha_mcp_engineering.providers import upstream_yaml_reader as y  # noqa: E402
from ha_mcp_engineering.providers.upstream_yaml_reader import YamlReadRefusal  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    load_reviewed_upstream_release_registry,
)
from test_hamcp129_platform_reads import call, ok, refused  # noqa: E402
import test_hamcp162_yaml_admission as adm  # noqa: E402
# Module import, not ``from ... import AuditTests``: a TestCase bound at module
# level is discovered again under its own id (duplicate_discovered_test_id).
import test_hamcp162_yaml_reader as yaml_reader_tests  # noqa: E402

PHASE1 = ROOT / "docs/evidence/hamcp319/phase1"
COMPATIBILITY = ROOT / "docs/evidence/upstream-read-compatibility"
FIXTURES = ROOT / "tests/fixtures/ha_mcp_9_1_0/yaml"
RESPONSES = json.loads((FIXTURES / "responses.json").read_text(encoding="utf-8"))
PROVENANCE = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))
TOOL = y.TOOL_NAME
SIBLINGS_91 = ("ha_delete_file", "ha_list_files", "ha_read_file", "ha_write_file")
OPTIONAL_91 = (TOOL,) + SIBLINGS_91
ENVELOPE_8X = ("file", "yaml_path", "source", "found", "value", "completeness",
               "limitations", "redaction")
ENVELOPE_91 = ("file", "yaml_path", "source", "found", "value", "completeness",
               "tag_completeness", "tag_lossy", "lossy_tags", "limitations", "redaction")
CANARIES = (
    "HAMCP319_YAML_TAG_CANARY",
    "HAMCP319_YAML_COMMENT_CANARY",
    "HAMCP319_YAML_URI_CANARY",
    "HAMCP319_YAML_COMPONENT_MESSAGE_CANARY",
    "HAMCP319_YAML_COMPONENT_SUGGESTION_CANARY",
    "HAMCP319_YAML_MISMATCH_CANARY",
)


def full_catalog():
    return json.loads((PHASE1 / "catalogs/full-1.json").read_text(encoding="utf-8"))["tools"]


def wire_catalog():
    """full-1 in the reviewed wire order (provenance ``gateway_catalog_order``).

    full-1 lists tools by name; the 77 stock descriptors are ordered by the
    reviewed ``runtime_tool_order`` and the five optional ones appended.
    """

    by_name = {tool["name"]: tool for tool in full_catalog()}
    review = json.loads((COMPATIBILITY / "ha-mcp-9.1.0-contract-review.json").read_text(encoding="utf-8"))
    order = review["runtime_catalog"]["runtime_tool_order"]
    return [by_name[name] for name in order] + [by_name[name] for name in OPTIONAL_91]


def filesystem_off_catalog():
    """Class S: the wire catalog without the five filesystem-gated optional tools."""

    return [tool for tool in wire_catalog() if tool["name"] not in OPTIONAL_91]


def class_c(name):
    return deepcopy(RESPONSES["class_c"][name])


def component_missing():
    return deepcopy(RESPONSES["class_s"]["component_missing"])


def entry(version):
    return y.synthesized_policy_entry(y.OPTIONAL_TOOL_BINDINGS[(version, TOOL)])


def result91(content, parsed, path="lamp", **match_extra):
    """The 9.1 ``_evaluate_read`` match shape (class S, tools_yaml_read.py:190-243)."""

    match = {"file": y.ALLOWED_FILE, "yaml_path": path, "parsed": parsed}
    if content is not _ABSENT:
        match["content"] = content
    match.update(match_extra)
    return ok({"success": True, "yaml_path": path, "matches": [match], "count": 1,
               "files_searched": 1, "files_unreadable": 0})


_ABSENT = object()


def result85(parsed, path="lamp"):
    return ok({"success": True, "yaml_path": path,
               "matches": [{"file": y.ALLOWED_FILE, "yaml_path": path, "parsed": parsed}],
               "count": 1, "files_searched": 1, "files_unreadable": 0})


def project(call_result, path="lamp", version="9.1.0"):
    return y.project_success(call_result, path, entry=entry(version))


def assert_clean(test, text, *needles):
    """No canary and no raw YAML text in one serialized sink."""

    for needle in CANARIES + tuple(needles):
        test.assertNotIn(needle, text)
        test.assertNotIn(json.dumps(needle)[1:-1], text)


def raw_yaml_needles(content):
    if type(content) is not str or not content.strip():
        return ()
    return (content, content.strip())


# ---------------------------------------------------------------------------
# Fixture provenance (tests/AGENTS.md, AGENTS.md persisted fixtures).


class PolicySiblingRefusalTests(unittest.TestCase):
    """A policy document can never review an optional YAML name, including
    the 9.1-only prohibited siblings ha_write_file and ha_delete_file."""

    def test_policy_reviewing_a_91_sibling_is_refused(self):
        import tempfile
        from ha_mcp_engineering.upstream_tool_policy import (
            UpstreamToolPolicyError, load_upstream_tool_policy)

        path = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering/upstream_tool_policy_9_1_0.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        pins = dict(expected_version="9.1.0", expected_source_tag="v9.1.0",
                    expected_source_commit=document["reviewed_source_commit"])
        with tempfile.TemporaryDirectory() as tmp:
            unchanged = Path(tmp) / "unchanged.json"
            unchanged.write_text(json.dumps(document), encoding="utf-8")
            load_upstream_tool_policy(unchanged, **pins)
            for name in ("ha_write_file", "ha_delete_file", y.TOOL_NAME):
                with self.subTest(name=name):
                    self.assertIn(name, y.ALL_OPTIONAL_NAMES)
                    changed = deepcopy(document)
                    entry = deepcopy(changed["tools"][0])
                    entry.update(upstream_name=name, exposed_name=name, argument_restrictions=[])
                    changed["tools"].append(entry)
                    target = Path(tmp) / f"{name}.json"
                    target.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaisesRegex(
                            UpstreamToolPolicyError, "^policy_uncompiled_yaml_adapter$"):
                        load_upstream_tool_policy(target, **pins)


class ProvenanceTests(unittest.TestCase):
    def test_class_c_fixtures_equal_their_receipt_pointers(self):
        sums = json.loads((PHASE1 / "SHA256SUMS.json").read_text(encoding="utf-8"))
        class_c_rows = {k: v for k, v in PROVENANCE["fixtures"].items() if v["class"] == "C"}
        self.assertEqual(len(class_c_rows), 9)
        for fixture_pointer, row in class_c_rows.items():
            with self.subTest(fixture=fixture_pointer):
                path = ROOT / row["receipt"]
                relative = row["receipt"].removeprefix("docs/evidence/hamcp319/phase1/")
                digest = sha256(path.read_bytes()).hexdigest()
                self.assertEqual(digest, row["sha256"])
                self.assertEqual(sums[relative], digest)
                document = json.loads(path.read_text(encoding="utf-8"))
                for part in row["pointer"].lstrip("/").split("/"):
                    document = document[int(part)] if isinstance(document, list) else document[part]
                fixture = RESPONSES
                for part in fixture_pointer.lstrip("/").split("/"):
                    fixture = fixture[part]
                self.assertEqual(fixture, document)

    def test_every_fixture_has_a_provenance_row(self):
        named = {f"/{group}/{name}" for group, items in RESPONSES.items() for name in items}
        self.assertEqual(named, set(PROVENANCE["fixtures"]))
        for row in PROVENANCE["fixtures"].values():
            self.assertIn(row["class"], {"C", "S"})
            if row["class"] == "S":
                self.assertTrue(row["source"])
                self.assertTrue(all(":" in item for item in row["source"]))
        self.assertEqual(PROVENANCE["upstream"]["source_commit"],
                         y.OPTIONAL_TOOL_BINDINGS[("9.1.0", TOOL)].source_commit)

    def test_captured_descriptors_match_the_compiled_bindings(self):
        tools = full_catalog()
        self.assertEqual(len(tools), 82)
        for name in OPTIONAL_91:
            with self.subTest(tool=name):
                self.assertEqual(adm._named(tools, name), RESPONSES["descriptors"][name])
                binding = y.OPTIONAL_TOOL_BINDINGS[("9.1.0", name)]
                self.assertEqual(y.match_descriptor(tools, binding).status, "matched")
                self.assertEqual(binding.capture_resource, "docs/evidence/hamcp319/phase1/catalogs/full-1.json")
                self.assertEqual(binding.capture_sha256,
                                 sha256((PHASE1 / "catalogs/full-1.json").read_bytes()).hexdigest())
        self.assertEqual(y.sibling_tools("9.1.0"), SIBLINGS_91)
        self.assertEqual(y.sibling_tools("8.5.0"), ("ha_list_files", "ha_read_file"))
        self.assertIsNone(y.OPTIONAL_TOOL_BINDINGS[("9.1.0", "ha_read_file")].adapter_token)

    def test_gateway_catalog_uses_the_reviewed_wire_order(self):
        from ha_mcp_engineering.upstream_tool_policy import (
            EXACT_RUNTIME_TOOL_ORDER_FINGERPRINTS,
            schema_fingerprint,
        )

        capture = json.loads((COMPATIBILITY / "ha-mcp-9.1.0.json").read_text(encoding="utf-8"))["tools"]
        wire = wire_catalog()
        self.assertEqual(len(wire), 82)
        self.assertEqual(sorted(tool["name"] for tool in wire), [tool["name"] for tool in full_catalog()])
        stock = wire[:77]
        self.assertEqual({tool["name"]: tool for tool in stock}, {tool["name"]: tool for tool in capture})
        self.assertEqual(schema_fingerprint([tool["name"] for tool in stock]),
                         EXACT_RUNTIME_TOOL_ORDER_FINGERPRINTS["9.1.0"])
        self.assertIn("gateway_catalog_order", json.dumps(PROVENANCE))


# ---------------------------------------------------------------------------
# Scanner rules 1-5.


class ScannerTests(unittest.TestCase):
    def test_each_preserved_tag_is_not_loss(self):
        self.assertEqual(y.PRESERVED_TAGS, frozenset({
            "!include", "!include_dir_list", "!include_dir_merge_list", "!include_dir_named",
            "!include_dir_merge_named", "!secret", "!env_var"}))
        for tag in sorted(y.PRESERVED_TAGS):
            with self.subTest(tag=tag):
                scan = y.scan_tags(f"lamp: {tag} lamp_ref\nother:\n  - {tag} more_ref\n")
                self.assertEqual(scan, y.TagScan(y.TAG_LOSSLESS))
                self.assertIs(scan.lossy, False)

    def test_an_actual_input_tag_is_disclosed(self):
        for content in ("initial: !input lamp_input\n", "- !input a\n- b\n", "x: !input\n"):
            with self.subTest(content=content):
                scan = y.scan_tags(content)
                self.assertEqual(scan.completeness, y.TAG_LOSSY)
                self.assertEqual(scan.lossy_tags, ("!input",))
                self.assertIs(scan.lossy, True)

    def test_input_inside_a_quoted_string_or_comment_is_not_a_tag(self):
        for content in ('initial: "!input lamp"  # !input other\n', "initial: '!input lamp'\n",
                        "initial: |\n  !input lamp\n", "# !input only a comment\ninitial: x\n",
                        "initial: plain !input text\n"):
            with self.subTest(content=content):
                self.assertEqual(y.scan_tags(content), y.TagScan(y.TAG_LOSSLESS))

    def test_tag_uri_and_arbitrary_tag_canaries_are_generic(self):
        cases = (
            "initial: !<tag:HAMCP319_YAML_URI_CANARY,2026:str> v\n",
            "initial: !HAMCP319_YAML_TAG_CANARY v\n",
            "initial: !!str v\n",
            "initial: !secre%74 lamp_ref\n",
            "%TAG !e! tag:HAMCP319_YAML_URI_CANARY,2026:\n---\ninitial: !secret lamp_ref\n",
            "initial: !Secret lamp_ref\n",
            "initial: !include_dir_named_HAMCP319_YAML_TAG_CANARY d\n",
        )
        for content in cases:
            with self.subTest(content=content):
                scan = y.scan_tags(content)
                self.assertEqual(scan, y.TagScan(y.TAG_LOSSY, ("unknown_tag",)))
                assert_clean(self, repr(scan))
        mixed = y.scan_tags("a: !input x\nb: !HAMCP319_YAML_TAG_CANARY y\nc: !secret z\n")
        self.assertEqual(mixed.lossy_tags, ("!input", "unknown_tag"))
        self.assertTrue(set(mixed.lossy_tags) <= y.LOSSY_TAG_NAMES)

    def test_absent_malformed_oversize_content_is_unavailable(self):
        within = "a: " + "x" * (y.MAX_SCAN_BYTES - 4) + "\n"
        self.assertEqual(len(within.encode()), y.MAX_SCAN_BYTES)
        self.assertEqual(y.scan_tags(within).completeness, y.TAG_LOSSLESS)
        cases = {
            "none": None, "int": 7, "list": ["a: !input x"], "dict": {"a": 1}, "bytes": b"a: b",
            "oversize": within + "b",
            "oversize_multibyte": "a: " + "\u00e9" * (y.MAX_SCAN_BYTES // 2),
            "unencodable": "a: \ud800\n",
            "unclosed_flow": "a: [b, c\n",
            "unterminated_quote": 'a: "b\n',
            "bad_indent": "a:\n  b: c\n d: e\n",
            "tab_indent": "a:\n\tb: c\n",
            "bad_tag": "a: !<> b\n",
            "bad_directive": "%YAML 9.9 9\n--- a\n",
            "undeclared_handle": "initial: !e!HAMCP319_YAML_TAG_CANARY v\n",
        }
        for label, content in cases.items():
            with self.subTest(case=label):
                scan = y.scan_tags(content)
                self.assertEqual(scan, y.TagScan(y.TAG_UNAVAILABLE), label)
                self.assertIsNone(scan.lossy)

    def test_scanner_never_constructs(self):
        # A tokenizer-only scan: a Python object tag is merely an unknown tag.
        scan = y.scan_tags("a: !!python/object/apply:os.system ['HAMCP319_YAML_TAG_CANARY']\n")
        self.assertEqual(scan, y.TagScan(y.TAG_LOSSY, ("unknown_tag",)))


# ---------------------------------------------------------------------------
# Projection: rule 6, contracts and envelopes (reader level).


class ProjectionTests(unittest.TestCase):
    def assert_stripped(self, call_result, content):
        text = json.dumps(call_result)
        self.assertNotIn('"content": "', text.replace('"content": [', ""))
        for match in json.loads(call_result["content"][0]["text"])["matches"]:
            self.assertNotIn("content", match)
        for match in call_result.get("structuredContent", {}).get("matches", []):
            self.assertNotIn("content", match)
        assert_clean(self, text, *raw_yaml_needles(content))

    def test_receipt_secret_projection_is_lossless(self):
        response = class_c("yaml_fixed_projection")
        result = project(response, "input_text")
        envelope = result.envelope
        self.assertEqual(tuple(envelope), ENVELOPE_91)
        self.assertEqual((envelope["tag_completeness"], envelope["tag_lossy"], envelope["lossy_tags"]),
                         (y.TAG_LOSSLESS, False, []))
        self.assertTrue(envelope["found"])
        self.assertNotIn(y.TAG_LOSSY_LIMITATION, envelope["limitations"])
        self.assertNotIn("hamcp319_sentinel", json.dumps(envelope))
        self.assert_stripped(response, "hamcp319_secret:\n  initial: !secret hamcp319_sentinel\n")

    def test_receipt_input_tag_is_disclosed_lossy(self):
        response = class_c("yaml_input_tag_source_only")
        envelope = project(response, "input_text").envelope
        self.assertEqual((envelope["tag_completeness"], envelope["tag_lossy"], envelope["lossy_tags"]),
                         (y.TAG_LOSSY, True, ["!input"]))
        self.assertEqual(envelope["completeness"], "partial")
        self.assertEqual(envelope["limitations"][0], y.TAG_LOSSY_LIMITATION)
        self.assert_stripped(response, "hamcp319_secret:\n  initial: !input hamcp319_input\n")

    def test_receipt_missing_key_is_not_applicable(self):
        envelope = project(class_c("yaml_missing_key"), "hamcp319_missing_key").envelope
        self.assertFalse(envelope["found"])
        self.assertIsNone(envelope["value"])
        self.assertEqual((envelope["tag_completeness"], envelope["tag_lossy"], envelope["lossy_tags"]),
                         (y.TAG_NOT_APPLICABLE, None, []))
        self.assertEqual(envelope["completeness"], "complete")

    def test_unresolved_secret_and_env_var_stay_unresolved(self):
        for tag, ref in (("!secret", "lamp_ref"), ("!env_var", "LAMP_REF")):
            with self.subTest(tag=tag):
                envelope = project(result91(f"initial: {tag} {ref}\n", {"initial": f"{tag} {ref}"})).envelope
                self.assertEqual(envelope["tag_completeness"], y.TAG_LOSSLESS)
                self.assertTrue(envelope["found"])
                self.assertIn(ref, json.dumps(envelope["value"]))
                self.assertNotIn(y.TAG_UNAVAILABLE_LIMITATION, envelope["limitations"])

    def test_each_include_tag_is_preserved_and_not_followed(self):
        for tag in ("!include", "!include_dir_list", "!include_dir_merge_list",
                    "!include_dir_named", "!include_dir_merge_named"):
            with self.subTest(tag=tag):
                envelope = project(result91(f"lights: {tag} lights\n", {"lights": f"{tag} lights"})).envelope
                self.assertEqual(envelope["tag_completeness"], y.TAG_LOSSLESS)
                self.assertTrue(any(item.startswith("include_not_followed:")
                                    for item in envelope["limitations"]), envelope)

    def test_lossy_and_unavailable_disclosures(self):
        cases = (
            ("initial: !input lamp_input\n", y.TAG_LOSSY, True, ["!input"], y.TAG_LOSSY_LIMITATION),
            ("initial: !HAMCP319_YAML_TAG_CANARY v  # HAMCP319_YAML_COMMENT_CANARY\n",
             y.TAG_LOSSY, True, ["unknown_tag"], y.TAG_LOSSY_LIMITATION),
            ('initial: "!input v"  # HAMCP319_YAML_COMMENT_CANARY\n', y.TAG_LOSSLESS, False, [], None),
            ("initial: [HAMCP319_YAML_TAG_CANARY\n", y.TAG_UNAVAILABLE, None, [], y.TAG_UNAVAILABLE_LIMITATION),
            ("x" * (y.MAX_SCAN_BYTES + 1), y.TAG_UNAVAILABLE, None, [], y.TAG_UNAVAILABLE_LIMITATION),
            (None, y.TAG_UNAVAILABLE, None, [], y.TAG_UNAVAILABLE_LIMITATION),
            (17, y.TAG_UNAVAILABLE, None, [], y.TAG_UNAVAILABLE_LIMITATION),
            (_ABSENT, y.TAG_UNAVAILABLE, None, [], y.TAG_UNAVAILABLE_LIMITATION),
        )
        for content, completeness, lossy, tags, limitation in cases:
            with self.subTest(content=str(content)[:40]):
                response = result91(content, {"initial": "v"})
                envelope = project(response).envelope
                self.assertEqual((envelope["tag_completeness"], envelope["tag_lossy"],
                                  envelope["lossy_tags"]), (completeness, lossy, tags))
                if limitation is None:
                    self.assertEqual(envelope["completeness"], "complete")
                else:
                    self.assertEqual(envelope["completeness"], "partial")
                    self.assertIn(limitation, envelope["limitations"])
                assert_clean(self, json.dumps(envelope), *raw_yaml_needles(content))
                self.assert_stripped(response, content)

    def test_duplicate_json_keys_are_refused_and_scrubbed(self):
        texts = (
            '{"success": true, "yaml_path": "lamp", "matches": [{"file": "configuration.yaml", '
            '"yaml_path": "lamp", "content": "initial: v\\n", "content": "initial: !HAMCP319_YAML_TAG_CANARY v\\n", '
            '"parsed": {"initial": "v"}}], "count": 1, "files_searched": 1, "files_unreadable": 0}',
            '{"success": true, "yaml_path": "lamp", "yaml_path": "lamp", "matches": [{"file": '
            '"configuration.yaml", "yaml_path": "lamp", "content": "HAMCP319_YAML_COMMENT_CANARY: v\\n", '
            '"parsed": {"initial": "v"}}], "count": 1, "files_searched": 1, "files_unreadable": 0}',
        )
        for text in texts:
            with self.subTest(text=text[:60]):
                response = {"isError": False, "content": [{"type": "text", "text": text}]}
                with self.assertRaises(YamlReadRefusal) as caught:
                    project(response)
                self.assertEqual(caught.exception.code, y.IDENTITY_INVALID)
                self.assertEqual(response["content"], [])
                self.assertNotIn("structuredContent", response)
                assert_clean(self, repr(caught.exception) + json.dumps(caught.exception.details))

    def test_text_structured_mismatch_is_refused_and_scrubbed(self):
        def mismatched(change):
            response = result91("initial: !secret lamp_ref  # HAMCP319_YAML_MISMATCH_CANARY\n",
                                {"initial": "!secret lamp_ref"})
            change(response["structuredContent"]["matches"][0], response["structuredContent"])
            return response

        cases = {
            "content_differs": lambda m, s: m.update(content="initial: !HAMCP319_YAML_TAG_CANARY v\n"),
            "content_only_in_text": lambda m, s: m.pop("content"),
            "content_type_differs": lambda m, s: m.update(content=["initial: v"]),
            "extra_structured_match": lambda m, s: s["matches"].append(deepcopy(m)),
            "structured_matches_not_list": lambda m, s: s.update(matches={"a": m}),
            "unknown_match_key": lambda m, s: m.update(raw="HAMCP319_YAML_MISMATCH_CANARY"),
        }
        for label, change in cases.items():
            with self.subTest(case=label):
                response = mismatched(change)
                with self.assertRaises(YamlReadRefusal) as caught:
                    project(response)
                self.assertEqual(caught.exception.code, y.IDENTITY_INVALID)
                self.assertEqual(response["content"], [])
                self.assertNotIn("structuredContent", response)
        with self.subTest(case="content_only_in_structured"):
            response = result91(_ABSENT, {"initial": "v"})
            response["structuredContent"]["matches"][0]["content"] = "HAMCP319_YAML_MISMATCH_CANARY: v\n"
            with self.assertRaises(YamlReadRefusal):
                project(response)
            self.assertEqual(response["content"], [])
        with self.subTest(case="parsed_differs_after_strip"):
            response = result91("initial: v\n", {"initial": "v"})
            response["structuredContent"]["matches"][0]["parsed"] = {"initial": "HAMCP319_YAML_MISMATCH_CANARY"}
            with self.assertRaises(YamlReadRefusal) as caught:
                project(response)
            assert_clean(self, repr(caught.exception))

    def test_85_contract_is_unchanged(self):
        for version in ("8.4.3", "8.5.0"):
            with self.subTest(version=version):
                contract = y.contract_for(entry(version))
                self.assertEqual(contract.contract_id, "hamcp162-yaml-read-8x-v1")
                self.assertEqual(contract.match_keys, frozenset({"file", "yaml_path", "parsed"}))
                self.assertEqual(y.upstream_arguments("recorder", entry=entry(version)), {
                    "yaml_path": "recorder", "file": "configuration.yaml",
                    "include_content": False, "include_parsed": True})
                legacy = y.project_success(result85({"purge_keep_days": 10}), "lamp")
                bound = project(result85({"purge_keep_days": 10}), version=version)
                self.assertEqual(tuple(bound.envelope), ENVELOPE_8X)
                self.assertEqual(json.dumps(bound.envelope), json.dumps(legacy.envelope))
                # The 8.x ingress boundary still refuses a content member.
                with self.assertRaises(YamlReadRefusal) as caught:
                    project(result91("initial: !HAMCP319_YAML_TAG_CANARY v\n", {"initial": "v"}),
                            version=version)
                self.assertEqual(caught.exception.code, y.IDENTITY_INVALID)
                assert_clean(self, repr(caught.exception))
        self.assertEqual(y.upstream_arguments("recorder"), y.upstream_arguments("recorder", entry=entry("8.5.0")))
        self.assertEqual(y.upstream_arguments("recorder", entry=entry("9.1.0"))["include_content"], True)

    def test_unknown_adapter_token_fails_closed(self):
        for restrictions in (("ha-mcp-9.9.9-yaml-fragment-read-v1",), (), (
                "ha-mcp-9.1.0-yaml-fragment-read-v1", "ha-mcp-8.5.0-yaml-fragment-read-v1")):
            with self.subTest(restrictions=restrictions):
                forged = replace(entry("9.1.0"), argument_restrictions=restrictions)
                self.assertFalse(y.is_yaml_adapter(forged))
                for action in (lambda: y.upstream_arguments("lamp", entry=forged),
                               lambda: y.project_success(result91("a: b\n", {"a": "b"}), "lamp", entry=forged)):
                    with self.assertRaises(YamlReadRefusal) as caught:
                        action()
                    self.assertEqual(caught.exception.code, y.READ_FAILED)
        for sibling in SIBLINGS_91:
            with self.subTest(sibling=sibling), self.assertRaises(y.OptionalBindingError):
                y.synthesized_policy_entry(y.OPTIONAL_TOOL_BINDINGS[("9.1.0", sibling)])

    def test_admitted_version_without_a_contract_is_refused(self):
        registry = load_reviewed_upstream_release_registry()
        y.validate_bindings_against_registry(registry)
        release = registry.by_version["9.1.0"]
        self.assertIsNone(y.withheld_reason(release, signed_selection=False))
        contracts = {k: v for k, v in y.YAML_READ_CONTRACTS.items() if k != "9.1.0"}
        with patch.dict(y.YAML_READ_CONTRACTS, contracts, clear=True):
            with self.assertRaisesRegex(y.OptionalBindingError, "optional_admitted_contract_absent"):
                y.validate_bindings_against_registry(registry)
            self.assertEqual(y.withheld_reason(release, signed_selection=False), "optional_contract_absent")
            state = dict(y.evaluate_optional(full_catalog(), release).tools)
            self.assertEqual(state[TOOL], "withheld:optional_contract_absent")
            with self.assertRaises(YamlReadRefusal):
                y.upstream_arguments("lamp", entry=entry("9.1.0"))

    def test_capability_row_names_every_binding(self):
        from ha_mcp_engineering.capabilities import CAPABILITY_PROVIDER_MATRIX

        rows = [row for row in CAPABILITY_PROVIDER_MATRIX if row.get("tool") == TOOL]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["writes"], row["fallback_policy"]), ("none", "none"))
        for version in ("8.4.3", "8.5.0", "9.1.0"):
            self.assertIn(f"ha-mcp-{version}-yaml-fragment-read-v1", row["trust_profile"])
            self.assertIn(version, row["security_justification"])


# ---------------------------------------------------------------------------
# Gateway level: modes A and B, positives, row 29 parity and negatives.


class _Transport(adm.RaceTransport):
    """``RaceTransport`` with a per-test YAML response."""

    def __init__(self, tools, version, **kwargs):
        super().__init__(tools, version, **kwargs)
        self.respond = None

    async def execute_read(self, tool_name, arguments, **kwargs):
        exchange = await super().execute_read(tool_name, arguments, **kwargs)
        if tool_name == TOOL and self.respond is not None:
            exchange = replace(exchange, call_result=deepcopy(self.respond(arguments)))
        return exchange


@contextmanager
def captured_logs():
    records: list[str] = []

    class Handler(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage() + repr(record.__dict__))

    root = logging.getLogger()
    handler = Handler(level=logging.DEBUG)
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        root.removeHandler(handler)
        root.setLevel(level)


class GatewayTests(adm.Harness):
    async def gateway(self, version="9.1.0", tools=None, **kwargs):
        if tools is None and version == "9.1.0":
            tools = wire_catalog()
        with patch.object(adm, "RaceTransport", _Transport):
            return await super().gateway(version, tools, **kwargs)

    def assert_success(self, result):
        self.assertTrue(result.get("success"), result)
        self.assertFalse(result["metadata"]["fallback_occurred"])
        return result["data"]

    async def test_91_yaml_positive(self):
        for mode in ("A", "B"):
            with self.subTest(mode=mode):
                gateway, transport = await self.gateway("9.1.0", mode=mode)
                self.assertIn(TOOL, self.exposed(gateway))
                self.assertEqual(self.optional(gateway)[TOOL], "admitted")
                transport.respond = lambda args: class_c("yaml_fixed_projection")
                result = await call(gateway, TOOL, {"yaml_path": "input_text"})
                data = self.assert_success(result)
                self.assertEqual(transport.sent, [(TOOL, {
                    "yaml_path": "input_text", "file": "configuration.yaml",
                    "include_content": True, "include_parsed": True})])
                self.assertEqual(tuple(data), ENVELOPE_91)
                self.assertEqual((data["tag_completeness"], data["tag_lossy"], data["lossy_tags"]),
                                 (y.TAG_LOSSLESS, False, []))
                self.assertEqual(result["warnings"], [y.K7_WARNING])
                text = json.dumps(result)
                self.assertNotIn("hamcp319_sentinel", text)
                assert_clean(self, text, "hamcp319_secret:\n  initial: !secret")

    async def test_85_yaml_positive(self):
        for mode in ("A", "B"):
            with self.subTest(mode=mode):
                gateway, transport = await self.gateway("8.5.0", mode=mode)
                self.assertEqual(self.optional(gateway)[TOOL], "admitted")
                result = await call(gateway, TOOL, {"yaml_path": "recorder"})
                data = self.assert_success(result)
                self.assertEqual(transport.sent, [(TOOL, {
                    "yaml_path": "recorder", "file": "configuration.yaml",
                    "include_content": False, "include_parsed": True})])
                self.assertEqual(tuple(data), ENVELOPE_8X)
                self.assertEqual(data["value"], {"purge_keep_days": 10})
                self.assertEqual(data["completeness"], "complete")

    # Row 29: 8.5-shaped public calls ({"yaml_path"[, "file"]}) on both releases.

    async def _row_29_positive(self, version):
        gateway, transport = await self.gateway(version)
        transport.respond = lambda args: (
            result91("initial: !secret lamp_ref\n", {"initial": "!secret lamp_ref"}, args["yaml_path"])
            if version == "9.1.0" else result85({"initial": "!secret lamp_ref"}, args["yaml_path"]))
        for arguments in ({"yaml_path": "lamp"}, {"yaml_path": "lamp", "file": "configuration.yaml"}):
            transport.sent.clear()
            data = self.assert_success(await call(gateway, TOOL, arguments))
            self.assertTrue(data["found"])
            self.assertIn("lamp_ref", json.dumps(data["value"]))
            self.assertEqual(len(transport.sent), 1)
            self.assertEqual(transport.sent[0][1]["include_content"], version == "9.1.0")
        return data

    async def test_91_parity_row_29_positive(self):
        data = await self._row_29_positive("9.1.0")
        self.assertEqual(data["tag_completeness"], y.TAG_LOSSLESS)

    async def test_85_parity_row_29_positive(self):
        data = await self._row_29_positive("8.5.0")
        self.assertNotIn("tag_completeness", data)

    async def _row_29_refusal(self, version):
        gateway, transport = await self.gateway(version)
        cases = (
            ({"yaml_path": "lamp", "file": "packages/lights.yaml"}, y.FILE_NOT_ALLOWED),
            ({"yaml_path": "lamp", "file": "packages/*.yaml"}, y.FILE_NOT_ALLOWED),
            ({"yaml_path": "lamp", "file": "packages"}, y.FILE_NOT_ALLOWED),
            ({"yaml_path": "lamp", "file": "*.yaml"}, y.FILE_NOT_ALLOWED),
            ({"yaml_path": "lamp", "file": "automations.yaml"}, y.FILE_NOT_ALLOWED),
            ({"yaml_path": "lamp", "file": "secrets.yaml"}, y.FILE_NOT_ALLOWED),
            ({"yaml_path": "lamp", "file": "../configuration.yaml"}, y.FILE_NOT_ALLOWED),
            ({"yaml_path": "lamp", "include_content": True}, None),
            ({"yaml_path": "lamp", "include_parsed": False}, None),
            ({"yaml_path": "mqtt.password"}, y.PATH_SENSITIVE),
            ({"yaml_path": "a..b"}, y.PATH_INVALID),
            ({"yaml_path": "packages.*"}, y.PATH_INVALID),
            ({}, None),
        )
        for arguments, code in cases:
            with self.subTest(version=version, arguments=arguments):
                result = await call(gateway, TOOL, arguments)
                refused(self, result, dispatched=False)
                if code is not None:
                    self.assertEqual(result["error_code"], code)
                for value in arguments.values():
                    if isinstance(value, str):
                        self.assertNotIn(value, json.dumps(result["details"]))
        self.assertEqual(transport.sent, [])

    async def test_91_parity_row_29_refusal(self):
        await self._row_29_refusal("9.1.0")

    async def test_85_parity_row_29_refusal(self):
        await self._row_29_refusal("8.5.0")

    async def _row_29_no_fabricated_fields(self, version):
        gateway, transport = await self.gateway(version)
        transport.respond = lambda args: ok({
            "success": True, "yaml_path": args["yaml_path"], "matches": [], "count": 0,
            "files_searched": 1, "files_unreadable": 0})
        data = self.assert_success(await call(gateway, TOOL, {"yaml_path": "lamp"}))
        self.assertFalse(data["found"])
        self.assertIsNone(data["value"])
        self.assertNotIn("content", data)
        self.assertNotIn("parsed", data)
        self.assertNotIn("matches", data)
        return data

    async def test_91_parity_row_29_no_fabricated_fields(self):
        data = await self._row_29_no_fabricated_fields("9.1.0")
        self.assertEqual(tuple(data), ENVELOPE_91)
        self.assertEqual((data["tag_completeness"], data["tag_lossy"], data["lossy_tags"]),
                         (y.TAG_NOT_APPLICABLE, None, []))

    async def test_85_parity_row_29_no_fabricated_fields(self):
        data = await self._row_29_no_fabricated_fields("8.5.0")
        self.assertEqual(tuple(data), ENVELOPE_8X)

    async def _row_29_unavailable(self, version):
        gateway, transport = await self.gateway(version)
        transport.respond = lambda args: component_missing()
        result = await call(gateway, TOOL, {"yaml_path": "lamp"})
        refused(self, result, dispatched=True)
        self.assertEqual(result["error_code"], y.READ_FAILED)
        assert_clean(self, json.dumps(result))
        return gateway, transport

    async def test_91_parity_row_29_unavailable_disclosure(self):
        gateway, transport = await self._row_29_unavailable("9.1.0")
        transport.respond = lambda args: result91("initial: [HAMCP319_YAML_TAG_CANARY\n", {"initial": "v"})
        data = self.assert_success(await call(gateway, TOOL, {"yaml_path": "lamp"}))
        self.assertEqual(data["tag_completeness"], y.TAG_UNAVAILABLE)
        self.assertIsNone(data["tag_lossy"])
        self.assertEqual(data["completeness"], "partial")
        self.assertIn(y.TAG_UNAVAILABLE_LIMITATION, data["limitations"])
        transport.respond = lambda args: class_c("yaml_input_tag_source_only")
        data = self.assert_success(await call(gateway, TOOL, {"yaml_path": "input_text"}))
        self.assertEqual((data["tag_lossy"], data["lossy_tags"]), (True, ["!input"]))
        self.assertIn(y.TAG_LOSSY_LIMITATION, data["limitations"])

    async def test_85_parity_row_29_unavailable_disclosure(self):
        await self._row_29_unavailable("8.5.0")

    async def test_91_disabled_tool_is_never_routed(self):
        # Receipt alternate.json /full_disabled_yaml: the disabled tool is
        # unknown upstream; the filesystem-off catalog never advertises it.
        disabled = class_c("full_disabled_yaml")
        self.assertTrue(disabled["isError"])
        for mode in ("A", "B"):
            with self.subTest(mode=mode):
                gateway, transport = await self.gateway("9.1.0", filesystem_off_catalog(), mode=mode)
                self.assertEqual(self.optional(gateway), {name: "optional_absent" for name in OPTIONAL_91})
                names = self.exposed(gateway)
                self.assertFalse(set(OPTIONAL_91) & names)
                self.assertTrue(gateway.health_snapshot()["reviewed_tool_accounting_valid"])
                self.assertEqual(transport.sent, [])

    async def test_91_filesystem_disabled_catalog_admitted(self):
        # Gap 2e: with the filesystem component off, 9.1 advertises the 77
        # stock tools only (class S projection of full-1). That catalog is
        # admitted exact under the same reviewed stock order fingerprint as
        # the full 82-tool wire catalog, and every optional name reports
        # optional_absent rather than blocking stock admission.
        from ha_mcp_engineering.upstream_tool_policy import (
            EXACT_RUNTIME_TOOL_ORDER_FINGERPRINTS,
            schema_fingerprint,
        )

        reviewed_order = "0aab73755d36e877b4ae6ed35683e7232974b04c98edb4fb4a0ea1f0bc8f6c8e"
        self.assertEqual(EXACT_RUNTIME_TOOL_ORDER_FINGERPRINTS["9.1.0"], reviewed_order)
        off = filesystem_off_catalog()
        self.assertEqual(len(off), 77)
        self.assertEqual(schema_fingerprint([tool["name"] for tool in off]), reviewed_order)
        full_stock_order = [tool["name"] for tool in wire_catalog() if tool["name"] not in OPTIONAL_91]
        self.assertEqual(schema_fingerprint(full_stock_order), reviewed_order)
        for mode in ("A", "B"):
            with self.subTest(mode=mode):
                full, _ = await self.gateway("9.1.0", mode=mode)
                gateway, transport = await self.gateway("9.1.0", off, mode=mode)
                health = gateway.health_snapshot()
                self.assertEqual(health["admission_status"], "admitted_exact", health)
                self.assertTrue(health["admission_complete"])
                self.assertTrue(health["reviewed_tool_accounting_valid"])
                self.assertEqual(health["quarantine_reason_counts"], {})
                self.assertEqual(health["upstream_advertised_tool_count"], 77)
                self.assertEqual(health["observed_strict_full_contract_fingerprint"],
                                 health["reviewed_strict_full_contract_fingerprint"])
                self.assertEqual(health["optional_tools"],
                                 {name: "optional_absent" for name in OPTIONAL_91})
                self.assertEqual(self.exposed(gateway), self.exposed(full) - {TOOL})
                self.assertEqual(transport.sent, [])

    async def test_stock_reads_admitted_with_and_without_the_optional_tool(self):
        # Architect ruling 3 (2026-11-07): the optional tool-name hardening
        # must not couple stock-read admission to the optional component.
        # Whether the optional reader (and, on 9.1, its filesystem siblings)
        # is advertised or not, the same stock reads are exposed and a stock
        # read dispatches and succeeds.
        from test_hamcp129_platform_reads import HEALTH

        reader_only_91 = [tool for tool in wire_catalog() if tool["name"] not in SIBLINGS_91]
        variants = {
            "9.1.0": {"full": wire_catalog(), "reader_only": reader_only_91,
                      "component_off": filesystem_off_catalog()},
            "8.5.0": {"full": adm.captured("8.5.0"), "component_off": adm.stock_catalog("8.5.0")},
        }
        optional_names = set(OPTIONAL_91) | set(adm.OPTIONAL)
        for version, catalogs in variants.items():
            stock_sets = {}
            for label, tools in catalogs.items():
                for mode in ("A", "B"):
                    with self.subTest(version=version, variant=label, mode=mode):
                        gateway, transport = await self.gateway(version, tools, mode=mode)
                        health = gateway.health_snapshot()
                        self.assertEqual(health["admission_status"], "admitted_exact", health)
                        self.assertEqual(health["quarantine_reason_counts"], {})
                        exposed = self.exposed(gateway)
                        self.assertEqual(TOOL in exposed, TOOL in {t["name"] for t in tools})
                        stock_sets[(label, mode)] = exposed - optional_names
                        self.assertIn(HEALTH, exposed)
                        result = await call(gateway, HEALTH, {"sections": []})
                        self.assertTrue(result.get("success"), result)
                        self.assertFalse(result["metadata"]["fallback_occurred"])
                        self.assertEqual([name for name, _ in transport.sent], [HEALTH])
            with self.subTest(version=version, check="same_stock_set"):
                self.assertEqual(len(set(map(frozenset, stock_sets.values()))), 1, sorted(stock_sets))

    async def test_91_siblings_prohibited_withheld_and_never_routed(self):
        for mode in ("A", "B"):
            with self.subTest(mode=mode):
                gateway, transport = await self.gateway("9.1.0", mode=mode)
                optional = self.optional(gateway)
                self.assertEqual(optional, {TOOL: "admitted", **{n: "prohibited_withheld" for n in SIBLINGS_91}})
                self.assertFalse(set(SIBLINGS_91) & self.exposed(gateway))
                self.assertTrue(gateway.health_snapshot()["reviewed_tool_accounting_valid"])
                await call(gateway, TOOL, {"yaml_path": "input_text"})
                self.assertEqual({name for name, _ in transport.sent}, {TOOL})

    async def test_91_altered_descriptors_cannot_grant_a_sibling(self):
        reader = RESPONSES["descriptors"][TOOL]

        def as_reader(sibling):
            def change(tool):
                tool.clear()
                tool.update(deepcopy(reader), name=sibling)
            return change

        def drift(tool):
            tool["description"] += " synthetic drift"

        def reader_schema(tool):
            tool["inputSchema"] = deepcopy(reader["inputSchema"])
            tool.setdefault("annotations", {})["readOnlyHint"] = True
            tool["annotations"]["destructiveHint"] = False

        for sibling in SIBLINGS_91:
            for label, change in (("reader_clone", as_reader(sibling)), ("drift", drift),
                                  ("reader_input_schema", reader_schema)):
                with self.subTest(sibling=sibling, change=label):
                    tools = wire_catalog()
                    change(adm._named(tools, sibling))
                    gateway, transport = await self.gateway("9.1.0", tools)
                    self.assertEqual(self.optional(gateway)[sibling], "prohibited_withheld")
                    self.assertNotIn(sibling, self.exposed(gateway))
                    self.assertTrue(y.sibling_drift(tools, "9.1.0"))
                    self.assertFalse(gateway.health_snapshot()["reviewed_tool_accounting_valid"])
                    self.assertEqual(transport.sent, [])
        with self.subTest(change="reader_drift"):
            tools = wire_catalog()
            drift(adm._named(tools, TOOL))
            gateway, transport = await self.gateway("9.1.0", tools)
            self.assertTrue(self.optional(gateway)[TOOL].startswith("quarantined:"))
            self.assertFalse(set(OPTIONAL_91) & self.exposed(gateway))

    async def test_85_session_receiving_content_is_refused(self):
        gateway, transport = await self.gateway("8.5.0")
        transport.respond = lambda args: result91(
            "initial: !HAMCP319_YAML_TAG_CANARY v  # HAMCP319_YAML_COMMENT_CANARY\n", {"initial": "v"},
            args["yaml_path"])
        result = await call(gateway, TOOL, {"yaml_path": "lamp"})
        refused(self, result, dispatched=True)
        self.assertEqual(result["error_code"], y.IDENTITY_INVALID)
        assert_clean(self, json.dumps(result))

    async def test_91_every_sink_is_free_of_raw_yaml_and_canaries(self):
        gateway, transport = await self.gateway("9.1.0")
        lossy = "initial: !HAMCP319_YAML_TAG_CANARY v  # HAMCP319_YAML_COMMENT_CANARY\n"
        uri = "initial: !<tag:HAMCP319_YAML_URI_CANARY,2026:x> v\n"
        malformed = "initial: [HAMCP319_YAML_COMMENT_CANARY\n"

        def mismatch(args):
            response = result91(lossy, {"initial": "v"}, args["yaml_path"])
            response["structuredContent"]["matches"][0]["content"] = "HAMCP319_YAML_MISMATCH_CANARY: v\n"
            return response

        def duplicate(args):
            return {"isError": False, "content": [{"type": "text", "text": (
                '{"success": true, "yaml_path": "lamp", "matches": [{"file": "configuration.yaml", '
                '"yaml_path": "lamp", "content": "a: b\\n", "content": "HAMCP319_YAML_MISMATCH_CANARY: v\\n", '
                '"parsed": {"initial": "v"}}], "count": 1, "files_searched": 1, "files_unreadable": 0}')}]}

        cases = (
            ("lossy", lambda args: result91(lossy, {"initial": "v"}, args["yaml_path"]), True, lossy),
            ("uri", lambda args: result91(uri, {"initial": "v"}, args["yaml_path"]), True, uri),
            ("malformed", lambda args: result91(malformed, {"initial": "v"}, args["yaml_path"]), True, malformed),
            ("mismatch", mismatch, False, lossy),
            ("duplicate", duplicate, False, None),
            ("component_missing", lambda args: component_missing(), False, None),
        )
        for label, respond, success, content in cases:
            with self.subTest(case=label):
                transport.respond = respond
                with captured_logs() as logs:
                    result = await call(gateway, TOOL, {"yaml_path": "lamp"})
                    serialized, record = await yaml_reader_tests.AuditTests.audited(self, gateway, {"yaml_path": "lamp"})
                self.assertEqual(result.get("success"), success, result)
                self.assertEqual(record["tool_name"], TOOL)
                for sink in (json.dumps(result), serialized, "\n".join(logs)):
                    assert_clean(self, sink, *raw_yaml_needles(content))
                if not success:
                    refused(self, result, dispatched=True)


if __name__ == "__main__":
    unittest.main()
