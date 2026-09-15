"""Bounded received-authority policy. Headers never establish caller identity."""
from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import re

MAX_POLICY_ENTRIES = 32
MAX_VALUE_BYTES = 512
MAX_POLICY_BYTES = 32_768
MAX_HEADER_ENTRIES = 256
MAX_HEADER_NAME_BYTES = 256
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)
_NUMERIC_HOST = re.compile(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", re.ASCII)
_HEADER_NAME = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def default_hosts(port: int) -> tuple[str, ...]:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid_inbound_listener_port")
    return (f"127.0.0.1:{port}", f"[::1]:{port}", f"localhost:{port}")


def _text(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_VALUE_BYTES:
        raise ValueError("invalid_inbound_value")
    if not value.isascii() or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
        raise ValueError("invalid_inbound_value")
    return value


def _authority(value: str) -> tuple[str, int | None]:
    value = _text(value).lower()
    if any(c in value for c in "/\\@?#%,*"):
        raise ValueError("invalid_inbound_authority")
    port_text = None
    if value.startswith("["):
        host, closing, suffix = value[1:].partition("]")
        if not closing or (suffix and not suffix.startswith(":")):
            raise ValueError("invalid_inbound_authority")
        try:
            host = str(ipaddress.IPv6Address(host))
        except ValueError:
            raise ValueError("invalid_inbound_authority") from None
        if suffix:
            port_text = suffix[1:]
    else:
        if value.count(":") > 1:
            raise ValueError("invalid_inbound_authority")
        host, colon, suffix = value.partition(":")
        if colon:
            port_text = suffix
        try:
            host = str(ipaddress.IPv4Address(host))
        except ValueError:
            host = host.removesuffix(".")
            if (not host or len(host) > 253 or _NUMERIC_HOST.fullmatch(host)
                    or any(not _LABEL.fullmatch(label) for label in host.split("."))):
                raise ValueError("invalid_inbound_authority") from None
    port = None
    if port_text is not None:
        if not port_text or len(port_text) > 5 or not port_text.isdecimal():
            raise ValueError("invalid_inbound_port")
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise ValueError("invalid_inbound_port")
    return host, port


def _origin(value: str) -> tuple[str, str, int]:
    value = _text(value)
    scheme, delimiter, rest = value.partition("://")
    scheme = scheme.lower()
    if not delimiter or scheme not in {"http", "https"}:
        raise ValueError("invalid_inbound_origin")
    host, port = _authority(rest)
    return scheme, host, port if port is not None else (443 if scheme == "https" else 80)


def option_list(options: dict, name: str, default):
    """Freeze a JSON list without coercing malformed data or exposing its value."""
    if name not in options:
        return default
    values = options[name]
    if not isinstance(values, list) or len(values) > MAX_POLICY_ENTRIES:
        raise ValueError("invalid_inbound_policy_list")
    # Validate types/bytes here, before retaining any mutable nested objects.
    for value in values:
        _text(value)
    return tuple(values)


@dataclass(frozen=True)
class Denial:
    status: int
    category: str

    @property
    def body(self) -> bytes:
        return {400: b"invalid request headers", 403: b"origin refused",
                421: b"host refused", 431: b"request headers too large"}[self.status]


def bounded_request_id(headers) -> str | None:
    if not isinstance(headers, (list, tuple)) or len(headers) > MAX_HEADER_ENTRIES:
        return None
    values = []
    for entry in headers:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            return None
        name, value = entry
        if not isinstance(name, bytes) or len(name) > MAX_HEADER_NAME_BYTES:
            return None
        if name.lower() == b"x-request-id":
            if not isinstance(value, bytes) or not 8 <= len(value) <= 128:
                return None
            values.append(value)
    if len(values) != 1:
        return None
    # begin_request owns the existing request-ID character contract.
    return values[0].decode("ascii", errors="replace")


@dataclass(frozen=True)
class InboundPolicy:
    hosts: frozenset = field(repr=False)
    origins: frozenset = field(repr=False)
    source: str

    def summary(self) -> dict:
        return {"enabled": True, "configuration_valid": True, "source": self.source,
                "host_count": len(self.hosts), "origin_count": len(self.origins)}

    def check(self, headers) -> Denial | None:
        if not isinstance(headers, (list, tuple)):
            return Denial(400, "malformed_headers")
        if len(headers) > MAX_HEADER_ENTRIES:
            return Denial(431, "header_count_exceeded")
        hosts, origins = [], []
        for entry in headers:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                return Denial(400, "malformed_headers")
            name, value = entry
            if (not isinstance(name, bytes) or not isinstance(value, bytes)
                    or not 1 <= len(name) <= MAX_HEADER_NAME_BYTES
                    or not _HEADER_NAME.fullmatch(name)):
                return Denial(400, "malformed_headers")
            if name.lower() == b"host":
                hosts.append(value)
            elif name.lower() == b"origin":
                origins.append(value)
        # A present invalid Origin gets the protocol-required 403, even if Host
        # is also invalid. A parser/envelope refusal can precede this decision.
        if origins:
            if len(origins) != 1:
                return Denial(403, "duplicate_origin")
            try:
                if len(origins[0]) > MAX_VALUE_BYTES:
                    raise ValueError("origin_size")
                origin = _origin(origins[0].decode("ascii"))
            except (ValueError, UnicodeError):
                return Denial(403, "invalid_origin")
            if origin not in self.origins:
                return Denial(403, "origin_not_allowed")
        if len(hosts) != 1:
            return Denial(400, "missing_or_duplicate_host")
        try:
            if len(hosts[0]) > MAX_VALUE_BYTES:
                raise ValueError("host_size")
            host = _authority(hosts[0].decode("ascii"))
        except (ValueError, UnicodeError):
            return Denial(400, "invalid_host")
        if host not in self.hosts:
            return Denial(421, "host_not_allowed")
        return None


def compile_policy(hosts, origins, port: int) -> InboundPolicy:
    source = "default_loopback" if hosts is None else "configured_hosts"
    hosts = default_hosts(port) if hosts is None else hosts
    if not isinstance(hosts, tuple) or not hosts or not isinstance(origins, tuple):
        raise ValueError("invalid_inbound_policy_list")
    if len(hosts) > MAX_POLICY_ENTRIES or len(origins) > MAX_POLICY_ENTRIES:
        raise ValueError("inbound_policy_count_exceeded")
    total = sum(len(_text(value)) for value in (*hosts, *origins))
    if total > MAX_POLICY_BYTES:
        raise ValueError("inbound_policy_bytes_exceeded")
    try:
        parsed_hosts = frozenset(_authority(value) for value in hosts)
        parsed_origins = frozenset(_origin(value) for value in origins)
    except ValueError:
        raise ValueError("invalid_inbound_policy_value") from None
    if len(parsed_hosts) != len(hosts) or len(parsed_origins) != len(origins):
        raise ValueError("duplicate_inbound_policy_value")
    return InboundPolicy(parsed_hosts, parsed_origins, source)
