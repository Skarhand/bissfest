"""DataUpdateCoordinator für Bissfest."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    BissfestApiClient,
    BissfestAuthError,
    BissfestError,
    BissfestRateLimitError,
    BissfestValidationError,
)
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

type BissfestConfigEntry = ConfigEntry[BissfestCoordinator]


class BissfestCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Holt regelmäßig den Tagesstatus von Bissfest."""

    config_entry: BissfestConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: BissfestConfigEntry, client: BissfestApiClient
    ) -> None:
        minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=minutes),
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.client.get_status()
        except BissfestAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except BissfestRateLimitError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="rate_limited"
            ) from err
        except BissfestError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

    async def async_write(self, call: Callable[[], Awaitable[dict[str, Any]]]) -> None:
        """Schreibaufruf ausführen und den zurückgelieferten Status übernehmen."""
        try:
            data = await call()
        except BissfestAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except BissfestRateLimitError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="rate_limited"
            ) from err
        except BissfestValidationError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="invalid_input",
                translation_placeholders={"error": str(err)},
            ) from err
        except BissfestError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        if self.data is None or data.get("date") == self.data.get("date"):
            self.async_set_updated_data(data)
        else:
            # Antwort beschreibt einen anderen Tag (Gewicht mit Datum, Tageswechsel) → neu abrufen
            await self.async_refresh()


def layout_signature(data: dict[str, Any]) -> tuple:
    """Welche Entitäten es gibt – ändert sich das, wird der Eintrag neu geladen."""
    meals = tuple(m.get("name") for m in data.get("meals") or [])
    coffee = bool((data.get("coffee") or {}).get("enabled"))
    tea = bool((data.get("tea") or {}).get("enabled"))
    return (meals, coffee, tea)
