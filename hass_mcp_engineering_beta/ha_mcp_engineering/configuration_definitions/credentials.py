"""Credential text withheld from every string of the final report (HAMCP-132 H1).

The YAML projection withholds credential-shaped values structurally, but a
report also copies Core text that never passes through it: config entry
titles, entry and source metadata, error details. The single exit point
(``ConfigurationDefinitionsService._finish``) therefore walks the complete
envelope, keys and values, before the strict sanitizer runs, and replaces
each string that reads as credential text with ``WITHHELD_MARKER``:

- a URI with userinfo (a user name, with or without a password, before the host);
- a database or broker connection URI by scheme (``db_url``-shaped, a sqlite
  path included), whether or not it carries a password;
- a user name and password before a host, without a scheme;
- a connection-string password field (``Password=``, ``pwd=``);
- a known secret (the access secret or the Home Assistant token);
- text the reader's canonicalization refuses (it cannot be screened);
- text containing the marker itself, so the marker is never ambiguous.

The checks read the reader's canonical form (percent-decoded, NFKC,
casefold). A dictionary entry whose key is credential text is dropped. Any
withholding adds the ``credential_text_withheld`` omission, so the report is
partial.
"""

from __future__ import annotations

import re
from typing import Any

from ..providers.upstream_yaml_reader import _userinfo, canonical

WITHHELD_MARKER = "[withheld:credential_text]"
OMISSION = "credential_text_withheld"

# Connection URI schemes whose value is a ``db_url``-shaped connection target,
# with an optional SQLAlchemy driver suffix (``postgresql+psycopg2://``).
_CONNECTION_SCHEME = re.compile(
    r"(?:sqlite|postgres|postgresql|pgsql|psql|mysql|mariadb|mssql|sqlserver|oracle|"
    r"mongodb|redis|rediss|cockroachdb|db2|firebird|sybase|snowflake|clickhouse|duckdb|"
    r"influxdb|amqp|amqps|mqtt|mqtts)(?:\+[a-z0-9_]+)*://"
)
# A user and password before a host: no whitespace, no further colon in the user part.
_USER_PASS_HOST = re.compile(r"[^\s:/@]+:[^\s/@]+@[^\s/@]+")
_PASSWORD_FIELD = re.compile(r"\b(?:password|passwd|pwd)\s*=")
_MARKER_FOLDED = WITHHELD_MARKER.casefold()


def credential_text(value: str, known_secrets: tuple[str, ...] = ()) -> bool:
    """Whether ``value`` must not reach the caller."""

    if any(secret and secret in value for secret in known_secrets):
        return True
    try:
        text, _rounds = canonical(value)
    except Exception:
        # Text the canonicalization refuses cannot be screened (fail closed).
        return True
    return (
        _userinfo(text)
        or _CONNECTION_SCHEME.search(text) is not None
        or _USER_PASS_HOST.search(text) is not None
        or _PASSWORD_FIELD.search(text) is not None
        or _MARKER_FOLDED in text
        or any(secret and secret.casefold() in text for secret in known_secrets)
    )


def withhold(value: Any, known_secrets: tuple[str, ...] = ()) -> tuple[Any, int]:
    """(copy of ``value`` with credential text withheld, number withheld)."""

    count = 0

    def walk(item: Any) -> Any:
        nonlocal count
        if type(item) is str:
            if credential_text(item, known_secrets):
                count += 1
                return WITHHELD_MARKER
            return item
        if type(item) is dict:
            kept = {}
            for key, child in item.items():
                if type(key) is str and credential_text(key, known_secrets):
                    count += 1
                    continue
                kept[key] = walk(child)
            return kept
        if type(item) in (list, tuple):
            return [walk(child) for child in item]
        return item

    return walk(value), count
