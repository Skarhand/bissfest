"""Schlanker Client für die Bissfest-REST-API (/api/v1)."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)


class BissfestError(Exception):
    """Allgemeiner Fehler bei der Kommunikation mit Bissfest."""


class BissfestConnectionError(BissfestError):
    """Server nicht erreichbar oder unerwartete Antwort."""


class BissfestAuthError(BissfestError):
    """Token ungültig oder widerrufen (HTTP 401)."""


class BissfestNotFoundError(BissfestError):
    """API nicht gefunden (HTTP 404) – meist eine Bissfest-Version vor 2.1.0."""


class BissfestRateLimitError(BissfestError):
    """Rate-Limit erreicht (HTTP 429)."""


class BissfestValidationError(BissfestError):
    """Ungültige Eingabe (HTTP 422)."""


def normalize_url(url: str) -> str:
    """Basis-URL vereinheitlichen: Schema ergänzen, Slash am Ende entfernen."""
    url = url.strip()
    if "://" not in url:
        url = f"https://{url}"
    scheme, rest = url.split("://", 1)
    host, sep, path = rest.partition("/")
    return f"{scheme.lower()}://{host.lower()}{sep}{path}".rstrip("/")


class BissfestApiClient:
    """Zugriff auf die Bissfest-API mit Bearer-Token."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str) -> None:
        self._session = session
        self.base_url = normalize_url(base_url)
        self._token = token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/api/v1/{path}"
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        try:
            async with self._session.request(
                method, url, json=json, params=params, headers=headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status == 401:
                    raise BissfestAuthError(await _detail(resp))
                if resp.status == 404:
                    raise BissfestNotFoundError(await _detail(resp))
                if resp.status == 429:
                    raise BissfestRateLimitError(await _detail(resp))
                if resp.status == 422:
                    raise BissfestValidationError(await _detail(resp))
                if resp.status >= 400:
                    raise BissfestConnectionError(f"HTTP {resp.status}: {await _detail(resp)}")
                data = await resp.json(content_type=None)
        except BissfestError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise BissfestConnectionError(str(err) or type(err).__name__) from err
        if not isinstance(data, dict):
            raise BissfestConnectionError("Unerwartete Antwort (kein JSON-Objekt)")
        return data

    async def get_status(self, date: str | None = None) -> dict[str, Any]:
        """Tagesstatus abrufen."""
        return await self._request("GET", "status", params={"date": date} if date else None)

    async def add_water(self, ml: int) -> dict[str, Any]:
        """Wasser hinzufügen (negativ = abziehen)."""
        return await self._request("POST", "water", json={"ml": ml})

    async def log_weight(self, kg: float, date: str | None = None) -> dict[str, Any]:
        """Gewicht eintragen."""
        body: dict[str, Any] = {"kg": kg}
        if date:
            body["date"] = date
        return await self._request("POST", "weight", json=body)

    async def add_drink(self, kind: str, size: str) -> dict[str, Any]:
        """Kaffee oder Tee (Tasse/Pott) eintragen."""
        return await self._request("POST", "drink", json={"kind": kind, "size": size})

    async def undo_drink(self, kind: str) -> dict[str, Any]:
        """Letzten Kaffee/Tee des Tages zurücknehmen."""
        return await self._request("DELETE", "drink/last", params={"kind": kind})


async def _detail(resp: aiohttp.ClientResponse) -> str:
    """Fehlermeldung aus {"detail": "..."} lesen, sonst Rohtext."""
    try:
        data = await resp.json(content_type=None)
    except (aiohttp.ClientError, ValueError):
        return (await resp.text())[:200]
    if isinstance(data, dict) and "detail" in data:
        return str(data["detail"])
    return str(data)[:200]
