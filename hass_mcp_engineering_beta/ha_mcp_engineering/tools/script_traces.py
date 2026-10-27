"""Public sanitized script trace reads with a non-reflecting input boundary."""

from typing import Annotated

from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations
from pydantic import Field

from ..script_traces import SCRIPT_TRACES, ScriptTraceError, validate_run_id, validate_script_id
from ..tool_framework import run_structured
from .compatibility import SETTINGS

_READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
)
_METADATA = {"provider": "engineering", "data_providers": ["direct_ha_api"], "fallback_occurred": False}
_PARTIAL = ("Trace content is partial: Engineering withheld credential-like or unverifiable text, "
            "shortened long strings, or failed closed on a field; see sanitization.")


async def _respond(operation, summary, call):
    metadata = dict(_METADATA)
    warnings = []

    async def action():
        value = await call(SCRIPT_TRACES.require())
        metadata["completeness"] = value["completeness"]
        if value["completeness"] != "complete":
            warnings.append(_PARTIAL)
        return value

    return await run_structured(operation, summary, action, warnings=warnings, metadata=metadata,
                                response_limit=SETTINGS.response_size_limit)


async def list_script_traces(
    script_id: Annotated[str, Field(min_length=1, max_length=128)],
) -> str:
    """List recent run traces of one script. Read-only.

    ``script_id`` is the script's configuration key (``script.<key>`` without
    the ``script.`` prefix). Returns sanitized, normalized trace headers
    (run_id, start/finish timestamps, state, last step, error), newest
    first. Never lists every script's traces, never reads trace contexts,
    and never uses the trace debugger.
    """

    return await _respond("list_script_traces", "Returned sanitized script trace headers.",
                          lambda service: service.list_traces(script_id))


async def get_script_trace(
    script_id: Annotated[str, Field(min_length=1, max_length=128)],
    run_id: Annotated[str, Field(min_length=1, max_length=64)],
) -> str:
    """Return one script run's step-by-step trace, sanitized. Read-only.

    ``script_id`` is the script's configuration key and ``run_id`` comes
    from list_script_traces. Credential-like, known-secret and unverifiable
    text is withheld whole and strings over 2,000 characters are shortened;
    such a trace is marked partial and names the withheld categories. A trace Home Assistant no longer holds is reported as
    not found; there is no fallback.
    """

    return await _respond("get_script_trace", "Returned one sanitized script trace.",
                          lambda service: service.get_trace(script_id, run_id))


def _validate(name: str, arguments) -> None:
    allowed = {"list_script_traces": {"script_id"},
               "get_script_trace": {"script_id", "run_id"}}[name]
    if not isinstance(arguments, dict) or set(arguments) != allowed:
        raise ScriptTraceError("invalid_arguments")
    validate_script_id(arguments["script_id"])
    if "run_id" in allowed:
        validate_run_id(arguments["run_id"])


class ScriptTraceTool(Tool):
    async def run(self, arguments, context=None, convert_result=False):
        try:
            _validate(self.name, arguments)
        except ScriptTraceError:
            # The refused value is never reflected.
            async def invalid():
                raise ScriptTraceError("invalid_arguments")
            rendered = await run_structured(self.name, "", invalid,
                metadata={"provider": "none", "fallback_occurred": False},
                response_limit=SETTINGS.response_size_limit)
            return self.fn_metadata.convert_result(rendered) if convert_result else rendered
        return await super().run(arguments, context, convert_result)


def _registered(function):
    tool = ScriptTraceTool.from_function(function, annotations=_READ_ANNOTATIONS)
    tool.parameters["additionalProperties"] = False
    return tool


def registered_list_tool():
    return _registered(list_script_traces)


def registered_get_tool():
    return _registered(get_script_trace)
