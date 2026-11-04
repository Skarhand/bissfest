"""Services der Bissfest-Integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DATE,
    ATTR_KG,
    ATTR_KIND,
    ATTR_ML,
    ATTR_SIZE,
    DOMAIN,
    DRINK_KINDS,
    DRINK_SIZES,
    SERVICE_ADD_DRINK,
    SERVICE_ADD_WATER,
    SERVICE_LOG_WEIGHT,
    SERVICE_UNDO_DRINK,
)
from .coordinator import BissfestConfigEntry, BissfestCoordinator

_ENTRY = {vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}

ADD_WATER_SCHEMA = vol.Schema(
    {**_ENTRY, vol.Required(ATTR_ML): vol.All(vol.Coerce(int), vol.Range(min=-3000, max=3000))}
)
LOG_WEIGHT_SCHEMA = vol.Schema(
    {
        **_ENTRY,
        vol.Required(ATTR_KG): vol.All(vol.Coerce(float), vol.Range(min=20, max=400)),
        vol.Optional(ATTR_DATE): cv.date,
    }
)
ADD_DRINK_SCHEMA = vol.Schema(
    {**_ENTRY, vol.Required(ATTR_KIND): vol.In(DRINK_KINDS), vol.Required(ATTR_SIZE): vol.In(DRINK_SIZES)}
)
UNDO_DRINK_SCHEMA = vol.Schema({**_ENTRY, vol.Required(ATTR_KIND): vol.In(DRINK_KINDS)})


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> BissfestCoordinator:
    """Ziel-Konto bestimmen: angegebener Eintrag oder das einzige geladene Konto."""
    entries: list[BissfestConfigEntry] = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED
    ]
    if entry_id := call.data.get(ATTR_CONFIG_ENTRY_ID):
        for entry in entries:
            if entry.entry_id == entry_id:
                return entry.runtime_data
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_found",
            translation_placeholders={"entry_id": entry_id},
        )
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_entries")
    if len(entries) > 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="multiple_entries")
    return entries[0].runtime_data


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Services registrieren."""

    async def add_water(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        await coordinator.async_write(lambda: coordinator.client.add_water(call.data[ATTR_ML]))

    async def log_weight(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        date = call.data.get(ATTR_DATE)
        await coordinator.async_write(
            lambda: coordinator.client.log_weight(
                call.data[ATTR_KG], date.isoformat() if date else None
            )
        )

    async def add_drink(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        await coordinator.async_write(
            lambda: coordinator.client.add_drink(call.data[ATTR_KIND], call.data[ATTR_SIZE])
        )

    async def undo_drink(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        await coordinator.async_write(lambda: coordinator.client.undo_drink(call.data[ATTR_KIND]))

    hass.services.async_register(DOMAIN, SERVICE_ADD_WATER, add_water, ADD_WATER_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_LOG_WEIGHT, log_weight, LOG_WEIGHT_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_ADD_DRINK, add_drink, ADD_DRINK_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_UNDO_DRINK, undo_drink, UNDO_DRINK_SCHEMA)
