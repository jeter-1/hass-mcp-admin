"""Authoritative, read-only identity for the running Engineering add-on."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json
import os
import re
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from ..configuration import Settings


MAX_SELF_INFO_BYTES = 32_000
IDENTITY_SOURCE = "supervisor_self_info"
_SAFE_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f]{1,512}$")
_FetchSelfInfo = Callable[[], Awaitable[tuple[int, bytes]]]


class SelfAddonIdentityError(RuntimeError):
    """Bounded failure to establish the exact current add-on identity."""

    category = "self_addon_identity_unavailable"

    def __init__(self) -> None:
        super().__init__(
            "The current Engineering add-on identity could not be verified."
        )


@dataclass(frozen=True)
class SupervisorSelfAddonIdentity:
    """Exact identity returned by Supervisor's caller-relative self endpoint."""

    slug: str
    name: str
    version: str
    repository: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "slug": self.slug,
            "name": self.name,
            "version": self.version,
            "repository": self.repository,
            "identity_source": IDENTITY_SOURCE,
            "authoritative": True,
        }


class SupervisorSelfAddonIdentityResolver:
    """Resolve only ``/addons/self/info`` with the existing add-on token."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_seconds: float,
        fetcher: _FetchSelfInfo | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout_seconds = max(
            1.0, min(float(timeout_seconds), 30.0)
        )
        self._fetcher = fetcher

    @classmethod
    def from_settings(
        cls, settings: Settings
    ) -> "SupervisorSelfAddonIdentityResolver":
        parsed = urlsplit(settings.ha_url)
        addon_token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
        is_supervisor_core = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname == "supervisor"
            and parsed.path.rstrip("/") == "/core"
            and bool(parsed.netloc)
        )
        base_url = (
            urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
            if is_supervisor_core
            else ""
        )
        return cls(
            base_url=base_url,
            token=addon_token,
            timeout_seconds=settings.ha_timeout_seconds,
        )

    async def resolve(self) -> SupervisorSelfAddonIdentity:
        if not self._base_url or not self._token:
            raise SelfAddonIdentityError()
        try:
            status, body = (
                await self._fetcher()
                if self._fetcher is not None
                else await self._fetch()
            )
            if status != 200 or len(body) > MAX_SELF_INFO_BYTES:
                raise SelfAddonIdentityError()
            payload = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_members,
                parse_constant=_reject_nonfinite,
            )
        except SelfAddonIdentityError:
            raise
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            TimeoutError,
            OSError,
            TypeError,
            ValueError,
            UnicodeError,
            RecursionError,
        ) as exc:
            raise SelfAddonIdentityError() from exc

        if (
            not isinstance(payload, dict)
            or payload.get("result") != "ok"
            or not isinstance(payload.get("data"), dict)
        ):
            raise SelfAddonIdentityError()
        data = payload["data"]
        slug = data.get("slug")
        name = data.get("name")
        version = data.get("version")
        repository = data.get("repository")
        if (
            not isinstance(slug, str)
            or not _SAFE_SLUG.fullmatch(slug)
            or not _safe_text(name)
            or not _safe_text(version)
            or (repository is not None and not _safe_text(repository))
        ):
            raise SelfAddonIdentityError()
        return SupervisorSelfAddonIdentity(
            slug=slug,
            name=name,
            version=version,
            repository=repository,
        )

    async def _fetch(self) -> tuple[int, bytes]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{self._base_url}/addons/self/info",
                headers=headers,
            ) as response:
                body = await response.content.read(
                    MAX_SELF_INFO_BYTES + 1
                )
                return response.status, body


def _safe_text(value: object) -> str | None:
    return (
        value
        if isinstance(value, str) and _SAFE_TEXT.fullmatch(value)
        else None
    )


def _reject_duplicate_members(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    raise ValueError("non-finite JSON constant")
