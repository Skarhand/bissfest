"""Bissfest – Abnehm-App als Home-Assistant-Integration."""

from __future__ import annotations

from homeassistant.const import CONF_TOKEN, CONF_URL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import BissfestApiClient
from .const import DOMAIN
from .coordinator import BissfestConfigEntry, BissfestCoordinator, layout_signature
from .services import async_setup_services

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Services einmalig registrieren."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BissfestConfigEntry) -> bool:
    """Ein Bissfest-Konto einrichten."""
    client = BissfestApiClient(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN]
    )
    coordinator = BissfestCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Mahlzeiten/Kaffee/Tee in Bissfest umgestellt → Entitäten neu aufbauen
    signature = layout_signature(coordinator.data)

    @callback
    def _check_layout() -> None:
        if coordinator.data and layout_signature(coordinator.data) != signature:
            hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(coordinator.async_add_listener(_check_layout))
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BissfestConfigEntry) -> bool:
    """Konto entladen."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: BissfestConfigEntry) -> None:
    """Abfrageintervall geändert → neu laden."""
    await hass.config_entries.async_reload(entry.entry_id)
