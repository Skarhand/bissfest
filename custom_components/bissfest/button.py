"""Buttons für Bissfest."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BissfestApiClient
from .coordinator import BissfestConfigEntry, BissfestCoordinator
from .entity import BissfestEntity, remove_stale_entities

PARALLEL_UPDATES = 1

WATER_STEP_ML = 250


@dataclass(frozen=True, kw_only=True)
class BissfestButtonDescription(ButtonEntityDescription):
    """Beschreibung eines Bissfest-Buttons."""

    press_fn: Callable[[BissfestApiClient], Awaitable[dict[str, Any]]]
    exists_fn: Callable[[dict[str, Any]], bool] = lambda _: True


def _enabled(kind: str) -> Callable[[dict[str, Any]], bool]:
    return lambda d: bool((d.get(kind) or {}).get("enabled"))


BUTTONS: tuple[BissfestButtonDescription, ...] = (
    BissfestButtonDescription(
        key="add_water_250",
        translation_key="add_water_250",
        press_fn=lambda c: c.add_water(WATER_STEP_ML),
    ),
    *(
        BissfestButtonDescription(
            key=f"add_{kind}_{size}",
            translation_key=f"add_{kind}_{size}",
            press_fn=lambda c, kind=kind, size=size: c.add_drink(kind, size),
            exists_fn=_enabled(kind),
        )
        for kind in ("coffee", "tea")
        for size in ("cup", "mug")
    ),
    *(
        BissfestButtonDescription(
            key=f"undo_{kind}",
            translation_key=f"undo_{kind}",
            press_fn=lambda c, kind=kind: c.undo_drink(kind),
            exists_fn=_enabled(kind),
        )
        for kind in ("coffee", "tea")
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: BissfestConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Buttons anlegen."""
    coordinator = entry.runtime_data
    entities = [
        BissfestButton(coordinator, desc) for desc in BUTTONS if desc.exists_fn(coordinator.data)
    ]
    remove_stale_entities(hass, entry, "button", {e.unique_id for e in entities})
    async_add_entities(entities)


class BissfestButton(BissfestEntity, ButtonEntity):
    """Aktion in Bissfest auslösen."""

    entity_description: BissfestButtonDescription

    def __init__(self, coordinator: BissfestCoordinator, description: BissfestButtonDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        client = self.coordinator.client
        await self.coordinator.async_write(lambda: self.entity_description.press_fn(client))
