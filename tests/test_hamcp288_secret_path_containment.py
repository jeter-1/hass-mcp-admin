"""HAMCP-288: Engineering never returns the configured ha-mcp secret path.

Every surface that can carry the ``upstream_dashboard_mcp_url`` secret path
out of Engineering is driven with a canary generated at run time (an
unmistakably synthetic ``private_SYNTH...`` segment of random URL-safe text,
the shape ha-mcp's own ``"/private_" + secrets.token_urlsafe(16)`` takes):
the shared sanitizer (strict, Core log window, stored plan, plan content),
response rendering, upstream error envelopes, audit records, structured and
foreign logs and tracebacks written to the process streams, both HTTP
listeners, ``get_error_log``, ``get_core_log_history`` evidence and
``get_incident_logs``. Each test proves the positive (redacted, or the source
withheld with its reason) and the negative (no raw or encoded canary, and no
eight-character run of it, in any decoded view of the serialized output),
and ordinary text, including the endpoint selectors, host and the word
``private_``, is kept. No live Home Assistant; nothing here prints a canary.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import io
import json
import logging
from pathlib import Path
import re
import secrets
import shutil
import sys
import tempfile
import traceback
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests"), str(ROOT)]

from ha_mcp_engineering import sanitization as S  # noqa: E402
from ha_mcp_engineering import secret_paths as SP  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.configuration import Settings, parse_upstream_dashboard_endpoint  # noqa: E402
from ha_mcp_engineering.incident_logs import host_journal as hj  # noqa: E402
from ha_mcp_engineering.incident_logs.cursor import CursorCodec  # noqa: E402
from ha_mcp_engineering.incident_logs.service import IncidentLogService  # noqa: E402
from ha_mcp_engineering.incident_logs.transport import JournalTransport  # noqa: E402
from ha_mcp_engineering.logging_config import JsonFormatter, log_event  # noqa: E402
from ha_mcp_engineering.models.responses import (  # noqa: E402
    FailureResponse,
    SuccessResponse,
    dump_json,
    dump_json_with_truncation,
)
from ha_mcp_engineering.providers.core_logs import CoreLogReader, CoreLogWindow  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from host_journal_support import T0_MS, journal_root, reader  # noqa: E402

PACKAGE = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
TOKEN = SP.MARKER
WINDOW = SP.MIN_BOUNDARY
HOST = "http://local-ha-mcp:9583"
SUFFIXES = ("/mcp", "/catalog/full", "/catalog/search", "/readonly", "/sse", "")
ORDINARY = (
    "readonly", "catalog/full", "/catalog/search", "local-ha-mcp:9583", "http://local-ha-mcp:9583/mcp",
    "private_", "the private_ prefix is public", "mcp", "Started MCP server on port 9583",
    "a1b2c3d4_ha_mcp", "secret_path option is set",
)


def canary() -> str:
    return "private_SYNTH" + secrets.token_urlsafe(12)


def url(secret: str, suffix: str = "/mcp", host: str = HOST) -> str:
    return f"{host}/{secret}{suffix}"


def _json_unescape(text: str) -> str:
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda match: chr(int(match.group(1), 16)), text)
    return re.sub(r"\\+([\"/\\])", r"\1", text)


def views(text: str) -> set[str]:
    """``text`` and every decoding of it an attacker could apply."""

    found = set()
    for start in (text, _json_unescape(text)):
        value = start
        for _ in range(4):
            found.add(value)
            found.add(_json_unescape(value))
            value = unquote(value)
    return found


class Canary(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = canary()
        SP.configure(url(self.secret))
        self.addCleanup(SP.reset)

    def leaked(self, value, secret: str | None = None) -> bool:
        secret = secret or self.secret
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        # The public ``private_`` prefix alone is not a run of the secret.
        runs = {secret[index:index + WINDOW] for index in range(len(secret) - WINDOW + 1)} - {"private_"}
        return any(run in view for view in views(text) for run in runs)

    def assert_no_leak(self, value, secret: str | None = None) -> None:
        # The message never carries the canary or the output.
        self.assertFalse(self.leaked(value, secret), "secret path canary leaked")


def encodings(secret: str) -> dict[str, str]:
    """Every form the path can plausibly take on its way to an output."""

    path = "/" + secret
    full = url(secret, "/catalog/full")
    upper = "".join(f"%{byte:02X}" for byte in secret.encode())
    lower = "".join(f"%{byte:02x}" for byte in secret.encode())
    mixed = "".join(
        f"%{byte:02X}" if index % 2 else f"%{byte:02x}" for index, byte in enumerate(secret.encode())
    )
    return {
        "raw": secret,
        "path": path,
        "url_85": url(secret, "/mcp"),
        "url_91_catalog_full": full,
        "url_91_catalog_search": url(secret, "/catalog/search"),
        "url_91_readonly": url(secret, "/readonly"),
        "quote_path_upper": quote(path, safe=""),
        "quote_path_lower_slash": quote(path, safe="").replace("%2F", "%2f"),
        "quote_url": quote(full, safe=""),
        "quote_url_lower": re.sub(r"%[0-9A-F]{2}", lambda match: match.group(0).lower(), quote(full, safe="")),
        "every_char_upper": upper,
        "every_char_lower": lower,
        "every_char_mixed_case": mixed,
        "double_encoded": quote(quote(path, safe=""), safe=""),
        "double_every_char": "".join(f"%25{byte:02x}" for byte in secret.encode()),
        "triple_encoded": quote(quote(quote(full, safe=""), safe=""), safe=""),
        "json_string": json.dumps(full),
        "json_slash_escaped": json.dumps(full).replace("/", "\\/"),
        "json_nested": json.dumps(json.dumps({"url": full})),
        "json_unicode_upper": "".join(f"\\u{ord(char):04X}" for char in secret),
        "json_unicode_lower": "".join(f"\\u{ord(char):04x}" for char in secret),
        "partial_unicode": secret[:5] + "".join(f"\\u{ord(char):04x}" for char in secret[5:12]) + secret[12:],
        "query_value": f"{HOST}/mcp?path={quote(path, safe='')}",
        "sentence": f"connect failed: All connection attempts failed for url '{full}' (retry 2)",
        "repr_bytes": repr(full.encode()),
    }


class MatcherTests(Canary):
    def test_fragments_are_the_secret_parts_never_the_host_or_selectors(self):
        for suffix in SUFFIXES:
            with self.subTest(suffix=suffix):
                self.assertEqual(SP.derive_fragments(url(self.secret, suffix)), (self.secret,))
        self.assertEqual(SP.derive_fragments(f"{HOST}/mcp"), ())
        self.assertEqual(SP.derive_fragments(f"{HOST}/catalog/full"), ())
        self.assertEqual(SP.derive_fragments(""), ())
        self.assertEqual(SP.derive_fragments(None), ())
        custom = "team-x/" + self.secret + "/vault"
        self.assertEqual(SP.derive_fragments(f"{HOST}/{custom}/mcp"), (custom, self.secret))
        self.assertEqual(SP.derive_fragments(f"{HOST}/{quote(self.secret)}%2Fmore/mcp"),
                         (self.secret + "/more", self.secret))
        # The parser Engineering already uses derives the same credential.
        self.assertIn(self.secret, parse_upstream_dashboard_endpoint(url(self.secret, "/catalog/full")).secret_values)
        SP.configure(f"{HOST}/{custom}/mcp")
        self.assertEqual(SP.fragment_count(), 2)
        self.assertEqual(SP.redact("x " + custom + " y"), "x " + TOKEN + " y")

    def test_every_encoding_is_redacted_in_every_endpoint_shape(self):
        cases = encodings(self.secret)
        self.assertEqual(len(cases), 25)
        for name, text in cases.items():
            with self.subTest(form=name):
                self.assertTrue(SP.contains(text))
                safe = SP.redact(text)
                self.assertIn(TOKEN, safe)
                self.assert_no_leak(safe)
                self.assert_no_leak(SP.mask_bytes(text.encode()).decode())

    def test_a_cut_fragment_at_any_edge_is_redacted_and_the_visible_part_is_bounded(self):
        line = "INFO Starting MCP server on " + url(self.secret)
        cut = line.index(self.secret)
        for position in range(cut + 1, cut + len(self.secret)):
            first, second = line[:position], line[position:]
            for decoration in ("", "...", "\u2026", "...<truncated>", '"', ")", "  "):
                with self.subTest(position=position - cut, decoration=decoration):
                    left = SP.redact(first + decoration)
                    right = SP.redact(decoration.strip(".\u2026<truncated>") + second)
                    self.assert_no_leak(left)
                    self.assert_no_leak(right)
                    # The justified bound: at most MIN_BOUNDARY - 1 characters
                    # of a fragment remain visible on either side of a cut.
                    visible = len(first) - cut
                    if visible >= WINDOW and first[cut:] != "private_":
                        self.assertIn(TOKEN, left)
                    if len(self.secret) - visible >= WINDOW:
                        self.assertIn(TOKEN, right)
        lines = SP.redact_lines(first_line := "a " + self.secret[:12] + "\r\n" + self.secret[12:] + "/mcp b\nplain")
        self.assertEqual(lines.split("\n")[2], "plain")
        self.assertTrue(lines.split("\n")[0].endswith("\r"))
        self.assertEqual(lines.count(TOKEN), 2)
        self.assert_no_leak(lines)
        self.assertNotEqual(first_line, lines)

    def test_the_boundary_short_segments_and_mapping_keys_are_pinned(self):
        # The documented bound is eight characters, not whatever the module says.
        self.assertEqual(SP.MIN_BOUNDARY, 8)
        self.assertEqual(SP.redact(self.secret[-8:] + " tail"), TOKEN + " tail")
        self.assertEqual(SP.redact("head " + self.secret[:9]), "head " + TOKEN)
        self.assertEqual(SP.redact(self.secret[-7:] + " tail"), self.secret[-7:] + " tail")
        # A custom path of short segments is secret only as a whole, so its
        # JSON-escaped ``\/`` form (nested any depth) must still match.
        short = "/".join(secrets.token_hex(3) for _ in range(4))
        SP.configure(f"{HOST}/{short}/mcp")
        self.assertEqual(SP.derive_fragments(f"{HOST}/{short}/mcp"), (short,))
        for depth in (1, 2, 3):
            with self.subTest(depth=depth):
                escaped = short.replace("/", "\\" * depth + "/")
                self.assertEqual(SP.redact("u=" + escaped + " end"), "u=" + TOKEN + " end")
        SP.configure(url(self.secret))
        # Mapping keys are redacted by the tree pass itself.
        tree = SP.redact_tree({url(self.secret): [{"k": self.secret}], 7: "seven", "plain": "kept"})
        self.assertEqual(sorted(map(str, tree)), sorted([f"{HOST}/{TOKEN}/mcp", "7", "plain"]))
        self.assertEqual(tree[7], "seven")
        self.assert_no_leak(json.dumps({str(key): value for key, value in tree.items()}))

    def test_a_cut_needs_evidence_and_a_bare_run_of_fragment_characters_is_ordinary(self):
        head, tail = self.secret[:16], self.secret[-9:]
        # A value that is only a run of fragment characters is not a cut one.
        for bare in (head, tail, f'"{head}"', f"({tail})", f"  {head}  "):
            with self.subTest(bare=bare):
                self.assertEqual(SP.redact(bare), bare)
        # The whole fragment is always matched, with or without other text.
        self.assertEqual(SP.redact(self.secret), TOKEN)
        # Evidence of a cut on either side: other text, a cut mark, an
        # adjacent line.
        self.assertEqual(SP.redact("x " + head), "x " + TOKEN)
        self.assertEqual(SP.redact(tail + " y"), TOKEN + " y")
        self.assertEqual(SP.redact(head + "..."), TOKEN + "...")
        self.assertEqual(SP.redact(head + "<truncated>"), TOKEN + "<truncated>")
        self.assertEqual(SP.redact("…" + tail), "…" + TOKEN)
        self.assertEqual(SP.redact_lines(head + "\n" + tail), TOKEN + "\n" + TOKEN)
        self.assertEqual(SP.redact_lines("plain\n" + tail), "plain\n" + TOKEN)
        self.assertEqual(SP.redact_lines(head + "\nplain"), TOKEN + "\nplain")

    def test_a_low_entropy_configured_path_keeps_ordinary_upstream_values_complete(self):
        # The exact-image acceptance configures this synthetic path; its fixture
        # returns a trace whose trigger is the bare word ``synthetic`` (a cut
        # prefix of the path). That value is ordinary text, so the delegated
        # read stays complete, while the path itself is still redacted.
        configured = "http://abcdef12-ha-mcp:18086/synthetic-upstream-secret/mcp"
        SP.configure(configured)
        trace = [{"run_id": "fixture-trace", "state": "stopped", "trigger": "synthetic"}]
        result = S.sanitize_untrusted_data(trace, max_string=4096)
        self.assertEqual(result.value, trace)
        self.assertFalse(result.redaction_applied)
        leaked = S.sanitize_untrusted_data({"error": "failed for url " + configured}, max_string=4096)
        self.assertNotIn("synthetic-upstream-secret", json.dumps(leaked.value))
        self.assertTrue(leaked.redaction_applied)

    def test_ordinary_text_is_kept_and_nothing_changes_unconfigured(self):
        for text in ORDINARY:
            with self.subTest(text=text):
                self.assertFalse(SP.contains(text))
                self.assertEqual(SP.redact(text), text)
        # A run shorter than the boundary at an edge is ordinary text.
        self.assertEqual(SP.redact("x " + self.secret[:WINDOW - 1]), "x " + self.secret[:WINDOW - 1])
        self.assertEqual(SP.redact(self.secret[-(WINDOW - 1):] + " y"), self.secret[-(WINDOW - 1):] + " y")
        # Inside a line (not at an edge) a short run is ordinary text too.
        middle = "a " + self.secret[3:14] + " b"
        self.assertEqual(SP.redact(middle), middle)
        SP.reset()
        self.assertFalse(SP.configured())
        for text in encodings(self.secret).values():
            self.assertEqual(SP.redact(text), text)
        self.assertEqual(SP.mask_bytes(self.secret.encode()), self.secret.encode())

    def test_fragments_are_never_reported_only_counted(self):
        self.assertEqual(SP.fragment_count(), 1)
        source = (PACKAGE / "secret_paths.py").read_text(encoding="utf-8")
        self.assertNotIn("LOGGER", source)
        self.assertNotIn("print(", source)
        # The module has no Engineering import, so no sink can be reached.
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, ast.dump(node))


class SanitizerSurfaceTests(Canary):
    def test_strict_pass_redacts_every_form_in_values_keys_and_repr(self):
        cases = encodings(self.secret)
        data = {
            "values": dict(cases),
            "keys": {text: "kept" for text in cases.values()},
            "nested": [{"error": cases["sentence"]}, (cases["url_85"],)],
            "object": ValueError(cases["url_91_catalog_full"]),
            "ordinary": list(ORDINARY),
        }
        result = S.sanitize_untrusted_data(data, max_string=4096)
        self.assert_no_leak(result.value)
        self.assertIn("token", result.redaction_categories)
        self.assertEqual(result.value["ordinary"], list(ORDINARY))
        self.assertFalse(result.failed_closed)

    def test_truncation_runs_after_redaction(self):
        text = "x" * 40 + url(self.secret)
        for bound in range(30, len(text) + 2):
            with self.subTest(bound=bound):
                self.assert_no_leak(S.sanitize_untrusted_data(text, max_string=bound).value)

    def test_identity_record_and_memo_never_skip_the_secret_path(self):
        SP.reset()
        registry = S.ReferenceMarkerRegistry()
        held = registry.register_output("own " + url(self.secret))
        memo = S.SegmentMemo(())
        text = "memo " + url(self.secret)
        self.assertEqual(S.sanitize_untrusted_data(text, segment_memo=memo).value, text)
        SP.configure(url(self.secret))
        self.assertFalse(registry.output(held))
        self.assert_no_leak(S.sanitize_untrusted_data([held], registry=registry).value)
        self.assert_no_leak(S.sanitize_untrusted_data(text, segment_memo=memo).value)
        # Engineering's own redacted output is still kept by identity.
        own = S.sanitize_untrusted_data("own " + url(self.secret), registry=registry).value
        self.assertTrue(registry.output(own))

    def test_core_log_window_redacts_each_line_edge_and_keeps_line_count(self):
        first, second = url(self.secret)[:-20], url(self.secret)[-20:]
        lines = ["INFO ordinary", "WARN rich wrap " + first, second + " continues", "INFO " + quote(url(self.secret)),
                 "INFO crlf " + self.secret[:10] + "\r", "INFO plain " + ORDINARY[4]]
        result = S.sanitize_log_lines(lines)
        self.assertEqual(len(result.value), len(lines))
        self.assertEqual(result.value[0], "INFO ordinary")
        self.assertEqual(result.value[-1], lines[-1])
        self.assertTrue(result.value[4].endswith(TOKEN + "\r"))
        self.assert_no_leak(result.value)
        self.assertFalse(result.failed_closed)

    def test_stored_plan_rescan_and_new_plan_content(self):
        stored = {"note": "[REDACTED:token] then " + url(self.secret, "/readonly") + " [REDACTED:token]"}
        self.assert_no_leak(S.rescan_stored_plan(stored).value)
        screen = S.screen_plan_content({"note": "see " + quote(url(self.secret))}, baseline=None)
        self.assertTrue(screen.refused)
        self.assertIn("token", screen.categories)
        allowed = S.screen_plan_content({"note": "see " + url(self.secret)}, baseline=None,
                                        refuse=lambda pattern: False)
        self.assertFalse(allowed.refused)
        self.assert_no_leak(allowed.value)
        kept = S.screen_plan_content({"note": "ordinary readonly catalog note"}, baseline=None)
        self.assertEqual((kept.refused, kept.findings), (False, ()))


class Value:
    def __init__(self, text):
        self.text = text

    def __str__(self):
        return self.text


class Text(str):
    pass


class ResponseTests(Canary):
    def test_rendered_responses_never_carry_the_path(self):
        cases = encodings(self.secret)
        data = {"url": cases["url_91_catalog_full"], cases["path"]: 1, "objects": [Value(cases["url_85"]),
                Text(cases["quote_url"])], "tuple": (cases["json_nested"],), "ok": ORDINARY[0]}
        for limit in (60_000, 400, 120):
            with self.subTest(limit=limit):
                output, _truncated = dump_json_with_truncation(data, limit)
                self.assertLessEqual(len(output), limit)
                self.assert_no_leak(output)
        rendered = json.loads(dump_json(data))
        self.assertEqual(rendered["ok"], ORDINARY[0])
        self.assertEqual(rendered["url"], HOST + "/" + TOKEN + "/catalog/full")

    def test_upstream_error_envelopes(self):
        failure = FailureResponse(
            operation="list_dashboards", error="upstream_unavailable", error_code="UPSTREAM_UNAVAILABLE",
            message="Client error '404 Not Found' for url '" + url(self.secret, "/catalog/full") + "'",
            details={"endpoint": url(self.secret), "status": 404, "cause": ConnectionError(url(self.secret))},
        )
        self.assert_no_leak(failure.to_json())
        success = SuccessResponse(operation="server_info", summary="ok",
                                  data={"upstream": quote(url(self.secret), safe="")})
        self.assert_no_leak(success.to_json())

    def test_ordinary_responses_render_unchanged(self):
        data = {"items": list(ORDINARY), "n": 3, "nested": {"readonly": True}}
        SP.reset()
        before = dump_json_with_truncation(data, 200)
        SP.configure(url(self.secret))
        self.assertEqual(dump_json_with_truncation(data, 200), before)


class AuditAndLoggingTests(Canary):
    def test_audit_records_and_their_reads(self):
        with tempfile.TemporaryDirectory(dir=str(ROOT / ".artifacts" / "tmp") if (ROOT / ".artifacts" / "tmp").is_dir() else None) as root:
            path = str(Path(root) / "audit.jsonl")
            audit = AuditLogger(path, "synthetic-hamcp288-access-secret")
            audit.write({"event": "tool_call", "error": "upstream " + url(self.secret, "/catalog/full"),
                         "arguments": {quote(url(self.secret)): "x"}})
            text = Path(path).read_text(encoding="utf-8")
        self.assertIn(TOKEN, text)
        self.assert_no_leak(text)

    def test_structured_foreign_and_traceback_output_through_the_stream_guard(self):
        sink = io.StringIO()
        stream = SP.RedactingStream(sink)
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        own = logging.getLogger("ha_mcp_engineering.hamcp288_test")
        foreign = logging.getLogger("httpx.hamcp288_test")
        for logger in (own, foreign):
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
            self.addCleanup(logger.removeHandler, handler)
        log_event(own, logging.ERROR, "upstream_failed", "upstream failed",
                  context={"url": url(self.secret, "/catalog/full")})
        own.error("plain " + url(self.secret))
        foreign_handler = logging.StreamHandler(stream)
        foreign.addHandler(foreign_handler)
        self.addCleanup(foreign.removeHandler, foreign_handler)
        foreign.info('HTTP Request: POST %s "HTTP/1.1 200 OK"', url(self.secret, "/readonly"))
        try:
            raise ConnectionError("All connection attempts failed: " + url(self.secret))
        except ConnectionError as error:
            traceback.print_exception(error, file=stream)
        # Written in pieces, and split mid-path: still seen whole.
        stream.write("piece " + HOST + "/" + self.secret[:9])
        stream.write(self.secret[9:] + "/mcp\n")
        print("print " + quote(url(self.secret), safe=""), file=stream)
        stream.write("held " + self.secret[:11])
        stream.flush()
        self.assertNotIn("held", sink.getvalue())
        stream.drain()
        output = sink.getvalue()
        self.assertIn(TOKEN, output)
        self.assertIn("held " + TOKEN, output)
        self.assert_no_leak(output)
        self.assertIn("Traceback", output)

    def test_a_line_over_the_pending_bound_is_written_redacted(self):
        sink = io.StringIO()
        stream = SP.RedactingStream(sink)
        stream.write("x" * SP.RedactingStream.MAX_PENDING + " " + url(self.secret))
        self.assertTrue(sink.getvalue())
        self.assert_no_leak(sink.getvalue())

    def test_install_guards_both_process_streams_once(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "stdout", out), patch.object(sys, "stderr", err), \
                patch("atexit.register") as register:
            SP.install_stream_guard()
            SP.install_stream_guard()
            self.assertIsInstance(sys.stdout, SP.RedactingStream)
            self.assertIs(sys.stdout.target, out)
            self.assertIs(sys.stderr.target, err)
            print("stdout " + url(self.secret))
            print("stderr " + url(self.secret), file=sys.stderr)
            self.assertEqual(register.call_count, 2)
        self.assert_no_leak(out.getvalue() + err.getvalue())
        self.assertIn("stdout " + HOST + "/" + TOKEN + "/mcp", out.getvalue())

    def test_application_wiring(self):
        tree = ast.parse((PACKAGE / "application.py").read_text(encoding="utf-8"))
        functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

        def calls(function):
            return [ast.unparse(node.func) for node in ast.walk(functions[function]) if isinstance(node, ast.Call)]

        main = functions["main"]
        self.assertEqual(ast.unparse(main.body[0]), "secret_paths.install_stream_guard()")
        self.assertIn("secret_paths.configure", calls("main"))
        self.assertLess(calls("main").index("secret_paths.configure"), calls("main").index("configure_logging"))
        first = [ast.unparse(statement) for statement in functions["create_application"].body[:2]]
        self.assertEqual(first[1], "secret_paths.configure(settings.upstream_dashboard_mcp_url)")
        self.assertEqual(calls("_serve").count("secret_paths.ResponseGuard"), 2)

    def test_every_test_module_that_builds_the_application_resets_the_redaction(self):
        # Test isolation: the redaction is process-wide, and the shard runner
        # runs many modules in one process, so a module whose tests (or the
        # test-module helpers they call) build the application must reset it.
        targets = {"create_application", "_serve"}
        trees = {path.stem: ast.parse(path.read_text(encoding="utf-8")) for path in (ROOT / "tests").glob("test_*.py")}

        def builds(node, names):
            for item in ast.walk(node):
                if isinstance(item, ast.Call):
                    func = item.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                    if name in names or (name == "main" and ast.unparse(func) == "application.main"):
                        return True
            return False

        helpers: set[str] = set()
        building: set[str] = set()
        grew = True
        while grew:
            grew = False
            for module, tree in trees.items():
                for node in tree.body:
                    if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                            and node.name not in helpers | {"tearDownModule"}
                            and builds(node, targets | helpers)):
                        helpers.add(node.name)
                        building.add(module)
                        grew = True
        building.discard(Path(__file__).stem)
        self.assertGreaterEqual(len(building), 14)
        for module in sorted(building):
            with self.subTest(module=module):
                teardown = [node for node in trees[module].body
                            if isinstance(node, ast.FunctionDef) and node.name == "tearDownModule"]
                self.assertEqual(len(teardown), 1)
                self.assertIn("secret_paths.reset()", ast.unparse(teardown[0]))


class ListenerGuardTests(Canary):
    def run_app(self, app, scope=None):
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        asyncio.run(SP.ResponseGuard(app)(scope or {"type": "http"}, receive, send))
        return sent

    def test_headers_and_bodies_are_masked_to_the_same_length(self):
        body = json.dumps({"result": {"content": [{"text": "see " + url(self.secret, "/catalog/full")}]}}).encode()
        plain = b"ordinary readonly chunk"

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 302, "headers": [
                (b"content-length", str(len(body) + len(plain)).encode()),
                (b"location", url(self.secret).encode())]})
            await send({"type": "http.response.body", "body": body, "more_body": True})
            await send({"type": "http.response.body", "body": plain})

        start, first, second = self.run_app(app)
        self.assertEqual(len(first["body"]), len(body))
        self.assertEqual(start["headers"][0], (b"content-length", str(len(body) + len(plain)).encode()))
        self.assertEqual(len(start["headers"][1][1]), len(url(self.secret)))
        self.assert_no_leak(start["headers"][1][1].decode() + first["body"].decode())
        self.assertIs(second["body"], plain)
        self.assertEqual(first["more_body"], True)

    def test_other_scopes_and_unconfigured_pass_through(self):
        seen = []

        async def app(scope, receive, send):
            seen.append(scope["type"])
            await send({"type": "http.response.body", "body": self.secret.encode()})

        self.run_app(app, {"type": "lifespan"})
        SP.reset()
        [message] = self.run_app(app)
        self.assertEqual(message["body"], self.secret.encode())
        self.assertEqual(seen, ["lifespan", "http"])
        guard = SP.ResponseGuard(app)
        self.assertIs(guard.app, app)


class LogToolTests(Canary, unittest.IsolatedAsyncioTestCase):
    def settings(self) -> Settings:
        return Settings(
            ha_url="http://supervisor/core", ha_token="synthetic-hamcp288-token",
            access_secret="synthetic-hamcp288-access-secret", port=8100,
            audit_path="unused-synthetic-audit.jsonl", rate_limit_per_minute=120, rate_limit_burst=20,
            destructive_services=frozenset(), upstream_dashboard_mcp_url=url(self.secret),
        )

    async def test_core_log_history_evidence(self):
        lines = ["2026-11-07 01:00:00 INFO ordinary " + ORDINARY[3],
                 "2026-11-07 01:00:01 ERROR upstream " + url(self.secret, "/catalog/full"),
                 "2026-11-07 01:00:02 ERROR wrapped " + url(self.secret)[:-14],
                 url(self.secret)[-14:] + " end",
                 "2026-11-07 01:00:03 ERROR encoded " + quote(url(self.secret), safe="")]
        window = CoreLogWindow(content=("\n".join(lines) + "\n").encode(), download_capped=False,
                               received_at="2026-11-07T01:00:09Z")
        out = CoreLogReader(self.settings()).evidence(window, limit=20, offset=0)
        self.assert_no_leak(out)
        self.assertEqual(out["log"].split("\n")[0], lines[0])

    async def test_get_error_log(self):
        from ha_mcp_engineering.tools import compatibility

        entry = {"timestamp": 1_789_000_000.0, "name": "homeassistant.components.synthetic", "level": "ERROR",
                 "message": ["upstream " + url(self.secret, "/catalog/full"), quote(url(self.secret))],
                 "exception": "Traceback ... " + url(self.secret), "code": "ordinary", "source": ["x.py", 1]}
        with patch.object(compatibility, "ws_command", new=AsyncMock(return_value=[entry])):
            text = await compatibility.get_error_log(tail_lines=1)
        payload = json.loads(text)
        self.assertTrue(payload["success"])
        self.assert_no_leak(text)


class IncidentLogTests(Canary, unittest.IsolatedAsyncioTestCase):
    HA_MCP = "addon:a0d7b954_ha_mcp"
    OTHER = "addon:core_mosquitto"

    def journal(self):
        path = journal_root("uncompressed")
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def service(self, journal_reader) -> IncidentLogService:
        transport = JournalTransport(token_source=lambda: "synthetic-hamcp288-supervisor", timeout_seconds=1.0,
                                     base_url="http://127.0.0.1:9")
        codec = CursorCodec("incident_logs", key=b"h" * 32, clock=lambda: T0_MS / 1000 + 7200)
        return IncidentLogService(transport, codec=codec, clock=lambda: T0_MS + 7_200_000,
                                  zone_source=lambda: "UTC", addon_zone_source=lambda: "UTC",
                                  journal_reader=journal_reader)

    async def read(self, service, **arguments) -> dict:
        from ha_mcp_engineering.incident_logs import contracts as c

        arguments.setdefault("start", c.format_ms(T0_MS))
        arguments.setdefault("end", c.format_ms(T0_MS + 7_000_000))
        telemetry, token = begin_request()
        telemetry.caller_id = "synthetic_authenticated_caller"
        try:
            return await service.read(arguments)
        finally:
            end_request(token)

    def recording(self, planted: dict[bytes, bytes] | None = None):
        real = reader(self.journal())
        planned: list[str] = []

        class Recording:
            def read(self, plans, digest, deadline):
                planned.extend(plan.key for plan in plans)
                result = real.read(plans, digest, deadline)
                for scan in result.scans.values():
                    for index, record in enumerate(scan.records):
                        for old, new in (planted or {}).items():
                            if record.message and old in record.message:
                                scan.records[index] = dataclasses.replace(record, message=new)
                return result
        return Recording(), planned

    def row(self, page, key):
        return next(row for row in page["sources"] if row["source"] == key)

    async def test_the_ha_mcp_app_log_is_withheld_on_every_page(self):
        journal_reader, planned = self.recording()
        service = self.service(journal_reader)
        page = await self.read(service, sources=[self.OTHER, self.HA_MCP], limit=1)
        pages = [page]
        while pages[-1]["next_cursor"]:
            self.assertLess(len(pages), 50)
            pages.append(await self.read(service, sources=[self.OTHER, self.HA_MCP], limit=1,
                                         cursor=pages[-1]["next_cursor"]))
        self.assertGreater(len(pages), 1)
        for page in pages:
            row = self.row(page, self.HA_MCP)
            self.assertEqual((row["status"], row["reason"], row["detail"], row["entries_consumed"]),
                             ("unavailable", SP.WITHHELD_SOURCE_REASON, SP.WITHHELD_SOURCE_DETAIL, 0))
            self.assertFalse(page["complete"])
            self.assertFalse([entry for entry in page["entries"] if entry["source"] == self.HA_MCP])
        # The other source is read as before; the withheld one is never planned.
        self.assertTrue(any(entry["source"] == self.OTHER for page in pages for entry in page["entries"]))
        self.assertNotIn(self.HA_MCP, planned)
        self.assertIn(self.OTHER, planned)
        self.assert_no_leak(pages)

    async def test_withheld_slugs(self):
        for slug in ("ha_mcp", "a0d7b954_ha_mcp", "local_ha_mcp", "ha_mcp_dev", "local-ha-mcp"):
            self.assertTrue(SP.is_withheld_addon_slug(slug), slug)
        for slug in ("core_mosquitto", "sha_mcpx", "ha_mcpx", "hamcp", "self", "", None):
            self.assertFalse(SP.is_withheld_addon_slug(slug), slug)
        # The slug the configured endpoint's Supervisor DNS name names.
        SP.configure(url(self.secret, host="http://5c53de3b-mcp-bridge:9583"))
        self.assertTrue(SP.is_withheld_addon_slug("5c53de3b_mcp_bridge"))
        SP.reset()
        self.assertTrue(SP.is_withheld_addon_slug("a0d7b954_ha_mcp"))
        self.assertFalse(SP.is_withheld_addon_slug("5c53de3b_mcp_bridge"))
        journal_reader, planned = self.recording()
        page = await self.read(self.service(journal_reader), sources=["addon:ha_mcp"])
        self.assertEqual(self.row(page, "addon:ha_mcp")["reason"], SP.WITHHELD_SOURCE_REASON)
        self.assertEqual(planned, [])

    async def test_a_path_in_any_other_source_is_withheld_or_redacted(self):
        raw = ("connecting to " + url(self.secret, "/catalog/full")).encode()
        encoded = ("retry " + quote(url(self.secret), safe="")).encode()
        journal_reader, _planned = self.recording({
            b"after reboot": raw,
            b"clock recovered": encoded,
        })
        page = await self.read(self.service(journal_reader), sources=[self.OTHER])
        messages = [entry["message"] for entry in page["entries"]]
        # Raw and percent-encoded alike, the planted entry is withheld whole
        # (the fragment is a known secret of the journal screen), the rest kept.
        self.assertIn(hj.WITHHELD_MARKER, messages)
        self.assertFalse([message for message in messages if "connecting to" in message or "retry " in message])
        self.assertIn("Error: Connection refused", messages)
        self.assert_no_leak(page)
        for text in (self.secret[:12], self.secret[-12:]):
            filtered = await self.read(self.service(journal_reader), sources=[self.OTHER], text=text)
            self.assertEqual(filtered["returned"], 0)

    async def test_unconfigured_reads_are_unchanged_for_other_sources(self):
        journal_reader, _planned = self.recording()
        configured = await self.read(self.service(journal_reader), sources=[self.OTHER])
        SP.reset()
        journal_reader, _planned = self.recording()
        plain = await self.read(self.service(journal_reader), sources=[self.OTHER])
        self.assertEqual([entry["message"] for entry in configured["entries"]],
                         [entry["message"] for entry in plain["entries"]])


if __name__ == "__main__":
    unittest.main()
