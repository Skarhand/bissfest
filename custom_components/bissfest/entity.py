"""Gemeinsame Basis für Bissfest-Entitäten."""

from __future__ import annotations

from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BissfestConfigEntry, BissfestCoordinator


class BissfestEntity(CoordinatorEntity[BissfestCoordinator]):
    """Entität eines Bissfest-Kontos (ein Gerät pro Konto)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BissfestCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            name=f"Bissfest – {coordinator.data.get('user')}",
            manufacturer="Bissfest",
            model="Abnehm-App",
            sw_version=coordinator.data.get("app_version"),
            configuration_url=entry.data[CONF_URL],
            entry_type=DeviceEntryType.SERVICE,
        )


@callback
def remove_stale_entities(
    hass: HomeAssistant, entry: BissfestConfigEntry, domain: str, keep: set[str]
) -> None:
    """Entitäten entfernen, die es nicht mehr gibt (z. B. Tee in Bissfest ausgeschaltet)."""
    registry = er.async_get(hass)
    for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg.domain == domain and reg.unique_id not in keep:
            registry.async_remove(reg.entity_id)
