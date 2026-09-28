"""Request-scoped single-attempt transport for supplementary verification."""

from contextvars import ContextVar

import aiohttp

from ..errors import HomeAssistantUnavailableError


SINGLE_READ = ContextVar("configuration_reverification_single_read", default=False)
MAX_READ_BYTES = 1024 * 1024


async def one_attempt(request, handler):
    try:
        response = await handler(request)
    except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError):
        # Convert before aiohttp's idempotent-method reconnect/replay loop.
        raise HomeAssistantUnavailableError() from None
    if 300 <= response.status < 400:
        response.close()
        raise HomeAssistantUnavailableError()
    return response


def session_options():
    return ({"middlewares": (one_attempt,), "auto_decompress": False,
             "headers": {"Accept-Encoding": "identity"}}
            if SINGLE_READ.get() else {})


async def response_text(response):
    if not SINGLE_READ.get():
        return await response.text()
    chunks, size = [], 0
    async for chunk in response.content.iter_chunked(16_384):
        size += len(chunk)
        if size > MAX_READ_BYTES:
            raise HomeAssistantUnavailableError()
        chunks.append(chunk)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeError:
        raise HomeAssistantUnavailableError() from None
