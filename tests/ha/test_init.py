"""Tests für Setup, Coordinator, Sensoren, Buttons und Services."""

from __future__ import annotations

from datetime import timedelta

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.bissfest.const import DOMAIN

from .conftest import API, URL


async def _setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_sensors(hass: HomeAssistant, aioclient_mock, status, config_entry) -> None:
    aioclient_mock.get(f"{API}/status", json=status)
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, config_entry.unique_id)})
    assert device.name == "Bissfest – sebastian"
    assert device.sw_version == "2.1.0"
    assert device.configuration_url == URL

    prefix = "sensor.bissfest_sebastian_"
    expect = {
        "calories_eaten": "1320",
        "calorie_goal": "1930",
        "calories_remaining": "610",
        "calorie_deficit": "1085",
        "estimated_calories_burned": "2405",
        "protein": "76.0",
        "carbohydrates": "87.0",
        "sugar": "30.0",
        "fat": "69.0",
        "water": "1500",
        "water_2": "75",
        "weight": "77.2",
        "weight_change_30_days": "-2.8",
        "target_weight": "75.0",
        "total_caffeine": "390",
        "coffee_cups": "1",
        "coffee_mugs": "2",
        "coffee_volume": "650",
        "breakfast_calories": "456",
        "lunch_calories": "600",
        "dinner_calories": "264",
        "snack_1_calories": "0",
    }
    for suffix, value in expect.items():
        state = hass.states.get(prefix + suffix)
        assert state is not None, suffix
        assert state.state == value, suffix

    assert hass.states.get(prefix + "weight").attributes["date"] == "2026-11-04"
    assert hass.states.get(prefix + "weight").attributes["unit_of_measurement"] == "kg"
    assert hass.states.get(prefix + "water").attributes["goal_ml"] == 2000
    breakfast = hass.states.get(prefix + "breakfast_calories").attributes
    assert breakfast["entries"] == 5 and breakfast["reminder"] == "09:10"
    assert breakfast["unit_of_measurement"] == "kcal"

    # Tee ist ausgeschaltet → keine Tee-Entitäten
    assert hass.states.get(prefix + "tea_cups") is None
    assert hass.states.get("button.bissfest_sebastian_tea_cup") is None
    assert hass.states.get("button.bissfest_sebastian_coffee_cup") is not None
    assert hass.states.get("button.bissfest_sebastian_undo_last_coffee") is not None


async def test_null_values(hass: HomeAssistant, aioclient_mock, status, config_entry) -> None:
    status["calories"].update(burned_estimate=None, deficit=None)
    status["weight"] = {k: None for k in status["weight"]}
    aioclient_mock.get(f"{API}/status", json=status)
    await _setup(hass, config_entry)
    assert hass.states.get("sensor.bissfest_sebastian_calorie_deficit").state == "unknown"
    assert hass.states.get("sensor.bissfest_sebastian_weight").state == "unknown"


async def test_setup_not_ready(hass: HomeAssistant, aioclient_mock, config_entry) -> None:
    aioclient_mock.get(f"{API}/status", status=502)
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_auth_failed_starts_reauth(hass: HomeAssistant, aioclient_mock, config_entry) -> None:
    aioclient_mock.get(f"{API}/status", status=401, json={"detail": "Token widerrufen"})
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_update_failed_marks_unavailable(hass: HomeAssistant, aioclient_mock, status, config_entry) -> None:
    aioclient_mock.get(f"{API}/status", json=status)
    await _setup(hass, config_entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/status", status=429, json={"detail": "zu viele"})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.bissfest_sebastian_water").state == "unavailable"


async def test_button_press_updates_state(hass: HomeAssistant, aioclient_mock, status, config_entry) -> None:
    aioclient_mock.get(f"{API}/status", json=status)
    after = {**status, "water": {"ml": 1750, "goal_ml": 2000, "percent": 88}}
    aioclient_mock.post(f"{API}/water", json=after)
    await _setup(hass, config_entry)

    await hass.services.async_call(
        "button", "press", {"entity_id": "button.bissfest_sebastian_water_250_ml"}, blocking=True
    )
    call = [c for c in aioclient_mock.mock_calls if c[0] == "POST"][0]
    assert call[2] == {"ml": 250}
    assert hass.states.get("sensor.bissfest_sebastian_water").state == "1750"


async def test_services(hass: HomeAssistant, aioclient_mock, status, config_entry) -> None:
    aioclient_mock.get(f"{API}/status", json=status)
    aioclient_mock.post(f"{API}/water", json=status)
    aioclient_mock.post(f"{API}/weight", json={**status, "weight": {**status["weight"], "latest_kg": 76.9}})
    aioclient_mock.post(f"{API}/drink", json=status)
    aioclient_mock.delete(f"{API}/drink/last?kind=coffee", json=status)
    await _setup(hass, config_entry)

    await hass.services.async_call(DOMAIN, "add_water", {"ml": -200}, blocking=True)
    await hass.services.async_call(DOMAIN, "add_drink", {"kind": "coffee", "size": "mug"}, blocking=True)
    await hass.services.async_call(
        DOMAIN, "undo_drink", {"kind": "coffee", "config_entry_id": config_entry.entry_id}, blocking=True
    )
    await hass.services.async_call(DOMAIN, "log_weight", {"kg": 76.9, "date": "2026-11-03"}, blocking=True)

    bodies = [(c[0], str(c[1]), c[2]) for c in aioclient_mock.mock_calls if c[0] != "GET"]
    assert bodies == [
        ("POST", f"{API}/water", {"ml": -200}),
        ("POST", f"{API}/drink", {"kind": "coffee", "size": "mug"}),
        ("DELETE", f"{API}/drink/last?kind=coffee", None),
        ("POST", f"{API}/weight", {"kg": 76.9, "date": "2026-11-03"}),
    ]
    assert hass.states.get("sensor.bissfest_sebastian_weight").state == "76.9"

    with pytest.raises(Exception):  # vol.Invalid
        await hass.services.async_call(DOMAIN, "add_water", {"ml": 5000}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "add_water", {"ml": 1, "config_entry_id": "nope"}, blocking=True)


async def test_log_weight_past_day_keeps_today(hass: HomeAssistant, aioclient_mock, status, config_entry) -> None:
    aioclient_mock.get(f"{API}/status", json=status)
    past = {**status, "date": "2026-11-01", "calories": {**status["calories"], "eaten": 99}}
    aioclient_mock.post(f"{API}/weight", json=past)
    await _setup(hass, config_entry)

    await hass.services.async_call(DOMAIN, "log_weight", {"kg": 78.0, "date": "2026-11-01"}, blocking=True)
    assert hass.states.get("sensor.bissfest_sebastian_calories_eaten").state == "1320"
    assert [c[0] for c in aioclient_mock.mock_calls] == ["GET", "POST", "GET"]


async def test_service_multiple_entries_needs_target(hass: HomeAssistant, aioclient_mock, status, config_entry) -> None:
    aioclient_mock.get(f"{API}/status", json=status)
    aioclient_mock.get("https://other.example.de/api/v1/status", json={**status, "user": "anna"})
    await _setup(hass, config_entry)
    other = MockConfigEntry(
        domain=DOMAIN, unique_id="https://other.example.de#anna",
        data={"url": "https://other.example.de", "token": "bf_x"},
    )
    await _setup(hass, other)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "add_water", {"ml": 250}, blocking=True)


async def test_write_errors(hass: HomeAssistant, aioclient_mock, status, config_entry) -> None:
    aioclient_mock.get(f"{API}/status", json=status)
    aioclient_mock.post(f"{API}/water", status=422, json={"detail": "ml außerhalb"})
    aioclient_mock.post(f"{API}/drink", status=401, json={"detail": "widerrufen"})
    await _setup(hass, config_entry)

    with pytest.raises(HomeAssistantError, match="ml außerhalb"):
        await hass.services.async_call(DOMAIN, "add_water", {"ml": 100}, blocking=True)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "add_drink", {"kind": "coffee", "size": "cup"}, blocking=True)
    await hass.async_block_till_done()
    assert any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress())


async def test_layout_change_reloads(hass: HomeAssistant, aioclient_mock, status, config_entry) -> None:
    aioclient_mock.get(f"{API}/status", json=status)
    await _setup(hass, config_entry)
    assert hass.states.get("sensor.bissfest_sebastian_tea_cups") is None

    status["tea"]["enabled"] = True
    status["coffee"]["enabled"] = False
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/status", json=status)
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("sensor.bissfest_sebastian_tea_cups").state == "0"
    assert hass.states.get("button.bissfest_sebastian_tea_mug") is not None
    registry = er.async_get(hass)
    assert registry.async_get("sensor.bissfest_sebastian_coffee_cups") is None
    assert registry.async_get("button.bissfest_sebastian_coffee_cup") is None


async def test_unload(hass: HomeAssistant, aioclient_mock, status, config_entry) -> None:
    aioclient_mock.get(f"{API}/status", json=status)
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
