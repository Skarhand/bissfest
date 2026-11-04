"""Sensoren für Bissfest."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfMass, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import MEAL_KEYS
from .coordinator import BissfestConfigEntry, BissfestCoordinator
from .entity import BissfestEntity, remove_stale_entities

PARALLEL_UPDATES = 0

KCAL = "kcal"

Data = dict[str, Any]


def _get(data: Data, section: str, key: str) -> Any:
    return (data.get(section) or {}).get(key)


@dataclass(frozen=True, kw_only=True)
class BissfestSensorDescription(SensorEntityDescription):
    """Beschreibung eines Bissfest-Sensors."""

    value_fn: Callable[[Data], StateType]
    attrs_fn: Callable[[Data], dict[str, Any]] | None = None
    exists_fn: Callable[[Data], bool] = lambda _: True


def _kcal(key: str, field: str, section: str = "calories") -> BissfestSensorDescription:
    return BissfestSensorDescription(
        key=key,
        translation_key=key,
        native_unit_of_measurement=KCAL,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _get(d, section, field),
    )


def _grams(key: str) -> BissfestSensorDescription:
    return BissfestSensorDescription(
        key=key,
        translation_key=key,
        native_unit_of_measurement=UnitOfMass.GRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _get(d, "nutrients", key),
    )


def _drink(kind: str) -> tuple[BissfestSensorDescription, ...]:
    enabled = lambda d: bool(_get(d, kind, "enabled"))  # noqa: E731
    return (
        BissfestSensorDescription(
            key=f"{kind}_cups",
            translation_key=f"{kind}_cups",
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda d: _get(d, kind, "cups"),
            exists_fn=enabled,
        ),
        BissfestSensorDescription(
            key=f"{kind}_mugs",
            translation_key=f"{kind}_mugs",
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda d: _get(d, kind, "mugs"),
            exists_fn=enabled,
        ),
        BissfestSensorDescription(
            key=f"{kind}_ml",
            translation_key=f"{kind}_ml",
            native_unit_of_measurement=UnitOfVolume.MILLILITERS,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda d: _get(d, kind, "ml"),
            exists_fn=enabled,
        ),
        BissfestSensorDescription(
            key=f"{kind}_caffeine",
            translation_key=f"{kind}_caffeine",
            native_unit_of_measurement=UnitOfMass.MILLIGRAMS,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda d: _get(d, kind, "caffeine_mg"),
            exists_fn=enabled,
        ),
    )


SENSORS: tuple[BissfestSensorDescription, ...] = (
    _kcal("calories_eaten", "eaten"),
    _kcal("calories_goal", "goal"),
    _kcal("calories_remaining", "remaining"),
    _kcal("calories_deficit", "deficit"),
    _kcal("calories_burned_estimate", "burned_estimate"),
    _grams("protein"),
    _grams("carbs"),
    _grams("sugar"),
    _grams("fat"),
    BissfestSensorDescription(
        key="water",
        translation_key="water",
        native_unit_of_measurement=UnitOfVolume.MILLILITERS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _get(d, "water", "ml"),
        attrs_fn=lambda d: {"goal_ml": _get(d, "water", "goal_ml")},
    ),
    BissfestSensorDescription(
        key="water_percent",
        translation_key="water_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _get(d, "water", "percent"),
    ),
    BissfestSensorDescription(
        key="weight",
        translation_key="weight",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _get(d, "weight", "latest_kg"),
        attrs_fn=lambda d: {
            "date": _get(d, "weight", "latest_date"),
            "today_kg": _get(d, "weight", "today_kg"),
        },
    ),
    BissfestSensorDescription(
        key="weight_change_30d",
        translation_key="weight_change_30d",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _get(d, "weight", "change_30d_kg"),
    ),
    BissfestSensorDescription(
        key="weight_target",
        translation_key="weight_target",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        suggested_display_precision=1,
        value_fn=lambda d: _get(d, "weight", "target_kg"),
    ),
    BissfestSensorDescription(
        key="caffeine_total",
        translation_key="caffeine_total",
        native_unit_of_measurement=UnitOfMass.MILLIGRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("caffeine_total_mg"),
    ),
    *_drink("coffee"),
    *_drink("tea"),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: BissfestConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Sensoren anlegen."""
    coordinator = entry.runtime_data
    data = coordinator.data
    entities: list[SensorEntity] = [
        BissfestSensor(coordinator, desc) for desc in SENSORS if desc.exists_fn(data)
    ]
    entities += [
        BissfestMealSensor(coordinator, meal["name"]) for meal in data.get("meals") or []
    ]
    remove_stale_entities(hass, entry, "sensor", {e.unique_id for e in entities})
    async_add_entities(entities)


class BissfestSensor(BissfestEntity, SensorEntity):
    """Wert aus dem Bissfest-Tagesstatus."""

    entity_description: BissfestSensorDescription

    def __init__(self, coordinator: BissfestCoordinator, description: BissfestSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)


class BissfestMealSensor(BissfestEntity, SensorEntity):
    """Kalorien einer Mahlzeit."""

    _attr_native_unit_of_measurement = KCAL
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: BissfestCoordinator, meal_name: str) -> None:
        key = MEAL_KEYS.get(meal_name)
        if key is None:  # unbekannter Name – trotzdem stabil benennen
            key = "".join(c if c.isalnum() else "_" for c in meal_name.lower())
            self._attr_translation_key = "meal_other"
            self._attr_translation_placeholders = {"name": meal_name}
        else:
            self._attr_translation_key = f"meal_{key}"
        super().__init__(coordinator, f"meal_{key}")
        self._meal_name = meal_name

    @property
    def _meal(self) -> dict[str, Any] | None:
        for meal in self.coordinator.data.get("meals") or []:
            if meal.get("name") == self._meal_name:
                return meal
        return None

    @property
    def available(self) -> bool:
        return super().available and self._meal is not None

    @property
    def native_value(self) -> StateType:
        meal = self._meal
        return meal.get("kcal") if meal else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        meal = self._meal or {}
        return {"entries": meal.get("entries"), "reminder": meal.get("reminder")}
