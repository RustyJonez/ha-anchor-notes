"""Minimal async client for the Anchor notes REST API (read-only)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import aiohttp

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)


class AnchorError(Exception):
    """Anchor returned something unexpected."""


class AnchorConnectionError(AnchorError):
    """Anchor could not be reached."""


class AnchorAuthError(AnchorError):
    """Anchor rejected the API token."""


class AnchorNotFoundError(AnchorError):
    """The note doesn't exist or this user can't see it."""


def normalize_url(url: str) -> str:
    """Return the server base URL without a trailing slash or /api suffix."""
    url = url.strip().rstrip("/")
    if url.endswith("/api"):
        url = url[: -len("/api")]
    if "://" not in url:
        url = f"http://{url}"
    return url


class AnchorClient:
    """Talks to one Anchor server as one user (identified by their API token)."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self._session = session
        self._token = token.strip()
        self.url = normalize_url(url)

    async def get_health(self) -> dict[str, Any]:
        """Return /api/health (no auth): status, app, version."""
        data = await self._get("/api/health", auth=False)
        if not isinstance(data, dict):
            raise AnchorError("Unexpected /api/health response")
        return data

    async def get_notes(self) -> list[dict[str, Any]]:
        """Return all active (not archived, not trashed) notes for this user."""
        return await self._get_list("/api/notes")

    async def get_note(self, note_id: str) -> dict[str, Any]:
        """Return one note (any state this user can see), with its full content."""
        path = f"/api/notes/{quote(note_id, safe='')}"
        data = await self._get(path)
        if not isinstance(data, dict):
            raise AnchorError(f"Unexpected {path} response")
        return data

    async def get_tags(self) -> list[dict[str, Any]]:
        """Return this user's tags."""
        return await self._get_list("/api/tags")

    async def _get_list(self, path: str) -> list[dict[str, Any]]:
        data = await self._get(path)
        if not isinstance(data, list):
            raise AnchorError(f"Unexpected {path} response")
        return [item for item in data if isinstance(item, dict)]

    async def _get(self, path: str, *, auth: bool = True) -> Any:
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            async with self._session.get(
                f"{self.url}{path}", headers=headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status in (401, 403):
                    raise AnchorAuthError(f"{path} returned HTTP {resp.status}")
                if resp.status in (400, 404):
                    raise AnchorNotFoundError(f"{path} returned HTTP {resp.status}")
                if resp.status >= 400:
                    raise AnchorError(f"{path} returned HTTP {resp.status}")
                try:
                    return await resp.json(content_type=None)
                except ValueError as err:
                    raise AnchorError(f"{path} did not return JSON") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise AnchorConnectionError(f"Cannot reach {self.url}: {err}") from err
