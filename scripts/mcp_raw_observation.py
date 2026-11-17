"""Raw MCP streamable-HTTP observations of a disposable ha-mcp peer.

HAMCP-371 observations P1 (the whole ``initialize`` result) and P2 (every
``tools/list`` page in full).  Standard library only, so the same file runs
inside a lane container and under the offline tests.

The observer records what the peer sent: status, reason, every response header
and the body text exactly as received, next to the parsed JSON-RPC messages.
Nothing is normalized, filtered or redacted here; the record is a capture, not
a review, and its contents are untrusted data.

Only a loopback endpoint is accepted.  A lane starts the peer and this
observer in one network namespace that has no interface except loopback, so
the peer cannot be anything but the disposable container the lane started.
"""

from __future__ import annotations

import argparse
import http.client
import json
from pathlib import Path
import sys
from typing import Any, Callable
from urllib.parse import urlsplit

OBSERVATION_FORMAT_VERSION = 1
# The protocol version Engineering's own upstream client requests
# (ha_mcp_engineering/mcp_sdk_compatibility.py, REVIEWED_UPSTREAM_PROTOCOL_VERSION).
REQUESTED_PROTOCOL_VERSION = "2025-03-26"
CLIENT_INFO = {"name": "hass-mcp-engineering-upstream-review", "version": "1"}
LOOPBACK_HOSTS = frozenset({"127.0.0.1"})
ACCEPT = "application/json, text/event-stream"
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_LINE_BYTES = MAX_BODY_BYTES
MAX_CATALOG_PAGES = 16
MAX_CATALOG_TOOLS = 512
TIMEOUT_SECONDS = 60


class ObservationError(Exception):
    """The peer could not be observed; the message is a fixed token."""


def parse_endpoint(endpoint: str) -> tuple[str, int, str]:
    """Return host, port and path of an exact loopback HTTP endpoint."""

    parts = urlsplit(endpoint)
    try:
        port = parts.port
    except ValueError:
        raise ObservationError("endpoint_port_invalid") from None
    if (
        parts.scheme != "http"
        or parts.hostname not in LOOPBACK_HOSTS
        or port is None
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or not parts.path.startswith("/")
    ):
        raise ObservationError("endpoint_not_loopback_http")
    return parts.hostname, port, parts.path


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ObservationError("response_json_duplicate_key")
        result[key] = value
    return result


def _decode_message(text: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_strict_object)
    except (ValueError, RecursionError):
        raise ObservationError("response_json_invalid") from None


def _matches(message: Any, request_id: Any) -> bool:
    return (
        isinstance(message, dict)
        and "id" in message
        and message["id"] == request_id
        and ("result" in message or "error" in message)
    )


def _read_event_stream(response: Any, request_id: Any) -> tuple[bytes, list[Any]]:
    """Read server-sent events until the response for ``request_id`` arrives."""

    raw = bytearray()
    messages: list[Any] = []
    data: list[str] = []
    while True:
        line = response.readline(MAX_LINE_BYTES + 1)
        if len(line) > MAX_LINE_BYTES or len(raw) + len(line) > MAX_BODY_BYTES:
            raise ObservationError("response_body_exceeds_bound")
        raw.extend(line)
        text = line.decode("utf-8", errors="strict").rstrip("\r\n") if line else ""
        if line and text.startswith("data:"):
            data.append(text[5:].removeprefix(" "))
            continue
        if line and text:
            # event:, id:, retry: and comment lines stay in the raw body only.
            continue
        if data:
            message = _decode_message("\n".join(data))
            data = []
            messages.append(message)
            if _matches(message, request_id):
                return bytes(raw), messages
        if not line:
            return bytes(raw), messages


def exchange(
    endpoint: str,
    payload: dict[str, Any] | None,
    *,
    method: str = "POST",
    session_id: str | None = None,
    protocol_version: str | None = None,
    connect: Callable[..., Any] = http.client.HTTPConnection,
) -> dict[str, Any]:
    """Send one JSON-RPC message (or a bare DELETE) and record the reply raw."""

    host, port, path = parse_endpoint(endpoint)
    headers = {"Accept": ACCEPT}
    body = None
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if session_id is not None:
        headers["Mcp-Session-Id"] = session_id
    if protocol_version is not None:
        headers["Mcp-Protocol-Version"] = protocol_version
    request_id = payload.get("id") if isinstance(payload, dict) else None
    connection = connect(host, port, timeout=TIMEOUT_SECONDS)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        response_headers = [[name, value] for name, value in response.getheaders()]
        content_type = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
        if content_type == "text/event-stream" and request_id is not None:
            raw, messages = _read_event_stream(response, request_id)
        else:
            raw = response.read(MAX_BODY_BYTES + 1)
            if len(raw) > MAX_BODY_BYTES:
                raise ObservationError("response_body_exceeds_bound")
            messages = []
            if content_type == "application/json" and raw:
                messages = [_decode_message(raw.decode("utf-8", errors="strict"))]
        status, reason = response.status, response.reason
        body_text = raw.decode("utf-8", errors="strict")
    except (OSError, http.client.HTTPException, UnicodeDecodeError):
        raise ObservationError("peer_exchange_failed") from None
    finally:
        connection.close()
    matching = [message for message in messages if _matches(message, request_id)]
    return {
        "request": {
            "method": method,
            "path": path,
            # The session identifier is a per-run value the peer issued; its
            # presence is recorded, the value stays in the response headers.
            "headers": sorted(headers),
            "json": payload,
        },
        "status": status,
        "reason": reason,
        "response_headers": response_headers,
        "content_type": content_type,
        "body_bytes": len(raw),
        "body_text": body_text,
        "messages": messages,
        "response": matching[0] if request_id is not None and len(matching) == 1 else None,
    }


def _result(record: dict[str, Any], failure: str) -> dict[str, Any]:
    response = record.get("response")
    if (
        record.get("status") != 200
        or not isinstance(response, dict)
        or not isinstance(response.get("result"), dict)
    ):
        raise ObservationError(failure)
    return response["result"]


def _header(record: dict[str, Any], name: str) -> str | None:
    values = [value for key, value in record["response_headers"] if key.lower() == name]
    return values[0] if len(values) == 1 else None


def observe(
    endpoint: str,
    *,
    connect: Callable[..., Any] = http.client.HTTPConnection,
) -> dict[str, Any]:
    """Initialize one session and read the whole tool catalog, raw."""

    initialize = exchange(
        endpoint,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": REQUESTED_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": dict(CLIENT_INFO),
            },
        },
        connect=connect,
    )
    initialized = _result(initialize, "initialize_failed")
    negotiated = initialized.get("protocolVersion")
    if not isinstance(negotiated, str):
        raise ObservationError("initialize_protocol_version_missing")
    session_id = _header(initialize, "mcp-session-id")
    notification = exchange(
        endpoint,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        session_id=session_id,
        protocol_version=negotiated,
        connect=connect,
    )
    if notification["status"] not in {200, 202, 204}:
        raise ObservationError("initialized_notification_refused")
    pages: list[dict[str, Any]] = []
    names: list[str] = []
    cursor: str | None = None
    seen: set[str] = set()
    for index in range(MAX_CATALOG_PAGES):
        params = {} if cursor is None else {"cursor": cursor}
        page = exchange(
            endpoint,
            {"jsonrpc": "2.0", "id": index + 2, "method": "tools/list", "params": params},
            session_id=session_id,
            protocol_version=negotiated,
            connect=connect,
        )
        pages.append(page)
        listed = _result(page, "tools_list_failed")
        tools = listed.get("tools")
        if not isinstance(tools, list) or not all(
            isinstance(tool, dict) and isinstance(tool.get("name"), str) for tool in tools
        ):
            raise ObservationError("tools_list_malformed")
        names.extend(tool["name"] for tool in tools)
        if len(names) > MAX_CATALOG_TOOLS:
            raise ObservationError("catalog_exceeds_bound")
        cursor = listed.get("nextCursor")
        if cursor is None or cursor == "":
            break
        if not isinstance(cursor, str) or cursor in seen:
            raise ObservationError("catalog_cursor_invalid")
        seen.add(cursor)
    else:
        raise ObservationError("catalog_pagination_exceeds_bound")
    if len(names) != len(set(names)):
        raise ObservationError("catalog_duplicate_tool_name")
    closing = None
    if session_id is not None:
        closing = exchange(
            endpoint,
            None,
            method="DELETE",
            session_id=session_id,
            protocol_version=negotiated,
            connect=connect,
        )
    return {
        "observation_format_version": OBSERVATION_FORMAT_VERSION,
        "endpoint": endpoint,
        "requested_protocol_version": REQUESTED_PROTOCOL_VERSION,
        "negotiated_protocol_version": negotiated,
        "session_header_present": session_id is not None,
        "initialize": initialize,
        "initialized_notification": notification,
        "tools_list_pages": pages,
        "tool_count": len(names),
        "tool_names_in_wire_order": names,
        "session_delete": closing,
    }


def serialize(value: Any) -> bytes:
    return (json.dumps(value, indent=1, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        observation = observe(args.endpoint)
    except ObservationError as exc:
        print(json.dumps({"result": "FAILED", "reason": str(exc)}))
        return 1
    args.output.write_bytes(serialize(observation))
    print(json.dumps({"result": "OBSERVED", "tool_count": observation["tool_count"],
                      "pages": len(observation["tools_list_pages"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
