"""HAMCP-319 design sections 3.2, 3.3, 8.1 item 2 and 8.2: endpoint selector.

Step 0 (configuration acceptance) is today's parser and stays the only
configuration-time refusal. Step 1 (``classify_endpoint_form``) only labels an
accepted value. Under ha-mcp 9.1.0 a ``legacy`` value is refused after
initialize and before any ``tools/list``; every other registered version keeps
its contract on either form, on the same no-redirect transport.

Loopback peers only (``support.ha_mcp_redirect_peer``); no Docker, network or
Home Assistant access. Every secret and base value is synthetic.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from mcp.shared._httpx_utils import create_mcp_http_client  # noqa: E402

from ha_mcp_engineering.application import validate_settings  # noqa: E402
from ha_mcp_engineering.clients import upstream_read as upstream_read_module  # noqa: E402
from ha_mcp_engineering.clients.ha_mcp_endpoint import (  # noqa: E402
    ENDPOINT_REFUSAL_CODES,
    LEGACY,
    STRICT_FULL,
    UPSTREAM_CATALOG_MODE_UNADMITTED,
    UPSTREAM_CATALOG_SELECTOR_INVALID,
    UPSTREAM_ENDPOINT_REJECTED,
    UPSTREAM_REDIRECT_REFUSED,
    EndpointSnapshot,
    NoRedirectBinding,
    UpstreamEndpointRefused,
    classify_endpoint_form,
    endpoint_form_failing_rule,
    ha_mcp_no_redirect_client_factory,
    publish_endpoint_snapshot,
    require_selector_agreement,
    selector_refusal_code,
    snapshot_is_current,
)
from ha_mcp_engineering.clients.mcp import DashboardTransportError  # noqa: E402
from ha_mcp_engineering.clients.upstream_read import McpReadGatewayTransport  # noqa: E402
from ha_mcp_engineering.configuration import (  # noqa: E402
    parse_upstream_dashboard_endpoint,
)
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from support.ha_mcp_redirect_peer import RedirectPeer  # noqa: E402
from test_hamcp319_no_redirect import (  # noqa: E402
    CLIENTS,
    DASHBOARD_TOOLS,
    DEADLINE_SECONDS,
    READ_TOOLS,
    _drive,
    _settings,
    _sinks,
    capture_all_logs,
)

HOST = "http://ha-mcp.invalid:9583"
B = f"{HOST}/private_synthetictoken123"
UPSTREAM_CONFIG_ERROR = (
    "upstream_dashboard_mcp_url is malformed or lacks a secret-bearing credential"
)
SI = UPSTREAM_CATALOG_SELECTOR_INVALID
MU = UPSTREAM_CATALOG_MODE_UNADMITTED

# Design section 3.2 accept/reject table. (row, value, Step 0 accepted, form,
# failing rule, 9.1 refusal code for a legacy value).
ROWS: tuple[tuple[int, str, bool, str | None, str | None, str | None], ...] = (
    (1, f"{B}/catalog/full", True, STRICT_FULL, None, None),
    (
        2,
        "https://ha-mcp.invalid/a/private_synthetictoken123/catalog/full",
        True,
        STRICT_FULL,
        None,
        None,
    ),
    (3, f"{B}/catalog/full?x=1", True, STRICT_FULL, None, None),
    (
        4,
        "http://u:synthetic-pass@ha-mcp.invalid:9583/base/catalog/full",
        True,
        STRICT_FULL,
        None,
        None,
    ),
    (5, B, True, LEGACY, "S4", MU),
    (5, f"{B}/", True, LEGACY, "S4", MU),
    (5, f"{B}/mcp", True, LEGACY, "S4", MU),
    (5, f"{HOST}/synthetic-upstream-secret/mcp/", True, LEGACY, "S4", MU),
    (6, f"{B}/catalog/full/", True, LEGACY, "S4", SI),
    (7, f"{B}/catalog//full", True, LEGACY, "S4", SI),
    (7, f"{B}//catalog/full", True, LEGACY, "S4", SI),
    (8, f"{B}/catalog/full/catalog/full", True, LEGACY, "S4", SI),
    (8, f"{B}/catalog/search/catalog/full", True, LEGACY, "S4", SI),
    (9, f"{B}/readonly/catalog/full", True, LEGACY, "S4", SI),
    (9, f"{B}/default/catalog/full", True, LEGACY, "S4", SI),
    (10, f"{B}/catalog/search", True, LEGACY, "S4", SI),
    (10, f"{B}/catalog/other", True, LEGACY, "S4", SI),
    (10, f"{B}/readonly", True, LEGACY, "S4", SI),
    (11, f"{B}/Catalog/Full", True, LEGACY, "S4", SI),
    (11, f"{B}/catalog/FULL", True, LEGACY, "S4", SI),
    (12, f"{B}/catalog/full;mode=readonly", True, LEGACY, "S4", SI),
    (12, f"{B};x/catalog/full", True, LEGACY, "S4", SI),
    (13, f"{B}/catalog%2Ffull", True, LEGACY, "S3", SI),
    (13, f"{B}/catalog/%66ull", True, LEGACY, "S3", SI),
    (
        14,
        f"{HOST}/private%2Fsynthetictoken123/catalog/full",
        True,
        LEGACY,
        "S3",
        SI,
    ),
    (
        14,
        f"{HOST}/a/%2E%2E/private_synthetictoken123/catalog/full",
        True,
        LEGACY,
        "S3",
        SI,
    ),
    (14, f"{HOST}/synthetic%2Dupstream-secret/mcp", True, LEGACY, "S3", MU),
    (15, f"{B}/./catalog/full", True, LEGACY, "S2", SI),
    (15, f"{B}/x/../catalog/full", True, LEGACY, "S2", SI),
    (
        15,
        f"{HOST}/../private_synthetictoken123/catalog/full",
        True,
        LEGACY,
        "S2",
        SI,
    ),
    (
        16,
        "http://HA-MCP.invalid:9583/private_synthetictoken123/catalog/full",
        True,
        LEGACY,
        "S2",
        SI,
    ),
    (
        16,
        "http://ha-mcp.invalid:80/private_synthetictoken123/catalog/full",
        True,
        LEGACY,
        "S2",
        SI,
    ),
    (17, f"{B}/cata\tlog/full", True, LEGACY, "S1", SI),
    (17, f"{B}\\catalog\\full", True, LEGACY, "S1", SI),
    (18, f"{HOST}/short/catalog/full", False, None, None, None),
    (19, f"{B}/catalog/full#frag", False, None, None, None),
)

STEP0_MESSAGES = {
    18: "The upstream dashboard MCP URL must contain a secret-bearing path or credential.",
    19: "The upstream dashboard MCP URL must not include a fragment.",
}

# Section 8.2 extra Step 1 vectors: (value, form, failing rule).
EXTRA_VECTORS: list[tuple[str, str, str | None]] = []
for _keyword in ("catalog", "full", "search", "readonly", "default"):
    for _variant in {_keyword, _keyword.upper(), _keyword.capitalize()}:
        EXTRA_VECTORS.append((f"{B}/{_variant}/catalog/full", LEGACY, "S4"))
        EXTRA_VECTORS.append(
            (
                f"http://u:synthetic-pass@ha-mcp.invalid:9583/{_variant}/catalog/full",
                LEGACY,
                "S4",
            )
        )
EXTRA_VECTORS.extend(
    [
        (f"{B}%2fx/catalog/full", LEGACY, "S3"),
        (f"{B}%2Fx/catalog/full", LEGACY, "S3"),
        (f"{B}/./catalog/full", LEGACY, "S2"),
        (f"{B}/../catalog/full", LEGACY, "S2"),
        (f"{B}/%2E/catalog/full", LEGACY, "S3"),
        (f"{B}/%2e%2e/catalog/full", LEGACY, "S3"),
        (f"{B}/catalog/full?", LEGACY, "S2"),
        (
            "https://ha-mcp.invalid:443/private_synthetictoken123/catalog/full",
            LEGACY,
            "S2",
        ),
        (
            "https://ha-mcp.invalid:9443/private_synthetictoken123/catalog/full",
            STRICT_FULL,
            None,
        ),
        (
            "http://synthetic-user@ha-mcp.invalid:9583/b/catalog/full",
            STRICT_FULL,
            None,
        ),
        (
            "http://ha-mcp.invalid:9583/b/catalog/full?credential=synthetic-q",
            STRICT_FULL,
            None,
        ),
        (f"{B}/v1.2~x_y-z/catalog/full", STRICT_FULL, None),
        (f"{B}/café/catalog/full", LEGACY, "S1"),
        (f"{B}/a b/catalog/full", LEGACY, "S1"),
        (f"{B}/catalog/full\n", LEGACY, "S1"),
        (f"{B}/catalog/full/", LEGACY, "S4"),
        (f"{B}/catalog/fulls", LEGACY, "S4"),
        (f"{HOST}/catalog/full?credential=synthetic-q", LEGACY, "S4"),
    ]
)


def _validate_errors(value: str) -> list[str]:
    try:
        validate_settings(_settings(value))
    except Exception as exc:  # noqa: BLE001 - the error list is the result
        return [str(exc), repr(getattr(exc, "details", None))]
    return []


def _url_literals_under_tests() -> set[str]:
    literals: set[str] = set()
    for path in sorted((ROOT / "tests").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith(("http://", "https://"))
                and len(node.value) < 2048
            ):
                literals.add(node.value)
    return literals


class Step0CorpusTests(unittest.TestCase):
    """Section 8.2: Step 0 stays the only configuration-time refusal (DC2)."""

    def test_step0_rows_accept_and_refuse_as_today(self):
        for row, value, accepted, _form, _rule, _code in ROWS:
            with self.subTest(row=row, value=repr(value)):
                if accepted:
                    endpoint = parse_upstream_dashboard_endpoint(value)
                    self.assertIsNotNone(endpoint)
                    errors = _validate_errors(value)
                    self.assertFalse(
                        any(UPSTREAM_CONFIG_ERROR in item for item in errors), errors
                    )
                else:
                    with self.assertRaises(ValueError) as caught:
                        parse_upstream_dashboard_endpoint(value)
                    self.assertEqual(str(caught.exception), STEP0_MESSAGES[row])
                    self.assertNotIn("synthetictoken123", str(caught.exception))
                    errors = _validate_errors(value)
                    self.assertTrue(
                        any(UPSTREAM_CONFIG_ERROR in item for item in errors), errors
                    )
                    for item in errors:
                        self.assertNotIn("synthetictoken123", item)

    def test_step0_corpus_url_literals_unchanged(self):
        literals = _url_literals_under_tests()
        self.assertGreater(len(literals), 100)
        accepted = 0
        for value in sorted(literals):
            try:
                endpoint = parse_upstream_dashboard_endpoint(value)
            except ValueError:
                endpoint = None
                parsed_ok = False
            else:
                parsed_ok = endpoint is not None
            if not parsed_ok:
                continue
            accepted += 1
            with self.subTest(value=value[:80]):
                # Classification is pure and never refuses an accepted value.
                form = classify_endpoint_form(endpoint.url)
                self.assertIn(form, {STRICT_FULL, LEGACY})
                if form == STRICT_FULL:
                    # Only this module's synthetic vectors are strict.
                    self.assertTrue(value.endswith("/catalog/full") or "?" in value)
        self.assertGreater(accepted, 50)

    def test_step0_corpus_has_no_strict_value_outside_hamcp319_tests(self):
        strict = []
        for path in sorted((ROOT / "tests").rglob("*.py")):
            if path.name.startswith("test_hamcp319_") or path.parent.name == "support":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value.startswith(("http://", "https://"))
                ):
                    try:
                        if parse_upstream_dashboard_endpoint(node.value) is None:
                            continue
                    except ValueError:
                        continue
                    if classify_endpoint_form(node.value.strip()) == STRICT_FULL:
                        strict.append(f"{path.name}:{node.lineno}")
        # No existing test configuration changes form (design section 3.2).
        self.assertEqual(strict, [])


class Step1GrammarTests(unittest.TestCase):
    """Section 8.2: each row gets its form and failing rule (DC1)."""

    def test_step1_rows_form_and_failing_rule(self):
        for row, value, accepted, form, rule, _code in ROWS:
            if not accepted:
                continue
            with self.subTest(row=row, value=repr(value)):
                self.assertEqual(endpoint_form_failing_rule(value), rule)
                self.assertEqual(classify_endpoint_form(value), form)

    def test_step1_extra_vectors(self):
        for value, form, rule in EXTRA_VECTORS:
            with self.subTest(value=repr(value)):
                self.assertEqual(endpoint_form_failing_rule(value), rule)
                self.assertEqual(classify_endpoint_form(value), form)

    def test_step1_never_raises_on_hostile_input(self):
        for value in ("", "http://[::1", "http://h:99999/x", "\x00", "%", "http://"):
            with self.subTest(value=repr(value)):
                self.assertEqual(classify_endpoint_form(value), LEGACY)

    def test_selector_refusal_code_per_legacy_row(self):
        for row, value, accepted, form, _rule, code in ROWS:
            if not accepted or form != LEGACY:
                continue
            with self.subTest(row=row, value=repr(value)):
                self.assertEqual(selector_refusal_code(value), code)


class SelectorAgreementUnitTests(unittest.TestCase):
    """Section 3.3 shared admission function, unit level."""

    def _bound(self, value: str):
        snapshot = EndpointSnapshot(
            url=value, form=classify_endpoint_form(value), generation=7
        )
        binding = NoRedirectBinding(snapshot)
        ha_mcp_no_redirect_client_factory(binding)
        return snapshot, binding

    def test_selector_agreement_admits_by_version_and_form(self):
        strict_snapshot, strict_binding = self._bound(f"{B}/catalog/full")
        legacy_snapshot, legacy_binding = self._bound(f"{B}/mcp")
        for version in ("9.1.0", "8.5.0", "8.6.0"):
            require_selector_agreement(version, strict_snapshot, strict_binding)
        for version in ("8.5.0", "8.6.0", "7.2.0"):
            require_selector_agreement(version, legacy_snapshot, legacy_binding)
        with self.assertRaises(UpstreamEndpointRefused) as caught:
            require_selector_agreement("9.1.0", legacy_snapshot, legacy_binding)
        self.assertEqual(caught.exception.category, MU)
        self.assertFalse(caught.exception.tool_call_attempted)
        self.assertNotIn("synthetictoken123", repr(caught.exception))
        self.assertNotIn("synthetictoken123", str(caught.exception))

    def test_selector_agreement_missing_binding_refused(self):
        snapshot, binding = self._bound(f"{B}/catalog/full")
        other_snapshot, other_binding = self._bound(f"{B}/catalog/full")
        unfactored = NoRedirectBinding(snapshot)
        for version in ("9.1.0", "8.5.0"):
            for candidate in (None, object(), unfactored, other_binding):
                with self.subTest(version=version, binding=type(candidate).__name__):
                    with self.assertRaises(UpstreamEndpointRefused) as caught:
                        require_selector_agreement(version, snapshot, candidate)
                    self.assertEqual(caught.exception.category, UPSTREAM_ENDPOINT_REJECTED)
            stale = NoRedirectBinding(snapshot)
            ha_mcp_no_redirect_client_factory(stale)
            stale.generation = snapshot.generation + 1
            with self.assertRaises(UpstreamEndpointRefused) as caught:
                require_selector_agreement(version, snapshot, stale)
            self.assertEqual(caught.exception.category, UPSTREAM_ENDPOINT_REJECTED)
        del other_snapshot

    def test_selector_agreement_latched_binding_refused(self):
        snapshot, binding = self._bound(f"{B}/catalog/full")
        binding.refuse_redirect()
        with self.assertRaises(UpstreamEndpointRefused) as caught:
            require_selector_agreement("9.1.0", snapshot, binding)
        self.assertEqual(caught.exception.category, UPSTREAM_REDIRECT_REFUSED)


def _loopback_suffix(value: str) -> tuple[str | None, str]:
    """Split a design row into (host override, value after the authority)."""

    for prefix, host in (
        ("http://ha-mcp.invalid:9583", None),
        ("http://HA-MCP.invalid:9583", "LOCALHOST"),
    ):
        if value.startswith(prefix):
            return host, value[len(prefix):]
    raise ValueError("row has no loopback form")


LOOPBACK_ROWS = tuple(
    (row, value, code)
    for row, value, accepted, form, _rule, code in ROWS
    if accepted and form == LEGACY and ":80/" not in value
)
SITES = ("gateway_isolated", "gateway_retained", "dashboard", "analysis")


async def _run_value(client: str, value: str, version: str):
    builder, kind, _requests, _full = CLIENTS[client]
    host, suffix = _loopback_suffix(value)
    peer = RedirectPeer(
        version=version,
        form="raw",
        raw_suffix=suffix,
        host=host,
        tool_names=DASHBOARD_TOOLS if kind != "read" else READ_TOOLS,
    )
    with peer.serve():
        transport = builder(peer.url)
        result = error = None
        try:
            request_type = "tools/call" if kind == "read" else "tools/list:first"
            result = await asyncio.wait_for(
                _drive(client, transport, kind, request_type), DEADLINE_SECONDS + 5
            )
        except Exception as exc:  # noqa: BLE001 - recorded outcome
            error = exc
        finally:
            closer = getattr(transport, "aclose", None)
            if callable(closer):
                try:
                    await closer()
                except Exception:  # noqa: BLE001
                    pass
    return SimpleNamespace(
        client=client,
        kind=kind,
        url=peer.url,
        result=result,
        error=error,
        counters=peer.counters,
    )


def _list_pages(counters) -> int:
    return counters.origin["tools/list:first"] + counters.origin["tools/list:cursor"]


class LegacyValueOn91Tests(unittest.TestCase):
    """Section 8.1 item 2: each legacy row on a 9.1.0 peer, every site."""

    def _run_all(self, version: str):
        async def scenario():
            outcomes = []
            for client in SITES:
                for row, value, code in LOOPBACK_ROWS:
                    outcome = await _run_value(client, value, version)
                    outcomes.append((row, value, code, outcome))
            return outcomes

        with capture_all_logs() as capture:
            outcomes = asyncio.run(scenario())
        return outcomes, capture.lines

    def test_91_legacy_form_refused_before_list(self):
        outcomes, logs = self._run_all("9.1.0")
        for row, value, code, cell in outcomes:
            label = f"{cell.client}/row{row}/{value!r}"
            with self.subTest(cell=label):
                # The configuration itself is still accepted (DC2).
                self.assertIsNotNone(parse_upstream_dashboard_endpoint(value))
                counters = cell.counters
                self.assertEqual(counters.destination_total(), 0)
                self.assertEqual(_list_pages(counters), 0)
                self.assertEqual(counters.origin["tools/call"], 0)
                self.assertEqual(counters.executions, 0)
                self.assertIsInstance(cell.error, DashboardTransportError)
                if "\t" in value:
                    # httpx refuses to build the request: a bounded transport
                    # failure with zero requests at all.
                    self.assertEqual(counters.origin_total(), 0)
                    self.assertNotIn(cell.error.category, {STRICT_FULL})
                else:
                    self.assertEqual(counters.origin["initialize"], 1)
                    self.assertEqual(cell.error.category, code)
                    self.assertFalse(cell.error.retryable)
                values = _sinks(cell.error, cell.result) + logs
                for sink in values:
                    self.assertNotIn(value, sink)
                    self.assertNotIn(cell.url, sink)
                    self.assertNotIn("synthetictoken123", sink)
                    self.assertNotIn("synthetic-upstream-secret", sink)

    def test_85_legacy_rows_admitted_on_same_transport(self):
        outcomes, _logs = self._run_all("8.5.0")
        for row, value, _code, cell in outcomes:
            label = f"{cell.client}/row{row}/{value!r}"
            with self.subTest(cell=label):
                counters = cell.counters
                self.assertEqual(counters.destination_total(), 0)
                if "\t" in value:
                    self.assertEqual(counters.origin_total(), 0)
                    self.assertIsInstance(cell.error, DashboardTransportError)
                    continue
                if cell.error is not None:
                    self.assertNotIn(
                        getattr(cell.error, "category", None), ENDPOINT_REFUSAL_CODES
                    )
                # Admitted: the catalog was listed past selector agreement.
                self.assertGreaterEqual(_list_pages(counters), 1)
                if cell.kind == "read":
                    self.assertIsNone(cell.error, repr(cell.error))
                    self.assertEqual(counters.executions, 1)
                if cell.kind == "dashboard":
                    self.assertIsNone(cell.error, repr(cell.error))

    def test_91_strict_rows_admitted(self):
        async def scenario():
            outcomes = []
            for client in SITES:
                outcomes.append(await _run_value(client, f"{B}/catalog/full", "9.1.0"))
                outcomes.append(
                    await _run_value(client, f"{B}/catalog/full?x=1", "9.1.0")
                )
            return outcomes

        for cell in asyncio.run(scenario()):
            with self.subTest(client=cell.client):
                self.assertEqual(cell.counters.destination_total(), 0)
                self.assertGreaterEqual(_list_pages(cell.counters), 1)
                if cell.error is not None:
                    self.assertNotIn(
                        getattr(cell.error, "category", None), ENDPOINT_REFUSAL_CODES
                    )
                if cell.kind in {"read", "dashboard"}:
                    self.assertIsNone(cell.error, repr(cell.error))


class _DriftingFactory:
    """Wrap the factory so a hook ahead of ours rewrites the request URL."""

    def __init__(self, real, mode: str):
        self.real = real
        self.mode = mode

    def __call__(self, binding, **kwargs):
        if self.mode == "sdk_default":
            # A session whose client carries no binding at all.
            return create_mcp_http_client
        inner = self.real(binding, **kwargs)

        def factory(headers=None, timeout=None, auth=None):
            client = inner(headers=headers, timeout=timeout, auth=auth)

            async def drift(request: httpx.Request) -> None:
                request.url = request.url.copy_with(path=request.url.path + "/drift")

            hooks = client.event_hooks
            hooks["request"].insert(0, drift)
            client.event_hooks = hooks
            return client

        return factory


class TransportBindingTests(unittest.TestCase):
    """Section 8.2: URL drift, missing binding and generation change."""

    def _run(self, mode: str, version: str):
        async def scenario():
            peer = RedirectPeer(version=version, form="strict_full", tool_names=READ_TOOLS)
            with peer.serve():
                transport = CLIENTS["gateway_isolated"][0](peer.url)
                error = None
                with patch.object(
                    upstream_read_module,
                    "ha_mcp_no_redirect_client_factory",
                    _DriftingFactory(ha_mcp_no_redirect_client_factory, mode),
                ):
                    try:
                        await transport.discover()
                    except Exception as exc:  # noqa: BLE001
                        error = exc
                return error, peer.counters

        return asyncio.run(scenario())

    def test_url_drift_refused_before_send(self):
        for version in ("8.5.0", "9.1.0"):
            with self.subTest(version=version):
                error, counters = self._run("drift", version)
                self.assertIsInstance(error, DashboardTransportError)
                self.assertEqual(error.category, UPSTREAM_ENDPOINT_REJECTED)
                self.assertEqual(counters.origin_total(), 0)
                self.assertEqual(counters.destination_total(), 0)

    def test_session_without_binding_refused_before_list(self):
        for version in ("8.5.0", "9.1.0"):
            with self.subTest(version=version):
                error, counters = self._run("sdk_default", version)
                self.assertIsInstance(error, DashboardTransportError)
                self.assertEqual(error.category, UPSTREAM_ENDPOINT_REJECTED)
                self.assertEqual(_list_pages(counters), 0)
                self.assertEqual(counters.origin["tools/call"], 0)
                self.assertEqual(counters.destination_total(), 0)

    def test_generation_change_closes_retained_worker(self):
        async def scenario():
            first = RedirectPeer(version="9.1.0", form="strict_full", tool_names=READ_TOOLS)
            second = RedirectPeer(version="9.1.0", form="strict_full", tool_names=READ_TOOLS)
            with first.serve(), second.serve():
                transport = McpReadGatewayTransport(
                    publish_endpoint_snapshot(first.url),
                    timeout_seconds=DEADLINE_SECONDS,
                    client_version="2.4.2-beta.7",
                    retain_session=True,
                )
                await transport.execute_read(
                    READ_TOOLS[0],
                    {},
                    timeout_seconds=DEADLINE_SECONDS,
                    catalog_validator=lambda _catalog: None,
                )
                worker = transport._worker
                self.assertIsNotNone(worker)
                self.assertFalse(worker.done())
                first_calls = first.counters.origin["tools/call"]
                # A new configuration generation is published.
                new_snapshot = publish_endpoint_snapshot(second.url)
                self.assertFalse(snapshot_is_current(transport._snapshot))
                with self.assertRaises(DashboardTransportError) as caught:
                    await transport.execute_read(
                        READ_TOOLS[0],
                        {},
                        timeout_seconds=DEADLINE_SECONDS,
                        catalog_validator=lambda _catalog: None,
                    )
                self.assertEqual(caught.exception.category, UPSTREAM_ENDPOINT_REJECTED)
                self.assertTrue(worker.done())
                # The stale snapshot never dispatched again, anywhere.
                self.assertEqual(first.counters.origin["tools/call"], first_calls)
                self.assertEqual(second.counters.origin_total(), 0)
                await transport.aclose()
                # The reconnect uses the new snapshot.
                fresh = McpReadGatewayTransport(
                    new_snapshot,
                    timeout_seconds=DEADLINE_SECONDS,
                    client_version="2.4.2-beta.7",
                    retain_session=True,
                )
                await fresh.execute_read(
                    READ_TOOLS[0],
                    {},
                    timeout_seconds=DEADLINE_SECONDS,
                    catalog_validator=lambda _catalog: None,
                )
                self.assertEqual(second.counters.origin["tools/call"], 1)
                self.assertEqual(first.counters.origin["tools/call"], first_calls)
                await fresh.aclose()
                for peer in (first, second):
                    self.assertEqual(peer.counters.destination_total(), 0)

        asyncio.run(scenario())

    def test_gateway_reconfigure_abandons_previous_transport(self):
        gateway = UpstreamReadGateway()
        gateway.configure(_settings(f"{B}/catalog/full"))
        previous = gateway._transport
        first_generation = previous._snapshot.generation
        calls = []
        previous.abandon = lambda: calls.append("abandon")
        gateway.configure(_settings(f"{B}/catalog/full?x=1"))
        self.assertIsNot(gateway._transport, previous)
        self.assertEqual(calls, ["abandon"])
        current = gateway._transport._snapshot
        self.assertGreater(current.generation, first_generation)
        self.assertTrue(snapshot_is_current(current))
        self.assertFalse(snapshot_is_current(previous._snapshot))


def _streamable_calls() -> list[tuple[Path, ast.Call, list[ast.AST]]]:
    """Every ``streamablehttp_client(`` call with its enclosing functions."""

    found: dict[int, tuple[Path, ast.Call, list[ast.AST]]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if not isinstance(node, ast.Call):
                    continue
                callee = node.func
                name = getattr(callee, "id", None) or getattr(callee, "attr", None)
                if name == "streamablehttp_client":
                    entry = found.setdefault(id(node), (path, node, []))
                    entry[2].append(function)
    return list(found.values())


def _is_factory_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None))
        == "ha_mcp_no_redirect_client_factory"
    )


class StreamableCallSiteTests(unittest.TestCase):
    """Section 8.2 AST check (supplementary to the behavior matrix)."""

    def test_every_streamable_call_passes_the_factory_unconditionally(self):
        calls = _streamable_calls()
        # Today exactly S-a, S-b, S-c and S-d (design section 3.3).
        self.assertEqual(len(calls), 4, [(p.name, c.lineno) for p, c, _f in calls])
        for path, call, functions in calls:
            with self.subTest(site=f"{path.name}:{call.lineno}"):
                keywords = {kw.arg: kw.value for kw in call.keywords}
                self.assertIn("httpx_client_factory", keywords)
                value = keywords["httpx_client_factory"]
                if _is_factory_call(value):
                    continue
                self.assertIsInstance(value, ast.Name)
                assignments = [
                    (function, node)
                    for function in functions
                    for node in function.body
                    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
                    and any(
                        isinstance(target, ast.Name) and target.id == value.id
                        for target in (
                            node.targets if isinstance(node, ast.Assign) else [node.target]
                        )
                    )
                ]
                # Unconditional: exactly one direct statement of an enclosing
                # function body (never under a branch) assigns the name, and
                # no other assignment anywhere rebinds it.
                self.assertEqual(len(assignments), 1)
                _function, assignment = assignments[0]
                self.assertTrue(_is_factory_call(assignment.value))
                rebinds = [
                    node
                    for function in functions
                    for node in ast.walk(function)
                    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr))
                    and value.id
                    in {
                        target.id
                        for target in (
                            node.targets
                            if isinstance(node, ast.Assign)
                            else [node.target]
                        )
                        if isinstance(target, ast.Name)
                    }
                ]
                self.assertEqual({id(node) for node in rebinds}, {id(assignment)})

    def test_ast_check_detects_a_site_without_the_factory(self):
        source = (
            "async def f(url):\n"
            "    async with streamablehttp_client(url) as streams:\n"
            "        return streams\n"
            "async def g(url, strict):\n"
            "    factory = ha_mcp_no_redirect_client_factory(b) if strict else None\n"
            "    async with streamablehttp_client(url, httpx_client_factory=factory):\n"
            "        pass\n"
        )
        tree = ast.parse(source)
        sites = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "streamablehttp_client"
        ]
        self.assertEqual(len(sites), 2)
        self.assertNotIn("httpx_client_factory", {kw.arg for kw in sites[0].keywords})
        conditional = tree.body[1].body[0]
        self.assertFalse(_is_factory_call(conditional.value))


if __name__ == "__main__":
    unittest.main()
