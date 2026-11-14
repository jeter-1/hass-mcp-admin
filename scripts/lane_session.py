"""The one HTTP session of the acceptance lanes: a redirect ends the request.

A lane talks to one disposable Core on loopback and carries that Core's token.
Home Assistant's websocket authentication is a message sent after the
connection is made, so a followed redirect would deliver the token to whatever
the redirect names. Nothing a lane asks for is answered with a redirect, so
every redirect is refused.

aiohttp 3.14.4, the pinned version, as read in its source:

- ``ClientSession._request`` follows redirects unless told otherwise
  (``allow_redirects: bool = True``, client.py:570).
- ``ClientSession._ws_connect`` has no ``allow_redirects`` parameter and calls
  ``self.request`` without one (client.py:1312-1324), so a websocket handshake
  answered with a redirect is followed.
- For a 301, 302, 303, 307 or 308 that is to be followed, the redirect trace is
  sent first (client.py:936-941). Only after it returns is ``Location`` read
  (client.py:980) and the loop continued to connect to it (client.py:1035-1038).
  An exception raised by the trace callback leaves ``_request`` through its
  ``except BaseException`` (client.py:1072), which closes the response
  (client.py:1082-1084) and raises it again. No second connection is opened.

``lane_session`` returns a session whose redirect trace raises, which covers
the websocket handshake. With ``allow_redirects=False`` aiohttp returns the 3xx
answer without sending the trace, so a REST call passes
``allow_redirects=False`` itself and gives the status it received to
``refuse_redirect``. The trace stays behind a REST call that omits the
argument.

Standard library and aiohttp only. aiohttp is imported when a session is
built, as the lane clients import it.
"""

from __future__ import annotations

# The one refusal of this module: never the URL, the Location or a header.
REDIRECT_REFUSED = "lane_redirect_refused"


class LaneRedirectRefused(Exception):
    """A lane request was answered with a redirect.

    Not an ``aiohttp.ClientError``: the readiness loops that wait through a
    connection error must not wait through this.
    """


async def _refuse_redirect_hop(_session, _context, _params):
    raise LaneRedirectRefused(REDIRECT_REFUSED)


def lane_session(*, timeout):
    """Return a session that ignores proxy settings and refuses every redirect."""
    import aiohttp
    trace = aiohttp.TraceConfig()
    trace.on_request_redirect.append(_refuse_redirect_hop)
    return aiohttp.ClientSession(timeout=timeout, trust_env=False, trace_configs=[trace])


def refuse_redirect(status):
    """Refuse a 3xx answer to a request sent with ``allow_redirects=False``."""
    if 300 <= status < 400:
        raise LaneRedirectRefused(REDIRECT_REFUSED)
