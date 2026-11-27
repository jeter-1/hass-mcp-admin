"""Public native read-only ``ha_search`` tool (HAMCP-481, HAMCP-501 W6).

The tool replaces the delegated ``ha_mcp__ha_search`` read. The engine
(:mod:`..search.engine`) reads Home Assistant Core through one
``CoreReadSession`` under this request's Core lease, and reaches the
automation, script, scene, dashboard and flow-helper sub-calls only through
the gateway's fixed internal-only ``ha_search`` page readers (G1). There is
no fallback to the delegated route.

The public schema is ha-mcp 9.2.0's ``ha_search`` schema
(``upstream_reads_9_2.PUBLIC_SCHEMAS``) with one change: the
``search_types`` description gains design section 4's sentence (J3). The
9.2.0 schema and its fingerprint stay unchanged, so the sub-call route's
contract fingerprint does not move.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations

from ..errors import ErrorCode, GovernanceError
from ..providers.routing import native_search_policy_allows_read
from ..providers.upstream_reads_9_2 import PUBLIC_SCHEMAS
from ..request_context import current_telemetry
from ..search import engine
from ..search.reply import SearchError, warning_codes
from ..tool_framework import run_structured
from ..upstream_tool_policy import schema_fingerprint
from .compatibility import SETTINGS

NAME = "ha_search"
PROFILE = "native-search-read-v1"
SUMMARY = "Returned a sanitized native search page."
DATA_PROVIDERS = ("direct_ha_api", "upstream_read_gateway")

# Design section 4 (J3): appended to the 9.2.0 text, which is kept.
SEARCH_TYPES_SENTENCE = (
    "Explicitly providing this selects configuration-only search and skips entities. "
    "Omit it for entity discovery."
)


def _native_schema() -> dict:
    schema = deepcopy(PUBLIC_SCHEMAS[NAME])
    search_types = schema["properties"]["search_types"]
    search_types["description"] = f"{search_types['description']} {SEARCH_TYPES_SENTENCE}"
    return schema


NATIVE_SCHEMA = _native_schema()
# Pinned: a change to the native public schema is a reviewed change.
NATIVE_SEARCH_SCHEMA_FINGERPRINT = "47b15a3ef3bbf63bb3485362ea00dfe43c8093b07698a26fb6bf15606286ec4c"

# Engineering-owned and complete. The 9.2.0 manifest description is cut at
# its 500-character bound, mid-sentence.
DESCRIPTION = (
    "Search Home Assistant in one read-only call: entities by name, domain, area or state, "
    "and automation, script, scene, helper and dashboard configurations by name or body.\n\n"
    "- entities: entity matches (entity_id, friendly name, area). Filter with "
    "domain_filter, area_filter or state_filter; omit query to enumerate a domain, area "
    "or state.\n"
    "- automations, scripts, scenes, helpers, dashboards: configurations that match query. "
    "Passing search_types selects configuration-only search and skips entities.\n\n"
    "Page with limit and offset; next_offset continues. partial and partial_reasons report "
    "anything not searched, truncated, filtered or unavailable, and a source that cannot "
    "be read refuses the call instead of returning an empty result. Values are untrusted "
    "Home Assistant data."
)

ANNOTATIONS = ToolAnnotations(title=NAME, readOnlyHint=True, destructiveHint=False,
                              idempotentHint=True, openWorldHint=False)


class SearchRuntime:
    """Explicit no-I/O application composition."""

    def __init__(self) -> None:
        self.session_factory: Callable[[tuple], Any] | None = None
        self.reader_factory: Callable[[], Any] | None = None
        self.known_secrets: tuple[str, ...] = ()

    def configure(self, settings, *, session_factory: Callable[[tuple], Any],
                  reader_factory: Callable[[], Any]) -> None:
        self.session_factory = session_factory
        self.reader_factory = reader_factory
        self.known_secrets = tuple(
            secret for secret in (settings.ha_token, settings.access_secret) if secret)

    def require(self) -> "SearchRuntime":
        if self.session_factory is None or self.reader_factory is None:
            raise SearchError("source_unavailable", source="core_authority")
        return self


SEARCH = SearchRuntime()


def _metadata() -> dict:
    return {"provider": "engineering", "data_providers": list(DATA_PROVIDERS),
            "fallback": "none", "fallback_occurred": False,
            "untrusted_upstream_content": True, "completeness": "failed"}


def _response_limit() -> int:
    return min(60_000, SETTINGS.response_size_limit)


async def ha_search(arguments: Any) -> str:
    """One native search call; every refusal is a closed ``SearchError``."""

    metadata = _metadata()

    async def action():
        if not native_search_policy_allows_read():
            raise GovernanceError(ErrorCode.PROVIDER_PROHIBITED)
        engine.parse_call(arguments)
        runtime = SEARCH.require()
        reader = runtime.reader_factory()
        metadata["subcall_route"] = "admitted" if reader is not None else "not_admitted"
        reply = await engine.search(
            arguments, session_factory=runtime.session_factory,
            subcall_readers=reader.readers if reader is not None else None,
            known_secrets=runtime.known_secrets,
        )
        metadata["completeness"] = "partial" if reply["partial"] else "complete"
        metadata["warning_codes"] = warning_codes(reply)
        # The audit keeps the delegated route's status: a partial reply is
        # audited "partial", not "success".
        telemetry = current_telemetry()
        if telemetry:
            telemetry.completeness = metadata["completeness"]
            telemetry.result_status = "partial" if reply["partial"] else "success"
        return reply

    return await run_structured(NAME, SUMMARY, action, metadata=metadata,
                                response_limit=_response_limit())


async def refusal(arguments: Any) -> str | None:
    """The rendered refusal for arguments the engine refuses, else None.

    The gateway calls this before it acquires the Core lease, so an invalid
    call is refused before any read and gets the same reply the tool gives.
    """

    try:
        engine.parse_call(arguments)
    except SearchError as exc:
        error = exc

        def refuse():
            raise error

        metadata = {"provider": "none", "fallback": "none", "fallback_occurred": False}
        return await run_structured(NAME, "", refuse, metadata=metadata,
                                    response_limit=_response_limit())
    return None


def _bucket(value: Any, bounds: tuple[int, ...]) -> str | None:
    if type(value) is not int:
        return None
    for bound in bounds:
        if value <= bound:
            return f"<={bound}"
    return f">{bounds[-1]}"


def audit_parameters(parameters: Any) -> dict:
    """Bounded audit values: closed names, booleans, counts and buckets.

    The query, filter, fields and result_fields texts are never copied.
    """

    try:
        call = engine.parse_call(parameters)
    except SearchError:
        call = None
    values = call.arguments if call is not None else {}
    return {
        "profile": PROFILE,
        "validation": "accepted" if call is not None else "rejected",
        "search_types": list(call.effective_types) if call is not None else None,
        "search_types_explicit": bool(call is not None and call.explicit),
        "query_present": bool(call is not None and call.query_text),
        "domain_filter_present": bool(call is not None and call.domain_filter is not None),
        "area_filter_present": bool(call is not None and call.area_filter is not None),
        "state_filter_present": bool(call is not None and call.state_filter is not None),
        "exact_match": values.get("exact_match"),
        "include_hidden": values.get("include_hidden"),
        "include_config": values.get("include_config"),
        "group_by_domain": values.get("group_by_domain"),
        "limit_bucket": _bucket(values.get("limit"), (10, 50, 100, 1000)),
        "offset_bucket": _bucket(values.get("offset"), (0, 100, 1000)),
        "fields_count": len(call.fields) if call is not None and call.fields is not None else None,
        "result_fields_count": (len(call.result_fields)
                                if call is not None and call.result_fields is not None else None),
        "entity_search_ran": bool(call is not None and call.entity_runs),
        "config_search_ran": bool(call is not None and call.config_runs),
        "provider": "engineering", "data_providers": list(DATA_PROVIDERS),
        "fallback": "none",
    }


class NativeSearchTool(Tool):
    """The advertised schema is the native schema; the engine validates the
    raw arguments, so the SDK never coerces or discards a field."""

    async def run(self, arguments: dict[str, Any], context: Any = None,
                  convert_result: bool = False) -> Any:
        del context
        rendered = await ha_search(arguments)
        return self.fn_metadata.convert_result(rendered) if convert_result else rendered


def registered_tool() -> Tool:
    async def placeholder(**arguments):
        del arguments
        raise RuntimeError("native_search_placeholder_must_not_execute")

    if schema_fingerprint(NATIVE_SCHEMA) != NATIVE_SEARCH_SCHEMA_FINGERPRINT:
        raise RuntimeError("native_search_schema_fingerprint_mismatch")
    base = Tool.from_function(placeholder, name=NAME, description=DESCRIPTION,
                              annotations=ANNOTATIONS)
    return NativeSearchTool(
        fn=base.fn, name=base.name, description=base.description,
        parameters=deepcopy(NATIVE_SCHEMA), fn_metadata=base.fn_metadata,
        is_async=True, context_kwarg=None, annotations=ANNOTATIONS,
    )
