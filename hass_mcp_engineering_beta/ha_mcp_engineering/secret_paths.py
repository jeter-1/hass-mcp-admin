"""HAMCP-288: the configured ha-mcp secret path is never returned in any output.

The ``upstream_dashboard_mcp_url`` option (schema type ``password``) holds the
ha-mcp app's private URL. Its secret path component is a bearer credential:
anyone who can reach the app's port with it can use the app's Home Assistant
authority. Per-provider sanitization already replaces the exact derived values
inside that provider's own outputs; this module makes the secret path a
process-wide redaction that every Engineering output applies, in every form
the path can plausibly take on the way to that output:

* the exact text, alone or inside a full URL (8.5.0 ``/<secret>/mcp`` or the
  9.1 endpoint suffixes such as ``/<secret>/catalog/full``);
* URL percent-encoding of any character, with upper-, lower- or mixed-case
  hex digits, encoded any number of times (``%25`` repeated before the
  escape), which covers the encoded ``/`` forms ``%2F``, ``%2f``, ``%252F``;
* JSON escapes: ``\\uXXXX`` (any case) and ``\\/``, ``\\"``, ``\\\\`` nested
  to any depth;
* a fragment cut at a string or line boundary (a wrapped or truncated log
  line, a bounded upstream error), when at least ``MIN_BOUNDARY`` characters
  of a fragment remain at that edge.

Only the configured value is known: a value Engineering was never given (a
path the app generated after rotation, before the option is updated) cannot
be recognized, so a log source that prints the app's own path is withheld
instead (``is_withheld_addon_slug``).

The module has no Engineering imports, so every sanitizer, renderer and sink
can use it without an import cycle. It never logs, prints or returns a
fragment; ``fragment_count`` is the only diagnostic.
"""

from __future__ import annotations

import io
import re
import sys
import threading
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qsl, unquote, urlsplit

# Path segments that name an endpoint, never a credential: the 8.5.0
# streamable route (``mcp``), the legacy SSE route and the 9.1 endpoint
# selectors (``/catalog/full``, ``/catalog/search``, ``/readonly``).
GENERIC_SEGMENTS = frozenset({"mcp", "sse", "catalog", "full", "search", "readonly"})
# The parser's own credential-segment bound (configuration.py).
MIN_FRAGMENT = 8
# A cut fragment is redacted when at least this many of its characters remain
# at a string or line edge; fewer than this many may remain visible.
MIN_BOUNDARY = 8
MARKER = "[REDACTED:token]"
# What may surround a cut fragment at an edge and still count as that edge:
# any run of characters that are not word characters or ``-`` (whitespace,
# quotes, brackets, punctuation, an ellipsis), or a truncation mark.
_LEAD = r"""(?:[^\w\-]){0,16}"""
_TRAIL = r"""(?:[^\w\-]|<truncated>|\[truncated\]){0,16}"""
# ha-mcp's generated paths start with this public prefix; a cut that leaves
# only the prefix visible reveals nothing and is not a cut fragment.
_PUBLIC_PREFIX = "private_"
_DECORATION_WINDOW = 16 * len("[truncated]")
# The same-length byte mask of the response guard keeps Content-Length valid.
MASK_BYTE = b"*"
# Slugs whose logs are the ha-mcp app's own: the app prints its secret path
# at startup, so its log source is withheld (HAMCP-288 item 2).
_HA_MCP_SLUG = re.compile(r"(?:\A|_)ha_mcp(?:_|\Z)")
WITHHELD_SOURCE_REASON = "upstream_secret_path_source_withheld"
WITHHELD_SOURCE_DETAIL = (
    "this source is the ha-mcp app's own log, which prints the app's private MCP path; "
    "Engineering cannot recognize a path it was not configured with (for example after "
    "rotation) or one split or cut across log lines, so the source is withheld whole"
)


@dataclass(frozen=True)
class _State:
    fragments: tuple[str, ...] = ()
    text: re.Pattern[str] | None = None
    data: re.Pattern[bytes] | None = None
    head: re.Pattern[str] | None = None
    tail: re.Pattern[str] | None = None
    tail_window: int = 0
    generation: int = 0
    endpoint_slug: str | None = None


_LOCK = threading.Lock()
_STATE = _State()


def _hex(value: int) -> str:
    return "".join(
        f"[{digit.lower()}{digit.upper()}]" if digit.isalpha() else digit
        for digit in f"{value:02x}"
    )


def _unicode_escape(code: int) -> str:
    return r"\\u" + "".join(
        f"[{digit.lower()}{digit.upper()}]" if digit.isalpha() else digit
        for digit in f"{code:04x}"
    )


def _char(char: str) -> str:
    """One character in every encoding this module recognizes."""

    forms = [re.escape(char)]
    encoded = char.encode("utf-8")
    forms.append("".join(f"%(?:25)*{_hex(byte)}" for byte in encoded))
    code = ord(char)
    if code <= 0xFFFF:
        forms.append(_unicode_escape(code))
    else:
        code -= 0x10000
        forms.append(_unicode_escape(0xD800 + (code >> 10)) + _unicode_escape(0xDC00 + (code & 0x3FF)))
    if char in '/"':
        # JSON escaping, nested any number of times.
        forms.append(r"\\+" + re.escape(char))
    elif char == "\\":
        forms.append(r"\\{2,}")
    return "(?:" + "|".join(forms) + ")"


def _sequence(text: str) -> str:
    return "".join(_char(char) for char in text)


def derive_fragments(value: str | None) -> tuple[str, ...]:
    """The secret parts of one configured upstream URL, longest first.

    The decoded path with its trailing endpoint selectors removed (so a
    custom multi-segment path is redacted whole), each non-generic path
    segment of at least ``MIN_FRAGMENT`` characters, the userinfo password
    and each query value of that length. The hostname, port and scheme are
    not secret and are kept, so ordinary text naming the host survives.
    A value that cannot be parsed is itself the fragment (fail closed).
    """

    candidate = (value or "").strip()
    if not candidate:
        return ()
    try:
        parsed = urlsplit(candidate)
        password = parsed.password
    except ValueError:
        return (candidate,) if len(candidate) >= MIN_FRAGMENT else ()
    fragments: list[str] = []
    # A segment whose ``/`` was percent-encoded decodes into several parts;
    # each part is a segment of its own as well as part of the whole path.
    segments = [part for segment in parsed.path.split("/") if segment
                for part in unquote(segment).split("/") if part]
    while segments and segments[-1].lower() in GENERIC_SEGMENTS:
        segments.pop()
    while segments and segments[0].lower() in GENERIC_SEGMENTS:
        segments.pop(0)
    if segments:
        fragments.append("/".join(segments))
    fragments.extend(
        segment for segment in segments
        if segment.lower() not in GENERIC_SEGMENTS
    )
    if password:
        fragments.extend((password, unquote(password)))
    if parsed.query:
        for _key, item in parse_qsl(parsed.query, keep_blank_values=True):
            fragments.append(item)
    kept = {fragment for fragment in fragments if len(fragment) >= MIN_FRAGMENT}
    return tuple(sorted(kept, key=lambda fragment: (-len(fragment), fragment)))


def _endpoint_slug(value: str | None) -> str | None:
    try:
        host = urlsplit((value or "").strip()).hostname
    except ValueError:
        return None
    if not host or "." in host:
        return None
    return host.replace("-", "_")


def configure(value: str | None) -> None:
    """Install the redaction for ``value`` (the configured upstream URL)."""

    global _STATE
    fragments = derive_fragments(value)
    with _LOCK:
        generation = _STATE.generation + 1
        if not fragments:
            _STATE = _State(generation=generation, endpoint_slug=_endpoint_slug(value))
            return
        alternation = "|".join(_sequence(fragment) for fragment in fragments)
        prefixes = sorted(
            {fragment[:length] for fragment in fragments
             for length in range(MIN_BOUNDARY, len(fragment))
             if fragment[:length] != _PUBLIC_PREFIX},
            key=lambda item: (-len(item), item),
        )
        suffixes = sorted(
            {fragment[start:] for fragment in fragments
             for start in range(1, len(fragment) - MIN_BOUNDARY + 1)},
            key=lambda item: (-len(item), item),
        )
        text = re.compile(alternation)
        data = re.compile(alternation.encode("utf-8"))
        head = re.compile(
            _LEAD + "(" + "|".join(_sequence(item) for item in suffixes) + ")"
        ) if suffixes else None
        tail = re.compile(
            "(" + "|".join(_sequence(item) for item in prefixes) + ")" + _TRAIL + r"\Z"
        ) if prefixes else None
        # A cut prefix in its longest encoded form (a doubly percent-encoded
        # four-byte character is twenty characters) plus its decoration; a
        # deeper encoding at an edge is not recognized as cut.
        window = 20 * max(len(fragment) for fragment in fragments) + _DECORATION_WINDOW
        _STATE = _State(fragments, text, data, head, tail, window, generation, _endpoint_slug(value))


def reset() -> None:
    """Remove the redaction (tests only)."""

    configure(None)


def generation() -> int:
    return _STATE.generation


def configured() -> bool:
    return _STATE.text is not None


def fragment_count() -> int:
    return len(_STATE.fragments)


def fragments() -> tuple[str, ...]:
    """The exact fragments, for callers that pass literal known secrets on."""

    return _STATE.fragments


def contains(value: str) -> bool:
    """True when ``value`` holds a whole fragment or a cut one at its edge."""

    return bool(spans(value))


def contains_bytes(value: bytes) -> bool:
    state = _STATE
    return bool(state.data is not None and value and state.data.search(value))


def spans(value: str, *, per_line: bool = False) -> list[tuple[int, int]]:
    """The merged ``(start, end)`` spans of ``value`` to replace, in order.

    Every whole fragment in any recognized encoding, and a cut fragment of
    at least ``MIN_BOUNDARY`` characters at the start or end of ``value``
    (with ``per_line``, of each line, a CR terminator excluded), where
    surrounding whitespace, quotes, brackets, punctuation, an ellipsis or a
    truncation mark do not move the edge. A span never contains a line
    break, because no fragment contains whitespace.
    """

    state = _STATE
    if state.text is None or type(value) is not str or not value:
        return []
    found = [match.span() for match in state.text.finditer(value) if match.end() > match.start()]
    if per_line:
        bounds = []
        position = 0
        for line in value.split("\n"):
            stop = position + len(line)
            bounds.append((position, stop - 1 if line.endswith("\r") else stop))
            position = stop + 1
    else:
        bounds = [(0, len(value))]
    for start, stop in bounds:
        if stop <= start:
            continue
        if state.head is not None:
            match = state.head.match(value, start, stop)
            if match:
                found.append(match.span(1))
        if state.tail is not None:
            match = state.tail.search(value, max(start, stop - state.tail_window), stop)
            if match:
                found.append(match.span(1))
    found.sort()
    merged: list[tuple[int, int]] = []
    for start, stop in found:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], stop))
        else:
            merged.append((start, stop))
    return merged


def _replace(value: str, found: list[tuple[int, int]]) -> str:
    output: list[str] = []
    position = 0
    for start, stop in found:
        output.append(value[position:start])
        output.append(MARKER)
        position = stop
    output.append(value[position:])
    return "".join(output)


def redact(value: str) -> str:
    """``value`` with every recognized form, and a cut one at its edge, replaced."""

    found = spans(value)
    return _replace(value, found) if found else value


def redact_lines(value: str) -> str:
    """``redact`` with the edge rule at every line edge; line breaks are kept."""

    found = spans(value, per_line=True)
    return _replace(value, found) if found else value


def mask_bytes(value: bytes) -> bytes:
    """Same-length mask of every whole fragment, so framing stays valid."""

    state = _STATE
    if state.data is None or not value:
        return value
    return state.data.sub(lambda match: MASK_BYTE * len(match.group(0)), value)


def redact_tree(value: Any) -> Any:
    """``value`` with every string and mapping key redacted; other values kept."""

    if _STATE.text is None:
        return value
    return _redact_tree(value, 0)


def _redact_tree(value: Any, depth: int) -> Any:
    if isinstance(value, str):
        # A str subclass renders as its text; it is replaced only if changed.
        text = str.__getitem__(value, slice(None))
        safe = redact(text)
        return value if safe == text else safe
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth > 200:
        return MARKER
    if isinstance(value, dict):
        return {
            (redact(key) if type(key) is str else key): _redact_tree(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_tree(item, depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_tree(item, depth + 1) for item in value)
    # Rendered with ``default=str``: the text it would render, redacted.
    text = str(value)
    safe = redact(text)
    return value if safe == text else safe


def is_withheld_addon_slug(slug: str | None) -> bool:
    """True for an add-on slug whose log is the ha-mcp app's own.

    The store slug ``ha_mcp`` with any repository prefix or variant suffix
    (``<repo>_ha_mcp``, ``ha_mcp_dev``), and the slug the configured
    endpoint's Supervisor DNS name names. Withheld whether or not a URL is
    configured: the app prints its own path either way.
    """

    if type(slug) is not str or not slug:
        return False
    normalized = slug.replace("-", "_")
    if _HA_MCP_SLUG.search(normalized):
        return True
    return _STATE.endpoint_slug is not None and normalized == _STATE.endpoint_slug


class RedactingStream(io.TextIOBase):
    """A text stream that redacts each complete line before writing it.

    Every log handler, ``print`` and the interpreter's own tracebacks write
    through ``sys.stdout``/``sys.stderr``; the add-on's container log (and so
    the host journal) receives only what this stream lets through. Text is
    held until its line ends, so a fragment written in pieces is still seen
    whole; a line longer than ``MAX_PENDING`` is written redacted at that
    bound with its edge rule applied.
    """

    MAX_PENDING = 64 * 1024

    def __init__(self, target: Any) -> None:
        super().__init__()
        self._target = target
        self._pending = ""
        self._lock = threading.RLock()

    @property
    def target(self) -> Any:
        return self._target

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return getattr(self._target, "encoding", "utf-8")

    @property
    def errors(self) -> str | None:  # type: ignore[override]
        return getattr(self._target, "errors", None)

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return bool(getattr(self._target, "isatty", lambda: False)())

    def fileno(self) -> int:
        return self._target.fileno()

    def write(self, text: str) -> int:
        if type(text) is not str:
            text = str(text)
        with self._lock:
            self._pending += text
            cut = self._pending.rfind("\n")
            if cut >= 0:
                ready, self._pending = self._pending[: cut + 1], self._pending[cut + 1:]
                self._target.write(redact_lines(ready))
            if len(self._pending) > self.MAX_PENDING:
                ready, self._pending = self._pending, ""
                self._target.write(redact_lines(ready))
        return len(text)

    def flush(self) -> None:
        # A partial line stays held: it may be the start of a fragment.
        with self._lock:
            flush = getattr(self._target, "flush", None)
            if flush is not None:
                flush()

    def drain(self) -> None:
        with self._lock:
            ready, self._pending = self._pending, ""
            if ready:
                self._target.write(redact_lines(ready))
            self.flush()

    def close(self) -> None:  # pragma: no cover - interpreter shutdown
        self.drain()


def install_stream_guard() -> None:
    """Route ``sys.stdout`` and ``sys.stderr`` through ``RedactingStream``.

    Called once at startup, before any log handler binds a stream. Idempotent.
    """

    import atexit

    for name in ("stdout", "stderr"):
        current = getattr(sys, name)
        if current is None or isinstance(current, RedactingStream):
            continue
        guarded = RedactingStream(current)
        setattr(sys, name, guarded)
        atexit.register(guarded.drain)


class ResponseGuard:
    """ASGI wrapper: the last-resort same-length mask of every response.

    Tool results, errors and gateway envelopes are redacted where they are
    rendered; this guard masks any whole fragment still present in a header
    value or body chunk of any response the wrapped application sends. It
    cannot see a fragment split across two body chunks (rendered responses
    are redacted before they are chunked).
    """

    def __init__(self, app: Callable) -> None:
        self.app = app

    def __getattr__(self, name: str) -> Any:
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        async def guarded_send(message):
            kind = message.get("type")
            if kind == "http.response.start" and configured():
                headers = [
                    (name, mask_bytes(value)) for name, value in message.get("headers", [])
                ]
                message = {**message, "headers": headers}
            elif kind == "http.response.body":
                body = message.get("body", b"")
                if body and contains_bytes(body):
                    message = {**message, "body": mask_bytes(body)}
            await send(message)

        return await self.app(scope, receive, guarded_send)
